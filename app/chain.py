# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""One Python-to-local-Hardhat boundary.
Uses web3 8.0.0 (requirements.txt). No connection is opened on import, and web3 is
imported inside the functions, so importing this module needs only the standard library.

Errors: every RPC call goes through one mapping (_rpc_errors), so node and web3 failures reach callers
only as the three model exceptions. A revert becomes TransactionRejected holding only the Solidity error
name, found by matching the 4-byte selector against the error entries of the three compiled ABIs. That
covers ContractLogicError from eth_call/eth_estimateGas and the Web3RPCError Hardhat 3 answers with when a
transaction reverts at send time. A panic is "Panic"; an unknown selector, and a receipt with status 0, is
"Reverted". RPC down, timeouts, wrong chain ID and any other node failure before a transaction is sent map
to ChainUnavailable. Once a transaction hash exists, no receipt in time (TimeExhausted) and a node that
stops answering while Python waits both map to TransactionPending, because the transaction may still be
mined. A missing, stale or mismatched deployment.json maps to DeploymentUnavailable, a missing compiled
artifact to ArtifactUnavailable (its subclass) and a missing web3 to Web3NotInstalled; all three are
ChainUnavailable subclasses. Anything else raised inside the mapping, such as a KeyError, a TypeError or an
ABI function that does not exist, is a bug rather than a node failure and goes up unchanged, so the menu
prints "failed: <type>". Never include calldata or local data in messages.
Caller mistakes stay ValueError: an address that is not one, a hash that is not 32 bytes, a scope outside
0-255 or a duration outside 0-65535. Business rules (supported scopes, 1-365 days) are the contract's, so
they come back as named reverts or as a denial reason, the same as tests/fake_chain.py.

Convention: in transaction helpers the first address after the contract is the signer; view helpers
follow the contract argument order; callers pass owner/requester/guardian as keywords. Addresses may be
given in any case; they are checksummed before use.
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
# where npm run compile writes artifacts, relative to the project root; deploy_local records it
DEFAULT_ARTIFACTS_DIR = "artifacts/contracts"
# one attempt per request and no retries, so a stalled node fails within this many seconds
RPC_TIMEOUT_SECONDS = 10
RECEIPT_TIMEOUT_SECONDS = 30
PANIC_SELECTOR = "0x4e487b71"
MODEL_ERRORS = (ChainUnavailable, TransactionRejected, TransactionPending)
# ChainUnavailable message when web3 cannot be imported (Web3NotInstalled)
WEB3_MISSING = "web3 not installed"

# 4-byte selector ("0x" + 8 hex digits) -> Solidity error name, filled from every artifact read
_error_names: dict[str, str] = {}
# set once the three default artifacts have been read for their errors
_default_errors_loaded = False


def load_settings(path: Path) -> dict[str, Any]:
    """Read RPC URL, expected chain ID, deployment/ABI references and actor account indices.
    Resolve paths relative to the project root. Contract addresses must be deployed values, not null placeholders.
    Read settings, then load deployment_file; its addresses must be non-null; ABIs come from artifacts_dir
    (<artifacts_dir>/<Name>.sol/<Name>.json, keys abi and bytecode).
    The console reads settings with plain json instead, so it works before anything is deployed.
    """
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
    """Connect only to the explicitly selected local Hardhat RPC and verify its chain ID.
    Return an initialized client; reject unavailable/mismatched networks. No remote endpoint or key fallback.
    Requests time out after RPC_TIMEOUT_SECONDS and are never retried, so a stalled node fails fast.
    Without web3 this raises Web3NotInstalled; a missing rpc_url or expected_chain_id is ChainUnavailable.
    """
    try:
        from web3 import Web3
    except ImportError as error:
        raise Web3NotInstalled(WEB3_MISSING) from error

    try:
        rpc_url, expected_chain_id = settings["rpc_url"], settings["expected_chain_id"]
    except (KeyError, TypeError):
        rpc_url = expected_chain_id = None
    # an empty URL would make web3 fall back to another endpoint
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
    """Resolve a predefined demo label to its local unlocked account.
    Unknown labels fail; do not silently choose guardian or deployer. This is demo mode, not production authentication.

    """
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
    """Read one compiled Hardhat artifact, <artifacts_dir>/<Name>.sol/<Name>.json under the project root.
    A missing or unreadable artifact raises ArtifactUnavailable (run npm run compile). Its error
    entries are added to the selector map, so reverts from this contract resolve to their names.
    """
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
    """The runtime code at address as raw bytes; empty when nothing is deployed there."""
    address = _address(address)
    with _rpc_errors():
        return bytes(client.eth.get_code(address))


