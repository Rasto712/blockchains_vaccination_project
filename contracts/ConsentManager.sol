// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

import {IdentityRegistry} from "./IdentityRegistry.sol";
import {ConsentRewardToken} from "./ConsentRewardToken.sol";

/**
 * @title ConsentManager
 * @notice Exact requester/scope consent, expiry, revocation, audit and reward trigger.
 * @dev Owner always comes from msg.sender for grants/revocations. Never let an arbitrary caller
 *      act as another guardian by supplying an owner address. Requester comes from msg.sender
 *      in requestAccess. No token movement happens during access.
 *      A well-formed denied access emits AccessAttempt and returns false normally; it never reverts,
 *      because a revert would erase the event.
 *      Lifetime reward deduplication belongs here, independently of current grant validity.
 */
contract ConsentManager {
    error ZeroAddress();
    error NotAContract();
    error UnsupportedScope();
    error InvalidDuration();
    error NotRegistered();
    error ConsentStillActive();
    error NoConsentToRevoke();

    // These numeric codes are frozen with Python (app/models.py); changing them changes the ABI.
    uint8 private constant MEASLES_STATUS = 1;
    uint8 private constant VACCINATION_SCHEDULE = 2;
    uint16 private constant MAX_DURATION_DAYS = 365;
    enum Reason {
        Allowed, NotRegistered, UnsupportedScope, MissingEvidence,
        NoConsent, Expired, Revoked, HashMismatch
    }
    // All of a tuple's state shares one storage slot, so a revoke or a first grant changes a slot that is
    // already in use instead of writing a new one from zero (about 20,000 gas each; SOL-CM-21).
    // uint64 holds any timestamp for the next 500 billion years; getConsent still returns uint256.
    struct Consent {
        uint64 expiresAt;
        bool revoked;
        // Permanent: set on a tuple's first grant and never cleared by revoke, expiry or regrant.
        bool rewarded;
    }
    IdentityRegistry private immutable _registry;
    ConsentRewardToken private immutable _rewardToken;
    mapping(bytes32 => Consent) private _consents;

    event ConsentGranted(address indexed owner, address indexed requester, uint8 indexed scope, uint256 expiresAt);
    event ConsentRevoked(address indexed owner, address indexed requester, uint8 indexed scope);
    event AccessAttempt(address indexed owner, address indexed requester, uint8 indexed scope, uint256 timestamp, bool allowed, Reason reason);

    /**
     * @notice Wire the two deployed dependencies for this local network.
     * @dev Revert order: ZeroAddress (either address), then NotAContract (either address has no code).
     *      Both references are immutable.
     * @param registry_ Nonzero IdentityRegistry contract address.
     * @param rewardToken_ Nonzero ConsentRewardToken contract address.
     */
    constructor(address registry_, address rewardToken_) {
        if (registry_ == address(0) || rewardToken_ == address(0)) {
            revert ZeroAddress();
        }
        if (registry_.code.length == 0 || rewardToken_.code.length == 0) {
            revert NotAContract();
        }
        _registry = IdentityRegistry(registry_);
        _rewardToken = ConsentRewardToken(rewardToken_);
    }
    /**
     * @notice Grant a time-limited scope owned by the caller.
     * @dev Revert order: UnsupportedScope, InvalidDuration, NotRegistered (owner or requester), ConsentStillActive.
     *      No evidence check and no owner != requester check. Emits ConsentGranted and mints once per lifetime
     *      (owner, requester, scope) tuple to the granting guardian. A failed mint reverts the whole grant.
     *      The tuple is the only reward limit: an owner needs no attested record, and every wallet it names
     *      (itself included) is a new tuple, so k registered wallets can mint 2 * k * k units by granting each
     *      other both scopes (SOL-CM-22). Units never authorize access.
     *      Computes expiresAt = block.timestamp + uint256(durationDays) * 1 days. The cast comes before the
     *      multiplication: durationDays * 1 days is evaluated in uint24 and reverts from 195 days.
     *      A grant writes a new expiresAt and revoked = false and leaves rewarded set. It never clears the
     *      rewarded flag; only the first grant of a tuple mints.
     * @param requester Exact registered requester wallet; not an entire organization.
     * @param scope 1 for measles status or 2 for vaccination schedule; other codes revert.
     * @param durationDays Whole days from 1 through 365 inclusive.
     */
    function grantConsent(address requester, uint8 scope, uint16 durationDays) external {
        if (!_isSupportedScope(scope)) {
            revert UnsupportedScope();
        }
        if (durationDays == 0 || durationDays > MAX_DURATION_DAYS) {
            revert InvalidDuration();
        }
        if (!_isRegistered(msg.sender) || !_isRegistered(requester)) {
            revert NotRegistered();
        }
        Consent storage consent = _consents[_consentKey(msg.sender, requester, scope)];
        // A never-granted tuple has expiresAt == 0, so it is never active.
        if (!consent.revoked && block.timestamp < consent.expiresAt) {
            revert ConsentStillActive();
        }
        uint256 expiresAt = block.timestamp + uint256(durationDays) * 1 days;
        bool firstGrant = !consent.rewarded;
        // The narrowing cast cannot cut off bits: block.timestamp + 365 days is far below 2**64.
        consent.expiresAt = uint64(expiresAt);
        consent.revoked = false;
        consent.rewarded = true;
        emit ConsentGranted(msg.sender, requester, scope, expiresAt);

        if (firstGrant) {
            _rewardToken.mintReward(msg.sender);
        }
    }
    /**
     * @notice Revoke the caller's grant without altering another guardian's state.
     * @dev Revert order: UnsupportedScope, then NoConsentToRevoke when never granted (expiresAt == 0).
     *      An active or expired grant is set to revoked and emits ConsentRevoked. Already revoked is a no-op
     *      with no event. Never clears the permanent rewarded flag.
     * @param requester Requester whose grant is being revoked.
     * @param scope Exact scope being revoked.
     */
    function revokeConsent(address requester, uint8 scope) external {
        if (!_isSupportedScope(scope)) {
            revert UnsupportedScope();
        }
        Consent storage consent = _consents[_consentKey(msg.sender, requester, scope)];
        if (consent.expiresAt == 0) {
            revert NoConsentToRevoke();
        }
        if (consent.revoked) {
            return;
        }
        consent.revoked = true;
        emit ConsentRevoked(msg.sender, requester, scope);
    }
    /**
     * @notice Check current registration, evidence and consent without logging a disclosure.
     * @dev Same rules as requestAccess without the commitment comparison, so it never returns HashMismatch.
     *      This view does not authorize unlogged file release.
     * @param owner Guardian owning the record.
     * @param requester Wallet whose consent is being evaluated.
     * @param scope Exact requested scope.
     * @return allowed True only for currently valid consent and registered evidence.
     * @return reason Compact reason for permission or denial.
     */
    function checkAccess(address owner, address requester, uint8 scope) external view returns (bool allowed, Reason reason) {
        (allowed, reason,) = _evaluateAccess(owner, requester, scope);
    }
    /**
     * @notice Record one authenticated access attempt and return its decision.
     * @dev Uses current consent; only if _evaluateAccess allows does it compare observedHash (zero included) with the
     *      registered vaccinationHash. Any difference, including zero, is HashMismatch (7); earlier denials keep their
     *      own reason. Emits exactly one AccessAttempt after the hash step, for allowed and denied outcomes alike, and
     *      returns normally on a business denial. The event proves an authorization attempt, not physical data delivery.
     * @param owner Guardian owning the record; requester is always msg.sender.
     * @param scope Requested scope; unsupported values produce a denied event with the raw value.
     * @param observedHash Hash computed by Python from its one local byte snapshot; zero signals unavailable/invalid local data.
     * @return allowed Final permission plus commitment-comparison outcome.
     * @return reason Decision reason also emitted in AccessAttempt.
     */
    function requestAccess(address owner, uint8 scope, bytes32 observedHash) external returns (bool allowed, Reason reason) {
        bytes32 vaccinationHash;
        (allowed, reason, vaccinationHash) = _evaluateAccess(owner, msg.sender, scope);
        if (allowed && observedHash != vaccinationHash) {
            allowed = false;
            reason = Reason.HashMismatch;
        }
        emit AccessAttempt(owner, msg.sender, scope, block.timestamp, allowed, reason);
    }
    /**
     * @notice Read minimal grant state for display and final checks.
     * @param owner Guardian wallet.
     * @param requester Exact requester wallet.
     * @param scope Scope code.
     * @return expiresAt Exclusive expiration timestamp; zero means no grant.
     * @return revoked Whether the stored grant was revoked.
     */
    function getConsent(address owner, address requester, uint8 scope) external view returns (uint256 expiresAt, bool revoked) {
        Consent storage consent = _consents[_consentKey(owner, requester, scope)];
        return (consent.expiresAt, consent.revoked);
    }
    /**
     * @notice Inspect lifetime reward eligibility without changing consent.
     * @dev A revoked/expired/regranted tuple stays rewarded. This prevents tuple replay only: it does not stop
     *      Sybil wallets, and it does not require the owner to hold an attested record (SOL-CM-22).
     * @param owner Guardian wallet.
     * @param requester Requester wallet.
     * @param scope Scope code.
     * @return rewarded True when this exact tuple already received its one reward.
     */
    function hasReceivedReward(address owner, address requester, uint8 scope) external view returns (bool rewarded) {
        return _consents[_consentKey(owner, requester, scope)].rewarded;
    }
    /**
     * @notice Create the stable key used by grant state and lifetime reward tracking.
     * @dev One identical encoding for every read/write path. abi.encode pads each field to 32 bytes, so no two
     *      distinct tuples share an encoding.
     * @param owner Guardian wallet.
     * @param requester Requester wallet.
     * @param scope Scope code.
     * @return key Hash of an unambiguous encoding of the three fields.
     */
    function _consentKey(address owner, address requester, uint8 scope) internal pure returns (bytes32 key) {
        return keccak256(abi.encode(owner, requester, scope));
    }
    /**
     * @notice Share the permission rules between read checks and logged access.
     * @dev Precedence (tests pin it): UnsupportedScope; NotRegistered (owner or requester); MissingEvidence;
     *      NoConsent (expiresAt == 0); Revoked; Expired (block.timestamp >= expiresAt); Allowed.
     *      Commitment mismatch is checked separately by requestAccess. Never reverts on a business denial:
     *      the registry's getUserInfo returns (false, 0, 0) for an unregistered account.
     * @param owner Guardian wallet.
     * @param requester Authenticated requester wallet.
     * @param scope Requested scope.
     * @return allowed Current authorization decision.
     * @return reason Deterministic precedence of denial reasons.
     * @return vaccinationHash Owner's registered commitment, so requestAccess needs no second registry call; zero on
     *         the denials that come before the evidence check.
     */
    function _evaluateAccess(address owner, address requester, uint8 scope)
        internal
        view
        returns (bool allowed, Reason reason, bytes32 vaccinationHash)
    {
        if (!_isSupportedScope(scope)) {
            return (false, Reason.UnsupportedScope, bytes32(0));
        }
        bool ownerRegistered;
        (ownerRegistered,, vaccinationHash) = _registry.getUserInfo(owner);
        if (!ownerRegistered || !_isRegistered(requester)) {
            return (false, Reason.NotRegistered, bytes32(0));
        }
        if (vaccinationHash == bytes32(0)) {
            return (false, Reason.MissingEvidence, bytes32(0));
        }
        Consent storage consent = _consents[_consentKey(owner, requester, scope)];
        uint256 expiresAt = consent.expiresAt;
        if (expiresAt == 0) {
            return (false, Reason.NoConsent, vaccinationHash);
        }
        if (consent.revoked) {
            return (false, Reason.Revoked, vaccinationHash);
        }
        if (block.timestamp >= expiresAt) {
            return (false, Reason.Expired, vaccinationHash);
        }
        return (true, Reason.Allowed, vaccinationHash);
    }
    /**
     * @notice Whether a scope code is one of the two supported scopes.
     * @param scope Raw scope code from the caller.
     * @return supported True for MEASLES_STATUS or VACCINATION_SCHEDULE.
     */
    function _isSupportedScope(uint8 scope) internal pure returns (bool supported) {
        return scope == MEASLES_STATUS || scope == VACCINATION_SCHEDULE;
    }
    /**
     * @notice Read an account's registration through the registry's public getter.
     * @dev getUserInfo never reverts, so an unregistered account is a plain false.
     * @param account Wallet to check.
     * @return registered Whether the account completed registration.
     */
    function _isRegistered(address account) internal view returns (bool registered) {
        (registered,,) = _registry.getUserInfo(account);
    }
}
