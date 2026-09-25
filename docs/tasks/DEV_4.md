# Developer 4: Hardhat setup, integration and measurements

Planned effort: 12-16 hours (10-14 core + 2 hours contingency).

## Files

Lab-derived Hardhat config/deployment scripts; app/chain.py; integration/demo_workflow.py; evaluation/measure.py; generated deployment addresses/ABIs and result tables.

## Implementation steps

1. Reuse the lab Hardhat version and Solidity-test setup. Before other work depends on it, compile a tiny lab contract, deploy locally and call it from Python. Pin the working dependencies and record commands.
2. Deploy registry and reward token, deploy ConsentManager with their addresses, then set the token's minter once. Verify addresses and trusted clinic. Export ABI/address configuration; never hardcode real private keys.
3. Implement Python RPC operations with the selected local account, successful receipts and decoding of the exact AccessAttempt event. A missing/reverted receipt or missing event is unavailable, never permission.
4. Create a repeatable integration workflow covering registration, frozen clinic attestation, first grant reward, permitted read, revocation/expiry denial and hash mismatch. Coordinate the final permission recheck with Developer 3.
5. Measure actual deployment gas for each contract and gasUsed for core functions. Separate first rewarded grants from later unrewarded grants and granted from denied access. Report sample counts and averages.
6. Run small local scenarios with 1, 5 and 10 requesters. Time send-to-receipt and send-to-event-decoding using a monotonic clock. Record local automining and machine settings; these are demo measurements, not public-chain throughput claims.

## Handoff

The Day 1 Python-to-Hardhat call is the main schedule gate. Give Developer 3 a stable helper interface and Developer 5 exact commands, deployment receipts and measured CSV tables.

## Acceptance criteria

- A clean checkout follows documented commands to compile, deploy, run Solidity tests and start the Python demo.
- All three contracts are connected to the same local deployment; unauthorized minter setup fails.
- The integration workflow uses real transactions, not a simulated permission dictionary.
- Evaluation tables are calculated from receipts and timings, with units and scenario labels.

Implementation details: [contract API](../CONTRACT_API.md), [architecture](../ARCHITECTURE.md), [validation](../TESTING.md).