def block_hash(client: Any, number: int) -> str | None:
    """Hash of block number on this node as lowercase 0x-hex; None when the node has no such block.
    deploy_local records it for the block of the IdentityRegistry deploy and load_contract compares it:
    a restarted node mines its blocks at other times, so the hash differs even for identical transactions.
    """
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
    """keccak-256 of runtime code as 0x-hex; deploy_local records it and load_contract compares it."""
    from web3 import Web3

    return Web3.to_hex(Web3.keccak(bytes(code)))


def matches_artifact(code: bytes, artifact: dict[str, Any]) -> bool:
    """True when runtime code is exactly the artifact's deployedBytecode.
    Immutable values (such as the token's deployer) are filled in at deployment, so their byte ranges
    from immutableReferences are masked in both before comparing.
    """
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
    """Read a compiled ABI and deployed address for one of the three named contracts.
    Check address/network provenance; never treat an unconfigured placeholder as a real deployment.
    The deployment must be for this node's chain ID, the block deploy_local recorded (deploy_block, the
    block of the IdentityRegistry deploy) must still have the same hash on this node, the code at the
    address must hash to the value deploy_local recorded, and it must still be the compiled contract.
    The block check tells a restarted node apart even when the same deployer nonces later deployed
    byte-identical contracts at the same addresses (python -m evaluation.measure does exactly that).
    A missing, stale or mismatched deployment.json raises DeploymentUnavailable; a missing compiled
    artifact raises ArtifactUnavailable.
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
        # also a null placeholder address, which _address rejects, and a file without deploy_block
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
    """Send one prepared contract call or constructor from sender and wait for its successful receipt.
    Every transaction in chain.py and deploy_local takes this path, so errors map the same way everywhere:
    a node failure before the transaction hash exists is ChainUnavailable, one after it TransactionPending.
    """
    sender = _address(sender)
    with _rpc_errors():
        transaction_hash = call.transact({"from": sender})
    return wait_for_receipt(client, transaction_hash)


def call_view(call: Any) -> Any:
    """Run one prepared view call; a revert is TransactionRejected with its error name."""
    with _rpc_errors():
        return call.call()


def register_user(registry: Any, account: str, identity_hash: bytes) -> Receipt:
    """Submit registerUser from the selected account and await a successful receipt.
    Reject wrong hash length; never upload the raw identity fixture.
    """
    account = _address(account)
    _require_hash(identity_hash, "identity hash")
    return _transact(registry, "registerUser", (identity_hash,), account)


def register_vaccination(registry: Any, clinic: str, guardian: str, record_hash: bytes) -> Receipt:
    """Submit the frozen record commitment from the trusted clinic account.
    Do not substitute the deployer when the selected clinic lacks authority.

    """
    clinic = _address(clinic)
    guardian = _address(guardian)
    _require_hash(record_hash, "record hash")
    return _transact(registry, "registerVaccination", (guardian, record_hash), clinic)


def get_user_info(registry: Any, account: str) -> IdentityInfo:
    """Decode the registered flag and two commitments using the contract ABI.
    A view query is not a logged data-access transaction.
    """
    account = _address(account)
    with _rpc_errors():
        registered, identity_hash, vaccination_hash = _view(registry, "getUserInfo", (account,))
        return {
            "registered": bool(registered),
            "identity_hash": bytes(identity_hash),
            "vaccination_hash": bytes(vaccination_hash),
        }


def grant_consent(manager: Any, guardian: str, requester: str, scope: Scope, duration_days: int) -> Receipt:
    """Submit the grant from the guardian wallet and await its receipt.
    The contract checks the scope (UnsupportedScope) and the 1-365 day range (InvalidDuration); here only
    values that cannot be encoded (scope outside 0-255, days outside 0-65535) are a ValueError.
    Raise TransactionRejected on revert. Do not independently mint rewards in Python; the manager owns that atomic action.
    """
    guardian = _address(guardian)
    requester = _address(requester)
    arguments = (requester, _scope(scope), _duration(duration_days))
    return _transact(manager, "grantConsent", arguments, guardian)


def revoke_consent(manager: Any, guardian: str, requester: str, scope: Scope) -> Receipt:
    """Submit caller-owned revocation and await its receipt.
    Do not modify the lifetime rewarded flag or another owner's consent. An unsupported scope reverts
    UnsupportedScope in the contract.
    """
    guardian = _address(guardian)
    requester = _address(requester)
    return _transact(manager, "revokeConsent", (requester, _scope(scope)), guardian)


def check_access(manager: Any, owner: str, requester: str, scope: Scope) -> ConsentDecision:
    """Read current registration/evidence/consent permission without releasing any data.
    This view supports the final recheck but never replaces the logged access transaction.
    An unsupported scope is the contract's answer (False, UNSUPPORTED_SCOPE), not an exception.
    """
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
    """Read the stored grant for (owner, requester, scope): its exclusive expiry and revoked flag.
    expires_at == 0 means never granted. For display and the demo's exact-expiry step; it never
    grants anything by itself.
    """
    owner = _address(owner)
    requester = _address(requester)
    scope_code = _scope(scope)
    with _rpc_errors():
        expires_at, revoked = _view(manager, "getConsent", (owner, requester, scope_code))
        return {"expires_at": int(expires_at), "revoked": bool(revoked)}


def request_access(manager: Any, requester: str, owner: str, scope: Scope, observed_hash: bytes) -> AccessAttempt:
    """Submit requestAccess from the requester, wait for its receipt and decode the matching event.
    Require expected owner/requester/scope and correct emitter. Business denial is allowed=false,
    not a reverted transaction; a missing or wrong event is ChainUnavailable and a reverted receipt
    TransactionRejected, both of which the caller treats as unavailable.
    The scope is not checked against 1 and 2, so any uint8 can be logged as a denial.

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
    """Wait with a bounded timeout and return status, gas, block, logs and any new contract address.
    A hash/timeout/pending submission must never be presented as committed success.
    Status 0 raises TransactionRejected("Reverted"). The transaction was already sent, so no receipt in
    time, and a node that stops answering (or goes down) while Python waits, raise TransactionPending,
    not ChainUnavailable: it may still be mined, so nothing is known either way.
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
    """Decode the exact AccessAttempt log emitted by ConsentManager.
    Validate emitter and fields; no log means no permission. Record transaction hash for audit display.
    Decodes with the ABI of the manager already loaded by load_contract, via
    manager.events.AccessAttempt().process_receipt. process_receipt does not filter by address, so keep
    only logs whose address equals the manager's, discard other events quietly, and require exactly one.
    """
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
    """Read reward units for display only. The balance must never be used to bypass consent.
    A revert is TransactionRejected like any other call, not ChainUnavailable.

    """
    account = _address(account)
    with _rpc_errors():
        return int(_view(token, "balanceOf", (account,)))


def list_access_events(manager: Any, from_block: int) -> list[AccessAttempt]:
    """Query bounded AccessAttempt events and display minimal audit metadata.
    No delete-log operation is part of the design; no medical JSON belongs in these events.
    Keep unknown scope codes as int; display as 'scope 3 (unsupported)'; never raise.

    """
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
    """Solidity error name for revert data (a 0x-hex string, or a dict holding one under "data").
    "Panic" for a Solidity panic, the name from the compiled ABIs for a custom error, else "Reverted".
    """
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
    """Turn node, transport and web3 failures into ChainUnavailable, TransactionRejected or
    TransactionPending. Any other exception (a KeyError, a TypeError, an ABI function that does not
    exist) is a bug in the calling code, not the node, and goes up unchanged.
    """
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
    """The model exception for one web3, transport or decoding failure, or None when error is none of
    those. Messages carry no details.
    """
    # requests' ConnectionError and Timeout are OSErrors, like a refused or reset socket
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
    # BadFunctionCallOutput: no contract, or another one, answers at the address
    node_errors = (
        BadFunctionCallOutput, BadResponseFormat, CannotHandleRequest, MultipleFailedRequests,
        ProviderConnectionError, TooManyRequests,
    )
    if isinstance(error, node_errors):
        return ChainUnavailable("RPC request failed")
    return None


def _rpc_revert_name(error: Any) -> str | None:
    # Hardhat 3 mines a transaction that reverts at send time and answers eth_sendTransaction with
    # {"code": 3, "message": "...reverted with custom error 'ZeroHash()'", "data": "0xf1ae58d5"}
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
        # nothing can revert without web3 anyway
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
    # canonical type for a selector: tuples are spelled out, array suffixes kept
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
            # without that artifact its errors just stay "Reverted"
            continue


def _deploy_block(value: Any) -> tuple[int, str]:
    """(number, lowercase hash) from deployment.json's deploy_block; anything else is a ValueError."""
    number, recorded = value["number"], value["hash"]
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        raise ValueError("invalid deploy block number")
    if not isinstance(recorded, str) or len(recorded) != 66 or not recorded.startswith("0x"):
        raise ValueError("invalid deploy block hash")
    int(recorded, 16)
    return number, recorded.lower()


def _address(value: Any) -> str:
    """Checksum an address given in any case; anything that is not an address is a caller mistake."""
    from web3 import Web3

    if not isinstance(value, str):
        raise ValueError("invalid address")
    try:
        return Web3.to_checksum_address(value)
    except (TypeError, ValueError):
        raise ValueError("invalid address") from None


def _scope(value: Any) -> int:
    # any uint8 goes to the contract, which decides what is supported
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ValueError("scope must be a whole number from 0 to 255")
    return int(value)


def _duration(value: Any) -> int:
    # any uint16 goes to the contract, which enforces 1-365 days
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        raise ValueError("duration must be a whole number of days from 0 to 65535")
    return int(value)


def _require_hash(value: Any, label: str) -> None:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ValueError(f"{label} must be 32 bytes")
