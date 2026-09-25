"""Developer 3: permitted output projection and the local disclosure boundary.
Coordinate helper results with Developer 4. A school must never receive the full vaccination card.

All workflow methods are placeholders. They raise NotImplementedError rather than
returning fake data or pretending that authorization has succeeded.
"""
from pathlib import Path
from typing import Any
from app.models import VaccinationCard, AccessResponse, Scope


def select_school_status(card: VaccinationCard) -> dict[str, Any]:
    """Produce only a positive verified-status field for the supported MMR/measles fixture.
    Call only after trusted evidence and consent checks. Missing evidence is unavailable,
    not a clinical NO; do not include vaccine dates, clinic, batch, child name or file path.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("select_school_status is an implementation task; see docs/tasks.")


def select_doctor_schedule(card: VaccinationCard) -> dict[str, Any]:
    """Return only vaccine/date fields from the validated authorized card.
    Do not return the original dictionary, raw bytes, coverage list, clinic, batch or salt.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("select_doctor_schedule is an implementation task; see docs/tasks.")


def perform_access(settings: dict[str, Any], requester_label: str, owner: str, scope: Scope) -> AccessResponse:
    """Coordinate one complete request using records.py and chain.py.
    Read/hash/parse one snapshot; submit observed hash (zero if local data is unavailable);
    await and validate the access event; compare with registered evidence; recheck current consent;
    then project only the permitted fields from the same bytes. Release nothing on denial,
    network failure, missing event or changed authorization. Tokens do not move during access.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("perform_access is an implementation task; see docs/tasks.")


def verify_for_school(settings: dict[str, Any], owner: str) -> AccessResponse:
    """Invoke the access workflow under the SCHOOL account and MEASLES_STATUS scope.
    Return a status-only success or a denied/unavailable result with no health payload.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("verify_for_school is an implementation task; see docs/tasks.")


def get_doctor_schedule(settings: dict[str, Any], owner: str) -> AccessResponse:
    """Invoke the access workflow under the DOCTOR account and VACCINATION_SCHEDULE scope.
    Return allowlisted schedule fields only after every check and committed event succeeds.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("get_doctor_schedule is an implementation task; see docs/tasks.")


def format_denial(outcome: str) -> AccessResponse:
    """Build a nonclinical denied/unavailable response with an empty fields object.
    Do not echo exceptions containing medical JSON, secrets or local filesystem paths.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("format_denial is an implementation task; see docs/tasks.")
