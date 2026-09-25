"""Tests for the console in app/main.py that need no chain: input checks, setup and local hashes.
The menu's chain actions are checked by running it against the real node (plan D13 and D14).
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import contextlib
import io
import json
import unittest
from unittest import mock

from app import chain, main, records
from app.models import ChainUnavailable, TransactionRejected
from tests.support import temp_runtime


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
        self.check(NotImplementedError("connect is an implementation task"), "not implemented yet")

    def test_unknown_choice(self):
        output, _, _ = self.run_with([], main.dispatch_action, "delete", "guardian", self.settings)
        self.assertEqual(output.strip(), "unknown choice")


class NoNodeTests(ConsoleTestCase):
    def test_setup_then_nothing_to_do(self):
        output, _, _ = self.run_with([], main.dispatch_action, "setup", "guardian", self.settings)
        self.assertEqual(output.count("setup: created"), 8)
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
        self.assertIn("on-chain: not implemented yet", output)
        with mock.patch.object(chain, "connect", side_effect=ChainUnavailable()):
            output, _, _ = self.run_with([], main.dispatch_action, "mine", "guardian", self.settings)
        self.assertIn("on-chain: local node not reachable", output)
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
