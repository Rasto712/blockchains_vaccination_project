# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Permitted output projection and the local disclosure boundary.
A school must never receive the full vaccination card.

Chain calls go through "from app import chain" only, so tests can swap in a fake in one place.
"""
from typing import Any
from app import chain, records
from app.models import (
    VaccinationCard, AccessResponse, Scope, Reason, RecordSnapshot,
    ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, TransactionRejected, TransactionPending,
    OUTCOME_ALLOWED, OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING,
)

# console text for NotImplementedError only, never an outcome
NOT_IMPLEMENTED_MESSAGE = "not implemented"
# console text for DeploymentUnavailable only: the node answered, but nothing usable is deployed on it
NO_DEPLOYMENT_MESSAGE = "no deployment for this node: run python -m scripts.deploy_local --reset"
# console text for ArtifactUnavailable (a DeploymentUnavailable): deploying would fail the same way
NO_ARTIFACTS_MESSAGE = "compiled contracts not found: run npm run compile first"
# console text for Web3NotInstalled: the node may well be running
NO_WEB3_MESSAGE = "web3 not installed: run python -m pip install -r requirements.txt with the venv's Python"
# denial_message for unavailable: with a logged request the local record did not verify against it;
# without one nothing was confirmed on-chain, because the node, the receipt or the event failed
RECORD_UNAVAILABLE_MESSAGE = "unavailable: local record could not be verified, not a clinical result"
CHAIN_UNAVAILABLE_MESSAGE = "unavailable: local node or receipt problem, nothing released"
ZERO_HASH = bytes(32)


def select_school_status(card: VaccinationCard) -> dict[str, Any]:
    """Produce only a positive verified-status field for the supported MMR/measles fixture.
    Call only after trusted evidence and consent checks. Missing evidence is unavailable,
    not a clinical NO; do not include vaccine dates, clinic, batch, child name or file path.
    Shape: {"measles_status": "verified"}.
    Verified means a clinic-attested MMR record exists, not that the child is immune.
    """
    if not any(event["vaccine"] == "MMR" and "measles" in event["covers"] for event in card["vaccinations"]):
        raise ValueError("card has no MMR vaccination covering measles")
    return {"measles_status": "verified"}


def select_doctor_schedule(card: VaccinationCard) -> dict[str, Any]:
    """Return only vaccine/date fields from the validated authorized card.
    Do not return the original dictionary, raw bytes, coverage list, clinic, batch or salt.
    Shape: {"vaccinations": [{"vaccine": ..., "date": ...}]}.
    """
    return {"vaccinations": [{"vaccine": event["vaccine"], "date": event["date"]} for event in card["vaccinations"]]}


def perform_access(settings: dict[str, Any], requester_label: str, owner: str, scope: Scope) -> AccessResponse:
    """Coordinate one complete request using records.py and chain.py.
    owner is the guardian's 0x address, not a label.
    Read/hash/parse one snapshot; submit observed hash (zero if local data is unavailable);
    await and validate the access event; compare with registered evidence; recheck current consent;
    then project only the permitted fields from the same bytes. If the final recheck fails, submit one
    more requestAccess so the late denial is logged. Release nothing on denial,
    network failure, missing event or changed authorization. Tokens do not move during access.
    When Python itself submitted the zero hash, show outcome unavailable (not a clinical NO) whatever reason the event carries.
    An unknown requester label is unavailable with no transaction. Chain errors become unavailable or pending;
    an unavailable response from a chain error has no transaction hash, so denial_message blames the node or
    receipt, not the local record. NotImplementedError, DeploymentUnavailable (ArtifactUnavailable included)
    and Web3NotInstalled are passed on, so the console can say "not implemented" or how to deploy,
    compile or install web3 instead. None of them releases anything.
    """
    if requester_label not in settings["actor_account_indices"]:
        return format_denial(OUTCOME_UNAVAILABLE)
    try:
        return _release(settings, requester_label, owner, scope)
    except (DeploymentUnavailable, Web3NotInstalled):
        # a setup problem, not an answer about this record
        raise
    except TransactionPending:
        return format_denial(OUTCOME_PENDING)
    except (ChainUnavailable, TransactionRejected):
        return format_denial(OUTCOME_UNAVAILABLE)


def verify_for_school(settings: dict[str, Any], owner: str) -> AccessResponse:
    """Invoke the access workflow under the SCHOOL account and MEASLES_STATUS scope.
    owner is the guardian's 0x address, not a label.
    Return a status-only success or a denied/unavailable result with no health payload.
    """
    return perform_access(settings, "school", owner, Scope.MEASLES_STATUS)


def get_doctor_schedule(settings: dict[str, Any], owner: str) -> AccessResponse:
    """Invoke the access workflow under the DOCTOR account and VACCINATION_SCHEDULE scope.
    owner is the guardian's 0x address, not a label.
    Return allowlisted schedule fields only after every check and committed event succeeds.
    """
    return perform_access(settings, "doctor", owner, Scope.VACCINATION_SCHEDULE)


def format_denial(outcome: str, reason: str = "", transaction_hash: str = "") -> AccessResponse:
    """Build a nonclinical denied/unavailable response with an empty fields object.
    Do not echo exceptions containing medical JSON, secrets or local filesystem paths.
    reason is a Reason name (a Reason member or code is turned into its name) or "" when there is no event.
    """
    if outcome not in (OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING):
        raise ValueError("format_denial is only for denied, unavailable or pending")
    if isinstance(reason, int):
        reason = Reason(reason).name
    if reason and reason not in Reason.__members__:
        raise ValueError("unknown reason")
    return {"outcome": outcome, "fields": {}, "reason": reason, "transaction_hash": transaction_hash}


def denial_message(response: AccessResponse) -> str:
    """Console text for a denied, unavailable or pending response. Never shows fields or exception text.
    Unavailable with a transaction hash means a request was logged but the local record (or its salt, or
    the registered commitment) did not verify; without one, nothing was confirmed on-chain.
    """
    outcome = response["outcome"]
    if outcome == OUTCOME_DENIED:
        message = f"denied: {response['reason']}" if response["reason"] else "denied"
        if response["transaction_hash"]:
            message += f" (logged on-chain in {response['transaction_hash']})"
        return message
    if outcome == OUTCOME_UNAVAILABLE:
        # the event's reason is never shown, e.g. when Python itself sent the zero hash
        return RECORD_UNAVAILABLE_MESSAGE if response["transaction_hash"] else CHAIN_UNAVAILABLE_MESSAGE
    if outcome == OUTCOME_PENDING:
        return "pending: not confirmed, nothing released"
    raise ValueError("denial_message is only for denied, unavailable or pending")


def _release(settings: dict[str, Any], requester_label: str, owner: str, scope: Scope) -> AccessResponse:
    # the release rule, in order: session, snapshot, logged request, event, commitment, recheck, view
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
        # Python sent the zero hash itself, so this is never a clinical answer
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
    # only the configured guardian's record is held locally; anything else sends the zero hash
    if owner.lower() != guardian.lower():
        return None
    try:
        return records.load_snapshot(
            records.settings_path(settings, "vaccination_file"),
            records.settings_path(settings, "vaccination_salt_file"),
        )
    except records.RecordError:
        return None
