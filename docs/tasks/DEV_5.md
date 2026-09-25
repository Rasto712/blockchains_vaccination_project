# Developer 5: Research, documentation and submission

Planned effort: 10-14 hours (8-12 core + 2 hours contingency).

## Files

docs/report; docs/architecture/UML; README; docs/test-results; evaluation tables; presentation and demo checklist. Choose final file formats to match the coursebook.

## Implementation steps

1. Read the coursebook report limits and Week 6 presentation format on Day 1. They were not included in the pasted manual. Collect two or three relevant, cited examples of current healthcare portals/data-sharing approaches and explain what they do well and where user control is limited.
2. Document this exact scope: one guardian-held child record, fixed clinic, scoped school/doctor access, reward and audit. Include actors, functional requirements and the on-chain/off-chain/hash mapping table.
3. Maintain the component and access-sequence UML from this plan, updating names only when interfaces actually change. Explain why the local file stays off-chain and why token balances do not grant permission.
4. Collect results from Developers 2 and 4. Include the reason each test matters, pass/fail evidence, deployment/per-function gas and local timing/scaling tables. Mark unavailable measurements honestly; never invent results.
5. Independently follow the README on the local lab environment. Capture concise evidence of denied access, guardian grant/reward, scoped outputs, revocation, expiry and tampering. Report any blocker to its code owner.
6. Prepare a short presentation following the lab format and a two-to-three-minute live demonstration. Every person explains their own module. Freeze the report and demo after the final tests pass.

## Handoff

Start research/report structure immediately; do not wait for finished code. Receive initial diagrams on Day 2, observed test results on Day 4 and final measurements by Day 6.

## Acceptance criteria

- The report contains every manual deliverable and conforms to coursebook length/structure.
- All research claims have sources, and all test/gas claims have measured evidence.
- A teammate can reproduce the deployment and demo using the instructions alone.
- The presentation explains limitations and clearly distinguishes the prototype from production healthcare software.

Implementation details: [contract API](../CONTRACT_API.md), [architecture](../ARCHITECTURE.md), [validation](../TESTING.md).
