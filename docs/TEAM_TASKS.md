# Five developer assignments

The Python side is implemented; the contracts, chain.py, the deploy script, the measurements and most of the scripted demo are still scaffold stubs. Read developer_plan.pdf for the full architecture; where it differs from the Markdown docs or code docstrings, the Markdown docs and docstrings win.

| Developer | Responsibility | Current owner |
| --- | --- | --- |
| 1 | [Identity and consent contracts](tasks/DEV_1.md) | Miki |
| 2 | [Reward token and Solidity unit tests](tasks/DEV_2.md) | Robin |
| 3 | [Python console, JSON and minimal disclosure](tasks/DEV_3.md) | Magdy |
| 4 | [Hardhat setup, integration and measurements](tasks/DEV_4.md) | Rasto |
| 5 | [Research, documentation and submission](tasks/DEV_5.md) | Ahmed |

Files by owner:

- Miki: contracts/IdentityRegistry.sol and contracts/ConsentManager.sol.
- Robin: contracts/ConsentRewardToken.sol and test/.
- Magdy: app/records.py, app/disclosure.py, app/main.py, data/ and tests/ (Python unit tests).
- Rasto: app/chain.py, scripts/, evaluation/, config/ and the root tooling (package.json, package-lock.json, hardhat.config.ts, requirements.txt, .gitattributes).
- Ahmed: the report, README.md, docs/ARCHITECTURE.md, docs/DEMO.md, docs/REPORT_OUTLINE.md and the slides.
- Shared: app/models.py (Magdy and Rasto), integration/demo_workflow.py (Rasto, with the tamper step from Magdy) and the rest of docs/.

Freeze signatures/account mapping on Day 1; integrate on Days 3-4; freeze features after Day 4; tests and measurements on Days 5-6; rehearse on Day 7.
