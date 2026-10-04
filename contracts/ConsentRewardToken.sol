// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

/// Simple reward counter (not an ERC-20): one unit per first consent grant.
/// Units can't be transferred and give no access rights.
/// Only the ConsentManager can mint, and the deployer sets it once.
contract ConsentRewardToken {
    error NotDeployer();
    error MinterAlreadySet();
    error ZeroAddress();
    error NotAContract();
    error NotMinter();

    // One reward per mint, plain 1 with no decimals.
    uint256 private constant REWARD_UNIT = 1;

    address private immutable _deployer;
    // No separate flag: the minter counts as set when _minter is not zero.
    address private _minter;
    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;

    event MinterConfigured(address indexed minter);
    event RewardMinted(address indexed recipient, uint256 amount);

    constructor() {
        _deployer = msg.sender;
    }

    // Deployer sets the ConsentManager once. The code check catches a plain wallet by mistake.
    function setMinterOnce(address minter_) external {
        if (msg.sender != _deployer) revert NotDeployer();
        if (_minter != address(0)) revert MinterAlreadySet();
        if (minter_ == address(0)) revert ZeroAddress();
        if (minter_.code.length == 0) revert NotAContract();

        _minter = minter_;
        emit MinterConfigured(minter_);
    }

    // Mints exactly one unit, so a buggy caller can't mint more.
    function mintReward(address recipient) external {
        if (msg.sender != _minter) revert NotMinter();
        if (recipient == address(0)) revert ZeroAddress();

        _balances[recipient] += REWARD_UNIT;
        _totalSupply += REWARD_UNIT;
        emit RewardMinted(recipient, REWARD_UNIT);
    }

    function balanceOf(address account) external view returns (uint256 balance) {
        return _balances[account];
    }

    function totalSupply() external view returns (uint256 supply) {
        return _totalSupply;
    }

    function minter() external view returns (address authorizedMinter) {
        return _minter;
    }
}
