# Contract API

Each function: who may call it, what it does and how it fails. NatSpec comments in contracts/*.sol hold the detail; the 38 tests in test/ check every rule. Overview: [ARCHITECTURE.md](ARCHITECTURE.md).

A **tuple** is one (owner, requester, scope), where the owner is the guardian; consent and rewards are per tuple.

## IdentityRegistry

| Function | Caller | What it does | Reverts with (in order) |
|---|---|---|---|
| `registerUser(identityHash)` | any wallet, once | Stores the caller's identity commitment; emits UserRegistered | ZeroHash, AlreadyRegistered |
| `registerVaccination(guardian, recordHash)` | trusted clinic | Stores a guardian's record commitment, once (frozen); emits VaccinationRegistered | NotTrustedClinic, NotRegistered, ZeroHash, EvidenceAlreadyRegistered |
| `getUserInfo(account)`, `trustedClinic()` | anyone (view) | (registered, identityHash, vaccinationHash); the clinic address | - |

## ConsentManager

| Function | Caller | What it does | Reverts with (in order) |
|---|---|---|---|
| `grantConsent(requester, scope, durationDays)` | guardian | Grants one scope for 1-365 days (self-grants allowed); emits ConsentGranted; may mint a reward | UnsupportedScope, InvalidDuration, NotRegistered, ConsentStillActive |
| `revokeConsent(requester, scope)` | guardian | Revokes that grant; emits ConsentRevoked | UnsupportedScope, NoConsentToRevoke |
| `requestAccess(owner, scope, observedHash)` | requester | Decides access, emits one AccessAttempt, returns (allowed, reason) | - |
| `checkAccess(owner, requester, scope)` | anyone (view) | Same decision, without the hash step or event | - |
| `getConsent`, `hasReceivedReward` | anyone (view) | (expiresAt, revoked); whether the tuple was rewarded | - |

## ConsentRewardToken

Not an ERC-20: no transfer, approve or burn, so units cannot move (SOL-RT-06).

| Function | Caller | What it does | Reverts with (in order) |
|---|---|---|---|
| `setMinterOnce(minter_)` | deployer, once | Makes ConsentManager the only minter; emits MinterConfigured | NotDeployer, MinterAlreadySet, ZeroAddress, NotAContract |
| `mintReward(recipient)` | ConsentManager only | Adds 1 unit; emits RewardMinted | NotMinter, ZeroAddress |
| `balanceOf`, `totalSupply`, `minter` | anyone (view) | Balance, units minted, the minter | - |

## Scopes and reason codes

Scopes: `1` = MEASLES_STATUS (school), `2` = VACCINATION_SCHEDULE (doctor).

Codes and names match app/models.py (SOL-CM-17); the console prints them in upper case, e.g. NO_CONSENT.

| Code | Reason | Denied when |
|---|---|---|
| 2 | UnsupportedScope | scope is not 1 or 2 |
| 1 | NotRegistered | owner or requester unregistered |
| 3 | MissingEvidence | owner has no attested record |
| 4 | NoConsent | tuple never granted |
| 6 | Revoked | grant revoked |
| 5 | Expired | block time has reached expiresAt |
| 7 | HashMismatch | observedHash is not the attested commitment |
| 0 | Allowed | every check passed |

## Key rules

- **No acting for others.** Owner and requester are always the transaction sender (SOL-CM-04).
- **Denials never revert.** requestAccess emits exactly one AccessAttempt and returns (false, reason) on a denial (SOL-CM-06, SOL-CM-07, SOL-CM-11).
- **Denial precedence.** Checks run in the reason table's order; the first failing one is the reason (SOL-CM-16).
- **Hash step.** Only when every other check passes does requestAccess compare observedHash (the hash of the local card) with the owner's commitment. Any difference, zero included, is HashMismatch (SOL-CM-10). checkAccess skips this step.
- **Expiry.** expiresAt = grant time + durationDays days (SOL-CM-02). Access is denied from exactly expiresAt (SOL-CM-08). An active grant cannot be regranted until it expires or is revoked (SOL-CM-03, SOL-CM-12, SOL-CM-13).
- **Revoke.** Works on an active or expired grant. A second revoke does nothing; a never-granted tuple reverts (SOL-CM-09).
- **Reward rule.** A tuple's first grant mints 1 unit to the guardian; regrants mint nothing (SOL-CM-12 to 14). A failed mint reverts the grant (SOL-CM-15). A balance never grants access (SOL-RT-05).
- **Events.** Named in each table row; AccessAttempt is the audit log.
- **Errors.** The console prints the error name, e.g. `rejected: NotTrustedClinic`. Revert orders, constructors included, are checked in tests (SOL-IR-07, SOL-IR-08, SOL-CM-18, SOL-CM-20, SOL-RT-01, SOL-RT-04).

## Deployment order

scripts/deploy_local.py verifies each step before the next:

1. Deploy IdentityRegistry with the clinic address.
2. Deploy ConsentRewardToken.
3. Deploy ConsentManager with the registry and token addresses.
4. From the deployer, call `setMinterOnce(manager)` on the token.

It then saves the addresses to deployment.json. After step 4 the deployer has no power (SOL-RT-07).
