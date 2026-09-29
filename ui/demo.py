# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""The guided demo and the two demo controls of the web UI (ui/server.py). No HTTP here either.

The guided demo plays the story of python -m integration.demo_workflow one click per step. Each step runs the
same functions as the page's buttons (ui/actions.py) and checks its own result the way demo_workflow does,
so the presenter sees each outcome verified. Step 0 is not in UI_HANDOFF's list: like demo_workflow, it
deploys fresh contracts first, so the demo can be played again and again. Step 11 copies demo_workflow's
exact expiry: an empty block at expiresAt - 1, where the checkAccess view still allows (a view logs and
releases nothing), then the school's request mined at exactly expiresAt, which is already expired. A step
that does not give the expected result stays the current step, so it can be tried again. A try that failed
after its transaction went through (the node stopped during a later read, say) is picked up where it
stopped: an earlier registration, attestation or grant counts, its reward is checked against the balance
before the first try, and step 11 does not move time twice. After anything done by hand, starting again
gives fresh contracts. The final check compares the AccessAttempt events of the guided demo's own requests
with demo_workflow.EXPECTED_AUDIT, and the reward balances with its 2 and 0s; if it cannot run, it can be
run again. Like demo_workflow, the guided demo runs only on chain 31337, because step 11 moves node time.

