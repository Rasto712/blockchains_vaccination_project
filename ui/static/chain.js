/* AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed. */
/* Talks to the contracts straight from the browser using Viem. The node signs the transactions, so the page
   keeps no keys. Addresses and ABIs come from /api/contracts. School and doctor requests stay in Python
   because they need the card file. */
'use strict';

const NODE_UNAVAILABLE = 'unavailable: local node not reachable or wrong chain';
const SERVER_SILENT = 'the UI server did not answer';
// same limits as app/chain.py: no retries, and a time limit on waiting for the receipt
const RPC_TIMEOUT_MS = 10000;
const RECEIPT_TIMEOUT_MS = 30000;
const ZERO_HASH = `0x${'0'.repeat(64)}`;

const chainLayer = {
  config: null, // contracts and clients for the current deployment
  errorNames: new Set(), // custom error names of all contracts
};

/** A ready-made result that we throw to skip the normal Viem error handling. */
class ChainAnswer extends Error {
  constructor(result) {
    super(result.message);
    this.result = result;
  }
}

function outcome(status, message, tx, details) {
  // same shape as result() in ui/actions.py
  return { status, message, reason: '', tx: tx || '', fields: {}, details: { ...(details || {}) } };
}

function viemOutcome(status, message, tx, details) {
  // result of a call the page sent to the node itself
  return outcome(status, message, tx, { ...(details || {}), via: 'viem' });
}

async function serverGet(path) {
  try {
    return await api('GET', path);
  } catch (error) {
    return outcome('failed', SERVER_SILENT);
  }
}

function utc(seconds) {
  // same format as _utc in ui/actions.py, e.g. 2026-09-29 12:00:00 UTC
  return `${new Date(seconds * 1000).toISOString().slice(0, 19).replace('T', ' ')} UTC`;
}

/* ---------- contracts and clients ---------- */

async function checkedContracts() {
  // the server checks the deployment against the node each time, like Python does
  const answer = await serverGet('/api/contracts');
  if (answer.status !== 'ok') throw new ChainAnswer(answer);
  const d = answer.details;
  // the Hardhat chain id is 31337; without a checked URL we do not connect
  if (d.chain_id !== Viem.hardhat.id || !d.rpc_url) throw new ChainAnswer(outcome('unavailable', NODE_UNAVAILABLE));
  const transport = () => Viem.http(d.rpc_url, { timeout: RPC_TIMEOUT_MS, retryCount: 0 });
  // every call gets all contracts' errors so a revert from a nested call still shows its name
  const errors = Object.values(d.contracts).flatMap((contract) => contract.abi.filter((item) => item.type === 'error'));
  errors.forEach((item) => chainLayer.errorNames.add(item.name));
  const wallets = {};
  for (const [role, address] of Object.entries(d.accounts)) {
    wallets[role] = Viem.createWalletClient({ account: address, chain: Viem.hardhat, transport: transport() });
  }
  return {
    ...d,
    errors,
    wallets,
    client: Viem.createPublicClient({ chain: Viem.hardhat, transport: transport(), pollingInterval: 250 }),
    scopeNames: Object.fromEntries(Object.entries(d.scopes).map(([name, code]) => [code, name])),
    reasonNames: Object.fromEntries(Object.entries(d.reasons).map(([name, code]) => [code, name])),
  };
}

function sameDeployment(config, deployment) {
  // the deploy block tells deployments apart even if the addresses are the same (restarted node)
  const names = Object.keys(config.contracts);
  return config.deploy_block === deployment.deploy_block
    && names.length === Object.keys(deployment.contracts).length
    && names.every((name) => String(deployment.contracts[name] || '').toLowerCase() === config.contracts[name].address.toLowerCase());
}

function contractCall(config, name, functionName, args) {
  const contract = config.contracts[name];
  return {
    address: contract.address,
    abi: [...contract.abi.filter((item) => item.type !== 'error'), ...config.errors],
    functionName,
    args,
  };
}

function read(config, name, functionName, args) {
  return config.client.readContract(contractCall(config, name, functionName, args));
}

async function send(config, role, name, functionName, args) {
  const call = contractCall(config, name, functionName, args);
  // simulate first, so a call the contract would reject is never sent
  await config.client.simulateContract({ ...call, account: config.accounts[role] });
  const hash = await config.wallets[role].writeContract(call);
  let receipt;
  try {
    receipt = await config.client.waitForTransactionReceipt({ hash, timeout: RECEIPT_TIMEOUT_MS });
  } catch (error) {
    // no receipt in time, but the transaction may still get mined
    throw new ChainAnswer(viemOutcome('pending', 'pending: not confirmed', hash));
  }
  if (receipt.status !== 'success') throw new ChainAnswer(viemOutcome('rejected', 'rejected: Reverted', hash));
  return receipt;
}

