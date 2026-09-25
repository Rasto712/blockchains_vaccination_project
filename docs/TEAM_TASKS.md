# Five developer assignments

The source is a scaffold, not implemented functionality. Read developer_plan.pdf (with [PLAN_ERRATA.md](PLAN_ERRATA.md)) for the full revised architecture and week schedule.

| Developer | Responsibility | Current owner |
| --- | --- | --- |
| 1 | [Identity and consent contracts](tasks/DEV_1.md) | Rasto712 |
| 2 | [Reward token and Solidity unit tests](tasks/DEV_2.md) | Rasto712 |
| 3 | [Python console, JSON and minimal disclosure](tasks/DEV_3.md) | Magdy Fares |
| 4 | [Hardhat setup, integration and measurements](tasks/DEV_4.md) | Magdy Fares |
| 5 | [Research, documentation and submission](tasks/DEV_5.md) | shared |

Files by owner:

- Rasto712: contracts/ and test/.
- Magdy Fares: app/ (including the shared app/models.py), scripts/, integration/, evaluation/, config/, data/ and the root tooling (package.json, package-lock.json, hardhat.config.ts, requirements.txt, .gitattributes).
- Shared: README.md and docs/.

Inactive members: each gets one concrete task that nobody else waits on, for example a cited research section for the Introduction, an independent clean-clone run of the README, or a first slides draft. If they do not take it up, the active two take it over and the contribution statement says so.

Freeze signatures/account mapping on Day 1; integrate on Days 3-4; freeze features after Day 4; tests and measurements on Days 5-6; rehearse on Day 7.

## Rules for everyone

- Generative AI (Option 2): only for explanations, debugging, review and adapted examples with credit; never ask it to write your function bodies; never submit AI output you have not fully reviewed; you may be asked to explain your code.
- Where AI helped with a file, add a header comment: `AI assistance: <tool>, used for <purpose>; reviewed by <name>`.
- The report ends with each member's contribution and the transparency statement (coursebook p.7); each author declares their own AI-assisted parts, including any scaffold, docstrings or plan text.
- Members without an adequate contribution get a zero; commit under your own name.
