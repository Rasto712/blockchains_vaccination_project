# From Dev 2 (Robin): reward token and Solidity tests

What I delivered, what it proves, what the others need to do next, and the design reasoning behind it. Files: `contracts/ConsentRewardToken.sol`, `test/IdentityRegistry.t.sol`, `test/ConsentManager.t.sol`, `test/ConsentRewardToken.t.sol`, `evaluation/export_solidity_results.py`.

## Status

- `ConsentRewardToken.sol` is implemented, and all its own unit tests pass (SOL-RT-01..04, 06, 07).
- There are 34 Solidity tests in total, all with stable IDs. Against the repo's current contracts (commit 646b9c7), **10 pass and 24 fail**. The 24 failures are all `NotImplemented` from Developer 1's unfinished code: `registerVaccination` and the whole of ConsentManager, whose constructor makes `setUp()` fail for every ConsentManager test. They are the honest current state, not test bugs.
- To check that the tests themselves are right, I ran them against a throwaway contract written straight from `CONTRACT_API.md` and the NatSpec. It is not committed and it is not Dev 1's code. All 34 passed.
- I then planted 20 typical bugs in that contract, one at a time, and every one of them made at least one test fail (see "Do the tests catch bugs?" below).

## How to run

```bash
npm ci
npm test                                   # = npx hardhat test solidity
python -m evaluation.export_solidity_results
```

The exporter writes `evaluation/results/solidity_test_results.csv` using the shared header (`test_id, requirement, why_critical, expected, actual, status, evidence`). It also saves the raw console log next to it.

- `expected`, `requirement` and `why_critical` come from each test's NatSpec.
- `actual` and `status` come only from the real run, so nothing is prefilled.
- A test that does not run gets no row.
- Re-run it after Dev 1's contracts land and before the report freezes. That run is the evidence Ahmed cites.

`npx hardhat test solidity --gas-stats` prints per-function gas from the unit tests. Those numbers are a useful cross-check for Rasto, but the report's gas table should come from his measured receipts.

## ConsentRewardToken design choices (what to say if asked)

| Choice | Why |
| --- | --- |
| Not an ERC-20 (no `transfer`, `approve`, `allowance`, `decimals`) | The brief asks for **non-transferable** reward units. ERC-20's core purpose is transfer, so implementing it and then blocking transfers would be misleading. Rewards record a contribution; they are not money and not an access right. `balanceOf` and `totalSupply` use the ERC-20 names so any tool can still read them. |
| Deployer stored as `immutable` | The setup authority is fixed into the bytecode at deployment and can never be reassigned. `immutable` is also cheaper to read than a storage slot. |
| `setMinterOnce` is a one-shot | The deployer's only power is to point the token at ConsentManager once. After that, nobody (the deployer included) can change who mints. |
| Minter must have code (`minter_.code.length > 0`) | This rejects a wallet (EOA), so nobody can make a person the minter by mistake or on purpose. It does not prove the contract **is** ConsentManager; `deploy_local.py` checks that by reading `minter()` back. |
| `mintReward(recipient)` has **no amount** | Each call mints exactly 1 (`REWARD_UNIT`). Even a buggy minter cannot mint 1,000 in one call, and the menu prints `balanceOf` as a plain integer. |
| Deduplication lives in ConsentManager, not the token | The token doesn't know about (owner, requester, scope) tuples. ConsentManager's permanent `_rewarded` flag decides whether to mint. The token test SOL-RT-03 shows the token itself would mint twice; SOL-CM-12/13/14 show the manager never asks it to. |
| Custom errors, checked in a fixed order | The order is `NotDeployer`, `MinterAlreadySet`, `ZeroAddress`, `NotAContract` for setup, then `NotMinter`, `ZeroAddress` for minting. Custom errors are cheaper than `require` strings, and the menu prints the bare name. |
| Before setup, nobody can mint | `_minter` is `address(0)`, which can never be `msg.sender`. |

## What Dev 1 needs for the remaining tests to pass

1. Implement ConsentManager and `registerVaccination` exactly as in `CONTRACT_API.md`.
2. Declare these ConsentManager errors with exactly these names: `ZeroAddress`, `NotAContract`, `UnsupportedScope`, `InvalidDuration`, `NotRegistered`, `ConsentStillActive`, `NoConsentToRevoke`. The tests compute selectors from these names, so the file compiles now and fails loudly if a name changes.
3. Mint the reward to `msg.sender` of `grantConsent` (the guardian), never to the requester. This is asserted in SOL-CM-01 and SOL-CM-14.
4. `grantConsent` must not require the owner to have evidence. SOL-RT-05 has a doctor grant consent on its own empty record in order to get a balance. This matches Dev 3's fake chain. If you want an evidence check in `grantConsent`, tell me and Dev 3, and I'll change RT-05.
5. Emit exactly one `AccessAttempt` per `requestAccess` call, after the hash step, with the raw scope, and never revert on a business denial. The `_request` helper in the ConsentManager tests enforces this on every access call.
6. Registry revert order (SOL-IR-08) is `NotTrustedClinic`, then `NotRegistered`, then `ZeroHash`, then `EvidenceAlreadyRegistered`, the same as Dev 3's fake.
7. The event-order assumption: the tests don't care whether `ConsentGranted` comes before or after the mint.

