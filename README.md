# My Vaccination Card

A guardian keeps a child's vaccination card as a local file and decides who may check it. Three Solidity contracts on a local Hardhat node record identities, consent and every access attempt. A Python console plays each role: clinic, guardian, school and doctor.

## What it demonstrates

- **Private data stays off-chain.** The card and identities stay in local JSON files. The chain stores only salted hash commitments (a hash of the file plus a secret random value) and consent and audit data.
- **The guardian controls consent.** Each grant covers one requester and one scope, lasts 1-365 days and can be revoked. The school sees only measles status; the doctor sees only vaccine and date.
- **Trusted attestation and tamper detection.** Only the clinic can attest a record. If the local copy changes, its hash no longer matches and nothing is released.
- **A full audit trail.** Every access request, allowed or denied, is logged on-chain with a reason.
- **A non-transferable reward.** The guardian gets one reward unit for the first grant to each requester and scope. It never grants access.

## Prerequisites

- Node.js 22.13 or newer, with npm.
- Python 3.10 or newer.
- Internet access for the first install.

## Quick start

Run every command from the project root; start Python with `-m` as shown.

1. Install and compile (once):

   ```bash
   npm ci
   npm run compile
   python3.13 -m venv .venv      # any Python 3.10 or newer
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   cp config/settings.example.json config/settings.json
   ```

   The settings file sets the node address, the Hardhat account for each role and the local file paths. The defaults work as is.

2. Start the local node in its own terminal and leave it running:

   ```bash
   npm run node
   ```

3. In a second terminal, activate the venv (`source .venv/bin/activate`) and run the scripted demo:

   ```bash
   python -m integration.demo_workflow
   ```

   It deploys fresh contracts, runs the whole story, checks every result and ends with `demo passed`.

4. Try the interactive menu on fresh contracts:

   ```bash
   python -m scripts.deploy_local --reset
   python -m app.main
   ```

   Pick an actor, then a numbered action. [DEMO.md](docs/DEMO.md) gives the order to try them in.

5. Or use the web UI, with the node still running:

   ```bash
   python -m ui.server
   ```

   Open http://127.0.0.1:8000/, pick a role, or press **Guided demo** to play the scripted demo one click per step.
   The page calls the contracts from the browser with Viem: register, attest, grant, revoke, and the registrations, consents, rewards and audit it shows. School check, doctor view, setup, deploy and the guided demo run in the Python server, because disclosure needs the local card and salts, which never reach the browser.

## Documentation

Reading order:

| Doc | Read it for |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it works: components, roles, privacy model, access flow, design decisions, limitations |
| [docs/DEMO.md](docs/DEMO.md) | The demo step by step, with the expected results |
| [docs/TESTING.md](docs/TESTING.md) | The evidence: recorded test, gas and timing results (in evaluation/results/) and how to rerun them |
| [docs/CONTRACT_API.md](docs/CONTRACT_API.md) | Contract reference: functions, reverts, reason codes, rules |
| [data/README.md](data/README.md) | The local data files and the commitment formula |

## Project layout

```text
contracts/     the three Solidity contracts
test/          Solidity tests, one .t.sol per contract
app/           Python console (main.py) and its modules
tests/         Python unit tests
scripts/       deploy_local.py: deploys the contracts
integration/   demo_workflow.py: the scripted demo
ui/            web UI: python -m ui.server serves it on 127.0.0.1
evaluation/    measurement and export scripts; results/ holds the recorded results
config/        settings.example.json (copy to settings.json)
data/          synthetic example card and identities
docs/          the docs above
runtime-data/  made at run time, git ignored: local files, salts, deployment.json
```

## Troubleshooting

| Message | Fix |
| --- | --- |
| `no deployment for this node: run python -m scripts.deploy_local --reset` | The node keeps everything in memory, so a restart wipes the contracts. Run that command, then register and attest again. |
| `local node not reachable or wrong chain` | Start `npm run node` and keep it running. `rpc_url` in config/settings.json must match its address. |
| `web3 not installed: ...`, or a `TypeError` at start | You ran a Python other than the venv's, or one older than 3.10. Activate the venv, or recreate it with Python 3.10 or newer. |

