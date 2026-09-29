# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Gas and timing measurements from real local transactions.
Run from the project root after npm run compile, with npm run node running:
    .venv/bin/python -m evaluation.measure [--settings PATH] [--output-dir DIR]
For 1, 5 and 10 requesters (scenario_requester_account_indices) it deploys fresh contracts and runs one
scenario in which every role acts, then writes gas_results.csv, timing_results.csv (one row per operation)
and timing_summary.csv (per-role and whole-run means over the same transactions, kept apart so that no
transaction is counted twice in one table) with the template headers, plus ENVIRONMENT.md (compiler, node,
automine, machine), to evaluation/results/.
Gas is gasUsed from successful receipts; times come from time.monotonic around each transaction. Nothing is
estimated, converted to money or prefilled: a missing row or a blank cell means not measured, never zero.
These are local Hardhat automine measurements for the demo, not public-chain throughput or latency.
Identities and the record hash are synthetic hashes made here, so no local file or runtime-data is read.
"""
import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any
from app import chain, disclosure, records
from app.models import (
    Receipt, Reason, Scope, ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, TransactionRejected,
    TransactionPending,
)
from app.records import PROJECT_ROOT

SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.json"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
TEMPLATES_DIR = PROJECT_ROOT / "evaluation" / "templates"
GAS_FILE = "gas_results.csv"
TIMING_FILE = "timing_results.csv"
TIMING_SUMMARY_FILE = "timing_summary.csv"
ENVIRONMENT_FILE = "ENVIRONMENT.md"
REQUESTER_COUNTS = (1, 5, 10)
LOCAL_CHAIN_ID = 31337
DEPLOY_ORDER = ("IdentityRegistry", "ConsentRewardToken", "ConsentManager")
SCOPE = Scope.MEASLES_STATUS
GRANT_DAYS = 30
SHORT_GRANT_DAYS = 1
COMPILER_NOTE = "solc 0.8.28, optimiser on, 200 runs"
SUMMARY_KEYS = ("requester_count", "role", "contract", "function", "scenario")
ROLE_LABELS = ("deployer", "clinic", "guardian")
NO_ROLES = "the settings have no usable deployer, clinic and guardian account indices"
# an uncommitted change under these changes what the tables measure, so ENVIRONMENT.md's commit label says so
CODE_PATHS = ("contracts", "app", "scripts", "evaluation/measure.py", "hardhat.config.ts", "package.json")

# scenario labels; the gas and timing tables group by them
DEPLOYED = "fresh deployment for the run"
MINTER = "one-time minter setup (ConsentManager)"
REGISTERS = "registers a synthetic identity hash"
ATTESTED = "attests the guardian's synthetic record hash"
FIRST_REWARD = f"first rewarded grant, first reward of the deployment ({GRANT_DAYS} days)"
LATER_REWARD = f"first rewarded grant, guardian already holds rewards ({GRANT_DAYS} days)"
REGRANT_AFTER_REVOKE = f"unrewarded regrant after revoke ({SHORT_GRANT_DAYS} day)"
REGRANT_AFTER_EXPIRY = f"unrewarded regrant after expiry ({GRANT_DAYS} days)"
REVOKED_ACTIVE = "revoke an active grant"
REQUESTS = {
    Reason.MISSING_EVIDENCE: "denied MISSING_EVIDENCE (both registered, record not attested yet)",
    Reason.NO_CONSENT: "denied NO_CONSENT (attested, never granted)",
    Reason.ALLOWED: "allowed (active grant, matching hash)",
    Reason.HASH_MISMATCH: "denied HASH_MISMATCH (active grant, one byte of the hash changed)",
    Reason.REVOKED: "denied REVOKED (grant revoked)",
    Reason.EXPIRED: f"denied EXPIRED ({SHORT_GRANT_DAYS}-day regrant, node time moved to its expiry)",
}

DENIED_NOTE = "one AccessAttempt with allowed=false; a denial is a successful transaction, not a revert"
GAS_NOTES = {
    DEPLOYED: "creation receipt: 53,000 intrinsic (21,000 base + 32,000 create), init-code calldata, constructor "
    "and 200 gas per stored byte of runtime code",
    MINTER: "deployer, right after the ConsentManager deploy",
    FIRST_REWARD: "includes the internal mintReward call (RewardMinted in the same receipt, not counted again); "
    "balance and totalSupply are written from 0",
    LATER_REWARD: "includes the internal mintReward call (RewardMinted in the same receipt, not counted again)",
    REGRANT_AFTER_REVOKE: "no RewardMinted in the receipt",
    REGRANT_AFTER_EXPIRY: "no RewardMinted in the receipt",
    REQUESTS[Reason.ALLOWED]: "one AccessAttempt with allowed=true; no token moves",
    **{scenario: DENIED_NOTE for reason, scenario in REQUESTS.items() if reason != Reason.ALLOWED},
    REQUESTS[Reason.EXPIRED]: f"{DENIED_NOTE}; the time step before it (evm_setNextBlockTimestamp, evm_mine) is not measured",
}
TIMING_NOTES = {
    ("deployer", "deployment"): "send to verified deploy, not send to receipt: timed around deploy_local's "
    "verified step (artifact file read, send, receipt, runtime code read back, and trustedClinic() for the "
    "registry); a constructor emits no event",
    ("deployer", "setMinterOnce"): "timed around deploy_local.configure_minter (artifact file read, send, "
    "receipt, minter() read back) before MinterConfigured is decoded",
}


class MeasureError(RuntimeError):
    """The measurement run cannot continue or its result would be wrong. The message is safe to print."""


def record_deployment_cost(contract_name: str, receipt: Receipt) -> dict[str, Any]:
    """Extract actual gasUsed for one successful deployment, not an estimate or fiat conversion.
    Returns contract, gas_used, transaction_hash, block_number and contract_address from the receipt.
    A receipt that is not successful, or has no positive whole gasUsed, raises ValueError.
    deploy_local.deploy_all calls this for its three deploy receipts, and so does the scenario here.
    """
    if contract_name not in chain.CONTRACT_NAMES:
        raise ValueError("unknown contract")
    try:
        status, gas_used = receipt["status"], receipt["gas_used"]
    except (KeyError, TypeError):
        raise ValueError("not a transaction receipt") from None
    if status != 1 or isinstance(gas_used, bool) or not isinstance(gas_used, int) or gas_used <= 0:
        raise ValueError("not a successful deployment receipt")
    return {
        "contract": contract_name,
        "gas_used": gas_used,
        "transaction_hash": receipt.get("transaction_hash", ""),
        "block_number": receipt.get("block_number"),
        "contract_address": receipt.get("contract_address"),
    }


def measure_transaction(send_transaction: Any, decode_event: Any = None) -> dict[str, Any]:
    """Use a monotonic clock before send, after successful receipt and after decoding the event.
    Return elapsed seconds and actual gasUsed; report failed/pending calls separately.
    send_transaction takes no arguments, sends one transaction and returns its successful Receipt (the
    app.chain helpers do exactly that). decode_event, if given, takes that receipt and returns the decoded
    event, raising ChainUnavailable when it is missing or wrong.
    status is "ok", or for a failure "rejected" (error holds the Solidity error name), "pending",
    "unavailable" or "no_event". Failed calls keep gas_used and the times None, so they never enter an
    average. started_at and finished_at are the raw monotonic readings, for the run's wall clock.
    """
    started = time.monotonic()
    result: dict[str, Any] = {
        "status": "ok", "error": "", "gas_used": None, "receipt_seconds": None, "event_seconds": None,
        "transaction_hash": "", "block_number": None, "receipt": None, "event": None,
        "started_at": started, "finished_at": None,
    }
    try:
        receipt = send_transaction()
    except TransactionRejected as error:
        result.update(status="rejected", error=error.args[0] if error.args else "Reverted")
    except TransactionPending:
        result.update(status="pending", error="no receipt in time")
    except ChainUnavailable:
        result.update(status="unavailable", error="node, receipt or deployment unavailable")
    if result["status"] != "ok":
        result["finished_at"] = time.monotonic()
        return result

    received = time.monotonic()
    result.update(
        receipt=receipt, transaction_hash=receipt["transaction_hash"], block_number=receipt["block_number"],
        finished_at=received,
    )
    if decode_event is not None:
        try:
            result["event"] = decode_event(receipt)
        except ChainUnavailable:
            result.update(status="no_event", error="expected event missing", finished_at=time.monotonic())
            return result
        result["finished_at"] = time.monotonic()
        result["event_seconds"] = result["finished_at"] - started
    result.update(gas_used=receipt["gas_used"], receipt_seconds=received - started)
    return result


def run_requester_scenario(settings: dict[str, Any], requester_count: int) -> list[dict[str, Any]]:
    """Run a documented local scenario for 1, 5 or 10 requesters with independent accounts.
    Keep first rewarded grants, unrewarded regrants, allowed and denied access in distinct groups.
    Every role acts: deployer (deploy, set minter), clinic (attest), guardian (register, grant, revoke),
    requesters from scenario_requester_account_indices (register, request). Redeploy fresh contracts
    before each run, then re-register and re-attest. Report mean gas and time per operation and role.

    Order, one transaction per block: deploy the three contracts and set the minter; the guardian and each
    requester register; each requester is denied MISSING_EVIDENCE; the clinic attests; each is denied
    NO_CONSENT; the guardian grants each one (rewarded); each is allowed, then denied HASH_MISMATCH with one
    changed byte; the guardian revokes each (REVOKED) and regrants for 1 day (no reward); node time is set
    to the latest expiry (EXPIRED); the guardian regrants after expiry (no reward). Every outcome is checked
    against the decoded event and the reward balances at the end, so a wrong contract stops the run.
    Returns one sample per transaction (see measure_transaction) with requester_count, role, contract,
    function, scenario and order (phase, index), for summarize_samples.
    """
    # imported here: deploy_local imports this module for record_deployment_cost
    from scripts import deploy_local

    if isinstance(requester_count, bool) or not isinstance(requester_count, int) or requester_count < 1:
        raise ValueError("requester_count must be a positive whole number")
    client = chain.connect(settings)
    _require_local_node(client)
    try:
        deployer, clinic, guardian = (chain.select_account(client, label, settings) for label in ROLE_LABELS)
    except ValueError:
        raise MeasureError(NO_ROLES) from None
    requesters = requester_accounts(client, settings, requester_count)
    _warm_up(client, deploy_local, deployer, clinic)
    run = f"run N={requester_count}"
    samples: list[dict[str, Any]] = []
    phase = 0

    def record(role: str, contract: str, function: str, scenario: str, measurement: dict[str, Any], index: int = 0) -> None:
        sample = {key: measurement[key] for key in measurement if key not in ("receipt", "event")}
        sample.update(
            requester_count=requester_count, role=role, contract=contract, function=function,
            scenario=scenario, order=(phase, index),
        )
        samples.append(sample)

    def deploy(name: str, step: Any) -> str:
        nonlocal phase
        phase += 1
        deployed: dict[str, str] = {}

        def send() -> Receipt:
            deployed["address"], receipt = step()
            return receipt

        measurement = measure_transaction(send)
        if measurement["status"] != "ok":
            raise MeasureError(f"{run}: {name} deploy failed: {measurement['error']}")
        measurement["gas_used"] = record_deployment_cost(name, measurement["receipt"])["gas_used"]
        record("deployer", name, "deployment", DEPLOYED, measurement)
        return deployed["address"]

    registry_address = deploy("IdentityRegistry", lambda: deploy_local.deploy_registry(client, deployer, clinic))
    token_address = deploy("ConsentRewardToken", lambda: deploy_local.deploy_reward_token(client, deployer))
    manager_address = deploy(
        "ConsentManager",
        lambda: deploy_local.deploy_consent_manager(client, deployer, registry_address, token_address),
    )
    registry = _contract(client, deploy_local, "IdentityRegistry", registry_address)
    token = _contract(client, deploy_local, "ConsentRewardToken", token_address)
    manager = _contract(client, deploy_local, "ConsentManager", manager_address)

    phase += 1
    record("deployer", "ConsentRewardToken", "setMinterOnce", MINTER, measure_transaction(
        lambda: deploy_local.configure_minter(client, deployer, token_address, manager_address),
        lambda receipt: _one_event(token, "MinterConfigured", receipt),
    ))

    phase += 1
    guardian_identity = synthetic_hash(f"guardian identity, {run}")
    record("guardian", "IdentityRegistry", "registerUser", REGISTERS, measure_transaction(
        lambda: chain.register_user(registry, account=guardian, identity_hash=guardian_identity),
        lambda receipt: _one_event(registry, "UserRegistered", receipt),
    ))
    phase += 1
    for index, requester in enumerate(requesters):
        identity = synthetic_hash(f"requester {index + 1} identity, {run}")
        record("requester", "IdentityRegistry", "registerUser", REGISTERS, measure_transaction(
            lambda requester=requester, identity=identity: chain.register_user(registry, account=requester, identity_hash=identity),
            lambda receipt: _one_event(registry, "UserRegistered", receipt),
        ), index)

    record_hash = synthetic_hash(f"vaccination record, {run}")
    altered_hash = bytes([record_hash[0] ^ 0x01]) + record_hash[1:]

    def request_phase(expected: Reason, observed_hash: bytes) -> None:
        nonlocal phase
        phase += 1
        for index, requester in enumerate(requesters):
            measurement = _request(client, manager, requester, guardian, observed_hash)
            event = measurement["event"]
            if event is not None and (event["reason"] != expected or event["allowed"] != (expected == Reason.ALLOWED)):
                got = Reason(event["reason"]).name
                if event["reason"] == expected:
                    # the reason is right, but the allowed flag contradicts it
                    got += f" with allowed={event['allowed']}"
                raise MeasureError(f"{run}: requester {index + 1} expected {expected.name}, got {got}")
            record("requester", "ConsentManager", "requestAccess", REQUESTS[expected], measurement, index)

    rewards_minted = 0

    def grant_phase(days: int, rewarded: bool, regrant_scenario: str = "") -> None:
        nonlocal phase, rewards_minted
        phase += 1
        for index, requester in enumerate(requesters):
            measurement = measure_transaction(
                lambda requester=requester: chain.grant_consent(
                    manager, guardian=guardian, requester=requester, scope=SCOPE, duration_days=days,
                ),
                lambda receipt, requester=requester: _grant_events(manager, token, receipt, guardian, requester),
            )
            scenario = regrant_scenario
            if rewarded:
                scenario = FIRST_REWARD if rewards_minted == 0 else LATER_REWARD
            if measurement["event"] is not None:
                minted = measurement["event"]["rewards"]
                if minted != int(rewarded):
                    raise MeasureError(f"{run}: requester {index + 1} grant minted {minted} rewards, expected {int(rewarded)}")
                rewards_minted += minted
            record("guardian", "ConsentManager", "grantConsent", scenario, measurement, index)

    def revoke_phase() -> None:
        nonlocal phase
        phase += 1
        for index, requester in enumerate(requesters):
            record("guardian", "ConsentManager", "revokeConsent", REVOKED_ACTIVE, measure_transaction(
                lambda requester=requester: chain.revoke_consent(manager, guardian=guardian, requester=requester, scope=SCOPE),
                lambda receipt: _one_event(manager, "ConsentRevoked", receipt),
            ), index)

    request_phase(Reason.MISSING_EVIDENCE, record_hash)
    phase += 1
    record("clinic", "IdentityRegistry", "registerVaccination", ATTESTED, measure_transaction(
        lambda: chain.register_vaccination(registry, clinic=clinic, guardian=guardian, record_hash=record_hash),
        lambda receipt: _one_event(registry, "VaccinationRegistered", receipt),
    ))
    request_phase(Reason.NO_CONSENT, record_hash)
    grant_phase(GRANT_DAYS, rewarded=True)
    request_phase(Reason.ALLOWED, record_hash)
    request_phase(Reason.HASH_MISMATCH, altered_hash)
    revoke_phase()
    request_phase(Reason.REVOKED, record_hash)
    grant_phase(SHORT_GRANT_DAYS, rewarded=False, regrant_scenario=REGRANT_AFTER_REVOKE)
    latest_expiry = max(
        chain.get_consent(manager, owner=guardian, requester=requester, scope=SCOPE)["expires_at"]
        for requester in requesters
    )
    _set_node_time(client, latest_expiry)
    request_phase(Reason.EXPIRED, record_hash)
    grant_phase(GRANT_DAYS, rewarded=False, regrant_scenario=REGRANT_AFTER_EXPIRY)

    _check_rewards(token, manager, guardian, requesters, run)
    return samples


def summarize_samples(samples: list[dict[str, Any]], keys: tuple[str, ...] = SUMMARY_KEYS) -> list[dict[str, Any]]:
    """Compute sample count, total/average gas and average timings from like-for-like measured rows.
    Do not double count internal token mint gas already included in a grant transaction.
    Groups samples by keys (default: requester count, role, contract, function, scenario), in scenario
    order. Only status "ok" samples are measured; the others count as failures. Values that were not
    measured (no successful sample, no event) are None, never 0. A grant's gasUsed already contains the
    mint it triggered, and no mint row exists, so the mint is counted exactly once.
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for sample in samples:
        groups.setdefault(tuple(sample[key] for key in keys), []).append(sample)

    rows = []
    for key, members in groups.items():
        measured = [sample for sample in members if sample["status"] == "ok"]
        gas = [sample["gas_used"] for sample in measured]
        receipt_times = [sample["receipt_seconds"] for sample in measured]
        event_times = [sample["event_seconds"] for sample in measured if sample["event_seconds"] is not None]
        row = dict(zip(keys, key))
        row.update(
            sample_count=len(measured),
            total_gas_used=sum(gas) if gas else None,
            average_gas_used=sum(gas) / len(gas) if gas else None,
            min_gas_used=min(gas) if gas else None,
            max_gas_used=max(gas) if gas else None,
            average_receipt_seconds=_mean(receipt_times),
            average_event_seconds=_mean(event_times),
            event_count=len(event_times),
            failures=len(members) - len(measured),
            runs=sorted({sample["requester_count"] for sample in members}),
            order=min(sample["order"] for sample in members),
        )
        rows.append(row)
    rows.sort(key=lambda row: (row.get("requester_count", 0), row["order"]))
    return rows


