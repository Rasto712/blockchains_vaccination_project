"""Tests for the tamper demonstration in integration/demo_workflow.py, run against the fake chain.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import records
from app.models import Scope, ChainUnavailable
from integration import demo_workflow
from tests.fake_chain import FakeChain, install, prepare_demo, ZERO_HASH
from tests.support import temp_runtime


class TamperTests(unittest.TestCase):
    def setUp(self):
        self.root, self.settings = temp_runtime(self)
        self.fake = FakeChain(self.settings)
        install(self, self.fake)
        self.commitment = prepare_demo(self.fake, self.settings)
        self.fake.grant_consent(
            "ConsentManager", guardian=self.fake.addresses["guardian"], requester=self.fake.addresses["doctor"],
            scope=Scope.VACCINATION_SCHEDULE, duration_days=30,
        )
        self.original = records.settings_path(self.settings, "vaccination_file")
        self.copy = self.root / "runtime-data" / "tamper" / "vaccination_record.json"
        self.before = self.fingerprint()

    def fingerprint(self):
        stat = self.original.stat()
        return hashlib.sha256(self.original.read_bytes()).hexdigest(), stat.st_mtime_ns, stat.st_size

    def run_demo(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            demo_workflow.demonstrate_tampering(self.settings, self.copy)
        return output.getvalue()

    def test_copy_is_denied_and_the_original_still_verifies(self):
        output = self.run_demo()
        self.assertIn("denied: HASH_MISMATCH", output)
        self.assertIn("original record still matches", output)
        self.assertFalse(self.copy.exists())
        self.assertEqual(self.fingerprint(), self.before)
        request = self.fake.calls_to("request_access")[0]
        self.assertNotIn(request["observed_hash"], (ZERO_HASH, self.commitment))
        self.assertEqual(request["requester"], self.fake.addresses["doctor"])

    def test_copy_is_deleted_when_the_request_fails(self):
        self.fake.failures["request_access"] = ChainUnavailable()
        with self.assertRaises(AssertionError):
            self.run_demo()
        self.assertFalse(self.copy.exists())
        self.assertEqual(self.fingerprint(), self.before)

    def test_without_the_doctor_grant_the_demo_fails_and_cleans_up(self):
        self.fake.revoke_consent(
            "ConsentManager", guardian=self.fake.addresses["guardian"], requester=self.fake.addresses["doctor"],
            scope=Scope.VACCINATION_SCHEDULE,
        )
        with self.assertRaises(AssertionError):
            self.run_demo()
        self.assertFalse(self.copy.exists())

    def test_relative_copy_path_is_taken_from_the_project_root(self):
        # run from another folder, as the settings paths are always relative to the project root
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(elsewhere.name)
        self.copy = Path("runtime-data") / "tamper" / "vaccination_record.json"
        with mock.patch.object(records, "PROJECT_ROOT", self.root):
            output = self.run_demo()
        self.assertIn("denied: HASH_MISMATCH", output)
        self.assertFalse((self.root / self.copy).exists())
        self.assertEqual(os.listdir(elsewhere.name), [])

    def test_copy_outside_the_data_root_is_refused(self):
        self.copy = self.root / "tamper.json"
        with self.assertRaises(AssertionError):
            self.run_demo()
        self.assertFalse(self.copy.exists())
        self.assertEqual(self.fake.calls_to("request_access"), [])

    def test_existing_files_are_never_used_as_the_copy(self):
        salt_path = records.settings_path(self.settings, "vaccination_salt_file")
        salt_before = salt_path.read_bytes()
        for target in (self.original, salt_path):
            with self.subTest(target.name):
                self.copy = target
                with self.assertRaises(AssertionError):
                    self.run_demo()
        self.assertEqual(self.fingerprint(), self.before)
        self.assertEqual(salt_path.read_bytes(), salt_before)
        self.assertEqual(self.fake.calls_to("request_access"), [])
