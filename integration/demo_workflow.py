# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Scripted end-to-end demo on the local Hardhat node, using real transactions.
Run after npm run compile, with npm run node running:
    .venv/bin/python -m integration.demo_workflow [--settings PATH]

It deploys fresh contracts first, so it can be run again and again. The expiry step moves node time
forward, so it runs last and only on chain 31337. Every result is checked with expect(), and a
wrong result stops the run with "demo FAILED".
"""
import argparse
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn
from app import chain, disclosure, records
from app.models import (
    Scope, Reason, AccessResponse, Receipt,
    ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, ArtifactUnavailable, TransactionRejected,
    TransactionPending, OUTCOME_ALLOWED, OUTCOME_DENIED,
)
from scripts import deploy_local

SETTINGS_FILE = records.PROJECT_ROOT / "config" / "settings.json"
LOCAL_CHAIN_ID = 31337
GRANT_DAYS = 30
REGRANT_DAYS = 1
DAY_SECONDS = 24 * 60 * 60
# where the tampered copy goes, inside the data root
TAMPER_COPY = Path("tamper") / "vaccination_record.json"
# the access attempts the story should log, in order
EXPECTED_AUDIT = [
    ("school", Scope.MEASLES_STATUS, False, "NO_CONSENT"),
    ("school", Scope.MEASLES_STATUS, True, "ALLOWED"),
    ("doctor", Scope.VACCINATION_SCHEDULE, True, "ALLOWED"),
    ("doctor", Scope.VACCINATION_SCHEDULE, False, "HASH_MISMATCH"),
    ("school", Scope.MEASLES_STATUS, False, "REVOKED"),
    ("school", Scope.MEASLES_STATUS, False, "EXPIRED"),
]
# indent for the output lines
PAD = " " * 10


def deploy_demo_contracts(settings_path: Path) -> None:
    """Deploy fresh contracts so each run starts clean."""
    deploy_local.run(settings_path, reset=True)


def register_demo_accounts(settings: dict[str, Any]) -> None:
    """Register the guardian, school and doctor identities. Only the identity hash goes on-chain."""
    created = records.setup_runtime(settings)
    for path in created:
        print(f"setup: created {_shown(path, settings)}")
    if not created:
        print("setup: every local file already exists; salts kept, so every hash matches the last run")

    client, accounts, contracts = _session(settings)
    expect(len(set(accounts.values())) == len(accounts), "every actor label needs its own account")
    for label, address in accounts.items():
        print(f"account: {label:<8} {address}")
    registry = contracts["IdentityRegistry"]
    for label in records.REGISTERING_LABELS:
        identity_hash = records.prepare_identity(*records.identity_paths(settings, label))
        receipt = chain.register_user(registry, account=accounts[label], identity_hash=identity_hash)
        info = chain.get_user_info(registry, account=accounts[label])
        expect(
            info["registered"] and info["identity_hash"] == identity_hash, f"{label} is not registered with its identity hash",
        )
        _show(label, f"registerUser({_hex(identity_hash)})", "ok, the on-chain identity hash is the local one", receipt)
    for label in ("deployer", "clinic"):
        expect(not chain.get_user_info(registry, account=accounts[label])["registered"], f"{label} must not be registered")
    print("deployer and clinic are not registered: they never register")


def attest_demo_record(settings: dict[str, Any]) -> None:
    """The clinic registers the record's hash.
    The guardian tries first and must be rejected with NotTrustedClinic.
    """
    record_path = records.settings_path(settings, "vaccination_file")
    snapshot = records.load_snapshot(record_path, records.settings_path(settings, "vaccination_salt_file"))
    commitment = snapshot["commitment"]
    print(f"local record {_shown(record_path, settings)} ({len(snapshot['raw_bytes'])} bytes, frozen, never written again):")
    for line in snapshot["raw_bytes"].decode("utf-8").splitlines():
        print(f"  {line}")
    print(f"local commitment {_hex(commitment)}")
    print(f"{PAD}= SHA-256(\"VACCINATION:v1\\n\" + 32-byte private salt + the exact file bytes); the salt stays local")

    client, accounts, contracts = _session(settings)
    registry = contracts["IdentityRegistry"]
    guardian = accounts["guardian"]
    rejected = ""
    try:
        chain.register_vaccination(registry, clinic=guardian, guardian=guardian, record_hash=commitment)
    except TransactionRejected as error:
        rejected = error.args[0] if error.args else "Reverted"
    expect(rejected == "NotTrustedClinic", "only the trusted clinic may attest a record")
    _show("guardian", "registerVaccination(guardian, commitment), its own record", "rejected: NotTrustedClinic, nothing stored")

    receipt = chain.register_vaccination(registry, clinic=accounts["clinic"], guardian=guardian, record_hash=commitment)
    registered = chain.get_user_info(registry, account=guardian)["vaccination_hash"]
    expect(registered == commitment, "the registered commitment is not the local one")
    _show("clinic", "registerVaccination(guardian, commitment)", "ok", receipt)
    print(f"on-chain commitment {_hex(registered)} (matches the local record: yes)")


def demonstrate_school_flow(settings: dict[str, Any]) -> None:
    """School is denied before consent, then gets only the measles status. The first grant rewards the guardian."""
    client, accounts, contracts = _session(settings)
    guardian, school = accounts["guardian"], accounts["school"]
    response = disclosure.verify_for_school(settings, guardian)
    _show_access(client, "school", "requestAccess(guardian, MEASLES_STATUS)", response)
    _expect_denied(response, "NO_CONSENT")

    _grant(client, contracts, guardian, "school", school, Scope.MEASLES_STATUS, GRANT_DAYS, reward=1)
    expect(chain.get_reward_balance(contracts["ConsentRewardToken"], account=school) == 0, "the requester must not be rewarded")
    print(f"{PAD}the school, as requester, holds 0 reward units")

    response = disclosure.verify_for_school(settings, guardian)
    _show_access(client, "school", "requestAccess(guardian, MEASLES_STATUS)", response)
    _expect_allowed(response, {"measles_status": "verified"})
    print(f"{PAD}status only: no vaccine, date, clinic, batch or child ID")


def demonstrate_doctor_flow(settings: dict[str, Any]) -> None:
    """Doctor gets only vaccine and date. Another first grant, so another reward."""
    card = records.load_snapshot(
        records.settings_path(settings, "vaccination_file"),
        records.settings_path(settings, "vaccination_salt_file"),
    )["card"]
    # built separately so the check does not reuse disclosure's code
    expected = {"vaccinations": [{"vaccine": event["vaccine"], "date": event["date"]} for event in card["vaccinations"]]}

    client, accounts, contracts = _session(settings)
    guardian = accounts["guardian"]
    _grant(client, contracts, guardian, "doctor", accounts["doctor"], Scope.VACCINATION_SCHEDULE, GRANT_DAYS, reward=1)

    response = disclosure.get_doctor_schedule(settings, guardian)
    _show_access(client, "doctor", "requestAccess(guardian, VACCINATION_SCHEDULE)", response)
    _expect_allowed(response, expected)
    print(f"{PAD}vaccine and date only: no child ID, coverage list, clinic or batch")


def demonstrate_tampering(settings: dict[str, Any], copied_fixture: Path) -> None:
    """Change one byte in a copy of the record and check the doctor request is denied (HASH_MISMATCH).
    The copy is deleted afterwards and the original is never written.
    """
    original = records.settings_path(settings, "vaccination_file")
    copy_path = records.PROJECT_ROOT / Path(copied_fixture)
    # never overwrite or delete an existing file
    expect(not copy_path.exists() and copy_path.resolve() != original.resolve(), "tamper copy must be a new file")
    expect(records.data_root(settings).resolve() in copy_path.resolve().parents, "tamper copy must be inside the data root")
    client = chain.connect(settings)
    owner = chain.select_account(client, "guardian", settings)
    raw_bytes = records.read_record_bytes(original)
    expect(b"ABC123-DEMO" in raw_bytes, "batch value ABC123-DEMO not found in the record")
    try:
        copy_path.parent.mkdir(parents=True, exist_ok=True)
        with open(copy_path, "xb") as file:
            file.write(raw_bytes.replace(b"ABC123-DEMO", b"ABC124-DEMO", 1))
        print("tamper: copied the record and changed one byte of the batch in the copy")
        response = disclosure.get_doctor_schedule(dict(settings, vaccination_file=str(copy_path)), owner)
        expect(response["fields"] == {}, "a tampered record must release nothing")
        print(f"tamper: doctor request on the copy -> {disclosure.denial_message(response)}")
        expect(response["outcome"] == OUTCOME_DENIED and response["reason"] == "HASH_MISMATCH", "expected denied: HASH_MISMATCH")
    finally:
        copy_path.unlink(missing_ok=True)
        print("tamper: copy deleted")
    registry = chain.load_contract(client, "IdentityRegistry", records.settings_path(settings, "deployment_file"))
    snapshot = records.load_snapshot(original, records.settings_path(settings, "vaccination_salt_file"))
    expect(snapshot["commitment"] == chain.get_user_info(registry, account=owner)["vaccination_hash"], "original no longer verifies")
    print("tamper: the original record still matches the registered commitment")


def demonstrate_revocation_and_expiry(settings: dict[str, Any]) -> None:
    """Revoke the school's consent, regrant it for 1 day, then move node time to the exact expiry.
    Run this last, because node time cannot go back.
    """
    client, accounts, contracts = _session(settings)
    guardian, school = accounts["guardian"], accounts["school"]
    manager = contracts["ConsentManager"]
    scope = Scope.MEASLES_STATUS

    receipt = chain.revoke_consent(manager, guardian=guardian, requester=school, scope=scope)
    expect(chain.get_consent(manager, owner=guardian, requester=school, scope=scope)["revoked"], "the school grant is not revoked")
    _show("guardian", "revokeConsent(school, MEASLES_STATUS)", "ok, the stored grant is now revoked", receipt)
    response = disclosure.verify_for_school(settings, guardian)
    _show_access(client, "school", "requestAccess(guardian, MEASLES_STATUS)", response)
    _expect_denied(response, "REVOKED")

    receipt = _grant(client, contracts, guardian, "school", school, scope, REGRANT_DAYS, reward=0)
    consent = chain.get_consent(manager, owner=guardian, requester=school, scope=scope)
    expires_at = consent["expires_at"]
    granted_at = _block_timestamp(client, receipt["block_number"])
    expect(not consent["revoked"], "the regrant must clear the revoked flag")
    expect(expires_at == granted_at + REGRANT_DAYS * DAY_SECONDS, "expiresAt is not the regrant's block time plus one day")
    print(f"{PAD}expiresAt {expires_at} ({_utc(expires_at)}) = regrant block time {granted_at} + 86400 s")

    advance_time(client, expires_at - 1, mine=True)
    expect(_block_timestamp(client, "latest") == expires_at - 1, "the empty block is not at expiresAt - 1")
    _show("node", "evm_setNextBlockTimestamp(expiresAt - 1), then evm_mine", f"latest block time {expires_at - 1}")
    decision = chain.check_access(manager, owner=guardian, requester=school, scope=scope)
    expect(decision["allowed"], "the 1-day grant must still hold one second before expiresAt")
    _show(
        "view", "checkAccess(guardian, school, MEASLES_STATUS)",
        f"allowed ({Reason(decision['reason']).name}); a view, so nothing is logged or released",
    )

    advance_time(client, expires_at)
    _show("node", "evm_setNextBlockTimestamp(expiresAt)", f"the next block's time is set to {expires_at}")
    response = disclosure.verify_for_school(settings, guardian)
    _show_access(client, "school", "requestAccess(guardian, MEASLES_STATUS)", response)
    _expect_denied(response, "EXPIRED")
    event = _event_for(manager, response["transaction_hash"], receipt["block_number"])
    expect(event["timestamp"] == expires_at, "the expired request did not land exactly on expiresAt")
    print(f"{PAD}AccessAttempt timestamp {event['timestamp']} == expiresAt: the grant ends at that second")


def show_rewards_and_audit(settings: dict[str, Any]) -> None:
    """Print the reward balances and the audit log, and check them against the story."""
    client, accounts, contracts = _session(settings)
    token, manager = contracts["ConsentRewardToken"], contracts["ConsentManager"]
    balances = {label: chain.get_reward_balance(token, account=address) for label, address in accounts.items()}
    print("reward balances: " + ", ".join(f"{label} {balance}" for label, balance in balances.items()))
    expected_balances = {label: 2 if label == "guardian" else 0 for label in accounts}
    expect(balances == expected_balances, "only the guardian holds rewards, one per first grant")

    labels = {address.lower(): label for label, address in accounts.items()}
    events = chain.list_access_events(manager, from_block=0)
    print(f"audit log: {len(events)} AccessAttempt events from ConsentManager {manager.address}")
    for event in events:
        requester = labels.get(event["requester"].lower(), event["requester"])
        result = "allowed" if event["allowed"] else "denied"
        print(
            f"  {_utc(event['timestamp'])}  {requester:<7} {_scope_name(event['scope']):<20} "
            f"{result:<8} {Reason(event['reason']).name:<14} {event['transaction_hash']}"
        )
    observed = [
        (labels.get(event["requester"].lower()), event["scope"], event["allowed"], Reason(event["reason"]).name)
        for event in events
    ]
    expect(observed == EXPECTED_AUDIT, "the audit log does not match the story")
    owners = {event["owner"].lower() for event in events}
    expect(owners == {accounts["guardian"].lower()}, "every attempt is for the guardian's record")


def main(argv: list[str] | None = None) -> None:
    """Run every step in order. A wrong result or an unreachable node prints one line and exits 1."""
    parser = argparse.ArgumentParser(description="Run the scripted My Vaccination Card demo on the local Hardhat node")
    parser.add_argument(
        "--settings", type=Path, default=SETTINGS_FILE, metavar="PATH",
        help="settings file (default: config/settings.json)",
    )
    args = parser.parse_args(argv)
    try:
        settings = json.loads(Path(args.settings).read_bytes())
    except OSError:
        _fail("start", "no settings file: copy config/settings.example.json to config/settings.json")
    except ValueError:
        _fail("start", "the settings file is not valid JSON")
    if not isinstance(settings, dict):
        _fail("start", "the settings file is not a settings object")

    steps: list[tuple[str, Callable[[], None]]] = [
        ("check the node", lambda: _check_local_node(settings)),
        ("deployer deploys fresh contracts (deploy_local --reset)", lambda: deploy_demo_contracts(args.settings)),
        ("local files, then guardian, school and doctor register", lambda: register_demo_accounts(settings)),
        ("the clinic attests the guardian's record", lambda: attest_demo_record(settings)),
        ("school: denied before consent, then status only", lambda: demonstrate_school_flow(settings)),
        ("doctor: vaccine and date only", lambda: demonstrate_doctor_flow(settings)),
        (
            "tamper a copy while the doctor grant is active",
            lambda: demonstrate_tampering(settings, records.data_root(settings) / TAMPER_COPY),
        ),
        ("revoke school, regrant for 1 day, exact expiry", lambda: demonstrate_revocation_and_expiry(settings)),
        ("reward balances and the on-chain audit log", lambda: show_rewards_and_audit(settings)),
    ]
    print("My Vaccination Card: scripted demo on the local Hardhat node")
    for number, (title, step) in enumerate(steps):
        print(f"\n== {number}. {title} ==", flush=True)
        try:
            step()
        except (AssertionError, ValueError, deploy_local.DeployError) as error:
            _fail(number, str(error))
        except TransactionRejected as error:
            _fail(number, f"rejected: {error.args[0] if error.args else 'Reverted'}")
        except TransactionPending:
            _fail(number, "pending: no receipt in time")
        except ArtifactUnavailable:
            _fail(number, disclosure.NO_ARTIFACTS_MESSAGE)
        except DeploymentUnavailable:
            _fail(number, disclosure.NO_DEPLOYMENT_MESSAGE)
        except Web3NotInstalled:
            _fail(number, disclosure.NO_WEB3_MESSAGE)
        except ChainUnavailable as error:
            _fail(number, f"unavailable: {error.args[0] if error.args else 'local node not reachable'}")
    print("\ndemo passed: every outcome above was checked")


def advance_time(client: Any, timestamp: int, mine: bool = False) -> None:
    """Set the time of the next block (and mine it if mine=True).
    Only works on chain 31337 and only forwards in time.
    """
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ValueError("timestamp must be a whole number of seconds")
    if _node(lambda: client.eth.chain_id) != LOCAL_CHAIN_ID:
        raise ChainUnavailable("time can only be moved on the local Hardhat chain 31337")
    if timestamp <= _block_timestamp(client, "latest"):
        raise ValueError("node time cannot go back")
    _node(lambda: client.manager.request_blocking("evm_setNextBlockTimestamp", [timestamp]))
    if mine:
        _node(lambda: client.manager.request_blocking("evm_mine", []))


def expect(condition: bool, message: str) -> None:
    """Like assert, but still runs with python -O."""
    if not condition:
        raise AssertionError(message)


def _check_local_node(settings: dict[str, Any]) -> None:
    # check early, the expiry step only works on the local chain
    try:
        client = chain.connect(settings)
    except Web3NotInstalled:
        raise
    except ChainUnavailable as error:
        reason = error.args[0] if error.args else "local node not reachable"
        raise ChainUnavailable(f"{reason}: start the node with npm run node and check rpc_url and expected_chain_id") from None
    chain_id = _node(lambda: client.eth.chain_id)
    expect(chain_id == LOCAL_CHAIN_ID, "the demo moves node time, so it only runs on the local Hardhat chain 31337")
    now = _block_timestamp(client, "latest")
    print(f"node {settings['rpc_url']}, chain {chain_id}, node time {_utc(now)}")


def _session(settings: dict[str, Any]) -> tuple[Any, dict[str, str], dict[str, Any]]:
    # connection, all accounts and the three contracts
    client = chain.connect(settings)
    accounts = {label: chain.select_account(client, label, settings) for label in settings["actor_account_indices"]}
    deployment = records.settings_path(settings, "deployment_file")
    contracts = {name: chain.load_contract(client, name, deployment) for name in chain.CONTRACT_NAMES}
    return client, accounts, contracts


def _grant(
    client: Any, contracts: dict[str, Any], guardian: str, label: str, requester: str, scope: Scope, days: int, reward: int,
) -> Receipt:
    # the manager mints the reward inside the grant transaction
    token, manager = contracts["ConsentRewardToken"], contracts["ConsentManager"]
    before = chain.get_reward_balance(token, account=guardian)
    receipt = chain.grant_consent(manager, guardian=guardian, requester=requester, scope=scope, duration_days=days)
    after = chain.get_reward_balance(token, account=guardian)
    expect(after == before + reward, f"guardian reward should change by +{reward}, not {before} -> {after}")
    expires_at = chain.get_consent(manager, owner=guardian, requester=requester, scope=scope)["expires_at"]
    note = "first grant of this consent" if reward else "this consent was already rewarded once"
    _show(
        "guardian", f"grantConsent({label}, {scope.name}, {days} day{'' if days == 1 else 's'})",
        f"ok, reward balance of guardian {before} -> {after} (+{reward}: {note}); expires {_utc(expires_at)}", receipt,
    )
    return receipt


def _expect_denied(response: AccessResponse, reason: str) -> None:
    expect(response["fields"] == {}, "a denied request must release nothing")
    expect(
        response["outcome"] == OUTCOME_DENIED and response["reason"] == reason,
        f"expected denied: {reason}, got {response['outcome']} {response['reason']}".rstrip(),
    )


def _expect_allowed(response: AccessResponse, fields: dict[str, Any]) -> None:
    expect(
        response["outcome"] == OUTCOME_ALLOWED and response["reason"] == Reason.ALLOWED.name,
        f"expected allowed, got {response['outcome']} {response['reason']}".rstrip(),
    )
    expect(response["fields"] == fields, "the released fields are not exactly the allowlisted ones")


def _event_for(manager: Any, transaction_hash: str, from_block: int) -> Any:
    events = [
        event for event in chain.list_access_events(manager, from_block=from_block)
        if event["transaction_hash"].lower() == transaction_hash.lower()
    ]
    expect(len(events) == 1, "no single AccessAttempt event for that request")
    return events[0]


def _show(actor: str, action: str, outcome: str, receipt: Receipt | None = None) -> None:
    print(f"{actor:<9} {action}")
    print(f"{PAD}-> {outcome}")
    if receipt is not None:
        print(f"{PAD}   tx {receipt['transaction_hash']} (block {receipt['block_number']}, gas {receipt['gas_used']})")


def _show_access(client: Any, actor: str, action: str, response: AccessResponse) -> None:
    released = json.dumps(response["fields"]) if response["fields"] else "nothing"
    outcome = f"{response['outcome']} ({response['reason'] or 'no event'}); released: {released}"
    _show(actor, action, outcome, _mined(client, response["transaction_hash"]) if response["transaction_hash"] else None)


def _mined(client: Any, transaction_hash: str) -> Receipt:
    # the response only has the hash, so get block and gas from the node
    raw = _node(lambda: client.eth.get_transaction_receipt(transaction_hash))
    return {
        "transaction_hash": transaction_hash,
        "status": int(raw["status"]),
        "gas_used": int(raw["gasUsed"]),
        "block_number": int(raw["blockNumber"]),
        "logs": [],
        "contract_address": None,
    }


def _block_timestamp(client: Any, block: int | str) -> int:
    return int(_node(lambda: client.eth.get_block(block))["timestamp"])


def _node(request: Callable[[], Any]) -> Any:
    # for node requests chain.py has no helper for
    try:
        return request()
    except Exception as error:
        raise ChainUnavailable("local node request failed") from error


def _fail(step: int | str, reason: str) -> NoReturn:
    print(f"demo FAILED at step {step}: {reason}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _hex(value: bytes) -> str:
    return f"0x{bytes(value).hex()}"


def _utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _scope_name(value: int) -> str:
    return Scope(value).name if value in (1, 2) else f"scope {value} (unsupported)"


def _shown(path: Path, settings: dict[str, Any]) -> str:
    return records.shown_path(path, settings)


if __name__ == "__main__":
    main()
