"""Developer 3: plain console workflow for the local demo.
One menu covers setup, register, attest, grant, revoke, school check, doctor view, rewards and audit.
The tamper and exact-expiry steps run in integration/demo_workflow.py instead.
Exception text is never printed, only a short outcome or the error name. The one exception is
RecordError, whose messages never hold data, salts or paths.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from app import chain, disclosure, records
from app.models import (
    Scope, Reason, AccessResponse, ChainUnavailable, TransactionRejected, TransactionPending, OUTCOME_ALLOWED,
)

SETTINGS_FILE = records.PROJECT_ROOT / "config" / "settings.json"
ACTIONS = (
    ("setup", "set up local files and salts"),
    ("register", "register this account"),
    ("attest", "clinic attests the guardian's record"),
    ("grant", "grant consent"),
    ("revoke", "revoke consent"),
    ("school", "school check (measles status)"),
    ("doctor", "doctor view (vaccine and date)"),
    ("rewards", "reward balances"),
    ("audit", "access audit log"),
    ("mine", "show my registration"),
    ("switch", "switch actor"),
    ("quit", "quit"),
)


def show_menu() -> str:
    """Show actions for register, clinic-attest, grant, revoke, school-check, doctor-view, rewards and audit.
    Return a validated choice. Do not implement a website or complex UI.
    """
    print()
    for number, (_, text) in enumerate(ACTIONS, 1):
        print(f"{number:>2}. {text}")
    return ACTIONS[ask_number("choose: ", 1, len(ACTIONS)) - 1][0]


def select_actor(settings: dict[str, Any]) -> str:
    """Select a predefined deployer/clinic/guardian/school/doctor label for local demo use.
    Never treat an unknown label as administrator or silently reuse another signer.
    """
    return pick_label("act as", settings)


def dispatch_action(choice: str, actor_label: str, settings: dict[str, Any]) -> None:
    """Connect one menu choice to records/chain/disclosure helpers.
    Handle pending, denied and unavailable outcomes distinctly without exposing private data.
    "not implemented yet" is only a console message for NotImplementedError, not an outcome.
    """
    handler = HANDLERS.get(choice)
    if handler is None:
        print("unknown choice")
        return
    try:
        handler(actor_label, settings)
    except EOFError:
        # end of input means quit, handled by run_console
        raise
    except NotImplementedError:
        print(disclosure.NOT_IMPLEMENTED_MESSAGE)
    except ChainUnavailable:
        print("unavailable: local node not reachable or wrong chain")
    except TransactionPending:
        print("pending: not confirmed")
    except TransactionRejected as error:
        print(f"rejected: {error.args[0] if error.args else 'reverted'}")
    except records.RecordError as error:
        # these messages never hold data, salts or paths
        print(f"unavailable: {error}")
    except Exception as error:
        print(f"failed: {type(error).__name__}")


def run_console(settings_path: Path) -> None:
    """Load settings and run the menu until quit. Setup and "show my registration" work without a node."""
    try:
        settings = json.loads(Path(settings_path).read_bytes())
    except OSError:
        print("no settings file: copy config/settings.example.json to config/settings.json and run again")
        return
    except ValueError:
        print("the settings file is not valid JSON")
        return
    try:
        actor = select_actor(settings)
        while True:
            print(f"\nacting as {actor}")
            choice = show_menu()
            if choice == "quit":
                return
            if choice == "switch":
                actor = select_actor(settings)
            else:
                dispatch_action(choice, actor, settings)
    except (EOFError, KeyboardInterrupt):
        print()


def main() -> None:
    """Start the console with config/settings.json."""
    print("My Vaccination Card console (local Hardhat demo)")
    run_console(SETTINGS_FILE)


def ask_number(prompt: str, low: int, high: int) -> int:
    """Ask until the answer is a whole number from low to high."""
    while True:
        answer = input(prompt).strip()
        try:
            number = int(answer)
        except ValueError:
            number = None
        if number is not None and low <= number <= high:
            return number
        print(f"enter a number from {low} to {high}")


def pick_label(prompt: str, settings: dict[str, Any], leave_out: str = "") -> str:
    labels = [label for label in settings["actor_account_indices"] if label != leave_out]
    for number, label in enumerate(labels, 1):
        print(f"{number}. {label}")
    return labels[ask_number(f"{prompt}: ", 1, len(labels)) - 1]


def pick_scope() -> Scope:
    print(f"1. {Scope.MEASLES_STATUS.name} (school)")
    print(f"2. {Scope.VACCINATION_SCHEDULE.name} (doctor)")
    return Scope(ask_number("scope: ", 1, 2))


def contract(client: Any, name: str, settings: dict[str, Any]) -> Any:
    return chain.load_contract(client, name, records.settings_path(settings, "deployment_file"))


def record_snapshot(settings: dict[str, Any]) -> Any:
    return records.load_snapshot(
        records.settings_path(settings, "vaccination_file"),
        records.settings_path(settings, "vaccination_salt_file"),
    )


def shown_path(path: Path) -> str:
    try:
        return str(path.relative_to(records.PROJECT_ROOT))
    except ValueError:
        return str(path)


def scope_name(value: int) -> str:
    # events can carry any uint8 scope, so never assume a valid Scope
    return Scope(value).name if value in (1, 2) else f"scope {value} (unsupported)"


def show_response(response: AccessResponse) -> None:
    if response["outcome"] != OUTCOME_ALLOWED:
        print(disclosure.denial_message(response))
        return
    fields = response["fields"]
    if "measles_status" in fields:
        text = f"measles status {fields['measles_status']}"
    else:
        text = ", ".join(f"{event['vaccine']} on {event['date']}" for event in fields["vaccinations"])
    print(f"allowed: {text} (logged on-chain in {response['transaction_hash']})")


def do_setup(actor_label: str, settings: dict[str, Any]) -> None:
    created = records.setup_runtime(settings)
    if not created:
        print("setup: every local file already exists, nothing changed")
    for path in created:
        print(f"setup: created {shown_path(path)}")


def do_register(actor_label: str, settings: dict[str, Any]) -> None:
    if actor_label not in records.REGISTERING_LABELS:
        print("only guardian, school and doctor register")
        return
    identity_hash = records.prepare_identity(*records.identity_paths(settings, actor_label))
    client = chain.connect(settings)
    account = chain.select_account(client, actor_label, settings)
    receipt = chain.register_user(contract(client, "IdentityRegistry", settings), account=account, identity_hash=identity_hash)
    print(f"registered {actor_label} with identity hash 0x{identity_hash.hex()} (tx {receipt['transaction_hash']})")


def do_attest(actor_label: str, settings: dict[str, Any]) -> None:
    # sent from the current actor; the registry itself refuses anyone but the trusted clinic
    commitment = record_snapshot(settings)["commitment"]
    client = chain.connect(settings)
    clinic = chain.select_account(client, actor_label, settings)
    guardian = chain.select_account(client, "guardian", settings)
    receipt = chain.register_vaccination(
        contract(client, "IdentityRegistry", settings), clinic=clinic, guardian=guardian, record_hash=commitment,
    )
    print(f"attested the guardian's record as 0x{commitment.hex()} (tx {receipt['transaction_hash']})")


def do_grant(actor_label: str, settings: dict[str, Any]) -> None:
    # every answer is checked before any chain call
    requester_label = pick_label("grant to", settings, leave_out=actor_label)
    scope = pick_scope()
    days = ask_number("days (1 to 365): ", 1, 365)
    client = chain.connect(settings)
    guardian = chain.select_account(client, actor_label, settings)
    requester = chain.select_account(client, requester_label, settings)
    token = contract(client, "ConsentRewardToken", settings)
    before = chain.get_reward_balance(token, account=guardian)
    receipt = chain.grant_consent(
        contract(client, "ConsentManager", settings), guardian=guardian, requester=requester, scope=scope, duration_days=days,
    )
    after = chain.get_reward_balance(token, account=guardian)
    print(f"granted {requester_label} {scope.name} for {days} day{'' if days == 1 else 's'} (tx {receipt['transaction_hash']})")
    print(f"reward balance of {actor_label}: {before} -> {after}")


def do_revoke(actor_label: str, settings: dict[str, Any]) -> None:
    requester_label = pick_label("revoke from", settings, leave_out=actor_label)
    scope = pick_scope()
    client = chain.connect(settings)
    guardian = chain.select_account(client, actor_label, settings)
    requester = chain.select_account(client, requester_label, settings)
    receipt = chain.revoke_consent(contract(client, "ConsentManager", settings), guardian=guardian, requester=requester, scope=scope)
    print(f"revoked {requester_label} {scope.name} (tx {receipt['transaction_hash']})")


def do_school_check(actor_label: str, settings: dict[str, Any]) -> None:
    client = chain.connect(settings)
    owner = chain.select_account(client, "guardian", settings)
    print("as school:")
    show_response(disclosure.verify_for_school(settings, owner))


def do_doctor_view(actor_label: str, settings: dict[str, Any]) -> None:
    client = chain.connect(settings)
    owner = chain.select_account(client, "guardian", settings)
    print("as doctor:")
    show_response(disclosure.get_doctor_schedule(settings, owner))


def do_rewards(actor_label: str, settings: dict[str, Any]) -> None:
    client = chain.connect(settings)
    token = contract(client, "ConsentRewardToken", settings)
    for label in settings["actor_account_indices"]:
        print(f"{label}: {chain.get_reward_balance(token, account=chain.select_account(client, label, settings))}")


def do_audit(actor_label: str, settings: dict[str, Any]) -> None:
    client = chain.connect(settings)
    labels = {chain.select_account(client, label, settings).lower(): label for label in settings["actor_account_indices"]}
    events = chain.list_access_events(contract(client, "ConsentManager", settings), from_block=0)
    if not events:
        print("audit: no access attempts yet")
    for event in events:
        when = datetime.fromtimestamp(event["timestamp"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        requester = labels.get(event["requester"].lower(), event["requester"])
        result = "allowed" if event["allowed"] else "denied"
        print(f"{when}  {requester}  {scope_name(event['scope'])}  {result}  {Reason(event['reason']).name}")


def do_show_registration(actor_label: str, settings: dict[str, Any]) -> None:
    # local hashes first, so this still works with no node running
    if actor_label not in records.REGISTERING_LABELS:
        print(f"{actor_label} does not register, so there is nothing to show")
        return
    identity_hash = records.prepare_identity(*records.identity_paths(settings, actor_label))
    print(f"local identity hash:        0x{identity_hash.hex()}")
    commitment = None
    if actor_label == "guardian":
        commitment = record_snapshot(settings)["commitment"]
        print(f"local record commitment:    0x{commitment.hex()}")
    try:
        client = chain.connect(settings)
        account = chain.select_account(client, actor_label, settings)
        info = chain.get_user_info(contract(client, "IdentityRegistry", settings), account=account)
    except NotImplementedError:
        print(f"on-chain: {disclosure.NOT_IMPLEMENTED_MESSAGE}")
        return
    except ChainUnavailable:
        print("on-chain: local node not reachable")
        return
    print(f"registered on-chain:        {'yes' if info['registered'] else 'no'}")
    print(f"on-chain identity hash:     0x{bytes(info['identity_hash']).hex()}")
    print(f"identity matches:           {'yes' if info['identity_hash'] == identity_hash else 'no'}")
    if commitment is not None:
        print(f"on-chain record commitment: 0x{bytes(info['vaccination_hash']).hex()}")
        print(f"local commitment matches:   {'yes' if info['vaccination_hash'] == commitment else 'no'}")


HANDLERS = {
    "setup": do_setup,
    "register": do_register,
    "attest": do_attest,
    "grant": do_grant,
    "revoke": do_revoke,
    "school": do_school_check,
    "doctor": do_doctor_view,
    "rewards": do_rewards,
    "audit": do_audit,
    "mine": do_show_registration,
}


if __name__ == "__main__":
    main()
