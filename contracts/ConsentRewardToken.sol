// SPDX-License-Identifier: UNLICENSED
// AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
pragma solidity 0.8.28;

/**
 * @title ConsentRewardToken
 * @notice Non-transferable reward units, one for the first grant of each (owner, requester, scope) tuple.
 * @dev Deliberately NOT an ERC-20: there is no transfer, approve, allowance, burn or decimals.
 *      Reward units count first grants. They are not money, not an access right and not proof of a record:
 *      ConsentManager also rewards owners with no attested record, and every requester a wallet names is a new
 *      tuple, so the units are not Sybil-resistant (see ConsentManager.grantConsent).
 *      Only the configured ConsentManager may mint; tokens never substitute for consent or transfer data ownership.
 *      One unit is minted per call; ConsentManager owns lifetime tuple deduplication.
 *      Setup authority: the deployer may configure the minter exactly once, and has no other power
 *      (it cannot call mintReward itself, cannot change the minter later and cannot touch balances).
 *      The deployer is trusted to pass the real ConsentManager at setup; anyone can check that
 *      minter() equals the ConsentManager address, and scripts/deploy_local.py does.
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
    // No separate "configured" flag: setMinterOnce rejects address(0), so the minter is set exactly when
    // _minter is nonzero (the same rule IdentityRegistry uses for a nonzero identityHash).
    address private _minter;
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
     * @dev Checks, in order: caller is deployer (NotDeployer), not configured yet (MinterAlreadySet),
     *      nonzero address (ZeroAddress), address has code (NotAContract).
     *      The code-length check rejects a plain wallet (EOA) passed by mistake, for example a guardian's.
     *      It does not prove the contract IS the ConsentManager: a deployer who meant to could configure
     *      a forwarding contract of its own. The deployer is trusted at setup, and the deploy script
     *      verifies the setup by reading minter() back and comparing it with the ConsentManager address.
     * @param minter_ Nonzero deployed ConsentManager contract address.
     */
    function setMinterOnce(address minter_) external {
        if (msg.sender != _deployer) revert NotDeployer();
        if (_minter != address(0)) revert MinterAlreadySet();
        if (minter_ == address(0)) revert ZeroAddress();
        if (minter_.code.length == 0) revert NotAContract();

        _minter = minter_;
        emit MinterConfigured(minter_);
    }

    /**
     * @notice Mint exactly one reward unit for an eligible consent grant.
     * @dev Checks, in order: caller is the minter (NotMinter), nonzero recipient (ZeroAddress).
     *      No amount parameter, so a compromised or buggy caller still cannot mint more than one per call.
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
     * @return supply Total units minted, for validation.
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
