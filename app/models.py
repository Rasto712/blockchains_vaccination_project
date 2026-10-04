# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Shared types and exceptions. Scope and Reason codes must match ConsentManager.sol."""
from enum import IntEnum
from typing import Any, TypedDict


class Scope(IntEnum):
    """The only data scopes in this project."""
    MEASLES_STATUS = 1
    VACCINATION_SCHEDULE = 2


class Reason(IntEnum):
    """Numeric values match the contract, do not reorder."""
    ALLOWED = 0
    NOT_REGISTERED = 1
    UNSUPPORTED_SCOPE = 2
    MISSING_EVIDENCE = 3
    NO_CONSENT = 4
    EXPIRED = 5
    REVOKED = 6
    HASH_MISMATCH = 7


class VaccinationEvent(TypedDict):
    """One vaccination in the private local file. Never give all of it to a school."""
    vaccine: str
    covers: list[str]
    date: str
    clinic: str
    batch: str


class VaccinationCard(TypedDict):
    """One made-up child with one vaccination."""
    child_id: str
    vaccinations: list[VaccinationEvent]


class RecordSnapshot(TypedDict):
    """Raw bytes and parsed data from a single file read. Internal only."""
    raw_bytes: bytes
    card: VaccinationCard
    salt: bytes
    commitment: bytes


class IdentityInfo(TypedDict):
    """Public registry data (hashes only)."""
    registered: bool
    identity_hash: bytes
    vaccination_hash: bytes


class ConsentDecision(TypedDict):
    """Result of a consent check."""
    allowed: bool
    reason: Reason


class ConsentInfo(TypedDict):
    """Stored grant; expires_at 0 means never granted."""
    expires_at: int  # exclusive: the grant is invalid from this block timestamp on
    revoked: bool


class AccessAttempt(TypedDict):
    """Decoded access event from the contract."""
    owner: str
    requester: str
    scope: int  # raw uint8; convert with Scope(x) only when x is 1 or 2
    timestamp: int
    allowed: bool
    reason: Reason
    transaction_hash: str


class Receipt(TypedDict):
    """Details of a mined transaction."""
    transaction_hash: str
    status: int
    gas_used: int
    block_number: int
    logs: list[Any]
    contract_address: str | None  # set only for a contract deployment


class ChainUnavailable(Exception):
    """Node is down, wrong chain, or an event is missing."""


class Web3NotInstalled(ChainUnavailable):
    """web3 cannot be imported."""


class DeploymentUnavailable(ChainUnavailable):
    """deployment.json is missing, old or does not match this node."""


class ArtifactUnavailable(DeploymentUnavailable):
    """A compiled contract artifact is missing."""


class TransactionRejected(Exception):
    """A transaction or call reverted. args hold the Solidity error name."""


class TransactionPending(Exception):
    """Transaction was sent but no receipt came back. It may still be mined."""


# Values for AccessResponse.outcome
OUTCOME_ALLOWED = "allowed"
OUTCOME_DENIED = "denied"  # committed AccessAttempt with allowed=false
OUTCOME_UNAVAILABLE = "unavailable"  # local record, salt, RPC, receipt or event missing; not a clinical NO
OUTCOME_PENDING = "pending"  # sent, but no receipt (timeout or the node stopped answering)


class AccessResponse(TypedDict):
    """What a requester gets back. fields is empty unless the outcome is allowed."""
    outcome: str
    fields: dict[str, Any]
    reason: str  # Reason name, "" if no event
    transaction_hash: str  # "" if none