Tamper follows UI_HANDOFF, not demonstrate_tampering (which prints and raises): a new copy under
<data_root>/tamper/ with one byte of the batch changed, the doctor's request on that copy (an operator
setting, never requester input), the copy deleted in a finally, then a check that the original still
matches the on-chain commitment. The original is never opened for writing. Jump to expiry moves node time
to a consent's expiresAt with demo_workflow.advance_time(mine=True). It refuses, without touching node time,
a consent that was never granted, is revoked or has already expired: node time cannot go back.
"""
import secrets
import threading
from collections.abc import Callable
from typing import Any, NamedTuple
from app import chain, disclosure, main, records
from app.models import Reason, Scope, OUTCOME_ALLOWED, OUTCOME_DENIED
from integration import demo_workflow
from ui import actions

GRANT_DAYS = demo_workflow.GRANT_DAYS
REGRANT_DAYS = demo_workflow.REGRANT_DAYS
# the batch value demo_workflow changes; one byte differs
BATCH, TAMPERED_BATCH = b"ABC123-DEMO", b"ABC124-DEMO"


class Step(NamedTuple):
    title: str
    role: str  # the role the page switches to afterwards
    expected: str
    # (demo, settings, settings file, the list its actions are added to) -> (as expected, what it got)
    run: Callable[["GuidedDemo", dict[str, Any], Any, list[dict[str, Any]]], tuple[bool, str]]


def tamper(settings: dict[str, Any]) -> dict[str, Any]:
    """The doctor's request on a tampered copy of the record, which is deleted again. It is denied with
    HASH_MISMATCH only while the doctor's grant allows access, because the contract compares the hash last.
    """
    original = records.settings_path(settings, "vaccination_file")
    raw_bytes = records.read_record_bytes(original)
    if BATCH not in raw_bytes:
        return actions.result("unavailable", "unavailable: the record has no batch ABC123-DEMO to change")
    folder = records.data_root(settings) / demo_workflow.TAMPER_COPY.parent
    folder.mkdir(parents=True, exist_ok=True)
    # a new file every time ("x" refuses an existing one), so nothing else is ever overwritten or deleted
    copy = folder / f"ui-{secrets.token_hex(8)}.json"
    try:
        with open(copy, "xb") as file:
            file.write(raw_bytes.replace(BATCH, TAMPERED_BATCH, 1))
        answer = actions.doctor_view(dict(settings, vaccination_file=str(copy)))
    finally:
        copy.unlink(missing_ok=True)
    answer["details"].update(copy_deleted=not copy.exists(), original_matches=_original_matches(settings))
    if answer["status"] == OUTCOME_DENIED and answer["reason"] in ("NOT_REGISTERED", "MISSING_EVIDENCE"):
        answer["details"]["note"] = "the contract checks registration and the clinic's attestation before the hash: register and attest first"
    elif answer["status"] == OUTCOME_DENIED and answer["reason"] != "HASH_MISMATCH":
        answer["details"]["note"] = "HASH_MISMATCH shows only while the doctor's grant is active: the contract compares the hash last"
    return answer


def expire(settings: dict[str, Any], requester: Any, scope: Any) -> dict[str, Any]:
    """Move node time to the expiry of the guardian's grant to requester for scope, and mine a block there, so
    the views (and the page) see it expired at once. Refused without any chain write when there is nothing
    to expire. The time move is permanent and also ends every other grant that expires earlier.
    """
    requester = actions._requester(settings, requester, "guardian")
    scope = actions._scope(scope)
    if settings["expected_chain_id"] != demo_workflow.LOCAL_CHAIN_ID:
        return actions.result("refused", "refused: node time can only be moved on the local Hardhat chain 31337")
    client = chain.connect(settings)
    guardian = chain.select_account(client, "guardian", settings)
    requester_address = chain.select_account(client, requester, settings)
    manager = main.contract(client, "ConsentManager", settings)
    consent = chain.get_consent(manager, owner=guardian, requester=requester_address, scope=scope)
    expires_at = consent["expires_at"]
    now = demo_workflow._block_timestamp(client, "latest")
    if expires_at == 0:
        return actions.result("refused", f"refused: the guardian never granted {requester} {scope.name}, so nothing expires")
    if consent["revoked"]:
        return actions.result("refused", f"refused: {requester} {scope.name} is revoked, and moving time would not change that")
    if now >= expires_at:
        return actions.result("refused", f"refused: {requester} {scope.name} has already expired; node time cannot go back")
    try:
        demo_workflow.advance_time(client, expires_at, mine=True)
    except ValueError as error:
        # advance_time's own fixed text, e.g. "node time cannot go back"
        return actions.result("refused", f"refused: {error}")
    return actions.result(
        "ok", f"node time moved to {actions._utc(expires_at)}, the expiry of {requester} {scope.name}; it cannot go back",
        node_time=expires_at, node_time_text=actions._utc(expires_at),
    )


class GuidedDemo:
    """The guided demo's progress: the next step, each step's last try and the requests it logged.
    Steps run under the server's action lock; this lock only guards reads from GET /api/state.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.next = 0
            self.entries: list[dict[str, Any] | None] = [None] * len(STEPS)
            self.transactions: list[str] = []
            self.summary: dict[str, Any] | None = None
            # what step 0 deployed, to notice contracts replaced since (a node restart, deploy_local --reset)
            self.contracts: dict[str, str] = {}
            self.deploy_block = ""
            # values a step keeps between its tries (the reward balance before a grant)
            self.memo: dict[str, Any] = {}

    def progress(self) -> dict[str, Any]:
        """A copy for the page (the server compares and removes contracts and deploy_block)."""
        with self.lock:
            return {
                "started": self.entries[0] is not None and self.entries[0]["ok"],
                "next": self.next,
                "total": len(STEPS) - 1,
                "finished": self.next >= len(STEPS),
                "steps": [{"step": number, "title": step.title, "role": step.role, "expected": step.expected}
                          for number, step in enumerate(STEPS)],
                "entries": list(self.entries),
                "summary": self.summary,
                "transactions": list(self.transactions),
                "contracts": dict(self.contracts),
                "deploy_block": self.deploy_block,
            }

    def start(self, settings: dict[str, Any], settings_path: Any) -> dict[str, Any]:
        """Step 0: fresh contracts and local files. Everything done before is left behind."""
        self.reset()
        return self._run(0, settings, settings_path)

    def run_next(self, settings: dict[str, Any], settings_path: Any, step: Any = None) -> dict[str, Any]:
        """Run the current step (again) or, once finished, a final check that could not run. step is the number
        the page showed on its button; a different one means the page was behind, and nothing is sent.
        """
        if step is not None and (isinstance(step, bool) or not isinstance(step, int)):
            raise actions.InvalidRequest("unknown step")
        if self.next == 0:
            raise actions.InvalidRequest("start the guided demo first: it begins with fresh contracts")
        if step is not None and step != self.next:
            raise actions.InvalidRequest("that step has already run (the page was behind): nothing was sent")
        if self.next >= len(STEPS):
            if self.summary is not None and "audit_ok" not in self.summary:
                return self._check_again(settings)
            raise actions.InvalidRequest("the guided demo is finished: start it again for fresh contracts")
        return self._run(self.next, settings, settings_path)

    def _run(self, number: int, settings: dict[str, Any], settings_path: Any) -> dict[str, Any]:
        step = STEPS[number]
        items: list[dict[str, Any]] = []
        try:
            ok, got = step.run(self, settings, settings_path, items)
        except Exception as error:
            # a read after the actions failed (the node stopped, say): the actions stay listed, the step current
            mapped = actions.error_result(error)
            items.append(_item("check", None, mapped))
            ok, got = False, mapped["message"]
        entry = {"step": number, "title": step.title, "role": step.role, "expected": step.expected,
                 "ok": ok, "got": got, "results": items}
        logged = [item["result"]["tx"] for item in items if item["logged"] and item["result"]["tx"]]
        # the last step's summary is ready before the page can see the demo as finished
        summary = self._final_check(settings, self.transactions + logged) if ok and number == len(STEPS) - 1 else None
        with self.lock:
            self.entries[number] = entry
            if ok:
                self.next = number + 1
                self.transactions += logged
                self.summary = summary
        status = "ok" if ok else "mismatch"
        message = f"step {number}: {step.title}: " + ("as expected" if ok else f"expected {step.expected}; got {got}")
        return actions.result(status, message, step=entry)

    def _check_again(self, settings: dict[str, Any]) -> dict[str, Any]:
        summary = self._final_check(settings, self.transactions)
        with self.lock:
            self.summary = summary
        return actions.result("ok" if summary["ok"] else "mismatch", f"final check: {summary['message']}", summary=summary)

    def _final_check(self, settings: dict[str, Any], transactions: list[str]) -> dict[str, Any]:
        # this run against demo_workflow's recorded story; only the guided demo's own requests count
        try:
            client, accounts, contracts = demo_workflow._session(settings)
            ours = {transaction.lower() for transaction in transactions}
            labels = {address.lower(): label for label, address in accounts.items()}
            events = [
                event for event in chain.list_access_events(contracts["ConsentManager"], from_block=0)
                if event["transaction_hash"].lower() in ours
            ]
            observed = [
                (labels.get(event["requester"].lower(), event["requester"]), event["scope"], event["allowed"],
                 Reason(event["reason"]).name) for event in events
            ]
            balances = {
                label: chain.get_reward_balance(contracts["ConsentRewardToken"], account=address)
                for label, address in accounts.items()
            }
        except Exception as error:
            # no audit_ok: the check did not run, so it can be run again
            return {"ok": False, "message": f"the final check could not run: {actions.error_result(error)['message']}"}
        audit_ok = observed == demo_workflow.EXPECTED_AUDIT
        rewards_ok = balances == {label: 2 if label == "guardian" else 0 for label in balances}
        return {
            "ok": audit_ok and rewards_ok, "audit_ok": audit_ok, "rewards_ok": rewards_ok, "rewards": balances,
            "audit": [f"{label} {main.scope_name(scope)} {'allowed' if allowed else 'denied'} {reason}"
                      for label, scope, allowed, reason in observed],
            "message": "the guided demo gave the same outcomes as python -m integration.demo_workflow"
            if audit_ok and rewards_ok else "the guided demo did not end like python -m integration.demo_workflow",
        }


