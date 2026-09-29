# My Vaccination Card

A guardian keeps a child's vaccination card as a local JSON file and decides who may check it. Three Solidity contracts run on a local Hardhat node:

- IdentityRegistry stores each registered account's salted identity hash and one vaccination-record commitment per guardian, attested only by a fixed trusted clinic.
- ConsentManager holds the guardian's scoped, time-limited grants (1-365 days) and revocations, and emits one AccessAttempt event for every access request, allowed or denied.
- ConsentRewardToken keeps non-transferable reward units: only ConsentManager mints them, one per first grant of an owner/requester/scope.

A Python console (app/main.py, with web3) acts as clinic, guardian, school or doctor. Privacy model: the card, the identities and the salts stay in local files, and the chain holds only salted SHA-256 commitments and consent and audit metadata. For each request Python hashes the local copy, logs the attempt on-chain, compares the hash with the attested commitment, rechecks consent and releases only the fields of that scope: measles status for the school, vaccine and date for the doctor.

## Project layout

```text
contracts/       IdentityRegistry.sol, ConsentManager.sol, ConsentRewardToken.sol
test/            Solidity tests, one .t.sol per contract (38 tests)
app/             main.py (menu), records.py, disclosure.py, chain.py (web3), models.py
tests/           Python unit tests (standard library unittest); fake_chain.py stands in for app/chain.py
scripts/         deploy_local.py: deploys the contracts and writes deployment.json
integration/     demo_workflow.py: the scripted end-to-end demo
evaluation/      measure.py, export_solidity_results.py, export_python_results.py, templates/ (headers only) and results/
config/          settings.example.json (copy to settings.json) and deployment.example.json (shape only)
data/            README.md (local data format) and examples/: synthetic vaccination and identity fixtures
runtime-data/    Git ignored. Setup makes the frozen record, identities and salts; deploy_local.py adds deployment.json
docs/            ARCHITECTURE.md, CONTRACT_API.md, TESTING.md, DEMO.md
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md): the components, the access flow and what Python and the contracts each enforce.
- [Contract API](docs/CONTRACT_API.md): functions, events, errors, reason codes and revert orders.
- [Testing](docs/TESTING.md): the Solidity and Python tests, their IDs, and how the measurements and results are made.
- [Demo walkthrough](docs/DEMO.md): the demo story step by step.
- [evaluation/results/](evaluation/results/): the recorded gas and timing tables, the Solidity (SOL) and Python (PY) test results, and [ENVIRONMENT.md](evaluation/results/ENVIRONMENT.md) with the machine, node and commit they came from.

## Run

A fresh copy of the project has no node_modules/, .venv/ or compiled artifacts/ (all git ignored), so run steps 1 and 2 before anything else.

From the project root, in this order. Needs Node 22.13 or newer and Python 3.10 or newer: the Python code uses 3.10 syntax, and web3 8 needs 3.10 too. Run Python files with -m from the project root (for example `python -m integration.demo_workflow`), because running a file by its path cannot find the app package.

### 1. Contracts and Solidity tests

```bash
npm ci
npm run compile
npm test
```

`npm ci` installs the pinned Hardhat and forge-std into node_modules/. `npm run compile` writes artifacts/ (git ignored), which every Python chain command reads, so run it again after any contract change. `npm test` runs `npx hardhat test solidity` and should print `38 passing`; it needs no node.

### 2. Python

macOS / Linux:

```bash
python3.13 -m venv .venv    # any Python 3.10 or newer; not plain python3 on macOS, which is 3.9
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests
cp config/settings.example.json config/settings.json
```

Use whichever Python 3.10+ you have in the first line (`python3.12`, `python3.11`, ...). The macOS system `python3` is 3.9: pip finds no web3 8.0.0 for it, and even the menu stops with a TypeError, because the code uses 3.10 syntax. The unit tests need no node and should end with `Ran 337 tests` and `OK`. requirements.txt installs web3 8.0.0, which every chain action needs; without it the 86 tests that need web3 are skipped, and the chain commands say "web3 not installed: run python -m pip install -r requirements.txt with the venv's Python".

Windows PowerShell (no activation needed; not tested on this version):

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests
copy config\settings.example.json config\settings.json
```

