"""Developer 4 with Developer 3: full user-story integration outline.
Use real local Hardhat transactions and synthetic files, not a simulated permission dictionary.

All workflow methods are placeholders. They raise NotImplementedError rather than
returning fake data or pretending that authorization has succeeded.
"""
from pathlib import Path
from typing import Any


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


def demonstrate_revocation_and_expiry(settings: dict[str, Any]) -> None:
    """Revoke and assert denied audit; use controlled local-node time advancement to test exact expiry.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("demonstrate_revocation_and_expiry is an implementation task; see docs/tasks.")


def demonstrate_tampering(settings: dict[str, Any], copied_fixture: Path) -> None:
    """Alter a copy of the frozen JSON, assert integrity denial and restore the fixture afterward.
    Never alter real user data or silently register a replacement hash.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("demonstrate_tampering is an implementation task; see docs/tasks.")


def main() -> None:
    """Run the complete story, asserting each result and recording genuine receipts/events.
    No result is currently implemented or marked passed.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("main is an implementation task; see docs/tasks.")


if __name__ == "__main__":
    main()
