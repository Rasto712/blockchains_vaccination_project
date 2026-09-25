# Contract agreement

Where developer_plan.pdf differs from the Markdown docs or code docstrings, the Markdown docs and docstrings win.

All constructor/function bodies currently revert NotImplemented. None are deployed or functional. NatSpec comments in each .sol file explain ownership, parameters, intended return values and validation rules. Keep named return types when implementing.

| Contract | Owner | Public methods |
| --- | --- | --- |
| IdentityRegistry | Developer 1 | registerUser, registerVaccination, getUserInfo, trustedClinic |
| ConsentManager | Developer 1 | grantConsent, revokeConsent, checkAccess, requestAccess, getConsent, hasReceivedReward |
| ConsentRewardToken | Developer 2 | setMinterOnce, mintReward, balanceOf, totalSupply, minter |

Scope 1 = MEASLES_STATUS; scope 2 = VACCINATION_SCHEDULE. Grant durations are whole days 1-365 inclusive; expiresAt = block.timestamp + uint256(durationDays) * 1 days (cast before multiplying, because durationDays * 1 days is evaluated in uint24 and reverts from 195 days). At block.timestamp == expiresAt the grant is invalid. Owner is msg.sender for grants/revocations; requester is msg.sender for requestAccess.

Reason codes must match app/models.py: Allowed=0, NotRegistered=1, UnsupportedScope=2, MissingEvidence=3, NoConsent=4, Expired=5, Revoked=6, HashMismatch=7. Use uint8 scope in public entry points so an unsupported value can be logged as business denial rather than rejected by enum decoding. AccessAttempt.scope can hold any uint8; Python must not assume a valid Scope. Zero observedHash signals unavailable/invalid local data; deny it.

Denial precedence (required, tests pin it): UnsupportedScope; NotRegistered (owner or requester); MissingEvidence; NoConsent (expiresAt == 0); Revoked; Expired (block.timestamp >= expiresAt); Allowed. Only if that gives Allowed does requestAccess compare observedHash (zero included) with the registered vaccinationHash; any difference, including zero, is HashMismatch (7). Earlier denials keep their own reason. checkAccess applies the same rules without the hash step, so it never returns HashMismatch.

One lifetime reward for (owner, requester, scope), regardless of revoke/expire/regrant cycles. Reject active duplicate grants. A revoked/expired tuple may be granted again but receives no second reward. A grant writes the whole Consent: new expiresAt and revoked = false. It never clears the rewarded flag; only the first grant of a tuple sets it. Only the manager mints, and no tokens move during access. Balance never grants consent.

Revoke: an active grant is set to revoked and emits ConsentRevoked; unsupported scope reverts UnsupportedScope; never granted (expiresAt == 0) reverts NoConsentToRevoke; already revoked is a no-op with no event; expired but not revoked sets revoked and emits ConsentRevoked.

Deploy registry with clinic address, deploy token, deploy manager with registry/token addresses, then set token minter once from deployer. Constructors deliberately revert until implemented. The provided deployment script is also a placeholder.

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

## Errors (proposed, Developer 1 confirms)

These are not declared in the .sol files yet. Add the `error` lines once Developer 1 agrees, so tests can use them with vm.expectRevert and Python can show the name. Business denials in requestAccess never revert.

- IdentityRegistry: ZeroAddress, ZeroHash, AlreadyRegistered, NotRegistered, NotTrustedClinic, EvidenceAlreadyRegistered.
- ConsentManager: ZeroAddress, NotAContract, UnsupportedScope, InvalidDuration, NotRegistered, ConsentStillActive, NoConsentToRevoke.
- ConsentRewardToken: NotDeployer, MinterAlreadySet, ZeroAddress, NotAContract, NotMinter.