config/settings.json is your local copy (git ignored):

- rpc_url, `http://127.0.0.1:8545`, is where `npm run node` listens. For another port, start the node with `npm run node -- --port 8546` and change rpc_url to match.
- actor_account_indices maps deployer, clinic, guardian, school and doctor to the node's accounts 0-4, and scenario_requester_account_indices (5-14) are the extra requesters for the measurements.
- Paths are relative to the project root; an absolute path is used as given.
- data_root (runtime-data by default) is the folder the frozen record must be inside. Setup refuses a vaccination_file outside it ("record path must be inside the data root") and creates nothing, and the demo's tamper copy goes to `<data_root>/tamper/`. To keep the local files outside the repo, point data_root and the five other path settings (deployment_file, vaccination_file, vaccination_salt_file, identity_directory, identity_salt_directory) at one folder together.
- The menu reads only config/settings.json; deploy, the demo, the measurement and the Python check export take `--settings PATH`.

### 3. Local node

```bash
npm run node
```

Keep it running in its own terminal. It is a Hardhat node on 127.0.0.1:8545, chain ID 31337, with 20 unlocked test accounts and automine. It keeps everything in memory, so stopping it wipes every contract and all node time; run the deploy again afterwards.

### 4. Deploy

```bash
python -m scripts.deploy_local
```

Deploys IdentityRegistry (with the clinic account as trustedClinic), ConsentRewardToken, ConsentManager (with the registry and token addresses), then calls setMinterOnce(manager) from the deployer. Each step prints its address, transaction hash, block and gas, and is checked before the next one: trustedClinic(), the runtime code and minter(). Only after every step succeeded does it write runtime-data/deployment.json (the deployment_file setting) with the chain ID, the number and hash of the block holding the IdentityRegistry deploy, the addresses and each contract's runtime-code hash; a failed run leaves the old file as it was. Printed paths are relative to the project, or `<data_root>/...` for a data_root outside it, never with your home folder.

- It refuses to replace an existing deployment.json. Add `--reset` after restarting the node, after `npm run compile` picked up a contract change, or whenever you want fresh contracts. The old contracts, with their registrations, grants and rewards, are left behind, so register and attest again.
- `--settings PATH` uses another settings file instead of config/settings.json. The demo and the measurement take it too; the menu always reads config/settings.json.

### 5. Menu

```bash
python -m app.main
```

Pick an actor, then a numbered action: 1 set up local files and salts, 2 register, 3 attest, 4 grant, 5 revoke, 6 school check, 7 doctor view, 8 rewards, 9 audit, 10 show my registration, 11 switch actor, 12 quit. The full story, in order:

