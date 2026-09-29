# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Offline tests for integration/demo_workflow.py: the whole scripted story, its failure lines, advance_time
and the tamper copy under settings data_root. No node: the fake chain stands in for app.chain, FakeNode
adds the few raw node calls the demo makes itself (block times, receipts, the two Hardhat time methods),
and deploy_local.run is mocked. The live runs on a real node are in evaluation/results/test_results.csv.
"""
import base64
import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import disclosure, records
from app.models import (
    Scope, Reason, ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, ArtifactUnavailable, TransactionRejected,
    TransactionPending,
)
from integration import demo_workflow
from scripts import deploy_local
from tests.fake_chain import FakeChain, install, DAY

# every transaction mines one block, as Hardhat's automine does
TRANSACTIONS = ("register_user", "register_vaccination", "grant_consent", "revoke_consent", "request_access")
# local record values that only the attest step may print (the date is also the doctor's released field)
RECORD_ONLY_TEXT = ("child-demo-001", "ABC123-DEMO", "Clinic A", "rubella", "mumps", '"batch"')


class FakeNode(FakeChain):
    """FakeChain plus block times: each transaction opens a block one second after the latest, unless
    evm_setNextBlockTimestamp set its time; views read the latest block's time, as the contracts do.
    """

    def __init__(self, settings, chain_id=31337):
        super().__init__(settings)
        self.block_times = [self.now]
        self.next_time = None
        self.receipts = {}
        # (method, params) of every raw node request the demo makes
        self.node_requests = []
        self.eth = SimpleNamespace(
            chain_id=chain_id, get_block=self.get_block, get_transaction_receipt=self.get_transaction_receipt,
        )
        self.manager = SimpleNamespace(request_blocking=self.request_blocking)
        self.contracts = {name: SimpleNamespace(name=name, address=f"0x{index:040x}") for index, name in enumerate(
            ("IdentityRegistry", "ConsentManager", "ConsentRewardToken"), 0xC0,
        )}

    def load_contract(self, client, name, deployment_path):
        super().load_contract(client, name, deployment_path)
        return self.contracts[name]

    def get_block(self, block):
        number = len(self.block_times) - 1 if block == "latest" else block
        return {"number": number, "timestamp": self.block_times[number]}

    def get_transaction_receipt(self, transaction_hash):
        receipt = self.receipts[transaction_hash]
        return {"status": 1, "gasUsed": receipt["gas_used"], "blockNumber": receipt["block_number"]}

    def request_blocking(self, method, params):
        self.node_requests.append((method, list(params)))
        if method == "evm_setNextBlockTimestamp":
            self.next_time = params[0]
        elif method == "evm_mine":
            self.mine()
        else:
            raise ValueError("method not faked")

    def mine(self):
        self.now = self.next_time if self.next_time is not None else self.now + 1
        self.next_time = None
        self.block_times.append(self.now)

    def _start(self, function, **arguments):
        if function in TRANSACTIONS:
            self.mine()
        super()._start(function, **arguments)

    def _receipt(self):
        receipt = super()._receipt()
        receipt.update(block_number=len(self.block_times) - 1, gas_used=40_000 + self.transactions)
        self.receipts[receipt["transaction_hash"]] = receipt
        return receipt


class DemoTestCase(unittest.TestCase):
    """A temporary project folder (PROJECT_ROOT and the default DATA_ROOT point into it) and a settings
    file whose data paths are absolute and point to data_root, which is outside that project folder.
    """

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.temp = Path(folder.name).resolve()
        self.project = self.temp / "project"
        self.data = self.temp / "scratch-data"
        for name, value in (("PROJECT_ROOT", self.project), ("DATA_ROOT", self.project / "runtime-data")):
            patcher = mock.patch.object(records, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.settings = {
            "rpc_url": "http://127.0.0.1:8545",
            "expected_chain_id": 31337,
            "data_root": str(self.data),
            "deployment_file": str(self.data / "deployment.json"),
            "vaccination_file": str(self.data / "vaccination_record.json"),
            "vaccination_salt_file": str(self.data / "private" / "vaccination_salt.json"),
            "identity_directory": str(self.data / "identities"),
            "identity_salt_directory": str(self.data / "private"),
            "actor_account_indices": {"deployer": 0, "clinic": 1, "guardian": 2, "school": 3, "doctor": 4},
        }
        self.settings_file = self.temp / "settings.json"
        self.settings_file.write_text(json.dumps(self.settings))
        self.fake = self.make_fake()
        install(self, self.fake)
        patcher = mock.patch.object(deploy_local, "run")
        self.deploy = patcher.start()
        self.addCleanup(patcher.stop)

    def make_fake(self):
        return FakeNode(self.settings)

    def run_main(self, *argv):
        """Run demo_workflow.main; returns (stdout, stderr, exit code or None)."""
        output, errors = io.StringIO(), io.StringIO()
        code = None
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                demo_workflow.main(list(argv) or ["--settings", str(self.settings_file)])
            except SystemExit as exit_:
                code = exit_.code
        return output.getvalue(), errors.getvalue(), code

    def assert_failed_at(self, step, reason):
        output, errors, code = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(errors, f"demo FAILED at step {step}: {reason}\n")
        self.assertNotIn("demo passed", output)
        return output

    def sections(self, output):
        """The transcript split by its "== N. title ==" headers: {N: text}."""
        parts = re.split(r"^== (\d+)\. .* ==$", output, flags=re.MULTILINE)
        return {int(number): text for number, text in zip(parts[1::2], parts[2::2])}


class StoryTests(DemoTestCase):
    def test_the_whole_story_passes(self):
        output, errors, code = self.run_main()
        self.assertIsNone(code)
        self.assertEqual(errors, "")
        self.assertTrue(output.rstrip().endswith("demo passed: every outcome above was checked"))
        self.deploy.assert_called_once_with(self.settings_file, reset=True)
        observed = [
            ({v.lower(): k for k, v in self.fake.addresses.items()}[event["requester"].lower()], event["scope"],
             event["allowed"], Reason(event["reason"]).name)
            for event in self.fake.events
        ]
        self.assertEqual(observed, demo_workflow.EXPECTED_AUDIT)
        balances = {label: self.fake.get_reward_balance(None, account=address) for label, address in self.fake.addresses.items()}
        self.assertEqual(balances, {"deployer": 0, "clinic": 0, "guardian": 2, "school": 0, "doctor": 0})

    def test_steps_run_in_the_order_of_the_file(self):
        output, _, _ = self.run_main()
        self.assertEqual(list(self.sections(output)), list(range(9)))
        titles = re.findall(r"^== \d+\. (.*) ==$", output, flags=re.MULTILINE)
        self.assertIn("tamper", titles[6])
        self.assertIn("revoke", titles[7])
        # node time only moves in the expiry step: one mined block at expiresAt - 1, then the request's time
        self.assertEqual(len(self.fake.calls_to("request_access")), 6)
        self.assertEqual(
            [method for method, _ in self.fake.node_requests],
            ["evm_setNextBlockTimestamp", "evm_mine", "evm_setNextBlockTimestamp"],
        )

    def test_wrong_actor_attest_is_rejected_before_the_clinic_attests(self):
        output, _, _ = self.run_main()
        attempts = self.fake.calls_to("register_vaccination")
        self.assertEqual([call["clinic"] for call in attempts], [self.fake.addresses["guardian"], self.fake.addresses["clinic"]])
        self.assertIn("rejected: NotTrustedClinic, nothing stored", self.sections(output)[3])

    def test_released_fields_are_status_only_and_vaccine_and_date_only(self):
        output, _, _ = self.run_main()
        sections = self.sections(output)
        self.assertIn('released: {"measles_status": "verified"}', sections[4])
        self.assertIn('released: {"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]}', sections[5])
        for number in (4, 5, 6, 7, 8):
            for text in RECORD_ONLY_TEXT:
                self.assertNotIn(text, sections[number], f"step {number}")
        self.assertNotIn("2026-03-12", sections[4])

    def test_transcript_never_shows_a_salt(self):
        output, _, _ = self.run_main()
        salt_files = [records.settings_path(self.settings, "vaccination_salt_file")]
        salt_files += [records.identity_paths(self.settings, label)[1] for label in records.REGISTERING_LABELS]
        for path in salt_files:
            salt = records.load_salt(path)
            for text in (base64.b64encode(salt).decode(), salt.hex(), salt.hex()[:16], repr(salt)[2:18]):
                self.assertNotIn(text, output)

    def test_tamper_copy_goes_under_the_configured_data_root(self):
        # the record was only accepted because data_root, not the default DATA_ROOT, is checked
        output, _, _ = self.run_main()
        self.assertIn("tamper: doctor request on the copy -> denied: HASH_MISMATCH", output)
        self.assertEqual(list((self.data / "tamper").iterdir()), [])
        self.assertFalse(self.project.exists())
        tampered = self.fake.calls_to("request_access")[3]
        self.assertEqual(tampered["requester"], self.fake.addresses["doctor"])
        self.assertNotEqual(tampered["observed_hash"], self.fake.users[self.fake.addresses["guardian"].lower()]["vaccination_hash"])

    def test_transcript_shows_local_files_without_the_home_folder(self):
        # data_root is outside the project here, as a real user's scratch folder would be
        output, _, _ = self.run_main()
        self.assertIn("setup: created <data_root>/private/vaccination_salt.json\n", output)
        self.assertIn("local record <data_root>/vaccination_record.json (269 bytes", output)
        self.assertNotIn(str(self.temp), output)

    def test_second_run_keeps_every_local_file(self):
        self.run_main()
        before = {path: path.read_bytes() for path in self.data.rglob("*") if path.is_file()}
        # a fresh deployment: a new fake chain, the same local files
        self.fake = self.make_fake()
        install(self, self.fake)
        output, errors, code = self.run_main()
        self.assertIsNone(code, errors)
        self.assertIn("setup: every local file already exists", output)
        self.assertEqual({path: path.read_bytes() for path in self.data.rglob("*") if path.is_file()}, before)

    def test_expired_request_lands_exactly_on_expires_at(self):
        self.run_main()
        expired = self.fake.events[-1]
        school = self.fake.addresses["school"]
        consent = self.fake.consents[(self.fake.addresses["guardian"].lower(), school.lower(), 1)]
        self.assertEqual(expired["timestamp"], consent["expires_at"])
        self.assertEqual(expired["reason"], Reason.EXPIRED)
        self.assertEqual(self.fake.node_requests, [
            ("evm_setNextBlockTimestamp", [consent["expires_at"] - 1]), ("evm_mine", []),
            ("evm_setNextBlockTimestamp", [consent["expires_at"]]),
        ])


class FailureTests(DemoTestCase):
    def test_missing_settings_file(self):
        output, errors, code = self.run_main("--settings", str(self.temp / "missing.json"))
        self.assertEqual((code, errors), (1, "demo FAILED at step start: no settings file: copy config/settings.example.json to config/settings.json\n"))
        self.deploy.assert_not_called()

    def test_settings_that_are_not_json_or_not_an_object(self):
        for text, reason in (("{", "the settings file is not valid JSON"), ("[]", "the settings file is not a settings object")):
            with self.subTest(text):
                self.settings_file.write_text(text)
                self.assert_failed_at("start", reason)
        self.deploy.assert_not_called()

    def test_node_not_reachable(self):
        self.fake.failures["connect"] = ChainUnavailable("RPC connection failed")
        self.assert_failed_at(0, "unavailable: RPC connection failed: start the node with npm run node and check rpc_url and expected_chain_id")
        self.deploy.assert_not_called()

    def test_missing_web3_says_how_to_install_it_not_to_start_the_node(self):
        self.fake.failures["connect"] = Web3NotInstalled("web3 not installed")
        self.assert_failed_at(0, disclosure.NO_WEB3_MESSAGE)
        self.deploy.assert_not_called()

    def test_missing_artifacts_say_compile(self):
        self.fake.failures["load_contract"] = ArtifactUnavailable("contract artifact unavailable")
        self.assert_failed_at(2, "compiled contracts not found: run npm run compile first")

    def test_deploy_failure_line_has_no_absolute_path(self):
        # the real deploy_local message for a deployment_file it cannot write, with data_root outside the project
        self.deploy.side_effect = deploy_local.DeployError(
            f"could not write {records.shown_path(self.data / 'deployment.json', self.settings)} (deployment_file); "
            "the contracts above are deployed but not saved"
        )
        self.assert_failed_at(
            1, "could not write <data_root>/deployment.json (deployment_file); the contracts above are deployed but not saved",
        )

    def test_other_chain_is_refused_before_deploying(self):
        self.fake.eth.chain_id = 1
        self.assert_failed_at(0, "the demo moves node time, so it only runs on the local Hardhat chain 31337")
        self.deploy.assert_not_called()

    def test_deploy_failure(self):
        self.deploy.side_effect = deploy_local.DeployError("IdentityRegistry deploy failed: local node not reachable")
        self.assert_failed_at(1, "IdentityRegistry deploy failed: local node not reachable")
        self.assertEqual(self.fake.calls_to("register_user"), [])

    def test_no_usable_deployment(self):
        self.fake.failures["load_contract"] = DeploymentUnavailable("deployment unavailable")
        self.assert_failed_at(2, disclosure.NO_DEPLOYMENT_MESSAGE)

    def test_rejected_and_pending_transactions(self):
        for name, error, step, reason in (
            ("register_user", TransactionRejected("AlreadyRegistered"), 2, "rejected: AlreadyRegistered"),
            ("grant_consent", TransactionPending(), 4, "pending: no receipt in time"),
            ("revoke_consent", TransactionRejected(), 7, "rejected: Reverted"),
        ):
            with self.subTest(name):
                self.fake = self.make_fake()
                install(self, self.fake)
                self.fake.failures[name] = error
                self.assert_failed_at(step, reason)

    def test_bad_local_file_fails_without_a_path(self):
        records.setup_runtime(self.settings)
        salt_path = records.settings_path(self.settings, "vaccination_salt_file")
        salt_path.write_text("{}")
        self.assert_failed_at(3, "salt unavailable")

    def test_school_view_that_leaks_a_field_stops_the_demo(self):
        leaky = {"measles_status": "verified", "date": "2026-03-12"}
        with mock.patch.object(disclosure, "select_school_status", return_value=leaky):
            self.assert_failed_at(4, "the released fields are not exactly the allowlisted ones")

    def test_denied_response_with_fields_stops_the_demo(self):
        real = disclosure.format_denial

        def with_fields(*args, **kwargs):
            response = real(*args, **kwargs)
            response["fields"] = {"measles_status": "verified"}
            return response

        with mock.patch.object(disclosure, "format_denial", side_effect=with_fields):
            self.assert_failed_at(4, "a denied request must release nothing")

    def test_two_labels_on_one_account_stop_the_demo(self):
        self.fake.addresses["school"] = self.fake.addresses["doctor"]
        self.assert_failed_at(2, "every actor label needs its own account")
        self.assertEqual(self.fake.calls_to("register_user"), [])


class MutatedChainTests(DemoTestCase):
    """The demo's own checks catch a chain that breaks a rule, one rule per fake."""

    def run_with(self, fake_class):
        self.fake = fake_class(self.settings)
        install(self, self.fake)
        return self.run_main()

    def test_reward_on_every_grant(self):
        class RewardsTwice(FakeNode):
            def grant_consent(self, manager, guardian, requester, scope, duration_days):
                self.rewarded.clear()
                return super().grant_consent(manager, guardian, requester, scope, duration_days)

        _, errors, code = self.run_with(RewardsTwice)
        self.assertEqual((code, errors), (1, "demo FAILED at step 7: guardian reward should change by +0, not 2 -> 3\n"))

    def test_any_account_may_attest(self):
        class AnyClinic(FakeNode):
            def register_vaccination(self, registry, clinic, guardian, record_hash):
                return super().register_vaccination(registry, self.addresses["clinic"], guardian, record_hash)

        _, errors, code = self.run_with(AnyClinic)
        self.assertEqual((code, errors), (1, "demo FAILED at step 3: only the trusted clinic may attest a record\n"))

    def test_grant_still_valid_at_expires_at(self):
        class LateExpiry(FakeNode):
            def _evaluate(self, owner, requester, scope):
                allowed, reason = super()._evaluate(owner, requester, scope)
                consent = self.consents.get((owner.lower(), requester.lower(), int(scope)))
                if reason == Reason.EXPIRED and self.now == consent["expires_at"]:
                    return True, Reason.ALLOWED
                return allowed, reason

        # the request at expiresAt is allowed and releases the status, which the first check catches
        _, errors, code = self.run_with(LateExpiry)
        self.assertEqual((code, errors), (1, "demo FAILED at step 7: a denied request must release nothing\n"))

    def test_expired_request_one_second_late(self):
        # still EXPIRED, but it no longer shows that the grant ends at exactly expiresAt
        class OneSecondLate(FakeNode):
            def request_blocking(self, method, params):
                late = method == "evm_setNextBlockTimestamp" and any(name == method for name, _ in self.node_requests)
                super().request_blocking(method, [params[0] + 1] if late else params)

        _, errors, code = self.run_with(OneSecondLate)
        self.assertEqual((code, errors), (1, "demo FAILED at step 7: the expired request did not land exactly on expiresAt\n"))

    def test_audit_event_for_another_owner(self):
        class OtherOwner(FakeNode):
            def list_access_events(self, manager, from_block):
                events = super().list_access_events(manager, from_block)
                if events:
                    events[0]["owner"] = self.addresses["doctor"]
                return events

        _, errors, code = self.run_with(OtherOwner)
        self.assertEqual((code, errors), (1, "demo FAILED at step 8: every attempt is for the guardian's record\n"))

    def test_revoke_that_does_not_revoke(self):
        class NoRevoke(FakeNode):
            def revoke_consent(self, manager, guardian, requester, scope):
                receipt = super().revoke_consent(manager, guardian, requester, scope)
                self.consents[(guardian.lower(), requester.lower(), int(scope))]["revoked"] = False
                return receipt

        _, errors, code = self.run_with(NoRevoke)
        self.assertEqual((code, errors), (1, "demo FAILED at step 7: the school grant is not revoked\n"))


