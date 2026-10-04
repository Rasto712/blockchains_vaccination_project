# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Reads and writes the local JSON files and makes salted hashes of them.
Bad or missing files raise RecordError, whose message never contains data, salts or paths.
Salt files are only readable by the owner where possible.
"""
import base64
import hashlib
import json
import os
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any
from app.models import VaccinationCard, RecordSnapshot

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# default data folder
DATA_ROOT = PROJECT_ROOT / "runtime-data"
EXAMPLES_DIR = PROJECT_ROOT / "data" / "examples"
REGISTERING_LABELS = ("guardian", "school", "doctor")

SALT_LENGTH = 32
# salt files: owner only
SALT_FILE_MODE = 0o600
COMMITMENT_PREFIXES = {
    "VACCINATION": b"VACCINATION:v1\n",
    "IDENTITY": b"IDENTITY:v1\n",
}
RECORD_KEYS = {"child_id", "vaccinations"}
EVENT_KEYS = {"vaccine", "covers", "date", "clinic", "batch"}
IDENTITY_KEYS = {"unique_id", "email"}
DATE_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class RecordError(ValueError):
    """A local record, identity or salt is missing or invalid."""


def save_record(path: Path, card: VaccinationCard, root: Path | None = None) -> None:
    """Check the card and save it once inside the data root. It is never overwritten."""
    target = Path(path).resolve()
    _check_inside(target, DATA_ROOT if root is None else root)
    try:
        raw_bytes = (json.dumps(card, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raw_bytes = None
    if raw_bytes is None:
        raise RecordError("record could not be serialised")
    parse_record(raw_bytes)
    _write_new(target, raw_bytes, "record already exists and is frozen")


def read_record_bytes(path: Path) -> bytes:
    """Read the record file once as bytes."""
    return _read_once(path, "local record unavailable")


def parse_record(raw_bytes: bytes) -> VaccinationCard:
    """Check the bytes are a valid card with one MMR vaccination."""
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
    """32 random bytes."""
    return secrets.token_bytes(SALT_LENGTH)


def save_salt(path: Path, salt: bytes) -> None:
    """Save a salt as base64 in a new file. Never send it to the chain."""
    if not isinstance(salt, bytes) or len(salt) != SALT_LENGTH:
        raise RecordError("salt must be exactly 32 bytes")
    text = json.dumps({"salt_b64": base64.b64encode(salt).decode("ascii")}) + "\n"
    _write_new(Path(path), text.encode("ascii"), "salt file already exists", SALT_FILE_MODE)


def load_salt(path: Path) -> bytes:
    """Load a salt file and check it is 32 bytes."""
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
    """SHA-256 of prefix + salt + the exact bytes. Purpose is VACCINATION or IDENTITY."""
    prefix = COMMITMENT_PREFIXES.get(purpose) if isinstance(purpose, str) else None
    if prefix is None:
        raise ValueError("unsupported commitment purpose")
    if not isinstance(salt, bytes) or len(salt) != SALT_LENGTH:
        raise ValueError("salt must be exactly 32 bytes")
    if not isinstance(raw_bytes, bytes):
        raise TypeError("record must be raw bytes")
    return hashlib.sha256(prefix + salt + raw_bytes).digest()


def load_snapshot(record_path: Path, salt_path: Path) -> RecordSnapshot:
    """Read the record and salt once and compute the commitment."""
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
    """Check an identity file and hash it with its salt. Only the hash goes on-chain."""
    raw_bytes = _read_once(identity_path, "identity unavailable")
    _check_identity(raw_bytes)
    return calculate_commitment(raw_bytes, load_salt(salt_path), "IDENTITY")


def settings_path(settings: dict[str, Any], key: str) -> Path:
    """Path from settings, relative to the project root."""
    return PROJECT_ROOT / settings[key]


def data_root(settings: dict[str, Any]) -> Path:
    """The data folder from settings, or the default runtime-data folder."""
    if "data_root" not in settings:
        return DATA_ROOT
    value = settings["data_root"]
    if not isinstance(value, str) or not value.strip():
        raise RecordError("invalid setting: data_root")
    return settings_path(settings, "data_root")


def shown_path(path: Path, settings: dict[str, Any] | None = None) -> str:
    """A path for console output that hides the home folder."""
    target = Path(path).resolve()
    try:
        return target.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        pass
    if settings is not None:
        try:
            inside = target.relative_to(data_root(settings).resolve()).as_posix()
        except (RecordError, KeyError, TypeError, ValueError):
            inside = None
        if inside is not None:
            return "<data_root>" if inside == "." else f"<data_root>/{inside}"
    return target.name


def identity_paths(settings: dict[str, Any], label: str) -> tuple[Path, Path]:
    """Identity file and salt file for a label."""
    if label not in REGISTERING_LABELS:
        raise ValueError("only guardian, school and doctor register")
    identity_path = settings_path(settings, "identity_directory") / f"{label}.json"
    salt_path = settings_path(settings, "identity_salt_directory") / f"identity_{label}_salt.json"
    return identity_path, salt_path


def setup_runtime(settings: dict[str, Any]) -> list[Path]:
    """Copy the example files into the runtime folders and make salts. Existing files are left alone."""
    root = data_root(settings)
    record_path = settings_path(settings, "vaccination_file")
    if not record_path.exists():
        _check_inside(record_path.resolve(), root)
    created = []
    for label in REGISTERING_LABELS:
        identity_path, _ = identity_paths(settings, label)
        if not identity_path.exists():
            raw_bytes = _read_once(EXAMPLES_DIR / "identities" / f"{label}.json", "example identity unavailable")
            _check_identity(raw_bytes)
            _write_new(identity_path, raw_bytes, "identity already exists")
            created.append(identity_path)
    if not record_path.exists():
        example = _read_once(EXAMPLES_DIR / "vaccination_record.json", "example record unavailable")
        save_record(record_path, parse_record(example), root)
        created.append(record_path)
    salt_paths = [identity_paths(settings, label)[1] for label in REGISTERING_LABELS]
    salt_paths.append(settings_path(settings, "vaccination_salt_file"))
    for salt_path in salt_paths:
        if not salt_path.exists():
            save_salt(salt_path, generate_salt())
            created.append(salt_path)
    return created


def _check_inside(target: Path, root: Path) -> None:
    # the root folder itself does not count
    if Path(root).resolve() not in target.parents:
        raise RecordError("record path must be inside the data root")


def _read_once(path: Path, message: str) -> bytes:
    try:
        with open(path, "rb") as file:
            return file.read()
    except OSError:
        pass
    raise RecordError(message)


def _write_new(path: Path, data: bytes, exists_message: str, mode: int = 0o666) -> None:
    # O_EXCL means never overwrite an existing file
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
        with open(descriptor, "wb") as file:
            file.write(data)
        return
    except FileExistsError:
        message = exists_message
    except OSError:
        message = "local file could not be written"
    raise RecordError(message)


def _decode_json(raw_bytes: bytes, message: str) -> Any:
    # repeated keys are an error
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
    # fromisoformat alone accepts other formats too
    if not DATE_SHAPE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
