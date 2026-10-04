# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Talks to the local Hardhat node through web3.
web3 is only imported inside functions, so importing this file needs nothing extra.

Node and web3 errors are turned into our own exceptions (ChainUnavailable, TransactionRejected,
TransactionPending). A revert only carries the Solidity error name. Bad arguments are a ValueError.
Other errors are bugs and are not caught.

In the transaction helpers the first address after the contract is the signer.
"""
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from app.models import (
    Scope, Reason, IdentityInfo, ConsentDecision, ConsentInfo, AccessAttempt, Receipt,
    ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, ArtifactUnavailable, TransactionRejected,
    TransactionPending,
)
from app.records import PROJECT_ROOT

CONTRACT_NAMES = ("IdentityRegistry", "ConsentManager", "ConsentRewardToken")
# where npm run compile writes artifacts
DEFAULT_ARTIFACTS_DIR = "artifacts/contracts"
# no retries, so a stuck node fails after this many seconds
RPC_TIMEOUT_SECONDS = 10
RECEIPT_TIMEOUT_SECONDS = 30
PANIC_SELECTOR = "0x4e487b71"
MODEL_ERRORS = (ChainUnavailable, TransactionRejected, TransactionPending)
WEB3_MISSING = "web3 not installed"

# error selector -> Solidity error name
_error_names: dict[str, str] = {}
# true once the default artifacts have been read
_default_errors_loaded = False


def load_settings(path: Path) -> dict[str, Any]:
    """Read the settings file and the deployment file, and check the deployment is usable."""
    try:
        with Path(path).open("r", encoding="utf-8") as file:
            settings = json.load(file)
        deployment_path = PROJECT_ROOT / settings["deployment_file"]
        expected_chain_id = settings["expected_chain_id"]
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ChainUnavailable("settings unavailable") from error

    try:
        with deployment_path.open("r", encoding="utf-8") as file:
            deployment = json.load(file)
        chain_id = deployment["chain_id"]
        contracts = deployment["contracts"]
        missing = [name for name in CONTRACT_NAMES if not contracts.get(name)]
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as error:
        raise DeploymentUnavailable("deployment unavailable") from error
    if chain_id != expected_chain_id:
        raise DeploymentUnavailable("deployment chain ID mismatch")
    if missing:
        raise DeploymentUnavailable("deployment address missing")

    settings["deployment"] = deployment
    return settings


def connect(settings: dict[str, Any]) -> Any:
    """Connect to the local node and check the chain ID. Requests time out and are not retried."""
    try:
        from web3 import Web3
    except ImportError as error:
        raise Web3NotInstalled(WEB3_MISSING) from error

    try:
        rpc_url, expected_chain_id = settings["rpc_url"], settings["expected_chain_id"]
    except (KeyError, TypeError):
        rpc_url = expected_chain_id = None
    # an empty URL would make web3 pick another endpoint
    if not isinstance(rpc_url, str) or not rpc_url.strip():
        raise ChainUnavailable("settings unavailable")

    with _rpc_errors():
        provider = Web3.HTTPProvider(
            rpc_url,
            request_kwargs={"timeout": RPC_TIMEOUT_SECONDS},
            exception_retry_configuration=None,
        )
        client = Web3(provider)
        if not client.is_connected():
            raise ChainUnavailable("RPC connection failed")
        if client.eth.chain_id != expected_chain_id:
            raise ChainUnavailable("chain ID mismatch")
    return client


def select_account(client: Any, actor_label: str, settings: dict[str, Any]) -> str:
    """Turn a demo label like 'guardian' into its local account address. Demo only, not real login."""
    account_indices = settings["actor_account_indices"]

    if actor_label not in account_indices:
        raise ValueError("unknown demo actor")

    account_index = account_indices[actor_label]
    with _rpc_errors():
        accounts = list(client.eth.accounts)

    if isinstance(account_index, bool) or not isinstance(account_index, int):
        raise ValueError("demo account index unavailable")
    if account_index < 0 or account_index >= len(accounts):
        raise ValueError("demo account index unavailable")

    return _address(accounts[account_index])


def read_artifact(name: str, artifacts_dir: str = DEFAULT_ARTIFACTS_DIR) -> dict[str, Any]:
    """Read one compiled contract artifact and remember its error names."""
    if name not in CONTRACT_NAMES:
        raise ValueError("unknown contract")
    try:
        artifact_path = PROJECT_ROOT / artifacts_dir / f"{name}.sol" / f"{name}.json"
        with artifact_path.open("r", encoding="utf-8") as file:
            artifact = json.load(file)
        abi = artifact["abi"]
        if not isinstance(abi, list):
            raise TypeError("abi must be a list")
        for key in ("bytecode", "deployedBytecode"):
            if not isinstance(artifact[key], str):
                raise TypeError("bytecode must be hex text")
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ArtifactUnavailable("contract artifact unavailable") from error
    _learn_errors(abi)
    return artifact


def runtime_code(client: Any, address: str) -> bytes:
    """Code deployed at an address (empty if nothing is there)."""
    address = _address(address)
    with _rpc_errors():
        return bytes(client.eth.get_code(address))


def block_hash(client: Any, number: int) -> str | None:
    """Hash of a block on this node, or None if the block does not exist."""
    from web3 import Web3
    from web3.exceptions import BlockNotFound

    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        raise ValueError("invalid block number")
    with _rpc_errors():
        try:
            block = client.eth.get_block(number)
        except BlockNotFound:
            return None
        return Web3.to_hex(block["hash"]).lower()


def code_hash(code: bytes) -> str:
    """keccak hash of contract code."""
    from web3 import Web3

    return Web3.to_hex(Web3.keccak(bytes(code)))


def matches_artifact(code: bytes, artifact: dict[str, Any]) -> bool:
    """True if the deployed code equals the compiled code. Immutable slots are blanked out first."""
    try:
        expected = bytearray.fromhex(artifact["deployedBytecode"].removeprefix("0x"))
        actual = bytearray(code)
        if not expected or len(expected) != len(actual):
            return False
        for references in artifact.get("immutableReferences", {}).values():
            for reference in references:
                start, length = int(reference["start"]), int(reference["length"])
                expected[start:start + length] = bytes(length)
                actual[start:start + length] = bytes(length)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return actual == expected


def load_contract(client: Any, name: str, deployment_path: Path) -> Any:
    """Load a contract after checking the deployment still matches this node.
    Checks the chain ID, the deploy block hash, the code hash and the compiled code.
    """
    if name not in CONTRACT_NAMES:
        raise ValueError("unknown contract")

    try:
        with Path(deployment_path).open("r", encoding="utf-8") as file:
            deployment = json.load(file)
        address = _address(deployment["contracts"][name])
        chain_id = deployment["chain_id"]
        recorded_hash = deployment["code_hashes"][name]
        artifacts_dir = deployment["artifacts_dir"]
        deploy_block, recorded_block_hash = _deploy_block(deployment["deploy_block"])
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise DeploymentUnavailable("deployment unavailable") from error

    artifact = read_artifact(name, artifacts_dir)
    with _rpc_errors():
        node_chain_id = client.eth.chain_id
    if chain_id != node_chain_id:
        raise DeploymentUnavailable("deployment is for another chain")
    if block_hash(client, deploy_block) != recorded_block_hash:
        raise DeploymentUnavailable("deployment is from another run of the node")
    code = runtime_code(client, address)
    if code in (b"", b"\x00"):
        raise DeploymentUnavailable("contract code unavailable")
    if code_hash(code) != recorded_hash:
        raise DeploymentUnavailable("contract code does not match the deployment")
    if not matches_artifact(code, artifact):
        raise DeploymentUnavailable("compiled contract does not match the deployed code")

    with _rpc_errors():
        return client.eth.contract(address=address, abi=artifact["abi"])


def send_transaction(client: Any, call: Any, sender: str) -> Receipt:
    """Send a prepared call from sender and wait for a successful receipt."""
    sender = _address(sender)
    with _rpc_errors():
        transaction_hash = call.transact({"from": sender})
    return wait_for_receipt(client, transaction_hash)


def call_view(call: Any) -> Any:
    """Run a read-only call. A revert becomes TransactionRejected."""
    with _rpc_errors():
        return call.call()


def register_user(registry: Any, account: str, identity_hash: bytes) -> Receipt:
    """Register the account's identity hash."""
    account = _address(account)
    _require_hash(identity_hash, "identity hash")
    return _transact(registry, "registerUser", (identity_hash,), account)


