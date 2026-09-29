# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Run the Python checks on a live local node and export the REAL results as the PY rows.

Usage, from the project root, after npm run compile and with npm run node running:

    .venv/bin/python -m evaluation.export_python_results [--settings PATH] [--output PATH]

It writes evaluation/results/test_results.csv with the shared header
(test_id, requirement, why_critical, expected, actual, status, evidence). It writes only the PY rows; the SOL
rows are in solidity_test_results.csv (python -m evaluation.export_solidity_results). requirement,
why_critical and expected are the fixed text in ROWS, while actual and status come only from this run.
A row is "pass" when every observation in it was as expected and "fail" otherwise; an observation that
was not as expected is marked "(not as expected)" in actual.

One run does this, in order, on the node the settings name (chain 31337 only: it moves node time forward):
1. the unit tests, with web3 and without it (python -S leaves out the venv's packages); no node needed;
2. the scripted demo as a subprocess: python -m integration.demo_workflow --settings PATH;
3. its own story on fresh contracts (deploy_local, as with --reset) through the app code in this process:
   register, attests from wrong accounts, attest, school and doctor requests, a tampered copy, a missing
   salt, a late recheck, unsupported scopes, a pending and an unreachable request, rewards and expiry;
4. failures in the console menu and in the demo (node down, no deployment, rejected transactions, bad
   local files), with the text they print;
5. a scan of every transaction mined since step 2 started, the demo's included: calldata, logs and every
   storage write (debug_traceTransaction) are searched for the record, identity and salt values.
Two cases cannot happen by themselves under automine, so the run makes them happen and its rows say so:
the late recheck gets a real revokeConsent mined between the logged request and the recheck, and the
pending request is sent with automine off and a 3-second receipt timeout (30 s normally).

Every local file stays under the settings' data_root: the tamper copy and the temporary files for the
failure cases are made there and deleted again. When the run cannot finish (settings unreadable, node
unreachable, an unexpected error), nothing is written and the previous CSV is removed, so it cannot be
mistaken for this run's evidence. Exit status: 0 when the CSV was written and every row passed, 1 otherwise.
"""
import argparse
import base64
import contextlib
import csv
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from app import chain, disclosure, records
from app import main as console
from app.models import (
    Scope, Reason, AccessResponse, ChainUnavailable, TransactionRejected, TransactionPending,
    OUTCOME_ALLOWED, OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING,
)
from integration import demo_workflow
from scripts import deploy_local

PROJECT_ROOT = records.PROJECT_ROOT
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
CSV_PATH = RESULTS_DIR / "test_results.csv"
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.json"
HEADER = ["test_id", "requirement", "why_critical", "expected", "actual", "status", "evidence"]
COMMAND = "python -m evaluation.export_python_results"
# a change under these makes the evidence say "+ uncommitted changes"
CODE_PATHS = ("app", "contracts", "integration", "scripts", "tests", "config", "evaluation/export_python_results.py")
LOCAL_CHAIN_ID = 31337
# the only ABI types a call, a constructor or an event may carry: hashes and metadata, never text or bytes
PLAIN_TYPES = {"address", "bool", "bytes32", "uint8", "uint16", "uint256"}
PENDING_TIMEOUT_SECONDS = 3
SUBPROCESS_TIMEOUT_SECONDS = 600
UNSUPPORTED_SCOPES = (0, 3, 255)
GRANT_DAYS = 30
TRACEBACK = "Traceback (most recent call last)"
# a slash-rooted path with at least one folder, as an exception text or a resolved Path prints it
ABSOLUTE_PATH = re.compile(r"(?:^|[\s'\"(=:])/[\w.@-]+/")
UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
UNITTEST_RESULT = re.compile(r"^(OK|FAILED)(?: \((.*)\))?\s*$", re.MULTILINE)
UNIT_TESTS = ["-m", "unittest", "discover", "-s", "tests"]
# set for the unit-test runs started here, so a test that reaches run_checks cannot start them again
NESTED_RUN_VARIABLE = "EXPORT_PYTHON_RESULTS_UNIT_RUN"
DEMO_PASSED = "demo passed: every outcome above was checked"
UNAVAILABLE_TEXT = "unavailable: local record could not be verified, not a clinical result"

# test_id -> (requirement, why_critical, expected)
ROWS = {
    "PY-01": (
        "Disclosure: the school gets measles status only; the doctor gets vaccine and date only",
        "a requester must never see more of the card than its scope allows; the school must not learn dates, "
        "clinic, batch or the child ID.",
        'School fields exactly {"measles_status": "verified"}; doctor fields exactly vaccine and date per '
        "vaccination, built from the card; the demo transcript releases the same.",
    ),
    "PY-02": (
        "Disclosure: a denied, unavailable or pending response releases nothing",
        "every outcome other than allowed must carry an empty fields object, or a denial or a node failure "
        "could still leak the card.",
        "fields == {} for denied NO_CONSENT, HASH_MISMATCH, late REVOKED, REVOKED and EXPIRED, unavailable "
        "(missing salt, node down) and pending (no receipt in time); the node-down answer is shown as a node "
        "or receipt problem, not as the local record; every denied line of the demo says released: nothing.",
    ),
    "PY-03": (
        "Integrity: a tampered local copy is denied with HASH_MISMATCH",
        "a changed record must never be shown as attested; the on-chain commitment is what proves the local bytes.",
        "A copy under data_root with one batch byte changed sends its own hash, is denied HASH_MISMATCH "
        "on-chain, releases nothing and is deleted; the original still matches the registered commitment.",
    ),
    "PY-04": (
        "Integrity: a missing salt sends the zero hash and is unavailable, not a clinical NO",
        "without its salt the record cannot be verified, so Python must not guess a hash or answer as if "
        "the child were not vaccinated.",
        "With the salt file missing, the school request's calldata carries observedHash 0x00..00, the "
        "response is unavailable with no fields, and the console says it is not a clinical result.",
    ),
    "PY-05": (
        "Access: a failed final recheck logs a second event",
        "if consent ends between the logged request and the release, the late denial must be on the audit "
        "log too, not only the earlier allowed event.",
        "With a revokeConsent mined between the allowed request and the recheck: two AccessAttempt events "
        "(ALLOWED, then REVOKED), and the response is denied REVOKED with the second transaction and no fields.",
    ),
    "PY-06": (
        "Attestation: only the trusted clinic can attest; a wrong actor is rejected with NotTrustedClinic",
        "if any account could register a commitment, a guardian could attest a forged record of their own.",
        "registerVaccination from guardian, school, doctor and deployer is rejected NotTrustedClinic and "
        "stores nothing; the menu prints rejected: NotTrustedClinic; the clinic's attest then succeeds; the "
        "demo shows the same rejection.",
    ),
    "PY-07": (
        "Privacy: on-chain storage and events hold only hashes and metadata, no medical JSON",
        "anything sent to the chain is public and permanent; the record, the identities and the salts must "
        "stay local.",
        "Every transaction of the run, the demo's included: calldata, constructor arguments and logs decode "
        "to address, bytes32, uint or bool values only; no record, identity or salt value (raw, hex or "
        "base64) is in calldata, logs or any storage write; stored words are local hashes, numbers, addresses or "
        "packed consent words (a uint64 expiry and the revoked and rewarded flags).",
    ),
    "PY-08": (
        "Rewards: the guardian gets one unit per first grant of a (guardian, requester, scope) tuple",
        "the reward must go to the data owner, never the requester, and regrants must not farm more units.",
        "First school and doctor grants +1 each, minted to the guardian; regrant after revoke and after "
        "expiry +0; an active duplicate grant is rejected ConsentStillActive; final balances guardian 2 and "
        "everyone else 0, as at the end of the demo.",
    ),
    "PY-09": (
        "Audit: attempts with unsupported scopes are shown with the raw scope",
        "an attempt with an unknown scope is still an access attempt; hiding it or failing on it would make "
        "the audit log incomplete.",
        "requestAccess with scopes 0, 3 and 255 is logged denied UNSUPPORTED_SCOPE with the raw value; "
        "list_access_events keeps the int and the menu audit prints scope N (unsupported).",
    ),
    "PY-10": (
        "Console: failures print no exception text, paths or data",
        "exception text can hold file paths, RPC details or record content; the console must show a short "
        "outcome only.",
        "Each failure ends with exactly its short line (menu: node down, no deployment, register twice, "
        "attest by the doctor, missing salt, bad record, record outside data_root, unexpected error; demo: "
        "no settings file, node down, record outside data_root) and prints no traceback, absolute path, "
        "record text or salt.",
    ),
    "PY-11": (
        "Integration: the scripted demo passes end to end, with the local files under data_root",
        "the demo is the live evidence for the whole story, and data_root must decide where local files live.",
        "python -m integration.demo_workflow --settings PATH exits 0 with 'demo passed'; every data path is "
        "under data_root; the tamper folder is empty afterwards; with data_root outside the repo, no "
        "runtime-data folder appears in the repo.",
    ),
    "PY-12": (
        "Python unit tests (tests/, no node)",
        "they pin the release rule, the record format, the chain mapping, the demo and the measurement "
        "offline, against a fake chain and mocked web3.",
        "python -m unittest discover -s tests: every test runs and passes.",
    ),
    "PY-13": (
        "Python unit tests without web3",
        "setup and the offline checks must work with the standard library only; the web3 tests must skip, "
        "not fail.",
        "python -S -m unittest discover -s tests, where web3 cannot be imported: no failures or errors, and "
        "the web3 tests are skipped.",
    ),
}


class ExportError(Exception):
    """The run could not finish, so no CSV is written."""


class Row:
    """One PY row: the fixed text from ROWS plus what this run observed."""

    def __init__(self, test_id: str):
        if test_id not in ROWS:
            raise ValueError("unknown test id")
        self.test_id = test_id
        self.observed: list[str] = []
        self.evidence: list[str] = []
        self.passed = True

    def see(self, text: str, as_expected: Any) -> None:
        self.observed.append(text if as_expected else f"{text} (not as expected)")
        self.passed = self.passed and bool(as_expected)

    @property
    def status(self) -> str:
        # a row with nothing observed was not checked, so it cannot pass
        return "pass" if self.passed and self.observed else "fail"

    def cells(self, context: str) -> list[str]:
        requirement, why_critical, expected = ROWS[self.test_id]
        actual = "; ".join(self.observed) or "not checked"
        return [self.test_id, requirement, why_critical, expected, actual, self.status, "; ".join([*self.evidence, context])]


def parse_unittest_output(text: str) -> tuple[int | None, str | None, str]:
    """(tests run, "OK" or "FAILED", the part in brackets such as "skipped=69") from unittest's summary."""
    ran = UNITTEST_RAN.search(text)
    results = UNITTEST_RESULT.findall(text)
    result, details = results[-1] if results else (None, "")
    return (int(ran.group(1)) if ran else None), result, details or ""


def encodings(value: bytes) -> set[bytes]:
    """The forms a value could take on-chain or in console text: raw, hex in either case and base64."""
    return {value, value.hex().encode(), value.hex().upper().encode(), base64.b64encode(value)}


def find_values(blobs: Iterable[bytes], values: list[tuple[str, bytes]]) -> list[str]:
    """Labels of the values that occur in any blob in any of their encodings. Labels never hold the value."""
    blobs = list(blobs)
    found = []
    for label, value in values:
        forms = encodings(value)
        if any(form in blob for blob in blobs for form in forms):
            found.append(label)
    return found


def leaks(text: str, values: list[tuple[str, bytes]]) -> list[str]:
    """What console text shows that it never should: a traceback, an absolute path or a private value."""
    found = []
    if TRACEBACK in text:
        found.append("a traceback")
    if ABSOLUTE_PATH.search(text):
        found.append("an absolute path")
    return found + find_values([text.encode("utf-8")], values)


def private_values(settings: dict[str, Any]) -> list[tuple[str, bytes]]:
    """Every local value that must never reach the chain or an error line: the record file, its salt and
    field values, the JSON keys, the tampered batch, and each identity file, its fields and its salt.
    """
    snapshot = records.load_snapshot(
        records.settings_path(settings, "vaccination_file"), records.settings_path(settings, "vaccination_salt_file"),
    )
    card = snapshot["card"]
    values = [("the record file", snapshot["raw_bytes"]), ("the record salt", snapshot["salt"])]
    values.append(("record child_id", card["child_id"].encode()))
    for event in card["vaccinations"]:
        values += [(f"record {field}", event[field].encode()) for field in ("vaccine", "date", "clinic", "batch")]
        values += [("record covers entry", item.encode()) for item in event["covers"]]
    values += [(f"record key {key}", f'"{key}"'.encode()) for key in sorted(records.RECORD_KEYS | records.EVENT_KEYS)]
    values.append(("tampered batch", b"ABC124-DEMO"))
    for label in records.REGISTERING_LABELS:
        identity_path, salt_path = records.identity_paths(settings, label)
        raw_bytes = records.read_record_bytes(identity_path)
        identity = json.loads(raw_bytes)
        values.append((f"{label} identity file", raw_bytes))
        values += [(f"{label} {field}", identity[field].encode()) for field in ("unique_id", "email")]
        values.append((f"{label} identity salt", records.load_salt(salt_path)))
    return values


def classify_word(value: int, local_hashes: set[bytes], addresses: set[int]) -> str:
    """What one stored 32-byte word is: one of the local hashes, a known address, a number, a packed
    consent or other. ConsentManager keeps a tuple's Consent in one slot: expiresAt as uint64 in the low
    8 bytes, then revoked and rewarded as one byte each, so such a word is a number plus two 0/1 bytes.
    """
    if value.to_bytes(32, "big") in local_hashes:
        return "local hash"
    if value in addresses:
        return "address"
    # timestamps, balances, flags and counters
    if value < 2 ** 64:
        return "number"
    if value < 2 ** 80 and (value >> 64) & ~0x0101 == 0:
        return "consent"
    return "other"


def storage_writes(trace: dict[str, Any]) -> list[tuple[int, int]]:
    """(slot, value) of every SSTORE in a debug_traceTransaction struct log; the value is below the slot."""
    writes = []
    for step in trace.get("structLogs", []):
        if step.get("op") == "SSTORE":
            stack = step["stack"]
            writes.append((int(stack[-1], 16), int(stack[-2], 16)))
    return writes


def write_rows(path: Path, rows: list[list[str]]) -> None:
    """Write the shared header and the rows, LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)


def git_revision() -> str:
    """Short commit, with "+ uncommitted changes" when the Python or contract sources differ from it."""
    try:
        sha = _git("rev-parse", "--short", "HEAD")
        dirty = _git("status", "--porcelain", "--", *CODE_PATHS)
    except OSError:
        return "unknown commit"
    if not sha:
        return "unknown commit"
    return f"{sha} + uncommitted changes" if dirty else sha


class LiveRun:
    """One run on the node: the rows, the settings, and the live session of the in-process story."""

    def __init__(self, settings_path: Path, settings: dict[str, Any]):
        self.settings_path = settings_path
        self.settings = settings
        self.rows = {test_id: Row(test_id) for test_id in ROWS}
        self.data_root = records.data_root(settings).resolve()
        self.transcript = ""
        self.client: Any = None
        self.accounts: dict[str, str] = {}
        self.contracts: dict[str, Any] = {}
        self.commitment = b""
        self.local_hashes: set[bytes] = set()
        self.values: list[tuple[str, bytes]] = []
        self.first_block = 0
        self.demo_blocks = (0, -1)
        self.closed_rpc_url = _closed_rpc_url()

    # 1. unit tests

    def unit_tests(self) -> None:
        if os.environ.get(NESTED_RUN_VARIABLE):
            raise ExportError("the unit tests of an export run cannot start another export run")
        nested = dict(os.environ, **{NESTED_RUN_VARIABLE: "1"})
        row = self.rows["PY-12"]
        code, _, errors = _run([sys.executable, *UNIT_TESTS], nested)
        ran, result, details = parse_unittest_output(errors)
        row.see(_suite_text(ran, result, details, code), code == 0 and result == "OK" and ran)
        row.evidence.append("python -m unittest discover -s tests")

        row = self.rows["PY-13"]
        code, _, _ = _run([sys.executable, "-S", "-c", "import importlib.util, sys; sys.exit(importlib.util.find_spec('web3') is not None)"])
        row.see("web3 cannot be imported under python -S" if code == 0 else "web3 can still be imported under python -S", code == 0)
        code, _, errors = _run([sys.executable, "-S", *UNIT_TESTS], nested)
        ran, result, details = parse_unittest_output(errors)
        skipped = re.search(r"skipped=(\d+)", details)
        row.see(_suite_text(ran, result, details, code), code == 0 and result == "OK" and ran and skipped)
        row.evidence.append("python -S -m unittest discover -s tests")

    # 2. the scripted demo

    def connect(self) -> None:
        self.client = chain.connect(self.settings)
        if _node(lambda: self.client.eth.chain_id) != LOCAL_CHAIN_ID:
            raise ExportError("the checks move node time, so they only run on the local Hardhat chain 31337")
        self.first_block = self.latest_block() + 1

    def demo(self) -> None:
        row = self.rows["PY-11"]
        runtime_data = PROJECT_ROOT / "runtime-data"
        before = _tree(runtime_data)
        start = self.latest_block() + 1
        code, output, errors = _run([sys.executable, "-m", "integration.demo_workflow", "--settings", str(self.settings_path)])
        self.demo_blocks = (start, self.latest_block())
        self.transcript = output
        last = output.rstrip().splitlines()[-1] if output.strip() else ""
        row.see(f"exit code {code}, last line '{last}'", code == 0 and last == DEMO_PASSED)
        if code != 0:
            row.see(f"stderr '{errors.strip()}'", False)
        outside = _outside_repo(self.data_root)
        keys =("deployment_file", "vaccination_file", "vaccination_salt_file", "identity_directory", "identity_salt_directory")
        paths = [records.settings_path(self.settings, key).resolve() for key in keys]
        files = [path for path in self.data_root.rglob("*") if path.is_file()]
        row.see(
            f"data_root {'outside the repo' if outside else _shown(self.data_root)}; all {len(keys)} data paths under it; "
            f"{len(files)} local files there",
            all(self.data_root in path.parents for path in paths),
        )
        tamper = self.data_root / demo_workflow.TAMPER_COPY.parent
        empty = not tamper.exists() or not any(tamper.iterdir())
        row.see("the tamper folder is empty afterwards" if empty else "the tamper folder is not empty afterwards", empty)
        if outside:
            after = _tree(runtime_data)
            if before is None and after is None:
                row.see("the repo still has no runtime-data folder", True)
            else:
                row.see("the repo's runtime-data folder was left unchanged" if before == after else "the repo's runtime-data changed", before == after)
        row.evidence.append(f"demo blocks {self.demo_blocks[0]}-{self.demo_blocks[1]}")

    # 3. the in-process story on fresh contracts

    def deploy(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            deploy_local.run(self.settings_path, reset=True)
        self.accounts = {label: chain.select_account(self.client, label, self.settings) for label in self.settings["actor_account_indices"]}
        deployment = records.settings_path(self.settings, "deployment_file")
        self.contracts = {name: chain.load_contract(self.client, name, deployment) for name in chain.CONTRACT_NAMES}

    def register(self) -> None:
        records.setup_runtime(self.settings)
        self.values = private_values(self.settings)
        registry = self.contracts["IdentityRegistry"]
        for label in records.REGISTERING_LABELS:
            identity_hash = records.prepare_identity(*records.identity_paths(self.settings, label))
            chain.register_user(registry, account=self.accounts[label], identity_hash=identity_hash)
            self.local_hashes.add(identity_hash)
        self.commitment = records.load_snapshot(
            records.settings_path(self.settings, "vaccination_file"), records.settings_path(self.settings, "vaccination_salt_file"),
        )["commitment"]
        self.local_hashes.add(self.commitment)

    def attest(self) -> None:
        row = self.rows["PY-06"]
        registry, guardian = self.contracts["IdentityRegistry"], self.accounts["guardian"]
        for label in ("guardian", "school", "doctor", "deployer"):
            try:
                chain.register_vaccination(registry, clinic=self.accounts[label], guardian=guardian, record_hash=self.commitment)
                outcome = "accepted"
            except TransactionRejected as error:
                outcome = f"rejected {error.args[0] if error.args else 'Reverted'}"
            row.see(f"{label} attest {outcome}", outcome == "rejected NotTrustedClinic")
        stored = chain.get_user_info(registry, account=guardian)["vaccination_hash"]
        row.see("nothing stored" if stored == bytes(32) else "a commitment was stored", stored == bytes(32))
        printed = self.menu("attest", "school", self.settings)
        row.see(f"menu attest as school prints '{printed.strip()}'", printed == "rejected: NotTrustedClinic\n")
        if stored == bytes(32):
            receipt = chain.register_vaccination(registry, clinic=self.accounts["clinic"], guardian=guardian, record_hash=self.commitment)
            row.evidence.append(f"clinic attest tx {receipt['transaction_hash']}")
        stored = chain.get_user_info(registry, account=guardian)["vaccination_hash"]
        row.see("the clinic's attest stored the local commitment", stored == self.commitment)
        row.see(
            "the demo shows the guardian's attempt rejected: NotTrustedClinic",
            "-> rejected: NotTrustedClinic, nothing stored" in self.transcript,
        )

    def school_and_doctor(self) -> None:
        guardian = self.accounts["guardian"]
        response = disclosure.verify_for_school(self.settings, guardian)
        self.no_fields("school before any grant", response, OUTCOME_DENIED, "NO_CONSENT")

        self.grant("school", Scope.MEASLES_STATUS, GRANT_DAYS, 1, "first school grant")
        school_balance = self.balance("school")
        self.rows["PY-08"].see(f"school (requester) holds {school_balance}", school_balance == 0)
        row = self.rows["PY-01"]
        response = disclosure.verify_for_school(self.settings, guardian)
        school_fields = {"measles_status": "verified"}
        row.see(f"school: {_response_text(response)}", _allowed_with(response, school_fields))
        row.evidence.append(f"school tx {response['transaction_hash']}")

        self.grant("doctor", Scope.VACCINATION_SCHEDULE, GRANT_DAYS, 1, "first doctor grant")
        card = records.load_snapshot(
            records.settings_path(self.settings, "vaccination_file"), records.settings_path(self.settings, "vaccination_salt_file"),
        )["card"]
        doctor_fields = {"vaccinations": [{"vaccine": event["vaccine"], "date": event["date"]} for event in card["vaccinations"]]}
        response = disclosure.get_doctor_schedule(self.settings, guardian)
        row.see(f"doctor: {_response_text(response)}", _allowed_with(response, doctor_fields))
        row.evidence.append(f"doctor tx {response['transaction_hash']}")
        for fields in (school_fields, doctor_fields):
            line = f"; released: {json.dumps(fields)}"
            row.see(f"demo transcript releases {json.dumps(fields)}", line in self.transcript)

        before = self.balance("guardian")
        try:
            chain.grant_consent(
                self.contracts["ConsentManager"], guardian=guardian, requester=self.accounts["doctor"],
                scope=Scope.VACCINATION_SCHEDULE, duration_days=GRANT_DAYS,
            )
            outcome = "accepted"
        except TransactionRejected as error:
            outcome = f"rejected {error.args[0] if error.args else 'Reverted'}"
        after = self.balance("guardian")
        self.rows["PY-08"].see(
            f"doctor grant again while active: {outcome}, guardian {before} -> {after}",
            outcome == "rejected ConsentStillActive" and after == before,
        )

    def tamper(self) -> None:
        row = self.rows["PY-03"]
        guardian = self.accounts["guardian"]
        original = records.settings_path(self.settings, "vaccination_file")
        copy_path = self.data_root / demo_workflow.TAMPER_COPY
        if copy_path.exists():
            # left by an earlier step or run; never overwritten or deleted here
            row.see("a tamper copy was already in the data root's tamper folder, so the check could not run", False)
            return
        raw_bytes = records.read_record_bytes(original)
        tampered = raw_bytes.replace(b"ABC123-DEMO", b"ABC124-DEMO", 1)
        if tampered == raw_bytes:
            row.see("the record has no batch ABC123-DEMO to change", False)
            return
        salt = records.load_salt(records.settings_path(self.settings, "vaccination_salt_file"))
        copy_hash = records.calculate_commitment(tampered, salt, "VACCINATION")
        try:
            copy_path.parent.mkdir(parents=True, exist_ok=True)
            with open(copy_path, "xb") as file:
                file.write(tampered)
            response = disclosure.get_doctor_schedule(dict(self.settings, vaccination_file=str(copy_path)), guardian)
        finally:
            copy_path.unlink(missing_ok=True)
        row.see(f"doctor request on the copy: {_response_text(response)}", _denied_with(response, "HASH_MISMATCH"))
        self.no_fields("doctor on a tampered copy", response, OUTCOME_DENIED, "HASH_MISMATCH")
        sent = self.sent_hash(response["transaction_hash"])
        row.see("the request carried the copy's own hash, not the registered one", sent == copy_hash != self.commitment)
        event = self.event_of(response["transaction_hash"])
        row.see(f"on-chain AccessAttempt {_event_text(event)}", not event["allowed"] and event["reason"] == Reason.HASH_MISMATCH)
        row.see("copy deleted" if not copy_path.exists() else "copy left behind", not copy_path.exists())
        registered = chain.get_user_info(self.contracts["IdentityRegistry"], account=guardian)["vaccination_hash"]
        still = records.load_snapshot(original, records.settings_path(self.settings, "vaccination_salt_file"))["commitment"]
        row.see("the original still matches the registered commitment", still == registered == self.commitment)
        row.see(
            "the demo's tamper step is denied: HASH_MISMATCH and deletes its copy",
            "tamper: doctor request on the copy -> denied: HASH_MISMATCH" in self.transcript and "tamper: copy deleted" in self.transcript,
        )
        row.evidence.append(f"requestAccess tx {response['transaction_hash']}")

    def missing_salt(self) -> None:
        row = self.rows["PY-04"]
        missing = self.data_root / "private" / "missing_salt_for_the_check.json"
        if missing.exists():
            raise ExportError("the file standing in for a missing salt exists in the data root: remove it and run again")
        response = disclosure.verify_for_school(dict(self.settings, vaccination_salt_file=str(missing)), self.accounts["guardian"])
        row.see(f"school request without the salt file: {_response_text(response)}", response["outcome"] == OUTCOME_UNAVAILABLE and response["fields"] == {})
        self.no_fields("school with the salt file missing", response, OUTCOME_UNAVAILABLE, None)
        sent = self.sent_hash(response["transaction_hash"]) if response["transaction_hash"] else None
        row.see(f"observedHash in the calldata {_hex(sent) if sent is not None else 'none: nothing sent'}", sent == bytes(32))
        if sent is not None:
            event = self.event_of(response["transaction_hash"])
            row.see(f"on-chain AccessAttempt {_event_text(event)}", not event["allowed"])
        message = disclosure.denial_message(response)
        row.see(f"console text '{message}'", message == UNAVAILABLE_TEXT)
        row.evidence.append(f"requestAccess tx {response['transaction_hash']}")

    def late_recheck(self) -> None:
        row = self.rows["PY-05"]
        manager, guardian = self.contracts["ConsentManager"], self.accounts["guardian"]
        start = self.latest_block() + 1
        real_check = chain.check_access
        revokes = []

        def revoke_then_check(manager: Any, owner: str, requester: str, scope: Scope) -> Any:
            # the consent really ends on-chain after the logged request and before the recheck
            if not revokes:
                revokes.append(chain.revoke_consent(manager, guardian=owner, requester=requester, scope=scope))
            return real_check(manager, owner=owner, requester=requester, scope=scope)

        with mock.patch.object(chain, "check_access", revoke_then_check):
            response = disclosure.verify_for_school(self.settings, guardian)
        events = chain.list_access_events(manager, from_block=start)
        names = [Reason(event["reason"]).name for event in events]
        row.see(
            f"revokeConsent mined between request and recheck: {'yes' if revokes else 'no'}; "
            f"AccessAttempt events: {', '.join(names) or 'none'}",
            revokes and [(event["allowed"], event["reason"]) for event in events] == [(True, Reason.ALLOWED), (False, Reason.REVOKED)],
        )
        row.see(f"response {_response_text(response)}", _denied_with(response, "REVOKED"))
        self.no_fields("school after a failed recheck", response, OUTCOME_DENIED, "REVOKED")
        second = len(events) == 2 and response["transaction_hash"] == events[1]["transaction_hash"] != events[0]["transaction_hash"]
        row.see("the response names the second (denied) transaction", second)
        row.evidence += [f"revokeConsent tx {revokes[0]['transaction_hash']}" if revokes else "no revoke"]
        row.evidence += [f"requestAccess txs {', '.join(event['transaction_hash'] for event in events)}"]

        response = disclosure.verify_for_school(self.settings, guardian)
        self.no_fields("school after revoke", response, OUTCOME_DENIED, "REVOKED")
        self.grant("school", Scope.MEASLES_STATUS, 1, 0, "school regrant after revoke")

    def unsupported_scopes(self) -> None:
        row = self.rows["PY-09"]
        manager = self.contracts["ConsentManager"]
        for scope in UNSUPPORTED_SCOPES:
            event = chain.request_access(
                manager, requester=self.accounts["school"], owner=self.accounts["guardian"], scope=scope, observed_hash=self.commitment,
            )
            row.see(f"scope {scope}: logged {_event_text(event)} with scope {event['scope']}",
                    not event["allowed"] and event["reason"] == Reason.UNSUPPORTED_SCOPE and event["scope"] == scope)
        listed = [event["scope"] for event in chain.list_access_events(manager, from_block=0) if event["scope"] not in (1, 2)]
        row.see(f"list_access_events keeps the raw ints {listed}", listed == list(UNSUPPORTED_SCOPES) and all(type(scope) is int for scope in listed))
        printed = self.menu("audit", "guardian", self.settings)
        lines = [line for line in printed.splitlines() if "(unsupported)" in line]
        wanted = [f"school  scope {scope} (unsupported)  denied  UNSUPPORTED_SCOPE" for scope in UNSUPPORTED_SCOPES]
        shown = [line.split("  ", 1)[-1] for line in lines]
        row.see(f"menu audit prints {' | '.join(shown) or 'no unsupported scope'}", shown == wanted)

    def pending_and_unreachable(self) -> None:
        guardian = self.accounts["guardian"]
        self.node_request("evm_setAutomine", [False])
        try:
            with mock.patch.object(chain, "RECEIPT_TIMEOUT_SECONDS", PENDING_TIMEOUT_SECONDS):
                response = disclosure.verify_for_school(self.settings, guardian)
        finally:
            self.node_request("evm_setAutomine", [True])
            # the request is mined now; Python had already answered pending and released nothing
            self.node_request("evm_mine", [])
        self.no_fields(f"school with automine off (no receipt in {PENDING_TIMEOUT_SECONDS} s)", response, OUTCOME_PENDING, None)
        response = disclosure.verify_for_school(dict(self.settings, rpc_url=self.closed_rpc_url), guardian)
        self.no_fields("school with the node unreachable", response, OUTCOME_UNAVAILABLE, None)
        message = disclosure.denial_message(response)
        self.rows["PY-02"].see(f"its console text '{message}'", message == disclosure.CHAIN_UNAVAILABLE_MESSAGE)

    def expiry(self) -> None:
        manager, guardian, school = self.contracts["ConsentManager"], self.accounts["guardian"], self.accounts["school"]
        expires_at = chain.get_consent(manager, owner=guardian, requester=school, scope=Scope.MEASLES_STATUS)["expires_at"]
        demo_workflow.advance_time(self.client, expires_at)
        response = disclosure.verify_for_school(self.settings, guardian)
        self.no_fields("school at exactly expiresAt", response, OUTCOME_DENIED, "EXPIRED")
        self.rows["PY-02"].evidence.append(f"expired request tx {response['transaction_hash']}")
        self.grant("school", Scope.MEASLES_STATUS, 1, 0, "school regrant after expiry")

    def rewards(self) -> None:
        row = self.rows["PY-08"]
        balances = {label: self.balance(label) for label in self.accounts}
        supply = chain.call_view(self.contracts["ConsentRewardToken"].functions.totalSupply())
        row.see(
            "final balances " + ", ".join(f"{label} {balance}" for label, balance in balances.items()) + f", total supply {supply}",
            balances == {label: 2 if label == "guardian" else 0 for label in self.accounts} and supply == 2,
        )
        row.see(
            "the demo ends with guardian 2 and everyone else 0",
            "reward balances: deployer 0, clinic 0, guardian 2, school 0, doctor 0" in self.transcript,
        )

    def demo_denials(self) -> None:
        # every denied request line in the demo transcript and what it released
        row = self.rows["PY-02"]
        lines = [line for line in self.transcript.splitlines() if "-> denied (" in line or "-> unavailable (" in line]
        released = [line.rsplit("released: ", 1)[-1] for line in lines]
        row.see(f"demo transcript: {len(lines)} denied requests, released: {', '.join(released) or 'none'}",
                lines and all(text == "nothing" for text in released))

    # 4. failure output

    def failures(self) -> None:
        row = self.rows["PY-10"]
        with tempfile.TemporaryDirectory(dir=self.data_root, prefix="export-check-") as folder:
            temp = Path(folder)
            corrupt = temp / "corrupt_record.json"
            corrupt.write_bytes(b'{"child_id": "child-demo-001", "vaccinations": [')
            outside = self.data_root.parent / f"{self.data_root.name}-outside" / "vaccination_record.json"
            without_deployment_key = {key: value for key, value in self.settings.items() if key != "deployment_file"}
            menu_cases = [
                ("menu node down", "school", "school", dict(self.settings, rpc_url=self.closed_rpc_url),
                 "unavailable: local node not reachable or wrong chain"),
                ("menu no deployment", "doctor", "doctor", dict(self.settings, deployment_file=str(temp / "no-deployment.json")),
                 f"unavailable: {disclosure.NO_DEPLOYMENT_MESSAGE}"),
                ("menu register twice", "register", "guardian", self.settings, "rejected: AlreadyRegistered"),
                ("menu attest by the doctor", "attest", "doctor", self.settings, "rejected: NotTrustedClinic"),
                ("menu missing salt", "school", "school", dict(self.settings, vaccination_salt_file=str(temp / "no-salt.json")),
                 UNAVAILABLE_TEXT),
                ("menu bad record", "mine", "guardian", dict(self.settings, vaccination_file=str(corrupt)),
                 "unavailable: record is not valid UTF-8 JSON"),
                ("menu setup, record outside data_root", "setup", "guardian", dict(self.settings, vaccination_file=str(outside)),
                 "unavailable: record path must be inside the data root"),
                ("menu unexpected error", "doctor", "doctor", without_deployment_key, "failed: KeyError"),
            ]
            for label, choice, actor, settings, expected in menu_cases:
                printed = self.menu(choice, actor, settings)
                last = printed.rstrip("\n").splitlines()[-1] if printed.strip() else ""
                self.failure_seen(row, label, printed, last, expected)
            row.see(
                "nothing created for the record outside data_root" if not outside.parent.exists() else "a folder was created outside data_root",
                not outside.parent.exists(),
            )

            demo_cases = [
                ("demo no settings file", temp / "missing-settings.json", None,
                 "demo FAILED at step start: no settings file: copy config/settings.example.json to config/settings.json"),
                ("demo node down", temp / "node-down.json", dict(self.settings, rpc_url=self.closed_rpc_url),
                 "demo FAILED at step 0: unavailable: RPC connection failed: start the node with npm run node and check "
                 "rpc_url and expected_chain_id"),
                ("demo record outside data_root", temp / "record-outside.json", _outside_settings(self.settings, temp),
                 "demo FAILED at step 2: record path must be inside the data root"),
            ]
            for label, path, settings, expected in demo_cases:
                if settings is not None:
                    path.write_text(json.dumps(settings))
                code, output, errors = _run([sys.executable, "-m", "integration.demo_workflow", "--settings", str(path)])
                # the failure goes to stderr as one line; stdout is the transcript up to that step
                extra = ["a traceback"] if TRACEBACK in output else []
                self.failure_seen(row, label, errors, errors.strip(), expected, extra, code)
            elsewhere = temp / "elsewhere"
            row.see(
                "the demo created nothing outside its data_root" if not elsewhere.exists() else "the demo wrote outside its data_root",
                not elsewhere.exists(),
            )

    def failure_seen(
        self, row: Row, label: str, printed: str, last: str, expected: str, extra: list[str] | None = None,
        exit_code: int | None = None,
    ) -> None:
        # exit_code is only given for the demo, which must exit 1
        problems = list(dict.fromkeys([*(extra or []), *leaks(printed, self.values)]))
        if problems:
            row.see(f"{label}: printed {', '.join(problems)}", False)
        elif exit_code not in (None, 1):
            row.see(f"{label} -> '{last}', exit code {exit_code}", False)
        else:
            row.see(f"{label} -> '{last}'", last == expected)

    # 5. the chain scan

    def scan(self) -> None:
        row = self.rows["PY-07"]
        result = scan_transactions(self.client, self.first_block, self.values, self.local_hashes, self.demo_blocks)
        counts = result["counts"]
        row.see(
            f"{counts['transactions']} transactions scanned, {counts['demo']} of them the demo's ({counts['deployments']} "
            f"deployments and {counts['calls']} calls in all), with {counts['logs']} logs and {counts['writes']} storage writes",
            counts["transactions"] and counts["demo"],
        )
        problems = result["problems"]
        row.see(
            "every call, constructor and event decodes to address, bytes32, uint or bool values only"
            if not problems else f"{len(problems)} not plain: {'; '.join(problems[:3])}",
            not problems,
        )
        found = result["found"]
        row.see(
            f"none of the {len(self.values)} record, identity and salt values found (raw, hex or base64)"
            if not found else f"found: {', '.join(found)}",
            not found,
        )
        stored = result["stored"]
        row.see(
            f"stored words: {stored['local hash']} local hashes, {stored['number']} numbers, {stored['address']} addresses, "
            f"{stored['consent']} packed consent words (expiry and flags), {stored['other']} other",
            stored["other"] == 0 and stored["local hash"],
        )
        row.evidence.append(f"blocks {self.first_block}-{result['last_block']}, eth_getBlockByNumber and debug_traceTransaction")

    # helpers

    def grant(self, label: str, scope: Scope, days: int, reward: int, what: str) -> None:
        from web3.logs import DISCARD

        row = self.rows["PY-08"]
        guardian, token = self.accounts["guardian"], self.contracts["ConsentRewardToken"]
        before = self.balance("guardian")
        try:
            receipt = chain.grant_consent(
                self.contracts["ConsentManager"], guardian=guardian, requester=self.accounts[label], scope=scope, duration_days=days,
            )
        except TransactionRejected as error:
            # an earlier step went wrong (for example the consent was never revoked); the run goes on
            row.see(f"{what}: rejected {error.args[0] if error.args else 'Reverted'}", False)
            return
        after = self.balance("guardian")
        logs = [log for log in receipt["logs"] if log["address"].lower() == token.address.lower()]
        minted = [event["args"]["recipient"] for event in token.events.RewardMinted().process_receipt({"logs": logs}, errors=DISCARD)]
        to = f", RewardMinted to {', '.join(_label_of(self.accounts, address) for address in minted)}" if minted else ", no RewardMinted"
        row.see(
            f"{what}: guardian {before} -> {after}{to}",
            after == before + reward and [address.lower() for address in minted] == [guardian.lower()] * reward,
        )
        row.evidence.append(f"{what} tx {receipt['transaction_hash']}")

    def no_fields(self, label: str, response: AccessResponse, outcome: str, reason: str | None) -> None:
        # reason None: any reason, because an unavailable or pending answer is never about the record
        as_expected = response["fields"] == {} and response["outcome"] == outcome and (reason is None or response["reason"] == reason)
        self.rows["PY-02"].see(f"{label}: {_response_text(response)}", as_expected)

    def balance(self, label: str) -> int:
        return chain.get_reward_balance(self.contracts["ConsentRewardToken"], account=self.accounts[label])

    def menu(self, choice: str, actor: str, settings: dict[str, Any]) -> str:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            console.dispatch_action(choice, actor, settings)
        return output.getvalue()

    def sent_hash(self, transaction_hash: str) -> bytes:
        """observedHash as the calldata of a requestAccess transaction really carried it."""
        from web3 import Web3

        data = bytes(_node(lambda: self.client.eth.get_transaction(transaction_hash))["input"])
        if data[:4] != Web3.keccak(text="requestAccess(address,uint8,bytes32)")[:4]:
            raise ExportError("that transaction is not a requestAccess call")
        return bytes(self.client.codec.decode(["address", "uint8", "bytes32"], data[4:])[2])

    def event_of(self, transaction_hash: str) -> Any:
        block = int(_node(lambda: self.client.eth.get_transaction_receipt(transaction_hash))["blockNumber"])
        events = [
            event for event in chain.list_access_events(self.contracts["ConsentManager"], from_block=block)
            if event["transaction_hash"].lower() == transaction_hash.lower()
        ]
        if len(events) != 1:
            raise ExportError("no single AccessAttempt event for a checked request")
        return events[0]

    def latest_block(self) -> int:
        return int(_node(lambda: self.client.eth.block_number))

    def node_request(self, method: str, params: list[Any]) -> Any:
        answer = _node(lambda: self.client.provider.make_request(method, params))
        if not isinstance(answer, dict) or "error" in answer:
            raise ExportError(f"the node refused {method}")
        return answer.get("result")


def scan_transactions(
    client: Any, first_block: int, values: list[tuple[str, bytes]], local_hashes: set[bytes], demo_blocks: tuple[int, int],
) -> dict[str, Any]:
    """Read every transaction from first_block to the latest block, with its receipt and trace.
    Returns counts, ABI problems (anything that is not a known call, constructor or event with plain
    types), the labels of private values found anywhere, and what the stored words are.
    """
    from web3 import Web3

    artifacts = {name: chain.read_artifact(name) for name in chain.CONTRACT_NAMES}
    creation = {name: bytes.fromhex(artifact["bytecode"].removeprefix("0x")) for name, artifact in artifacts.items()}
    constructors, functions, events = {}, {}, {}
    for name, artifact in artifacts.items():
        functions[name], events[name] = {}, {}
        for entry in artifact["abi"]:
            types = [item["type"] for item in entry.get("inputs", [])]
            signature = f"{entry.get('name')}({','.join(types)})"
            if entry["type"] == "constructor":
                constructors[name] = types
            elif entry["type"] == "function":
                functions[name][bytes(Web3.keccak(text=signature)[:4])] = (entry["name"], types)
            elif entry["type"] == "event":
                events[name][bytes(Web3.keccak(text=signature))] = (entry["name"], entry["inputs"])
    counts = {"transactions": 0, "deployments": 0, "calls": 0, "demo": 0, "logs": 0, "writes": 0}
    stored = {"local hash": 0, "number": 0, "address": 0, "consent": 0, "other": 0}
    problems: list[str] = []
    blobs: list[bytes] = []
    deployed: dict[str, str] = {}
    addresses = {int(address, 16) for address in _node(lambda: client.eth.accounts)}
    writes: list[tuple[int, int]] = []
    last_block = int(_node(lambda: client.eth.block_number))
    for number in range(first_block, last_block + 1):
        block = _node(lambda: client.eth.get_block(number, full_transactions=True))
        for transaction in block["transactions"]:
            counts["transactions"] += 1
            counts["demo"] += demo_blocks[0] <= number <= demo_blocks[1]
            data = bytes(transaction["input"])
            blobs.append(data)
            receipt = _node(lambda: client.eth.get_transaction_receipt(transaction["hash"]))
            where = f"block {number}"
            if transaction["to"] is None:
                counts["deployments"] += 1
                name = next((name for name, code in creation.items() if data.startswith(code)), None)
                if name is None:
                    problems.append(f"{where}: an unknown contract creation")
                    continue
                problems += _plain(f"{where} {name} constructor", constructors.get(name, []), data[len(creation[name]):], client)
                deployed[str(receipt["contractAddress"]).lower()] = name
                addresses.add(int(receipt["contractAddress"], 16))
            else:
                counts["calls"] += 1
                name = deployed.get(str(transaction["to"]).lower())
                entry = functions.get(name, {}).get(data[:4])
                if entry is None:
                    problems.append(f"{where}: a call that is not a function of the deployed contracts")
                else:
                    problems += _plain(f"{where} {entry[0]}", entry[1], data[4:], client)
            for log in receipt["logs"]:
                counts["logs"] += 1
                topics = [bytes(topic) for topic in log["topics"]]
                blobs.append(b"".join(topics) + bytes(log["data"]))
                entry = events.get(deployed.get(str(log["address"]).lower()), {}).get(topics[0] if topics else b"")
                if entry is None:
                    problems.append(f"{where}: a log that is not an event of the deployed contracts")
                    continue
                inputs = entry[1]
                problems += _plain(f"{where} {entry[0]}", [item["type"] for item in inputs if not item["indexed"]], bytes(log["data"]), client)
                problems += [f"{where} {entry[0]}: indexed {item['type']}" for item in inputs if item["indexed"] and item["type"] not in PLAIN_TYPES]
            trace = _node(lambda: client.provider.make_request(
                "debug_traceTransaction", [Web3.to_hex(transaction["hash"]), {"disableMemory": True, "disableStorage": True}],
            ))
            if not isinstance(trace, dict) or not isinstance(trace.get("result"), dict):
                raise ExportError("the node gave no trace for a transaction (debug_traceTransaction)")
            writes += storage_writes(trace["result"])
    for slot, value in writes:
        counts["writes"] += 1
        blobs.append(slot.to_bytes(32, "big") + value.to_bytes(32, "big"))
        stored[classify_word(value, local_hashes, addresses)] += 1
    return {
        "counts": counts, "problems": problems, "found": find_values(blobs, values), "stored": stored, "last_block": last_block,
    }


def run_checks(settings_path: Path) -> LiveRun:
    """Every check in the order of the module docstring. Raises ExportError when the run cannot finish."""
    settings = _read_settings(settings_path)
    try:
        run = LiveRun(settings_path, settings)
    except records.RecordError as error:
        raise ExportError(str(error)) from None
    steps: list[tuple[str, Callable[[], None]]] = [
        ("unit tests", run.unit_tests),
        ("node", run.connect),
        ("scripted demo", run.demo),
        ("deploy fresh contracts", run.deploy),
        ("register", run.register),
        ("attest", run.attest),
        ("school and doctor", run.school_and_doctor),
        ("tamper", run.tamper),
        ("missing salt", run.missing_salt),
        ("late recheck", run.late_recheck),
        ("unsupported scopes", run.unsupported_scopes),
        ("pending and unreachable", run.pending_and_unreachable),
        ("expiry", run.expiry),
        ("rewards", run.rewards),
        ("demo denials", run.demo_denials),
        ("failure output", run.failures),
        ("chain scan", run.scan),
    ]
    for name, step in steps:
        print(f"{name} ...", flush=True)
        try:
            step()
        except ExportError as error:
            raise ExportError(f"{name}: {error}") from None
        except deploy_local.DeployError as error:
            raise ExportError(f"{name}: {error}") from None
        except (ChainUnavailable, TransactionRejected, TransactionPending, records.RecordError) as error:
            # these messages hold no details by design
            reason = error.args[0] if error.args else type(error).__name__
            raise ExportError(f"{name}: {type(error).__name__}: {reason}") from None
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Python checks on the local Hardhat node and write the PY rows")
    parser.add_argument(
        "--settings", type=Path, default=None, metavar="PATH", help="settings file (default: config/settings.json)",
    )
    parser.add_argument(
        "--output", type=Path, default=None, metavar="PATH", help="CSV to write (default: evaluation/results/test_results.csv)",
    )
    args = parser.parse_args(argv)
    # the evidence names the options that were given, never their paths
    command = COMMAND + " --settings PATH" * (args.settings is not None) + " --output PATH" * (args.output is not None)
    output = args.output or CSV_PATH
    try:
        run = run_checks(args.settings or SETTINGS_FILE)
    except ExportError as error:
        print(f"export_python_results: {error}", file=sys.stderr)
        _discard_stale(output)
        print("CSV not written.", file=sys.stderr)
        return 1
    except BaseException:
        _discard_stale(output)
        raise

    where = "data_root outside the repo" if _outside_repo(run.data_root) else f"data_root {_shown(run.data_root)}"
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    context = f"{command} (Hardhat node, chain {LOCAL_CHAIN_ID}, {where}); {git_revision()}; {when}"
    rows = [row.cells(context) for row in run.rows.values()]
    write_rows(output, rows)
    failed = [row.test_id for row in run.rows.values() if row.status != "pass"]
    print(f"{len(rows) - len(failed)} pass, {len(failed)} fail{': ' + ', '.join(failed) if failed else ''} -> {_shown(output)}")
    return 1 if failed else 0


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        settings = json.loads(Path(path).read_bytes())
    except OSError:
        raise ExportError("no settings file: copy config/settings.example.json and pass it with --settings") from None
    except ValueError:
        raise ExportError("the settings file is not valid JSON") from None
    if not isinstance(settings, dict):
        raise ExportError("the settings file is not a settings object")
    if settings.get("expected_chain_id") != LOCAL_CHAIN_ID:
        raise ExportError("the checks move node time, so they only run on the local Hardhat chain 31337")
    return settings


def _outside_settings(settings: dict[str, Any], temp: Path) -> dict[str, Any]:
    # a fresh data root in the temporary folder, and a record path outside it
    data = temp / "data"
    return dict(
        settings,
        data_root=str(data), deployment_file=str(data / "deployment.json"),
        vaccination_file=str(temp / "elsewhere" / "vaccination_record.json"),
        vaccination_salt_file=str(data / "private" / "vaccination_salt.json"),
        identity_directory=str(data / "identities"), identity_salt_directory=str(data / "private"),
    )


def _plain(where: str, types: list[str], data: bytes, client: Any) -> list[str]:
    # the ABI types are plain and the data decodes as exactly those types
    problems = [f"{where}: {kind}" for kind in types if kind not in PLAIN_TYPES]
    if problems:
        return problems
    if len(data) != 32 * len(types):
        return [f"{where}: {len(data)} bytes of arguments for {len(types)} words"]
    try:
        client.codec.decode(types, data)
    except Exception:
        return [f"{where}: arguments do not decode"]
    return []


def _run(command: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    # env None: the child gets this process's environment
    try:
        completed = subprocess.run(
            command, cwd=PROJECT_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=SUBPROCESS_TIMEOUT_SECONDS,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExportError(f"could not run {' '.join(command[1:4])}: {type(error).__name__}") from None
    return completed.returncode, completed.stdout, completed.stderr


def _node(request: Callable[[], Any]) -> Any:
    # raw node requests app.chain has no helper for
    try:
        return request()
    except Exception:
        raise ExportError("local node request failed") from None


def _git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()


def _closed_rpc_url() -> str:
    # a port that was free a moment ago, so nothing answers on it
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def _tree(folder: Path) -> list[tuple[str, int, int]] | None:
    if not folder.exists():
        return None
    return sorted((str(path.relative_to(folder)), path.stat().st_size, path.stat().st_mtime_ns) for path in folder.rglob("*"))


def _suite_text(ran: int | None, result: str | None, details: str, code: int) -> str:
    if ran is None or result is None:
        return f"no unittest summary (exit code {code})"
    return f"Ran {ran} tests: {result}{f' ({details})' if details else ''}"


def _response_text(response: AccessResponse) -> str:
    reason = f" {response['reason']}" if response["reason"] else ""
    return f"{response['outcome']}{reason}, fields {json.dumps(response['fields'])}"


def _allowed_with(response: AccessResponse, fields: dict[str, Any]) -> bool:
    return response["outcome"] == OUTCOME_ALLOWED and response["reason"] == Reason.ALLOWED.name and response["fields"] == fields


def _denied_with(response: AccessResponse, reason: str) -> bool:
    return response["outcome"] == OUTCOME_DENIED and response["reason"] == reason and response["fields"] == {}


def _event_text(event: Any) -> str:
    return f"{'allowed' if event['allowed'] else 'denied'} {Reason(event['reason']).name}"


def _label_of(accounts: dict[str, str], address: str) -> str:
    return next((label for label, account in accounts.items() if account.lower() == address.lower()), address)


def _hex(value: bytes) -> str:
    return f"0x{bytes(value).hex()}"


def _outside_repo(path: Path) -> bool:
    project, path = PROJECT_ROOT.resolve(), Path(path).resolve()
    return path != project and project not in path.parents


def _shown(path: Path) -> str:
    # relative to the project, or only the file name: never the home folder
    return records.shown_path(path)


def _discard_stale(path: Path) -> None:
    if Path(path).exists():
        Path(path).unlink()
        print(f"removed the previous {_shown(path)}: it does not match this run", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
