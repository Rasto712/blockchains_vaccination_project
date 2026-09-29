# UI handoff

Starting point for building the visual UI (bonus points) on top of the finished system. Written 2026-09-29, after the fix and verification passes. Read README.md for setup, then this.

## What was decided

- A local web app: a small Python server that reuses `app/chain.py`, `app/disclosure.py` and `app/records.py`. No separate JS dApp and no second copy of the hashing or disclosure logic.
- A role switcher over the Hardhat unlocked demo accounts (deployer, clinic, guardian, school, doctor, from `actor_account_indices` in settings). No MetaMask. This is demo mode, not real authentication.
- Four areas: role dashboards, a live audit timeline, demo controls (tamper, jump to exact expiry), and gas and timing charts.

## State it starts from

- 38 Solidity tests and 337 Python unit tests pass. `python -m integration.demo_workflow` and the full menu story pass on a live node.
- Result tables are in `evaluation/results/`: `gas_results.csv`, `timing_results.csv`, `timing_summary.csv`, `solidity_test_results.csv`, `test_results.csv` (PY rows) and `ENVIRONMENT.md`. Their headers match `evaluation/templates/`.

## Running the backend it needs

```bash
npm run compile
npm run node                                   # separate terminal, port 8545
cp config/settings.example.json config/settings.json
.venv/bin/python -m scripts.deploy_local --reset
.venv/bin/python -m app.main                   # the console, for comparison
```

## Python calls to reuse

The server should call these rather than web3 directly, so the error mapping and the privacy rules stay in one place.

| Need | Call |
| --- | --- |
| Connect and pick an account | `chain.connect(settings)`, `chain.select_account(client, label, settings)` |
| Contracts | `chain.load_contract(client, name, records.settings_path(settings, "deployment_file"))` |
| Local files and salts | `records.setup_runtime(settings)` |
| Identity or record hash | `records.prepare_identity(*records.identity_paths(settings, label))`, `records.load_snapshot(record_path, salt_path)["commitment"]` |
| Register or attest | `chain.register_user(...)`, `chain.register_vaccination(...)` |
| Grant, revoke or read a consent | `chain.grant_consent(...)`, `chain.revoke_consent(...)`, `chain.get_consent(...)` (gives `expires_at` and `revoked`) |
| School and doctor views | `disclosure.verify_for_school(settings, owner)` and `disclosure.get_doctor_schedule(settings, owner)` return an `AccessResponse` |
| Messages for denials | `disclosure.denial_message(response)` |
| Rewards | `chain.get_reward_balance(token, account=...)` |
| Audit timeline | `chain.list_access_events(manager, from_block=0)` |
| Registration view | `chain.get_user_info(registry, account=...)` |
| Demo controls | `integration.demo_workflow.advance_time(client, timestamp, mine=True)` (refuses unless chain 31337); `demonstrate_tampering(settings, records.data_root(settings) / demo_workflow.TAMPER_COPY)` |

`app/main.py`'s `do_*` handlers show how each action is wired, with the exact console texts.

## Rules the UI must keep

- The school sees measles status only. The doctor sees vaccine and date only. Always go through `disclosure`. Never read the record file and show it to a requester.
- Denied, unavailable and pending responses carry no fields. A failed check is "unavailable", never a clinical NO.
- Never show exception text, file paths, salts or raw record JSON. Map the three model exceptions:
  - `TransactionRejected`: `args[0]` is the Solidity error name (e.g. `NotTrustedClinic`)
  - `TransactionPending`: no receipt in time
  - `ChainUnavailable` and its subclasses `DeploymentUnavailable`, `ArtifactUnavailable` and `Web3NotInstalled`: use `disclosure.NO_DEPLOYMENT_MESSAGE`, `NO_ARTIFACTS_MESSAGE` or `CHAIN_UNAVAILABLE_MESSAGE`
- The requester is always the selected account. Business denials come back as `allowed=False` with a reason, not as an exception.
- Bind the server to 127.0.0.1 only. The app is local.

## Gotchas

- `load_contract` checks the exact runtime code and the deploy block. After a recompile or a node restart, every chain call raises `DeploymentUnavailable` until `python -m scripts.deploy_local --reset` runs. The UI should show that hint and could offer a redeploy button.
- Node time only moves forward. The demo moves it about 1 day per run and `evaluation.measure` about 3 days; both redeploy and replace `deployment.json`.
- `python -m app.main` has no `--settings` flag; it always reads `config/settings.json`. The other entry points take `--settings`.
- The existing `.venv` still points at the project's old folder for `pip` and `activate`. Recreate it if `pip` fails: `rm -rf .venv && python3.13 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`.
- Keep to the standard library plus web3 on the Python side. Add an "AI assistance" line to each new file, as the other files have.