def register_vaccination(registry: Any, clinic: str, guardian: str, record_hash: bytes) -> Receipt:
    """The clinic registers the record hash for a guardian."""
    clinic = _address(clinic)
    guardian = _address(guardian)
    _require_hash(record_hash, "record hash")
    return _transact(registry, "registerVaccination", (guardian, record_hash), clinic)


def get_user_info(registry: Any, account: str) -> IdentityInfo:
    """Read the registered flag and the two hashes for an account."""
    account = _address(account)
    with _rpc_errors():
        registered, identity_hash, vaccination_hash = _view(registry, "getUserInfo", (account,))
        return {
            "registered": bool(registered),
            "identity_hash": bytes(identity_hash),
            "vaccination_hash": bytes(vaccination_hash),
        }


def grant_consent(manager: Any, guardian: str, requester: str, scope: Scope, duration_days: int) -> Receipt:
    """The guardian grants a requester access to a scope for some days. The contract checks the rules."""
    guardian = _address(guardian)
    requester = _address(requester)
    arguments = (requester, _scope(scope), _duration(duration_days))
    return _transact(manager, "grantConsent", arguments, guardian)


def revoke_consent(manager: Any, guardian: str, requester: str, scope: Scope) -> Receipt:
    """The guardian revokes a requester's consent for a scope."""
    guardian = _address(guardian)
    requester = _address(requester)
    return _transact(manager, "revokeConsent", (requester, _scope(scope)), guardian)


