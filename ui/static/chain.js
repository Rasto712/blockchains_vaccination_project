/* AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed. */
/* The Viem layer: the page's own connection to the contracts on the local Hardhat node (vendor/viem.js, the
   global Viem). It sends register, attest, grant and revoke from the acting role's unlocked Hardhat account
   (a JSON-RPC account: the node signs eth_sendTransaction, so the page holds no keys) and reads the contract
   views for each poll. Addresses and ABIs come from GET /api/contracts, which the server answers only once
   chain.load_contract has checked them against the node; the identity hash and the record commitment come
   from the server too, because they need local files and salts. School and doctor requests stay in Python
   (app/disclosure.py): they need the card itself. The contracts decide everything; this file only calls them
   and turns their answers into the page's result shape with the console's texts (ui/actions.py). It uses
   app.js's api() only when an action or a poll runs. */
'use strict';

const NODE_UNAVAILABLE = 'unavailable: local node not reachable or wrong chain';
const SERVER_SILENT = 'the UI server did not answer';
// as app/chain.py: one attempt per request with no retries, and a bounded wait for the receipt
const RPC_TIMEOUT_MS = 10000;
const RECEIPT_TIMEOUT_MS = 30000;
const ZERO_HASH = `0x${'0'.repeat(64)}`;

const chainLayer = {
  config: null, // the checked contracts and clients of the deployment the page shows
  errorNames: new Set(), // every custom error of the three contracts
};

/** A result that is already in the page's shape (a server answer, or a fixed text), thrown past Viem calls. */
class ChainAnswer extends Error {
  constructor(result) {
    super(result.message);
    this.result = result;
  }
}

function outcome(status, message, tx, details) {
  // the shape of ui/actions.py result()
  return { status, message, reason: '', tx: tx || '', fields: {}, details: { ...(details || {}) } };
}

function viemOutcome(status, message, tx, details) {
  // the answer to a call this page sent to the node itself with Viem
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
  // the text of ui/actions.py _utc: 2026-09-29 12:00:00 UTC
  return `${new Date(seconds * 1000).toISOString().slice(0, 19).replace('T', ' ')} UTC`;
}

/* ---------- contracts and clients ---------- */

async function checkedContracts() {
  // the server checks the deployment against the node every time (chain.load_contract), as Python does
  // before each of its own transactions
  const answer = await serverGet('/api/contracts');
  if (answer.status !== 'ok') throw new ChainAnswer(answer);
  const d = answer.details;
  // Viem's hardhat chain is 31337; with no checked URL the page has no allowed way to the node
  if (d.chain_id !== Viem.hardhat.id || !d.rpc_url) throw new ChainAnswer(outcome('unavailable', NODE_UNAVAILABLE));
  const transport = () => Viem.http(d.rpc_url, { timeout: RPC_TIMEOUT_MS, retryCount: 0 });
  // each call gets every contract's errors, so a revert from a nested call keeps its name (chain.error_name)
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
  // the deploy block tells two deployments apart even at the same addresses (a restarted node)
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
  // an eth_call first, like web3's gas estimate in the Python path: a call the contract rejects is never mined
  await config.client.simulateContract({ ...call, account: config.accounts[role] });
  const hash = await config.wallets[role].writeContract(call);
  let receipt;
  try {
    receipt = await config.client.waitForTransactionReceipt({ hash, timeout: RECEIPT_TIMEOUT_MS });
  } catch (error) {
    // sent, but no receipt in time or the node stopped answering: it may still be mined (chain.wait_for_receipt)
    throw new ChainAnswer(viemOutcome('pending', 'pending: not confirmed', hash));
  }
  if (receipt.status !== 'success') throw new ChainAnswer(viemOutcome('rejected', 'rejected: Reverted', hash));
  return receipt;
}

function failure(error) {
  // the console's texts (ui/actions.py error_result); never the error itself, its text or a stack
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
  // registerUser with the role's salted identity hash; only that hash goes on-chain
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
  // registerVaccination for the guardian's record, sent from the acting role: the registry itself refuses
  // anyone but the trusted clinic (NotTrustedClinic)
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
  // grantConsent; the manager mints the first grant's reward in the same transaction, shown as before -> after.
  // Only whole numbers that fit the uint16 are sent (as chain.py does); the contract decides 1-365 days
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
  // revokeConsent; a grant that was never made reverts NoConsentToRevoke, one already revoked is a no-op in the
  // contract. The reward balance is read around it too: a revoke never takes the reward back
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
  // the page only offers the listed requesters and scopes; anything else is refused before any chain call
  if (!(requester in config.accounts) || requester === role) throw new ChainAnswer(outcome('invalid', 'unknown requester'));
  if (!(scope in config.scopes)) throw new ChainAnswer(outcome('invalid', 'unknown scope'));
  return { owner: config.accounts[role], other: config.accounts[requester], code: config.scopes[scope] };
}

/* ---------- the views of each poll ---------- */

async function addChainViews(snapshot) {
  // fills in registrations, record, consents, rewards and audit, in the shapes ui/actions.py state() sends,
  // read from the node with Viem. Returns false when the snapshot should be dropped: the deployment changed
  // between the state and /api/contracts, and the next poll shows the new one
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
    // like actions.state: the first chain error stands for the whole chain part, and none of it is shown
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
        // events can carry any uint8 scope (app/main.py scope_name)
        scope: config.scopeNames[event.args.scope] || `scope ${event.args.scope} (unsupported)`,
        allowed: event.args.allowed,
        reason: config.reasonNames[event.args.reason] || `reason ${event.args.reason}`,
        tx: event.transactionHash,
      };
    }),
  };
}

function consentStatus(expiresAt, revoked, now) {
  // the badge only, as ui/actions.py _consent_status shows it: the contract's order (never granted, revoked,
  // expired at exactly its second, active). It never decides anything; requestAccess does
  if (expiresAt === 0) return 'none';
  if (revoked) return 'revoked';
  if (now >= expiresAt) return 'expired';
  return 'active';
}
