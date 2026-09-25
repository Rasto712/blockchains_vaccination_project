"""Shared type declarations, not implemented business logic.

Developers 3 and 4: keep scope and reason codes aligned with ConsentManager.sol.
The child is a local subject; guardian, clinic, school, doctor and deployer use
predefined local account labels.
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
    scope: int  # raw uint8; convert with Scope(x) only when x is 1 or 2
    timestamp: int
    allowed: bool
    reason: Reason
    transaction_hash: str


class Receipt(TypedDict):
    """Metadata of a mined transaction; chain.py returns it only when status == 1."""
    transaction_hash: str
    status: int
    gas_used: int
    block_number: int
    logs: list[Any]


class ChainUnavailable(Exception):
    """RPC down or wrong chain ID."""


class TransactionRejected(Exception):
    """The transaction reverted. args hold the error name only."""


class TransactionPending(Exception):
    """No receipt within the timeout, so nothing is confirmed."""


# Values for AccessResponse.outcome
OUTCOME_ALLOWED = "allowed"
OUTCOME_DENIED = "denied"  # committed AccessAttempt with allowed=false
OUTCOME_UNAVAILABLE = "unavailable"  # local record, salt, RPC, receipt or event missing; not a clinical NO
OUTCOME_PENDING = "pending"  # no receipt within the timeout


class AccessResponse(TypedDict):
    """Only allowlisted fields; denied/unavailable responses must carry no health payload.
    fields == {} for every outcome except allowed.
    """
    outcome: str
    fields: dict[str, Any]
    reason: str  # Reason name, "" if no event
    transaction_hash: str  # "" if none
