// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

import {IdentityRegistry} from "./IdentityRegistry.sol";
import {ConsentRewardToken} from "./ConsentRewardToken.sol";

/// Handles consent grants, revocations, access checks and the audit log.
/// A denied access emits an event and returns false instead of reverting, because a revert would erase the event.
contract ConsentManager {
    error ZeroAddress();
    error NotAContract();
    error UnsupportedScope();
    error InvalidDuration();
    error NotRegistered();
    error ConsentStillActive();
    error NoConsentToRevoke();

    // Scope codes are shared with the Python code (app/models.py).
    uint8 private constant MEASLES_STATUS = 1;
    uint8 private constant VACCINATION_SCHEDULE = 2;
    uint16 private constant MAX_DURATION_DAYS = 365;
    enum Reason {
        Allowed, NotRegistered, UnsupportedScope, MissingEvidence,
        NoConsent, Expired, Revoked, HashMismatch
    }
    // Packed into one storage slot to save gas.
    struct Consent {
        uint64 expiresAt;
        bool revoked;
        // Set on the first grant and never cleared.
        bool rewarded;
    }
    IdentityRegistry private immutable _registry;
    ConsentRewardToken private immutable _rewardToken;
    mapping(bytes32 => Consent) private _consents;

    event ConsentGranted(address indexed owner, address indexed requester, uint8 indexed scope, uint256 expiresAt);
    event ConsentRevoked(address indexed owner, address indexed requester, uint8 indexed scope);
    event AccessAttempt(address indexed owner, address indexed requester, uint8 indexed scope, uint256 timestamp, bool allowed, Reason reason);

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
    // The caller is the owner. Only the first grant of each (owner, requester, scope) mints a reward.
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
        // Never granted means expiresAt is 0, so it counts as not active.
        if (!consent.revoked && block.timestamp < consent.expiresAt) {
            revert ConsentStillActive();
        }
        // Cast to uint256 first, otherwise durationDays * 1 days overflows uint24.
        uint256 expiresAt = block.timestamp + uint256(durationDays) * 1 days;
        bool firstGrant = !consent.rewarded;
        // Safe cast, the timestamp is far below 2**64.
        consent.expiresAt = uint64(expiresAt);
        consent.revoked = false;
        consent.rewarded = true;
        emit ConsentGranted(msg.sender, requester, scope, expiresAt);

        if (firstGrant) {
            _rewardToken.mintReward(msg.sender);
        }
    }
    // Revoking an already revoked consent does nothing. The reward flag is kept.
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
    // Same rules as requestAccess but with no hash check and no log.
    function checkAccess(address owner, address requester, uint8 scope) external view returns (bool allowed, Reason reason) {
        (allowed, reason,) = _evaluateAccess(owner, requester, scope);
    }
    // The requester is msg.sender. Logs one AccessAttempt, then compares the hash if access is otherwise allowed.
    function requestAccess(address owner, uint8 scope, bytes32 observedHash) external returns (bool allowed, Reason reason) {
        bytes32 vaccinationHash;
        (allowed, reason, vaccinationHash) = _evaluateAccess(owner, msg.sender, scope);
        if (allowed && observedHash != vaccinationHash) {
            allowed = false;
            reason = Reason.HashMismatch;
        }
        emit AccessAttempt(owner, msg.sender, scope, block.timestamp, allowed, reason);
    }
    function getConsent(address owner, address requester, uint8 scope) external view returns (uint256 expiresAt, bool revoked) {
        Consent storage consent = _consents[_consentKey(owner, requester, scope)];
        return (consent.expiresAt, consent.revoked);
    }
    function hasReceivedReward(address owner, address requester, uint8 scope) external view returns (bool rewarded) {
        return _consents[_consentKey(owner, requester, scope)].rewarded;
    }
    function _consentKey(address owner, address requester, uint8 scope) internal pure returns (bytes32 key) {
        return keccak256(abi.encode(owner, requester, scope));
    }
    // Shared by checkAccess and requestAccess. Reasons are checked in this fixed order (tests rely on it).
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
    function _isSupportedScope(uint8 scope) internal pure returns (bool supported) {
        return scope == MEASLES_STATUS || scope == VACCINATION_SCHEDULE;
    }
    function _isRegistered(address account) internal view returns (bool registered) {
        (registered,,) = _registry.getUserInfo(account);
    }
}
