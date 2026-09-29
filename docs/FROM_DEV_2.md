# From Dev 2 (Robin): reward token and Solidity tests

Status (2026-09-28): Robin's delivery is commit a635a30. On 2026-09-28 Magdy took over the remaining work and made the later changes described here with AI assistance (the token's dropped flag, the extra test cases and SOL-CM-20, the exporter's checks and this update), as the AI disclosure at the end says. The other two contracts are now implemented too, so all tests pass; SOL-IR-09, SOL-CM-21 and SOL-CM-22 were added on 2026-09-29, with AI assistance too, which makes 38.

What I delivered, what it proves, what the tests pin on the other contracts, and the design reasoning behind it. Files: `contracts/ConsentRewardToken.sol`, `test/IdentityRegistry.t.sol`, `test/ConsentManager.t.sol`, `test/ConsentRewardToken.t.sol`, `evaluation/export_solidity_results.py`.

## Status

- `ConsentRewardToken.sol` is implemented.
- There are 38 Solidity tests, all with stable IDs: SOL-IR-01..09, SOL-CM-01..22 and SOL-RT-01..07.
- Against the implemented contracts in the working tree (a635a30 plus Developer 1's `IdentityRegistry.sol` and `ConsentManager.sol` and the review fixes, none committed yet), **all 38 pass**. `npx hardhat test solidity` prints `38 passing` with no failures. `python -m evaluation.export_solidity_results` writes one row per test to `evaluation/results/solidity_test_results.csv` and the raw log next to it; the file there now predates the three new tests, so rerun it.
- Once Developer 1's contracts are committed, re-run the exporter, so that the evidence column names a clean commit instead of "a635a30 + uncommitted contracts/tests".
- The tests are checked against the real contracts too: 37 bugs were planted in them, one at a time, and every one made at least one test fail (see "Do the tests catch bugs?" below).

## How to run

```bash
npm ci
npm test                                   # = npx hardhat test solidity
python -m evaluation.export_solidity_results
```

The exporter writes `evaluation/results/solidity_test_results.csv` using the shared header (`test_id, requirement, why_critical, expected, actual, status, evidence`). It also saves the raw console log next to it (`solidity_test_run.log`).

- `expected`, `requirement` and `why_critical` come from each test's NatSpec.
- `actual` and `status` come only from the real run, so nothing is prefilled. `evidence` names the test, the exact command, the commit and the time.
- A test Hardhat lists as skipped, or one that does not appear at all, gets no row, so a missing row means "not run".
- If a suite's `setUp()` fails, none of its tests runs. Each of them still gets a `fail` row, with actual `not run: setUp() failed: ...`, so a broken fixture never looks like a pass or a missing row.
- The exporter refuses to write a CSV that could disagree with Hardhat:
  - Every `test*` function must carry exactly one `@notice [SOL-XX-nn]` tag, and no id may repeat. Otherwise it stops before running anything and lists the offending functions.
  - Its pass, fail and skip counts must match Hardhat's own `N passing / M failing / K skipped` summary.
  - If a run yields no usable results (for example a compile error), it deletes the old CSV, so the old file can't be mistaken for this run's evidence.
- It exits 0 only when the CSV was written and every test passed. Without Node.js or `npm ci`, it says so instead of printing a traceback.
- Extra global Hardhat options pass through unchanged and in order, for example `--config x.ts`.
- This file holds the SOL rows only. Dev 3's PY rows share the same header but live in their own file, `evaluation/results/test_results.csv`. The exporter never writes that file, so it can't overwrite them. The report reads both files.
- Re-run the exporter after any contract change and before the report freezes. That run is the evidence Ahmed cites.

`npx hardhat test solidity --gas-stats` prints per-function gas from the unit tests. Those numbers are a useful cross-check for Rasto, but the report's gas table should come from his measured receipts (`evaluation/results/gas_results.csv`, written by `python -m evaluation.measure`). On Hardhat 3.18 the `--gas-stats` values equal receipt gasUsed, for example registerUser 45,430 and setMinterOnce 47,919.

## ConsentRewardToken design choices (what to say if asked)

| Choice | Why |
| --- | --- |
| Not an ERC-20 (no `transfer`, `approve`, `allowance`, `decimals`) | The brief asks for **non-transferable** reward units. ERC-20's core purpose is transfer, so implementing it and then blocking transfers would be misleading. Rewards record a contribution; they are not money and not an access right. `balanceOf` and `totalSupply` use the ERC-20 names so any tool can still read them. |
| Deployer stored as `immutable` | The setup authority is fixed into the bytecode at deployment and can never be reassigned. `immutable` is also cheaper to read than a storage slot. |
| `setMinterOnce` is a one-shot | The deployer's only power is to point the token at ConsentManager once. After that, nobody (the deployer included) can change who mints. |
| Minter must have code (`minter_.code.length > 0`) | This rejects a plain wallet (EOA) passed by mistake, for example a guardian's. It does not prove the contract **is** ConsentManager: a deployer who meant to could configure a forwarding contract of its own and mint through it. The deployer is trusted at setup (the usual deployer model), and anyone can check that `minter()` equals the ConsentManager address. `deploy_local.py` does that check right after setup. |
| No separate "configured" flag | `setMinterOnce` rejects `address(0)`, so the minter is set exactly when `minter()` is nonzero. `IdentityRegistry` uses the same rule for a nonzero identity hash, and it leaves one source of truth. It saves a little gas: `setMinterOnce` 47,936 → 47,919 and deployment 235,445 → 233,933 (unit-test `--gas-stats`); the ABI is unchanged. |
| `mintReward(recipient)` has **no amount** | Each call mints exactly 1 (`REWARD_UNIT`). Even a buggy minter cannot mint 1,000 in one call, and the menu prints `balanceOf` as a plain integer. |
| Deduplication lives in ConsentManager, not the token | The token doesn't know about (owner, requester, scope) tuples. ConsentManager's permanent `_rewarded` flag decides whether to mint. The token test SOL-RT-03 shows the token itself would mint twice; SOL-CM-12/13/14 show the manager never asks it to. |
| Custom errors, checked in a fixed order | The order is `NotDeployer`, `MinterAlreadySet`, `ZeroAddress`, `NotAContract` for setup (SOL-RT-01), then `NotMinter`, `ZeroAddress` for minting (SOL-RT-04). Custom errors are cheaper than `require` strings, and the menu prints the bare name. |
| Before setup, nobody can mint | `_minter` is `address(0)`, which can never be `msg.sender`. |

## What the tests pin on Dev 1's contracts

All of these hold for the implemented contracts today. Any change to one of them needs the same change in `tests/fake_chain.py`, `CONTRACT_API.md` and the named test.

1. **Error names.** The tests use `ConsentManager.<Name>.selector` and `IdentityRegistry.<Name>.selector`, so renaming or removing an error breaks compilation instead of passing silently.
2. **Reward recipient.** The reward goes to `msg.sender` of `grantConsent` (the guardian), never to the requester (SOL-CM-01, SOL-CM-14).
3. **What `grantConsent` requires.** Only a supported scope, a valid duration and two registered parties. There is no evidence check (SOL-RT-05: a doctor grants on its own empty record) and no owner != requester check (SOL-CM-05: a self-grant is accepted), as FOR_DEV_1.md and Dev 3's fake say.
4. **One event per access.** Every `requestAccess` emits exactly one `AccessAttempt`, after the hash step, with the raw scope. It never reverts on a business denial. The `_request` helper enforces this on every access call.
5. **Revert orders**, the same as Dev 3's fake:
   - registry (SOL-IR-08): `NotTrustedClinic`, `NotRegistered`, `ZeroHash`, `EvidenceAlreadyRegistered`; `registerUser` checks `ZeroHash` before `AlreadyRegistered`
   - `grantConsent` (SOL-CM-20): `UnsupportedScope`, `InvalidDuration`, `NotRegistered`, `ConsentStillActive`
   - `revokeConsent`: `UnsupportedScope` before `NoConsentToRevoke`
   - constructor (SOL-CM-18): `ZeroAddress` before `NotAContract`
6. **Denial precedence** (SOL-CM-16): UnsupportedScope, NotRegistered, MissingEvidence, NoConsent, Revoked, Expired, then the hash. A wrong or zero `observedHash` never replaces an earlier denial, including Revoked and Expired.
7. **Grant events.** Every grant, the first and any regrant, emits `ConsentGranted` with the new `expiresAt` (SOL-CM-01, 12, 13). The tests don't care whether `ConsentGranted` comes before or after the mint, and CONTRACT_API.md says the same: nothing may rely on the order.
8. **Failed mint.** A failed mint rolls the grant back, and the token's own `NotMinter` error comes out unchanged (SOL-CM-15), so the menu would print "rejected: NotMinter". CONTRACT_API.md now documents this. If the team prefers another error, change SOL-CM-15 and CONTRACT_API.md together.

## Answers to Dev 3's requests (FOR_DEV_2.md)

| Request | Where it is covered |
| --- | --- |
| Reward goes to the guardian, not the requester | SOL-CM-01 (+1 guardian, 0 school), SOL-CM-14 (requesters stay 0) |
| Demo balances 0→1, 1→2, 2→2 | SOL-CM-14 and SOL-CM-19 (demo storyline) |
| Denial-order test with all six cases | SOL-CM-16 `testDenialPrecedence`: your six cases (a)–(f), plus (g) revoked and (h) expired with a wrong or zero hash |
| Check the event, not just the return value | Every access call goes through `_request`, which requires exactly one `AccessAttempt`, checks owner, requester, raw scope and timestamp, and checks that the return value matches the event. SOL-CM-10 also uses `vm.expectEmit` for the tampered and zero hash. |
| Keep `revoked == false` after a regrant | SOL-CM-12 |
| `getUserInfo` is `(false, 0, 0)` for a fresh address; school and unattested guardian have hash 0 | SOL-IR-06 |
| Reason codes are the literal numbers 0–7 | SOL-CM-17, and all denial asserts compare literal numbers |
| Raw scope 3 appears in the event | SOL-CM-06 (`vm.expectEmit` with scope 3; scope 255 is also checked) |
| Shared results table and stable IDs | `evaluation/export_solidity_results.py` writes the SOL rows to `evaluation/results/solidity_test_results.csv`; IDs are listed below |

## Test catalogue

IdentityRegistry (SOL-IR):

| ID | Function | Checks |
| --- | --- | --- |
| 01 | testRegisterUserStoresHash | Registration stores the flag and hash, emits `UserRegistered` |
| 02 | testRejectDuplicateOrZeroRegistration | Zero hash rejected; a second registration is rejected and state is unchanged |
| 03 | testOnlyTrustedClinicCanAttest | A stranger, attacker, the guardian and the deployer are rejected; the clinic succeeds without being registered |
| 04 | testRejectMissingGuardianOrReplacementHash | Unregistered guardian, zero hash, and replacing (or re-sending) evidence are all rejected |
| 05 | testRetrieveReferencesContainsNoPlaintext | Registration and attestation log one event each, with one `bytes32` commitment as data and only addresses as topics; `getUserInfo` returns three fixed-size words |
| 06 | testGetUserInfoForUnregisteredAndUnattestedAccounts | Never reverts; returns zeros where there is no data |
| 07 | testConstructorRejectsZeroClinicAndStoresClinic | Zero clinic reverts; the clinic is stored |
| 08 | testAttestationAndRegistrationRevertOrder | Full attestation order (a wrong caller always sees `NotTrustedClinic`, a zero hash beats `EvidenceAlreadyRegistered`) and registration order |
| 09 | testIdentityCommitmentsAreNotUnique | Stated limitation: a second wallet may register a copy of another wallet's commitment; both stay registered and the copy gains no evidence |

ConsentManager (SOL-CM):

| ID | Function | Checks |
| --- | --- | --- |
| 01 | testRegisteredGuardianCanGrant | Exact expiry, `ConsentGranted`, one `RewardMinted`, reward goes to the guardian |
| 02 | testDurationBounds | 0, 366 and 65535 rejected; 1, 194, 195 and 365 accepted with exact expiry (catches the uint24 overflow) |
| 03 | testRejectActiveDuplicateGrant | Duplicate rejected, even one second before expiry; no second reward |
| 04 | testCallerCannotChangeAnotherOwnersConsent | Another guardian, the requester or a stranger can't revoke; guardian2's actions don't touch guardian's grant |
| 05 | testGrantRequiresRegisteredPartiesAndSupportedScope | Unregistered requester or owner, scope 0 or 3 → revert, no reward; a self-grant is accepted |
| 06 | testWrongRequesterAndUnsupportedScopeAreLoggedDenied | NoConsent for the wrong requester or scope; UnsupportedScope logged with raw scope 3 or 255 |
| 07 | testUnregisteredOrMissingEvidenceIsLoggedDenied | NotRegistered (either party, in requestAccess and checkAccess), MissingEvidence, no mint |
| 08 | testExactExpiryBoundaryIsDenied | Allowed at expiresAt − 1, Expired at expiresAt (checkAccess and requestAccess) |
| 09 | testRevocationIsIdempotentAndBlocksAccess | `ConsentRevoked`; second revoke has no event; revoking a never-granted tuple reverts; an expired grant can still be revoked |
| 10 | testHashMismatchAndZeroObservedHashAreDenied | Tampered, zero and another child's hash all give HashMismatch (7); the right hash is allowed |
| 11 | testValidAccessEmitsAuditWithoutReward | Allowed event; repeats are logged; balances and supply unchanged |
| 12 | testRegrantDoesNotRepeatReward | Revoke then regrant: `ConsentGranted` with the new expiry, revoked cleared, no second reward |
| 13 | testExpiryRegrantDoesNotRepeatReward | Expire then regrant: `ConsentGranted` with the new expiry, allowed, no second reward |
| 14 | testDifferentRequesterOrScopeEarnsOwnReward | The reward key is the full (owner, requester, scope) tuple |
| 15 | testRewardFailureRollsBackGrant | With an unconfigured token the grant reverts with `NotMinter`; no consent and no flag remain |
| 16 | testDenialPrecedence | Dev 3's six precedence cases, plus revoked or expired with a wrong or zero hash |
| 17 | testReasonCodesAreFrozen | Enum values 0–7 |
| 18 | testConstructorRejectsInvalidDependencies | Zero or wallet addresses are rejected; zero is reported first |
| 19 | testDemoStorylineEndToEnd | The whole demo on-chain: revoke, then tamper, then regrant and expiry (DEMO.md and the scripted demo run tamper before revoke; the outcomes are the same, because tamper only uses the doctor grant) |
| 20 | testGrantAndRevokeRevertOrder | Grant and revoke revert order when several conditions fail at once |
| 21 | testConsentStateUsesOneStorageSlot | expiresAt, revoked and rewarded share one storage slot: a first grant, a revoke and a regrant each write only that slot, and the views still read every field |
| 22 | testRewardLimitIsPerTupleOnly | Stated limitation: the tuple is the only reward limit; three registered wallets with no record mint 2 * 3 * 3 = 18 units, regrants add none, and the units open no record |

ConsentRewardToken (SOL-RT):

| ID | Function | Checks |
| --- | --- | --- |
| 01 | testOnlyDeployerCanConfigureMinterOnce | Wrong caller, zero address and wallet rejected, in the documented order; `MinterConfigured`; second call rejected |
| 02 | testOnlyConfiguredManagerCanMint | Nobody mints before setup; guardian, school, attacker and deployer are rejected afterwards |
| 03 | testMintAddsOneUnitAndEmitsEvent | +1 balance and supply, `RewardMinted(recipient, 1)` |
| 04 | testRejectZeroRecipient | No mint to `address(0)`; a non-minter sees `NotMinter` first |
| 05 | testBalanceDoesNotAuthorizeAccess | Uses the real manager: a rewarded account with no consent is denied, and access ends at revoke while the balance stays |
| 06 | testTokenIsNonTransferable | `transfer`, `transferFrom`, `approve` and `burn` calls all fail |
| 07 | testDeployerHasNoOngoingPower | After setup the deployer can't mint or reconfigure |

## Do the tests catch bugs? (mutation check)

A test that passes against both correct and broken code proves nothing. I first did this check against a throwaway contract written from the spec. It is now repeated against **the real contracts**: one bug is planted at a time in a scratch copy, and the suite is re-run. The count is the number of tests that failed.

| Injected bug | Failing tests |
| --- | --- |
| Reward minted to the requester | 8 |
| Expired checked before Revoked | 1 |
| Expiry inclusive (`>` instead of `>=`) | 4 |
| Regrant keeps `revoked = true` | 2 |
| Denied access reverts (erasing the event) | 8 |
| Missing `uint256` cast in the expiry calculation | 1 |
| A reward on every grant | 3 |
| Zero observed hash accepted | 1 |
| Duration bound wrong (1–366) | 2 |
| A second revoke emits an event | 1 |
| No evidence check | 2 |
| Reason enum reordered | 8 |
| Scope altered in the event | 2 |
| Deployer can mint | 2 |
| Wallet accepted as minter | 1 |
| Minter can be reset | 2 |
| Total supply not tracked | 7 |
| Anyone can attest | 2 |
| Evidence can be replaced | 1 |
| Re-registration allowed | 1 |
| Hash compared when there is no grant | 1 |
| Hash compared on a revoked grant\* | 1 |
| Hash compared on an expired grant\* | 1 |
| Hash compared on revoked and expired grants\* | 1 |
| `ConsentGranted` emitted only on the first grant\* | 2 |
| Regrant emits the old `expiresAt`\* | 2 |
| Grant checks registration before scope and duration\* | 1 |
| Grant checks "still active" before duration\* | 1 |
| Grant rejects a self-grant\* | 1 |
| Registry checks existing evidence before a zero hash\* | 1 |
| `checkAccess` disagrees with `requestAccess` for an unregistered owner\* | 1 |
| Token checks "already set" before the caller\* | 1 |
| Token checks the zero address before the caller\* | 1 |
| Token checks the address before "already set"\* | 1 |
| Token checks the recipient before the minter\* | 1 |
| Manager constructor reports a wallet before a zero address\* | 1 |
| Registry logs an extra event\* | 1 |

All 37 were caught. The 16 marked \* passed the original 34-test suite unnoticed, which is why SOL-CM-20 and the extra cases were added. This table suits the report's Experimental Results section, as long as it's described as a check on the tests.

## Presentation Q&A prep

- **Why test in Solidity and not in Python?** The manual requires Solidity unit tests (Lab 3). They run directly on Hardhat's EVM with cheatcodes. `vm.prank` sets `msg.sender`, `vm.warp` moves time to the exact expiry second, and `vm.expectEmit` or `vm.recordLogs` check events. Python can't warp time within a test that cheaply.
- **How do you test expiry without waiting a day?** `vm.warp(expiresAt - 1)` gives allowed, and `vm.warp(expiresAt)` gives Expired. The contract keeps its real 1-day minimum; only the test clock moves.
- **Why check events and not just return values?** web3 does not give Python the return value of a transaction, only the logs. The event is what the app and the audit trail actually use. Asserting "it reverted" would be wrong: a revert erases the event, which is exactly the bug the tests guard against.
- **Why is the reward not a real ERC-20?** Non-transferable by design, as explained in the design choices table above.
- **Can a guardian farm rewards?** Not with one tuple. The permanent flag allows one reward per (owner, requester, scope) for life (SOL-CM-12/13). The design is not Sybil-resistant: fake requester identities could each earn a reward. A guardian may also grant consent to its own wallet (no owner != requester check, as specified), which earns at most one reward per scope. That's stated as a limitation in the plan.
- **Who can mint?** Only the configured minter, which the deployer sets once and can never change (SOL-RT-01/02/07). The token cannot tell whether that minter is the real ConsentManager. The deployer is trusted at setup, and anyone can compare `minter()` with the ConsentManager address, which `deploy_local.py` does.
- **How do you know the tests are meaningful?** They fail on each of the 37 planted bugs in the real contracts (mutation check above).

## AI disclosure

Per the instructor's email (Misha, 28 Sept), AI-generated code is allowed if reviewed, understood and disclosed. The token contract, the three test suites, the exporter and this note were drafted with Claude (Anthropic) and reviewed by Robin. Later fixes after the code review were also made with Claude: the redundant flag, the extra test cases and SOL-CM-20, the exporter's checks, and this note's update. They still need a human review before submission. The report's contribution section should say so.
