"""Shared type declarations, not implemented business logic.

Developers 3 and 4: keep scope and reason codes aligned with ConsentManager.sol.
The child is a local subject; guardian, clinic, school, doctor and deployer use
predefined local account labels. No Java-style actor hierarchy is required.
TypedDict definitions document returned dictionaries but perform no validation.
"""
from enum import IntEnum
from typing import Any, TypedDict


class Scope(IntEnum):
    """The only data scopes in this one-week project."""
    MEASLES_STATUS = 1
    VACCINATION_SCHEDULE = 2


class Reason(IntEnum):
    """Numeric ABI values; never silently reorder these entries."""
    ALLOWED = 0
    NOT_REGISTERED = 1
    UNSUPPORTED_SCOPE = 2
    MISSING_EVIDENCE = 3
    NO_CONSENT = 4
    EXPIRED = 5
    REVOKED = 6
    HASH_MISMATCH = 7


class VaccinationEvent(TypedDict):
    """Private local JSON event; never return this entire object to a school."""
    vaccine: str
    covers: list[str]
    date: str
    clinic: str
    batch: str


class VaccinationCard(TypedDict):
    """One synthetic child and one frozen vaccination for the demo."""
    child_id: str
    vaccinations: list[VaccinationEvent]


class RecordSnapshot(TypedDict):
    """Internal-only bytes and parsed data from the same file read."""
    raw_bytes: bytes
    card: VaccinationCard
    salt: bytes
    commitment: bytes


class IdentityInfo(TypedDict):
    """Public registry metadata, not raw personal identity attributes."""
    registered: bool
    identity_hash: bytes
    vaccination_hash: bytes


class ConsentDecision(TypedDict):
    """Current view result; not a substitute for a committed access event."""
    allowed: bool
    reason: Reason


class AccessAttempt(TypedDict):
    """Decoded event from the exact contract and transaction receipt."""
    owner: str
    requester: str
    scope: Scope
    timestamp: int
    allowed: bool
    reason: Reason
    transaction_hash: str


class Receipt(TypedDict):
    """Successful/failed receipt metadata; transaction submission is not success."""
    transaction_hash: str
    status: int
    gas_used: int
    block_number: int
    logs: list[Any]


class AccessResponse(TypedDict):
    """Only allowlisted fields; denied/unavailable responses must carry no health payload."""
    outcome: str
    fields: dict[str, Any]
