# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Decides what a requester is allowed to see and builds the response.
A school must never get the full vaccination card.
"""
from typing import Any
from app import chain, records
from app.models import (
    VaccinationCard, AccessResponse, Scope, Reason, RecordSnapshot,
    ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, TransactionRejected, TransactionPending,
    OUTCOME_ALLOWED, OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING,
)

NOT_IMPLEMENTED_MESSAGE = "not implemented"
NO_DEPLOYMENT_MESSAGE = "no deployment for this node: run python -m scripts.deploy_local --reset"
NO_ARTIFACTS_MESSAGE = "compiled contracts not found: run npm run compile first"
NO_WEB3_MESSAGE = "web3 not installed: run python -m pip install -r requirements.txt with the venv's Python"
# messages for the unavailable outcome:
# first when the local record did not verify, second when the node or receipt failed
RECORD_UNAVAILABLE_MESSAGE = "unavailable: local record could not be verified, not a clinical result"
CHAIN_UNAVAILABLE_MESSAGE = "unavailable: local node or receipt problem, nothing released"
ZERO_HASH = bytes(32)


def select_school_status(card: VaccinationCard) -> dict[str, Any]:
    """Only a 'verified' measles status, nothing else from the card.
    Verified means a clinic attested an MMR record, not that the child is immune.
    """
    if not any(event["vaccine"] == "MMR" and "measles" in event["covers"] for event in card["vaccinations"]):
        raise ValueError("card has no MMR vaccination covering measles")
    return {"measles_status": "verified"}


def select_doctor_schedule(card: VaccinationCard) -> dict[str, Any]:
    """Only the vaccine name and date of each vaccination."""
    return {"vaccinations": [{"vaccine": event["vaccine"], "date": event["date"]} for event in card["vaccinations"]]}


def perform_access(settings: dict[str, Any], requester_label: str, owner: str, scope: Scope) -> AccessResponse:
    """Run one full access request: log it on-chain, check the record and consent, then release only the allowed fields.
    Nothing is released if anything fails. If consent changed before the final recheck, a second request
    is sent so the late denial is logged too. owner is the guardian's 0x address.
    """
    if requester_label not in settings["actor_account_indices"]:
        return format_denial(OUTCOME_UNAVAILABLE)
    try:
        return _release(settings, requester_label, owner, scope)
    except (DeploymentUnavailable, Web3NotInstalled):
        raise
    except TransactionPending:
        return format_denial(OUTCOME_PENDING)
    except (ChainUnavailable, TransactionRejected):
        return format_denial(OUTCOME_UNAVAILABLE)


def verify_for_school(settings: dict[str, Any], owner: str) -> AccessResponse:
    """Access request as the school for the measles status."""
    return perform_access(settings, "school", owner, Scope.MEASLES_STATUS)


def get_doctor_schedule(settings: dict[str, Any], owner: str) -> AccessResponse:
    """Access request as the doctor for the vaccination schedule."""
    return perform_access(settings, "doctor", owner, Scope.VACCINATION_SCHEDULE)


def format_denial(outcome: str, reason: str = "", transaction_hash: str = "") -> AccessResponse:
    """Build a denied/unavailable/pending response with no health data."""
    if outcome not in (OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING):
        raise ValueError("format_denial is only for denied, unavailable or pending")
    if isinstance(reason, int):
        reason = Reason(reason).name
    if reason and reason not in Reason.__members__:
        raise ValueError("unknown reason")
    return {"outcome": outcome, "fields": {}, "reason": reason, "transaction_hash": transaction_hash}


def denial_message(response: AccessResponse) -> str:
    """Console text for a response that was not allowed."""
    outcome = response["outcome"]
    if outcome == OUTCOME_DENIED:
        message = f"denied: {response['reason']}" if response["reason"] else "denied"
        if response["transaction_hash"]:
            message += f" (logged on-chain in {response['transaction_hash']})"
        return message
    if outcome == OUTCOME_UNAVAILABLE:
        return RECORD_UNAVAILABLE_MESSAGE if response["transaction_hash"] else CHAIN_UNAVAILABLE_MESSAGE
    if outcome == OUTCOME_PENDING:
        return "pending: not confirmed, nothing released"
    raise ValueError("denial_message is only for denied, unavailable or pending")


def _release(settings: dict[str, Any], requester_label: str, owner: str, scope: Scope) -> AccessResponse:
    # steps: connect, read record, log request, check event, check hash, recheck consent, pick fields
    client = chain.connect(settings)
    requester = chain.select_account(client, requester_label, settings)
    guardian = chain.select_account(client, "guardian", settings)
    deployment = records.settings_path(settings, "deployment_file")
    registry = chain.load_contract(client, "IdentityRegistry", deployment)
    manager = chain.load_contract(client, "ConsentManager", deployment)

    snapshot = _snapshot_for(settings, owner, guardian)
    observed_hash = snapshot["commitment"] if snapshot else ZERO_HASH
    event = chain.request_access(manager, requester=requester, owner=owner, scope=scope, observed_hash=observed_hash)
    if snapshot is None:
        # we sent the zero hash ourselves, so this is not a real answer
        return format_denial(OUTCOME_UNAVAILABLE, event["reason"], event["transaction_hash"])
    if not event["allowed"]:
        return format_denial(OUTCOME_DENIED, event["reason"], event["transaction_hash"])

    registered = chain.get_user_info(registry, account=owner)["vaccination_hash"]
    if registered == ZERO_HASH or registered != snapshot["commitment"]:
        return format_denial(OUTCOME_UNAVAILABLE, transaction_hash=event["transaction_hash"])

    decision = chain.check_access(manager, owner=owner, requester=requester, scope=scope)
    if not decision["allowed"]:
        # log the late denial too, then release nothing
        late = chain.request_access(manager, requester=requester, owner=owner, scope=scope, observed_hash=observed_hash)
        reason = decision["reason"] if late["allowed"] else late["reason"]
        return format_denial(OUTCOME_DENIED, reason, late["transaction_hash"])

    if scope == Scope.MEASLES_STATUS:
        fields = select_school_status(snapshot["card"])
    elif scope == Scope.VACCINATION_SCHEDULE:
        fields = select_doctor_schedule(snapshot["card"])
    else:
        return format_denial(OUTCOME_UNAVAILABLE, transaction_hash=event["transaction_hash"])
    return {"outcome": OUTCOME_ALLOWED, "fields": fields, "reason": Reason.ALLOWED.name, "transaction_hash": event["transaction_hash"]}


def _snapshot_for(settings: dict[str, Any], owner: str, guardian: str) -> RecordSnapshot | None:
    # we only have the guardian's record locally
    if owner.lower() != guardian.lower():
        return None
    try:
        return records.load_snapshot(
            records.settings_path(settings, "vaccination_file"),
            records.settings_path(settings, "vaccination_salt_file"),
        )
    except records.RecordError:
        return None
