"""Developer 3: permitted output projection and the local disclosure boundary.
Coordinate helper results with Developer 4. A school must never receive the full vaccination card.

Methods not written yet raise NotImplementedError rather than returning fake data
or pretending that authorisation has succeeded.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
from pathlib import Path
from typing import Any
from app.models import (
    VaccinationCard, AccessResponse, Scope, Reason,
    OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING,
)

# console text for NotImplementedError only, never an outcome
NOT_IMPLEMENTED_MESSAGE = "not implemented yet"


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

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("perform_access is an implementation task; see docs/tasks.")


def verify_for_school(settings: dict[str, Any], owner: str) -> AccessResponse:
    """Invoke the access workflow under the SCHOOL account and MEASLES_STATUS scope.
    owner is the guardian's 0x address, not a label.
    Return a status-only success or a denied/unavailable result with no health payload.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("verify_for_school is an implementation task; see docs/tasks.")


def get_doctor_schedule(settings: dict[str, Any], owner: str) -> AccessResponse:
    """Invoke the access workflow under the DOCTOR account and VACCINATION_SCHEDULE scope.
    owner is the guardian's 0x address, not a label.
    Return allowlisted schedule fields only after every check and committed event succeeds.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("get_doctor_schedule is an implementation task; see docs/tasks.")


def format_denial(outcome: str, reason: str = "", transaction_hash: str = "") -> AccessResponse:
    """Build a nonclinical denied/unavailable response with an empty fields object.
    Do not echo exceptions containing medical JSON, secrets or local filesystem paths.
    reason is a Reason name (a Reason member is turned into its name) or "" when there is no event.
    """
    if outcome not in (OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING):
        raise ValueError("format_denial is only for denied, unavailable or pending")
    if isinstance(reason, Reason):
        reason = reason.name
    if reason and reason not in Reason.__members__:
        raise ValueError("unknown reason")
    return {"outcome": outcome, "fields": {}, "reason": reason, "transaction_hash": transaction_hash}


def denial_message(response: AccessResponse) -> str:
    """Console text for a denied, unavailable or pending response. Never shows fields or exception text."""
    outcome = response["outcome"]
    if outcome == OUTCOME_DENIED:
        message = f"denied: {response['reason']}" if response["reason"] else "denied"
        if response["transaction_hash"]:
            message += f" (logged on-chain in {response['transaction_hash']})"
        return message
    if outcome == OUTCOME_UNAVAILABLE:
        # same text whatever reason the event carried, e.g. when Python itself sent the zero hash
        return "unavailable: local record could not be verified, not a clinical result"
    if outcome == OUTCOME_PENDING:
        return "pending: not confirmed, nothing released"
    raise ValueError("denial_message is only for denied, unavailable or pending")
