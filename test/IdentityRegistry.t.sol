// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {IdentityRegistry} from "../contracts/IdentityRegistry.sol";

/**
 * @title IdentityRegistryTest
 * @notice Developer 2: Solidity unit tests for IdentityRegistry (run with `npm test`, Hardhat 3 + forge-std).
 * @dev Test IDs SOL-IR-01..08 are stable; the report and evaluation/results/test_results.csv cite them.
 *      Every test has a "Why it matters" line. Actors are synthetic addresses from makeAddr; hashes are
 *      synthetic bytes32 constants (the real ones are Python's SHA-256 commitments, which the contract
 *      treats as opaque bytes32 anyway).
 *      AI assistance: drafted with Claude (Anthropic) and reviewed by Developer 2.
 */
contract IdentityRegistryTest is Test {
    IdentityRegistry internal registry;

    address internal clinic = makeAddr("clinic");
    address internal guardian = makeAddr("guardian");
    address internal school = makeAddr("school");
    address internal attacker = makeAddr("attacker");

    bytes32 internal constant GUARDIAN_ID = keccak256("IDENTITY:v1 guardian (synthetic)");
    bytes32 internal constant SCHOOL_ID = keccak256("IDENTITY:v1 school (synthetic)");
    bytes32 internal constant RECORD = keccak256("VACCINATION:v1 child-demo-001 (synthetic)");
    bytes32 internal constant OTHER_RECORD = keccak256("VACCINATION:v1 tampered copy (synthetic)");

    // Copies of the registry events so vm.expectEmit can compare them.
    event UserRegistered(address indexed account, bytes32 identityHash);
    event VaccinationRegistered(address indexed guardian, address indexed clinic, bytes32 recordHash);

    /// @notice Deploy a fresh registry with one trusted clinic before every test.
    function setUp() public {
        registry = new IdentityRegistry(clinic);
    }

    // ------------------------------------------------------------------ helpers

    function _register(address account, bytes32 identityHash) internal {
        vm.prank(account);
        registry.registerUser(identityHash);
    }

    function _attest(address guardian_, bytes32 recordHash) internal {
        vm.prank(clinic);
        registry.registerVaccination(guardian_, recordHash);
    }

    function _assertInfo(address account, bool registered, bytes32 identityHash, bytes32 vaccinationHash) internal view {
        (bool r, bytes32 i, bytes32 v) = registry.getUserInfo(account);
        assertEq(r, registered, "registered flag");
        assertEq(i, identityHash, "identity hash");
        assertEq(v, vaccinationHash, "vaccination hash");
    }

    // ------------------------------------------------------------------ tests

    /// @notice [SOL-IR-01] Register a nonzero identity hash from one wallet; assert stored flag/hash and UserRegistered event.
    /// @dev Why it matters: every consent and access check starts from "is this wallet registered", so registration must bind the hash to msg.sender.
    /// @custom:requirement Registration/clinic: a wallet binds to one nonzero identity commitment
    function testRegisterUserStoresHash() public {
        vm.expectEmit(true, false, false, true, address(registry));
        emit UserRegistered(guardian, GUARDIAN_ID);
        _register(guardian, GUARDIAN_ID);

        _assertInfo(guardian, true, GUARDIAN_ID, bytes32(0));
        // Registration is per wallet: another account is unaffected.
        _assertInfo(school, false, bytes32(0), bytes32(0));
    }

    /// @notice [SOL-IR-02] Prove duplicate registration and zero hashes are rejected without corrupting previous state.
    /// @dev Why it matters: a second registration could swap a wallet's identity after consents were granted; a zero hash is not a commitment.
    /// @custom:requirement Registration/clinic: duplicate and zero registrations are rejected
    function testRejectDuplicateOrZeroRegistration() public {
        vm.prank(guardian);
        vm.expectRevert(IdentityRegistry.ZeroHash.selector);
        registry.registerUser(bytes32(0));
        _assertInfo(guardian, false, bytes32(0), bytes32(0));

        _register(guardian, GUARDIAN_ID);

        vm.prank(guardian);
        vm.expectRevert(IdentityRegistry.AlreadyRegistered.selector);
        registry.registerUser(SCHOOL_ID);

        // Same hash again is still a duplicate.
        vm.prank(guardian);
        vm.expectRevert(IdentityRegistry.AlreadyRegistered.selector);
        registry.registerUser(GUARDIAN_ID);

        _assertInfo(guardian, true, GUARDIAN_ID, bytes32(0));
    }

    /// @notice [SOL-IR-03] Use distinct clinic and attacker callers; only the configured clinic may attest.
    /// @dev Why it matters: the vaccination commitment is the evidence every access is checked against; only the trusted issuer may create it.
    /// @custom:requirement Registration/clinic: only the trusted clinic attests a vaccination
    function testOnlyTrustedClinicCanAttest() public {
        _register(guardian, GUARDIAN_ID);
        _register(attacker, SCHOOL_ID);

        // An outsider, a registered attacker, the guardian itself and the deployer (this test contract) all fail.
        address[4] memory notClinic = [makeAddr("stranger"), attacker, guardian, address(this)];
        for (uint256 i = 0; i < notClinic.length; i++) {
            vm.prank(notClinic[i]);
            vm.expectRevert(IdentityRegistry.NotTrustedClinic.selector);
            registry.registerVaccination(guardian, RECORD);
        }
        _assertInfo(guardian, true, GUARDIAN_ID, bytes32(0));

        // The clinic is never registered as a user and still attests.
        (bool clinicRegistered,,) = registry.getUserInfo(clinic);
        assertFalse(clinicRegistered, "clinic does not need registration");

        vm.expectEmit(true, true, false, true, address(registry));
        emit VaccinationRegistered(guardian, clinic, RECORD);
        _attest(guardian, RECORD);

        _assertInfo(guardian, true, GUARDIAN_ID, RECORD);
    }

    /// @notice [SOL-IR-04] Reject unregistered guardians, zero record hashes and overwriting frozen evidence.
    /// @dev Why it matters: frozen evidence means a later (possibly tampered) file can never replace the attested commitment.
    /// @custom:requirement Registration/clinic: evidence needs a registered guardian and is frozen
    function testRejectMissingGuardianOrReplacementHash() public {
        vm.prank(clinic);
        vm.expectRevert(IdentityRegistry.NotRegistered.selector);
        registry.registerVaccination(guardian, RECORD);

        _register(guardian, GUARDIAN_ID);

        vm.prank(clinic);
        vm.expectRevert(IdentityRegistry.ZeroHash.selector);
        registry.registerVaccination(guardian, bytes32(0));

        _attest(guardian, RECORD);

        vm.prank(clinic);
        vm.expectRevert(IdentityRegistry.EvidenceAlreadyRegistered.selector);
        registry.registerVaccination(guardian, OTHER_RECORD);

        vm.prank(clinic);
        vm.expectRevert(IdentityRegistry.EvidenceAlreadyRegistered.selector);
        registry.registerVaccination(guardian, RECORD);

        _assertInfo(guardian, true, GUARDIAN_ID, RECORD);
    }

    /// @notice [SOL-IR-05] Assert the query returns only registered flag and the two bytes32 commitments.
    /// @dev Why it matters: the chain is public, so the read API must expose references only, never identity or health plaintext.
    /// @custom:requirement Data minimisation: the read API exposes commitments only
    function testRetrieveReferencesContainsNoPlaintext() public {
        _register(guardian, GUARDIAN_ID);
        _attest(guardian, RECORD);

        // Raw ABI return data is exactly three 32-byte words: bool, bytes32, bytes32. No string/bytes (dynamic) fields.
        (bool ok, bytes memory ret) =
            address(registry).staticcall(abi.encodeCall(IdentityRegistry.getUserInfo, (guardian)));
        assertTrue(ok, "getUserInfo succeeds");
        assertEq(ret.length, 96, "three static words only");

        (bool r, bytes32 i, bytes32 v) = abi.decode(ret, (bool, bytes32, bytes32));
        assertTrue(r);
        assertEq(i, GUARDIAN_ID, "identity commitment returned exactly");
        assertEq(v, RECORD, "record commitment returned exactly");
    }

    /// @notice [SOL-IR-06] getUserInfo on a fresh address returns (false, 0, 0) without reverting; registered non-guardians and unattested guardians have vaccinationHash 0.
    /// @dev Why it matters: ConsentManager and the Python menu call getUserInfo for unregistered parties; a revert would erase the denied AccessAttempt event.
    /// @custom:requirement Audit/integrity: lookups of unregistered accounts never revert
    function testGetUserInfoForUnregisteredAndUnattestedAccounts() public {
        _assertInfo(makeAddr("fresh"), false, bytes32(0), bytes32(0));
        _assertInfo(address(0), false, bytes32(0), bytes32(0));

        _register(school, SCHOOL_ID);
        _assertInfo(school, true, SCHOOL_ID, bytes32(0));

        _register(guardian, GUARDIAN_ID);
        _assertInfo(guardian, true, GUARDIAN_ID, bytes32(0));
    }

    /// @notice [SOL-IR-07] The constructor rejects a zero clinic and stores the configured clinic.
    /// @dev Why it matters: with a zero clinic nobody could ever attest, and the trusted issuer must be fixed at deployment, not chosen later.
    /// @custom:requirement Registration/clinic: the trusted clinic is fixed at deployment
    function testConstructorRejectsZeroClinicAndStoresClinic() public {
        vm.expectRevert(IdentityRegistry.ZeroAddress.selector);
        new IdentityRegistry(address(0));

        assertEq(registry.trustedClinic(), clinic, "trusted clinic");
    }

    /// @notice [SOL-IR-08] Revert order: NotTrustedClinic before NotRegistered before ZeroHash; ZeroHash before AlreadyRegistered.
    /// @dev Why it matters: the menu prints the bare error name, so a wrong actor must always see "rejected: NotTrustedClinic" whatever else is wrong.
    /// @custom:requirement Interface: revert order matches the Python error messages
    function testAttestationAndRegistrationRevertOrder() public {
        // Wrong caller + unregistered guardian + zero hash -> NotTrustedClinic.
        vm.prank(attacker);
        vm.expectRevert(IdentityRegistry.NotTrustedClinic.selector);
        registry.registerVaccination(guardian, bytes32(0));

        // Clinic + unregistered guardian + zero hash -> NotRegistered.
        vm.prank(clinic);
        vm.expectRevert(IdentityRegistry.NotRegistered.selector);
        registry.registerVaccination(guardian, bytes32(0));

        // Already registered + zero hash -> ZeroHash.
        _register(guardian, GUARDIAN_ID);
        vm.prank(guardian);
        vm.expectRevert(IdentityRegistry.ZeroHash.selector);
        registry.registerUser(bytes32(0));
    }
}