1. As guardian: 1 (setup, once) and 2 (register). Switch to school and doctor and register each.
2. As clinic: 3 (attest the guardian's record). Anyone else gets "rejected: NotTrustedClinic".
3. As guardian: 10 shows the local and on-chain commitments matching.
4. 6 (school check): denied, NO_CONSENT.
5. As guardian: 4 (grant school MEASLES_STATUS for 30 days, reward 0 -> 1), then 6: "allowed: measles status verified".
6. As guardian: 4 (grant doctor VACCINATION_SCHEDULE for 30 days, 1 -> 2), then 7: "allowed: MMR on 2026-03-12".
7. As guardian: 5 (revoke school), then 6: denied, REVOKED.
8. 8 (rewards) and 9 (audit).

The school check and the doctor view always sign as school and doctor, whatever actor is selected. Setup and "show my registration" work without a node. Setup is safe to run again, because it leaves existing files alone. Do not delete runtime-data/ or its salts after the clinic has attested unless you also redeploy: new salts change every hash, and the registry refuses a second registration or attestation. Tamper and exact expiry only run in the scripted demo.

### 6. Scripted demo

```bash
python -m integration.demo_workflow
```

Checks the node is chain 31337, deploys fresh contracts (as `deploy_local --reset`, so it replaces deployment.json), then runs register, attest (the guardian's own attempt is rejected first), school, doctor, tamper, revoke, a 1-day regrant and the exact expiry, and prints the reward balances and the audit log. Every outcome is checked: it ends with "demo passed" or "demo FAILED at step N: reason" (exit 1). Local files and salts are created only if missing, so the hashes stay the same between runs. Each run moves node time forward about one day, which cannot be undone short of restarting the node. Afterwards the menu works on the demo's contracts: the accounts are registered and the doctor's 30-day grant is still active. See [DEMO.md](docs/DEMO.md).

### 7. Measurements

```bash
python -m evaluation.measure
```

For 1, 5 and 10 requesters it deploys its own fresh contracts (deployment.json is not touched) and runs a scenario in which every role acts. Each of the three scenarios moves node time forward about one day, so one run moves it about three days. It needs automine and chain 31337, which `npm run node` gives, and takes `--settings PATH`. It writes evaluation/results/gas_results.csv, timing_results.csv (one row per operation, so the sample counts add up to each run's transactions), timing_summary.csv (per-role and whole-run means over the same transactions, never to be added to the other rows) and ENVIRONMENT.md, and nothing if any step fails; `--output-dir DIR` writes them elsewhere. After a node restart, run `python -m scripts.deploy_local --reset` before the menu even if the measurement already ran: it deploys identical contracts at the same addresses, which the menu refuses because the recorded deploy block no longer matches. The timings are local automine numbers, not public-chain throughput ([TESTING.md](docs/TESTING.md)).

### 8. Solidity test results

```bash
python -m evaluation.export_solidity_results
```

Runs `npx hardhat test solidity` (no node needed) and writes evaluation/results/solidity_test_results.csv (the SOL rows) and solidity_test_run.log. It exits 0 only when the CSV was written and every test passed. It never touches the Python (PY) rows, which use the same header and are written by step 9 to evaluation/results/test_results.csv.

### 9. Python check results

```bash
python -m evaluation.export_python_results
```

Needs the node (chain 31337 only) and takes `--settings PATH` and `--output PATH` (default evaluation/results/test_results.csv). It runs the unit tests with and without web3, the scripted demo (step 6) as a subprocess, then its own checks on fresh contracts through the app code (register, wrong-actor attests, school and doctor, a tampered copy, a missing salt, a late recheck, unsupported scopes, a pending and an unreachable request, rewards, expiry), the console's failure messages, and a scan of every transaction for the record, identity and salt values. It writes the 13 PY rows (PY-01..PY-13) under the shared header and exits 0 only when every row passed; when it cannot finish it writes nothing and removes the previous CSV. It traces every transaction, so it takes longer than the demo. Side effects: it replaces deployment_file twice (the demo, then its own deploy), turns automine off and on again for the pending case, and moves node time forward about two days (the demo's day and its own 1-day expiry). Its temporary files go under data_root and are deleted again. Afterwards the menu works on the exporter's contracts in their final state (for example the school has an active 1-day grant), so run `python -m scripts.deploy_local --reset` for a clean menu session.

### What each command writes

| Command | Writes |
| --- | --- |
| `npm ci` | node_modules/ |
| `npm run compile`, `npm test` | artifacts/ and cache/ (npm test builds first) |
| `python -m unittest discover -s tests` | nothing in the project (temporary folders only) |
| `cp config/settings.example.json config/settings.json` | config/settings.json |
| `npm run node` | nothing on disk; the chain lives in memory |
| `python -m scripts.deploy_local` | deployment_file (runtime-data/deployment.json by default) |
| `python -m app.main` | setup: 3 identities, the frozen record and 4 salts at the settings paths (under runtime-data/ by default); chain actions only write on-chain |
| `python -m integration.demo_workflow` | replaces deployment_file; any missing runtime files; a tamper copy that it deletes again (the empty tamper/ folder stays) |
| `python -m evaluation.measure` | evaluation/results/gas_results.csv, timing_results.csv, timing_summary.csv, ENVIRONMENT.md (or the `--output-dir` folder) |
| `python -m evaluation.export_solidity_results` | evaluation/results/solidity_test_results.csv, solidity_test_run.log |
| `python -m evaluation.export_python_results` | evaluation/results/test_results.csv (or `--output`); replaces deployment_file; any missing runtime files; temporary files under data_root that it deletes again |

## Troubleshooting

- "no deployment for this node: run python -m scripts.deploy_local --reset": the node answers, but deployment.json is missing, is for another chain, is from before a node restart, or its contracts are not there with the exact recorded code. load_contract compares the hash of the recorded deploy block (a restarted node mined its blocks at other times, even if `python -m evaluation.measure` then put identical contracts at the same addresses), the runtime code with the recorded hash, and the code with the compiled artifact (even a comment edit in a .sol changes it). A deployment.json written before the deploy block was recorded is refused too. Run `python -m scripts.deploy_local --reset`, then register and attest again.
- "local node not reachable or wrong chain" (menu, deploy or measure) or "RPC connection failed" (demo): the node is down or on another port. Start `npm run node`, and check that rpc_url and expected_chain_id in the settings match it.
- "unavailable: local node or receipt problem, nothing released" (school check or doctor view): the node failed during the request, so nothing was confirmed on-chain; the local record was not judged. "unavailable: local record could not be verified, not a clinical result" means the request was logged, but the local record or its salt is missing, broken or does not match the registered commitment.
- "pending: not confirmed": the node took the transaction but no receipt came back in time, or the node stopped answering while Python waited. It may still be mined, so check with "show my registration", rewards or audit before trying again.
- "web3 not installed: run python -m pip install -r requirements.txt with the venv's Python": the Python you ran has no web3 (for example the system python3 instead of the venv's). `python -c "import web3"` should work afterwards.
- "compiled contracts not found: run npm run compile first" (menu, deploy, demo, measure): run `npm ci` and `npm run compile`.
- `source .venv/bin/activate` works but then `python` or `pip` is "command not found": the venv was created in another folder and the project was moved since. A venv remembers its folder, so recreate it: `rm -rf .venv`, then step 2.
- "failed: <ErrorType>" in the menu: a bug in the Python code, not a node problem; the menu shows only the exception type.
- "runtime-data/deployment.json already exists; rerun with --reset" (or `<data_root>/deployment.json` for a data_root outside the project): see step 4.
- "unavailable: record path must be inside the data root" at setup: vaccination_file is not inside data_root; see the settings list in step 2.

## Hardhat build setup

package.json pins Hardhat 3.18.0 and forge-std; hardhat.config.ts pins solc 0.8.28 (optimiser on, 200 runs). To use another Hardhat 3 version, change package.json and hardhat.config.ts together. npm ci uses the committed package-lock.json, so git and SSH keys are not needed (forge-std comes from GitHub over HTTPS). Use npm install only when changing dependencies, then commit the new lockfile. An npm 11 warning that esbuild and fsevents have install scripts not covered by allowScripts is harmless. The only TypeScript file is the Hardhat configuration; there is no frontend.

## Design rules

- Child data stays in local JSON. Store only salted identity/record commitments and permission/audit metadata on-chain.
- Guardian controls its own grants; clinic alone attests; requester identity comes from the transaction signer.
- Duration is 1-365 whole days; consent is invalid at the exact expiry timestamp.
- Allowed and denied access attempts both leave a committed event: a well-formed business denial returns allowed = false and a reason instead of reverting, because a revert would erase the event.
- Reward once per lifetime owner/requester/scope tuple. No tokens move during access; reward balances never authorize access.
- School sees status only; doctor sees vaccine/date only. Failed evidence is unavailable, not a clinical NO.

## Official setup references

- https://hardhat.org/docs/reference/configuration
- https://hardhat.org/docs/guides/testing/using-solidity