function failure(error) {
  // same texts as error_result in ui/actions.py; never show the raw error
  if (error instanceof ChainAnswer) return error.result;
  const reverted = cause(error, Viem.ContractFunctionRevertedError);
  if (reverted) {
    const name = reverted.data && reverted.data.errorName;
    return viemOutcome('rejected', `rejected: ${name === 'Panic' || chainLayer.errorNames.has(name) ? name : 'Reverted'}`);
  }
  const node = [Viem.HttpRequestError, Viem.TimeoutError, Viem.RpcRequestError, Viem.ChainMismatchError,
    Viem.ContractFunctionZeroDataError];
  if (node.some((kind) => cause(error, kind))) return viemOutcome('unavailable', NODE_UNAVAILABLE);
  return viemOutcome('failed', `failed: ${(error && error.name) || 'Error'}`);
}

function cause(error, kind) {
  for (let current = error; current; current = current.cause) {
    if (current instanceof kind) return current;
  }
  return null;
}

async function chainAction(steps) {
  try {
    return await steps();
  } catch (error) {
    return failure(error);
  }
}

/* ---------- actions ---------- */

function viemRegister(role) {
  // only the salted identity hash goes on-chain
  return chainAction(async () => {
    const answer = await serverGet(`/api/identity-hash?role=${encodeURIComponent(role)}`);
    if (answer.status !== 'ok') return answer;
    const identityHash = answer.details.identity_hash;
    const config = await checkedContracts();
    const receipt = await send(config, role, 'IdentityRegistry', 'registerUser', [identityHash]);
    return viemOutcome('ok', `registered ${role} with identity hash ${identityHash}`, receipt.transactionHash);
  });
}

function viemAttest(role) {
  // the registry refuses anyone but the trusted clinic (NotTrustedClinic)
  return chainAction(async () => {
    const answer = await serverGet('/api/record-commitment');
    if (answer.status !== 'ok') return answer;
    const commitment = answer.details.commitment;
    const config = await checkedContracts();
    const receipt = await send(config, role, 'IdentityRegistry', 'registerVaccination', [config.accounts.guardian, commitment]);
    return viemOutcome('ok', `attested the guardian's record as ${commitment}`, receipt.transactionHash);
  });
}

function viemGrant(role, requester, scope, daysText) {
  // the first grant also mints a reward, so we show the balance before and after.
  // Only whole numbers that fit in uint16 are sent; the contract checks the 1-365 day range
  return chainAction(async () => {
    const days = Number(daysText);
    if (String(daysText).trim() === '' || !Number.isInteger(days) || days < 0 || days > 65535) {
      return outcome('invalid', 'days must be a whole number from 0 to 65535');
    }
    const config = await checkedContracts();
    const { owner, other, code } = consentParties(config, role, requester, scope);
    const before = Number(await read(config, 'ConsentRewardToken', 'balanceOf', [owner]));
    const receipt = await send(config, role, 'ConsentManager', 'grantConsent', [other, code, days]);
    const after = Number(await read(config, 'ConsentRewardToken', 'balanceOf', [owner]));
    const [expiresAt] = await read(config, 'ConsentManager', 'getConsent', [owner, other, code]);
    return viemOutcome(
      'ok',
      `granted ${requester} ${scope} for ${days} day${days === 1 ? '' : 's'}; reward balance of ${role}: ${before} -> ${after}`,
      receipt.transactionHash,
      { reward_before: before, reward_after: after, expires_at: Number(expiresAt), expires_text: utc(Number(expiresAt)) },
    );
  });
}

function viemRevoke(role, requester, scope) {
  // revoking something never granted reverts (NoConsentToRevoke); revoking twice does nothing.
  // The reward balance is shown too, since a revoke does not take the reward back
  return chainAction(async () => {
    const config = await checkedContracts();
    const { owner, other, code } = consentParties(config, role, requester, scope);
    const before = Number(await read(config, 'ConsentRewardToken', 'balanceOf', [owner]));
    const [, already] = await read(config, 'ConsentManager', 'getConsent', [owner, other, code]);
    const receipt = await send(config, role, 'ConsentManager', 'revokeConsent', [other, code]);
    const after = Number(await read(config, 'ConsentRewardToken', 'balanceOf', [owner]));
    const note = already ? ': it was already revoked, nothing changed' : '';
    return viemOutcome('ok', `revoked ${requester} ${scope}${note}; reward balance of ${role}: ${before} -> ${after}`,
      receipt.transactionHash, { reward_before: before, reward_after: after });
  });
}

