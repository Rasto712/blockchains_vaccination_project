// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {Vm} from "forge-std/Vm.sol";
import {IdentityRegistry} from "../contracts/IdentityRegistry.sol";
import {ConsentManager} from "../contracts/ConsentManager.sol";
import {ConsentRewardToken} from "../contracts/ConsentRewardToken.sol";

/**
 * @title ConsentManagerTest
 * @notice Developer 2: Solidity unit tests for ConsentManager, wired to a real IdentityRegistry and ConsentRewardToken.
 * @dev Test IDs SOL-CM-01..19 are stable; the report and evaluation/results/test_results.csv cite them.
 *      Business denials are asserted through the emitted AccessAttempt event (what Python reads), never
 *      through a revert. Reason codes are compared as literal numbers 0-7 so a reordered enum fails here.
 *      Custom-error selectors are computed from their signature strings (the names agreed in
 *      docs/CONTRACT_API.md), so this file compiles before Developer 1 declares every error, and it
 *      fails loudly if a name drifts.
 *      AI assistance: drafted with Claude (Anthropic) and reviewed by Developer 2.
 */
contract ConsentManagerTest is Test {
    IdentityRegistry internal registry;
    ConsentRewardToken internal token;
    ConsentManager internal manager;

    address internal clinic = makeAddr("clinic");
    address internal guardian = makeAddr("guardian");
    address internal guardian2 = makeAddr("guardian2");
    address internal school = makeAddr("school");
    address internal doctor = makeAddr("doctor");
    address internal stranger = makeAddr("stranger"); // never registers

    uint8 internal constant MEASLES_STATUS = 1;
    uint8 internal constant VACCINATION_SCHEDULE = 2;
    uint8 internal constant UNSUPPORTED = 3;

    // Frozen reason codes, identical to app/models.py.
    uint8 internal constant ALLOWED = 0;
    uint8 internal constant NOT_REGISTERED = 1;
    uint8 internal constant UNSUPPORTED_SCOPE = 2;
    uint8 internal constant MISSING_EVIDENCE = 3;
    uint8 internal constant NO_CONSENT = 4;
    uint8 internal constant EXPIRED = 5;
    uint8 internal constant REVOKED = 6;
    uint8 internal constant HASH_MISMATCH = 7;

    bytes32 internal constant RECORD = keccak256("VACCINATION:v1 child-demo-001 (synthetic)");
    bytes32 internal constant RECORD2 = keccak256("VACCINATION:v1 child-demo-002 (synthetic)");
    bytes32 internal constant TAMPERED = keccak256("VACCINATION:v1 tampered copy (synthetic)");

    // Copies of the contract events so vm.expectEmit can compare them.
    event ConsentGranted(address indexed owner, address indexed requester, uint8 indexed scope, uint256 expiresAt);
    event ConsentRevoked(address indexed owner, address indexed requester, uint8 indexed scope);
    event AccessAttempt(
        address indexed owner, address indexed requester, uint8 indexed scope, uint256 timestamp, bool allowed, ConsentManager.Reason reason
    );

    bytes32 internal constant ACCESS_ATTEMPT_TOPIC =
        keccak256("AccessAttempt(address,address,uint8,uint256,bool,uint8)");
    bytes32 internal constant CONSENT_REVOKED_TOPIC = keccak256("ConsentRevoked(address,address,uint8)");
    bytes32 internal constant REWARD_MINTED_TOPIC = keccak256("RewardMinted(address,uint256)");

    /// @notice Deploy registry -> token -> manager -> setMinterOnce (the real deployment order), then register and attest the demo actors.
    function setUp() public {
        registry = new IdentityRegistry(clinic);
        token = new ConsentRewardToken();
        manager = new ConsentManager(address(registry), address(token));
        token.setMinterOnce(address(manager));

        _register(guardian, "guardian");
        _register(guardian2, "guardian2");
        _register(school, "school");
        _register(doctor, "doctor");

        vm.startPrank(clinic);
        registry.registerVaccination(guardian, RECORD);
        registry.registerVaccination(guardian2, RECORD2);
        vm.stopPrank();

        // Start from a realistic timestamp rather than 1.
        vm.warp(1_760_000_000);
    }

    // ------------------------------------------------------------------ helpers

    function _err(string memory signature) internal pure returns (bytes4) {
        return bytes4(keccak256(bytes(signature)));
    }

    function _register(address account, string memory label) internal {
        vm.prank(account);
        registry.registerUser(keccak256(abi.encodePacked("IDENTITY:v1 ", label, " (synthetic)")));
    }

    function _grant(address owner, address requester, uint8 scope, uint16 durationDays) internal {
        vm.prank(owner);
        manager.grantConsent(requester, scope, durationDays);
    }

    function _revoke(address owner, address requester, uint8 scope) internal {
        vm.prank(owner);
        manager.revokeConsent(requester, scope);
    }

    function _check(address owner, address requester, uint8 scope) internal view returns (bool allowed, uint8 reason) {
        ConsentManager.Reason r;
        (allowed, r) = manager.checkAccess(owner, requester, scope);
        reason = uint8(r);
    }

    /// @dev Sends requestAccess as `requester` and returns the ONE AccessAttempt it logged (what Python reads).
    ///      Fails if the call emits zero or several AccessAttempt events, or if the return value disagrees with the event.
    function _request(address requester, address owner, uint8 scope, bytes32 observedHash)
        internal
        returns (bool allowed, uint8 reason)
    {
        vm.recordLogs();
        vm.prank(requester);
        (bool retAllowed, ConsentManager.Reason retReason) = manager.requestAccess(owner, scope, observedHash);
        Vm.Log[] memory logs = vm.getRecordedLogs();

        uint256 found;
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].emitter == address(manager) && logs[i].topics[0] == ACCESS_ATTEMPT_TOPIC) {
                found++;
                assertEq(logs[i].topics[1], bytes32(uint256(uint160(owner))), "event owner = owner as passed");
                assertEq(logs[i].topics[2], bytes32(uint256(uint160(requester))), "event requester = msg.sender");
                assertEq(logs[i].topics[3], bytes32(uint256(scope)), "event scope = raw scope sent");
                uint256 timestamp;
                uint8 r;
                (timestamp, allowed, r) = abi.decode(logs[i].data, (uint256, bool, uint8));
                reason = r;
                assertEq(timestamp, block.timestamp, "event timestamp = block.timestamp");
            }
            // No token activity of any kind during access.
            assertTrue(logs[i].emitter != address(token), "no token event during access");
        }
        assertEq(found, 1, "exactly one AccessAttempt per requestAccess");
        assertEq(retAllowed, allowed, "return value matches event (allowed)");
        assertEq(uint8(retReason), reason, "return value matches event (reason)");
    }

    function _expiry(address owner, address requester, uint8 scope) internal view returns (uint256 expiresAt) {
        (expiresAt,) = manager.getConsent(owner, requester, scope);
    }

    function _countLogs(Vm.Log[] memory logs, address emitter, bytes32 topic0) internal pure returns (uint256 n) {
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].emitter == emitter && logs[i].topics.length > 0 && logs[i].topics[0] == topic0) n++;
        }
    }

    // ------------------------------------------------------------------ grants

    /// @notice [SOL-CM-01] Register owner/requester, attest a record, then assert grant expiry, ConsentGranted and a reward minted to the granting guardian.
    /// @dev Why it matters: this is the core guardian action; the reward must go to the guardian (msg.sender), never to the requester, because the menu shows the guardian's balance.
    /// @custom:requirement Consent: guardian grants scoped, time-limited consent; Rewards: first grant rewards the guardian
    function testRegisteredGuardianCanGrant() public {
        uint256 expected = block.timestamp + 30 days;

        vm.expectEmit(true, true, true, true, address(manager));
        emit ConsentGranted(guardian, school, MEASLES_STATUS, expected);
        vm.recordLogs();
        _grant(guardian, school, MEASLES_STATUS, 30);
        Vm.Log[] memory logs = vm.getRecordedLogs();

        (uint256 expiresAt, bool revoked) = manager.getConsent(guardian, school, MEASLES_STATUS);
        assertEq(expiresAt, expected, "expiresAt = now + 30 days");
        assertFalse(revoked);
        assertTrue(manager.hasReceivedReward(guardian, school, MEASLES_STATUS));

        assertEq(_countLogs(logs, address(token), REWARD_MINTED_TOPIC), 1, "one RewardMinted");
        assertEq(token.balanceOf(guardian), 1, "guardian 0 -> 1");
        assertEq(token.balanceOf(school), 0, "requester never rewarded");
        assertEq(token.totalSupply(), 1);

        (bool allowed, uint8 reason) = _check(guardian, school, MEASLES_STATUS);
        assertTrue(allowed);
        assertEq(reason, ALLOWED);
    }

    /// @notice [SOL-CM-02] Reject 0, 366 and 65535 days; accept 1, 194, 195 and 365 days with exact expiry (195+ catches the uint24 overflow).
    /// @dev Why it matters: consent must be time-limited to at most a year, and a missing uint256 cast would make every grant of 195+ days revert.
    /// @custom:requirement Consent: duration is 1-365 whole days
    function testDurationBounds() public {
        uint16[3] memory bad = [uint16(0), 366, type(uint16).max];
        for (uint256 i = 0; i < bad.length; i++) {
            vm.prank(guardian);
            vm.expectRevert(_err("InvalidDuration()"));
            manager.grantConsent(school, MEASLES_STATUS, bad[i]);
        }
        assertEq(_expiry(guardian, school, MEASLES_STATUS), 0, "rejected grants leave no state");

        // Four accepted durations on four distinct tuples.
        _grant(guardian, school, MEASLES_STATUS, 1);
        assertEq(_expiry(guardian, school, MEASLES_STATUS), block.timestamp + 1 days);
        _grant(guardian, school, VACCINATION_SCHEDULE, 194);
        assertEq(_expiry(guardian, school, VACCINATION_SCHEDULE), block.timestamp + 194 days);
        _grant(guardian, doctor, MEASLES_STATUS, 195);
        assertEq(_expiry(guardian, doctor, MEASLES_STATUS), block.timestamp + 195 days);
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 365);
        assertEq(_expiry(guardian, doctor, VACCINATION_SCHEDULE), block.timestamp + 365 days);
    }

    /// @notice [SOL-CM-03] An already-active tuple cannot be re-granted (not even one second before expiry) or rewarded again.
    /// @dev Why it matters: silently extending an active grant, or re-minting on it, would let a guardian farm rewards or change terms without a revoke.
    /// @custom:requirement Consent: an active duplicate grant is rejected
    function testRejectActiveDuplicateGrant() public {
        _grant(guardian, school, MEASLES_STATUS, 10);
        uint256 expiresAt = _expiry(guardian, school, MEASLES_STATUS);

        vm.prank(guardian);
        vm.expectRevert(_err("ConsentStillActive()"));
        manager.grantConsent(school, MEASLES_STATUS, 365);

        vm.warp(expiresAt - 1);
        vm.prank(guardian);
        vm.expectRevert(_err("ConsentStillActive()"));
        manager.grantConsent(school, MEASLES_STATUS, 1);

        assertEq(_expiry(guardian, school, MEASLES_STATUS), expiresAt, "expiry unchanged");
        assertEq(token.balanceOf(guardian), 1, "still one reward");
        assertEq(token.totalSupply(), 1);
    }

    /// @notice [SOL-CM-04] Separate owners: a caller can only create or revoke consent keyed to its own wallet.
    /// @dev Why it matters: consent belongs to the guardian; no other guardian, requester or outsider may revoke or alter it.
    /// @custom:requirement Consent: only the owner can change its own consent
    function testCallerCannotChangeAnotherOwnersConsent() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        uint256 expiresAt = _expiry(guardian, school, MEASLES_STATUS);

        // guardian2, the school itself and a stranger all try to revoke; each only addresses its OWN (empty) grant.
        address[3] memory others = [guardian2, school, stranger];
        for (uint256 i = 0; i < others.length; i++) {
            vm.prank(others[i]);
            vm.expectRevert(_err("NoConsentToRevoke()"));
            manager.revokeConsent(school, MEASLES_STATUS);
        }

        // guardian2 grants and revokes its own consent for the same school and scope.
        _grant(guardian2, school, MEASLES_STATUS, 5);
        _revoke(guardian2, school, MEASLES_STATUS);

        (uint256 e, bool revoked) = manager.getConsent(guardian, school, MEASLES_STATUS);
        assertEq(e, expiresAt, "guardian's expiry untouched");
        assertFalse(revoked, "guardian's grant not revoked");
        (bool allowed, uint8 reason) = _check(guardian, school, MEASLES_STATUS);
        assertTrue(allowed);
        assertEq(reason, ALLOWED);
        (, reason) = _check(guardian2, school, MEASLES_STATUS);
        assertEq(reason, REVOKED, "guardian2's own grant revoked");
    }

    /// @notice [SOL-CM-05] Grants need both parties registered and a supported scope.
    /// @dev Why it matters: consent must name a real, registered requester; granting to an unregistered address or an unknown scope is a user error, not a grant.
    /// @custom:requirement Consent: a grant needs registered parties and a supported scope
    function testGrantRequiresRegisteredPartiesAndSupportedScope() public {
        vm.prank(guardian);
        vm.expectRevert(_err("NotRegistered()"));
        manager.grantConsent(stranger, MEASLES_STATUS, 30);

        vm.prank(stranger);
        vm.expectRevert(_err("NotRegistered()"));
        manager.grantConsent(school, MEASLES_STATUS, 30);

        vm.prank(guardian);
        vm.expectRevert(_err("UnsupportedScope()"));
        manager.grantConsent(school, UNSUPPORTED, 30);

        vm.prank(guardian);
        vm.expectRevert(_err("UnsupportedScope()"));
        manager.grantConsent(school, 0, 30);

        vm.prank(guardian);
        vm.expectRevert(_err("UnsupportedScope()"));
        manager.revokeConsent(school, UNSUPPORTED);

        assertEq(token.totalSupply(), 0, "no rewards from rejected grants");
    }

    // ------------------------------------------------------------------ access denials

    /// @notice [SOL-CM-06] Wrong requester, wrong scope and unsupported scope give a false result and a persistent denied AccessAttempt (no revert); the raw scope 3 is logged.
    /// @dev Why it matters: consent is for an exact requester and scope; the audit log must record denied attempts, including the raw scope the audit view prints.
    /// @custom:requirement Consent/Audit: exact requester and scope; denied attempts are logged
    function testWrongRequesterAndUnsupportedScopeAreLoggedDenied() public {
        _grant(guardian, school, MEASLES_STATUS, 30);

        (bool allowed, uint8 reason) = _request(doctor, guardian, MEASLES_STATUS, RECORD);
        assertFalse(allowed, "doctor has no measles grant");
        assertEq(reason, NO_CONSENT);

        (allowed, reason) = _request(school, guardian, VACCINATION_SCHEDULE, RECORD);
        assertFalse(allowed, "school has no schedule grant");
        assertEq(reason, NO_CONSENT);

        // Exact event for the unsupported scope, including the raw value 3.
        vm.expectEmit(true, true, true, true, address(manager));
        emit AccessAttempt(guardian, school, UNSUPPORTED, block.timestamp, false, ConsentManager.Reason.UnsupportedScope);
        (allowed, reason) = _request(school, guardian, UNSUPPORTED, RECORD);
        assertFalse(allowed);
        assertEq(reason, UNSUPPORTED_SCOPE);

        (allowed, reason) = _request(school, guardian, type(uint8).max, RECORD);
        assertEq(reason, UNSUPPORTED_SCOPE, "scope 255 is also a logged denial");
    }

    /// @notice [SOL-CM-07] Each missing prerequisite (unregistered requester, unregistered owner, owner without evidence) creates a denied event and no token movement.
    /// @dev Why it matters: an attacker with no identity, or a record that was never attested, must be refused, and the refusal must still be auditable.
    /// @custom:requirement Audit/integrity: unregistered parties or missing evidence are denied and logged
    function testUnregisteredOrMissingEvidenceIsLoggedDenied() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        uint256 supplyBefore = token.totalSupply();

        (bool allowed, uint8 reason) = _request(stranger, guardian, MEASLES_STATUS, RECORD);
        assertFalse(allowed);
        assertEq(reason, NOT_REGISTERED, "unregistered requester");

        (allowed, reason) = _request(school, stranger, MEASLES_STATUS, RECORD);
        assertFalse(allowed);
        assertEq(reason, NOT_REGISTERED, "unregistered owner");

        // The school is registered but has no attested vaccination record.
        (allowed, reason) = _request(doctor, school, MEASLES_STATUS, RECORD);
        assertFalse(allowed);
        assertEq(reason, MISSING_EVIDENCE, "owner without evidence");

        assertEq(token.totalSupply(), supplyBefore, "no mint during access");
        assertEq(token.balanceOf(stranger), 0);
    }

    /// @notice [SOL-CM-08] At expiresAt - 1 access is allowed; at exactly expiresAt it is denied Expired (checkAccess and requestAccess agree).
    /// @dev Why it matters: expiry is exclusive (valid while now < expiresAt); an off-by-one would give a requester one extra block of access.
    /// @custom:requirement Consent: consent is invalid at the exact expiry timestamp
    function testExactExpiryBoundaryIsDenied() public {
        _grant(guardian, school, MEASLES_STATUS, 1);
        uint256 expiresAt = _expiry(guardian, school, MEASLES_STATUS);

        vm.warp(expiresAt - 1);
        (bool allowed, uint8 reason) = _check(guardian, school, MEASLES_STATUS);
        assertTrue(allowed, "one second before expiry");
        (allowed, reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertTrue(allowed);
        assertEq(reason, ALLOWED);

        vm.warp(expiresAt);
        (allowed, reason) = _check(guardian, school, MEASLES_STATUS);
        assertFalse(allowed, "exactly at expiry");
        assertEq(reason, EXPIRED);
        (allowed, reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertFalse(allowed);
        assertEq(reason, EXPIRED);
    }

    /// @notice [SOL-CM-09] Revoke emits ConsentRevoked and blocks access; a second revoke is a silent no-op; never-granted reverts; expired-but-not-revoked can still be revoked.
    /// @dev Why it matters: the guardian must be able to withdraw consent at any time, and repeating the action must be safe for the menu.
    /// @custom:requirement Consent: revocation at any time, safe to repeat
    function testRevocationIsIdempotentAndBlocksAccess() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        uint256 expiresAt = _expiry(guardian, school, MEASLES_STATUS);

        vm.expectEmit(true, true, true, true, address(manager));
        emit ConsentRevoked(guardian, school, MEASLES_STATUS);
        _revoke(guardian, school, MEASLES_STATUS);

        (uint256 e, bool revoked) = manager.getConsent(guardian, school, MEASLES_STATUS);
        assertTrue(revoked);
        assertEq(e, expiresAt, "revoke keeps expiresAt");
        (bool allowed, uint8 reason) = _check(guardian, school, MEASLES_STATUS);
        assertFalse(allowed);
        assertEq(reason, REVOKED);
        (allowed, reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertFalse(allowed);
        assertEq(reason, REVOKED);

        // Second revoke: no revert, no event.
        vm.recordLogs();
        _revoke(guardian, school, MEASLES_STATUS);
        assertEq(_countLogs(vm.getRecordedLogs(), address(manager), CONSENT_REVOKED_TOPIC), 0, "no-op emits nothing");

        // Never granted.
        vm.prank(guardian);
        vm.expectRevert(_err("NoConsentToRevoke()"));
        manager.revokeConsent(doctor, VACCINATION_SCHEDULE);

        // Expired but not revoked: revoke still records it.
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 1);
        vm.warp(_expiry(guardian, doctor, VACCINATION_SCHEDULE));
        vm.expectEmit(true, true, true, true, address(manager));
        emit ConsentRevoked(guardian, doctor, VACCINATION_SCHEDULE);
        _revoke(guardian, doctor, VACCINATION_SCHEDULE);
        (, revoked) = manager.getConsent(guardian, doctor, VACCINATION_SCHEDULE);
        assertTrue(revoked);
    }

    /// @notice [SOL-CM-10] A wrong (tampered) or zero observedHash is denied HashMismatch (7) in the one emitted event; the correct hash is allowed.
    /// @dev Why it matters: this is the integrity check: a modified or unreadable local file must never be released, and the denial must be logged.
    /// @custom:requirement Audit/integrity: a changed or missing local record is denied
    function testHashMismatchAndZeroObservedHashAreDenied() public {
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 30);

        vm.expectEmit(true, true, true, true, address(manager));
        emit AccessAttempt(guardian, doctor, VACCINATION_SCHEDULE, block.timestamp, false, ConsentManager.Reason.HashMismatch);
        (bool allowed, uint8 reason) = _request(doctor, guardian, VACCINATION_SCHEDULE, TAMPERED);
        assertFalse(allowed, "tampered copy");
        assertEq(reason, HASH_MISMATCH);

        vm.expectEmit(true, true, true, true, address(manager));
        emit AccessAttempt(guardian, doctor, VACCINATION_SCHEDULE, block.timestamp, false, ConsentManager.Reason.HashMismatch);
        (allowed, reason) = _request(doctor, guardian, VACCINATION_SCHEDULE, bytes32(0));
        assertFalse(allowed, "zero hash = unavailable local data");
        assertEq(reason, HASH_MISMATCH);

        // Another guardian's valid commitment is also a mismatch for this owner.
        (allowed, reason) = _request(doctor, guardian, VACCINATION_SCHEDULE, RECORD2);
        assertEq(reason, HASH_MISMATCH, "hash of a different record");

        (allowed, reason) = _request(doctor, guardian, VACCINATION_SCHEDULE, RECORD);
        assertTrue(allowed, "untouched record");
        assertEq(reason, ALLOWED);
    }

    /// @notice [SOL-CM-11] A valid request returns (true, Allowed) and emits the allowed event; every call is logged; no reward is minted and balances do not change.
    /// @dev Why it matters: access must be auditable every time and must not move tokens, otherwise reading data could be used to farm rewards.
    /// @custom:requirement Audit: every access attempt is logged and moves no tokens
    function testValidAccessEmitsAuditWithoutReward() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        uint256 guardianBefore = token.balanceOf(guardian);
        uint256 supplyBefore = token.totalSupply();

        vm.expectEmit(true, true, true, true, address(manager));
        emit AccessAttempt(guardian, school, MEASLES_STATUS, block.timestamp, true, ConsentManager.Reason.Allowed);
        (bool allowed, uint8 reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertTrue(allowed);
        assertEq(reason, ALLOWED);

        // Repeating the identical request logs a second event (Python's late-denial recheck relies on this).
        vm.warp(block.timestamp + 60);
        (allowed, reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertTrue(allowed);

        assertEq(token.balanceOf(guardian), guardianBefore, "guardian balance unchanged");
        assertEq(token.balanceOf(school), 0, "requester balance unchanged");
        assertEq(token.totalSupply(), supplyBefore, "no mint during access");
    }

    // ------------------------------------------------------------------ rewards

    /// @notice [SOL-CM-12] Grant, revoke and regrant: checkAccess is Allowed, getConsent shows revoked == false and a new expiry, and the tuple keeps exactly one lifetime reward.
    /// @dev Why it matters: toggling consent must not farm rewards, and a regrant must fully clear the revoked flag (the demo's final EXPIRED step depends on it).
    /// @custom:requirement Rewards: revoke and regrant gives no second reward
    function testRegrantDoesNotRepeatReward() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        _revoke(guardian, school, MEASLES_STATUS);
        vm.warp(block.timestamp + 1 hours);

        vm.recordLogs();
        _grant(guardian, school, MEASLES_STATUS, 1);
        Vm.Log[] memory logs = vm.getRecordedLogs();

        (uint256 expiresAt, bool revoked) = manager.getConsent(guardian, school, MEASLES_STATUS);
        assertFalse(revoked, "regrant clears revoked");
        assertEq(expiresAt, block.timestamp + 1 days, "regrant writes a new expiry");
        (bool allowed, uint8 reason) = _check(guardian, school, MEASLES_STATUS);
        assertTrue(allowed);
        assertEq(reason, ALLOWED);

        assertEq(_countLogs(logs, address(token), REWARD_MINTED_TOPIC), 0, "no RewardMinted on regrant");
        assertTrue(manager.hasReceivedReward(guardian, school, MEASLES_STATUS), "rewarded flag survives");
        assertEq(token.balanceOf(guardian), 1, "demo: school regrant 1 -> 1");
        assertEq(token.totalSupply(), 1);
    }

    /// @notice [SOL-CM-13] Grant, let it expire, regrant: allowed again, but no second reward.
    /// @dev Why it matters: expiry is the other way a tuple becomes re-grantable; it must follow the same one-lifetime-reward rule as revocation.
    /// @custom:requirement Rewards: expiry and regrant gives no second reward
    function testExpiryRegrantDoesNotRepeatReward() public {
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 1);
        vm.warp(_expiry(guardian, doctor, VACCINATION_SCHEDULE));
        (, uint8 reason) = _check(guardian, doctor, VACCINATION_SCHEDULE);
        assertEq(reason, EXPIRED);

        _grant(guardian, doctor, VACCINATION_SCHEDULE, 7);
        (bool allowed,) = _check(guardian, doctor, VACCINATION_SCHEDULE);
        assertTrue(allowed, "regranted after expiry");
        assertEq(token.balanceOf(guardian), 1, "still one reward");
        assertEq(token.totalSupply(), 1);
    }

    /// @notice [SOL-CM-14] A different requester or scope for the same owner, or a different owner, earns its own single reward (the key is the full tuple).
    /// @dev Why it matters: shows the reward key is (owner, requester, scope), matching the demo balances: school 0 -> 1, doctor 1 -> 2.
    /// @custom:requirement Rewards: one lifetime reward per (owner, requester, scope)
    function testDifferentRequesterOrScopeEarnsOwnReward() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        assertEq(token.balanceOf(guardian), 1, "demo: school grant 0 -> 1");
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 30);
        assertEq(token.balanceOf(guardian), 2, "demo: doctor grant 1 -> 2");
        _grant(guardian, school, VACCINATION_SCHEDULE, 30);
        assertEq(token.balanceOf(guardian), 3, "same requester, other scope");
        _grant(guardian2, school, MEASLES_STATUS, 30);
        assertEq(token.balanceOf(guardian2), 1, "other owner, same requester and scope");

        assertEq(token.balanceOf(guardian), 3, "guardian unaffected by guardian2");
        assertEq(token.balanceOf(school), 0, "requesters never rewarded");
        assertEq(token.balanceOf(doctor), 0, "requesters never rewarded");
        assertEq(token.totalSupply(), 4);
        assertFalse(manager.hasReceivedReward(guardian, doctor, MEASLES_STATUS), "untouched tuple not rewarded");
    }

    /// @notice [SOL-CM-15] With an unconfigured reward token, the grant reverts as a whole: no consent, no rewarded flag, access still NoConsent.
    /// @dev Why it matters: grant and reward are one atomic transaction; a failed mint must not leave a half-done grant that looks successful.
    /// @custom:requirement Rewards: grant and mint are atomic
    function testRewardFailureRollsBackGrant() public {
        ConsentRewardToken unconfigured = new ConsentRewardToken(); // setMinterOnce never called
        ConsentManager manager2 = new ConsentManager(address(registry), address(unconfigured));

        vm.prank(guardian);
        vm.expectRevert(ConsentRewardToken.NotMinter.selector);
        manager2.grantConsent(school, MEASLES_STATUS, 30);

        (uint256 expiresAt, bool revoked) = manager2.getConsent(guardian, school, MEASLES_STATUS);
        assertEq(expiresAt, 0, "no consent stored");
        assertFalse(revoked);
        assertFalse(manager2.hasReceivedReward(guardian, school, MEASLES_STATUS), "no rewarded flag");
        (bool allowed, ConsentManager.Reason reason) = manager2.checkAccess(guardian, school, MEASLES_STATUS);
        assertFalse(allowed);
        assertEq(uint8(reason), NO_CONSENT);
        assertEq(unconfigured.totalSupply(), 0);
    }

    // ------------------------------------------------------------------ frozen interface

    /// @notice [SOL-CM-16] Denial precedence: several failing conditions at once always yield the agreed reason, in the event and in checkAccess.
    /// @dev Why it matters: Python's fake chain and messages assume this exact order (UnsupportedScope, NotRegistered, MissingEvidence, NoConsent, Revoked, Expired, then hash); this is the only test that pins it on the real contract.
    /// @custom:requirement Interface: denial precedence matches Python
    function testDenialPrecedence() public {
        bool allowed;
        uint8 reason;

        // (a) revoked AND past expiresAt -> Revoked, not Expired.
        _grant(guardian, school, MEASLES_STATUS, 1);
        _revoke(guardian, school, MEASLES_STATUS);
        vm.warp(_expiry(guardian, school, MEASLES_STATUS) + 1 days);
        (, reason) = _check(guardian, school, MEASLES_STATUS);
        assertEq(reason, REVOKED, "(a) checkAccess");
        (, reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertEq(reason, REVOKED, "(a) requestAccess");

        // (b) zero observedHash with no grant -> NoConsent, not HashMismatch.
        (, reason) = _request(doctor, guardian, VACCINATION_SCHEDULE, bytes32(0));
        assertEq(reason, NO_CONSENT, "(b) requestAccess");

        // (c) unsupported scope from an unregistered requester -> UnsupportedScope.
        (, reason) = _check(guardian, stranger, UNSUPPORTED);
        assertEq(reason, UNSUPPORTED_SCOPE, "(c) checkAccess");
        (, reason) = _request(stranger, guardian, UNSUPPORTED, bytes32(0));
        assertEq(reason, UNSUPPORTED_SCOPE, "(c) requestAccess");

        // (d) unregistered requester against an owner with no evidence -> NotRegistered.
        (, reason) = _check(school, stranger, MEASLES_STATUS);
        assertEq(reason, NOT_REGISTERED, "(d) checkAccess");
        (, reason) = _request(stranger, school, MEASLES_STATUS, bytes32(0));
        assertEq(reason, NOT_REGISTERED, "(d) requestAccess");

        // (e) owner with no evidence and no grant -> MissingEvidence, not NoConsent.
        (, reason) = _check(school, doctor, MEASLES_STATUS);
        assertEq(reason, MISSING_EVIDENCE, "(e) checkAccess");
        (, reason) = _request(doctor, school, MEASLES_STATUS, TAMPERED);
        assertEq(reason, MISSING_EVIDENCE, "(e) requestAccess");

        // (f) valid consent + wrong observedHash: requestAccess HashMismatch, checkAccess Allowed (it has no hash step).
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 30);
        (allowed, reason) = _check(guardian, doctor, VACCINATION_SCHEDULE);
        assertTrue(allowed, "(f) checkAccess allowed");
        assertEq(reason, ALLOWED, "(f) checkAccess");
        (allowed, reason) = _request(doctor, guardian, VACCINATION_SCHEDULE, TAMPERED);
        assertFalse(allowed, "(f) requestAccess denied");
        assertEq(reason, HASH_MISMATCH, "(f) requestAccess");
    }

    /// @notice [SOL-CM-17] The Reason enum values are frozen at 0-7 in the order app/models.py uses.
    /// @dev Why it matters: Python names reasons from plain integers; a reordered enum would still pass name-based checks but print the wrong reason in the demo.
    /// @custom:requirement Interface: reason codes are frozen at 0-7
    function testReasonCodesAreFrozen() public pure {
        assertEq(uint8(ConsentManager.Reason.Allowed), 0);
        assertEq(uint8(ConsentManager.Reason.NotRegistered), 1);
        assertEq(uint8(ConsentManager.Reason.UnsupportedScope), 2);
        assertEq(uint8(ConsentManager.Reason.MissingEvidence), 3);
        assertEq(uint8(ConsentManager.Reason.NoConsent), 4);
        assertEq(uint8(ConsentManager.Reason.Expired), 5);
        assertEq(uint8(ConsentManager.Reason.Revoked), 6);
        assertEq(uint8(ConsentManager.Reason.HashMismatch), 7);
    }

    /// @notice [SOL-CM-18] The constructor rejects zero and non-contract dependency addresses.
    /// @dev Why it matters: a manager wired to a wallet instead of the real registry or token would accept or deny access on garbage data.
    /// @custom:requirement Deployment: the manager is wired to real contracts
    function testConstructorRejectsInvalidDependencies() public {
        vm.expectRevert(_err("ZeroAddress()"));
        new ConsentManager(address(0), address(token));

        vm.expectRevert(_err("ZeroAddress()"));
        new ConsentManager(address(registry), address(0));

        vm.expectRevert(_err("NotAContract()"));
        new ConsentManager(guardian, address(token));

        vm.expectRevert(_err("NotAContract()"));
        new ConsentManager(address(registry), guardian);
    }

    /// @notice [SOL-CM-19] The full demo storyline on-chain: deny, grant/reward, allow, revoke deny, tamper deny, regrant 1 day (no reward), exact expiry deny.
    /// @dev Why it matters: shows the unit-tested rules compose into the scripted demonstration in the same order integration/demo_workflow.py runs it.
    /// @custom:requirement Full workflow: the demo storyline works on-chain
    function testDemoStorylineEndToEnd() public {
        (bool allowed, uint8 reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertEq(reason, NO_CONSENT, "1. school before consent");

        _grant(guardian, school, MEASLES_STATUS, 30);
        assertEq(token.balanceOf(guardian), 1, "2. first reward");
        (allowed,) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertTrue(allowed, "3. school status allowed");

        _grant(guardian, doctor, VACCINATION_SCHEDULE, 30);
        assertEq(token.balanceOf(guardian), 2, "4. doctor reward");
        (allowed,) = _request(doctor, guardian, VACCINATION_SCHEDULE, RECORD);
        assertTrue(allowed, "4. doctor schedule allowed");

        (, reason) = _request(doctor, guardian, VACCINATION_SCHEDULE, TAMPERED);
        assertEq(reason, HASH_MISMATCH, "5. tampered copy");

        _revoke(guardian, school, MEASLES_STATUS);
        (, reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertEq(reason, REVOKED, "6. revoked");

        _grant(guardian, school, MEASLES_STATUS, 1);
        assertEq(token.balanceOf(guardian), 2, "7. regrant no reward");
        (allowed,) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertTrue(allowed, "7. regranted");
        vm.warp(_expiry(guardian, school, MEASLES_STATUS));
        (, reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertEq(reason, EXPIRED, "7. expired");
    }
}