def _item(label: str, key: str | None, result: dict[str, Any], logged: bool = False) -> dict[str, Any]:
    # one action inside a step; key is the page's outcome card it also fills; logged marks an access request
    return {"label": label, "key": key, "result": result, "logged": logged}


def _said(result: dict[str, Any]) -> str:
    # what an answer said, for the "got" text
    text = f"{result['status']} {result['reason']}".strip()
    if result["fields"]:
        text += f" {result['fields']}"
    return text if result["status"] in (OUTCOME_ALLOWED, OUTCOME_DENIED) else result["message"]


def _answer_is(result: dict[str, Any], status: str, reason: str, fields: dict[str, Any]) -> bool:
    return result["status"] == status and result["reason"] == reason and result["fields"] == fields


def _matching(value: bool | None) -> str:
    return {True: "matches the local record", False: "does NOT match the local record"}.get(
        value, "could not be checked (node or local record unavailable)")


def _prepare(demo: GuidedDemo, settings: dict[str, Any], settings_path: Any, items: list[dict[str, Any]]) -> Any:
    if settings["expected_chain_id"] != demo_workflow.LOCAL_CHAIN_ID:
        # as demo_workflow: step 11 moves node time, so refuse before deploying anything
        refused = actions.result("refused", "refused: the guided demo moves node time, so it only runs on the local Hardhat chain 31337")
        items.append(_item("deployer deploys fresh contracts", "deploy", refused))
        return False, refused["message"]
    deployed = actions.perform(actions.deploy, settings_path)
    items.append(_item("deployer deploys fresh contracts", "deploy", deployed))
    if deployed["status"] != "ok":
        return False, deployed["message"]
    with demo.lock:
        demo.contracts = dict(deployed["details"]["contracts"])
        demo.deploy_block = deployed["details"]["deploy_block"]
    files = actions.perform(actions.setup, settings)
    items.append(_item("local files and salts", "setup", files))
    return files["status"] == "ok", f"fresh contracts; {files['message']}"


