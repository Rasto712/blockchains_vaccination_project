# For Dev 1

What the Python side needs from `contracts/IdentityRegistry.sol` and `contracts/ConsentManager.sol`, and what I need from you. Read this next to [tasks/DEV_1.md](tasks/DEV_1.md) and [CONTRACT_API.md](CONTRACT_API.md). Rasto's `chain.py` sits between your contracts and my code, and his notes are in [FOR_DEV_4.md](FOR_DEV_4.md). The other notes are [FOR_DEV_2.md](FOR_DEV_2.md) and [FOR_DEV_5.md](FOR_DEV_5.md).

## Where things stand

The Python side is done: the local record, salts and commitments, the release rule, the school and doctor views, the menu and the tamper demo. It has 106 unit tests and none of them needs a node: the ones that touch the chain use a fake in `tests/fake_chain.py`, because there is no working contract yet. Nothing runs on a real node until your contracts deploy and `chain.py` exists.

## Python only reads the AccessAttempt event

Python never sees what `requestAccess` returns, because web3 does not hand back a transaction's return value. It trusts the single `AccessAttempt` log in the receipt, so:

- Emit exactly one `AccessAttempt` per call, from ConsentManager itself.
- Emit it after the hash step, with the final `allowed` and `reason`. A tampered copy must say `HashMismatch` in the event, not `Allowed`.
- Log `owner` exactly as passed, `requester` as `msg.sender`, `scope` as the raw uint8 that was passed (even 3), and `timestamp` as `block.timestamp`.
- A zero `observedHash` must not hit a ZeroHash check. When everything else allows, it returns normally with `HashMismatch`. Python sends the zero hash when its local record or salt is missing or broken, so this is how those cases get logged.
- Log every call, including repeats for the same tuple. When Python's final `checkAccess` recheck fails, it sends a second `requestAccess` with the same hash, so the late denial is recorded.

## getUserInfo never reverts

- For an account that has not registered, return `(false, 0, 0)`.
- `_users` is private, so ConsentManager has to use `getUserInfo` for the NotRegistered and MissingEvidence checks. If it reverted, `requestAccess` would revert for an unregistered party and no denied event would be logged.
- There is no evidence flag, so MissingEvidence means `vaccinationHash == bytes32(0)`.
- Return the stored bytes32 exactly, because Python compares it with `==`.
- The menu's "show my registration" expects to print "registered on-chain: no" for an account that has not registered.

It is an easy slip because `NotRegistered` is also on the proposed error list, so please do not use it in `getUserInfo`.

## Registration and attestation

- Only guardian, school and doctor register. The deployer and the clinic never call `registerUser`, so `registerVaccination` checks `msg.sender == trustedClinic` and must not require the clinic to be registered.
- The menu's attest action sends from whichever actor is selected and has no role check of its own. `NotTrustedClinic` is the only thing stopping, for example, the guardian attesting its own record.
- My fake checks in this order: `NotTrustedClinic`, then `NotRegistered` for the guardian, then `ZeroHash`, then `EvidenceAlreadyRegistered`. If you use the same order, a wrong actor always sees "rejected: NotTrustedClinic".

## Rewards

Mint the grant reward to the guardian who grants (`msg.sender` of `grantConsent`), not to the requester. The menu prints the granting guardian's balance before and after ("reward balance of guardian: 0 -> 1"), and the demo expects +1 on the first school grant and +0 on the 1-day regrant. The ConsentManager NatSpec says to mint once per tuple but not to whom. Only the token's `mintReward` NatSpec names the guardian. Robin's note says the same.

## Errors: please confirm the names

Both contracts still declare only `NotImplemented`, and CONTRACT_API lists the error names as proposed, waiting for you. I need either "as proposed" or the final list. Please declare them as custom errors, not require strings, because the menu prints the bare name as "rejected: <Name>". My fake raises exactly these:

- IdentityRegistry: `ZeroHash`, `AlreadyRegistered`, `NotTrustedClinic`, `NotRegistered`, `EvidenceAlreadyRegistered`
- ConsentManager: `UnsupportedScope`, `InvalidDuration`, `NotRegistered`, `ConsentStillActive`, `NoConsentToRevoke`

Tell me about any rename, or any extra revert that is not on the list (for example an evidence check or an owner != requester check in `grantConsent`). The Python tests mock the chain, so they would not notice, and the fake would quietly drift from your contract.

## Please check the fake

`tests/fake_chain.py`, from `register_user` down to `_evaluate` (about 90 lines), is my reading of your NatSpec as runnable Python. Please read it and reply "matches" or list the differences. Check:

- the revert order in each function
- what `grantConsent` requires (only that both parties are registered; no evidence check and no owner != requester check)
- the revoke rules
- the denial order
- the zero hash handling
- `getUserInfo` for an account that has not registered

It does not model rewards, minting, the ConsentGranted and ConsentRevoked events or the constructors, so leave those out. The 28 tests in `tests/test_access.py` and `tests/test_tamper.py` stand on it.

## What the first real run needs

- Python calls seven of your functions: `registerUser`, `registerVaccination` and `getUserInfo` on the registry, and `grantConsent`, `revokeConsent`, `checkAccess` and `requestAccess` on the manager.
- Rasto's deploy script needs the constructors, and his expiry step needs `expiresAt` (from `getConsent` or the ConsentGranted event).
- Nothing in Python calls `hasReceivedReward` or `trustedClinic`, so those can come last.
- `grantConsent` mints in the same transaction, so a grant only works once Robin's `setMinterOnce` and `mintReward` work too.

Tell me and Rasto as soon as the contracts deploy and register and attest work. That is when we do the first run on the node, then the scripted demo.
