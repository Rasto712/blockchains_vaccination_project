# For Dev 2

What the Python side needs from `contracts/ConsentRewardToken.sol` and the Solidity tests, and what I need from you. Read this next to [tasks/DEV_2.md](tasks/DEV_2.md) and [CONTRACT_API.md](CONTRACT_API.md). Miki's note is [FOR_DEV_1.md](FOR_DEV_1.md), Rasto's is [FOR_DEV_4.md](FOR_DEV_4.md) and Ahmed's is [FOR_DEV_5.md](FOR_DEV_5.md).

## Where things stand

The Python side is done: the local record, salts and commitments, the release rule, the school and doctor views, the menu and the tamper demo. It has 106 unit tests and none of them needs a node: the ones that touch the chain use a fake in `tests/fake_chain.py`, because there is no working contract yet. The fake has no token, so nothing on my side checks rewards until the first real run. Your suite is the only place the real contracts get checked before then.

## Rewards

- The reward goes to the granting guardian, but no test checks who receives it. The menu reads the granting account's balance before and after a grant and prints "reward balance of guardian: 0 -> 1", and the rewards view prints every actor's balance. That only works if ConsentManager mints to `msg.sender` of `grantConsent`, never to the requester. The token NatSpec says guardian (`mintReward`), but none of the reward test outlines names the recipient.
- Please assert it in a ConsentManager test, for example `testRegisteredGuardianCanGrant` or `testDifferentRequesterOrScopeEarnsOwnReward`: after a first grant, `balanceOf(guardian)` goes up by exactly 1 and `balanceOf(requester)` stays 0. The token-only `testMintAddsOneUnitAndEmitsEvent` cannot check this, because there the test itself is the minter.
- Expected guardian balances through the demo: school grant 0 -> 1, doctor grant 1 -> 2, school regrant 2 -> 2. Everyone else stays at 0.
- One reward is the plain integer 1. The menu shows `balanceOf` exactly as it comes back. If the Lab 3 token standard review turns it into an ERC-20 style token with decimals (minting `10**decimals` per reward), tell me and Rasto first, so the display can divide. Keep `balanceOf(address)` returning uint256, because that is the one token call my code reaches.

## Tests that pin what Python relies on

- Denial order: CONTRACT_API and ConsentManager.sol say the order is pinned by tests, but every ConsentManager outline sets up only one failing condition, so a contract that checks in another order would pass them all. My fake uses the frozen order and my tests expect it, so your suite is the only check that the real contract matches. Please add one precedence test covering:
  - revoked and past `expiresAt` gives `Revoked`
  - a zero `observedHash` with no grant gives `NoConsent`, not `HashMismatch`
  - an unsupported scope from an unregistered requester gives `UnsupportedScope`
  - an unregistered requester against an owner with no evidence gives `NotRegistered`
  - an owner with no evidence and no grant gives `MissingEvidence`, not `NoConsent`
  - with a wrong `observedHash`, `requestAccess` gives `HashMismatch` while `checkAccess` gives `Allowed`
- The event, not just the return value: Python never sees what `requestAccess` returns, only the one `AccessAttempt` log in the receipt. So in the precedence test and in `testHashMismatchAndZeroObservedHashAreDenied`, please check the emitted event (`vm.expectEmit` or `vm.recordLogs`): exactly one `AccessAttempt` per call, with the final `allowed` and `reason` (for example `HashMismatch` for a wrong or zero `observedHash`).
- Regrant: Keep the assertion in `testRegrantDoesNotRepeatReward` that `revoked == false` after a regrant. Without it, the demo's last step (revoke, regrant for 1 day, then EXPIRED) would show REVOKED.
- getUserInfo: Add a check that `getUserInfo` on a fresh address returns `(false, 0, 0)` without reverting, and that a registered school or doctor, or a guardian before attestation, gets `vaccinationHash` 0. The menu's "show my registration" relies on it. `testRetrieveReferencesContainsNoPlaintext` only checks the shape.
- Reason codes: Python names reasons from plain ints using its own enum (ALLOWED=0 up to HASH_MISMATCH=7). An assertion like `reason == ConsentManager.Reason.Revoked` still passes after a reorder, but Python would then print the wrong name. One assertion that `uint8(ConsentManager.Reason.X)` is 0 to 7 for all eight, or literal numbers in the denial tests, fixes it.
- Raw scope: In `testWrongRequesterAndUnsupportedScopeAreLoggedDenied`, check with `vm.expectEmit` that the emitted scope is the raw value sent (for example 3), because the audit view prints it as "scope 3 (unsupported)".

## Shared test results table

Your Solidity rows and my Python rows (about eight) go under one header: `test_id, requirement, why_critical, expected, actual, status, evidence`. My proposal, which Ahmed confirms because the report cites these ids:

- one row per test function
- `test_id` is SOL-IR-nn, SOL-CM-nn and SOL-RT-nn for yours, PY-nn for mine
- `requirement` uses the numbered requirement ids Ahmed adds in the Architecture section, the same ids on both sides
- `why_critical` is the one sentence DEV_2.md already asks for
- `status` is pass or fail only, and `evidence` names the suite and function plus the command and commit of the run
- rows are only added after a real run, so a missing row means not run
- on-chain rules (denial order, hash comparison, rewards, minter) are SOL rows; off-chain checks (commitment and byte flips, no-leak, zero hash shown as unavailable) are PY rows
- the filled file probably lives at `evaluation/results/test_results.csv`, since the templates keep headers only (Rasto owns `evaluation/`, and Ahmed reads it)

Please share the final list of test functions and ids once it stops changing, so the ids stay stable when the report cites them.
