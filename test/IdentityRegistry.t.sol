// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {Vm} from "forge-std/Vm.sol";
import {IdentityRegistry} from "../contracts/IdentityRegistry.sol";

/// Tests for IdentityRegistry. Test IDs (SOL-IR-xx) are used by the results export.
/// Addresses and hashes are made-up test values.
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

    // Fresh registry before every test.
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

    /// @notice [SOL-IR-01] A wallet registers a nonzero identity hash and it is stored with the event.
    /// @dev Why it matters: Every later check starts from the wallet being registered.
    /// @custom:requirement Registration/clinic: a wallet binds to one nonzero identity commitment
    function testRegisterUserStoresHash() public {
        vm.expectEmit(true, false, false, true, address(registry));
        emit UserRegistered(guardian, GUARDIAN_ID);
        _register(guardian, GUARDIAN_ID);

        _assertInfo(guardian, true, GUARDIAN_ID, bytes32(0));
        // Another account is unaffected.
        _assertInfo(school, false, bytes32(0), bytes32(0));
    }

    /// @notice [SOL-IR-02] Duplicate registration and zero hashes are rejected and old state stays the same.
    /// @dev Why it matters: A second registration could swap a wallet's identity.
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

    /// @notice [SOL-IR-03] Only the clinic can attest, judged by msg.sender (not tx.origin).
    /// @dev Why it matters: Only the trusted clinic should create the evidence hash.
    /// @custom:requirement Registration/clinic: only the trusted clinic attests a vaccination
    function testOnlyTrustedClinicCanAttest() public {
        _register(guardian, GUARDIAN_ID);
        _register(attacker, SCHOOL_ID);

        // Outsider, attacker, guardian and deployer all fail.
        address[4] memory notClinic = [makeAddr("stranger"), attacker, guardian, address(this)];
        for (uint256 i = 0; i < notClinic.length; i++) {
            vm.prank(notClinic[i]);
            vm.expectRevert(IdentityRegistry.NotTrustedClinic.selector);
            registry.registerVaccination(guardian, RECORD);
        }
        // tx.origin is the clinic but msg.sender is not.
        vm.prank(attacker, clinic);
        vm.expectRevert(IdentityRegistry.NotTrustedClinic.selector);
        registry.registerVaccination(guardian, RECORD);
        _assertInfo(guardian, true, GUARDIAN_ID, bytes32(0));

        // The clinic is never registered as a user and still attests.
        (bool clinicRegistered,,) = registry.getUserInfo(clinic);
        assertFalse(clinicRegistered, "clinic does not need registration");

        vm.expectEmit(true, true, false, true, address(registry));
        emit VaccinationRegistered(guardian, clinic, RECORD);
        _attest(guardian, RECORD);

        _assertInfo(guardian, true, GUARDIAN_ID, RECORD);
    }

    /// @notice [SOL-IR-04] Reject unregistered guardians, zero hashes and overwriting a stored record hash.
    /// @dev Why it matters: The stored hash is frozen so a tampered file can't replace it.
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

    /// @notice [SOL-IR-05] Reads and events only expose fixed-size hashes.
    /// @dev Why it matters: The chain is public, so no plain data should show up.
    /// @custom:requirement Data minimisation: the read API and the events expose commitments only
    function testRetrieveReferencesContainsNoPlaintext() public {
        vm.recordLogs();
        _register(guardian, GUARDIAN_ID);
        _attest(guardian, RECORD);
        Vm.Log[] memory logs = vm.getRecordedLogs();

        // One event per call: one 32-byte data word and only address topics.
        assertEq(logs.length, 2, "one event per call, nothing else logged");
        assertEq(logs[0].emitter, address(registry));
        assertEq(logs[0].topics.length, 2, "UserRegistered: signature + account");
        assertEq(logs[0].topics[1], bytes32(uint256(uint160(guardian))));
        assertEq(logs[0].data, abi.encode(GUARDIAN_ID), "UserRegistered data = identity commitment only");
        assertEq(logs[1].emitter, address(registry));
        assertEq(logs[1].topics.length, 3, "VaccinationRegistered: signature + guardian + clinic");
        assertEq(logs[1].topics[1], bytes32(uint256(uint160(guardian))));
        assertEq(logs[1].topics[2], bytes32(uint256(uint160(clinic))));
        assertEq(logs[1].data, abi.encode(RECORD), "VaccinationRegistered data = record commitment only");

        // Return data should be exactly three 32-byte words.
        (bool ok, bytes memory ret) =
            address(registry).staticcall(abi.encodeCall(IdentityRegistry.getUserInfo, (guardian)));
        assertTrue(ok, "getUserInfo succeeds");
        assertEq(ret.length, 96, "three static words only");

        (bool r, bytes32 i, bytes32 v) = abi.decode(ret, (bool, bytes32, bytes32));
        assertTrue(r);
        assertEq(i, GUARDIAN_ID, "identity commitment returned exactly");
        assertEq(v, RECORD, "record commitment returned exactly");
    }

    /// @notice [SOL-IR-06] getUserInfo on an unknown address returns (false, 0, 0) and doesn't revert.
    /// @dev Why it matters: A revert would erase the denied AccessAttempt event.
    /// @custom:requirement Audit/integrity: lookups of unregistered accounts never revert
    function testGetUserInfoForUnregisteredAndUnattestedAccounts() public {
        _assertInfo(makeAddr("fresh"), false, bytes32(0), bytes32(0));
        _assertInfo(address(0), false, bytes32(0), bytes32(0));

        _register(school, SCHOOL_ID);
        _assertInfo(school, true, SCHOOL_ID, bytes32(0));

        _register(guardian, GUARDIAN_ID);
        _assertInfo(guardian, true, GUARDIAN_ID, bytes32(0));
    }

    /// @notice [SOL-IR-07] The constructor rejects a zero clinic and stores the given one.
    /// @dev Why it matters: The clinic is fixed at deployment.
    /// @custom:requirement Registration/clinic: the trusted clinic is fixed at deployment
    function testConstructorRejectsZeroClinicAndStoresClinic() public {
        vm.expectRevert(IdentityRegistry.ZeroAddress.selector);
        new IdentityRegistry(address(0));

        assertEq(registry.trustedClinic(), clinic, "trusted clinic");
    }

    /// @notice [SOL-IR-08] Check the order of reverts when several things are wrong at once.
    /// @dev Why it matters: The menu shows the first error name, so the order matters.
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

        // Evidence already attested + zero hash -> ZeroHash, not EvidenceAlreadyRegistered.
        _attest(guardian, RECORD);
        vm.prank(clinic);
        vm.expectRevert(IdentityRegistry.ZeroHash.selector);
        registry.registerVaccination(guardian, bytes32(0));

        // Wrong caller + evidence already attested + zero hash -> still NotTrustedClinic.
        vm.prank(attacker);
        vm.expectRevert(IdentityRegistry.NotTrustedClinic.selector);
        registry.registerVaccination(guardian, bytes32(0));
    }

    /// @notice [SOL-IR-09] Known limitation: another wallet can register a copy of someone's hash, and it gets no evidence.
    /// @dev Why it matters: A hash is tied to a wallet, not to a person.
    /// @custom:requirement Registration/clinic: stated limitation, a commitment identifies no one on its own
    function testIdentityCommitmentsAreNotUnique() public {
        _register(guardian, GUARDIAN_ID);
        (, bytes32 published,) = registry.getUserInfo(guardian);

        vm.expectEmit(true, false, false, true, address(registry));
        emit UserRegistered(attacker, published);
        _register(attacker, published);

        _assertInfo(attacker, true, GUARDIAN_ID, bytes32(0));
        _assertInfo(guardian, true, GUARDIAN_ID, bytes32(0));

        // Evidence stays per wallet: attesting the guardian gives the copy nothing.
        _attest(guardian, RECORD);
        _assertInfo(guardian, true, GUARDIAN_ID, RECORD);
        _assertInfo(attacker, true, GUARDIAN_ID, bytes32(0));
    }
}
