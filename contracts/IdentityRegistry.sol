// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.28;

/**
 * @title IdentityRegistry
 * @notice Developer 1: hashed account identity and one frozen vaccination commitment per guardian.
 * @dev Scaffold only. Every constructor/function deliberately reverts until implemented.
 *      The child is the record subject; its guardian wallet is the on-chain data owner.
 *      One trusted clinic is configured at deployment. Do not build a clinic-management system.
 *      Store no identity plaintext, vaccination values, file paths or salts on-chain.
 *      Test file: test/IdentityRegistry.t.sol. See docs/CONTRACT_API.md.
 */
contract IdentityRegistry {
    error NotImplemented();

    // Planned storage; these declarations do not implement registration or authorization.
    struct UserInfo {
        bool registered;
        bytes32 identityHash;
        bytes32 vaccinationHash;
    }
    mapping(address => UserInfo) private _users;
    address private _trustedClinic;

    event UserRegistered(address indexed account, bytes32 identityHash);
    event VaccinationRegistered(address indexed guardian, address indexed clinic, bytes32 recordHash);

    /**
     * @notice Configure the one trusted clinic for this local demonstration.
     * @dev Do not accept a zero address. Deployment currently reverts because the constructor is unfinished.
     * @param trustedClinic_ Nonzero clinic signer; validate and store during implementation.
     */
    constructor(address trustedClinic_) {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Register the caller once using a salted identity commitment.
     * @dev Use msg.sender, reject duplicate registration, persist the hash and emit UserRegistered.
     * @param identityHash Nonzero SHA-256 commitment from the private local identity fixture.
     */
    function registerUser(bytes32 identityHash) external {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Attest one frozen local vaccination file for a registered guardian.
     * @dev Only the configured clinic may call. Reject missing guardian, duplicate/replacement evidence and zero hashes; emit VaccinationRegistered.
     * @param guardian Registered guardian wallet representing the child.
     * @param recordHash Nonzero commitment to the exact local JSON bytes plus private salt.
     */
    function registerVaccination(address guardian, bytes32 recordHash) external {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Read minimal identity/evidence references, never raw health data.
     * @dev An unregistered query should eventually return registered=false and zero commitments; the scaffold currently reverts.
     * @param account Wallet to query; may be a guardian or requester.
     * @return registered Whether the account completed registration.
     * @return identityHash Registered salted identity commitment.
     * @return vaccinationHash Frozen vaccination commitment, or zero when absent.
     */
    function getUserInfo(address account) external view returns (bool registered, bytes32 identityHash, bytes32 vaccinationHash) {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Return the configured issuer reference.
     * @return clinic Trusted clinic signer address; not a private key.
     */
    function trustedClinic() external view returns (address clinic) {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
}
