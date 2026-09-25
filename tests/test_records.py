"""Tests for app/records.py. Only temporary directories are written to.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import unittest

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
TEST_SALT = bytes(range(32))

VACCINATION_VECTOR = "3f2242f3cce59c18d546a104712ece887fdaf0563cd6bd3f4cb5c932cc6ec5b9"
IDENTITY_VECTOR = "5caf6a69e4d774d6b75cafe49a045f514f61ba3b0bf22deef7a5f2a8c7b23e1b"
FLIPPED_VECTOR = "861cbfae01590bc4aef4b283aa9db1bd4ca171613a410ff23f4a5470c34f8c10"
EMPTY_VECTOR = "d37a843965acaac7c67e0a99c16bcd0ee38f2c031cfd40f974bf994023c4dca9"


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


class CalculateCommitmentTests(unittest.TestCase):
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

    def assert_no_data_in(self, error):
        message = str(error)
        # salt fragments, so a cut or padded salt in the message is caught too
        for secret in ("child-demo", "ABC123", "2026-03-12", "Clinic A", TEST_SALT[:8].hex(), repr(TEST_SALT[:8])[2:-1]):
            self.assertNotIn(secret, message)
        self.assertIsNone(error.__cause__)
