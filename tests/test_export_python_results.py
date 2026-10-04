# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Offline tests for evaluation/export_python_results.py (the parts that need no node)."""
import base64
import contextlib
import csv
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import records
from evaluation import export_python_results as export
from tests.support import temp_runtime


class UnittestSummaryTests(unittest.TestCase):
    def test_ok_skipped_failed_and_missing(self):
        for text, expected in (
            ("....\n------\nRan 4 tests in 0.010s\n\nOK\n", (4, "OK", "")),
            ("Ran 268 tests in 0.2s\n\nOK (skipped=69)\n", (268, "OK", "skipped=69")),
            ("Ran 1 test in 0.1s\n\nFAILED (failures=1, errors=2)\n", (1, "FAILED", "failures=1, errors=2")),
            ("ImportError: no module\n", (None, None, "")),
        ):
            with self.subTest(text=text):
                self.assertEqual(export.parse_unittest_output(text), expected)

    def test_only_the_final_summary_counts(self):
        # a test that prints "OK" itself must not decide the result
        self.assertEqual(export.parse_unittest_output("OK\nRan 2 tests in 1s\n\nFAILED (errors=1)\n")[1], "FAILED")


class FindValuesTests(unittest.TestCase):
    VALUES = [("record batch", b"ABC123-DEMO"), ("a salt", bytes(range(32)))]

    def test_every_encoding_is_found(self):
        for blob, label in (
            (b"\x00\x01ABC123-DEMO\x02", "record batch"),
            (b"xx" + b"ABC123-DEMO".hex().encode(), "record batch"),
            (b"ABC123-DEMO".hex().upper().encode(), "record batch"),
            (base64.b64encode(bytes(range(32))), "a salt"),
            (bytes(12) + bytes(range(32)), "a salt"),
        ):
            with self.subTest(blob=blob):
                self.assertEqual(export.find_values([b"other", blob], self.VALUES), [label])

    def test_absent_values_and_labels_only(self):
        self.assertEqual(export.find_values([b"ABC124-DEMO", bytes(range(31))], self.VALUES), [])
        found = export.find_values([b"ABC123-DEMO" + bytes(range(32))], self.VALUES)
        self.assertEqual(found, ["record batch", "a salt"])


