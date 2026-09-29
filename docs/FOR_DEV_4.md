# For Dev 4

Status (2026-09-28): done, and everything runs on a local node. `chain.py`, `deploy_local.py`, the rest of `demo_workflow.py` and `measure.py` are complete; on 2026-09-28 I completed the remaining parts with AI assistance (the review fixes to `chain.py` and `deploy_local.py`, the rest of the scripted demo and `measure.py`), and the 2026-09-29 review fixes (the recorded deploy block, pending after a send, the narrower error mapping, the compile and web3 hints, paths without the home folder, the split timing table) were made the same way. The commands are in the [README](../README.md). The rest of this note is kept as written, with corrections where the code now differs.

Everything you need from the Dev 3 side to write `app/chain.py`, `scripts/deploy_local.py`, the rest of `integration/demo_workflow.py` and `evaluation/measure.py`. Read this next to [tasks/DEV_4.md](tasks/DEV_4.md), [CONTRACT_API.md](CONTRACT_API.md) and [DEMO.md](DEMO.md). The notes for the others are [FOR_DEV_1.md](FOR_DEV_1.md), [FOR_DEV_2.md](FOR_DEV_2.md) and [FOR_DEV_5.md](FOR_DEV_5.md).

## Where things stand

Done on my side, all on main:

- `app/records.py`: reading, checking and saving the local record, salt files, the snapshot and its commitment, identity hashes, and `setup_runtime` for the runtime folder.
- `app/disclosure.py`: the school and doctor views, `perform_access` with the full release rule, `verify_for_school`, `get_doctor_schedule`, `format_denial` and `denial_message`.
- `app/main.py`: the console menu. `python -m app.main` now starts it.
- `integration/demo_workflow.py`: `demonstrate_tampering` and a small `expect` helper. Everything else in that file is yours.
- `tests/`: 337 unit tests. None of them needs a node: the ones that touch the chain swap a fake in for `app.chain` (`tests/fake_chain.py`), `test_demo.py` and `test_measure.py` run your demo and measurement against that fake with the node calls mocked, and `test_chain.py` and `test_deploy.py` mock web3.

The menu and the scripted demo have run against a real node since 2026-09-28.

## Setup and commands

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
cp config/settings.example.json config/settings.json
.venv/bin/python -m app.main
```

The macOS system Python is 3.9, which is too old, so make the venv with 3.10 or newer. Run everything with `-m` from the project root (for example `.venv/bin/python -m scripts.deploy_local`), because running a file by its path cannot find `app`.

In the menu, pick an actor, then "set up local files and salts". That creates `runtime-data/` (git ignored):

```text
runtime-data/
  vaccination_record.json        frozen record, never written again
  identities/guardian.json       also school.json and doctor.json
  private/vaccination_salt.json
  private/identity_<label>_salt.json
  deployment.json                yours, from deploy_local.py
  tamper/                        the tamper copy only exists while that demo runs; the empty folder stays
