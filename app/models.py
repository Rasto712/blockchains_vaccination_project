# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Shared type declarations, not implemented business logic.

Keep scope and reason codes aligned with ConsentManager.sol.
The child is a local subject; guardian, clinic, school, doctor and deployer use
predefined local account labels.
TypedDict definitions document returned dictionaries but perform no validation.
"""
from enum import IntEnum
from typing import Any, TypedDict


class Scope(IntEnum):
    """The only data scopes in this project."""
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


class ConsentInfo(TypedDict):
    """Stored grant state from ConsentManager.getConsent; expires_at == 0 means never granted."""
    expires_at: int  # exclusive: the grant is invalid from this block timestamp on
    revoked: bool


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
    contract_address: str | None  # set only for a contract deployment


class ChainUnavailable(Exception):
    """RPC down, wrong chain ID, or a missing or wrong event. Messages hold no details."""


class Web3NotInstalled(ChainUnavailable):
    """web3 cannot be imported, so no chain action can run although the node may be up. Fixed by running
    python -m pip install -r requirements.txt with the venv's Python.
    """


class DeploymentUnavailable(ChainUnavailable):
    """deployment.json is missing, stale (for example from before a node restart) or does not match the
    contracts on this node. Fixed by running python -m scripts.deploy_local --reset.
    """


class ArtifactUnavailable(DeploymentUnavailable):
    """A compiled contract artifact is missing or unreadable. Fixed by running npm run compile."""


class TransactionRejected(Exception):
    """The transaction or view reverted. args hold the Solidity error name only, for example
    "NotTrustedClinic"; "Panic" for a Solidity panic and "Reverted" when the error is unknown.
    """


class TransactionPending(Exception):
    """The transaction was sent but no receipt came back: none within the timeout, or the node stopped
    answering while Python waited. It may still be mined, so nothing is confirmed either way.
    """


# Values for AccessResponse.outcome
OUTCOME_ALLOWED = "allowed"
OUTCOME_DENIED = "denied"  # committed AccessAttempt with allowed=false
OUTCOME_UNAVAILABLE = "unavailable"  # local record, salt, RPC, receipt or event missing; not a clinical NO
OUTCOME_PENDING = "pending"  # sent, but no receipt (timeout or the node stopped answering)


class AccessResponse(TypedDict):
    """Only allowlisted fields; denied/unavailable responses must carry no health payload.
    fields == {} for every outcome except allowed.
    """
    outcome: str
    fields: dict[str, Any]
    reason: str  # Reason name, "" if no event
    transaction_hash: str  # "" if none
