"""Developer 4: one Python-to-local-Hardhat boundary.
Use web3 8.0.0 (requirements.txt) during implementation. No connection is opened on import.

Errors: web3 ContractLogicError (covers Custom and Panic errors) raised at transact() maps to
TransactionRejected; TimeExhausted maps to TransactionPending; RPC down or wrong chain ID maps to
ChainUnavailable. Never include calldata or local data in messages.

Convention: in transaction helpers the first address after the contract is the signer; view helpers
follow the contract argument order; callers pass owner/requester/guardian as keywords.

"""
import json
from pathlib import Path
from typing import Any
from app.models import *
from app.records import PROJECT_ROOT


def load_settings(path: Path) -> dict[str, Any]:
    """Read RPC URL, expected chain ID, deployment/ABI references and actor account indices.
    Resolve paths relative to the project root. Contract addresses must be deployed values, not null placeholders.
    Read settings, then load deployment_file; its addresses must be non-null; ABIs come from artifacts_dir
    (<artifacts_dir>/<Name>.sol/<Name>.json, keys abi and bytecode).
    """
    try:
        with path.open("r", encoding="utf-8") as file:
            settings = json.load(file)

        deployment_path = PROJECT_ROOT / settings["deployment_file"]

        with deployment_path.open("r", encoding="utf-8") as file:
            deployment = json.load(file)

        if deployment["chain_id"] != settings["expected_chain_id"]:
            raise ChainUnavailable("deployment chain ID mismatch")

        contracts = deployment["contracts"]
        for name in ("IdentityRegistry", "ConsentManager", "ConsentRewardToken"):
            if not contracts.get(name):
                raise ChainUnavailable("deployment address missing")
    except ChainUnavailable:
        raise
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ChainUnavailable("settings or deployment unavailable") from error

    settings["deployment"] = deployment
    return settings


def connect(settings: dict[str, Any]) -> Any:
    """Connect only to the explicitly selected local Hardhat RPC and verify its chain ID.
    Return an initialized client; reject unavailable/mismatched networks. No remote endpoint or key fallback.
    """
    from web3 import Web3

    try:
        rpc_url = settings["rpc_url"]
        expected_chain_id = settings["expected_chain_id"]
        client = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        if not client.is_connected():
            raise ChainUnavailable("RPC connection failed")
        if client.eth.chain_id != expected_chain_id:
            raise ChainUnavailable("chain ID mismatch")
        return client
    except ChainUnavailable:
        raise
    except Exception as error:
        raise ChainUnavailable("RPC connection failed") from error


def select_account(client: Any, actor_label: str, settings: dict[str, Any]) -> str:
    """Resolve a predefined demo label to its local unlocked account.
    Unknown labels fail; do not silently choose guardian or deployer. This is demo mode, not production authentication.

    """
    account_indices = settings["actor_account_indices"]

    if actor_label not in account_indices:
        raise ValueError("unknown demo actor")

    account_index = account_indices[actor_label]
    accounts = client.eth.accounts

    if account_index < 0 or account_index >= len(accounts):
        raise ValueError("demo account index unavailable")

    return accounts[account_index]


