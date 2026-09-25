# Developer 1: Identity and consent contracts

## Files

contracts/IdentityRegistry.sol; contracts/ConsentManager.sol. Pair with Developer 2 on their matching Solidity tests.

## Implementation steps

1. Implement wallet registration using msg.sender and a nonzero identity commitment. Prevent duplicate registration; never accept a requester address as proof of caller identity.
2. Fix one trusted clinic in the registry constructor. Permit that clinic to register one frozen vaccination commitment for a registered guardian. Reject arbitrary callers, missing owners and replacement hashes.
3. Implement grants keyed by owner/requester/scope with duration 1-365 days; expiry = block.timestamp + uint256(durationDays) * 1 days (without the cast it overflows from 195 days). Check exact requester and scope; deny at expiry. Own-grant revocation must not affect another guardian.
4. Implement requestAccess as a state-changing audit operation. Every well-formed denied request emits its denied reason and returns normally. Check the supplied observed commitment against registry evidence.
5. Implement a permanent rewarded-tuple flag and call the restricted reward minter on the first eligible grant only. A failed mint must not leave an apparently successful grant; keep the transaction atomic.
6. Agree event layouts and reason codes with Developer 4 before coding the Python event reader. Review Solidity tests with Developer 2 and fix the logic they expose.

## Handoff

Day 1: signatures/state/events to Developers 2 and 4. Day 3: compilable contracts and positive/negative calls. Developer 2 owns the test files but you remain responsible for fixing these contracts.

## Acceptance criteria

- Another account cannot attest a vaccination or alter someone else's grant.
- Wrong requester/scope, unregistered users, expired/revoked consent and hash mismatch are denied and logged.
- Repeated active grant is rejected; regrant after revocation works without duplicate reward.
- Read methods expose only registered references; every mutation has an appropriate event.

Implementation details: [contract API](../CONTRACT_API.md), [architecture](../ARCHITECTURE.md), [validation](../TESTING.md).
