"""Developer 3: local JSON persistence and salted byte-snapshot commitments.
Use fixed demo filenames under a configured root; no database, encryption or version store.

Bad or missing local files raise RecordError. Its message never holds the data, the salt or a path,
and it is raised outside any except block so the original error (which can hold them) is not chained.
Paths in settings are relative to the project root.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import base64
import hashlib
import json
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any
from app.models import VaccinationCard, RecordSnapshot

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# mirrors data_root in settings, because save_record gets no settings
DATA_ROOT = PROJECT_ROOT / "runtime-data"
EXAMPLES_DIR = PROJECT_ROOT / "data" / "examples"
REGISTERING_LABELS = ("guardian", "school", "doctor")

SALT_LENGTH = 32
COMMITMENT_PREFIXES = {
    "VACCINATION": b"VACCINATION:v1\n",
    "IDENTITY": b"IDENTITY:v1\n",
}
RECORD_KEYS = {"child_id", "vaccinations"}
EVENT_KEYS = {"vaccine", "covers", "date", "clinic", "batch"}
IDENTITY_KEYS = {"unique_id", "email"}
DATE_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class RecordError(ValueError):
    """Local record, identity or salt is missing or invalid. Never a clinical result."""


def save_record(path: Path, card: VaccinationCard) -> None:
    """Validate and save the synthetic local card before clinic attestation.
    Input path must resolve inside the configured data root (settings data_root). Reject malformed fields
    and unintended overwrite of the frozen evidence file; never save private keys.
    Serialise as json.dumps(card, indent=2, ensure_ascii=False) plus one trailing newline, encode UTF-8
    and write in binary mode (never write_text). That reproduces data/examples/vaccination_record.json
    byte for byte (269 bytes, LF). Test vector with salt = bytes(range(32)) and the VACCINATION prefix:
    3f2242f3cce59c18d546a104712ece887fdaf0563cd6bd3f4cb5c932cc6ec5b9.
    """
    target = Path(path).resolve()
    if DATA_ROOT.resolve() not in target.parents:
        raise RecordError("record path must be inside the data root")
    try:
        raw_bytes = (json.dumps(card, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raw_bytes = None
    if raw_bytes is None:
        raise RecordError("record could not be serialised")
    # check the exact bytes that will be written
    parse_record(raw_bytes)
    _write_new(target, raw_bytes, "record already exists and is frozen")


def read_record_bytes(path: Path) -> bytes:
    """Read one immutable byte snapshot of the controlled local JSON file.
    Report missing/unreadable input as unavailable. Do not reread different bytes after authorization.
    """
    return _read_once(path, "local record unavailable")


def parse_record(raw_bytes: bytes) -> VaccinationCard:
    """Decode UTF-8 and validate one child and one MMR vaccination event.
    Require nonempty child ID, vaccine, coverage list, calendar date, clinic and batch.
    Invalid data is not a negative medical result.
    """
    card = _decode_json(raw_bytes, "record is not valid UTF-8 JSON")
    if not isinstance(card, dict) or set(card) != RECORD_KEYS:
        raise RecordError("record must have exactly child_id and vaccinations")
    if not _is_text(card["child_id"]):
        raise RecordError("invalid record field: child_id")
    events = card["vaccinations"]
    if not isinstance(events, list) or len(events) != 1:
        raise RecordError("record must have exactly one vaccination")
    event = events[0]
    if not isinstance(event, dict) or set(event) != EVENT_KEYS:
        raise RecordError("vaccination must have exactly vaccine, covers, date, clinic and batch")
    for field in ("vaccine", "date", "clinic", "batch"):
        if not _is_text(event[field]):
            raise RecordError(f"invalid record field: {field}")
    if event["vaccine"] != "MMR":
        raise RecordError("invalid record field: vaccine")
    covers = event["covers"]
    if not isinstance(covers, list) or not all(_is_text(item) for item in covers) or "measles" not in covers:
        raise RecordError("invalid record field: covers")
    if not _is_calendar_date(event["date"]):
        raise RecordError("invalid record field: date")
    return card


def generate_salt() -> bytes:
    """Generate exactly 32 cryptographically random bytes for one frozen commitment.
    Use the standard secrets module during implementation; do not reuse fixed fixture salts.
    """
    return secrets.token_bytes(SALT_LENGTH)


def save_salt(path: Path, salt: bytes) -> None:
    """Validate length and save the private salt locally as base64 metadata.
    Never include it in contract calls, ordinary logs or requester responses.
    File format (every salt file, vaccination and identity): {"salt_b64": "<44-char base64 of 32 bytes>"}.
    """
    if not isinstance(salt, bytes) or len(salt) != SALT_LENGTH:
        raise RecordError("salt must be exactly 32 bytes")
    text = json.dumps({"salt_b64": base64.b64encode(salt).decode("ascii")}) + "\n"
    _write_new(Path(path), text.encode("ascii"), "salt file already exists")


def load_salt(path: Path) -> bytes:
    """Load the matching base64 salt and require exactly 32 bytes.
    Expects the {"salt_b64": "..."} format written by save_salt.
    A missing or invalid salt means unavailable evidence; never substitute an empty salt.
    """
    content = _decode_json(_read_once(path, "salt unavailable"), "salt unavailable")
    if not isinstance(content, dict) or set(content) != {"salt_b64"} or not isinstance(content["salt_b64"], str):
        raise RecordError("salt unavailable")
    try:
        salt = base64.b64decode(content["salt_b64"], validate=True)
    except ValueError:
        salt = b""
    if len(salt) != SALT_LENGTH:
        raise RecordError("salt unavailable")
    return salt


def calculate_commitment(raw_bytes: bytes, salt: bytes, purpose: str) -> bytes:
    """Return 32-byte SHA-256(prefix + salt + exact bytes).
    Only purposes VACCINATION and IDENTITY are supported. Prefix is the ASCII purpose
    followed by :v1 and one newline. Validate salt length; no JSON canonicalization is used.
    """
    # error messages never include the record or salt
    prefix = COMMITMENT_PREFIXES.get(purpose) if isinstance(purpose, str) else None
    if prefix is None:
        raise ValueError("unsupported commitment purpose")
    if not isinstance(salt, bytes) or len(salt) != SALT_LENGTH:
        raise ValueError("salt must be exactly 32 bytes")
    if not isinstance(raw_bytes, bytes):
        raise TypeError("record must be raw bytes")
    return hashlib.sha256(prefix + salt + raw_bytes).digest()


def load_snapshot(record_path: Path, salt_path: Path) -> RecordSnapshot:
    """Read once, validate, load the matching salt and compute the commitment.
    Return the same raw bytes, parsed card, salt and hash for internal verification.
    The snapshot is never a public response.
    """
    raw_bytes = read_record_bytes(record_path)
    card = parse_record(raw_bytes)
    salt = load_salt(salt_path)
    return {
        "raw_bytes": raw_bytes,
        "card": card,
        "salt": salt,
        "commitment": calculate_commitment(raw_bytes, salt, "VACCINATION"),
    }


def prepare_identity(identity_path: Path, salt_path: Path) -> bytes:
    """Validate synthetic identity attributes and compute a separate salted identity hash.
    Keep unique demo ID/email and salt locally; registration sends only the resulting bytes32.
    identity_path is <identity_directory>/<label>.json; salt_path is <identity_salt_directory>/identity_<label>_salt.json;
    hash only that file's bytes with the IDENTITY:v1 prefix.
    """
    raw_bytes = _read_once(identity_path, "identity unavailable")
    _check_identity(raw_bytes)
    return calculate_commitment(raw_bytes, load_salt(salt_path), "IDENTITY")


def settings_path(settings: dict[str, Any], key: str) -> Path:
    """Resolve one path setting against the project root."""
    return PROJECT_ROOT / settings[key]


def identity_paths(settings: dict[str, Any], label: str) -> tuple[Path, Path]:
    """Identity file and identity salt file for guardian, school or doctor."""
    if label not in REGISTERING_LABELS:
        raise ValueError("only guardian, school and doctor register")
    identity_path = settings_path(settings, "identity_directory") / f"{label}.json"
    salt_path = settings_path(settings, "identity_salt_directory") / f"identity_{label}_salt.json"
    return identity_path, salt_path


def setup_runtime(settings: dict[str, Any]) -> list[Path]:
    """Copy the example identities and card into the runtime folders and give each file its own salt.
    Repeatable: files that already exist are left alone. Returns the files it created.
    """
    created = []
    for label in REGISTERING_LABELS:
        identity_path, _ = identity_paths(settings, label)
        if not identity_path.exists():
            raw_bytes = _read_once(EXAMPLES_DIR / "identities" / f"{label}.json", "example identity unavailable")
            _check_identity(raw_bytes)
            _write_new(identity_path, raw_bytes, "identity already exists")
            created.append(identity_path)
    record_path = settings_path(settings, "vaccination_file")
    if not record_path.exists():
        example = _read_once(EXAMPLES_DIR / "vaccination_record.json", "example record unavailable")
        save_record(record_path, parse_record(example))
        created.append(record_path)
    salt_paths = [identity_paths(settings, label)[1] for label in REGISTERING_LABELS]
    salt_paths.append(settings_path(settings, "vaccination_salt_file"))
    for salt_path in salt_paths:
        if not salt_path.exists():
            save_salt(salt_path, generate_salt())
            created.append(salt_path)
    return created


def _read_once(path: Path, message: str) -> bytes:
    try:
        with open(path, "rb") as file:
            return file.read()
    except OSError:
        pass
    raise RecordError(message)


def _write_new(path: Path, data: bytes, exists_message: str) -> None:
    # "xb" writes bytes and refuses to replace a file that is already there
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "xb") as file:
            file.write(data)
        return
    except FileExistsError:
        message = exists_message
    except OSError:
        message = "local file could not be written"
    raise RecordError(message)


def _decode_json(raw_bytes: bytes, message: str) -> Any:
    # strict UTF-8, and a repeated key is an error rather than last one wins
    if not isinstance(raw_bytes, bytes):
        raise TypeError("expected raw bytes")
    try:
        return json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_no_repeated_keys)
    except (ValueError, RecursionError):
        pass
    raise RecordError(message)


def _no_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("repeated key")
    return dict(pairs)


def _check_identity(raw_bytes: bytes) -> None:
    identity = _decode_json(raw_bytes, "identity is not valid UTF-8 JSON")
    if not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS:
        raise RecordError("identity must have exactly unique_id and email")
    for field in ("unique_id", "email"):
        if not _is_text(identity[field]):
            raise RecordError(f"invalid identity field: {field}")


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_calendar_date(value: str) -> bool:
    # fromisoformat on its own also takes 20260312 and week dates from Python 3.11
    if not DATE_SHAPE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
