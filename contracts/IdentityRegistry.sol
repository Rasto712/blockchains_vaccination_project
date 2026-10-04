// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

/// Stores a hashed identity per wallet and one vaccination hash per guardian.
/// Only the trusted clinic (set at deployment) can add the vaccination hash.
/// No personal data is stored on-chain, only hashes.
contract IdentityRegistry {
    error ZeroAddress();
    error ZeroHash();
    error AlreadyRegistered();
    error NotRegistered();
    error NotTrustedClinic();
    error EvidenceAlreadyRegistered();

    // No "registered" flag: an account is registered when its identityHash is not zero.
    struct UserInfo {
        bytes32 identityHash;
        bytes32 vaccinationHash;
    }
    mapping(address => UserInfo) private _users;
    address private immutable _trustedClinic;

    event UserRegistered(address indexed account, bytes32 identityHash);
    event VaccinationRegistered(address indexed guardian, address indexed clinic, bytes32 recordHash);

    // The clinic is fixed at deployment and can't be changed.
    constructor(address trustedClinic_) {
        if (trustedClinic_ == address(0)) {
            revert ZeroAddress();
        }
        _trustedClinic = trustedClinic_;
    }
    // A wallet registers itself once with a hash of its identity.
    // Hashes are not unique, so this doesn't prove who the person is.
    function registerUser(bytes32 identityHash) external {
        if (identityHash == bytes32(0)) {
            revert ZeroHash();
        }
        UserInfo storage user = _users[msg.sender];
        if (user.identityHash != bytes32(0)) {
            revert AlreadyRegistered();
        }
        user.identityHash = identityHash;
        emit UserRegistered(msg.sender, identityHash);
    }
    // Only the clinic can call this, for a registered guardian, once per guardian.
    function registerVaccination(address guardian, bytes32 recordHash) external {
        if (msg.sender != _trustedClinic) {
            revert NotTrustedClinic();
        }
        UserInfo storage user = _users[guardian];
        if (user.identityHash == bytes32(0)) {
            revert NotRegistered();
        }
        if (recordHash == bytes32(0)) {
            revert ZeroHash();
        }
        if (user.vaccinationHash != bytes32(0)) {
            revert EvidenceAlreadyRegistered();
        }
        user.vaccinationHash = recordHash;
        emit VaccinationRegistered(guardian, msg.sender, recordHash);
    }
    // Never reverts: an unregistered account gives (false, 0, 0).
    // ConsentManager needs this so denied requests are still logged.
    function getUserInfo(address account) external view returns (bool registered, bytes32 identityHash, bytes32 vaccinationHash) {
        UserInfo storage user = _users[account];
        return (user.identityHash != bytes32(0), user.identityHash, user.vaccinationHash);
    }
    function trustedClinic() external view returns (address clinic) {
        return _trustedClinic;
    }
}
