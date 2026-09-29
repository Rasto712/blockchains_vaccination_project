// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

/**
 * @title IdentityRegistry
 * @notice Hashed account identity and one frozen vaccination commitment per guardian.
 * @dev Wallets register themselves once; the one trusted clinic then attests one frozen record commitment per guardian.
 *      The child is the record subject; its guardian wallet is the on-chain data owner.
 *      One trusted clinic is configured at deployment.
 *      Store no identity plaintext, vaccination values, file paths or salts on-chain.
 *      Test file: test/IdentityRegistry.t.sol. See docs/CONTRACT_API.md.
 */
contract IdentityRegistry {
    error ZeroAddress();
    error ZeroHash();
    error AlreadyRegistered();
    error NotRegistered();
    error NotTrustedClinic();
    error EvidenceAlreadyRegistered();

    // There is no registered flag: registerUser rejects a zero identityHash and nothing ever clears one,
    // so an account is registered exactly when its identityHash is nonzero (as MissingEvidence means vaccinationHash == 0).
    struct UserInfo {
        bytes32 identityHash;
        bytes32 vaccinationHash;
    }
    mapping(address => UserInfo) private _users;
    address private immutable _trustedClinic;

    event UserRegistered(address indexed account, bytes32 identityHash);
    event VaccinationRegistered(address indexed guardian, address indexed clinic, bytes32 recordHash);

    /**
     * @notice Configure the one trusted clinic for this local demonstration.
     * @dev Rejects a zero address. The clinic is immutable: it can never be changed after deployment.
     * @param trustedClinic_ Nonzero clinic signer.
     */
    constructor(address trustedClinic_) {
        if (trustedClinic_ == address(0)) {
            revert ZeroAddress();
        }
        _trustedClinic = trustedClinic_;
    }
    /**
     * @notice Register the caller once using a salted identity commitment.
     * @dev Uses msg.sender. Revert order: ZeroHash, then AlreadyRegistered. Persists the hash and emits UserRegistered.
     *      Commitments are not unique across wallets: a wallet may register a copy of another wallet's public
     *      commitment (SOL-IR-09). Registration binds a commitment to a wallet; it proves no identity and does
     *      not stop Sybil wallets.
     * @param identityHash Nonzero SHA-256 commitment from the private local identity fixture.
     */
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
    /**
     * @notice Attest one frozen local vaccination file for a registered guardian.
     * @dev Only the configured clinic may call; the clinic itself does not need to be registered.
     *      Revert order: NotTrustedClinic, NotRegistered (guardian), ZeroHash, EvidenceAlreadyRegistered,
     *      so a wrong actor always sees NotTrustedClinic. Emits VaccinationRegistered.
     * @param guardian Registered guardian wallet representing the child.
     * @param recordHash Nonzero commitment to the exact local JSON bytes plus private salt.
     */
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
    /**
     * @notice Read minimal identity/evidence references, never raw health data.
     * @dev Never reverts: an unregistered account returns (false, 0, 0). ConsentManager relies on this
     *      so a denied access to an unregistered party is still logged.
     * @param account Wallet to query; may be a guardian or requester.
     * @return registered Whether the account completed registration.
     * @return identityHash Registered salted identity commitment.
     * @return vaccinationHash Frozen vaccination commitment, or zero when absent.
     */
    function getUserInfo(address account) external view returns (bool registered, bytes32 identityHash, bytes32 vaccinationHash) {
        UserInfo storage user = _users[account];
        return (user.identityHash != bytes32(0), user.identityHash, user.vaccinationHash);
    }
    /**
     * @notice Return the configured issuer reference.
     * @return clinic Trusted clinic signer address; not a private key.
     */
    function trustedClinic() external view returns (address clinic) {
        return _trustedClinic;
    }
}
