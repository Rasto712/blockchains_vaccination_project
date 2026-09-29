# Report outline - Developer 5

This is a checklist, not a completed assessed report.

## Format

A4, 1-inch margins, 10-pt Times New Roman, double spaced. At most 10 pages including cover, tables, figures and appendices (references and code excluded), so aim for about 9.5. Cover page with case title, names and student IDs. Numbered pages. One reference style.

## Sections (use these headers exactly)

1. Introduction: problem, 2-3 cited current systems, aims, no solution details.
2. Architecture: roles and permissions, numbered requirements, attribute table (on-chain, off-chain, hashed), consent model with a state diagram or pseudocode, audit log design (who, what, when, granted/denied; logs cannot be deleted), component diagram mapped to the brief's Digital Identity and Data Sharing, access sequence.
3. Implementation: contracts and key functions, Python modules, commitment scheme, requirement to function to test table, commands.
4. Experimental Results: unit test table with why each matters, integration test, gas table (deployment and per function), optimisation choices with measured effect where a comparison exists, 1/5/10 multi-role simulation (time and cost).
5. Discussion: limitations, honestly stated (for example: role selection is local demo mode, Python mediates disclosure, hashes do not prove physical delivery or clinical completeness).
6. Conclusion: brief, no new ideas.
7. Contribution and use of generative AI.

Link each design choice to course content.

## Where the evidence is (2026-09-28)

Cite only these files and runs. Commands are in the [README](../README.md) and caveats in [TESTING.md](TESTING.md).

- Unit test table: evaluation/results/solidity_test_results.csv (one SOL row per Solidity test, 38 after the rerun, `python -m evaluation.export_solidity_results`) and evaluation/results/test_results.csv (13 PY rows, same header, `python -m evaluation.export_python_results`, which needs the node). Test catalogue and the mutation check (37 planted bugs, all caught) are in [FROM_DEV_2.md](FROM_DEV_2.md). The 337 Python unit tests (`python -m unittest discover -s tests`) are counted per file in TESTING.md.
- Integration test: the transcript of `python -m integration.demo_workflow`, which checks every outcome and prints the six-event audit log. The script does not save it, so keep the console output of the run you cite.
- Gas table: evaluation/results/gas_results.csv, from `python -m evaluation.measure`. Put "solc 0.8.28, optimiser on, 200 runs" under it.
- 1/5/10 simulation: evaluation/results/timing_results.csv (one row per operation, so its sample counts add up to each run's transactions), with the per-role and whole-run means in evaluation/results/timing_summary.csv and the machine and node in evaluation/results/ENVIRONMENT.md. Never add the summary rows to the per-operation ones. These are local automine timings for transactions sent one at a time; they show growth with N, not public-chain throughput. Deployer rows are send to verified deploy, not send to receipt.
- Optimisation with measured effect: dropping the registry's separate registered flag cut registerUser from 70,055 to 45,430 gas (code review measurement at a635a30, then gas_results.csv), and dropping the token's configured flag cut setMinterOnce from 47,936 to 47,919 and its deployment from 235,445 to 233,933 (`--gas-stats` before and after, see FROM_DEV_2.md). Re-measure the "before" numbers yourself if the report relies on them.
- Contribution: the owners in [TEAM_TASKS.md](TEAM_TASKS.md), plus the 2026-09-28 takeover, in which Magdy completed the remaining parts with AI assistance. The AI lines in the code file headers, the last line of the rewritten docs and FROM_DEV_2.md say which files.

Do not copy placeholder tests or blank CSVs into the report as successful outcomes. Rerun the result commands after the final commit, so the evidence names a clean commit. Optional frontend/public testnet work comes only after mandatory features pass.
