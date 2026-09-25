"""Tests for app/records.py. Only temporary directories are written to.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import records

# data/examples/vaccination_record.json as a literal, so line endings on disk cannot break the vectors
EXAMPLE_RECORD = (
    b'{\n'
    b'  "child_id": "child-demo-001",\n'
    b'  "vaccinations": [\n'
    b'    {\n'
    b'      "vaccine": "MMR",\n'
    b'      "covers": [\n'
    b'        "measles",\n'
    b'        "mumps",\n'
    b'        "rubella"\n'
    b'      ],\n'
    b'      "date": "2026-03-12",\n'
    b'      "clinic": "Clinic A (demo)",\n'
    b'      "batch": "ABC123-DEMO"\n'
    b'    }\n'
    b'  ]\n'
    b'}\n'
)
EXAMPLE_CARD = {
    "child_id": "child-demo-001",
    "vaccinations": [{
        "vaccine": "MMR",
        "covers": ["measles", "mumps", "rubella"],
        "date": "2026-03-12",
        "clinic": "Clinic A (demo)",
        "batch": "ABC123-DEMO",
    }],
}
TEST_SALT = bytes(range(32))

VACCINATION_VECTOR = "3f2242f3cce59c18d546a104712ece887fdaf0563cd6bd3f4cb5c932cc6ec5b9"
IDENTITY_VECTOR = "5caf6a69e4d774d6b75cafe49a045f514f61ba3b0bf22deef7a5f2a8c7b23e1b"
FLIPPED_VECTOR = "861cbfae01590bc4aef4b283aa9db1bd4ca171613a410ff23f4a5470c34f8c10"
EMPTY_VECTOR = "d37a843965acaac7c67e0a99c16bcd0ee38f2c031cfd40f974bf994023c4dca9"
# identity hashes of the three example files with TEST_SALT
IDENTITY_PREFIXES = {"guardian": "808ddb", "school": "f2a772", "doctor": "24aafe"}

# record values and salt fragments, so a cut or padded salt in a message is caught too
PRIVATE_TEXT = ("child-demo", "ABC123", "2026-03-12", "Clinic A", TEST_SALT[:8].hex(), repr(TEST_SALT[:8])[2:-1])


def record_with(change):
    card = json.loads(EXAMPLE_RECORD)
    change(card)
    return json.dumps(card, indent=2).encode()


def temp_settings(root):
    runtime = root / "runtime-data"
    return {
        "data_root": str(runtime),
        "vaccination_file": str(runtime / "vaccination_record.json"),
        "vaccination_salt_file": str(runtime / "private" / "vaccination_salt.json"),
        "identity_directory": str(runtime / "identities"),
        "identity_salt_directory": str(runtime / "private"),
    }


def salt_file_text(salt):
    return json.dumps({"salt_b64": base64.b64encode(salt).decode()}) + "\n"


class RecordsTestCase(unittest.TestCase):
    def assert_no_data_in(self, error, *extra):
        message = str(error)
        for secret in PRIVATE_TEXT + extra:
            self.assertNotIn(secret, message)
        # a chained decoder or OS error would still carry the document or the path
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def temp_root(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        patcher = mock.patch.object(records, "DATA_ROOT", root / "runtime-data")
        patcher.start()
        self.addCleanup(patcher.stop)
        return root


class GenerateSaltTests(unittest.TestCase):
    def test_salt_is_32_bytes(self):
        salt = records.generate_salt()
        self.assertIsInstance(salt, bytes)
        self.assertEqual(len(salt), 32)

    def test_salts_differ(self):
        self.assertNotEqual(records.generate_salt(), records.generate_salt())

    def test_salt_is_accepted_by_commitment(self):
        digest = records.calculate_commitment(EXAMPLE_RECORD, records.generate_salt(), "VACCINATION")
        self.assertEqual(len(digest), 32)


class CalculateCommitmentTests(RecordsTestCase):
    def test_example_literal_matches_the_file_layout(self):
        self.assertEqual(len(EXAMPLE_RECORD), 269)
        self.assertTrue(EXAMPLE_RECORD.endswith(b"}\n"))

    def test_vaccination_vector(self):
        digest = records.calculate_commitment(EXAMPLE_RECORD, TEST_SALT, "VACCINATION")
        self.assertIsInstance(digest, bytes)
        self.assertEqual(digest.hex(), VACCINATION_VECTOR)

    def test_identity_vector(self):
        digest = records.calculate_commitment(EXAMPLE_RECORD, TEST_SALT, "IDENTITY")
        self.assertEqual(digest.hex(), IDENTITY_VECTOR)

    def test_one_flipped_bit_changes_the_commitment(self):
        tampered = bytearray(EXAMPLE_RECORD)
        tampered[-2] ^= 1
        digest = records.calculate_commitment(bytes(tampered), TEST_SALT, "VACCINATION")
        self.assertEqual(digest.hex(), FLIPPED_VECTOR)

    def test_line_endings_and_whitespace_change_the_commitment(self):
        changed = (
            EXAMPLE_RECORD.replace(b"\n", b"\r\n"),
            EXAMPLE_RECORD[:-1],
            EXAMPLE_RECORD + b"\n",
            EXAMPLE_RECORD.replace(b": ", b":  ", 1),
        )
        for case, raw in enumerate(changed):
            with self.subTest(case=case):
                digest = records.calculate_commitment(raw, TEST_SALT, "VACCINATION")
                self.assertNotEqual(digest.hex(), VACCINATION_VECTOR)

    def test_empty_bytes_with_zero_salt(self):
        digest = records.calculate_commitment(b"", bytes(32), "VACCINATION")
        self.assertEqual(digest.hex(), EMPTY_VECTOR)

    def test_other_purposes_are_rejected(self):
        for purpose in ("vaccination", "Identity", "VACCINATION:v1", "VACCINATION\n", "", "SCHOOL", None, b"VACCINATION"):
            with self.subTest(purpose=purpose):
                with self.assertRaises(ValueError) as caught:
                    records.calculate_commitment(EXAMPLE_RECORD, TEST_SALT, purpose)
                self.assert_no_data_in(caught.exception)

    def test_wrong_salt_is_rejected(self):
        for salt in (b"", TEST_SALT[:16], TEST_SALT[:31], TEST_SALT + b"!", bytearray(TEST_SALT), TEST_SALT.hex()[:32], None):
            with self.subTest(salt=salt):
                with self.assertRaises(ValueError) as caught:
                    records.calculate_commitment(EXAMPLE_RECORD, salt, "VACCINATION")
                self.assert_no_data_in(caught.exception)

    def test_record_must_be_bytes(self):
        with self.assertRaises(TypeError) as caught:
            records.calculate_commitment(EXAMPLE_RECORD.decode(), TEST_SALT, "VACCINATION")
        self.assert_no_data_in(caught.exception)


class ParseRecordTests(RecordsTestCase):
    def test_example_parses(self):
        self.assertEqual(records.parse_record(EXAMPLE_RECORD), EXAMPLE_CARD)

    def test_bad_fields_are_rejected(self):
        def event(card):
            return card["vaccinations"][0]

        cases = {
            "missing batch": lambda card: event(card).pop("batch"),
            "empty child_id": lambda card: card.update(child_id=""),
            "blank child_id": lambda card: card.update(child_id="   "),
            "extra top key": lambda card: card.update(name="x"),
            "extra event key": lambda card: event(card).update(notes="x"),
            "no vaccinations": lambda card: card.update(vaccinations=[]),
            "two vaccinations": lambda card: card["vaccinations"].append(dict(event(card))),
            "vaccinations not a list": lambda card: card.update(vaccinations=event(card)),
            "other vaccine": lambda card: event(card).update(vaccine="Polio"),
            "lowercase vaccine": lambda card: event(card).update(vaccine="mmr"),
            "empty covers": lambda card: event(card).update(covers=[]),
            "covers without measles": lambda card: event(card).update(covers=["mumps", "rubella"]),
            "covers as text": lambda card: event(card).update(covers="measles"),
            "blank cover": lambda card: event(card)["covers"].append(""),
            "empty clinic": lambda card: event(card).update(clinic=""),
            "batch not text": lambda card: event(card).update(batch=123),
        }
        for name, change in cases.items():
            with self.subTest(name):
                with self.assertRaises(records.RecordError) as caught:
                    records.parse_record(record_with(change))
                self.assert_no_data_in(caught.exception)

    def test_bad_dates_are_rejected(self):
        for bad_date in ("2026-13-40", "20260312", "2026-02-30", "2026-W11-4", "2026-3-12", "2026-03-12T00:00", "٢٠٢٦-03-12"):
            with self.subTest(bad_date):
                raw = record_with(lambda card: card["vaccinations"][0].update(date=bad_date))
                with self.assertRaises(records.RecordError) as caught:
                    records.parse_record(raw)
                self.assert_no_data_in(caught.exception, bad_date)

    def test_bad_bytes_are_rejected(self):
        cases = {
            "invalid utf-8": EXAMPLE_RECORD.replace(b"ABC123", b"ABC\xff23"),
            "empty": b"",
            "truncated": EXAMPLE_RECORD[:150],
            "byte order mark": b"\xef\xbb\xbf" + EXAMPLE_RECORD,
            "top level list": b"[]",
            "null": b"null",
            "repeated key": EXAMPLE_RECORD.replace(b'"vaccinations"', b'"child_id": "child-demo-002",\n  "vaccinations"'),
            "deep nesting": b"[" * 100000,
        }
        for name, raw in cases.items():
            with self.subTest(name):
                with self.assertRaises(records.RecordError) as caught:
                    records.parse_record(raw)
                self.assert_no_data_in(caught.exception)

    def test_text_is_not_accepted(self):
        with self.assertRaises(TypeError):
            records.parse_record(EXAMPLE_RECORD.decode())


class ReadAndSaveRecordTests(RecordsTestCase):
    def setUp(self):
        self.root = self.temp_root()
        self.path = self.root / "runtime-data" / "vaccination_record.json"

    def test_saving_the_example_card_gives_the_example_bytes(self):
        records.save_record(self.path, EXAMPLE_CARD)
        self.assertEqual(self.path.read_bytes(), EXAMPLE_RECORD)
        self.assertEqual(records.read_record_bytes(self.path), EXAMPLE_RECORD)

    def test_second_save_fails_and_leaves_the_file(self):
        records.save_record(self.path, EXAMPLE_CARD)
        changed = json.loads(EXAMPLE_RECORD)
        changed["vaccinations"][0]["batch"] = "XYZ999-DEMO"
        with self.assertRaises(records.RecordError) as caught:
            records.save_record(self.path, changed)
        self.assert_no_data_in(caught.exception, str(self.path))
        self.assertEqual(self.path.read_bytes(), EXAMPLE_RECORD)

    def test_path_outside_the_data_root_fails(self):
        for path in (self.root / "elsewhere" / "card.json", self.root / "runtime-data" / ".." / "card.json", self.root / "runtime-data"):
            with self.subTest(str(path)):
                with self.assertRaises(records.RecordError) as caught:
                    records.save_record(path, EXAMPLE_CARD)
                self.assert_no_data_in(caught.exception, str(path))
        self.assertFalse((self.root / "elsewhere").exists())
        self.assertFalse((self.root / "card.json").exists())

    def test_invalid_card_is_not_written(self):
        card = json.loads(EXAMPLE_RECORD)
        del card["vaccinations"][0]["batch"]
        with self.assertRaises(records.RecordError) as caught:
            records.save_record(self.path, card)
        self.assert_no_data_in(caught.exception)
        self.assertFalse(self.path.exists())

    def test_card_that_cannot_be_serialised_is_not_written(self):
        card = json.loads(EXAMPLE_RECORD)
        card["vaccinations"][0]["covers"] = {"measles"}
        with self.assertRaises(records.RecordError):
            records.save_record(self.path, card)
        self.assertFalse(self.path.exists())

    def test_missing_file_is_unavailable_without_the_path(self):
        with self.assertRaises(records.RecordError) as caught:
            records.read_record_bytes(self.path)
        self.assertEqual(str(caught.exception), "local record unavailable")
        self.assert_no_data_in(caught.exception, str(self.path))

    def test_folder_is_unavailable(self):
        with self.assertRaises(records.RecordError) as caught:
            records.read_record_bytes(self.root)
        self.assertEqual(str(caught.exception), "local record unavailable")


class SaltFileTests(RecordsTestCase):
    def setUp(self):
        self.root = self.temp_root()
        self.path = self.root / "runtime-data" / "private" / "vaccination_salt.json"

    def test_round_trip_and_format(self):
        records.save_salt(self.path, TEST_SALT)
        self.assertEqual(self.path.read_text(), salt_file_text(TEST_SALT))
        self.assertEqual(len(json.loads(self.path.read_text())["salt_b64"]), 44)
        self.assertEqual(records.load_salt(self.path), TEST_SALT)

    def test_wrong_salt_is_not_saved(self):
        for salt in (TEST_SALT[:16], TEST_SALT[:31], TEST_SALT + b"!", bytearray(TEST_SALT), TEST_SALT.hex()):
            with self.subTest(salt=salt):
                with self.assertRaises(records.RecordError) as caught:
                    records.save_salt(self.path, salt)
                self.assert_no_data_in(caught.exception)
                self.assertFalse(self.path.exists())

    def test_saving_twice_fails_and_keeps_the_first_salt(self):
        records.save_salt(self.path, TEST_SALT)
        with self.assertRaises(records.RecordError) as caught:
            records.save_salt(self.path, bytes(32))
        self.assert_no_data_in(caught.exception, str(self.path))
        self.assertEqual(records.load_salt(self.path), TEST_SALT)

    def test_bad_salt_files_are_unavailable(self):
        cases = {
            "31 bytes": salt_file_text(TEST_SALT[:31]),
            "33 bytes": salt_file_text(TEST_SALT + b"!"),
            "empty salt": salt_file_text(b""),
            "invalid base64": '{"salt_b64": "not base64 at all!"}',
            "base64 with a newline": json.dumps({"salt_b64": base64.encodebytes(TEST_SALT).decode()}),
            "raw hex": json.dumps({"salt_b64": TEST_SALT.hex()}),
            "wrong key": json.dumps({"salt": base64.b64encode(TEST_SALT).decode()}),
            "extra key": json.dumps({"salt_b64": base64.b64encode(TEST_SALT).decode(), "note": "x"}),
            "number": '{"salt_b64": 5}',
            "not json": base64.b64encode(TEST_SALT).decode(),
            "empty file": "",
        }
        for name, text in cases.items():
            with self.subTest(name):
                path = self.root / f"{name}.json"
                path.write_bytes(text.encode())
                with self.assertRaises(records.RecordError) as caught:
                    records.load_salt(path)
                self.assertEqual(str(caught.exception), "salt unavailable")
                self.assert_no_data_in(caught.exception, str(path))

    def test_missing_salt_is_unavailable(self):
        with self.assertRaises(records.RecordError) as caught:
            records.load_salt(self.path)
        self.assertEqual(str(caught.exception), "salt unavailable")
        self.assert_no_data_in(caught.exception, str(self.path))


class SnapshotTests(RecordsTestCase):
    def setUp(self):
        self.root = self.temp_root()
        self.record_path = self.root / "record.json"
        self.salt_path = self.root / "salt.json"
        self.record_path.write_bytes(EXAMPLE_RECORD)
        self.salt_path.write_text(salt_file_text(TEST_SALT))

    def test_snapshot_matches_the_vector(self):
        snapshot = records.load_snapshot(self.record_path, self.salt_path)
        self.assertEqual(snapshot["commitment"].hex(), VACCINATION_VECTOR)
        self.assertEqual(snapshot["raw_bytes"], EXAMPLE_RECORD)
        self.assertEqual(snapshot["card"], EXAMPLE_CARD)
        self.assertEqual(snapshot["salt"], TEST_SALT)

    def test_record_file_is_read_once(self):
        # swap the file straight after the first read, so any second read by any means sees other bytes
        real_read = records.read_record_bytes
        reads = []

        def read_then_swap(path):
            data = real_read(path)
            self.record_path.write_bytes(EXAMPLE_RECORD.replace(b"ABC123", b"EVIL99"))
            reads.append(path)
            return data

        with mock.patch.object(records, "read_record_bytes", side_effect=read_then_swap):
            snapshot = records.load_snapshot(self.record_path, self.salt_path)
        self.assertEqual(len(reads), 1)
        self.assertEqual(snapshot["raw_bytes"], EXAMPLE_RECORD)
        self.assertEqual(snapshot["card"], EXAMPLE_CARD)
        self.assertEqual(snapshot["commitment"].hex(), VACCINATION_VECTOR)

    def test_changing_the_file_later_does_not_change_the_snapshot(self):
        snapshot = records.load_snapshot(self.record_path, self.salt_path)
        self.record_path.write_bytes(EXAMPLE_RECORD.replace(b"ABC123", b"ABC124"))
        self.assertEqual(snapshot["raw_bytes"], EXAMPLE_RECORD)
        self.assertEqual(snapshot["card"], EXAMPLE_CARD)
        self.assertEqual(snapshot["commitment"].hex(), VACCINATION_VECTOR)

    def test_tampered_copy_gives_another_commitment(self):
        self.record_path.write_bytes(EXAMPLE_RECORD.replace(b"ABC123", b"ABC124"))
        snapshot = records.load_snapshot(self.record_path, self.salt_path)
        self.assertNotEqual(snapshot["commitment"].hex(), VACCINATION_VECTOR)

    def test_layout_changes_give_another_commitment(self):
        # same card, different bytes, so the hash must be over the file and not a re-serialised card
        layouts = (
            EXAMPLE_RECORD.replace(b"\n", b"\r\n"),
            EXAMPLE_RECORD + b"\n",
            json.dumps(EXAMPLE_CARD, indent=4).encode() + b"\n",
        )
        for case, raw in enumerate(layouts):
            with self.subTest(case=case):
                self.record_path.write_bytes(raw)
                snapshot = records.load_snapshot(self.record_path, self.salt_path)
                self.assertEqual(snapshot["card"], EXAMPLE_CARD)
                self.assertEqual(snapshot["commitment"], records.calculate_commitment(raw, TEST_SALT, "VACCINATION"))
                self.assertNotEqual(snapshot["commitment"].hex(), VACCINATION_VECTOR)

    def test_missing_or_bad_parts_fail(self):
        missing = self.root / "missing.json"
        bad_record = self.root / "bad.json"
        bad_record.write_bytes(EXAMPLE_RECORD[:150])
        for record_path, salt_path in ((missing, self.salt_path), (bad_record, self.salt_path), (self.record_path, missing)):
            with self.subTest(record=record_path.name, salt=salt_path.name):
                with self.assertRaises(records.RecordError) as caught:
                    records.load_snapshot(record_path, salt_path)
                self.assert_no_data_in(caught.exception, str(record_path), str(salt_path))


class IdentityTests(RecordsTestCase):
    def setUp(self):
        self.root = self.temp_root()
        self.settings = temp_settings(self.root)
        records.setup_runtime(self.settings)
        self.fixed_salt = self.root / "fixed_salt.json"
        self.fixed_salt.write_text(salt_file_text(TEST_SALT))

    def test_labels_give_the_expected_different_hashes(self):
        hashes = {}
        for label, prefix in IDENTITY_PREFIXES.items():
            identity_path, _ = records.identity_paths(self.settings, label)
            hashes[label] = records.prepare_identity(identity_path, self.fixed_salt)
            self.assertEqual(len(hashes[label]), 32)
            self.assertTrue(hashes[label].hex().startswith(prefix), label)
        self.assertEqual(len(set(hashes.values())), 3)

    def test_same_file_and_salt_give_the_same_hash(self):
        identity_path, salt_path = records.identity_paths(self.settings, "guardian")
        first = records.prepare_identity(identity_path, salt_path)
        self.assertEqual(records.prepare_identity(identity_path, salt_path), first)

    def test_bad_identities_are_rejected(self):
        cases = {
            "missing email": {"unique_id": "guardian-demo-001"},
            "extra key": {"unique_id": "guardian-demo-001", "email": "guardian@example.test", "name": "x"},
            "empty unique_id": {"unique_id": "", "email": "guardian@example.test"},
            "email not text": {"unique_id": "guardian-demo-001", "email": 5},
            "list": ["guardian-demo-001"],
        }
        for name, identity in cases.items():
            with self.subTest(name):
                path = self.root / f"{name}.json"
                path.write_text(json.dumps(identity))
                with self.assertRaises(records.RecordError) as caught:
                    records.prepare_identity(path, self.fixed_salt)
                self.assert_no_data_in(caught.exception, "guardian-demo-001", "guardian@example.test")

    def test_identity_must_be_utf8(self):
        path = self.root / "latin.json"
        path.write_bytes(b'{"unique_id": "guardian-demo-\xff01", "email": "guardian@example.test"}')
        with self.assertRaises(records.RecordError) as caught:
            records.prepare_identity(path, self.fixed_salt)
        self.assert_no_data_in(caught.exception, "guardian-demo", "guardian@example.test")

    def test_identity_line_endings_change_the_hash(self):
        identity_path, _ = records.identity_paths(self.settings, "guardian")
        crlf_path = self.root / "guardian_crlf.json"
        crlf_path.write_bytes(identity_path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertNotEqual(
            records.prepare_identity(crlf_path, self.fixed_salt),
            records.prepare_identity(identity_path, self.fixed_salt),
        )

    def test_missing_identity_is_unavailable(self):
        path = self.root / "nobody.json"
        with self.assertRaises(records.RecordError) as caught:
            records.prepare_identity(path, self.fixed_salt)
        self.assertEqual(str(caught.exception), "identity unavailable")
        self.assert_no_data_in(caught.exception, str(path))

    def test_only_registering_labels_have_identity_paths(self):
        for label in ("deployer", "clinic", "../guardian", "Guardian", ""):
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    records.identity_paths(self.settings, label)


class SetupRuntimeTests(RecordsTestCase):
    def setUp(self):
        self.root = self.temp_root()
        self.settings = temp_settings(self.root)

    def runtime_files(self):
        return {path: path.read_bytes() for path in (self.root / "runtime-data").rglob("*") if path.is_file()}

    def test_first_run_creates_every_file(self):
        created = records.setup_runtime(self.settings)
        self.assertEqual(len(created), 8)
        self.assertEqual(set(created), set(self.runtime_files()))
        record_path = records.settings_path(self.settings, "vaccination_file")
        self.assertEqual(record_path.read_bytes(), EXAMPLE_RECORD)
        salts = {records.load_salt(records.settings_path(self.settings, "vaccination_salt_file"))}
        for label in records.REGISTERING_LABELS:
            identity_path, salt_path = records.identity_paths(self.settings, label)
            example = records.EXAMPLES_DIR / "identities" / f"{label}.json"
            self.assertEqual(identity_path.read_bytes(), example.read_bytes())
            salts.add(records.load_salt(salt_path))
        self.assertEqual(len(salts), 4)

    def test_second_run_changes_nothing(self):
        records.setup_runtime(self.settings)
        before = self.runtime_files()
        self.assertEqual(records.setup_runtime(self.settings), [])
        self.assertEqual(self.runtime_files(), before)

    def test_only_missing_files_are_made_again(self):
        records.setup_runtime(self.settings)
        before = self.runtime_files()
        _, school_salt = records.identity_paths(self.settings, "school")
        school_salt.unlink()
        self.assertEqual(records.setup_runtime(self.settings), [school_salt])
        after = self.runtime_files()
        for path, content in before.items():
            if path != school_salt:
                self.assertEqual(after[path], content)