def check_access(manager: Any, owner: str, requester: str, scope: Scope) -> ConsentDecision:
    """Ask the contract if access is allowed right now. Nothing is logged or released."""
    owner = _address(owner)
    requester = _address(requester)
    scope_code = _scope(scope)
    with _rpc_errors():
        allowed, reason = _view(manager, "checkAccess", (owner, requester, scope_code))

    try:
        decoded_reason = Reason(int(reason))
    except (TypeError, ValueError) as error:
        raise ChainUnavailable("invalid access reason") from error

    return {
        "allowed": bool(allowed),
        "reason": decoded_reason,
    }


def get_consent(manager: Any, owner: str, requester: str, scope: Scope) -> ConsentInfo:
    """Read the stored grant (expiry time and revoked flag). expires_at 0 means never granted."""
    owner = _address(owner)
    requester = _address(requester)
    scope_code = _scope(scope)
    with _rpc_errors():
        expires_at, revoked = _view(manager, "getConsent", (owner, requester, scope_code))
        return {"expires_at": int(expires_at), "revoked": bool(revoked)}


def request_access(manager: Any, requester: str, owner: str, scope: Scope, observed_hash: bytes) -> AccessAttempt:
    """The requester logs an access request and we read back the event.
    A denial is allowed=False, not an error.
    """
    requester = _address(requester)
    owner = _address(owner)
    scope_code = _scope(scope)
    _require_hash(observed_hash, "observed hash")

    receipt = _transact(manager, "requestAccess", (owner, scope_code, observed_hash), requester)
    event = decode_access_event(receipt, manager)
    if event["owner"].lower() != owner.lower():
        raise ChainUnavailable("access event owner mismatch")
    if event["requester"].lower() != requester.lower():
        raise ChainUnavailable("access event requester mismatch")
    if event["scope"] != scope_code:
        raise ChainUnavailable("access event scope mismatch")
    return event


