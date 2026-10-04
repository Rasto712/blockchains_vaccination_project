# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""What the web UI does, without any HTTP: one state snapshot for the page and one function per action.
ui/server.py turns them into JSON endpoints.

Everything goes through the existing modules (app.chain, app.disclosure, app.records, scripts.deploy_local),
so privacy rules and error handling stay in one place. Nothing here hashes, discloses or calls web3 itself.
The page sends register, attest, grant and revoke itself with Viem (ui/static/chain.js).

Every action returns a result {status, message, reason, tx, fields, details}. Exception text is never passed
on, only a Solidity error name, a fixed message, or "failed: <TypeName>" for a bug.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from app import chain, disclosure, main, records
from app.models import (
    Scope, Reason, AccessResponse, ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, ArtifactUnavailable,
    TransactionRejected, TransactionPending, OUTCOME_ALLOWED, OUTCOME_DENIED, OUTCOME_UNAVAILABLE,
)
from integration import demo_workflow
from scripts import deploy_local

# same text the console shows when the node is down or on another chain
NODE_UNAVAILABLE_MESSAGE = "unavailable: local node not reachable or wrong chain"
REQUESTERS = ("school", "doctor")
SCOPES = (Scope.MEASLES_STATUS, Scope.VACCINATION_SCHEDULE)
MAX_DAYS = 365


class InvalidRequest(Exception):
    """The request is wrong (unknown role, requester or scope, or bad day count). Nothing was sent."""


class Forbidden(Exception):
    """This role is not allowed to see this. Demo mode, so this is not real authentication."""


def result(
    status: str, message: str, reason: str = "", tx: str = "", fields: dict[str, Any] | None = None, **details: Any,
) -> dict[str, Any]:
    """One action's answer, in the shape the page shows."""
    return {"status": status, "message": message, "reason": reason, "tx": tx, "fields": fields or {}, "details": details}


def error_result(error: BaseException) -> dict[str, Any]:
    """Turn an exception into a result using the console's texts. For a bug only the type is shown."""
    if isinstance(error, TransactionRejected):
        return result("rejected", f"rejected: {error.args[0] if error.args else 'reverted'}")
    if isinstance(error, TransactionPending):
        return result("pending", "pending: not confirmed")
    if isinstance(error, ArtifactUnavailable):
        return result("unavailable", f"unavailable: {disclosure.NO_ARTIFACTS_MESSAGE}")
    if isinstance(error, DeploymentUnavailable):
        return result("unavailable", f"unavailable: {disclosure.NO_DEPLOYMENT_MESSAGE}")
    if isinstance(error, Web3NotInstalled):
        return result("unavailable", f"unavailable: {disclosure.NO_WEB3_MESSAGE}")
    if isinstance(error, ChainUnavailable):
        return result("unavailable", NODE_UNAVAILABLE_MESSAGE)
    if isinstance(error, records.RecordError):
        # these messages never contain data, salts or paths
        return result("unavailable", f"unavailable: {error}")
    if isinstance(error, deploy_local.DeployError):
        return result("unavailable", f"unavailable: {_deploy_message(error)}")
    log_failure(error)
    return result("failed", f"failed: {type(error).__name__}")


def perform(action: Any, *arguments: Any) -> dict[str, Any]:
    """Run one action and turn any error into a result. InvalidRequest and Forbidden go up to the server."""
    try:
        return action(*arguments)
    except (InvalidRequest, Forbidden):
        raise
    except Exception as error:
        return error_result(error)


def log_failure(error: BaseException) -> None:
    # log only the type, the text could contain local data
    print(f"ui: failed: {type(error).__name__}", file=sys.stderr, flush=True)