class LeakTests(unittest.TestCase):
    VALUES = [("record child_id", b"child-demo-001")]

    def test_clean_console_lines(self):
        for text in (
            "unavailable: local node not reachable or wrong chain\n",
            "demo FAILED at step start: no settings file: copy config/settings.example.json to config/settings.json\n",
            "unavailable: no deployment for this node: run python -m scripts.deploy_local --reset\n",
            "failed: KeyError\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(export.leaks(text, self.VALUES), [])

    def test_traceback_paths_and_values(self):
        for text, found in (
            ('Traceback (most recent call last):\n  File "x.py"', ["a traceback"]),
            ("failed: [Errno 2] No such file: '/Users/someone/data/salt.json'", ["an absolute path"]),
            ("could not write /private/tmp/x/deployment.json", ["an absolute path"]),
            ("unavailable: /home/a/b", ["an absolute path"]),
            ('record {"child_id": "child-demo-001"}', ["record child_id"]),
        ):
            with self.subTest(text=text):
                self.assertEqual(export.leaks(text, self.VALUES), found)


class PrivateValuesTests(unittest.TestCase):
    def setUp(self):
        _, self.settings = temp_runtime(self)
        records.setup_runtime(self.settings)
        self.values = export.private_values(self.settings)

    def test_record_identities_and_every_salt(self):
        labels = [label for label, _ in self.values]
        for label in (
            "the record file", "the record salt", "record child_id", "record batch", "record clinic", "tampered batch",
            "guardian identity file", "school email", "doctor unique_id", "doctor identity salt",
        ):
            self.assertIn(label, labels)
        values = dict(self.values)
        self.assertEqual(values["record batch"], b"ABC123-DEMO")
        self.assertEqual(len(values["the record salt"]), 32)
        salts = [value for label, value in self.values if label.endswith("salt")]
        self.assertEqual(len(set(salts)), 4)

    def test_labels_never_hold_a_value(self):
        for label, value in self.values:
            with self.subTest(label=label):
                for form in export.encodings(value):
                    self.assertNotIn(form, label.encode())


class StorageTests(unittest.TestCase):
    def test_sstore_slot_and_value(self):
        trace = {"structLogs": [
            {"op": "PUSH1", "stack": []},
            {"op": "SSTORE", "stack": ["0x1", "0x963c67", "0x215b"]},
            {"op": "SSTORE", "stack": ["0x0", "0x2"]},
        ]}
        self.assertEqual(export.storage_writes(trace), [(0x215B, 0x963C67), (2, 0)])
        self.assertEqual(export.storage_writes({}), [])

    def test_classify_word(self):
        local = {bytes(31) + b"\x07"}
        address = int("3C44CdDdB6a900fa2b585dd299e03d12FA4293BC", 16)
        self.assertEqual(export.classify_word(7, local, set()), "local hash")
        self.assertEqual(export.classify_word(address, set(), {address}), "address")
        self.assertEqual(export.classify_word(1_790_714_834, set(), set()), "number")
        self.assertEqual(export.classify_word(int.from_bytes(b"child-demo-001", "big"), set(), set()), "other")

    def test_packed_consent_words(self):
        # ConsentManager's Consent slot: uint64 expiresAt, then the revoked and rewarded bytes
        expires_at = 1_790_714_834
        for revoked, rewarded in ((False, True), (True, True), (True, False)):
            with self.subTest(revoked=revoked, rewarded=rewarded):
                word = expires_at | int(revoked) << 64 | int(rewarded) << 72
                self.assertEqual(export.classify_word(word, set(), set()), "consent")
        # any other byte above the expiry, or a flag byte that is not 0 or 1, is not a consent
        for word in (expires_at | 2 << 64, expires_at | 1 << 80, expires_at | 0x41 << 72):
            with self.subTest(word=hex(word)):
                self.assertEqual(export.classify_word(word, set(), set()), "other")


class PlainTests(unittest.TestCase):
    def client(self, fails=False):
        def decode(types, data):
            if fails:
                raise ValueError("bad")
            return tuple(0 for _ in types)
        return SimpleNamespace(codec=SimpleNamespace(decode=decode))

    def test_plain_types_that_decode(self):
        self.assertEqual(export._plain("x", ["address", "uint8", "bytes32"], bytes(96), self.client()), [])
        self.assertEqual(export._plain("x", [], b"", self.client()), [])

    def test_text_bytes_wrong_length_or_bad_data(self):
        self.assertEqual(export._plain("x", ["string", "bytes"], bytes(64), self.client()), ["x: string", "x: bytes"])
        self.assertEqual(export._plain("x", ["bytes32"], bytes(64), self.client()), ["x: 64 bytes of arguments for 1 words"])
        self.assertEqual(export._plain("x", ["address"], bytes(32), self.client(fails=True)), ["x: arguments do not decode"])


class RowTests(unittest.TestCase):
    def test_ids_and_fixed_text(self):
        self.assertEqual(list(export.ROWS), [f"PY-{number:02d}" for number in range(1, len(export.ROWS) + 1)])
        for test_id, texts in export.ROWS.items():
            with self.subTest(test_id):
                self.assertEqual(len(texts), 3)
                self.assertTrue(all(text.strip() for text in texts))

    def test_status_from_observations(self):
        row = export.Row("PY-01")
        self.assertEqual(row.status, "fail")
        self.assertEqual(row.cells("ctx")[4:6], ["not checked", "fail"])
        row.see("school fields ok", True)
        self.assertEqual(row.status, "pass")
        row.see("doctor fields had clinic", False)
        row.evidence.append("tx 0xab")
        cells = row.cells("command; commit; time")
        self.assertEqual(len(cells), len(export.HEADER))
        self.assertEqual(cells[:4], ["PY-01", *export.ROWS["PY-01"]])
        self.assertEqual(cells[4], "school fields ok; doctor fields had clinic (not as expected)")
        self.assertEqual(cells[5:], ["fail", "tx 0xab; command; commit; time"])

    def test_unknown_id(self):
        with self.assertRaises(ValueError):
            export.Row("PY-99")


class WriteRowsTests(unittest.TestCase):
    def test_template_header_lf_and_quoting(self):
        _, settings = temp_runtime(self)
        path = Path(settings["data_root"]) / "out" / "test_results.csv"
        row = export.Row("PY-02")
        row.see('school: denied NO_CONSENT, fields {}, "quoted"', True)
        export.write_rows(path, [row.cells("ctx")])
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("\r", text)
        template = (records.PROJECT_ROOT / "evaluation" / "templates" / "test_results.csv").read_text().splitlines()[0]
        self.assertEqual(text.splitlines()[0], template)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(rows[0]["actual"], 'school: denied NO_CONSENT, fields {}, "quoted"')
        self.assertEqual(rows[0]["status"], "pass")


class OutsideRepoTests(unittest.TestCase):
    def test_only_a_folder_outside_the_project_counts(self):
        project = records.PROJECT_ROOT
        for path, outside in (
            (project, False), (project / "runtime-data", False), (project / "a" / ".." / "b", False),
            (project.parent / f"{project.name}-data", True), (project / ".." / "elsewhere", True),
        ):
            with self.subTest(path=str(path)):
                self.assertEqual(export._outside_repo(path), outside)


class GitRevisionTests(unittest.TestCase):
    def test_clean_dirty_and_unknown(self):
        for answers, expected in (
            (["a635a30", ""], "a635a30"),
            (["a635a30", " M app/records.py"], "a635a30 + uncommitted changes"),
            (["", ""], "unknown commit"),
        ):
            with self.subTest(expected=expected), mock.patch.object(export, "_git", side_effect=answers):
                self.assertEqual(export.git_revision(), expected)
        with mock.patch.object(export, "_git", side_effect=OSError("no git")):
            self.assertEqual(export.git_revision(), "unknown commit")


class SettingsAndMainTests(unittest.TestCase):
    def setUp(self):
        root, self.settings = temp_runtime(self)
        self.settings.update(rpc_url="http://127.0.0.1:8545", expected_chain_id=31337)
        self.settings_path = root / "settings.json"
        self.output = root / "results" / "test_results.csv"
        # none of these tests may start a subprocess: a check that stopped refusing bad settings would
        # otherwise reach the unit-test step and start this suite again, and again
        patcher = mock.patch.object(export, "_run", side_effect=AssertionError("a subprocess was started"))
        self.subprocess = patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = export.main(["--settings", str(self.settings_path), "--output", str(self.output)])
        return code, output.getvalue(), errors.getvalue()

    def test_bad_settings_write_nothing_and_remove_a_stale_csv(self):
        for content, message in (
            (None, "no settings file"),
            ("{", "not valid JSON"),
            ("[]", "not a settings object"),
            (json.dumps(dict(self.settings, expected_chain_id=1)), "only run on the local Hardhat chain 31337"),
            (json.dumps(dict(self.settings, data_root="")), "invalid setting: data_root"),
        ):
            with self.subTest(message=message):
                if content is not None:
                    self.settings_path.write_text(content)
                self.output.parent.mkdir(parents=True, exist_ok=True)
                self.output.write_text("stale\n")
                code, _, errors = self.run_main()
                self.assertEqual(code, 1)
                self.assertIn(message, errors)
                self.assertIn("CSV not written.", errors)
                self.assertFalse(self.output.exists())

    def test_node_down_writes_nothing(self):
        self.settings_path.write_text(json.dumps(self.settings))
        with mock.patch.object(export.LiveRun, "unit_tests", lambda run: None), \
                mock.patch.object(export.chain, "connect", side_effect=export.ChainUnavailable("RPC connection failed")):
            code, _, errors = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("export_python_results: node: ChainUnavailable: RPC connection failed", errors)
        self.assertFalse(self.output.exists())

    def test_rows_written_and_exit_code_from_their_status(self):
        self.settings_path.write_text(json.dumps(self.settings))
        for failing, expected_code in ((False, 0), (True, 1)):
            with self.subTest(failing=failing):
                def run_checks(settings_path, failing=failing):
                    run = export.LiveRun(settings_path, self.settings)
                    for row in run.rows.values():
                        row.see("observed", not (failing and row.test_id == "PY-07"))
                    return run

                with mock.patch.object(export, "run_checks", run_checks), mock.patch.object(export, "git_revision", lambda: "abc1234"):
                    code, output, _ = self.run_main()
                self.assertEqual(code, expected_code)
                rows = list(csv.DictReader(io.StringIO(self.output.read_text())))
                self.assertEqual([row["test_id"] for row in rows], list(export.ROWS))
                self.assertIn("abc1234", rows[0]["evidence"])
                self.assertIn("data_root outside the repo", rows[0]["evidence"])
                # the options that were given, never their paths
                self.assertIn("python -m evaluation.export_python_results --settings PATH --output PATH (", rows[0]["evidence"])
                self.assertNotIn(str(self.output.parent), rows[0]["evidence"] + output)
                self.assertEqual([row["test_id"] for row in rows if row["status"] == "fail"], ["PY-07"] if failing else [])
                self.assertIn("fail: PY-07" if failing else "13 pass, 0 fail", output)

    def test_evidence_without_options_names_none(self):
        self.settings_path.write_text(json.dumps(self.settings))

        def run_checks(settings_path):
            run = export.LiveRun(settings_path, self.settings)
            for row in run.rows.values():
                row.see("observed", True)
            return run

        with mock.patch.object(export, "run_checks", run_checks), mock.patch.object(export, "git_revision", lambda: "abc1234"), \
                mock.patch.object(export, "SETTINGS_FILE", self.settings_path), mock.patch.object(export, "CSV_PATH", self.output), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(export.main([]), 0)
        rows = list(csv.DictReader(io.StringIO(self.output.read_text())))
        self.assertIn("python -m evaluation.export_python_results (Hardhat node", rows[0]["evidence"])
        self.assertNotIn("--settings", rows[0]["evidence"])

    def test_demo_row_checks_that_every_data_path_is_under_data_root(self):
        demo_output = f"== 0. check the node ==\n...\n{export.DEMO_PASSED}\n"
        for label, changes, passes in (
            ("all under data_root", {}, True),
            ("record elsewhere", {"vaccination_file": str(Path(self.settings["data_root"]).parent / "elsewhere" / "card.json")}, False),
            ("deployment elsewhere", {"deployment_file": str(Path(self.settings["data_root"]).parent / "deployment.json")}, False),
        ):
            with self.subTest(label):
                run = export.LiveRun(self.settings_path, dict(self.settings, **changes))
                self.subprocess.side_effect = None
                self.subprocess.return_value = (0, demo_output, "")
                with mock.patch.object(export.LiveRun, "latest_block", lambda run: 7):
                    run.demo()
                row = run.rows["PY-11"]
                self.assertEqual(row.status, "pass" if passes else "fail", row.observed)
                paths = [text for text in row.observed if "data paths under it" in text]
                self.assertEqual(len(paths), 1)
                self.assertEqual(paths[0].endswith("(not as expected)"), not passes)

    def test_unit_tests_of_an_export_run_cannot_start_another(self):
        run = export.LiveRun(self.settings_path, self.settings)
        with mock.patch.dict(export.os.environ, {export.NESTED_RUN_VARIABLE: "1"}):
            with self.assertRaises(export.ExportError):
                run.unit_tests()
        self.subprocess.assert_not_called()

    def test_unit_tests_are_started_marked_as_nested(self):
        self.subprocess.side_effect = None
        self.subprocess.return_value = (0, "", "Ran 3 tests in 0.1s\n\nOK (skipped=1)\n")
        run = export.LiveRun(self.settings_path, self.settings)
        with mock.patch.dict(export.os.environ, clear=False) as environment:
            environment.pop(export.NESTED_RUN_VARIABLE, None)
            run.unit_tests()
        suites = [call for call in self.subprocess.call_args_list if call.args[0][-5:] == export.UNIT_TESTS]
        self.assertEqual(len(suites), 2)
        for call in suites:
            self.assertEqual(call.args[1][export.NESTED_RUN_VARIABLE], "1")
        self.assertEqual([run.rows[test_id].status for test_id in ("PY-12", "PY-13")], ["pass", "pass"])


if __name__ == "__main__":
    unittest.main()