def _register(demo: GuidedDemo, settings: dict[str, Any], settings_path: Any, items: list[dict[str, Any]]) -> Any:
    answers = []
    for label in records.REGISTERING_LABELS:
        answers.append(actions.perform(actions.register, settings, label))
        items.append(_item(f"{label} registers", f"register:{label}", answers[-1]))
    # AlreadyRegistered: an earlier try of this step got that far; the on-chain hash below decides
    earlier = [answer for answer in answers if answer["message"] == "rejected: AlreadyRegistered"]
    failed = [answer["message"] for answer in answers if answer["status"] != "ok" and answer not in earlier]
    if failed:
        return False, "; ".join(failed)
    client, accounts, contracts = demo_workflow._session(settings)
    matching = all(
        chain.get_user_info(contracts["IdentityRegistry"], account=accounts[label])["identity_hash"]
        == records.prepare_identity(*records.identity_paths(settings, label))
        for label in records.REGISTERING_LABELS
    )
    got = "3 registered" + (" (some by an earlier try)" if earlier else "")
    return matching, got + "; on-chain identity hashes " + ("match the local ones" if matching else "differ from the local ones")


def _attest(demo: GuidedDemo, settings: dict[str, Any], settings_path: Any, items: list[dict[str, Any]]) -> Any:
    wrong = actions.perform(actions.attest, settings, "guardian")
    items.append(_item("the guardian tries to attest its own record", "attest:guardian", wrong))
    right = actions.perform(actions.attest, settings, "clinic")
    items.append(_item("the clinic attests the guardian's record", "attest:clinic", right))
    # EvidenceAlreadyRegistered: an earlier try attested already; the on-chain commitment decides
    attested = right["status"] == "ok" or right["message"] == "rejected: EvidenceAlreadyRegistered"
    matches = _original_matches(settings) if attested else None
    clinic = "ok" if right["status"] == "ok" else right["message"]
    got = f"guardian: {wrong['message']}; clinic: {clinic}" + (f", the on-chain commitment {_matching(matches)}" if attested else "")
    return wrong["message"] == "rejected: NotTrustedClinic" and matches is True, got


def _grant_step(requester: str, scope: Scope, days: int, reward: int) -> Callable[..., Any]:
    def run(demo: GuidedDemo, settings: dict[str, Any], settings_path: Any, items: list[dict[str, Any]]) -> Any:
        key = f"reward before granting {requester} {scope.name} for {days} days"
        retry = key in demo.memo
        if not retry:
            demo.memo[key] = _guardian_balance(settings)
        before = demo.memo[key]
        answer = actions.perform(actions.grant, settings, "guardian", requester, scope.name, days)
        items.append(_item(f"guardian grants {requester} {scope.name} for {days} day{'' if days == 1 else 's'}", "consent", answer))
        if answer["status"] == "ok":
            after, note = answer["details"]["reward_after"], ""
        elif retry and answer["message"] == "rejected: ConsentStillActive":
            # the grant of an earlier try went through; its reward is checked from the balance before that try
            after, note = _guardian_balance(settings), " (granted by an earlier try)"
        else:
            return False, answer["message"]
        return after == before + reward, f"reward {before} -> {after}{note}"
    return run


