# Contract API reference

The three contracts in contracts/ (Solidity 0.8.28). NatSpec comments in each .sol file explain parameters, return values and validation rules. The 38 Solidity tests in test/ pin the behaviour below: SOL-IR-01..09 (IdentityRegistry), SOL-CM-01..22 (ConsentManager) and SOL-RT-01..07 (ConsentRewardToken). [TESTING.md](TESTING.md) says how to run them.

## Functions

```solidity
// IdentityRegistry
constructor(address trustedClinic_)
function registerUser(bytes32 identityHash) external
function registerVaccination(address guardian, bytes32 recordHash) external
function getUserInfo(address account) external view returns (bool registered, bytes32 identityHash, bytes32 vaccinationHash)
function trustedClinic() external view returns (address clinic)
// ConsentManager
constructor(address registry_, address rewardToken_)
function grantConsent(address requester, uint8 scope, uint16 durationDays) external
function revokeConsent(address requester, uint8 scope) external
function checkAccess(address owner, address requester, uint8 scope) external view returns (bool allowed, Reason reason)
function requestAccess(address owner, uint8 scope, bytes32 observedHash) external returns (bool allowed, Reason reason)
function getConsent(address owner, address requester, uint8 scope) external view returns (uint256 expiresAt, bool revoked)
function hasReceivedReward(address owner, address requester, uint8 scope) external view returns (bool rewarded)
// ConsentRewardToken
constructor()
function setMinterOnce(address minter_) external
function mintReward(address recipient) external
function balanceOf(address account) external view returns (uint256 balance)
function totalSupply() external view returns (uint256 supply)
function minter() external view returns (address authorizedMinter)
```

Owner is msg.sender for grantConsent and revokeConsent; requester is msg.sender for requestAccess. No caller can grant or revoke another wallet's consent (SOL-CM-04). The token is not an ERC-20: it has no transfer, transferFrom, approve or burn (SOL-RT-06).

## Scopes and reason codes

Scope 1 = MEASLES_STATUS; scope 2 = VACCINATION_SCHEDULE. Reason codes are the same in app/models.py (SOL-CM-17): Allowed=0, NotRegistered=1, UnsupportedScope=2, MissingEvidence=3, NoConsent=4, Expired=5, Revoked=6, HashMismatch=7.

Public entry points take scope as uint8, so requestAccess logs an unsupported value as a denial (UnsupportedScope) instead of failing ABI enum decoding; grantConsent and revokeConsent revert UnsupportedScope. AccessAttempt.scope can therefore hold any uint8. Python keeps it as a raw int, and the audit view prints an unsupported code as "scope 3 (unsupported)".

## Access decision