```

Every path in settings is relative to the project root (`records.settings_path` resolves them; an absolute path is used as given). Since 2026-09-28 the record is only saved inside `records.data_root(settings)`, the `data_root` setting (`runtime-data` by default), so all local files can live outside the repo. The menu reads `config/settings.json` with plain `json`, not through `chain.load_settings`, and passes that dict to `connect` and `select_account`. That is why setup and "show my registration" work before anything is deployed.

## Every chain call my code makes

Please keep these names and parameter names exactly. The table shows which arguments I pass by position and which by keyword, so a renamed keyword breaks my side straight away. The fake in `tests/fake_chain.py` has the same signatures for all of these, plus `get_consent`.

| Call as made | What I read back |
| --- | --- |
| `connect(settings)` | the client, passed straight back to you |
| `select_account(client, label, settings)` | an address string |
| `load_contract(client, name, deployment_path)` | the contract object, passed straight back to you |
| `register_user(registry, account=, identity_hash=)` | `["transaction_hash"]` |
| `register_vaccination(registry, clinic=, guardian=, record_hash=)` | `["transaction_hash"]` |
| `get_user_info(registry, account=)` | `["registered"]`, `["identity_hash"]`, `["vaccination_hash"]` |
| `grant_consent(manager, guardian=, requester=, scope=, duration_days=)` | `["transaction_hash"]` |
| `revoke_consent(manager, guardian=, requester=, scope=)` | `["transaction_hash"]` |
| `check_access(manager, owner=, requester=, scope=)` | `["allowed"]`, `["reason"]` |
| `request_access(manager, requester=, owner=, scope=, observed_hash=)` | `["allowed"]`, `["reason"]`, `["transaction_hash"]` |
| `get_reward_balance(token, account=)` | an int |
| `list_access_events(manager, from_block=0)` | per event: `["timestamp"]`, `["requester"]`, `["scope"]`, `["allowed"]`, `["reason"]` |

Contract names passed to `load_contract` are `"IdentityRegistry"`, `"ConsentManager"` and `"ConsentRewardToken"`. `deployment_path` is the `deployment_file` setting resolved from the project root, so `runtime-data/deployment.json` by default. Return shapes are the TypedDicts in `app/models.py`.

## Rules chain.py has to follow for my side to work

- For anything the node or the deployment can cause, raise only the three exceptions in `app/models.py`:
  - `ChainUnavailable` when the node is down, the chain ID is wrong or the event is missing or wrong. Also use it for a `deployment.json` that is missing, unreadable or stale, or for a missing artifact. "Show my registration" only catches this one, so it has to cover the case where nothing is deployed yet. (Now: those deployment cases raise `DeploymentUnavailable`, a `ChainUnavailable` subclass, and the menu prints "no deployment for this node: run python -m scripts.deploy_local --reset". A missing artifact raises `ArtifactUnavailable`, a `DeploymentUnavailable` subclass, and the menu prints "compiled contracts not found: run npm run compile first"; missing web3 raises `Web3NotInstalled`, another `ChainUnavailable` subclass, and the menu says to install requirements.txt.)
  - `TransactionRejected` for a revert. Its args hold the error name only, because the menu prints it as "rejected: NotTrustedClinic".
  - `TransactionPending` when no receipt comes in time (web3 `TimeExhausted`). (Now also when the node stops answering or goes down after the transaction was sent, because it may still be mined; a failure before the send stays `ChainUnavailable`.)

  In `perform_access` these become unavailable or pending and nothing is released, except `DeploymentUnavailable` (with `ArtifactUnavailable`) and `Web3NotInstalled`, which are passed on to the caller so the menu and the demo can name the fix. (Now: an unavailable response from a chain error has no transaction hash, and `denial_message` then prints "unavailable: local node or receipt problem, nothing released" instead of blaming the local record.) Caller mistakes, such as an unknown label, a hash that is not 32 bytes or a scope or duration that does not fit uint8 or uint16, can stay a plain `ValueError`, as in the fake. An unsupported scope (3) or a duration of 0 or 366 days is not a caller mistake: it reaches the contract and comes back as `UnsupportedScope` or `InvalidDuration`. Any other exception goes up uncaught, and the menu prints only its type name, so it shows up as "failed: KeyError" and so on. (Now true inside `chain.py` as well: its mapping only covers transport and web3 failures, so a bug such as a missing ABI function shows as "failed: ABIFunctionNotFound", not as a node problem.) Never put calldata, local data or paths in a message.
- `request_access` must wait for the receipt, decode exactly one `AccessAttempt` log from the manager's own address (`process_receipt` does not filter by address), and check that owner, requester and scope match what was sent. My code trusts the event it gets back and does not check that again.
- Business denials come back as `allowed=False` with a reason, not as an exception.
- Hashes are raw 32-byte values. HexBytes is fine, because I compare with `==` and print with `bytes(x).hex()`. The zero hash is `bytes(32)`, which I send when the local record, the salt or the owner check fails.
- Addresses can be checksummed. I compare them lowercased.
- `reason` can be a plain int (which is what web3 decodes) or a `Reason` member. Both work.
- Event scope stays a raw int. The audit view shows an unknown one as "scope 3 (unsupported)", and `list_access_events` should never raise on it.
- Transaction hashes are strings with `0x`. In web3 8 `HexBytes.hex()` drops the `0x`, so use `Web3.to_hex(...)` or add it.
- `select_account` should raise for an unknown label and never fall back to guardian or deployer. `perform_access` also checks the label against `actor_account_indices` before calling you.
- Importing `app.chain` must not open a connection. If you can, import web3 inside the functions, so the unit tests and setup keep working with just the standard library.
- `load_contract` should check that the address actually has code, so a stale `deployment.json` after a node restart gives `ChainUnavailable` rather than a confusing error later. (Now: it compares the hash of the block `deploy_local` recorded (`deploy_block`, the block of the registry deploy), the exact runtime code with the `code_hashes` it records and the code with the compiled artifact, so a node restart or a recompiled contract both need `python -m scripts.deploy_local --reset`. The block check matters because `python -m evaluation.measure` on a restarted node deploys identical contracts at the same addresses.)

## Helpers you can reuse

For `register_demo_accounts`, `attest_demo_record` and the rest of the demo:

| Helper | Gives you |
| --- | --- |
| `records.setup_runtime(settings)` | creates the runtime files and salts; safe to run again, and returns the files it made |
| `records.identity_paths(settings, label)` | `(identity_path, salt_path)` for guardian, school or doctor |
| `records.prepare_identity(identity_path, salt_path)` | the 32-byte identity hash for `registerUser` |
| `records.load_snapshot(record_path, salt_path)["commitment"]` | the 32-byte record hash for `registerVaccination` |
| `records.settings_path(settings, key)` | a settings path resolved from the project root |
| `records.shown_path(path, settings)` | a path for console text: relative to the project, `<data_root>/...` or the file name, never the home folder |
| `records.REGISTERING_LABELS` | `("guardian", "school", "doctor")`; deployer and clinic never register |
| `disclosure.verify_for_school(settings, owner)` | the school check; `owner` is the guardian's address |
| `disclosure.get_doctor_schedule(settings, owner)` | the doctor view |
| `disclosure.denial_message(response)` | the console text for a denied, unavailable or pending response |
| `demo_workflow.expect(condition, message)` | a demo check that still runs under `python -O` |

A response is an `AccessResponse`. `outcome` is one of the `OUTCOME_*` constants in `app/models.py`. `fields` is `{}` for everything but allowed. `reason` is a name like `"REVOKED"`, and there is a `transaction_hash`.

## The scripted demo (demo_workflow.main)

Run it with `python -m integration.demo_workflow [--settings PATH]`. It first deploys fresh contracts (as `deploy_local --reset`, replacing `deployment.json`), so it can run again on the same node, and each run moves node time forward about one day. This covers the DEMO.md steps in the file's order: the school revoke comes after the tamper step, which changes nothing, because the tamper step only uses the doctor grant. DEMO.md now lists them in this order too. Run the functions in the order they appear in the file:

1. `register_demo_accounts`: setup, then register guardian, school and doctor.
2. `attest_demo_record`: the clinic registers the record commitment. Show the local JSON and the registered commitment.
3. `demonstrate_school_flow`: school check denied with no payload, then grant school for 30 days (reward +1), then school check gives status only.
4. `demonstrate_doctor_flow`: grant doctor for 30 days, then the doctor view gives vaccine and date only.
5. `demonstrate_tampering` (done): pass `Path("runtime-data/tamper/vaccination_record.json")`. The doctor grant has to still be active, and there must be no time advance before it. It refuses any existing file and any path outside `runtime-data`, and it deletes its copy even if something fails. (Now: `main` passes `records.data_root(settings) / "tamper" / "vaccination_record.json"`, and the check is against the data root.)
6. `demonstrate_revocation_and_expiry`: revoke school (REVOKED), regrant school for 1 day (reward +0), set the next block timestamp to `expiresAt`, then school check (EXPIRED). This one runs last, because node time cannot go back. `advance_time` should refuse unless the chain ID is 31337.

Grants are 30 days in the demo, not 365, so the live run does not depend on the 195-day overflow fix. Please print a readable transcript, because I need it for the report.

## Deploy script

- Deploy in this order:
  1. registry, with the clinic account as `trustedClinic`
  2. token
  3. manager, with the registry and token addresses
  4. `setMinterOnce(manager)` from the deployer
- The deploy functions return `(address, receipt)` and `configure_minter` returns its receipt, so `measure.py` can record the gas.
- `save_deployment` writes `chain_id`, `artifacts_dir` and the three addresses to `runtime-data/deployment.json`.
- ABIs and bytecode come from `artifacts/contracts/<Name>.sol/<Name>.json` after `npm run compile`.
- Add a reset option, because restarting `npm run node` wipes the chain but leaves `deployment.json` behind.
- (Now: `python -m scripts.deploy_local [--reset] [--settings PATH]`. It refuses to replace an existing `deployment.json` without `--reset`, prints each address, transaction, block and gas, checks `trustedClinic()`, the runtime code and `minter()`, and writes the file once, after every step succeeded, with a `code_hashes` entry per contract and `deploy_block`, the number and hash of the block holding the registry deploy.)

## Measurements

- The gas table comes from real receipts: deployment cost per contract and the mean per function, with first rewarded grants kept apart from regrants and allowed access kept apart from denied.
- If you use `--gas-stats` to compare gas before and after an optimisation, call those rows "execution gas", because they leave out the 21k base cost and calldata. (Correction: with Hardhat 3.18 that is not true here. The `--gas-stats` values equal receipt gasUsed, for example registerUser 45,430 and setMinterOnce 47,919, so they are plain gasUsed.)
- Scenarios use 1, 5 and 10 requesters from `scenario_requester_account_indices`, with every role acting. Deploy fresh contracts before each run.
- Time send to receipt and send to decoded event, with a monotonic clock.
- Put "solc 0.8.28, optimiser on, 200 runs" under the gas table. Empty rows mean not measured, never zero. More detail is in [TESTING.md](TESTING.md).
- (Now: `python -m evaluation.measure [--settings PATH] [--output-dir DIR]` writes `evaluation/results/gas_results.csv`, `timing_results.csv` (one row per operation), `timing_summary.csv` (per-role and whole-run means over the same transactions) and `ENVIRONMENT.md`. The timings are local automine numbers, about 5 ms per transaction sent one at a time, so they show growth with N, not throughput. Deployer rows are send to verified deploy, not send to receipt.)

## What the Python side needs from the contracts

Miki and Robin own the contracts. Your `chain.py` sits between them and my code, so these are worth checking with them:

- `requestAccess` never reverts on a business denial. It emits `AccessAttempt` and returns, otherwise there is no event to decode.
- The denial order is the one in CONTRACT_API. The hash is only compared when everything else allows, and any difference, zero included, is `HashMismatch`.
- `getUserInfo` on an unregistered account returns `registered=false` with zero hashes instead of reverting.
- The `Reason` enum keeps the same order as `app/models.py`.
- The expiry is computed as `uint256(durationDays) * 1 days`, otherwise grants of 195 days or more overflow.

## Please tell me before changing

- function or parameter names in `chain.py`
- the TypedDicts, outcome constants or exceptions in `app/models.py`
- settings keys or the `runtime-data` layout

The unit tests patch the `app.chain` functions by name (all thirteen in `FAKED` in `tests/fake_chain.py`, `get_reward_balance`, `list_access_events` and `get_consent` included), so they fail loudly if one of those is renamed.

## First real run together

Once the contracts deploy, and register and attest work, we run the menu on the node (setup, register, attest, grant, school check, doctor view, revoke, rewards, audit), then the scripted demo. After that I add the no-leak checks and the rows for the Python checks to the filled test results table, using those real runs. (Done: the menu and the demo have both run on a node. `python -m evaluation.export_python_results [--settings PATH] [--output PATH]` writes the Python rows to `evaluation/results/test_results.csv` on a running node; it reruns the demo and its own checks, so it replaces `deployment.json` and moves node time forward about two days. The Solidity rows are in `evaluation/results/solidity_test_results.csv`, both under the template header.)
