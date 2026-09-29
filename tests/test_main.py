# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Tests for the console in app/main.py: input checks, setup, local hashes and error display, plus the
grant, rewards, audit and attest actions against the fake chain.
The menu's chain actions are also checked by running it against the real node.
"""
import contextlib
import io
import json
import unittest
from unittest import mock

from app import chain, main, records
from app.models import ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, ArtifactUnavailable, TransactionRejected
from tests.fake_chain import FakeChain, install, prepare_demo
from tests.support import temp_runtime

NO_DEPLOYMENT = "no deployment for this node: run python -m scripts.deploy_local --reset"
NO_ARTIFACTS = "compiled contracts not found: run npm run compile first"
NO_WEB3 = "web3 not installed: run python -m pip install -r requirements.txt with the venv's Python"


class ConsoleTestCase(unittest.TestCase):
    def setUp(self):
        self.root, self.settings = temp_runtime(self)

    def run_with(self, answers, action, *args):
        """Run action with typed answers; returns printed text and the answers not used."""
        answers = list(answers)

        def fake_input(prompt=""):
            if not answers:
                raise EOFError
            return answers.pop(0)

        output = io.StringIO()
        with mock.patch("builtins.input", fake_input), contextlib.redirect_stdout(output):
            result = action(*args)
        return output.getvalue(), answers, result


class InputTests(ConsoleTestCase):
    def test_menu_asks_again_until_the_choice_is_valid(self):
        _, left, choice = self.run_with(["0", "x", "13", "", "4.0", "4"], main.show_menu)
        self.assertEqual(choice, "grant")
        self.assertEqual(left, [])

    def test_every_menu_number_maps_to_an_action(self):
        for number, (key, _) in enumerate(main.ACTIONS, 1):
            _, _, choice = self.run_with([str(number)], main.show_menu)
            self.assertEqual(choice, key)
            self.assertTrue(key in main.HANDLERS or key in ("switch", "quit"))

    def test_actor_comes_only_from_the_settings_list(self):
        _, _, actor = self.run_with(["6", "admin", "3"], main.select_actor, self.settings)
        self.assertEqual(actor, "guardian")

    def test_grant_days_are_checked_before_any_chain_call(self):
        with mock.patch.object(chain, "connect", side_effect=ChainUnavailable()) as connect:
            # school, measles status, then three bad day counts and one good one
            output, left, _ = self.run_with(["3", "1", "0", "366", "ten", "30"], main.dispatch_action, "grant", "guardian", self.settings)
            self.assertEqual(left, [])
            self.assertEqual(connect.call_count, 1)
            self.assertEqual(output.count("enter a number from 1 to 365"), 3)
            self.assertIn("unavailable: local node not reachable", output)

    def test_grant_never_calls_the_chain_with_bad_days(self):
        with mock.patch.object(chain, "connect") as connect:
            with self.assertRaises(EOFError):
                self.run_with(["3", "1", "0", "366"], main.dispatch_action, "grant", "guardian", self.settings)
            connect.assert_not_called()

    def test_grant_does_not_offer_the_actor_itself(self):
        output, _, label = self.run_with(["3"], main.pick_label, "grant to", self.settings, "guardian")
        self.assertEqual(output.splitlines(), ["1. deployer", "2. clinic", "3. school", "4. doctor"])
        self.assertEqual(label, "school")


class ErrorDisplayTests(ConsoleTestCase):
    def check(self, error, expected):
        with mock.patch.object(chain, "connect", side_effect=error):
            output, _, _ = self.run_with([], main.dispatch_action, "rewards", "guardian", self.settings)
        self.assertEqual(output.strip(), expected)

    def test_chain_errors_show_no_exception_text(self):
        self.check(ChainUnavailable("http://127.0.0.1 child-demo-001"), "unavailable: local node not reachable or wrong chain")
        self.check(TransactionRejected("NotTrustedClinic"), "rejected: NotTrustedClinic")
        self.check(RuntimeError("ABC123-DEMO /Users/somebody"), "failed: RuntimeError")
        self.check(NotImplementedError("connect is not implemented"), "not implemented")
        self.check(DeploymentUnavailable("/Users/somebody/runtime-data/deployment.json"), f"unavailable: {NO_DEPLOYMENT}")

    def test_missing_artifacts_name_the_compile_step_not_the_deploy(self):
        # deploy_local would only answer with the same compile hint
        self.check(ArtifactUnavailable("contract artifact unavailable"), f"unavailable: {NO_ARTIFACTS}")

    def test_missing_web3_names_the_install_not_the_node(self):
        self.check(Web3NotInstalled("web3 not installed"), f"unavailable: {NO_WEB3}")

    def test_unknown_choice(self):
        output, _, _ = self.run_with([], main.dispatch_action, "delete", "guardian", self.settings)
        self.assertEqual(output.strip(), "unknown choice")


class NoNodeTests(ConsoleTestCase):
    def test_setup_then_nothing_to_do(self):
        output, _, _ = self.run_with([], main.dispatch_action, "setup", "guardian", self.settings)
        self.assertEqual(output.count("setup: created"), 8)
        # the temporary data_root is outside the project, so it is shown as <data_root>, never as a full path
        self.assertIn("setup: created <data_root>/private/vaccination_salt.json\n", output)
        self.assertNotIn(str(self.root), output)
        output, _, _ = self.run_with([], main.dispatch_action, "setup", "guardian", self.settings)
        self.assertIn("nothing changed", output)

    def test_show_registration_works_without_a_node(self):
        records.setup_runtime(self.settings)
        snapshot = records.load_snapshot(
            records.settings_path(self.settings, "vaccination_file"),
            records.settings_path(self.settings, "vaccination_salt_file"),
        )
        identity = records.prepare_identity(*records.identity_paths(self.settings, "guardian"))
        with mock.patch.object(chain, "connect", side_effect=NotImplementedError()):
            output, _, _ = self.run_with([], main.dispatch_action, "mine", "guardian", self.settings)
        self.assertIn(f"0x{identity.hex()}", output)
        self.assertIn(f"0x{snapshot['commitment'].hex()}", output)
        self.assertIn("on-chain: not implemented", output)
        with mock.patch.object(chain, "connect", side_effect=ChainUnavailable()):
            output, _, _ = self.run_with([], main.dispatch_action, "mine", "guardian", self.settings)
        self.assertIn("on-chain: local node not reachable", output)
        with mock.patch.object(chain, "connect", side_effect=DeploymentUnavailable()):
            output, _, _ = self.run_with([], main.dispatch_action, "mine", "guardian", self.settings)
        self.assertIn(f"on-chain: {NO_DEPLOYMENT}", output)
        self.assertNotIn("not reachable", output)
        for error, text in ((ArtifactUnavailable(), NO_ARTIFACTS), (Web3NotInstalled(), NO_WEB3)):
            with mock.patch.object(chain, "connect", side_effect=error):
                output, _, _ = self.run_with([], main.dispatch_action, "mine", "guardian", self.settings)
            self.assertEqual(output.splitlines()[-1], f"on-chain: {text}")
        for value in ("MMR", "2026-03-12", "ABC123", "child-demo"):
            self.assertNotIn(value, output)

    def test_show_registration_before_setup_is_unavailable(self):
        output, _, _ = self.run_with([], main.dispatch_action, "mine", "school", self.settings)
        self.assertEqual(output.strip(), "unavailable: identity unavailable")

    def test_clinic_does_not_register(self):
        with mock.patch.object(chain, "connect") as connect:
            output, _, _ = self.run_with([], main.dispatch_action, "register", "clinic", self.settings)
        self.assertEqual(output.strip(), "only guardian, school and doctor register")
        connect.assert_not_called()


class FakeChainMenuTests(ConsoleTestCase):
    """Chain actions of the menu against the fake, which follows the contract rules and error names."""

    def setUp(self):
        super().setUp()
        self.fake = FakeChain(self.settings)
        install(self, self.fake)
        prepare_demo(self.fake, self.settings)

    def action(self, choice, actor, answers=()):
        output, left, _ = self.run_with(answers, main.dispatch_action, choice, actor, self.settings)
        self.assertEqual(left, [])
        return output

    def test_first_grant_rewards_the_guardian_once(self):
        # grant to school (3), measles status (1), 30 days
        output = self.action("grant", "guardian", ["3", "1", "30"])
        self.assertIn("granted school MEASLES_STATUS for 30 days", output)
        self.assertIn("reward balance of guardian: 0 -> 1", output)
        self.assertEqual(self.action("grant", "guardian", ["3", "1", "30"]).splitlines()[-1], "rejected: ConsentStillActive")
        self.assertIn("revoked school MEASLES_STATUS", self.action("revoke", "guardian", ["3", "1"]))
        self.assertIn("reward balance of guardian: 1 -> 1", self.action("grant", "guardian", ["3", "1", "1"]))

    def test_rewards_lists_every_actor(self):
        self.action("grant", "guardian", ["3", "1", "30"])
        output = self.action("rewards", "school")
        self.assertEqual(output.splitlines(), ["deployer: 0", "clinic: 0", "guardian: 1", "school: 0", "doctor: 0"])

    def test_audit_shows_labels_reasons_and_unsupported_scopes(self):
        self.action("school", "school")
        self.fake.request_access("ConsentManager", requester=self.fake.addresses["doctor"], owner=self.fake.addresses["guardian"],
                                 scope=3, observed_hash=bytes(32))
        lines = self.action("audit", "guardian").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("school  MEASLES_STATUS  denied  NO_CONSENT"))
        self.assertTrue(lines[1].endswith("doctor  scope 3 (unsupported)  denied  UNSUPPORTED_SCOPE"))

    def test_audit_with_no_events(self):
        self.assertEqual(self.action("audit", "guardian").strip(), "audit: no access attempts yet")

    def test_wrong_actor_attest_shows_the_error_name(self):
        self.assertEqual(self.action("attest", "guardian").strip(), "rejected: NotTrustedClinic")

    def test_school_check_without_a_deployment_names_the_fix(self):
        self.fake.failures["load_contract"] = DeploymentUnavailable()
        output = self.action("school", "school")
        self.assertEqual(output.splitlines(), ["as school:", f"unavailable: {NO_DEPLOYMENT}"])
        self.assertEqual(self.fake.calls_to("request_access"), [])

    def test_doctor_view_without_artifacts_says_compile(self):
        self.fake.failures["load_contract"] = ArtifactUnavailable()
        output = self.action("doctor", "doctor")
        self.assertEqual(output.splitlines(), ["as doctor:", f"unavailable: {NO_ARTIFACTS}"])
        self.assertEqual(self.fake.calls_to("request_access"), [])

    def test_node_failure_during_a_request_does_not_blame_the_record(self):
        grant = ["4", "2", "30"]
        self.action("grant", "guardian", grant)
        self.fake.failures["request_access"] = ChainUnavailable("access event unavailable")
        self.assertEqual(
            self.action("doctor", "doctor").splitlines(),
            ["as doctor:", "unavailable: local node or receipt problem, nothing released"],
        )


class RunConsoleTests(ConsoleTestCase):
    def test_missing_settings_says_copy_the_example(self):
        output, left, _ = self.run_with(["3"], main.run_console, self.root / "settings.json")
        self.assertIn("copy config/settings.example.json", output)
        self.assertEqual(left, ["3"])

    def test_invalid_settings(self):
        path = self.root / "settings.json"
        path.write_bytes(b"{not json")
        output, _, _ = self.run_with([], main.run_console, path)
        self.assertIn("not valid JSON", output)

    def test_a_short_session(self):
        path = self.root / "settings.json"
        path.write_text(json.dumps(self.settings))
        # guardian, setup, show my registration, switch to school, show my registration, quit
        output, left, _ = self.run_with(["3", "1", "10", "11", "4", "10", "12"], main.run_console, path)
        self.assertEqual(left, [])
        self.assertIn("acting as guardian", output)
        self.assertIn("acting as school", output)
        self.assertEqual(output.count("local identity hash"), 2)
        self.assertEqual(output.count("local record commitment"), 1)

    def test_end_of_input_quits(self):
        path = self.root / "settings.json"
        path.write_text(json.dumps(self.settings))
        output, _, _ = self.run_with(["3", "4", "3"], main.run_console, path)
        self.assertNotIn("failed", output)

    def test_main_starts_the_console(self):
        with mock.patch.object(main, "SETTINGS_FILE", self.root / "missing.json"):
            output, _, _ = self.run_with([], main.main)
        self.assertIn("My Vaccination Card console", output)
        self.assertIn("copy config/settings.example.json", output)