def state(settings: dict[str, Any], chain_views: bool = True) -> dict[str, Any]:
    """Everything the page shows, read fresh. Local files first (they work without a node), then the chain.
    The first chain error stops the chain part. Only public values: never the card, a salt or a path.
    chain_views=False skips the contract views, because the page reads those itself with Viem.
    """
    snapshot: dict[str, Any] = {
        "roles": list(settings["actor_account_indices"]),
        "registering": list(records.REGISTERING_LABELS),
        "requesters": list(REQUESTERS),
        "scopes": [scope.name for scope in SCOPES],
        "max_days": MAX_DAYS,
        "local": _local_state(settings),
        "node": {"reachable": False, "chain_id": None, "time": None, "time_text": "", "message": ""},
        "deployment": {"deployed": False, "message": "", "contracts": {}, "deploy_block": ""},
        "accounts": {},
        "registrations": {},
        "record": None,
        "consents": [],
        "rewards": {},
        "audit": [],
    }
    node, deployment = snapshot["node"], snapshot["deployment"]
    try:
        client = chain.connect(settings)
    except Exception as error:
        node["message"] = error_result(error)["message"]
        return snapshot
    try:
        # connect already checked the chain ID
        now = demo_workflow._block_timestamp(client, "latest")
        node.update(reachable=True, chain_id=settings["expected_chain_id"], time=now, time_text=_utc(now))
        snapshot["accounts"] = {label: chain.select_account(client, label, settings) for label in snapshot["roles"]}
        try:
            contracts = {name: main.contract(client, name, settings) for name in chain.CONTRACT_NAMES}
        except DeploymentUnavailable as error:
            # includes the case where the node answers but nothing usable is deployed
            deployment["message"] = error_result(error)["message"]
            return snapshot
        deployment.update(
            deployed=True, contracts={name: contract.address for name, contract in contracts.items()},
            deploy_block=_recorded_deploy_block(settings),
        )
        if chain_views:
            snapshot.update(_contract_state(contracts, snapshot["accounts"], snapshot["local"], now))
    except Exception as error:
        # the node stopped answering during the snapshot
        node.update(reachable=False, message=error_result(error)["message"])
        deployment.update(deployed=False, contracts={}, deploy_block="")
    return snapshot


def contracts(settings: dict[str, Any], rpc_url: str) -> dict[str, Any]:
    """What the page's Viem code needs: contract addresses and ABIs, role accounts, scope and reason codes.
    Only served after the deployment was checked against the node.
    """
    client = chain.connect(settings)
    accounts = {label: chain.select_account(client, label, settings) for label in settings["actor_account_indices"]}
    loaded = {name: main.contract(client, name, settings) for name in chain.CONTRACT_NAMES}
    return result(
        "ok", "contracts checked against this node",
        # connect already checked the chain ID
        chain_id=settings["expected_chain_id"], rpc_url=rpc_url, deploy_block=_recorded_deploy_block(settings),
        contracts={name: {"address": contract.address, "abi": contract.abi} for name, contract in loaded.items()},
        accounts=accounts,
        scopes={scope.name: int(scope) for scope in SCOPES},
        reasons={reason.name: int(reason) for reason in Reason},
    )


def identity_hash(settings: dict[str, Any], role: Any) -> dict[str, Any]:
    """The salted identity hash of a role, which the page sends with registerUser. Only the hash."""
    role = _role(settings, role)
    if role not in records.REGISTERING_LABELS:
        raise InvalidRequest("only guardian, school and doctor register")
    value = records.prepare_identity(*records.identity_paths(settings, role))
    return result("ok", f"identity hash of {role}", identity_hash=_hex(value))


def record_commitment(settings: dict[str, Any]) -> dict[str, Any]:
    """The commitment of the guardian's local record. Only the commitment, not the card."""
    commitment = main.record_snapshot(settings)["commitment"]
    return result("ok", "commitment of the guardian's local record", commitment=_hex(commitment))


def setup(settings: dict[str, Any]) -> dict[str, Any]:
    """Create the missing local files and salts. No paths are shown."""
    created = records.setup_runtime(settings)
    if not created:
        return result("ok", "setup: every local file already exists, nothing changed")
    return result("ok", f"setup: created {len(created)} local file{'' if len(created) == 1 else 's'}")


def deploy(settings_path: Path) -> dict[str, Any]:
    """Deploy fresh contracts like deploy_local --reset. Old registrations and grants are left behind."""
    outcome = deploy_local.run(settings_path, reset=True)
    return result(
        "ok", "deployed fresh contracts: register and attest again",
        contracts=dict(outcome["addresses"]),
        transactions={name: receipt["transaction_hash"] for name, receipt in outcome["receipts"].items()},
        deploy_block=str(outcome["deploy_block"]["hash"]).lower(),
    )


def register(settings: dict[str, Any], role: Any) -> dict[str, Any]:
    """registerUser from the role's account with its salted identity hash."""
    role = _role(settings, role)
    if role not in records.REGISTERING_LABELS:
        raise InvalidRequest("only guardian, school and doctor register")
    identity_hash = records.prepare_identity(*records.identity_paths(settings, role))
    client = chain.connect(settings)
    account = chain.select_account(client, role, settings)
    receipt = chain.register_user(main.contract(client, "IdentityRegistry", settings), account=account, identity_hash=identity_hash)
    return result("ok", f"registered {role} with identity hash {_hex(identity_hash)}", tx=receipt["transaction_hash"])


def attest(settings: dict[str, Any], role: Any) -> dict[str, Any]:
    """registerVaccination for the guardian's record. Offered to every role on purpose: the registry
    refuses anyone but the trusted clinic (NotTrustedClinic).
    """
    role = _role(settings, role)
    commitment = main.record_snapshot(settings)["commitment"]
    client = chain.connect(settings)
    sender = chain.select_account(client, role, settings)
    guardian = chain.select_account(client, "guardian", settings)
    receipt = chain.register_vaccination(
        main.contract(client, "IdentityRegistry", settings), clinic=sender, guardian=guardian, record_hash=commitment,
    )
    return result("ok", f"attested the guardian's record as {_hex(commitment)}", tx=receipt["transaction_hash"])


