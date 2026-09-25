"""Developer 4 with Developer 3: full user-story integration outline.
Use real local Hardhat transactions and synthetic files, not a simulated permission dictionary.

Methods not written yet raise NotImplementedError rather than returning fake data
or pretending that authorisation has succeeded.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
from pathlib import Path
from typing import Any
from app import chain, disclosure, records
from app.models import OUTCOME_DENIED


def register_demo_accounts(settings: dict[str, Any]) -> None:
    """Register separate synthetic guardian, school and doctor identities; keep signer labels distinct.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("register_demo_accounts is an implementation task; see docs/tasks.")


def attest_demo_record(settings: dict[str, Any]) -> None:
    """Save/freeze local bytes, compute commitment and register it from the trusted clinic.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("attest_demo_record is an implementation task; see docs/tasks.")


def demonstrate_school_flow(settings: dict[str, Any]) -> None:
    """Assert denied-before-grant, committed grant/reward and permitted status-only response.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("demonstrate_school_flow is an implementation task; see docs/tasks.")


def demonstrate_doctor_flow(settings: dict[str, Any]) -> None:
    """Assert doctor-specific consent returns only vaccine/date, never the complete JSON.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("demonstrate_doctor_flow is an implementation task; see docs/tasks.")


def demonstrate_tampering(settings: dict[str, Any], copied_fixture: Path) -> None:
    """Run while the doctor grant is still active, before any time advance.
    Copy runtime-data/vaccination_record.json to runtime-data/tamper/vaccination_record.json (copied_fixture)
    and change one byte inside the batch value (ABC123-DEMO to ABC124-DEMO) so it still validates.
    Run the doctor request with a copy of settings whose vaccination_file points to the copy
    (operator setting, never requester input).
    Assert denied (HASH_MISMATCH) and no payload, delete the copy even if something fails, then show the
    original still verifies. The frozen file is never opened for writing and no replacement hash is registered.
    """
    original = records.settings_path(settings, "vaccination_file")
    # relative to the project root like every settings path, so the write and the request see the same file
    copy_path = records.PROJECT_ROOT / Path(copied_fixture)
    # refuse an existing file, so the frozen record or a salt can never be overwritten or deleted here
    expect(not copy_path.exists() and copy_path.resolve() != original.resolve(), "tamper copy must be a new file")
    expect(records.DATA_ROOT.resolve() in copy_path.resolve().parents, "tamper copy must be inside the data root")
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
    """Revoke school consent and assert denied (REVOKED). Then regrant school for 1 day (assert no second
    reward), set the next block timestamp to expiresAt with evm_setNextBlockTimestamp and assert denied (EXPIRED).
    Run this last: node time cannot go back.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("demonstrate_revocation_and_expiry is an implementation task; see docs/tasks.")


def main() -> None:
    """Run the complete story, asserting each result and recording genuine receipts/events.
    No result is currently implemented or marked passed.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("main is an implementation task; see docs/tasks.")


def expect(condition: bool, message: str) -> None:
    """Demo check that still runs under python -O, unlike assert."""
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    main()