def _access_step(label: str, request: Callable[..., Any], key: str, status: str, reason: str, fields: Any) -> Callable[..., Any]:
    def run(demo: GuidedDemo, settings: dict[str, Any], settings_path: Any, items: list[dict[str, Any]]) -> Any:
        answer = actions.perform(request, settings)
        items.append(_item(label, key, answer, logged=True))
        expected = fields(settings) if callable(fields) else fields
        return _answer_is(answer, status, reason, expected), _said(answer)
    return run


def _tamper(demo: GuidedDemo, settings: dict[str, Any], settings_path: Any, items: list[dict[str, Any]]) -> Any:
    answer = actions.perform(tamper, settings)
    items.append(_item("doctor requests with a tampered copy", "tamper", answer, logged=True))
    details = answer["details"]
    got = _said(answer)
    if "copy_deleted" in details:
        got += f"; copy deleted: {'yes' if details['copy_deleted'] else 'NO'}; the original {_matching(details['original_matches'])}"
    ok = _answer_is(answer, OUTCOME_DENIED, "HASH_MISMATCH", {}) and details.get("copy_deleted") is True \
        and details.get("original_matches") is True
    return ok, got


def _revoke(demo: GuidedDemo, settings: dict[str, Any], settings_path: Any, items: list[dict[str, Any]]) -> Any:
    # a revoke of a revoked grant changes nothing, so a second try is safe
    revoked = actions.perform(actions.revoke, settings, "guardian", "school", Scope.MEASLES_STATUS.name)
    items.append(_item("guardian revokes the school", "consent", revoked))
    if revoked["status"] != "ok":
        return False, revoked["message"]
    answer = actions.perform(actions.school_check, settings)
    items.append(_item("school checks measles status", "school", answer, logged=True))
    return _answer_is(answer, OUTCOME_DENIED, "REVOKED", {}), _said(answer)


def _expiry(demo: GuidedDemo, settings: dict[str, Any], settings_path: Any, items: list[dict[str, Any]]) -> Any:
    # demo_workflow.demonstrate_revocation_and_expiry's exact-expiry part, with the page's actions
    client, accounts, contracts = demo_workflow._session(settings)
    manager, guardian, school = contracts["ConsentManager"], accounts["guardian"], accounts["school"]
    consent = chain.get_consent(manager, owner=guardian, requester=school, scope=Scope.MEASLES_STATUS)
    expires_at = consent["expires_at"]
    now = demo_workflow._block_timestamp(client, "latest")
    if expires_at == 0 or consent["revoked"]:
        note = "the school's 1-day grant from step 10 is not in place: start again for fresh contracts"
    elif now >= expires_at:
        note = "node time is already at or past expiresAt (a jump to expiry, or an earlier try): start again for fresh contracts"
    else:
        note = ""
    if note:
        items.append(_item("node", None, actions.result("refused", f"refused: {note}")))
        return False, note
    if now < expires_at - 1:
        demo_workflow.advance_time(client, expires_at - 1, mine=True)
        items.append(_item("node", None, actions.result("ok", f"empty block mined at expiresAt - 1 ({actions._utc(expires_at - 1)})")))
    else:
        # an earlier try of this step mined it already
        items.append(_item("node", None, actions.result("ok", f"the block at expiresAt - 1 ({actions._utc(expires_at - 1)}) is already mined")))
    decision = chain.check_access(manager, owner=guardian, requester=school, scope=Scope.MEASLES_STATUS)
    view = Reason(decision["reason"]).name
    items.append(_item("view", None, actions.result(
        "ok", f"checkAccess(guardian, school, MEASLES_STATUS) one second before: {'allowed' if decision['allowed'] else 'denied'} "
        f"({view}). View only: nothing logged or released")))
    demo_workflow.advance_time(client, expires_at)
    items.append(_item("node", None, actions.result("ok", f"the next block's time is set to expiresAt ({actions._utc(expires_at)})")))
    answer = actions.perform(actions.school_check, settings)
    items.append(_item("school checks measles status", "school", answer, logged=True))
    landed = None
    if answer["tx"]:
        events = [event for event in chain.list_access_events(manager, from_block=0)
                  if event["transaction_hash"].lower() == answer["tx"].lower()]
        landed = events[0]["timestamp"] if len(events) == 1 else None
    ok = decision["allowed"] and _answer_is(answer, OUTCOME_DENIED, "EXPIRED", {}) and landed == expires_at
    got = f"view one second before: {view}; request: {_said(answer)}"
    if landed is not None:
        got += f"; mined at {'exactly expiresAt' if landed == expires_at else actions._utc(landed)}"
    return ok, got


