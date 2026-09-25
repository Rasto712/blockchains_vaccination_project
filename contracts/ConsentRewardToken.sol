// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.28;

/**
 * @title ConsentRewardToken
 * @notice Developer 2: non-transferable reward units for first eligible consent grants.
 * @dev Scaffold only, not an ERC-20 implementation or deployed token.
 *      Only ConsentManager may mint; tokens never substitute for consent or transfer data ownership.
 *      One unit is minted per eligible tuple; ConsentManager owns tuple deduplication.
 *      No transfer, trading, payment, burn or allowance API belongs in the one-week scope.
 *      If Lab 3 mandates a token standard, align the interface with that lab before implementing.
 */
contract ConsentRewardToken {
    error NotImplemented();
    address private _deployer;
    address private _minter;
    bool private _minterConfigured;
    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;

    event MinterConfigured(address indexed minter);
    event RewardMinted(address indexed recipient, uint256 amount);

    /**
     * @notice Capture the deployer as the one-time minter-setup authority.
     * @dev Store msg.sender when implemented. Current deployment deliberately reverts.
     */
    constructor() {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Authorize the deployed ConsentManager exactly once.
     * @dev Only the deployer may call; reject repeated configuration and invalid contract addresses; emit MinterConfigured.
     * @param minter_ Nonzero deployed ConsentManager contract address.
     */
    function setMinterOnce(address minter_) external {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Mint exactly one reward unit for an eligible consent grant.
     * @dev Require msg.sender == configured minter; increment balance and total supply; emit RewardMinted. No arbitrary amount parameter.
     * @param recipient Guardian receiving the reward; reject zero address.
     */
    function mintReward(address recipient) external {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Read reward units; do not infer any access permission.
     * @param account Wallet whose balance is queried.
     * @return balance Number of reward units earned by the account.
     */
    function balanceOf(address account) external view returns (uint256 balance) {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Read all minted reward units.
     * @return supply Total units minted, for validation and presentation.
     */
    function totalSupply() external view returns (uint256 supply) {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
    /**
     * @notice Read the configured minting authority.
     * @return authorizedMinter ConsentManager address, or zero before configuration.
     */
    function minter() external view returns (address authorizedMinter) {
        // TODO: Implement the documented behavior. Never return a fake successful result.
        revert NotImplemented();
    }
}
