// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ConsentRewardToken} from "../contracts/ConsentRewardToken.sol";
import {IdentityRegistry} from "../contracts/IdentityRegistry.sol";
import {ConsentManager} from "../contracts/ConsentManager.sol";

// Dummy contract so the code-length check in setMinterOnce can be tested.
contract DummyContract {}

/// Tests for ConsentRewardToken. Test IDs (SOL-RT-xx) are used by the results export.
/// The token is deployed from a separate deployer wallet so deployer and minter are different.
contract ConsentRewardTokenTest is Test {
    ConsentRewardToken internal token;

    address internal deployer = makeAddr("deployer");
    address internal guardian = makeAddr("guardian");
    address internal school = makeAddr("school");
    address internal attacker = makeAddr("attacker");

    event MinterConfigured(address indexed minter);
    event RewardMinted(address indexed recipient, uint256 amount);

    // Fresh token before every test.
    function setUp() public {
        vm.prank(deployer);
        token = new ConsentRewardToken();
    }

    // Makes this test contract the minter (instead of ConsentManager).
    function _configureSelfAsMinter() internal {
        vm.prank(deployer);
        token.setMinterOnce(address(this));
    }

    /// @notice [SOL-RT-01] Only the deployer can set the minter, once, and it must be a contract (errors checked in order).
    /// @dev Why it matters: Whoever is the minter controls all rewards.
    /// @custom:requirement Rewards: the minter is set once, by the deployer, to a contract
    function testOnlyDeployerCanConfigureMinterOnce() public {
        DummyContract manager = new DummyContract();
        assertEq(token.minter(), address(0), "no minter before setup");

        vm.prank(attacker);
        vm.expectRevert(ConsentRewardToken.NotDeployer.selector);
        token.setMinterOnce(address(manager));

        // tx.origin is the deployer but msg.sender is not.
        vm.prank(attacker, deployer);
        vm.expectRevert(ConsentRewardToken.NotDeployer.selector);
        token.setMinterOnce(address(manager));

        // Wrong caller AND zero address -> NotDeployer comes first.
        vm.prank(attacker);
        vm.expectRevert(ConsentRewardToken.NotDeployer.selector);
        token.setMinterOnce(address(0));

        vm.prank(deployer);
        vm.expectRevert(ConsentRewardToken.ZeroAddress.selector);
        token.setMinterOnce(address(0));

        vm.prank(deployer);
        vm.expectRevert(ConsentRewardToken.NotAContract.selector);
        token.setMinterOnce(guardian); // a wallet, not a contract

        vm.expectEmit(true, false, false, true, address(token));
        emit MinterConfigured(address(manager));
        vm.prank(deployer);
        token.setMinterOnce(address(manager));
        assertEq(token.minter(), address(manager), "minter configured");

        // Second call: rejected even with a different, valid contract.
        DummyContract other = new DummyContract();
        vm.prank(deployer);
        vm.expectRevert(ConsentRewardToken.MinterAlreadySet.selector);
        token.setMinterOnce(address(other));

        // After setup: a wrong caller still sees NotDeployer first; the deployer sees MinterAlreadySet
        // before any address check (zero address, wallet).
        vm.prank(attacker);
        vm.expectRevert(ConsentRewardToken.NotDeployer.selector);
        token.setMinterOnce(address(other));
        vm.prank(deployer);
        vm.expectRevert(ConsentRewardToken.MinterAlreadySet.selector);
        token.setMinterOnce(address(0));
        vm.prank(deployer);
        vm.expectRevert(ConsentRewardToken.MinterAlreadySet.selector);
        token.setMinterOnce(guardian);
        assertEq(token.minter(), address(manager), "minter unchanged");
    }

    /// @notice [SOL-RT-02] Nobody can mint before setup, and afterwards only the minter can.
    /// @dev Why it matters: Only ConsentManager should be able to mint.
    /// @custom:requirement Rewards: only the configured ConsentManager can mint
    function testOnlyConfiguredManagerCanMint() public {
        // Before configuration the minter is address(0), so every caller fails.
        vm.prank(deployer);
        vm.expectRevert(ConsentRewardToken.NotMinter.selector);
        token.mintReward(guardian);
        vm.expectRevert(ConsentRewardToken.NotMinter.selector);
        token.mintReward(guardian);

        _configureSelfAsMinter();

        address[4] memory notMinter = [guardian, school, attacker, deployer];
        for (uint256 i = 0; i < notMinter.length; i++) {
            vm.prank(notMinter[i]);
            vm.expectRevert(ConsentRewardToken.NotMinter.selector);
            token.mintReward(notMinter[i]);
        }
        assertEq(token.totalSupply(), 0, "nothing minted by unauthorized callers");

        token.mintReward(guardian); // msg.sender == configured minter
        assertEq(token.balanceOf(guardian), 1);
    }

    /// @notice [SOL-RT-03] Each mint adds exactly one unit to the balance and total supply and emits RewardMinted.
    /// @dev Why it matters: One unit per call, so no one can inflate a reward.
    /// @custom:requirement Rewards: exactly one unit per mint
    function testMintAddsOneUnitAndEmitsEvent() public {
        _configureSelfAsMinter();

        vm.expectEmit(true, false, false, true, address(token));
        emit RewardMinted(guardian, 1);
        token.mintReward(guardian);
        assertEq(token.balanceOf(guardian), 1);
        assertEq(token.totalSupply(), 1);

        token.mintReward(guardian);
        token.mintReward(school);
        assertEq(token.balanceOf(guardian), 2, "token itself does not deduplicate; ConsentManager does");
        assertEq(token.balanceOf(school), 1);
        assertEq(token.balanceOf(attacker), 0);
        assertEq(token.totalSupply(), 3, "supply = sum of balances");
    }

    /// @notice [SOL-RT-04] Minting to the zero address is rejected (a non-minter sees NotMinter first).
    /// @dev Why it matters: Rewards sent to address(0) would belong to nobody.
    /// @custom:requirement Rewards: no mint to the zero address
    function testRejectZeroRecipient() public {
        _configureSelfAsMinter();
        vm.expectRevert(ConsentRewardToken.ZeroAddress.selector);
        token.mintReward(address(0));

        vm.prank(attacker);
        vm.expectRevert(ConsentRewardToken.NotMinter.selector);
        token.mintReward(address(0));
        assertEq(token.totalSupply(), 0);
        assertEq(token.balanceOf(address(0)), 0);
    }

    /// @notice [SOL-RT-05] A reward balance never gives access, and it stays after revocation.
    /// @dev Why it matters: Consent controls access, tokens are only rewards.
    /// @custom:requirement Rewards: a token balance never authorizes access
    function testBalanceDoesNotAuthorizeAccess() public {
        address clinic = makeAddr("clinic");
        address doctor = makeAddr("doctor");
        bytes32 record = keccak256("VACCINATION:v1 child-demo-001 (synthetic)");

        IdentityRegistry registry = new IdentityRegistry(clinic);
        vm.prank(deployer);
        ConsentRewardToken rewards = new ConsentRewardToken();
        ConsentManager manager = new ConsentManager(address(registry), address(rewards));
        vm.prank(deployer);
        rewards.setMinterOnce(address(manager));

        vm.prank(guardian);
        registry.registerUser(keccak256("IDENTITY:v1 guardian (synthetic)"));
        vm.prank(school);
        registry.registerUser(keccak256("IDENTITY:v1 school (synthetic)"));
        vm.prank(doctor);
        registry.registerUser(keccak256("IDENTITY:v1 doctor (synthetic)"));
        vm.prank(clinic);
        registry.registerVaccination(guardian, record);

        // The doctor earns a reward by granting consent on its own (empty) record, so it holds a balance.
        vm.prank(doctor);
        manager.grantConsent(school, 1, 30);
        assertEq(rewards.balanceOf(doctor), 1, "doctor holds a reward");

        // Holding a reward does not let the doctor read the guardian's record.
        vm.prank(doctor);
        (bool allowed, ConsentManager.Reason reason) = manager.requestAccess(guardian, 2, record);
        assertFalse(allowed, "balance is not consent");
        assertEq(uint8(reason), 4, "NoConsent");

        // Guardian grants and is rewarded; after revoking, the balance stays but access ends.
        vm.prank(guardian);
        manager.grantConsent(school, 1, 30);
        vm.prank(guardian);
        manager.revokeConsent(school, 1);
        assertEq(rewards.balanceOf(guardian), 1, "reward kept after revoke");
        vm.prank(school);
        (allowed, reason) = manager.requestAccess(guardian, 1, record);
        assertFalse(allowed, "revoked despite guardian balance");
        assertEq(uint8(reason), 6, "Revoked");
    }

    /// @notice [SOL-RT-06] The token has no transfer, transferFrom, approve or burn.
    /// @dev Why it matters: Reward units must not be transferable.
    /// @custom:requirement Rewards: reward units are non-transferable
    function testTokenIsNonTransferable() public {
        _configureSelfAsMinter();
        token.mintReward(guardian);

        bytes[4] memory calls = [
            abi.encodeWithSignature("transfer(address,uint256)", school, 1),
            abi.encodeWithSignature("transferFrom(address,address,uint256)", guardian, school, 1),
            abi.encodeWithSignature("approve(address,uint256)", school, 1),
            abi.encodeWithSignature("burn(uint256)", 1)
        ];
        for (uint256 i = 0; i < calls.length; i++) {
            vm.prank(guardian);
            (bool ok,) = address(token).call(calls[i]);
            assertFalse(ok, "no such function");
        }
        assertEq(token.balanceOf(guardian), 1, "balance unchanged");
        assertEq(token.balanceOf(school), 0);
    }

    /// @notice [SOL-RT-07] The deployer can only do the one-time setup, nothing else.
    /// @dev Why it matters: The deployer must not reward itself or change the minter later.
    /// @custom:requirement Deployment: the deployer has setup authority only
    function testDeployerHasNoOngoingPower() public {
        _configureSelfAsMinter();

        vm.prank(deployer);
        vm.expectRevert(ConsentRewardToken.NotMinter.selector);
        token.mintReward(deployer);

        DummyContract other = new DummyContract();
        vm.prank(deployer);
        vm.expectRevert(ConsentRewardToken.MinterAlreadySet.selector);
        token.setMinterOnce(address(other));

        assertEq(token.balanceOf(deployer), 0);
        assertEq(token.minter(), address(this));
    }
}