def _schedule(settings: dict[str, Any]) -> dict[str, Any]:
    # built from the card here, not with disclosure's projection, so the check is independent (as in demo_workflow)
    card = main.record_snapshot(settings)["card"]
    return {"vaccinations": [{"vaccine": event["vaccine"], "date": event["date"]} for event in card["vaccinations"]]}


def _guardian_balance(settings: dict[str, Any]) -> int:
    client, accounts, contracts = demo_workflow._session(settings)
    return chain.get_reward_balance(contracts["ConsentRewardToken"], account=accounts["guardian"])


def _original_matches(settings: dict[str, Any]) -> bool | None:
    # the local record's commitment against the one registered for the guardian; None when nothing is
    # attested yet or either side cannot be read
    try:
        commitment = main.record_snapshot(settings)["commitment"]
        client = chain.connect(settings)
        guardian = chain.select_account(client, "guardian", settings)
        registry = main.contract(client, "IdentityRegistry", settings)
        registered = chain.get_user_info(registry, account=guardian)["vaccination_hash"]
    except Exception:
        return None
    return None if registered == disclosure.ZERO_HASH else registered == commitment


STEPS = (
    Step("fresh contracts and local files", "deployer",
         "fresh contracts deployed; local files and salts created or kept", _prepare),
    Step("register the guardian, school and doctor", "guardian",
         "3 registered with the local identity hashes", _register),
    Step("attest: a wrong actor, then the clinic", "clinic",
         "guardian rejected: NotTrustedClinic; the clinic's attestation matches the local record", _attest),
    Step("the school is denied", "school", "denied NO_CONSENT, nothing released",
         _access_step("school checks measles status", actions.school_check, "school", OUTCOME_DENIED, "NO_CONSENT", {})),
    Step("grant the school 30 days", "guardian", "reward +1 (first grant of this consent)",
         _grant_step("school", Scope.MEASLES_STATUS, GRANT_DAYS, 1)),
    Step("the school is allowed", "school", "allowed, status only: {'measles_status': 'verified'}",
         _access_step("school checks measles status", actions.school_check, "school", OUTCOME_ALLOWED, "ALLOWED",
                      {"measles_status": "verified"})),
    Step("grant the doctor 30 days", "guardian", "reward +1",
         _grant_step("doctor", Scope.VACCINATION_SCHEDULE, GRANT_DAYS, 1)),
    Step("the doctor is allowed", "doctor", "allowed, vaccine and date only",
         _access_step("doctor views the schedule", actions.doctor_view, "doctor", OUTCOME_ALLOWED, "ALLOWED", _schedule)),
    Step("tamper with a copy", "doctor",
         "denied HASH_MISMATCH, nothing released; the copy is deleted and the original still matches", _tamper),
    Step("revoke the school", "school", "denied REVOKED", _revoke),
    Step("regrant the school for 1 day", "guardian", "reward +0 (this consent was already rewarded)",
         _grant_step("school", Scope.MEASLES_STATUS, REGRANT_DAYS, 0)),
    Step("jump to the exact expiry", "school",
         "one second before, the view still allows; the request mined at exactly expiresAt is denied EXPIRED", _expiry),
)
