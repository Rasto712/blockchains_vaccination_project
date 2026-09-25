# Validation and evidence

Three Solidity suites exist under test/, named after their contracts. Every test method currently reverts TestNotImplemented. This is intentional: an empty passing test would misrepresent coverage. Developer 2 must implement meaningful Lab 3 setup and assertions. Constructors also remain unimplemented, so deployment workflows must not be reported as working.

Compile commands require the Hardhat dependency and compiler download. Syntax verification of this delivery is separate from functional test success. Python compile/import/startup checks do not prove consent enforcement, storage or blockchain connectivity.

Required cases: registration and duplicates; clinic-only attestation; grant duration 0/1/365/366 days; wrong owner/requester/scope; revoked/exact-expiry denial; unavailable/mismatched record; granted and persistent denied events; first reward and repeat-grant prevention; unauthorized minter; no rewards during access.

Integration must use real local transactions for register -> attest -> denied access -> grant/reward -> permitted school/doctor output -> revoke/expire -> denied access -> tamper denial. No real personal data is permitted.

Measure each contract's deployment gas and averages for comparable function calls. Separate first rewarded grants, unrewarded regrants, granted and denied access. Run small 1/5/10-requester scenarios; record sample counts, gasUsed, send-to-receipt seconds and send-to-decoded-event seconds with a monotonic clock. Never invent gas results or describe local automining as public-network performance.

CSV files in evaluation/templates contain headers only. Their missing result rows mean NOT MEASURED, not zero cost or successful tests.
