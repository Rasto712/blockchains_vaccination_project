# Validation and evidence

Three Solidity suites exist under test/, named after their contracts. Every test method currently reverts TestNotImplemented. This is intentional: an empty passing test would misrepresent coverage. Developer 2 must implement meaningful Lab 3 setup and assertions. Constructors also remain unimplemented, so deployment workflows must not be reported as working.

Compile commands require the Hardhat dependency and compiler download. Compiling the scaffold is separate from functional test success. Python compile/import/startup checks do not prove consent enforcement, storage or blockchain connectivity.

Python unit tests go in tests/ and run with `python -m unittest discover -s tests` (standard library only).

Required cases: registration and duplicates; clinic-only attestation; grant duration 0/1/194/195/365/366 days; wrong owner/requester/scope; revoked/exact-expiry denial; unavailable/mismatched record; granted and persistent denied events; first reward and repeat-grant prevention; unauthorized minter; no rewards during access.

Integration must use real local transactions for register -> attest -> denied access -> grant/reward -> permitted school/doctor output -> revoke school, denied (REVOKED) -> tamper a copy while the doctor grant is active, denied (HASH_MISMATCH) -> regrant school for 1 day with no second reward, set the next block timestamp to expiresAt, denied (EXPIRED). The expiry step runs last because node time cannot go back. No real personal data is permitted.

Measure each contract's deployment gas and averages for comparable function calls. Separate first rewarded grants, unrewarded regrants, granted and denied access. Run scenarios with 1, 5 and 10 requester accounts (scenario_requester_account_indices in settings) in which every role acts: deployer (deploy, set minter), clinic (attest), guardian (register, grant, revoke), requesters (register, request); redeploy fresh contracts before each N and re-register and re-attest; report mean gas and time per operation and role. Record sample counts, gasUsed, send-to-receipt seconds and send-to-decoded-event seconds with a monotonic clock. Never invent gas results or describe local automining as public-network performance. All gas numbers use solc 0.8.28 with the optimiser on, 200 runs (hardhat.config.ts); state this under the gas table.

CSV files in evaluation/templates contain headers only. Their missing result rows mean NOT MEASURED, not zero cost or successful tests.