checkAccess and requestAccess share one rule. Denial precedence (SOL-CM-16): UnsupportedScope; NotRegistered (owner or requester); MissingEvidence (owner's vaccinationHash == 0); NoConsent (expiresAt == 0); Revoked; Expired (block.timestamp >= expiresAt); Allowed. Only if that gives Allowed does requestAccess compare observedHash with the owner's registered vaccinationHash; any difference, including a zero observedHash, is HashMismatch (7). Earlier denials keep their own reason. checkAccess has no hash step, so it never returns HashMismatch.

requestAccess emits exactly one AccessAttempt per call, after the hash step, for allowed and denied outcomes alike, and returns normally on every business denial (SOL-CM-06, SOL-CM-07, SOL-CM-10, SOL-CM-11). Python sends a zero observedHash when it has no valid local record for the owner, so that attempt is still logged and denied. No tokens move during access, and a balance never grants access (SOL-RT-05).

## Grants, expiry and revocation

grantConsent requires only a supported scope, a duration of 1-365 whole days and two registered parties (SOL-CM-02, SOL-CM-05). There is no evidence check and no owner != requester check, so a guardian can grant consent to its own wallet. An active grant of the same tuple is rejected with ConsentStillActive (SOL-CM-03); a revoked or expired tuple can be granted again (SOL-CM-12, SOL-CM-13). A grant writes the whole consent: a new expiresAt and revoked = false.

expiresAt = block.timestamp + uint256(durationDays) * 1 days. The cast comes before the multiplication, because durationDays * 1 days is evaluated in uint24 and would revert from 195 days (SOL-CM-02). Expiry is exclusive: a grant is valid while block.timestamp < expiresAt and invalid at exactly expiresAt (SOL-CM-08). getConsent returns expiresAt 0 for a tuple that was never granted.

revokeConsent (SOL-CM-09):

- active grant: set to revoked, emits ConsentRevoked;
- expired but not revoked: set to revoked, emits ConsentRevoked;
- already revoked: no-op, no event;
- never granted (expiresAt == 0): reverts NoConsentToRevoke;
- unsupported scope: reverts UnsupportedScope.

Revoking keeps expiresAt. A tuple's expiresAt (uint64 in storage), revoked and rewarded fields share one storage slot, keyed by keccak256(abi.encode(owner, requester, scope)) (SOL-CM-21).

## Rewards

One lifetime reward per (owner, requester, scope) tuple, whatever the revoke, expiry and regrant cycles. Every grant emits ConsentGranted. The first grant of a tuple also mints 1 unit to the granting guardian (msg.sender, never the requester) in the same transaction, so its receipt holds one RewardMinted from the token as well (SOL-CM-01, SOL-CM-14). A regrant never clears the rewarded flag and mints nothing. If the mint fails, the whole grant reverts with the token's own error unchanged, so an unconfigured token surfaces NotMinter (SOL-CM-15). ConsentGranted is emitted before the mintReward call, but no test pins that order; app/chain.py and evaluation/ select each event by emitter address and event name, not by its position in the receipt.

Only the configured ConsentManager can mint, one unit per call (SOL-RT-02, SOL-RT-03). The tuple is the only reward limit: an owner needs no attested record, and every wallet it names (itself included) is a new tuple, so k registered wallets can mint 2 * k * k units by granting each other, and themselves, both scopes (SOL-CM-22). This is a stated limitation, like Sybil requesters. Units never authorize access.

## Registration

IdentityRegistry stores no separate registered flag. registerUser rejects a zero identityHash and nothing clears one, so `registered` is derived as identityHash != 0. getUserInfo returns (bool registered, bytes32 identityHash, bytes32 vaccinationHash) and never reverts; an unregistered account gets (false, 0, 0) (SOL-IR-06). MissingEvidence means vaccinationHash == 0. The trusted clinic is immutable, is judged by msg.sender and does not need to register (SOL-IR-03). An attested vaccination commitment is frozen (SOL-IR-04). Identity commitments are not unique across wallets: registration binds a commitment to a wallet and proves no identity (SOL-IR-09).

## Events

As declared in the contracts:

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

Every check reverts with a custom error, which the tests match with vm.expectRevert. app/chain.py matches the 4-byte selector against the three compiled ABIs, so the console prints the bare name ("rejected: NotTrustedClinic"). A name declared in more than one contract (ZeroAddress in all three, NotRegistered, NotAContract) has the same selector in each. Business denials in requestAccess never revert. When several conditions fail at once, the first one in each list wins (SOL-IR-08, SOL-CM-18, SOL-CM-20, SOL-RT-01, SOL-RT-04):

- IdentityRegistry: ZeroAddress, ZeroHash, AlreadyRegistered, NotRegistered, NotTrustedClinic, EvidenceAlreadyRegistered.
  - constructor: ZeroAddress (zero clinic).
  - registerUser: ZeroHash, then AlreadyRegistered.
  - registerVaccination: NotTrustedClinic, NotRegistered (guardian), ZeroHash, EvidenceAlreadyRegistered. A wrong caller always sees NotTrustedClinic.
  - getUserInfo and trustedClinic never revert.
- ConsentManager: ZeroAddress, NotAContract, UnsupportedScope, InvalidDuration, NotRegistered, ConsentStillActive, NoConsentToRevoke.
  - constructor: ZeroAddress (either address), then NotAContract (either address has no code).
  - grantConsent: UnsupportedScope, InvalidDuration, NotRegistered (owner or requester), ConsentStillActive. A failed mint then reverts the whole grant with the token's error ("rejected: NotMinter" for an unconfigured token).
  - revokeConsent: UnsupportedScope, then NoConsentToRevoke.
  - checkAccess, requestAccess, getConsent and hasReceivedReward never revert on a business denial.
- ConsentRewardToken: NotDeployer, MinterAlreadySet, ZeroAddress, NotAContract, NotMinter.
  - constructor: no checks; it stores msg.sender as the immutable deployer.
  - setMinterOnce: NotDeployer, MinterAlreadySet, ZeroAddress, NotAContract.
  - mintReward: NotMinter, then ZeroAddress.
  - balanceOf, totalSupply and minter never revert.

## Deployment

1. IdentityRegistry, with the clinic address as trustedClinic.
2. ConsentRewardToken.
3. ConsentManager, with the registry and token addresses.
4. setMinterOnce(manager) on the token, from the deployer.

scripts/deploy_local.py does exactly this and checks trustedClinic(), each runtime code and minter(). It then writes deployment.json with the chain ID, the addresses, the runtime-code hashes and the number and hash of the block holding the IdentityRegistry deploy. Before using a contract, app/chain.py checks the chain ID, that block hash and the code hash, and that the code is still the compiled artifact. A file from before a node restart is therefore refused even when identical contracts were deployed at the same addresses again.

The guardian is the consent owner, token units are rewards, and the deployer has setup authority only: one setMinterOnce call. It can never mint, and cannot change the minter afterwards (SOL-RT-02, SOL-RT-07). Consent, not a token balance, controls data access.