def write_results(path: Path, rows: list[dict[str, Any]], header: list[str] | None = None) -> None:
    """Write observed results with scenario, sample count and units. Never prefill fabricated success/cost.
    The header is the template of the same file name in evaluation/templates unless one is given, so the
    columns and their units (gas, seconds) stay fixed. None is written as a blank cell, which
    means not measured. An empty table is refused, because a header-only file reads like a template.
    """
    path = Path(path)
    if header is None:
        template = TEMPLATES_DIR / path.name
        try:
            header = next(csv.reader(template.read_text(encoding="utf-8").splitlines()))
        except (OSError, StopIteration):
            raise ValueError(f"no header given and no template for {path.name}") from None
    if not rows:
        raise ValueError("no measured rows to write")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(["" if row[column] is None else row[column] for column in header])
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> None:
    """Collect three deployment costs and core-function/scaling measurements.
    Read-only RPC queries have no transaction receipt fee; describe those separately.
    Runs the scenario for 1, 5 and 10 requesters, each on fresh contracts, and writes the tables only after
    every run succeeded and the compiled contracts did not change meanwhile. View calls and reverted calls
    have no receipt, so they have no rows; ENVIRONMENT.md says so. On failure print one reason and exit 1.
    """
    parser = argparse.ArgumentParser(description="Measure gas and timing on the local Hardhat node")
    parser.add_argument(
        "--settings", type=Path, default=None, metavar="PATH",
        help="settings file (default: config/settings.json)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None, metavar="DIR",
        help="where the CSV files and ENVIRONMENT.md go (default: evaluation/results)",
    )
    args = parser.parse_args(argv)
    # the command as given, without the paths, for ENVIRONMENT.md
    command = "python -m evaluation.measure" + " --settings PATH" * (args.settings is not None)
    command += " --output-dir DIR" * (args.output_dir is not None)
    # imported here: deploy_local imports this module for record_deployment_cost
    from scripts.deploy_local import DeployError

    try:
        settings = _read_settings(args.settings or SETTINGS_FILE)
        artifacts_before = _artifact_fingerprints()
        client = chain.connect(settings)
        node = _node_facts(client)
        # every account of the largest run must exist before anything is sent
        requester_accounts(client, settings, max(REQUESTER_COUNTS))
        samples: list[dict[str, Any]] = []
        for count in REQUESTER_COUNTS:
            run_samples = run_requester_scenario(settings, count)
            samples.extend(run_samples)
            print(_run_line(count, run_samples), flush=True)
        if _artifact_fingerprints() != artifacts_before:
            raise MeasureError("the compiled contracts changed during the run (npm run compile?); nothing was written, run it again")
    except (MeasureError, DeployError) as error:
        _stop(str(error))
    except DeploymentUnavailable:
        # nothing here reads deployment.json, so it can only be a missing artifact
        _stop(disclosure.NO_ARTIFACTS_MESSAGE)
    except Web3NotInstalled:
        _stop(disclosure.NO_WEB3_MESSAGE)
    except ChainUnavailable:
        _stop("local node not reachable or wrong chain: start it with npm run node and check rpc_url and expected_chain_id")
    except TransactionRejected as error:
        _stop(f"a call was rejected: {error.args[0] if error.args else 'Reverted'}")
    except TransactionPending:
        _stop("no receipt in time")

    output = Path(args.output_dir or RESULTS_DIR)
    tables = (
        (GAS_FILE, gas_table(samples)),
        (TIMING_FILE, timing_table(samples)),
        (TIMING_SUMMARY_FILE, timing_summary_table(samples)),
    )
    try:
        for name, rows in tables:
            write_results(output / name, rows)
        _write_environment(output / ENVIRONMENT_FILE, settings, node, samples, artifacts_before, command)
    except OSError:
        _stop(f"could not write the results to {_shown(output)}")
    for name, rows in tables:
        print(f"wrote {_shown(output / name)} ({len(rows)} rows)")
    print(f"wrote {_shown(output / ENVIRONMENT_FILE)}")


