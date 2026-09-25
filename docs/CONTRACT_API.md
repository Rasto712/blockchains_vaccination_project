# Contract agreement

All constructor/function bodies currently revert NotImplemented. None are deployed or functional. NatSpec comments in each .sol file explain ownership, parameters, intended return values and validation rules. Keep named return types when implementing; do not copy the previous Java void-getter approach.

| Contract | Owner | Public methods |
| --- | --- | --- |
| IdentityRegistry | Developer 1 | registerUser, registerVaccination, getUserInfo, trustedClinic |
| ConsentManager | Developer 1 | grantConsent, revokeConsent, checkAccess, requestAccess, getConsent, hasReceivedReward |
| ConsentRewardToken | Developer 2 | setMinterOnce, mintReward, balanceOf, totalSupply, minter |

Scope 1 = MEASLES_STATUS; scope 2 = VACCINATION_SCHEDULE. Grant durations are whole days 1-365 inclusive. At block.timestamp == expiresAt the grant is invalid. Owner is msg.sender for grants/revocations; requester is msg.sender for requestAccess.

Reason codes must match app/models.py: Allowed=0, NotRegistered=1, UnsupportedScope=2, MissingEvidence=3, NoConsent=4, Expired=5, Revoked=6, HashMismatch=7. Use uint8 scope in public entry points so an unsupported value can be logged as business denial rather than rejected by enum decoding. Zero observedHash signals unavailable/invalid local data; deny it.

One lifetime reward for (owner, requester, scope), regardless of revoke/expire/regrant cycles. Reject active duplicate grants. A revoked/expired tuple may be granted again but receives no second reward. Only the manager mints, and no tokens move during access. Balance never grants consent.

Deploy registry with clinic address, deploy token, deploy manager with registry/token addresses, then set token minter once from deployer. Constructors deliberately revert until implemented. The provided deployment script is also a placeholder.

Review the lab token standard and the manual's ambiguous contract-owner phrasing on Day 1. This design interprets the guardian as consent owner and token units as rewards; deployer has setup authority only. The supplied manual explicitly says consent agreements control data access.
