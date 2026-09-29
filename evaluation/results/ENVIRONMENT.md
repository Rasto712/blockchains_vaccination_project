# Measurement environment

Local demo measurements: one machine, one local Hardhat node with automine, transactions sent one at a time. They are not public-chain throughput, latency or fee figures, and no gas is converted to ETH or money.

Written by `python -m evaluation.measure --settings PATH` on 2026-09-28 23:28 UTC, at commit a635a30 + uncommitted changes. Uncommitted changes are looked for under contracts, app, scripts, evaluation/measure.py, hardhat.config.ts, package.json.

## Compiler

- solc 0.8.28, optimiser on, 200 runs (hardhat.config.ts). This line goes under the gas table.
- solc 0.8.28+commit.7893614a, optimiser on, 200 runs, EVM cancun: IdentityRegistry (solc-0_8_28-e3c23e288422f90113447449891057ea1dcd1b59), ConsentRewardToken (solc-0_8_28-e3c23e288422f90113447449891057ea1dcd1b59), ConsentManager (solc-0_8_28-e3c23e288422f90113447449891057ea1dcd1b59)

## Node

- HardhatNetwork/3.18.0/@nomicfoundation/edr/0.3.8; Hardhat 3.18.0 (node_modules); chain ID 31337; RPC http://127.0.0.1:8699 on the same machine
- automine on (hardhat_getAutomine), so each transaction is mined in its own block while eth_sendTransaction runs; no interval mining
- block gas limit 60,000,000; 20 unlocked accounts; gas price and base fee play no part in the tables

## Machine

- macOS 26.7 (Darwin 25.6.0, arm64)
- CPU Apple M5 Pro, 18 logical cores, 24 GiB memory
- Python 3.13.15, web3 8.0.0, Node.js v26.9.0

## Method

- Runs with 1, 5, 10 requesters from scenario_requester_account_indices; fresh contracts for every run. Transactions: N=1: 17, N=5: 61, N=10: 116; 0 failed.
- Roles: deployer (three deploys, setMinterOnce), clinic (registerVaccination), guardian (registerUser, grantConsent, revokeConsent), each requester (registerUser, requestAccess). Scope 1 only.
- Identities and the record hash are SHA-256 hashes of fixed synthetic labels; no personal data or local file is used.
- Gas: gasUsed of each successful receipt, including the intrinsic cost (21,000 per transaction, 53,000 per deployment) and calldata; a grant's internal mintReward call is inside its gasUsed and is not counted again. Small differences inside one row come from calldata: a zero byte costs 4 gas and any other byte 16, and account addresses and synthetic hashes differ in their zero bytes.
- Receipt seconds: time.monotonic() before the app.chain call until the successful receipt is back. That includes web3's gas estimate and fee lookup, eth_sendTransaction (mining happens inside it) and eth_getTransactionReceipt. Event seconds: the same start until the expected event is decoded from that receipt (AccessAttempt through chain.decode_access_event).
- Deployer rows are send to verified deploy, not send to receipt: they are timed around scripts/deploy_local's verified steps, which also read the artifact file and read the runtime code (and trustedClinic() or minter()) back.
- timing_results.csv has one row per operation, so its sample counts add up to each run's transactions. timing_summary.csv has the per-role and whole-run means over those same transactions; never add its rows to the others. In a run's whole-run row the receipt mean covers every transaction, the deployments included, while the event mean covers only the transactions that emit one.
- Not in the tables: view calls (getUserInfo, checkAccess, getConsent, balanceOf) use eth_call, have no receipt and cost the caller no gas. A call that would revert (for example ConsentStillActive) fails at web3's gas estimate and is never mined, so it has no receipt either.
- A blank cell or a missing row means not measured, never zero.

