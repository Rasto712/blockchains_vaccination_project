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

Do not copy placeholder tests or blank CSVs into the report as successful outcomes. Optional frontend/public testnet work comes only after mandatory features pass.