def grant(settings: dict[str, Any], role: Any, requester: Any, scope: Any, days: Any) -> dict[str, Any]:
    """grantConsent from the role's account. Values are checked before any chain call. The reward, if any,
    is shown as before -> after.
    """
    role = _role(settings, role)
    requester = _requester(settings, requester, role)
    scope = _scope(scope)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise InvalidRequest(f"days must be a whole number from 1 to {MAX_DAYS}")
    client = chain.connect(settings)
    guardian = chain.select_account(client, role, settings)
    requester_address = chain.select_account(client, requester, settings)
    token = main.contract(client, "ConsentRewardToken", settings)
    manager = main.contract(client, "ConsentManager", settings)
    before = chain.get_reward_balance(token, account=guardian)
    receipt = chain.grant_consent(manager, guardian=guardian, requester=requester_address, scope=scope, duration_days=days)
    after = chain.get_reward_balance(token, account=guardian)
    expires_at = chain.get_consent(manager, owner=guardian, requester=requester_address, scope=scope)["expires_at"]
    return result(
        "ok",
        f"granted {requester} {scope.name} for {days} day{'' if days == 1 else 's'}; "
        f"reward balance of {role}: {before} -> {after}",
        tx=receipt["transaction_hash"], reward_before=before, reward_after=after,
        expires_at=expires_at, expires_text=_utc(expires_at),
    )


def revoke(settings: dict[str, Any], role: Any, requester: Any, scope: Any) -> dict[str, Any]:
    """revokeConsent from the role's account. Revoking something never granted reverts; revoking twice
    does nothing and the message says so.
    """
    role = _role(settings, role)
    requester = _requester(settings, requester, role)
    scope = _scope(scope)
    client = chain.connect(settings)
    guardian = chain.select_account(client, role, settings)
    requester_address = chain.select_account(client, requester, settings)
    manager = main.contract(client, "ConsentManager", settings)
    already = chain.get_consent(manager, owner=guardian, requester=requester_address, scope=scope)["revoked"]
    receipt = chain.revoke_consent(manager, guardian=guardian, requester=requester_address, scope=scope)
    note = ": it was already revoked, nothing changed" if already else ""
    return result("ok", f"revoked {requester} {scope.name}{note}", tx=receipt["transaction_hash"])


def school_check(settings: dict[str, Any]) -> dict[str, Any]:
    """disclosure.verify_for_school, always signed as the school."""
    return _access(settings, disclosure.verify_for_school)


def doctor_view(settings: dict[str, Any]) -> dict[str, Any]:
    """disclosure.get_doctor_schedule, always signed as the doctor."""
    return _access(settings, disclosure.get_doctor_schedule)


def local_record(settings: dict[str, Any], role: Any) -> dict[str, Any]:
    """The guardian's own card as parsed fields, only for the guardian's view. Never the raw file, salt or path."""
    if role != "guardian":
        raise Forbidden("only the guardian's own view shows the local record")
    card = main.record_snapshot(settings)["card"]
    return result("ok", "the local record (only the guardian's view shows it)", card=card)


def access_result(response: AccessResponse) -> dict[str, Any]:
    """The result for a disclosure answer, with the console's texts."""
    outcome = response["outcome"]
    if outcome == OUTCOME_ALLOWED:
        fields = response["fields"]
        return result(OUTCOME_ALLOWED, f"allowed: {_released(fields)}", response["reason"], response["transaction_hash"], fields)
    reason = response["reason"] if outcome == OUTCOME_DENIED else ""
    return result(outcome, disclosure.denial_message(response), reason, response["transaction_hash"])


def _access(settings: dict[str, Any], request: Any) -> dict[str, Any]:
    # the owner is the guardian's address. If the node fails during this lookup, answer "unavailable"
    # and release nothing; the setup hints keep their own messages
    try:
        client = chain.connect(settings)
        owner = chain.select_account(client, "guardian", settings)
    except Web3NotInstalled:
        raise
    except ChainUnavailable:
        return access_result(disclosure.format_denial(OUTCOME_UNAVAILABLE))
    return access_result(request(settings, owner))


