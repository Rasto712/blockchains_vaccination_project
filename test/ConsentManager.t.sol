// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {Vm} from "forge-std/Vm.sol";
import {IdentityRegistry} from "../contracts/IdentityRegistry.sol";
import {ConsentManager} from "../contracts/ConsentManager.sol";
import {ConsentRewardToken} from "../contracts/ConsentRewardToken.sol";

/// Tests for ConsentManager with a real IdentityRegistry and ConsentRewardToken.
/// Test IDs (SOL-CM-xx) are used by the results export.
/// Denials are checked through the AccessAttempt event, and reason codes as plain numbers 0-7.
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
    bytes32 internal constant CONSENT_GRANTED_TOPIC = keccak256("ConsentGranted(address,address,uint8,uint256)");
    bytes32 internal constant CONSENT_REVOKED_TOPIC = keccak256("ConsentRevoked(address,address,uint8)");
    bytes32 internal constant REWARD_MINTED_TOPIC = keccak256("RewardMinted(address,uint256)");

    // Deploys in the real order (registry, token, manager, setMinterOnce), then registers the demo actors.
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

        // Realistic timestamp.
        vm.warp(1_760_000_000);
    }

    // ------------------------------------------------------------------ helpers

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

    // Calls requestAccess and returns the one AccessAttempt event it logged.
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
            if (
                logs[i].emitter == address(manager) && logs[i].topics.length > 0
                    && logs[i].topics[0] == ACCESS_ATTEMPT_TOPIC
            ) {
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
            // No token events during access.
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

    // Returns the single storage slot written; fails if none or more than one.
    function _onlyWrittenSlot(string memory action) internal view returns (bytes32 slot) {
        (, bytes32[] memory writes) = vm.accesses(address(manager));
        assertGt(writes.length, 0, string.concat(action, " writes storage"));
        slot = writes[0];
        for (uint256 i = 1; i < writes.length; i++) {
            assertEq(writes[i], slot, string.concat(action, " writes one slot only"));
        }
    }

    // ------------------------------------------------------------------ grants

    /// @notice [SOL-CM-01] Grant consent: expiry is set, ConsentGranted is emitted and the guardian gets a reward.
    /// @dev Why it matters: The reward goes to the guardian, not the requester.
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

    /// @notice [SOL-CM-02] Durations 0, 366 and 65535 are rejected, 1, 194, 195 and 365 are accepted (195+ catches the uint24 overflow).
    /// @dev Why it matters: Consent is limited to one year, and a missing cast would break 195+ days.
    /// @custom:requirement Consent: duration is 1-365 whole days
    function testDurationBounds() public {
        uint16[3] memory bad = [uint16(0), 366, type(uint16).max];
        for (uint256 i = 0; i < bad.length; i++) {
            vm.prank(guardian);
            vm.expectRevert(ConsentManager.InvalidDuration.selector);
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

    /// @notice [SOL-CM-03] An active grant can't be granted again or rewarded again.
    /// @dev Why it matters: Otherwise a guardian could farm rewards.
    /// @custom:requirement Consent: an active duplicate grant is rejected
    function testRejectActiveDuplicateGrant() public {
        _grant(guardian, school, MEASLES_STATUS, 10);
        uint256 expiresAt = _expiry(guardian, school, MEASLES_STATUS);

        vm.prank(guardian);
        vm.expectRevert(ConsentManager.ConsentStillActive.selector);
        manager.grantConsent(school, MEASLES_STATUS, 365);

        vm.warp(expiresAt - 1);
        vm.prank(guardian);
        vm.expectRevert(ConsentManager.ConsentStillActive.selector);
        manager.grantConsent(school, MEASLES_STATUS, 1);

        assertEq(_expiry(guardian, school, MEASLES_STATUS), expiresAt, "expiry unchanged");
        assertEq(token.balanceOf(guardian), 1, "still one reward");
        assertEq(token.totalSupply(), 1);
    }

    /// @notice [SOL-CM-04] A caller can only grant or revoke its own consent.
    /// @dev Why it matters: Consent belongs to the guardian.
    /// @custom:requirement Consent: only the owner can change its own consent
    function testCallerCannotChangeAnotherOwnersConsent() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        uint256 expiresAt = _expiry(guardian, school, MEASLES_STATUS);

        // Others only touch their own (empty) grant.
        address[3] memory others = [guardian2, school, stranger];
        for (uint256 i = 0; i < others.length; i++) {
            vm.prank(others[i]);
            vm.expectRevert(ConsentManager.NoConsentToRevoke.selector);
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

    /// @notice [SOL-CM-05] A grant needs both parties registered and a supported scope, nothing more (self-grant is allowed).
    /// @dev Why it matters: Extra checks would make the contract differ from the Python fake chain.
    /// @custom:requirement Consent: a grant needs registered parties and a supported scope
    function testGrantRequiresRegisteredPartiesAndSupportedScope() public {
        vm.prank(guardian);
        vm.expectRevert(ConsentManager.NotRegistered.selector);
        manager.grantConsent(stranger, MEASLES_STATUS, 30);

        vm.prank(stranger);
        vm.expectRevert(ConsentManager.NotRegistered.selector);
        manager.grantConsent(school, MEASLES_STATUS, 30);

        vm.prank(guardian);
        vm.expectRevert(ConsentManager.UnsupportedScope.selector);
        manager.grantConsent(school, UNSUPPORTED, 30);

        vm.prank(guardian);
        vm.expectRevert(ConsentManager.UnsupportedScope.selector);
        manager.grantConsent(school, 0, 30);

        vm.prank(guardian);
        vm.expectRevert(ConsentManager.UnsupportedScope.selector);
        manager.revokeConsent(school, UNSUPPORTED);

        assertEq(token.totalSupply(), 0, "no rewards from rejected grants");

        // A self-grant is accepted.
        _grant(guardian, guardian, MEASLES_STATUS, 30);
        (bool allowed, uint8 reason) = _check(guardian, guardian, MEASLES_STATUS);
        assertTrue(allowed, "self-grant accepted");
        assertEq(reason, ALLOWED);
    }

    // ------------------------------------------------------------------ access denials

    /// @notice [SOL-CM-06] Wrong requester, wrong scope and unsupported scopes are denied and logged, without reverting.
    /// @dev Why it matters: Denied attempts must be in the audit log.
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

        // Scope 0 lies below the supported range, so a check written as `scope > 2` would miss it.
        (allowed, reason) = _request(school, guardian, 0, RECORD);
        assertFalse(allowed);
        assertEq(reason, UNSUPPORTED_SCOPE, "scope 0 is a logged denial, not NoConsent");
        (allowed, reason) = _check(guardian, school, 0);
        assertFalse(allowed);
        assertEq(reason, UNSUPPORTED_SCOPE, "checkAccess agrees: scope 0");
    }

    /// @notice [SOL-CM-07] Missing prerequisites (unregistered parties, no evidence) are denied and logged with no token movement.
    /// @dev Why it matters: Refusals must still be auditable.
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
        (allowed, reason) = _check(stranger, school, MEASLES_STATUS);
        assertFalse(allowed);
        assertEq(reason, NOT_REGISTERED, "checkAccess agrees: unregistered owner");

        // The school is registered but has no attested vaccination record.
        (allowed, reason) = _request(doctor, school, MEASLES_STATUS, RECORD);
        assertFalse(allowed);
        assertEq(reason, MISSING_EVIDENCE, "owner without evidence");

        assertEq(token.totalSupply(), supplyBefore, "no mint during access");
        assertEq(token.balanceOf(stranger), 0);
    }

    /// @notice [SOL-CM-08] At expiresAt - 1 access is allowed, at expiresAt it is denied as Expired.
    /// @dev Why it matters: Expiry is exclusive, an off-by-one would give extra access.
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

    /// @notice [SOL-CM-09] Revoke blocks access, revoking twice does nothing, never-granted reverts, expired can still be revoked.
    /// @dev Why it matters: The guardian can withdraw consent any time and repeating it is safe.
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
        vm.expectRevert(ConsentManager.NoConsentToRevoke.selector);
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

    /// @notice [SOL-CM-10] A wrong or zero observedHash is denied as HashMismatch, the correct one is allowed.
    /// @dev Why it matters: A changed or unreadable file must not be released.
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

    /// @notice [SOL-CM-11] A valid request returns (true, Allowed), is logged each time and mints nothing.
    /// @dev Why it matters: Access is audited and must not move tokens.
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

    /// @notice [SOL-CM-12] Grant, revoke, regrant: the regrant works but the tuple keeps only one reward.
    /// @dev Why it matters: Toggling consent must not farm rewards.
    /// @custom:requirement Rewards: revoke and regrant gives no second reward
    function testRegrantDoesNotRepeatReward() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        _revoke(guardian, school, MEASLES_STATUS);
        vm.warp(block.timestamp + 1 hours);

        vm.expectEmit(true, true, true, true, address(manager));
        emit ConsentGranted(guardian, school, MEASLES_STATUS, block.timestamp + 1 days);
        vm.recordLogs();
        _grant(guardian, school, MEASLES_STATUS, 1);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        assertEq(_countLogs(logs, address(manager), CONSENT_GRANTED_TOPIC), 1, "every grant emits ConsentGranted once");

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

    /// @notice [SOL-CM-13] Grant, let it expire, regrant: access works again but no second reward.
    /// @dev Why it matters: Expiry follows the same one-reward rule as revocation.
    /// @custom:requirement Rewards: expiry and regrant gives no second reward
    function testExpiryRegrantDoesNotRepeatReward() public {
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 1);
        vm.warp(_expiry(guardian, doctor, VACCINATION_SCHEDULE));
        (, uint8 reason) = _check(guardian, doctor, VACCINATION_SCHEDULE);
        assertEq(reason, EXPIRED);

        vm.expectEmit(true, true, true, true, address(manager));
        emit ConsentGranted(guardian, doctor, VACCINATION_SCHEDULE, block.timestamp + 7 days);
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 7);
        assertEq(_expiry(guardian, doctor, VACCINATION_SCHEDULE), block.timestamp + 7 days, "new expiry stored");
        (bool allowed,) = _check(guardian, doctor, VACCINATION_SCHEDULE);
        assertTrue(allowed, "regranted after expiry");
        assertEq(token.balanceOf(guardian), 1, "still one reward");
        assertEq(token.totalSupply(), 1);
    }

    /// @notice [SOL-CM-14] A different requester, scope or owner earns its own reward.
    /// @dev Why it matters: The reward key is (owner, requester, scope).
    /// @custom:requirement Rewards: one lifetime reward per (owner, requester, scope)
    function testDifferentRequesterOrScopeEarnsOwnReward() public {
        _grant(guardian, school, MEASLES_STATUS, 30);
        assertEq(token.balanceOf(guardian), 1, "demo: school grant 0 -> 1");
        assertFalse(manager.hasReceivedReward(guardian, doctor, VACCINATION_SCHEDULE), "scope 2 before its grant");
        _grant(guardian, doctor, VACCINATION_SCHEDULE, 30);
        assertEq(token.balanceOf(guardian), 2, "demo: doctor grant 1 -> 2");
        assertTrue(manager.hasReceivedReward(guardian, doctor, VACCINATION_SCHEDULE), "scope 2 after its grant");
        assertFalse(manager.hasReceivedReward(guardian, school, VACCINATION_SCHEDULE), "same requester, scope 2 not granted yet");
        _grant(guardian, school, VACCINATION_SCHEDULE, 30);
        assertEq(token.balanceOf(guardian), 3, "same requester, other scope");
        assertTrue(manager.hasReceivedReward(guardian, school, VACCINATION_SCHEDULE));
        _grant(guardian2, school, MEASLES_STATUS, 30);
        assertEq(token.balanceOf(guardian2), 1, "other owner, same requester and scope");

        assertEq(token.balanceOf(guardian), 3, "guardian unaffected by guardian2");
        assertEq(token.balanceOf(school), 0, "requesters never rewarded");
        assertEq(token.balanceOf(doctor), 0, "requesters never rewarded");
        assertEq(token.totalSupply(), 4);
        assertFalse(manager.hasReceivedReward(guardian, doctor, MEASLES_STATUS), "untouched tuple not rewarded");
        assertFalse(manager.hasReceivedReward(guardian2, doctor, VACCINATION_SCHEDULE), "untouched scope-2 tuple not rewarded");
    }

    /// @notice [SOL-CM-15] If the token has no minter set, the whole grant reverts with NotMinter.
    /// @dev Why it matters: Grant and reward must succeed or fail together.
    /// @custom:requirement Rewards: grant and mint are atomic
    function testRewardFailureRollsBackGrant() public {
        ConsentRewardToken unconfigured = new ConsentRewardToken(); // setMinterOnce never called
        ConsentManager manager2 = new ConsentManager(address(registry), address(unconfigured));

        vm.prank(guardian);
        vm.expectRevert(ConsentRewardToken.NotMinter.selector);
        manager2.grantConsent(school, MEASLES_STATUS, 30);

        // These just show the revert undid everything.
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

    /// @notice [SOL-CM-16] When several things fail at once, the reason follows the fixed order, and a bad hash never replaces an earlier denial.
    /// @dev Why it matters: Python's fake chain assumes this order.
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

        // (g) revoked (not expired) + wrong or zero observedHash -> Revoked, not HashMismatch.
        _grant(guardian, doctor, MEASLES_STATUS, 30);
        _revoke(guardian, doctor, MEASLES_STATUS);
        (, reason) = _request(doctor, guardian, MEASLES_STATUS, TAMPERED);
        assertEq(reason, REVOKED, "(g) revoked beats a tampered hash");
        (, reason) = _request(doctor, guardian, MEASLES_STATUS, bytes32(0));
        assertEq(reason, REVOKED, "(g) revoked beats a zero hash");

        // (h) expired (not revoked) + wrong or zero observedHash -> Expired, not HashMismatch.
        _grant(guardian, school, VACCINATION_SCHEDULE, 1);
        vm.warp(_expiry(guardian, school, VACCINATION_SCHEDULE));
        (, reason) = _request(school, guardian, VACCINATION_SCHEDULE, TAMPERED);
        assertEq(reason, EXPIRED, "(h) expired beats a tampered hash");
        (, reason) = _request(school, guardian, VACCINATION_SCHEDULE, bytes32(0));
        assertEq(reason, EXPIRED, "(h) expired beats a zero hash");
    }

    /// @notice [SOL-CM-17] The Reason enum values stay 0-7 in the order app/models.py uses.
    /// @dev Why it matters: Python names reasons from numbers, so a reorder would print wrong reasons.
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

    /// @notice [SOL-CM-18] The constructor rejects zero and non-contract addresses (ZeroAddress first).
    /// @dev Why it matters: The manager must point at the real contracts.
    /// @custom:requirement Deployment: the manager is wired to real contracts
    function testConstructorRejectsInvalidDependencies() public {
        vm.expectRevert(ConsentManager.ZeroAddress.selector);
        new ConsentManager(address(0), address(token));

        vm.expectRevert(ConsentManager.ZeroAddress.selector);
        new ConsentManager(address(registry), address(0));

        vm.expectRevert(ConsentManager.NotAContract.selector);
        new ConsentManager(guardian, address(token));

        vm.expectRevert(ConsentManager.NotAContract.selector);
        new ConsentManager(address(registry), guardian);

        // A wallet AND a zero address: ZeroAddress comes first, whichever argument is zero.
        vm.expectRevert(ConsentManager.ZeroAddress.selector);
        new ConsentManager(guardian, address(0));
        vm.expectRevert(ConsentManager.ZeroAddress.selector);
        new ConsentManager(address(0), guardian);
    }

    /// @notice [SOL-CM-19] The full demo storyline from docs/DEMO.md run on-chain.
    /// @dev Why it matters: Shows the rules work together in the demo.
    /// @custom:requirement Full workflow: the demo storyline works on-chain
    function testDemoStorylineEndToEnd() public {
        (bool allowed, uint8 reason) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertEq(reason, NO_CONSENT, "2. school before consent");

        _grant(guardian, school, MEASLES_STATUS, 30);
        assertEq(token.balanceOf(guardian), 1, "3. first reward");
        (allowed,) = _request(school, guardian, MEASLES_STATUS, RECORD);
        assertTrue(allowed, "3. school status allowed");

        _grant(guardian, doctor, VACCINATION_SCHEDULE, 30);
        assertEq(token.balanceOf(guardian), 2, "4. doctor reward");
        (allowed,) = _request(doctor, guardian, VACCINATION_SCHEDULE, RECORD);
        assertTrue(allowed, "4. doctor schedule allowed");

        (, reason) = _request(doctor, guardian, VACCINATION_SCHEDULE, TAMPERED);
        assertEq(reason, HASH_MISMATCH, "5. tampered copy, doctor grant still active");
        (allowed,) = _request(doctor, guardian, VACCINATION_SCHEDULE, RECORD);
        assertTrue(allowed, "5. the original still verifies");

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

    /// @notice [SOL-CM-20] Revert order when several things fail at once, for grantConsent and revokeConsent.
    /// @dev Why it matters: Python's fake chain raises errors in this order.
    /// @custom:requirement Interface: revert order matches the Python error messages
    function testGrantAndRevokeRevertOrder() public {
        // Unregistered owner and requester + unsupported scope + 0 days -> UnsupportedScope.
        vm.prank(stranger);
        vm.expectRevert(ConsentManager.UnsupportedScope.selector);
        manager.grantConsent(stranger, UNSUPPORTED, 0);

        // Unregistered owner and requester + 0 days -> InvalidDuration.
        vm.prank(stranger);
        vm.expectRevert(ConsentManager.InvalidDuration.selector);
        manager.grantConsent(stranger, MEASLES_STATUS, 0);

        // Unregistered requester + 366 days -> InvalidDuration.
        vm.prank(guardian);
        vm.expectRevert(ConsentManager.InvalidDuration.selector);
        manager.grantConsent(stranger, MEASLES_STATUS, 366);

        // Active grant + 0 days -> InvalidDuration, not ConsentStillActive. (NotRegistered and
        // ConsentStillActive cannot hold together: an active grant needs two registered parties.)
        _grant(guardian, school, MEASLES_STATUS, 30);
        vm.prank(guardian);
        vm.expectRevert(ConsentManager.InvalidDuration.selector);
        manager.grantConsent(school, MEASLES_STATUS, 0);

        // Revoke: unsupported scope on a never-granted tuple -> UnsupportedScope.
        vm.prank(stranger);
        vm.expectRevert(ConsentManager.UnsupportedScope.selector);
        manager.revokeConsent(stranger, UNSUPPORTED);

        assertEq(token.totalSupply(), 1, "only the one valid grant was rewarded");
    }

    /// @notice [SOL-CM-21] expiresAt, revoked and rewarded share one storage slot, so grant, revoke and regrant each write only that slot.
    /// @dev Why it matters: Writing a new slot from zero costs about 20,000 more gas.
    /// @custom:requirement Gas: a revoke or a first grant writes no new storage slot
    function testConsentStateUsesOneStorageSlot() public {
        vm.record();
        _grant(guardian, school, MEASLES_STATUS, 30);
        bytes32 slot = _onlyWrittenSlot("first grant");
        uint256 firstExpiry = block.timestamp + 30 days;
        assertTrue(manager.hasReceivedReward(guardian, school, MEASLES_STATUS), "rewarded set by the same write");

        vm.record();
        _revoke(guardian, school, MEASLES_STATUS);
        assertEq(_onlyWrittenSlot("revoke"), slot, "revoke writes the grant's slot");
        (uint256 expiresAt, bool revoked) = manager.getConsent(guardian, school, MEASLES_STATUS);
        assertTrue(revoked);
        assertEq(expiresAt, firstExpiry, "revoke keeps expiresAt");
        assertTrue(manager.hasReceivedReward(guardian, school, MEASLES_STATUS), "revoke keeps rewarded");

        vm.record();
        _grant(guardian, school, MEASLES_STATUS, 365);
        assertEq(_onlyWrittenSlot("regrant"), slot, "regrant writes the same slot");
        (expiresAt, revoked) = manager.getConsent(guardian, school, MEASLES_STATUS);
        assertEq(expiresAt, block.timestamp + 365 days, "full expiry read back");
        assertFalse(revoked, "regrant clears revoked");
        assertTrue(manager.hasReceivedReward(guardian, school, MEASLES_STATUS), "regrant keeps rewarded");
        assertEq(token.balanceOf(guardian), 1, "still one reward");

        // Another tuple gets a slot of its own.
        vm.record();
        _grant(guardian, school, VACCINATION_SCHEDULE, 30);
        assertTrue(_onlyWrittenSlot("other tuple") != slot, "one slot per tuple");
    }

    /// @notice [SOL-CM-22] Known limitation: the tuple is the only reward limit, so 3 wallets can mint 2 * 3 * 3 = 18 units, and the units open nothing.
    /// @dev Why it matters: Shows the real bound on rewards. Access never reads a balance (SOL-RT-05).
    /// @custom:requirement Rewards: stated limitation, rewards are limited per tuple only
    function testRewardLimitIsPerTupleOnly() public {
        address[3] memory wallets = [makeAddr("wallet0"), makeAddr("wallet1"), makeAddr("wallet2")];
        string[3] memory labels = ["wallet0", "wallet1", "wallet2"];
        for (uint256 i = 0; i < wallets.length; i++) {
            _register(wallets[i], labels[i]);
        }

        for (uint256 i = 0; i < wallets.length; i++) {
            for (uint256 j = 0; j < wallets.length; j++) {
                for (uint8 scope = MEASLES_STATUS; scope <= VACCINATION_SCHEDULE; scope++) {
                    _grant(wallets[i], wallets[j], scope, 1);
                }
            }
        }
        for (uint256 i = 0; i < wallets.length; i++) {
            (,, bytes32 vaccinationHash) = registry.getUserInfo(wallets[i]);
            assertEq(vaccinationHash, bytes32(0), "no attested record");
            assertEq(token.balanceOf(wallets[i]), 6, "one unit per requester (itself included) and scope");
        }
        assertEq(token.totalSupply(), 18, "2 * k * k units from k = 3 wallets");

        // The tuple limit itself still holds: regrants after expiry mint nothing.
        vm.warp(block.timestamp + 1 days);
        _grant(wallets[0], wallets[1], MEASLES_STATUS, 1);
        _grant(wallets[2], wallets[2], VACCINATION_SCHEDULE, 1);
        assertEq(token.totalSupply(), 18, "no reward for a regrant");

        // The units open nothing: these grants cover no record, and a balance is not consent.
        (bool allowed, uint8 reason) = _request(wallets[1], wallets[0], MEASLES_STATUS, bytes32(0));
        assertFalse(allowed);
        assertEq(reason, MISSING_EVIDENCE, "granted, but the owner has no record");
        (allowed, reason) = _request(wallets[0], guardian, MEASLES_STATUS, RECORD);
        assertFalse(allowed);
        assertEq(reason, NO_CONSENT, "six units do not open the guardian's record");
    }
}
