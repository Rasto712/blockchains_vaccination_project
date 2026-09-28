// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ConsentRewardToken} from "../contracts/ConsentRewardToken.sol";
import {IdentityRegistry} from "../contracts/IdentityRegistry.sol";
import {ConsentManager} from "../contracts/ConsentManager.sol";

/// @dev Stand-in contract with deployed code, so setMinterOnce's code-length check can be exercised in isolation.
contract DummyContract {}

/**
 * @title ConsentRewardTokenTest
 * @notice Developer 2: Solidity unit tests for ConsentRewardToken (run with `npm test`, Hardhat 3 + forge-std).
 * @dev Test IDs SOL-RT-01..07 are stable; the report and evaluation/results/test_results.csv cite them.
 *      The token is deployed from a separate `deployer` wallet so "deployer" and "minter" are distinct actors.
 *      In RT-02..04 this test contract plays the minter (it is a contract, like ConsentManager); RT-05 uses the
 *      real ConsentManager, so it only passes once Developer 1's contracts are implemented.
 *      AI assistance: drafted with Claude (Anthropic) and reviewed by Developer 2.
 */
contract ConsentRewardTokenTest is Test {
    ConsentRewardToken internal token;

    address internal deployer = makeAddr("deployer");
    address internal guardian = makeAddr("guardian");
    address internal school = makeAddr("school");
    address internal attacker = makeAddr("attacker");

    event MinterConfigured(address indexed minter);
    event RewardMinted(address indexed recipient, uint256 amount);

    /// @notice Deploy a fresh token from the deployer wallet before every test.
    function setUp() public {
        vm.prank(deployer);
        token = new ConsentRewardToken();
    }

    /// @dev Make this test contract the minter (stand-in for ConsentManager).
    function _configureSelfAsMinter() internal {
        vm.prank(deployer);
        token.setMinterOnce(address(this));
    }

    /// @notice [SOL-RT-01] Reject a wrong caller, zero and non-contract minters, and a second configuration; the deployer configures exactly once.
    /// @dev Why it matters: whoever is minter controls all rewards, so setup must be a one-shot deployer action that points at a contract, never at a person.
    /// @custom:requirement Rewards: the minter is set once, by the deployer, to a contract
    function testOnlyDeployerCanConfigureMinterOnce() public {
        DummyContract manager = new DummyContract();
        assertEq(token.minter(), address(0), "no minter before setup");

        vm.prank(attacker);
        vm.expectRevert(ConsentRewardToken.NotDeployer.selector);
        token.setMinterOnce(address(manager));

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
        assertEq(token.minter(), address(manager), "minter unchanged");
    }

    /// @notice [SOL-RT-02] Nobody can mint before setup; afterwards guardian, requester, attacker and even the deployer are rejected; only the configured minter succeeds.
    /// @dev Why it matters: if anyone but ConsentManager could mint, rewards would no longer prove a real consent grant.
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

    /// @notice [SOL-RT-03] Each mint adds exactly one unit to the recipient and to total supply and emits RewardMinted(recipient, 1).
    /// @dev Why it matters: one unit per call with no amount parameter means even the minter cannot inflate a single reward; the menu prints balanceOf as a plain integer.
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

    /// @notice [SOL-RT-04] Minting to the zero address is rejected and changes nothing.
    /// @dev Why it matters: a reward sent to address(0) would inflate supply with units nobody holds.
    /// @custom:requirement Rewards: no mint to the zero address
    function testRejectZeroRecipient() public {
        _configureSelfAsMinter();
        vm.expectRevert(ConsentRewardToken.ZeroAddress.selector);
        token.mintReward(address(0));
        assertEq(token.totalSupply(), 0);
        assertEq(token.balanceOf(address(0)), 0);
    }

    /// @notice [SOL-RT-05] A positive balance alone never grants access: a rewarded account without consent is denied, and a guardian's balance survives revocation while access does not.
    /// @dev Why it matters: the design rule is "consent controls access, tokens are only rewards"; this proves the manager never consults balanceOf.
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

    /// @notice [SOL-RT-06] The token exposes no transfer, transferFrom, approve or burn function: calls with those selectors fail.
    /// @dev Why it matters: reward units must be non-transferable so they cannot be sold or used to buy someone else's standing.
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

    /// @notice [SOL-RT-07] The deployer's only power is the one-time setup: it holds no balance and cannot mint or reconfigure afterwards.
    /// @dev Why it matters: the deployer is an administrator, not a data owner; it must not be able to reward itself or redirect minting later.
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
