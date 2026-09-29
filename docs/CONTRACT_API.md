# Contract agreement

Where developer_plan.pdf differs from the Markdown docs or code docstrings, the Markdown docs and docstrings win.

Status (2026-09-28): all three contracts are implemented as described here. `python -m scripts.deploy_local` deploys them on the local node, and the 38 Solidity tests pin the behaviour below (catalogue in [FROM_DEV_2.md](FROM_DEV_2.md)). NatSpec comments in each .sol file explain parameters, return values and validation rules. Keep named return types and this file in sync with any change.

| Contract | Owner | Public methods |
| --- | --- | --- |
| IdentityRegistry | Developer 1 | registerUser, registerVaccination, getUserInfo, trustedClinic |
| ConsentManager | Developer 1 | grantConsent, revokeConsent, checkAccess, requestAccess, getConsent, hasReceivedReward |
| ConsentRewardToken | Developer 2 | setMinterOnce, mintReward, balanceOf, totalSupply, minter |

Owners are unchanged. On 2026-09-28 Magdy completed ConsentManager and registerVaccination, and made the review fixes to the registry (no separate registered flag, immutable clinic) and the token, with AI assistance (see [TEAM_TASKS.md](TEAM_TASKS.md)).

Scope 1 = MEASLES_STATUS; scope 2 = VACCINATION_SCHEDULE. Grant durations are whole days 1-365 inclusive; expiresAt = block.timestamp + uint256(durationDays) * 1 days (cast before multiplying, because durationDays * 1 days is evaluated in uint24 and reverts from 195 days). At block.timestamp == expiresAt the grant is invalid. Owner is msg.sender for grants/revocations; requester is msg.sender for requestAccess.

Reason codes must match app/models.py: Allowed=0, NotRegistered=1, UnsupportedScope=2, MissingEvidence=3, NoConsent=4, Expired=5, Revoked=6, HashMismatch=7. Use uint8 scope in public entry points so an unsupported value can be logged as business denial rather than rejected by enum decoding. AccessAttempt.scope can hold any uint8; Python must not assume a valid Scope. Zero observedHash signals unavailable/invalid local data; deny it.

Denial precedence (required, tests pin it): UnsupportedScope; NotRegistered (owner or requester); MissingEvidence; NoConsent (expiresAt == 0); Revoked; Expired (block.timestamp >= expiresAt); Allowed. Only if that gives Allowed does requestAccess compare observedHash (zero included) with the registered vaccinationHash; any difference, including zero, is HashMismatch (7). Earlier denials keep their own reason. checkAccess applies the same rules without the hash step, so it never returns HashMismatch. requestAccess emits exactly one AccessAttempt per call, after the hash step, and returns normally on every business denial.

One lifetime reward for (owner, requester, scope), regardless of revoke/expire/regrant cycles. Reject active duplicate grants. A revoked/expired tuple may be granted again but receives no second reward. A grant writes the whole Consent: new expiresAt and revoked = false. It never clears the rewarded flag; only the first grant of a tuple sets it. Every grant emits ConsentGranted; a first grant also mints 1 unit to the granting guardian (msg.sender) in the same transaction, so its receipt holds one RewardMinted from the token as well. The contract happens to emit ConsentGranted before it calls mintReward, but no test pins that order and nothing may rely on it: find each event by its emitter and name, as app/ and evaluation/ do. Only the manager mints, and no tokens move during access. Balance never grants consent.

grantConsent requires only a supported scope, a valid duration and two registered parties. There is no evidence check and no owner != requester check: a guardian can grant consent to its own wallet and earn at most one reward per scope that way (a stated limitation, like Sybil requesters). The tuple is the only reward limit, so k registered wallets can mint 2 * k * k units by granting each other, and themselves, both scopes (SOL-CM-22); units never authorize access.

Revoke: an active grant is set to revoked and emits ConsentRevoked; unsupported scope reverts UnsupportedScope; never granted (expiresAt == 0) reverts NoConsentToRevoke; already revoked is a no-op with no event; expired but not revoked sets revoked and emits ConsentRevoked.