function consentParties(config, role, requester, scope) {
  // reject unknown requesters and scopes before touching the chain
  if (!(requester in config.accounts) || requester === role) throw new ChainAnswer(outcome('invalid', 'unknown requester'));
  if (!(scope in config.scopes)) throw new ChainAnswer(outcome('invalid', 'unknown scope'));
  return { owner: config.accounts[role], other: config.accounts[requester], code: config.scopes[scope] };
}

/* ---------- the views of each poll ---------- */

async function addChainViews(snapshot) {
  // fills in registrations, record, consents, rewards and audit like state() in ui/actions.py.
  // Returns false if the deployment changed in between; the next poll will fix it
  const s = snapshot;
  if (!s.node.reachable || !s.deployment.deployed) return true;
  try {
    let config = chainLayer.config;
    if (!config || !sameDeployment(config, s.deployment)) {
      config = await checkedContracts();
      if (!sameDeployment(config, s.deployment)) return false;
      chainLayer.config = config;
    }
    Object.assign(s, await readViews(config, s));
  } catch (error) {
    // like actions.state: one chain error hides the whole chain part
    const result = failure(error);
    if (result.message === NODE_UNAVAILABLE || !(error instanceof ChainAnswer)) {
      Object.assign(s.node, { reachable: false, message: result.message });
    } else {
      s.deployment.message = result.message;
    }
    Object.assign(s.deployment, { deployed: false, contracts: {}, deploy_block: '' });
  }
  return true;
}

async function readViews(config, s) {
  const accounts = config.accounts;
  const guardian = accounts.guardian;
  const cells = s.requesters.flatMap((requester) => s.scopes.map((scope) => [requester, scope]));
  const manager = config.contracts.ConsentManager;
  const [block, infos, consents, balances, events] = await Promise.all([
    config.client.getBlock(),
    Promise.all(s.registering.map((label) => read(config, 'IdentityRegistry', 'getUserInfo', [accounts[label]]))),
    Promise.all(cells.map(([requester, scope]) => read(config, 'ConsentManager', 'getConsent',
      [guardian, accounts[requester], config.scopes[scope]]))),
    Promise.all(s.roles.map((label) => read(config, 'ConsentRewardToken', 'balanceOf', [accounts[label]]))),
    config.client.getContractEvents({ address: manager.address, abi: manager.abi, eventName: 'AccessAttempt', fromBlock: 0n }),
  ]);
  const now = Number(block.timestamp);

  const registrations = {};
  s.registering.forEach((label, index) => {
    const [registered, identityHash] = infos[index];
    const onchain = registered ? identityHash.toLowerCase() : '';
    const mine = s.local.identity_hashes[label];
    registrations[label] = { registered, identity_hash: onchain, matches: onchain && mine ? onchain === mine : null };
  });
  const evidence = infos[s.registering.indexOf('guardian')][2].toLowerCase();
  const attested = evidence !== ZERO_HASH;
  const record = {
    attested, onchain: attested ? evidence : '',
    matches: attested && s.local.commitment ? evidence === s.local.commitment : null,
  };

  const names = Object.fromEntries(Object.entries(accounts).map(([label, address]) => [address.toLowerCase(), label]));
  return {
    registrations,
    record,
    consents: cells.map(([requester, scope], index) => {
      const expiresAt = Number(consents[index][0]);
      return {
        requester, scope, status: consentStatus(expiresAt, consents[index][1], now),
        expires_at: expiresAt, expires_text: expiresAt ? utc(expiresAt) : '',
      };
    }),
    rewards: Object.fromEntries(s.roles.map((label, index) => [label, Number(balances[index])])),
    audit: events.slice().reverse().map((event) => {
      const time = Number(event.args.timestamp);
      return {
        time, time_text: utc(time),
        requester: names[event.args.requester.toLowerCase()] || event.args.requester,
        // events can have any uint8 scope
        scope: config.scopeNames[event.args.scope] || `scope ${event.args.scope} (unsupported)`,
        allowed: event.args.allowed,
        reason: config.reasonNames[event.args.reason] || `reason ${event.args.reason}`,
        tx: event.transactionHash,
      };
    }),
  };
}

function consentStatus(expiresAt, revoked, now) {
  // only for the badge; the contract decides access in requestAccess
  if (expiresAt === 0) return 'none';
  if (revoked) return 'revoked';
  if (now >= expiresAt) return 'expired';
  return 'active';
}