def _recorded_deploy_block(settings: dict[str, Any]) -> str:
    # the block hash deploy_local saved. It tells deployments apart even at the same addresses
    # (a restarted node reuses the deployer's nonces)
    try:
        with open(records.settings_path(settings, "deployment_file"), "rb") as file:
            return str(json.load(file)["deploy_block"]["hash"]).lower()
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _deploy_message(error: BaseException) -> str:
    # keep only the first line (it skips the "nothing was saved: <file>" note) and use fixed texts for
    # messages that name a file
    first = str(error).splitlines()[0] if str(error) else "deploy failed"
    if first.startswith("could not write"):
        return "deployed, but the deployment file could not be saved: run python -m scripts.deploy_local --reset"
    if first.startswith("no settings file") or first.endswith(("is not valid JSON", "is not a settings object")):
        return "the settings file is missing or not valid JSON"
    return first


def _local_state(settings: dict[str, Any]) -> dict[str, Any]:
    # check each file separately so one broken file does not hide the others; show the first error
    local: dict[str, Any] = {"ready": True, "message": "", "identity_hashes": {}, "commitment": None}

    def load(key: str, read: Any) -> None:
        try:
            value = _hex(read())
        except records.RecordError as error:
            if local["ready"]:
                local.update(ready=False, message=f"unavailable: {error}")
            return
        if key == "commitment":
            local["commitment"] = value
        else:
            local["identity_hashes"][key] = value

    for label in records.REGISTERING_LABELS:
        load(label, lambda: records.prepare_identity(*records.identity_paths(settings, label)))
    load("commitment", lambda: main.record_snapshot(settings)["commitment"])
    return local


def _contract_state(contracts: dict[str, Any], accounts: dict[str, str], local: dict[str, Any], now: int) -> dict[str, Any]:
    # every chain view the page shows: all of it, or an exception
    registry, manager = contracts["IdentityRegistry"], contracts["ConsentManager"]
    token = contracts["ConsentRewardToken"]

    registrations = {}
    for label in records.REGISTERING_LABELS:
        info = chain.get_user_info(registry, account=accounts[label])
        onchain = _hex(info["identity_hash"]) if info["registered"] else ""
        mine = local["identity_hashes"].get(label)
        registrations[label] = {
            "registered": info["registered"], "identity_hash": onchain,
            "matches": onchain == mine if onchain and mine else None,
        }
    guardian = accounts["guardian"]
    evidence = chain.get_user_info(registry, account=guardian)["vaccination_hash"]
    attested = evidence != disclosure.ZERO_HASH
    record = {
        "attested": attested, "onchain": _hex(evidence) if attested else "",
        "matches": _hex(evidence) == local["commitment"] if attested and local["commitment"] else None,
    }

    consents = []
    for requester in REQUESTERS:
        for scope in SCOPES:
            consent = chain.get_consent(manager, owner=guardian, requester=accounts[requester], scope=scope)
            expires_at = consent["expires_at"]
            consents.append({
                "requester": requester, "scope": scope.name, "status": _consent_status(consent, now),
                "expires_at": expires_at, "expires_text": _utc(expires_at) if expires_at else "",
            })

    names = {address.lower(): label for label, address in accounts.items()}
    audit = [
        {
            "time": event["timestamp"], "time_text": _utc(event["timestamp"]),
            "requester": names.get(event["requester"].lower(), event["requester"]),
            "scope": main.scope_name(event["scope"]), "allowed": event["allowed"],
            "reason": Reason(event["reason"]).name, "tx": event["transaction_hash"],
        }
        for event in reversed(chain.list_access_events(manager, from_block=0))
    ]
    return {
        "registrations": registrations,
        "record": record,
        "consents": consents,
        "rewards": {label: chain.get_reward_balance(token, account=address) for label, address in accounts.items()},
        "audit": audit,
    }


def _consent_status(consent: dict[str, Any], now: int) -> str:
    # same order as the contract: never granted, revoked, expired, active
    if consent["expires_at"] == 0:
        return "none"
    if consent["revoked"]:
        return "revoked"
    if now >= consent["expires_at"]:
        return "expired"
    return "active"


def _role(settings: dict[str, Any], value: Any) -> str:
    if not isinstance(value, str) or value not in settings["actor_account_indices"]:
        raise InvalidRequest("unknown role")
    return value


def _requester(settings: dict[str, Any], value: Any, role: str) -> str:
    if not isinstance(value, str) or value not in settings["actor_account_indices"] or value == role:
        raise InvalidRequest("unknown requester")
    return value


def _scope(value: Any) -> Scope:
    if not isinstance(value, str) or value not in Scope.__members__:
        raise InvalidRequest("unknown scope")
    return Scope[value]


def _released(fields: dict[str, Any]) -> str:
    # same wording as the console
    if "measles_status" in fields:
        return f"measles status {fields['measles_status']}"
    return ", ".join(f"{event['vaccine']} on {event['date']}" for event in fields["vaccinations"])


def _hex(value: bytes) -> str:
    return f"0x{bytes(value).hex()}"


def _utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
