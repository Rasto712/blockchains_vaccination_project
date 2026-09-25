# Developer 5: Research, documentation and submission

## Files

Report PDF (outline in docs/REPORT_OUTLINE.md); diagrams in docs/ARCHITECTURE.md; README; test and measurement results in evaluation/results/*.csv; presentation and docs/DEMO.md. Choose final file formats to match the coursebook.

## Implementation steps

1. Report format: A4, 1-inch margins, 10-pt Times New Roman, double spaced, at most 10 pages excluding references and code (see REPORT_OUTLINE.md). Presentation format: see the Week 6 Lab 6 material, and send the slides to the instructors by the slide deadline. Collect two or three relevant, cited examples of current healthcare portals/data-sharing approaches and explain what they do well and where user control is limited.
2. Document this exact scope: one guardian-held child record, fixed clinic, scoped school/doctor access, reward and audit. Include actors, functional requirements and the on-chain/off-chain/hash mapping table.
3. Maintain the component and access-sequence UML from this plan, updating names only when interfaces actually change. Explain why the local file stays off-chain and why token balances do not grant permission.
4. Collect results from Developers 2 and 4. Include the reason each test matters, pass/fail evidence, deployment/per-function gas and local timing/scaling tables. Mark unavailable measurements honestly; never invent results.
5. Independently follow the README on the local lab environment. Capture concise evidence of denied access, guardian grant/reward, scoped outputs, revocation, expiry and tampering. Report any blocker to its code owner.
6. Prepare a short presentation following the lab format and a two-to-three-minute live demonstration. Each member presents the part they delivered. Freeze the report and demo after the final tests pass.

## Handoff

Start research/report structure immediately; do not wait for finished code. Receive initial diagrams first, observed test results once the Solidity suites run, and final measurements once the integration run works.

## Acceptance criteria

- The report contains every manual deliverable and conforms to coursebook length/structure.
- All research claims have sources, and all test/gas claims have measured evidence.
- A teammate can reproduce the deployment and demo using the instructions alone.
- The presentation explains limitations and clearly distinguishes the prototype from production healthcare software.

Implementation details: [contract API](../CONTRACT_API.md), [architecture](../ARCHITECTURE.md), [validation](../TESTING.md).
