# UI handoff

Starting point for building the visual UI (bonus points) on top of the finished system. Written 2026-09-29, after the fix and verification passes. Read README.md for setup, then this.

## What was decided

- A local web app: a small Python server that reuses `app/chain.py`, `app/disclosure.py` and `app/records.py`. No separate JS dApp and no second copy of the hashing or disclosure logic.
- A role switcher over the Hardhat unlocked demo accounts (deployer, clinic, guardian, school, doctor, from `actor_account_indices` in settings). No MetaMask. This is demo mode, not real authentication.
- Four areas: role dashboards, a live audit timeline, demo controls (tamper, jump to exact expiry), and gas and timing charts.

## UI scope

Build in this order, and finish each step before starting the next:

1. Role dashboards and the audit timeline: the core story.
2. The guided demo and the demo controls.
3. Charts.

Screens:

- **Top bar:** a role switcher (deployer, clinic, guardian, school, doctor), the acting address, node status (reachable, chain ID, deployed or not) and node time.
- **Setup panel** (what a fresh node shows first): a checklist with one button per step, each acting as the right role:
  - node reachable
  - contracts deployed (show the `deploy_local --reset` hint, or a button that runs it)
  - local files and salts
  - guardian, school and doctor registered
  - record attested
- **Guardian:**
  - registration, and whether the local commitment matches the one on-chain
  - the local record (the guardian owns it, so only this view may show it in full)
  - a consent matrix of school and doctor × MEASLES_STATUS and VACCINATION_SCHEDULE. Each cell shows none, active until a date, revoked or expired, and has grant (1-365 days) and revoke buttons.
  - the reward balance
- **Clinic:** attest the guardian's record. Show the attest button to every role on purpose, so a wrong actor visibly gets "rejected: NotTrustedClinic".
- **School:** "check measles status". Show the status or the denial reason, plus the tx hash.
- **Doctor:** "view vaccination schedule". Show vaccine and date only, or the denial reason, plus the tx hash.
- **Audit timeline:** visible in every role. List the AccessAttempt events newest first: time, requester label, scope, allowed or denied, reason and tx hash. Show an unknown scope as "scope N (unsupported)".
- **Deployer:** the deployment status and the three contract addresses.

Guided demo: a stepper that plays the DEMO.md story one click per step. Each step switches role automatically and highlights its result:

1. register the three accounts
2. attest, including the wrong-actor rejection
3. the school is denied (NO_CONSENT)
4. grant the school 30 days (+1 reward)
5. the school is allowed (status only)
6. grant the doctor
7. the doctor is allowed
8. tamper (HASH_MISMATCH)
9. revoke the school (REVOKED)
10. regrant the school for 1 day (+0 reward)
11. jump to expiry (EXPIRED)

Implementation notes:

- **Consent matrix:** there is no helper that lists grants. Call `chain.get_consent` for each of the 4 requester and scope pairs.
- **Tamper:** `demonstrate_tampering` prints to stdout and raises AssertionError, so don't call it from the server. Do the same steps in the server:
  1. write a new temporary copy under `records.data_root(settings) / "tamper"` with one byte of the batch changed
  2. call `disclosure.get_doctor_schedule(dict(settings, vaccination_file=str(copy)), owner)`
  3. delete the copy in a `finally`
  4. show the HASH_MISMATCH denial, and that the original still matches the on-chain commitment
- **Jump to expiry:** read `expires_at` from `chain.get_consent`, then call `advance_time(client, expires_at, mine=True)`. Warn that node time cannot move back.
- **Charts:** the server returns rows from `evaluation/results/gas_results.csv` and `timing_summary.csv` as JSON. Chart gas per function (first grant vs regrant, allowed vs denied access), and cost against 1, 5 and 10 requesters. Label the timings as local automine, not throughput.
- **Front end:** plain HTML, CSS and JS served by the Python server (`http.server.ThreadingHTTPServer`), with JSON endpoints under `/api/` and no build step. There is no CDN, because the demo must work offline: draw charts as SVG or put one small library file in the repo. Poll every 2 seconds for live updates. Every action shows a pending state, then the outcome, reason and tx hash. Use fonts that are readable on a projector.

Done when:

- On a fresh node and a fresh deploy, the whole guided demo can be clicked through in under 3 minutes, and its outcomes match `python -m integration.demo_workflow`.
- Every denial shows its reason and releases nothing. The school never sees more than the status, and the doctor never more than vaccine and date.
- A stopped node and a missing deployment show the mapped messages, never a stack trace.
- Endpoint tests run without a node, and the existing 38 Solidity and 337 Python tests still pass.
- README.md gives the command that starts the UI.

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
| Demo controls | `integration.demo_workflow.advance_time(client, timestamp, mine=True)` (refuses unless chain 31337). For tamper, see "UI scope" below: don't call `demonstrate_tampering` from the server. |

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