def wait_for_receipt(client: Any, transaction_hash: str) -> Receipt:
    """Wait for the receipt. Status 0 is TransactionRejected.
    If no receipt arrives, the transaction may still be mined, so it is TransactionPending.
    """
    from web3 import Web3

    try:
        with _rpc_errors():
            raw_receipt = client.eth.wait_for_transaction_receipt(
                transaction_hash,
                timeout=RECEIPT_TIMEOUT_SECONDS,
            )
    except ChainUnavailable as error:
        raise TransactionPending("transaction receipt pending") from error

    try:
        status = int(raw_receipt["status"])
        contract_address = raw_receipt.get("contractAddress")
        receipt: Receipt = {
            "transaction_hash": Web3.to_hex(raw_receipt["transactionHash"]),
            "status": status,
            "gas_used": int(raw_receipt["gasUsed"]),
            "block_number": int(raw_receipt["blockNumber"]),
            "logs": list(raw_receipt["logs"]),
            "contract_address": Web3.to_checksum_address(contract_address) if contract_address else None,
        }
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ChainUnavailable("receipt invalid") from error

    if status != 1:
        raise TransactionRejected("Reverted")
    return receipt


def decode_access_event(receipt: Receipt, manager: Any) -> AccessAttempt:
    """Find the one AccessAttempt event emitted by the manager in a receipt."""
    from web3 import Web3
    from web3.logs import DISCARD

    try:
        expected_address = Web3.to_checksum_address(manager.address)
        matching_logs = [
            log
            for log in receipt["logs"]
            if Web3.to_checksum_address(log["address"]) == expected_address
        ]
        decoded = manager.events.AccessAttempt().process_receipt(
            {"logs": matching_logs},
            errors=DISCARD,
        )
    except Exception as error:
        raise ChainUnavailable("access event unavailable") from error

    if len(decoded) != 1:
        raise ChainUnavailable("access event unavailable")

    args = decoded[0]["args"]
    try:
        reason = Reason(int(args["reason"]))
        return {
            "owner": Web3.to_checksum_address(args["owner"]),
            "requester": Web3.to_checksum_address(args["requester"]),
            "scope": int(args["scope"]),
            "timestamp": int(args["timestamp"]),
            "allowed": bool(args["allowed"]),
            "reason": reason,
            "transaction_hash": receipt["transaction_hash"],
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ChainUnavailable("access event invalid") from error


def get_reward_balance(token: Any, account: str) -> int:
    """Read reward tokens for display only."""
    account = _address(account)
    with _rpc_errors():
        return int(_view(token, "balanceOf", (account,)))


def list_access_events(manager: Any, from_block: int) -> list[AccessAttempt]:
    """Read all AccessAttempt events from a block onwards."""
    from web3 import Web3

    if isinstance(from_block, bool) or not isinstance(from_block, int) or from_block < 0:
        raise ValueError("invalid starting block")

    with _rpc_errors():
        logs = manager.events.AccessAttempt().get_logs(from_block=from_block)

    try:
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


def error_name(revert_data: Any) -> str:
    """Solidity error name for revert data, 'Panic' for a panic, otherwise 'Reverted'."""
    if isinstance(revert_data, dict):
        revert_data = revert_data.get("data")
    if not isinstance(revert_data, str) or not revert_data.startswith("0x") or len(revert_data) < 10:
        return "Reverted"
    selector = revert_data[:10].lower()
    if selector == PANIC_SELECTOR:
        return "Panic"
    if selector not in _error_names:
        _load_default_errors()
    return _error_names.get(selector, "Reverted")


def _transact(contract: Any, function: str, arguments: tuple[Any, ...], sender: str) -> Receipt:
    with _rpc_errors():
        call = getattr(contract.functions, function)(*arguments)
    return send_transaction(contract.w3, call, sender)


def _view(contract: Any, function: str, arguments: tuple[Any, ...]) -> Any:
    with _rpc_errors():
        call = getattr(contract.functions, function)(*arguments)
    return call_view(call)


@contextmanager
def _rpc_errors() -> Iterator[None]:
    """Turn node and web3 failures into our exceptions. Anything else is a bug and passes through."""
    try:
        yield
    except MODEL_ERRORS:
        raise
    except Exception as error:
        mapped = _mapped(error)
        if mapped is None:
            raise
        raise mapped from error


def _mapped(error: Exception) -> Exception | None:
    """Our exception for a web3 or transport error, or None if it is not one."""
    # connection errors and timeouts are OSErrors
    if isinstance(error, (OSError, json.JSONDecodeError)):
        return ChainUnavailable("RPC request failed")
    try:
        from web3.exceptions import (
            BadFunctionCallOutput, BadResponseFormat, CannotHandleRequest, ContractLogicError, ContractPanicError,
            MultipleFailedRequests, ProviderConnectionError, TimeExhausted, TooManyRequests, Web3RPCError,
        )
    except ImportError:
        return None

    if isinstance(error, ContractPanicError):
        return TransactionRejected("Panic")
    if isinstance(error, ContractLogicError):
        return TransactionRejected(error_name(error.data))
    if isinstance(error, TimeExhausted):
        return TransactionPending("transaction receipt pending")
    if isinstance(error, Web3RPCError):
        revert = _rpc_revert_name(error)
        if revert is not None:
            return TransactionRejected(revert)
        return ChainUnavailable("RPC request failed")
    node_errors = (
        BadFunctionCallOutput, BadResponseFormat, CannotHandleRequest, MultipleFailedRequests,
        ProviderConnectionError, TooManyRequests,
    )
    if isinstance(error, node_errors):
        return ChainUnavailable("RPC request failed")
    return None


def _rpc_revert_name(error: Any) -> str | None:
    # Hardhat 3 reports a revert at send time like this:
    # {"code": 3, "message": "...custom error 'ZeroHash()'", "data": "0xf1ae58d5"}
    response = getattr(error, "rpc_response", None)
    details = response.get("error") if isinstance(response, dict) else None
    if not isinstance(details, dict):
        return None
    message = details.get("message")
    if details.get("code") == 3 or (isinstance(message, str) and "revert" in message.lower()):
        return error_name(details.get("data"))
    return None


def _learn_errors(abi: list[Any]) -> None:
    try:
        from web3 import Web3
    except ImportError:
        return

    for entry in abi:
        if not isinstance(entry, dict) or entry.get("type") != "error" or not isinstance(entry.get("name"), str):
            continue
        try:
            signature = f"{entry['name']}({','.join(_abi_type(item) for item in entry.get('inputs', []))})"
        except (KeyError, TypeError):
            continue
        _error_names.setdefault(Web3.to_hex(Web3.keccak(text=signature)[:4]), entry["name"])


def _abi_type(item: dict[str, Any]) -> str:
    # tuples have to be spelled out to get the right selector
    kind = item["type"]
    if kind.startswith("tuple"):
        return f"({','.join(_abi_type(component) for component in item['components'])}){kind[len('tuple'):]}"
    return kind


def _load_default_errors() -> None:
    global _default_errors_loaded
    if _default_errors_loaded:
        return
    _default_errors_loaded = True
    for name in CONTRACT_NAMES:
        try:
            read_artifact(name)
        except DeploymentUnavailable:
            continue


def _deploy_block(value: Any) -> tuple[int, str]:
    """Number and hash from deployment.json's deploy_block."""
    number, recorded = value["number"], value["hash"]
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        raise ValueError("invalid deploy block number")
    if not isinstance(recorded, str) or len(recorded) != 66 or not recorded.startswith("0x"):
        raise ValueError("invalid deploy block hash")
    int(recorded, 16)
    return number, recorded.lower()


def _address(value: Any) -> str:
    """Checksum an address; anything else is a ValueError."""
    from web3 import Web3

    if not isinstance(value, str):
        raise ValueError("invalid address")
    try:
        return Web3.to_checksum_address(value)
    except (TypeError, ValueError):
        raise ValueError("invalid address") from None


def _scope(value: Any) -> int:
    # the contract decides which scopes are supported
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ValueError("scope must be a whole number from 0 to 255")
    return int(value)


def _duration(value: Any) -> int:
    # the contract enforces 1-365 days
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        raise ValueError("duration must be a whole number of days from 0 to 65535")
    return int(value)


def _require_hash(value: Any, label: str) -> None:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ValueError(f"{label} must be 32 bytes")