class TamperDataRootTests(DemoTestCase):
    def setUp(self):
        super().setUp()
        records.setup_runtime(self.settings)
        for label in records.REGISTERING_LABELS:
            identity_hash = records.prepare_identity(*records.identity_paths(self.settings, label))
            self.fake.register_user(None, account=self.fake.addresses[label], identity_hash=identity_hash)
        commitment = records.load_snapshot(
            records.settings_path(self.settings, "vaccination_file"),
            records.settings_path(self.settings, "vaccination_salt_file"),
        )["commitment"]
        self.fake.register_vaccination(None, clinic=self.fake.addresses["clinic"], guardian=self.fake.addresses["guardian"], record_hash=commitment)
        self.fake.grant_consent(
            None, guardian=self.fake.addresses["guardian"], requester=self.fake.addresses["doctor"],
            scope=Scope.VACCINATION_SCHEDULE, duration_days=30,
        )

    def tamper(self, copy_path):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            demo_workflow.demonstrate_tampering(self.settings, copy_path)
        return output.getvalue()

    def test_copy_under_an_absolute_data_root_outside_the_project(self):
        output = self.tamper(records.data_root(self.settings) / demo_workflow.TAMPER_COPY)
        self.assertIn("denied: HASH_MISMATCH", output)
        self.assertFalse((self.data / demo_workflow.TAMPER_COPY).exists())

    def test_copy_in_the_default_runtime_data_is_refused_when_data_root_is_elsewhere(self):
        for copy_path in (records.DATA_ROOT / demo_workflow.TAMPER_COPY, Path("runtime-data") / demo_workflow.TAMPER_COPY):
            with self.subTest(str(copy_path)):
                with self.assertRaises(AssertionError) as caught:
                    self.tamper(copy_path)
                self.assertEqual(str(caught.exception), "tamper copy must be inside the data root")
        self.assertFalse(self.project.exists())
        self.assertEqual(self.fake.calls_to("request_access"), [])

    def test_relative_data_root_is_taken_from_the_project_root(self):
        self.settings["data_root"] = "../scratch-data"
        self.assertEqual(records.data_root(self.settings).resolve(), self.data)
        self.assertIn("denied: HASH_MISMATCH", self.tamper(Path("../scratch-data/tamper/vaccination_record.json")))


class AdvanceTimeTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNode({"actor_account_indices": {"guardian": 2}})
        self.latest = self.fake.block_times[-1]

    def test_sets_the_next_block_time_and_mines_on_request(self):
        demo_workflow.advance_time(self.fake, self.latest + 10)
        self.assertEqual(self.fake.node_requests, [("evm_setNextBlockTimestamp", [self.latest + 10])])
        self.assertEqual(self.fake.block_times[-1], self.latest)
        demo_workflow.advance_time(self.fake, self.latest + 20, mine=True)
        self.assertEqual(self.fake.node_requests[-1], ("evm_mine", []))
        self.assertEqual(self.fake.block_times[-1], self.latest + 20)

    def test_time_cannot_go_back_or_stay(self):
        for timestamp in (self.latest, self.latest - 1):
            with self.subTest(timestamp):
                with self.assertRaises(ValueError) as caught:
                    demo_workflow.advance_time(self.fake, timestamp)
                self.assertEqual(str(caught.exception), "node time cannot go back")
        self.assertEqual(self.fake.node_requests, [])

    def test_only_whole_seconds(self):
        for timestamp in (True, float(self.latest + 5), str(self.latest + 5), None):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(ValueError):
                    demo_workflow.advance_time(self.fake, timestamp)
        self.assertEqual(self.fake.node_requests, [])

    def test_only_on_the_local_chain(self):
        self.fake.eth.chain_id = 11155111
        with self.assertRaises(ChainUnavailable) as caught:
            demo_workflow.advance_time(self.fake, self.latest + DAY)
        self.assertEqual(str(caught.exception), "time can only be moved on the local Hardhat chain 31337")
        self.assertEqual(self.fake.node_requests, [])

    def test_node_failure_carries_no_details(self):
        def broken(method, params):
            raise ConnectionError(f"{method} failed at /home/someone/secret {params}")

        self.fake.manager.request_blocking = broken
        with self.assertRaises(ChainUnavailable) as caught:
            demo_workflow.advance_time(self.fake, self.latest + 5)
        self.assertEqual(caught.exception.args, ("local node request failed",))


class HelperTests(unittest.TestCase):
    def test_expect(self):
        self.assertIsNone(demo_workflow.expect(True, "not raised"))
        with self.assertRaises(AssertionError) as caught:
            demo_workflow.expect(False, "the reason")
        self.assertEqual(str(caught.exception), "the reason")

    def test_scope_names_keep_the_raw_unsupported_value(self):
        self.assertEqual(demo_workflow._scope_name(1), "MEASLES_STATUS")
        self.assertEqual(demo_workflow._scope_name(2), "VACCINATION_SCHEDULE")
        for value in (0, 3, 255):
            self.assertEqual(demo_workflow._scope_name(value), f"scope {value} (unsupported)")

    def test_expected_audit_is_the_story(self):
        self.assertEqual(
            [(label, int(scope), allowed, reason) for label, scope, allowed, reason in demo_workflow.EXPECTED_AUDIT],
            [
                ("school", 1, False, "NO_CONSENT"), ("school", 1, True, "ALLOWED"),
                ("doctor", 2, True, "ALLOWED"), ("doctor", 2, False, "HASH_MISMATCH"),
                ("school", 1, False, "REVOKED"), ("school", 1, False, "EXPIRED"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