## Answers to Dev 3's requests (FOR_DEV_2.md)

| Request | Where it is covered |
| --- | --- |
| Reward goes to the guardian, not the requester | SOL-CM-01 (+1 guardian, 0 school), SOL-CM-14 (requesters stay 0) |
| Demo balances 0→1, 1→2, 2→2 | SOL-CM-14 and SOL-CM-19 (demo storyline) |
| Denial-order test with all six cases | SOL-CM-16 `testDenialPrecedence`, cases (a)–(f) |
| Check the event, not just the return value | Every access call goes through `_request`, which requires exactly one `AccessAttempt`, checks owner, requester, raw scope and timestamp, and checks that the return value matches the event. SOL-CM-10 also uses `vm.expectEmit` for the tampered and zero hash. |
| Keep `revoked == false` after a regrant | SOL-CM-12 |
| `getUserInfo` is `(false, 0, 0)` for a fresh address; school and unattested guardian have hash 0 | SOL-IR-06 |
| Reason codes are the literal numbers 0–7 | SOL-CM-17, and all denial asserts compare literal numbers |
| Raw scope 3 appears in the event | SOL-CM-06 (`vm.expectEmit` with scope 3; scope 255 is also checked) |
| Shared results table and stable IDs | `evaluation/export_solidity_results.py`; IDs are listed below |

## Test catalogue

IdentityRegistry (SOL-IR):

| ID | Function | Checks |
| --- | --- | --- |
| 01 | testRegisterUserStoresHash | Registration stores the flag and hash, emits `UserRegistered` |
| 02 | testRejectDuplicateOrZeroRegistration | Zero hash rejected; a second registration is rejected and state is unchanged |
| 03 | testOnlyTrustedClinicCanAttest | A stranger, attacker, the guardian and the deployer are rejected; the clinic succeeds without being registered |
| 04 | testRejectMissingGuardianOrReplacementHash | Unregistered guardian, zero hash, and replacing (or re-sending) evidence are all rejected |
| 05 | testRetrieveReferencesContainsNoPlaintext | Raw return data is exactly 3 × 32 bytes (bool plus two hashes), with no dynamic fields |
| 06 | testGetUserInfoForUnregisteredAndUnattestedAccounts | Never reverts; returns zeros where there is no data |
| 07 | testConstructorRejectsZeroClinicAndStoresClinic | Zero clinic reverts; the clinic is stored |
| 08 | testAttestationAndRegistrationRevertOrder | The error order matches the Python menu |

ConsentManager (SOL-CM):

| ID | Function | Checks |
| --- | --- | --- |
| 01 | testRegisteredGuardianCanGrant | Exact expiry, `ConsentGranted`, one `RewardMinted`, reward goes to the guardian |
| 02 | testDurationBounds | 0, 366 and 65535 rejected; 1, 194, 195 and 365 accepted with exact expiry (catches the uint24 overflow) |
| 03 | testRejectActiveDuplicateGrant | Duplicate rejected, even one second before expiry; no second reward |
| 04 | testCallerCannotChangeAnotherOwnersConsent | Another guardian, the requester or a stranger can't revoke; guardian2's actions don't touch guardian's grant |
| 05 | testGrantRequiresRegisteredPartiesAndSupportedScope | Unregistered requester or owner, scope 0 or 3 → revert, no reward |
| 06 | testWrongRequesterAndUnsupportedScopeAreLoggedDenied | NoConsent for the wrong requester or scope; UnsupportedScope logged with raw scope 3 or 255 |
| 07 | testUnregisteredOrMissingEvidenceIsLoggedDenied | NotRegistered (either party), MissingEvidence, no mint |
| 08 | testExactExpiryBoundaryIsDenied | Allowed at expiresAt − 1, Expired at expiresAt (checkAccess and requestAccess) |
| 09 | testRevocationIsIdempotentAndBlocksAccess | `ConsentRevoked`; second revoke has no event; revoking a never-granted tuple reverts; an expired grant can still be revoked |
| 10 | testHashMismatchAndZeroObservedHashAreDenied | Tampered, zero and another child's hash all give HashMismatch (7); the right hash is allowed |
| 11 | testValidAccessEmitsAuditWithoutReward | Allowed event; repeats are logged; balances and supply unchanged |
| 12 | testRegrantDoesNotRepeatReward | Revoke then regrant: revoked cleared, new expiry, no second reward |
| 13 | testExpiryRegrantDoesNotRepeatReward | Expire then regrant: allowed, no second reward |
| 14 | testDifferentRequesterOrScopeEarnsOwnReward | The reward key is the full (owner, requester, scope) tuple |
| 15 | testRewardFailureRollsBackGrant | With an unconfigured token the grant reverts; no consent and no flag remain |
| 16 | testDenialPrecedence | Dev 3's six precedence cases |
| 17 | testReasonCodesAreFrozen | Enum values 0–7 |
| 18 | testConstructorRejectsInvalidDependencies | Zero or wallet addresses are rejected |
| 19 | testDemoStorylineEndToEnd | The whole demo order on-chain, in one test |

