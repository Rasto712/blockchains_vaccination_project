# My Vaccination Card - one-week scaffold

**New architecture: three Solidity contracts + Python console + local JSON + local Hardhat.** This replaces the previous Java/Fabric source layout. The earlier Desktop project contents are preserved in archive/previous_java_scaffold.zip; its Git history and IDE folder are retained.

## What works now

`python -m app.main` prints a startup message and exits. Source files have typed method signatures, class/contract/data declarations, events and detailed implementation comments. Solidity and Python business operations are **not implemented**. Solidity calls/constructors revert NotImplemented; Python workflow stubs raise NotImplementedError. No contract is deployed and no permission, reward, hash or JSON I/O behavior is active.

The three .t.sol files are test outlines and intentionally fail TestNotImplemented until real assertions are written. They are not passing tests or evidence of requirement completion. Files that are purely configuration, JSON or Markdown describe data/settings and do not contain artificial methods.

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

1. Read [developer plan](docs/developer_plan.pdf), [team assignments](docs/TEAM_TASKS.md) and [architecture](docs/ARCHITECTURE.md).
2. Agree [contract API](docs/CONTRACT_API.md) and Lab 3 versions before implementing.
3. Implement one owned feature at a time; replace placeholder failures with the documented behavior.
4. Use [validation requirements](docs/TESTING.md), [report outline](docs/REPORT_OUTLINE.md) and [demo checklist](docs/DEMO.md).

## Run the scaffold

From the project root with Python 3.10 or newer:

```powershell
python -m app.main
```

No third-party Python package is required for the scaffold startup. Developer 4 adds and pins the lab-compatible web3.py dependency when implementing the RPC helper. Do not call deployment/integration scripts expecting success yet.

## Hardhat build setup

The package manifest pins Hardhat 3.18.0 (verified in the npm registry) and Solidity 0.8.28. Configuration follows Hardhat 3's built-in Solidity test support. Use a supported modern Node release (the local verification runtime is Node 24); align with your lab template if its exact setup differs.

```powershell
npm install
npm run compile
npm test
npm run node
```

`npm test` is intentionally unsuccessful until test outlines and contracts are implemented. `npm run node` only starts a local blockchain; it does not deploy or complete the application. Contract constructors and the Python deployment script currently fail explicitly. The only TypeScript file is Hardhat configuration; there is no JavaScript application/frontend.

## Rules that must survive implementation

- Child data stays in local JSON. Store only salted identity/record commitments and permission/audit metadata on-chain.
- Guardian controls its own grants; clinic alone attests; requester identity comes from the transaction signer.
- Duration is 1-365 whole days; consent is invalid at the exact expiry timestamp.
- Both allowed and denied access attempts need committed events. Remove placeholder reverts from the final business-denial path, since reverting would erase events.
- Reward once per lifetime owner/requester/scope tuple. No tokens move during access; reward balances never authorize access.
- School sees status only; doctor sees vaccine/date only. Failed evidence is unavailable/invalid, not a clinical NO.

## Official setup references

- https://hardhat.org/docs/reference/configuration
- https://hardhat.org/docs/guides/testing/using-solidity

See docs/SCAFFOLD_VALIDATION.md for the checks actually performed on this delivery. Compiler success is not functional correctness.