def gas_table(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """gas_results.csv rows: every run pooled, one row per role, contract, function and scenario."""
    sizes = {name: _runtime_size(name) for name in DEPLOY_ORDER}
    rows = []
    for summary in summarize_samples(samples, ("role", "contract", "function", "scenario")):
        notes = [GAS_NOTES.get(summary["scenario"], "")]
        if summary["function"] == "deployment" and sizes.get(summary["contract"]):
            notes.append(f"runtime code {sizes[summary['contract']]:,} bytes")
        notes.append(_spread(summary))
        if summary["function"] == "deployment":
            notes.append(COMPILER_NOTE)
        rows.append({
            "contract": summary["contract"],
            "function": summary["function"],
            "scenario": f"{summary['role']}: {summary['scenario']}",
            "sample_count": summary["sample_count"],
            "total_gas_used": summary["total_gas_used"],
            "average_gas_used": _gas(summary["average_gas_used"]),
            "notes": "; ".join(note for note in notes if note),
        })
    return rows


def timing_table(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """timing_results.csv rows: per requester count, one row per role and operation. Every transaction is
    in exactly one row, so the sample counts of one requester count add up to that run's transactions.
    The per-role and whole-run means are in timing_summary_table, not here.
    """
    rows = []
    for count in sorted({sample["requester_count"] for sample in samples}):
        run_samples = [sample for sample in samples if sample["requester_count"] == count]
        for summary in summarize_samples(run_samples):
            spread = _spread(summary, runs=False) if summary["min_gas_used"] != summary["max_gas_used"] else ""
            notes = [TIMING_NOTES.get((summary["role"], summary["function"]), ""), spread]
            rows.append(_timing_row(count, _operation(summary), summary["scenario"], summary, notes))
    return rows


def timing_summary_table(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """timing_summary.csv rows: per requester count, one row per role and one for the whole run (mean over
    mixed operations, total gas and wall clock in the notes). They pool the same transactions as the rows of
    timing_results.csv, so they are in a file of their own and must never be added to those rows.
    """
    rows = []
    for count in sorted({sample["requester_count"] for sample in samples}):
        run_samples = [sample for sample in samples if sample["requester_count"] == count]
        roles = sorted({sample["role"] for sample in run_samples}, key=["deployer", "clinic", "guardian", "requester"].index)
        for role in roles:
            role_samples = [dict(sample, group="") for sample in run_samples if sample["role"] == role]
            summary = summarize_samples(role_samples, ("group",))[0]
            notes = [f"mean over all {role} transactions; {_total(summary)}", _event_share(summary)]
            rows.append(_timing_row(count, f"{role}: all transactions", _run_name(count), summary, notes))
        summary = summarize_samples([dict(sample, group="") for sample in run_samples], ("group",))[0]
        wall_clock = max(sample["finished_at"] for sample in run_samples) - min(sample["started_at"] for sample in run_samples)
        notes = [
            f"mean over every transaction of the run; {_total(summary)}",
            _event_share(summary),
            f"wall clock {wall_clock:.3f} s from the first deploy to the last decoded event, including untimed "
            "view reads and the time step",
        ]
        rows.append(_timing_row(count, "all roles: all transactions", _run_name(count), summary, notes))
    return rows


def requester_accounts(client: Any, settings: dict[str, Any], count: int) -> list[str]:
    """The first count accounts of scenario_requester_account_indices, each distinct from the deployer,
    clinic and guardian, resolved through chain.select_account like every other role.
    """
    indices = settings.get("scenario_requester_account_indices")
    roles = settings.get("actor_account_indices", {})
    if not isinstance(indices, list) or len(indices) < count:
        raise MeasureError(f"scenario_requester_account_indices needs at least {count} account indices")
    chosen = indices[:count]
    taken = {roles.get(label) for label in ("deployer", "clinic", "guardian")}
    if len(set(map(repr, chosen))) != count or any(index in taken for index in chosen):
        raise MeasureError("requester accounts must be distinct and separate from deployer, clinic and guardian")
    labels = {f"requester {number}": index for number, index in enumerate(chosen, 1)}
    lookup = dict(settings, actor_account_indices=labels)
    try:
        return [chain.select_account(client, label, lookup) for label in labels]
    except ValueError:
        raise MeasureError("a requester account index is not one of the node's accounts") from None


def synthetic_hash(label: str) -> bytes:
    """A 32-byte stand-in for an identity or record commitment: SHA-256 of a fixed prefix and a label such
    as "requester 3 identity, run N=5". Never derived from real data or any file.
    """
    return hashlib.sha256(b"MEASUREMENT-SYNTHETIC:v1\n" + label.encode("utf-8")).digest()


def _warm_up(client: Any, deploy_local: Any, deployer: str, clinic: str) -> None:
    """Untimed: read the artifacts and estimate the registry deploy, so the first timed transaction does
    not also pay for web3's one-time setup. Nothing is sent.
    """
    artifact = deploy_local.load_artifact("IdentityRegistry")
    for name in DEPLOY_ORDER:
        deploy_local.load_artifact(name)
    factory = client.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    try:
        factory.constructor(clinic).estimate_gas({"from": deployer})
        client.eth.max_priority_fee
    except Exception as error:
        raise ChainUnavailable("RPC request failed") from error


def _contract(client: Any, deploy_local: Any, name: str, address: str) -> Any:
    return client.eth.contract(address=address, abi=deploy_local.load_artifact(name)["abi"])


def _request(client: Any, manager: Any, requester: str, owner: str, observed_hash: bytes) -> dict[str, Any]:
    # the same steps as chain.request_access, split so the receipt and the decoded event are timed apart
    def send() -> Receipt:
        return chain.send_transaction(client, manager.functions.requestAccess(owner, SCOPE, observed_hash), requester)

    def decode(receipt: Receipt) -> Any:
        event = chain.decode_access_event(receipt, manager)
        if (event["owner"].lower(), event["requester"].lower(), event["scope"]) != (owner.lower(), requester.lower(), SCOPE):
            raise ChainUnavailable("access event does not match the request")
        return event

    return measure_transaction(send, decode)


def _logs_of(contract: Any, event_name: str, receipt: Receipt) -> list[Any]:
    # process_receipt does not filter by address, so keep only this contract's logs, as chain.py does
    from web3.logs import DISCARD

    try:
        address = contract.address.lower()
        logs = [log for log in receipt["logs"] if str(log["address"]).lower() == address]
        return list(getattr(contract.events, event_name)().process_receipt({"logs": logs}, errors=DISCARD))
    except Exception as error:
        raise ChainUnavailable("event unavailable") from error


def _one_event(contract: Any, event_name: str, receipt: Receipt) -> Any:
    events = _logs_of(contract, event_name, receipt)
    if len(events) != 1:
        raise ChainUnavailable("event unavailable")
    return events[0]["args"]


def _grant_events(manager: Any, token: Any, receipt: Receipt, guardian: str, requester: str) -> dict[str, Any]:
    granted = _one_event(manager, "ConsentGranted", receipt)
    if str(granted["requester"]).lower() != requester.lower():
        raise ChainUnavailable("grant event does not match the grant")
    minted = _logs_of(token, "RewardMinted", receipt)
    if any(str(event["args"]["recipient"]).lower() != guardian.lower() for event in minted):
        raise ChainUnavailable("reward went to someone other than the guardian")
    return {"granted": granted, "rewards": len(minted)}


def _check_rewards(token: Any, manager: Any, guardian: str, requesters: list[str], run: str) -> None:
    # one lifetime reward per requester tuple, all to the guardian, none to a requester
    count = len(requesters)
    guardian_balance = chain.get_reward_balance(token, account=guardian)
    supply = chain.call_view(token.functions.totalSupply())
    requester_balances = [chain.get_reward_balance(token, account=requester) for requester in requesters]
    rewarded = [chain.call_view(manager.functions.hasReceivedReward(guardian, requester, SCOPE)) for requester in requesters]
    if guardian_balance != count or supply != count or any(requester_balances) or not all(rewarded):
        raise MeasureError(f"{run}: rewards do not match one per requester (guardian {guardian_balance}, supply {supply})")


def _set_node_time(client: Any, timestamp: int) -> None:
    """Mine one block at exactly timestamp; Hardhat only (chain 31337 is checked before the run)."""
    for method, params in (("evm_setNextBlockTimestamp", [timestamp]), ("evm_mine", [])):
        try:
            response = client.provider.make_request(method, params)
        except Exception as error:
            raise ChainUnavailable("RPC request failed") from error
        if not isinstance(response, dict) or "error" in response:
            raise MeasureError(f"the node refused {method}")


def _require_local_node(client: Any) -> None:
    try:
        chain_id = client.eth.chain_id
        automine = client.provider.make_request("hardhat_getAutomine", []).get("result")
    except Exception as error:
        raise ChainUnavailable("RPC request failed") from error
    if chain_id != LOCAL_CHAIN_ID:
        raise MeasureError(f"measurements run only on the local Hardhat chain {LOCAL_CHAIN_ID}")
    if automine is False:
        raise MeasureError("automine is off on the node; the timing method needs a plain npm run node")


def _node_facts(client: Any) -> dict[str, Any]:
    _require_local_node(client)
    facts: dict[str, Any] = {}
    try:
        facts["client"] = client.provider.make_request("web3_clientVersion", []).get("result")
        facts["automine"] = client.provider.make_request("hardhat_getAutomine", []).get("result")
        block = client.eth.get_block("latest")
        facts["gas_limit"] = int(block["gasLimit"])
        facts["accounts"] = len(client.eth.accounts)
    except Exception as error:
        raise ChainUnavailable("RPC request failed") from error
    return facts


def _artifact_fingerprints() -> dict[str, tuple[str, str]]:
    """(sha256 of the artifact file, buildInfoId) per contract, to notice a recompile during the run."""
    fingerprints = {}
    for name in DEPLOY_ORDER:
        path = PROJECT_ROOT / chain.DEFAULT_ARTIFACTS_DIR / f"{name}.sol" / f"{name}.json"
        try:
            content = path.read_bytes()
            build = json.loads(content).get("buildInfoId", "")
        except (OSError, ValueError, AttributeError):
            raise MeasureError("compiled contracts not found: run npm run compile first") from None
        fingerprints[name] = (hashlib.sha256(content).hexdigest(), str(build))
    return fingerprints


def _compiler_facts(fingerprints: dict[str, tuple[str, str]]) -> list[str]:
    """One line per distinct compiler setting, read from the build-info files of the deployed artifacts."""
    settings: dict[str, list[str]] = {}
    for name, (_, build) in fingerprints.items():
        try:
            info = json.loads((PROJECT_ROOT / "artifacts" / "build-info" / f"{build}.json").read_bytes())
            optimizer = info["input"]["settings"]["optimizer"]
            text = (
                f"solc {info['solcLongVersion']}, optimiser {'on' if optimizer.get('enabled') else 'off'}, "
                f"{optimizer.get('runs')} runs, EVM {info['input']['settings'].get('evmVersion', 'default')}"
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            text = "build info not found (see hardhat.config.ts)"
        settings.setdefault(text, []).append(f"{name} ({build or 'no build id'})")
    return [f"{text}: {', '.join(names)}" for text, names in settings.items()]


def _runtime_size(name: str) -> int | None:
    try:
        artifact = json.loads((PROJECT_ROOT / chain.DEFAULT_ARTIFACTS_DIR / f"{name}.sol" / f"{name}.json").read_bytes())
        return len(bytes.fromhex(artifact["deployedBytecode"].removeprefix("0x")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _write_environment(
    path: Path,
    settings: dict[str, Any],
    node: dict[str, Any],
    samples: list[dict[str, Any]],
    fingerprints: dict[str, tuple[str, str]],
    command: str = "python -m evaluation.measure",
) -> None:
    measured = [sample for sample in samples if sample["status"] == "ok"]
    counts = ", ".join(
        f"N={count}: {sum(1 for sample in samples if sample['requester_count'] == count)}"
        for count in sorted({sample["requester_count"] for sample in samples})
    )
    automine = {True: "on", False: "off"}.get(node.get("automine"), "not reported by the node")
    lines = [
        "# Measurement environment",
        "",
        "Local demo measurements: one machine, one local Hardhat node with automine, transactions sent one at a "
        "time. They are not public-chain throughput, latency or fee figures, and no gas is converted to ETH or money.",
        "",
        f"Written by `{command}` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, at {_git_revision()}. "
        f"Uncommitted changes are looked for under {', '.join(CODE_PATHS)}.",
        "",
        "## Compiler",
        "",
        f"- {COMPILER_NOTE} (hardhat.config.ts).",
        *(f"- {line}" for line in _compiler_facts(fingerprints)),
        "",
        "## Node",
        "",
        f"- {node.get('client') or 'client version not reported'}; Hardhat {_hardhat_version()} (node_modules); "
        f"chain ID {LOCAL_CHAIN_ID}; RPC {settings.get('rpc_url')} on the same machine",
        f"- automine {automine} (hardhat_getAutomine), so each transaction is mined in its own block while "
        "eth_sendTransaction runs; no interval mining",
        f"- block gas limit {node.get('gas_limit', 0):,}; {node.get('accounts', 0)} unlocked accounts; "
        "gas price and base fee play no part in the tables",
        "",
        "## Machine",
        "",
        f"- {_os_name()}",
        f"- {_cpu_name()}",
        f"- Python {platform.python_version()}, web3 {_package_version('web3')}, Node.js {_node_version()}",
        "",
        "## Method",
        "",
        f"- Runs with {', '.join(map(str, REQUESTER_COUNTS))} requesters from scenario_requester_account_indices; "
        f"fresh contracts for every run. Transactions: {counts}; {len(samples) - len(measured)} failed.",
        "- Roles: deployer (three deploys, setMinterOnce), clinic (registerVaccination), guardian (registerUser, "
        "grantConsent, revokeConsent), each requester (registerUser, requestAccess). Scope 1 only.",
        "- Identities and the record hash are SHA-256 hashes of fixed synthetic labels; no personal data or local file is used.",
        "- Gas: gasUsed of each successful receipt, including the intrinsic cost (21,000 per transaction, 53,000 "
        "per deployment) and calldata; a grant's internal mintReward call is inside its gasUsed and is not "
        "counted again. Small differences inside one row come from calldata: a zero byte costs 4 gas and any "
        "other byte 16, and account addresses and synthetic hashes differ in their zero bytes.",
        "- Receipt seconds: time.monotonic() before the app.chain call until the successful receipt is back. "
        "That includes web3's gas estimate and fee lookup, eth_sendTransaction (mining happens inside it) and "
        "eth_getTransactionReceipt. Event seconds: the same start until the expected event is decoded from "
        "that receipt (AccessAttempt through chain.decode_access_event).",
        "- Deployer rows are send to verified deploy, not send to receipt: they are timed around "
        "scripts/deploy_local's verified steps, which also read the artifact file and read the runtime code "
        "(and trustedClinic() or minter()) back.",
        f"- {TIMING_FILE} has one row per operation, so its sample counts add up to each run's transactions. "
        f"{TIMING_SUMMARY_FILE} has the per-role and whole-run means over those same transactions; never add "
        "its rows to the others. In a run's whole-run row the receipt mean covers every transaction, the "
        "deployments included, while the event mean covers only the transactions that emit one.",
        "- Not in the tables: view calls (getUserInfo, checkAccess, getConsent, balanceOf) use eth_call, have no "
        "receipt and cost the caller no gas. A call that would revert (for example ConsentStillActive) fails at "
        "web3's gas estimate and is never mined, so it has no receipt either.",
        "- A blank cell or a missing row means not measured, never zero.",
    ]
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _timing_row(count: int, operation: str, scenario: str, summary: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    return {
        "requester_count": count,
        "operation": operation,
        "scenario": scenario,
        "sample_count": summary["sample_count"],
        "average_gas_used": _gas(summary["average_gas_used"]),
        "average_receipt_seconds": _seconds(summary["average_receipt_seconds"]),
        "average_event_seconds": _seconds(summary["average_event_seconds"]),
        "failures": summary["failures"],
        "notes": "; ".join(note for note in notes if note),
    }


def _operation(summary: dict[str, Any]) -> str:
    if summary["function"] == "deployment":
        return f"{summary['role']}: deploy {summary['contract']}"
    return f"{summary['role']}: {summary['function']}"


def _spread(summary: dict[str, Any], runs: bool = True) -> str:
    if summary["sample_count"] == 0:
        return "not measured: every call failed"
    where = f" (runs N={','.join(str(count) for count in summary['runs'])})" if runs else ""
    if summary["min_gas_used"] == summary["max_gas_used"]:
        return f"gasUsed identical in all {summary['sample_count']} samples{where}"
    return f"gasUsed {summary['min_gas_used']:,}-{summary['max_gas_used']:,}{where}"


def _total(summary: dict[str, Any]) -> str:
    if summary["total_gas_used"] is None:
        return "total gasUsed not measured"
    return f"total gasUsed {summary['total_gas_used']:,}"


def _event_share(summary: dict[str, Any]) -> str:
    if summary["event_count"] in (0, summary["sample_count"]):
        return ""
    return f"event mean over the {summary['event_count']} of {summary['sample_count']} transactions that emit one (a constructor emits none)"


def _run_name(count: int) -> str:
    return f"run with {count} requester{'' if count == 1 else 's'}"


def _run_line(count: int, samples: list[dict[str, Any]]) -> str:
    failed = sum(1 for sample in samples if sample["status"] != "ok")
    wall_clock = max(sample["finished_at"] for sample in samples) - min(sample["started_at"] for sample in samples)
    return f"N={count}: {len(samples)} transactions on fresh contracts, {failed} failed, {wall_clock:.2f} s"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _gas(value: float | None) -> str | None:
    if value is None:
        return None
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _seconds(value: float | None) -> str | None:
    return None if value is None else f"{value:.6f}"


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        settings = json.loads(Path(path).read_bytes())
    except OSError:
        raise MeasureError(
            f"no settings file at {_shown(path)}: copy config/settings.example.json and set rpc_url"
        ) from None
    except ValueError:
        raise MeasureError(f"{_shown(path)} is not valid JSON") from None
    if not isinstance(settings, dict) or not all(
        key in settings for key in ("rpc_url", "expected_chain_id", "actor_account_indices", "scenario_requester_account_indices")
    ):
        raise MeasureError(
            f"{_shown(path)} needs rpc_url, expected_chain_id, actor_account_indices and scenario_requester_account_indices"
        )
    if settings["expected_chain_id"] != LOCAL_CHAIN_ID:
        raise MeasureError(f"measurements run only on the local Hardhat chain {LOCAL_CHAIN_ID}")
    roles = settings["actor_account_indices"]
    if not isinstance(roles, dict) or not all(label in roles for label in ROLE_LABELS):
        raise MeasureError(NO_ROLES)
    return settings


def _command_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _git_revision() -> str:
    revision = _command_output(["git", "rev-parse", "--short", "HEAD"])
    if not revision:
        return "an unknown commit"
    changed = _command_output(["git", "status", "--porcelain", "--", *CODE_PATHS])
    return f"commit {revision}" + (" + uncommitted changes" if changed else "")


def _hardhat_version() -> str:
    try:
        return json.loads((PROJECT_ROOT / "node_modules" / "hardhat" / "package.json").read_bytes())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return "version unknown"


def _node_version() -> str:
    node = shutil.which("node")
    return (_command_output([node, "--version"]) if node else "") or "version unknown"


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not installed"


def _os_name() -> str:
    if platform.system() == "Darwin" and platform.mac_ver()[0]:
        return f"macOS {platform.mac_ver()[0]} (Darwin {platform.release()}, {platform.machine()})"
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def _cpu_name() -> str:
    name = ""
    if platform.system() == "Darwin":
        name = _command_output(["sysctl", "-n", "machdep.cpu.brand_string"])
    elif Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                name = line.split(":", 1)[1].strip()
                break
    name = name or platform.processor() or "CPU unknown"
    memory = _command_output(["sysctl", "-n", "hw.memsize"]) if platform.system() == "Darwin" else ""
    memory_text = f", {int(memory) / 2**30:.0f} GiB memory" if memory.isdigit() else ""
    return f"CPU {name}, {os.cpu_count() or '?'} logical cores{memory_text}"


def _shown(path: Path) -> str:
    # relative to the project, or only the file name: never the home folder
    return records.shown_path(path)


def _stop(reason: str) -> None:
    print(f"measurement stopped: {reason}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
