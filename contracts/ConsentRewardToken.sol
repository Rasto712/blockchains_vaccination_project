// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.28;

/**
 * @title ConsentRewardToken
 * @notice Developer 2: non-transferable reward units for first eligible consent grants.
 * @dev Deliberately NOT an ERC-20: there is no transfer, approve, allowance, burn or decimals.
 *      Reward units are a record of contribution, not money and not an access right.
 *      Only the configured ConsentManager may mint; tokens never substitute for consent or transfer data ownership.
 *      One unit is minted per call; ConsentManager owns lifetime tuple deduplication.
 *      Setup authority: the deployer may configure the minter exactly once, and has no other power
 *      (it cannot mint, cannot change the minter later and cannot touch balances).
 *      AI assistance: drafted with Claude (Anthropic) and reviewed by Developer 2.
 */
contract ConsentRewardToken {
    /// @notice setMinterOnce was called by an account other than the deployer.
    error NotDeployer();
    /// @notice setMinterOnce was called a second time.
    error MinterAlreadySet();
    /// @notice A zero address was supplied as minter or reward recipient.
    error ZeroAddress();
    /// @notice The proposed minter has no deployed code, so it cannot be the ConsentManager.
    error NotAContract();
    /// @notice mintReward was called by an account other than the configured minter.
    error NotMinter();

    /// @dev One reward per mint call. Plain integer 1, no decimals; Python prints balanceOf as-is.
    uint256 private constant REWARD_UNIT = 1;

    address private immutable _deployer;
    address private _minter;
    bool private _minterConfigured;
    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;

    event MinterConfigured(address indexed minter);
    event RewardMinted(address indexed recipient, uint256 amount);

    /**
     * @notice Capture the deployer as the one-time minter-setup authority.
     * @dev immutable: the setup authority is fixed in the bytecode and can never be reassigned.
     */
    constructor() {
        _deployer = msg.sender;
    }

    /**
     * @notice Authorize the deployed ConsentManager exactly once.
     * @dev Checks, in order: caller is deployer, not configured yet, nonzero address, address has code.
     *      A code-length check rejects EOAs (for example a guardian wallet) so nobody can quietly make
     *      a person the minter. It does not prove the contract IS the ConsentManager; the deploy
     *      script verifies that by reading minter() back after setup.
     * @param minter_ Nonzero deployed ConsentManager contract address.
     */
    function setMinterOnce(address minter_) external {
        if (msg.sender != _deployer) revert NotDeployer();
        if (_minterConfigured) revert MinterAlreadySet();
        if (minter_ == address(0)) revert ZeroAddress();
        if (minter_.code.length == 0) revert NotAContract();

        _minter = minter_;
        _minterConfigured = true;
        emit MinterConfigured(minter_);
    }

    /**
     * @notice Mint exactly one reward unit for an eligible consent grant.
     * @dev No amount parameter, so a compromised or buggy caller still cannot mint more than one per call.
     *      Before configuration _minter is address(0), which can never be msg.sender, so minting is impossible.
     *      Solidity 0.8 checked arithmetic reverts on overflow (unreachable in practice).
     * @param recipient Guardian receiving the reward; reject zero address.
     */
    function mintReward(address recipient) external {
        if (msg.sender != _minter) revert NotMinter();
        if (recipient == address(0)) revert ZeroAddress();

        _balances[recipient] += REWARD_UNIT;
        _totalSupply += REWARD_UNIT;
        emit RewardMinted(recipient, REWARD_UNIT);
    }

    /**
     * @notice Read reward units; do not infer any access permission.
     * @param account Wallet whose balance is queried.
     * @return balance Number of reward units earned by the account.
     */
    function balanceOf(address account) external view returns (uint256 balance) {
        return _balances[account];
    }

    /**
     * @notice Read all minted reward units.
     * @return supply Total units minted, for validation and presentation.
     */
    function totalSupply() external view returns (uint256 supply) {
        return _totalSupply;
    }

    /**
     * @notice Read the configured minting authority.
     * @return authorizedMinter ConsentManager address, or zero before configuration.
     */
    function minter() external view returns (address authorizedMinter) {
        return _minter;
    }
}