def load_contract(client: Any, name: str, deployment_path: Path) -> Any:
    """Read a compiled ABI and deployed address for one of the three named contracts.
    Check address/network provenance; never treat an unconfigured placeholder as a real deployment.
    """
    allowed_names = {"IdentityRegistry", "ConsentManager", "ConsentRewardToken"}
    if name not in allowed_names:
        raise ValueError("unknown contract")

    try:
        with deployment_path.open("r", encoding="utf-8") as file:
            deployment = json.load(file)
        contracts = deployment["contracts"]
        address = contracts[name]
        artifacts_dir = PROJECT_ROOT / deployment["artifacts_dir"]
        artifact_path = artifacts_dir / f"{name}.sol" / f"{name}.json"
        with artifact_path.open("r", encoding="utf-8") as file:
            artifact = json.load(file)
        abi = artifact["abi"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ChainUnavailable("deployment or contract artifact unavailable") from error

    if not isinstance(address, str) or not address:
        raise ChainUnavailable("contract address unavailable")

    try:
        checksum_address = client.to_checksum_address(address)
        if client.eth.get_code(checksum_address) in (b"", b"\x00"):
            raise ChainUnavailable("contract code unavailable")
        return client.eth.contract(address=checksum_address, abi=abi)
    except ChainUnavailable:
        raise
    except Exception as error:
        raise ChainUnavailable("contract unavailable") from error


def register_user(registry: Any, account: str, identity_hash: bytes) -> Receipt:
    """Submit registerUser from the selected account and await a successful receipt.
    Reject wrong hash length; never upload the raw identity fixture.
    """
    if not isinstance(identity_hash, bytes) or len(identity_hash) != 32:
        raise ValueError("identity hash must be 32 bytes")

    try:
        transaction_hash = registry.functions.registerUser(identity_hash).transact(
            {"from": account}
        )
    except Exception as error:
        from web3.exceptions import ContractLogicError

        if isinstance(error, ContractLogicError):
            raise TransactionRejected(type(error).__name__) from error
        raise

    return wait_for_receipt(registry.w3, transaction_hash)


def register_vaccination(registry: Any, clinic: str, guardian: str, record_hash: bytes) -> Receipt:
    """Submit the frozen record commitment from the trusted clinic account.
    Do not substitute the deployer when the selected clinic lacks authority.

    """

    if not isinstance(record_hash, bytes) or len(record_hash) != 32:
        raise ValueError("record hash must be 32 bytes")

    try:
        transaction_hash = registry.functions.registerVaccination(
            guardian,
            record_hash,
        ).transact(
            {"from": clinic}
        )
    except Exception as error:
        from web3.exceptions import ContractLogicError

        if isinstance(error, ContractLogicError):
            raise TransactionRejected(type(error).__name__) from error
        raise


    return wait_for_receipt(registry.w3, transaction_hash)


def get_user_info(registry: Any, account: str) -> IdentityInfo:
    """Decode the registered flag and two commitments using the agreed ABI.
    A view query is not a logged data-access transaction.
    """

    registered, identity_hash, vaccination_hash = (
        registry.functions.getUserInfo(account).call()
    )

    return {
        "registered": bool(registered),
        "identity_hash": bytes(identity_hash),
        "vaccination_hash": bytes(vaccination_hash),
    }

def grant_consent(manager: Any, guardian: str, requester: str, scope: Scope, duration_days: int) -> Receipt:
    """Validate 1-365 days and submit the grant from the guardian wallet.
    Raise TransactionRejected on revert. Do not independently mint rewards in Python; the manager owns that atomic action.
    """
    if scope not in (1, 2):
        raise ValueError("unsupported scope")
    if not 1 <= duration_days <= 365:
        raise ValueError("invalid duration")

    try:
        transaction_hash = manager.functions.grantConsent(
            requester,
            int(scope),
            duration_days,
        ).transact(
            {"from": guardian}
        )
    except Exception as error:
        from web3.exceptions import ContractLogicError

        if isinstance(error, ContractLogicError):
            raise TransactionRejected(type(error).__name__) from error
        raise

    return wait_for_receipt(manager.w3, transaction_hash)


def revoke_consent(manager: Any, guardian: str, requester: str, scope: Scope) -> Receipt:
    """Submit caller-owned revocation and await its receipt.
    Do not modify the lifetime rewarded flag or another owner's consent.
    """
    if scope not in (1, 2):
        raise ValueError("unsupported scope")
    if not isinstance(guardian, str) or not isinstance(requester, str):
        raise ValueError("invalid address")

    try:
        transaction_hash = manager.functions.revokeConsent(
            requester,
            int(scope),
        ).transact(
            {"from": guardian}
        )
    except Exception as error:
        from web3.exceptions import ContractLogicError

        if isinstance(error, ContractLogicError):
            raise TransactionRejected(type(error).__name__) from error
        raise


    return wait_for_receipt(manager.w3, transaction_hash)


def check_access(manager: Any, owner: str, requester: str, scope: Scope) -> ConsentDecision:
    """Read current registration/evidence/consent permission without releasing any data.
    This view supports the final recheck but never replaces the logged access transaction.
    """
    if scope not in (1, 2):
        raise ValueError("unsupported scope")
    if not isinstance(owner, str) or not isinstance(requester, str):
        raise ValueError("invalid address")

    try:
        allowed, reason = manager.functions.checkAccess(
            owner,
            requester,
            int(scope),
        ).call()
    except Exception as error:
        from web3.exceptions import ContractLogicError

        if isinstance(error, ContractLogicError):
            raise TransactionRejected(type(error).__name__) from error
        raise

    try:
        decoded_reason = Reason(int(reason))
    except (TypeError, ValueError) as error:
        raise ChainUnavailable("invalid access reason") from error

    return {
        "allowed": bool(allowed),
        "reason": decoded_reason,
    }

def request_access(manager: Any, requester: str, owner: str, scope: Scope, observed_hash: bytes) -> AccessAttempt:
    """Submit requestAccess from the requester, wait for its receipt and decode the matching event.
    Require expected owner/requester/scope and correct emitter. Business denial is allowed=false,
    not a reverted transaction; missing event or failed receipt is unavailable.

    """
    if not isinstance(requester, str) or not isinstance(owner, str):
        raise ValueError("invalid address")
    if not isinstance(observed_hash, bytes) or len(observed_hash) != 32:
        raise ValueError("observed hash must be 32 bytes")

    try:
        transaction_hash = manager.functions.requestAccess(
            owner,
            int(scope),
            observed_hash,
        ).transact(
            {"from": requester}
        )
    except Exception as error:
        from web3.exceptions import ContractLogicError

        if isinstance(error, ContractLogicError):
            raise TransactionRejected(type(error).__name__) from error
        raise

    receipt = wait_for_receipt(manager.w3, transaction_hash)
    event = decode_access_event(receipt, manager.address)
    if event["owner"].lower() != owner.lower():
        raise ChainUnavailable("access event owner mismatch")
    if event["requester"].lower() != requester.lower():
        raise ChainUnavailable("access event requester mismatch")
    if event["scope"] != int(scope):
        raise ChainUnavailable("access event scope mismatch")
    return event


def wait_for_receipt(client: Any, transaction_hash: str) -> Receipt:
    """Wait with a bounded timeout and return status, gas, block and logs.
    A hash/timeout/pending submission must never be presented as committed success.

    """
    from web3 import Web3
    from web3.exceptions import TimeExhausted

    try:
        raw_receipt = client.eth.wait_for_transaction_receipt(
            transaction_hash,
            timeout=30,
        )
    except TimeExhausted as error:
        raise TransactionPending("transaction receipt pending") from error

    status = int(raw_receipt["status"])
    if status != 1:
        raise TransactionRejected("transaction reverted")

    return {
        "transaction_hash": Web3.to_hex(raw_receipt["transactionHash"]),
        "status": status,
        "gas_used": int(raw_receipt["gasUsed"]),
        "block_number": int(raw_receipt["blockNumber"]),
        "logs": list(raw_receipt["logs"]),
    }


def decode_access_event(receipt: Receipt, expected_contract: str) -> AccessAttempt:
    """Decode the exact AccessAttempt log emitted by ConsentManager.
    Validate emitter and fields; no log means no permission. Record transaction hash for audit display.
    Decode with the ConsentManager ABI from the compiled artifact via
    contract.events.AccessAttempt().process_receipt({'logs': receipt['logs']}). process_receipt does not
    filter by address, so keep only logs whose address equals expected_contract (checksummed) and
    require exactly one.
    """
    from web3 import Web3

    artifact_path = (
        PROJECT_ROOT
        / "artifacts"
        / "contracts"
        / "ConsentManager.sol"
        / "ConsentManager.json"
    )
    try:
        with artifact_path.open("r", encoding="utf-8") as file:
            artifact = json.load(file)
        contract = Web3().eth.contract(abi=artifact["abi"])
        expected_address = Web3.to_checksum_address(expected_contract)
        matching_logs = [
            log
            for log in receipt["logs"]
            if Web3.to_checksum_address(log["address"]) == expected_address
        ]
        decoded = contract.events.AccessAttempt().process_receipt(
            {"logs": matching_logs}
        )
    except Exception as error:
        raise ChainUnavailable("access event unavailable") from error

    if len(decoded) != 1:
        raise ChainUnavailable("access event unavailable")

    args = decoded[0]["args"]
    try:
        reason = Reason(int(args["reason"]))
        return {
            "owner": args["owner"],
            "requester": args["requester"],
            "scope": int(args["scope"]),
            "timestamp": int(args["timestamp"]),
            "allowed": bool(args["allowed"]),
            "reason": reason,
            "transaction_hash": receipt["transaction_hash"],
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ChainUnavailable("access event invalid") from error


def get_reward_balance(token: Any, account: str) -> int:
    """Read reward units for display only. The balance must never be used to bypass consent.

    """
    if not isinstance(account, str):
        raise ValueError("invalid address")

    try:
        return int(token.functions.balanceOf(account).call())
    except Exception as error:
        raise ChainUnavailable("reward balance unavailable") from error


def list_access_events(manager: Any, from_block: int) -> list[AccessAttempt]:
    """Query bounded AccessAttempt events and display minimal audit metadata.
    No delete-log operation is part of the design; no medical JSON belongs in these events.
    Keep unknown scope codes as int; display as 'scope 3 (unsupported)'; never raise.

    """
    from web3 import Web3

    if not isinstance(from_block, int) or from_block < 0:
        raise ValueError("invalid starting block")

    try:
        logs = manager.events.AccessAttempt().get_logs(from_block=from_block)
        events: list[AccessAttempt] = []
        for log in logs:
            args = log["args"]
            events.append(
                {
                    "owner": args["owner"],
                    "requester": args["requester"],
                    "scope": int(args["scope"]),
                    "timestamp": int(args["timestamp"]),
                    "allowed": bool(args["allowed"]),
                    "reason": Reason(int(args["reason"])),
                    "transaction_hash": Web3.to_hex(log["transactionHash"]),
                }
            )
        return events
    except (KeyError, TypeError, ValueError) as error:
        raise ChainUnavailable("access events invalid") from error
    except Exception as error:
        raise ChainUnavailable("access events unavailable") from error