Registration: IdentityRegistry stores no separate registered flag. registerUser rejects a zero identityHash and nothing clears one, so `registered` is derived as identityHash != 0. The ABI is unchanged: getUserInfo still returns (bool registered, bytes32 identityHash, bytes32 vaccinationHash) and never reverts; an unregistered account gets (false, 0, 0). MissingEvidence means vaccinationHash == 0. The trusted clinic is immutable and does not need to register.

Deploy registry with clinic address, deploy token, deploy manager with registry/token addresses, then set token minter once from deployer. scripts/deploy_local.py does exactly this, checks trustedClinic(), each runtime code and minter(), and records the addresses, the runtime-code hashes and the number and hash of the block holding the IdentityRegistry deploy in deployment.json; app/chain.py compares all three, so a file from before a node restart is refused even when identical contracts were deployed at the same addresses again.

Review the lab token standard and the manual's ambiguous contract-owner phrasing on Day 1. This design interprets the guardian as consent owner and token units as rewards; deployer has setup authority only. The project brief (Step 2B) says consent agreements control data access.

## Events

Copied from the contracts; keep this list in sync with them.

```solidity
// IdentityRegistry
event UserRegistered(address indexed account, bytes32 identityHash);
event VaccinationRegistered(address indexed guardian, address indexed clinic, bytes32 recordHash);
// ConsentManager
event ConsentGranted(address indexed owner, address indexed requester, uint8 indexed scope, uint256 expiresAt);
event ConsentRevoked(address indexed owner, address indexed requester, uint8 indexed scope);
event AccessAttempt(address indexed owner, address indexed requester, uint8 indexed scope, uint256 timestamp, bool allowed, Reason reason);
// ConsentRewardToken
event MinterConfigured(address indexed minter);
event RewardMinted(address indexed recipient, uint256 amount);
```

## Errors and revert order

Declared as custom errors in the .sol files, exactly as first proposed here; `NotImplemented` is gone. Tests use them with vm.expectRevert, and app/chain.py matches the 4-byte selector against the three ABIs, so the menu prints the bare name ("rejected: NotTrustedClinic"). A name declared in more than one contract (ZeroAddress in all three, NotRegistered, NotAContract) has the same selector in each. Business denials in requestAccess never revert. When several conditions fail at once, the first one in each list wins:

- IdentityRegistry: ZeroAddress, ZeroHash, AlreadyRegistered, NotRegistered, NotTrustedClinic, EvidenceAlreadyRegistered.
  - constructor: ZeroAddress (zero clinic).
  - registerUser: ZeroHash, then AlreadyRegistered.
  - registerVaccination: NotTrustedClinic, NotRegistered (guardian), ZeroHash, EvidenceAlreadyRegistered. A wrong caller always sees NotTrustedClinic.
  - getUserInfo and trustedClinic never revert.
- ConsentManager: ZeroAddress, NotAContract, UnsupportedScope, InvalidDuration, NotRegistered, ConsentStillActive, NoConsentToRevoke.
  - constructor: ZeroAddress (either address), then NotAContract (either address has no code).
  - grantConsent: UnsupportedScope, InvalidDuration, NotRegistered (owner or requester), ConsentStillActive. A failed mint then reverts the whole grant with the token's own error unchanged, so an unconfigured token surfaces NotMinter ("rejected: NotMinter"; SOL-CM-15).
  - revokeConsent: UnsupportedScope, then NoConsentToRevoke.
  - checkAccess, requestAccess, getConsent and hasReceivedReward never revert on a business denial.
- ConsentRewardToken: NotDeployer, MinterAlreadySet, ZeroAddress, NotAContract, NotMinter.
  - constructor: no checks; it stores msg.sender as the immutable deployer.
  - setMinterOnce: NotDeployer, MinterAlreadySet, ZeroAddress, NotAContract.
  - mintReward: NotMinter, then ZeroAddress.
  - balanceOf, totalSupply and minter never revert.

AI assistance: the status, grant-event, deployment, registration and error sections were updated with Claude (Anthropic); review before submission.