ConsentRewardToken (SOL-RT):

| ID | Function | Checks |
| --- | --- | --- |
| 01 | testOnlyDeployerCanConfigureMinterOnce | Wrong caller, zero address and wallet rejected; `MinterConfigured`; second call rejected |
| 02 | testOnlyConfiguredManagerCanMint | Nobody mints before setup; guardian, school, attacker and deployer are rejected afterwards |
| 03 | testMintAddsOneUnitAndEmitsEvent | +1 balance and supply, `RewardMinted(recipient, 1)` |
| 04 | testRejectZeroRecipient | No mint to `address(0)` |
| 05 | testBalanceDoesNotAuthorizeAccess | Uses the real manager: a rewarded account with no consent is denied, and access ends at revoke while the balance stays |
| 06 | testTokenIsNonTransferable | `transfer`, `transferFrom`, `approve` and `burn` calls all fail |
| 07 | testDeployerHasNoOngoingPower | After setup the deployer can't mint or reconfigure |

## Do the tests catch bugs? (mutation check)

A test that passes against both correct and broken code proves nothing. Using the throwaway spec-following contracts, I introduced one bug at a time and re-ran the suite. The count is the number of tests that failed.

| Injected bug | Failing tests |
| --- | --- |
| Reward minted to the requester | 8 |
| Expired checked before Revoked | 1 |
| Expiry inclusive (`>` instead of `>=`) | 3 |
| Regrant keeps `revoked = true` | 2 |
| Denied access reverts (erasing the event) | 8 |
| Missing `uint256` cast in the expiry calculation | 1 |
| A reward on every grant | 3 |
| Zero observed hash accepted | 1 |
| Duration bound wrong (0–366) | 1 |
| A second revoke emits an event | 1 |
| No evidence check | 2 |
| Reason enum reordered | 4 |
| Scope altered in the event | 1 |
| Deployer can mint | 2 |
| Wallet accepted as minter | 1 |
| Minter can be reset | 2 |
| Total supply not tracked | 6 |
| Anyone can attest | 2 |
| Evidence can be replaced | 1 |
| Re-registration allowed | 1 |

All 20 were caught. This table suits the report's Experimental Results section, as long as it's described as a check on the tests rather than on Dev 1's final contracts.

## Presentation Q&A prep

- **Why test in Solidity and not in Python?** The manual requires Solidity unit tests (Lab 3). They run directly on Hardhat's EVM with cheatcodes. `vm.prank` sets `msg.sender`, `vm.warp` moves time to the exact expiry second, and `vm.expectEmit` or `vm.recordLogs` check events. Python can't warp time within a test that cheaply.
- **How do you test expiry without waiting a day?** `vm.warp(expiresAt - 1)` gives allowed, and `vm.warp(expiresAt)` gives Expired. The contract keeps its real 1-day minimum; only the test clock moves.
- **Why check events and not just return values?** web3 does not give Python the return value of a transaction, only the logs. The event is what the app and the audit trail actually use. Asserting "it reverted" would be wrong: a revert erases the event, which is exactly the bug the tests guard against.
- **Why is the reward not a real ERC-20?** Non-transferable by design, as explained in the design choices table above.
- **Can a guardian farm rewards?** Not with one tuple. The permanent flag allows one reward per (owner, requester, scope) for life (SOL-CM-12/13). The design is not Sybil-resistant: fake requester identities could each earn a reward. That's stated as a limitation in the plan.
- **Who can mint?** Only the ConsentManager contract, set once by the deployer (SOL-RT-01/02/07).
- **How do you know the tests are meaningful?** They fail on each of the 20 planted bugs (mutation check above).

## AI disclosure

Per the instructor's email (Misha, 28 Sept), AI-generated code is allowed if reviewed, understood and disclosed. The token contract, the three test suites, the exporter and this note were drafted with Claude (Anthropic) and reviewed by Robin. The report's contribution section should say so.
