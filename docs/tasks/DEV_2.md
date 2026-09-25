# Developer 2: Reward token and Solidity unit tests

## Files

contracts/ConsentRewardToken.sol; test/IdentityRegistry.t.sol; test/ConsentManager.t.sol; test/ConsentRewardToken.t.sol. Use the lab's test-folder convention if different.

## Implementation steps

1. Implement balances, total supply and a RewardMinted event. Allow deployer-controlled one-time minter setup and reject unauthorized minting. Keep reward units non-transferable unless the lab explicitly requires another standard.
2. Configure tests as Solidity .t.sol files run by Hardhat, following Lab 3. Do not replace the required Solidity unit tests with only Python or TypeScript tests. Tests inherit forge-std Test (declared in package.json); remove `pure` from an outline function once its body uses state or cheatcodes.
3. Cover identity registration, duplicate/zero values and clinic-only frozen attestation. Cover grant duration boundaries, ownership, exact scope/requester and exact expiry.
4. Assert the AccessAttempt event is present for denied well-formed calls; do not merely assert that something reverted. Check that no token transfer or mint occurs during access.
5. Test first grant reward, duplicate active grant, revoke-and-regrant, expiry-and-regrant and a different requester/scope. The same tuple receives only one lifetime reward.
6. Give each test a stable ID and one sentence explaining why its functionality matters. Export actual pass/fail evidence for Developer 5; do not prefill success or gas values.

## Handoff

Agree the test harness and contract interfaces on Day 1 with Developers 1 and 4. Contract owners resolve logic bugs; you own reproducible unit-test coverage and results.

## Acceptance criteria

- All three correctly named Solidity suites compile and run in the lab Hardhat environment.
- Tests cover both permitted and denied behavior, not only happy paths.
- Only the configured ConsentManager can mint; repeated reward attempts cannot inflate balances for the same tuple.
- The test report ties every tested behavior to a requirement and observed result.

Implementation details: [contract API](../CONTRACT_API.md), [architecture](../ARCHITECTURE.md), [validation](../TESTING.md).
