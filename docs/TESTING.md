# Testing and evidence

The evidence: what to run, where the results are and what they show.

## Evidence

The recorded results are already in evaluation/results/; rerunning a command overwrites its files. Run from the project root with the venv active, after the [README](../README.md) setup. The last three need `npm run node` running.

| What | Command | Output file | Result |
|---|---|---|---|
| Solidity tests | `npx hardhat test solidity` | - | 38 passing |
| Solidity tests, saved | `python -m evaluation.export_solidity_results` | solidity_test_results.csv, solidity_test_run.log | 38 rows, all pass |
| Python unit tests | `python -m unittest discover -s tests` | - | 401 tests, OK |
| Scripted demo | `python -m integration.demo_workflow` | - | `demo passed` ([DEMO.md](DEMO.md)) |
| Python checks, saved | `python -m evaluation.export_python_results` | test_results.csv | 13 rows, all pass |
| Gas and timing | `python -m evaluation.measure` | gas_results.csv, timing_results.csv, timing_summary.csv, ENVIRONMENT.md | 17, 61 and 116 transactions for 1, 5 and 10 requesters; 0 failed |

Each test CSV row gives the requirement, expected and actual result, command and commit. ENVIRONMENT.md records the compiler, node and machine for gas and timing.

## Test IDs

SOL-IR, SOL-CM and SOL-RT tests are in test/IdentityRegistry.t.sol (9), test/ConsentManager.t.sol (22) and test/ConsentRewardToken.t.sol (7). PY-01 to PY-13 are in evaluation/export_python_results.py; PY-12 and 13 run the unit tests.

## What the tests show

| Property | Solidity | Live Python |
|---|---|---|
| Only the trusted clinic can attest | SOL-IR-03 | PY-06 |
| School sees measles status only; doctor sees vaccine and date only | - | PY-01 |
| A changed record is denied HASH_MISMATCH | SOL-CM-10 | PY-03 |
| Revoke and exact-second expiry block access | SOL-CM-08, SOL-CM-09 | PY-02, PY-05 |
| One reward per (guardian, requester, scope), paid to the guardian | SOL-CM-01, SOL-CM-12 to 14 | PY-08 |
| The reward token is non-transferable and grants no access | SOL-RT-05, SOL-RT-06 | - |
| Denied attempts are logged, not reverted | SOL-CM-06, SOL-CM-07 | PY-09 |
| The chain holds only hashes and metadata | SOL-IR-05 | PY-07 |
| The whole demo story | SOL-CM-19 | PY-11 |

## Gas

Average gasUsed from the receipts (solc 0.8.28, optimizer on, 200 runs). Full table: gas_results.csv.

| Operation | Gas |
|---|---|
| Deploy IdentityRegistry, ConsentRewardToken, ConsentManager | 236,456; 233,933; 598,636 |
| registerUser | 45,430 |
| registerVaccination (clinic attests) | 48,545 |
| grantConsent, first grant (mints a reward) | 77,634 (a deployment's first: 111,835) |
| grantConsent, regrant (no reward) | 43,346 to 43,462 |
| revokeConsent | 29,713 |
| requestAccess, allowed or denied | 38,821 to 41,498 |

Mean send-to-receipt time: 4.6 to 6.0 ms per transaction (timing_summary.csv).

## Caveats

- Timings are from one machine and a local node that mines each transaction at once. They show how cost grows with requesters, not public-chain speed or fees.
- The unit tests use tests/fake_chain.py, an in-memory copy of the contract rules. They show the Python handles those rules; the Solidity tests and live runs show the contracts enforce them.
- Live commands move node time forward for good. The demo and `export_python_results` also replace deployment.json, so run `python -m scripts.deploy_local --reset` for a fresh menu session.
