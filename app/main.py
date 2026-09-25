"""Developer 3: future plain console workflow.
The only currently runnable behavior is main(), which prints scaffold status and exits.

All workflow methods are placeholders. They raise NotImplementedError rather than
returning fake data or pretending that authorization has succeeded.
"""
from pathlib import Path
from typing import Any


def show_menu() -> str:
    """Show actions for register, clinic-attest, grant, revoke, school-check, doctor-view, rewards and audit.
    Return a validated choice. Do not implement a website or complex UI.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("show_menu is an implementation task; see docs/tasks.")


def select_actor(settings: dict[str, Any]) -> str:
    """Select a predefined deployer/clinic/guardian/school/doctor label for local demo use.
    Never treat an unknown label as administrator or silently reuse another signer.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("select_actor is an implementation task; see docs/tasks.")


def dispatch_action(choice: str, actor_label: str, settings: dict[str, Any]) -> None:
    """Connect one menu choice to records/chain/disclosure helpers.
    Handle pending, denied, unavailable and unimplemented outcomes distinctly without exposing private data.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("dispatch_action is an implementation task; see docs/tasks.")


def run_console(settings_path: Path) -> None:
    """Load settings, connect to the local node and run the future menu lifecycle.
    This is intentionally not called by main until dependencies and methods are implemented.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("run_console is an implementation task; see docs/tasks.")


def main() -> None:
    """Print truthful startup status without opening a node or pretending features work."""
    print("My Vaccination Card scaffold started.")
    print("Three Solidity contracts and Python workflows are placeholders; no access is authorized.")


if __name__ == "__main__":
    main()
