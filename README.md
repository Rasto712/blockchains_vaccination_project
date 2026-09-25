# My Vaccination Card - one-week scaffold

**Architecture:** three Solidity contracts, a Python console and local JSON on a local Hardhat network.

## What works now

`python -m app.main` prints a startup message and exits. Source files have typed method signatures, class/contract/data declarations, events and detailed implementation comments. Solidity and Python business operations are **not implemented**. Solidity calls/constructors revert NotImplemented; Python workflow stubs raise NotImplementedError. No contract is deployed and no permission, reward, hash or JSON I/O behavior is active.

The three .t.sol files are test outlines and intentionally fail TestNotImplemented until real assertions are written. They are not passing tests or evidence of requirement completion.

## Project layout

```text
contracts/       IdentityRegistry.sol, ConsentManager.sol, ConsentRewardToken.sol
test/            Three matching Solidity .t.sol test outlines
app/             main.py, records.py, chain.py, disclosure.py, models.py
scripts/         deploy_local.py (placeholder)
integration/     demo_workflow.py (placeholder)
evaluation/      measure.py and empty result-table templates
config/          Example local settings and unset deployment addresses
data/examples/   Synthetic local vaccination and identity fixtures
docs/            Revised PDF, architecture/UML, developer tasks and report outline
```

## Start here

1. Read [developer plan](docs/developer_plan.pdf), [team assignments](docs/TEAM_TASKS.md) and [architecture](docs/ARCHITECTURE.md). Read developer_plan.pdf together with [docs/PLAN_ERRATA.md](docs/PLAN_ERRATA.md); where the PDF differs from the Markdown docs or code docstrings, the Markdown docs and docstrings win.
2. Agree [contract API](docs/CONTRACT_API.md) and Lab 3 versions before implementing.
3. Implement one owned feature at a time; replace placeholder failures with the documented behavior.
4. Use [validation requirements](docs/TESTING.md), [report outline](docs/REPORT_OUTLINE.md) and [demo checklist](docs/DEMO.md).

## Run the scaffold

From the project root with Python 3.10 or newer.

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m app.main
```

If `python3 --version` is older than 3.10 (the macOS system Python is 3.9), use a newer one in the first line, for example `python3.13 -m venv .venv`.

Windows PowerShell (no activation needed):

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m app.main
```

No third-party Python package is required for the scaffold startup. The live file is config/settings.json, copied from config/settings.example.json; run npm run compile before any Python chain command. Do not call deployment/integration scripts expecting success yet.

Python unit tests use the standard library, no pytest: `python -m unittest discover -s tests` (tests/ is separate from Hardhat's test/).

## Hardhat build setup

package.json pins Hardhat 3.18.0 and forge-std; hardhat.config.ts pins solc 0.8.28 (optimiser on, 200 runs). Requires Node 22.13 or newer. Checked with npm ci, npx hardhat build and npx hardhat test solidity. If Lab 3 uses another Hardhat 3 version, change package.json and hardhat.config.ts together.

```powershell
npm ci
npm run compile
npm test
npm run node
```

npm ci uses the committed package-lock.json, so git and SSH keys are not needed (forge-std comes from GitHub over HTTPS). Use npm install only when changing dependencies, then commit the new lockfile. An npm 11 'allowScripts' warning for esbuild is harmless.

`npm test` is intentionally unsuccessful until test outlines and contracts are implemented. `npm run node` only starts a local blockchain; it does not deploy or complete the application. Contract constructors and the Python deployment script currently fail explicitly. The only TypeScript file is the Hardhat configuration; there is no frontend.

## Rules that must survive implementation

- Child data stays in local JSON. Store only salted identity/record commitments and permission/audit metadata on-chain.
- Guardian controls its own grants; clinic alone attests; requester identity comes from the transaction signer.
- Duration is 1-365 whole days; consent is invalid at the exact expiry timestamp.
- Both allowed and denied access attempts need committed events. Remove placeholder reverts from the final business-denial path, since reverting would erase events.
- Reward once per lifetime owner/requester/scope tuple. No tokens move during access; reward balances never authorize access.
- School sees status only; doctor sees vaccine/date only. Failed evidence is unavailable, not a clinical NO.

## Official setup references

- https://hardhat.org/docs/reference/configuration
- https://hardhat.org/docs/guides/testing/using-solidity

See docs/SCAFFOLD_VALIDATION.md for the checks actually performed on the scaffold. Compiler success is not functional correctness.
