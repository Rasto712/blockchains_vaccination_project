"""Tests for the disclosure views and denial messages in app/disclosure.py.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import copy
import unittest

from app import disclosure
from app.models import Reason, OUTCOME_ALLOWED, OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING

CARD = {
    "child_id": "child-demo-001",
    "vaccinations": [{
        "vaccine": "MMR",
        "covers": ["measles", "mumps", "rubella"],
        "date": "2026-03-12",
        "clinic": "Clinic A (demo)",
        "batch": "ABC123-DEMO",
    }],
}
HEALTH_TEXT = ("MMR", "2026-03-12", "ABC123-DEMO", "Clinic A", "child-demo-001", "measles")
TX_HASH = "0x" + "ab" * 32


class SchoolViewTests(unittest.TestCase):
    def test_status_only(self):
        self.assertEqual(disclosure.select_school_status(copy.deepcopy(CARD)), {"measles_status": "verified"})

    def test_changing_the_result_leaves_the_card(self):
        card = copy.deepcopy(CARD)
        disclosure.select_school_status(card)["measles_status"] = "changed"
        self.assertEqual(card, CARD)
        self.assertEqual(disclosure.select_school_status(card), {"measles_status": "verified"})

    def test_no_measles_cover_is_not_verified(self):
        card = copy.deepcopy(CARD)
        card["vaccinations"][0]["covers"] = ["mumps", "rubella"]
        with self.assertRaises(ValueError):
            disclosure.select_school_status(card)


class DoctorViewTests(unittest.TestCase):
    def test_vaccine_and_date_only(self):
        self.assertEqual(
            disclosure.select_doctor_schedule(copy.deepcopy(CARD)),
            {"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]},
        )

    def test_changing_the_result_leaves_the_card(self):
        card = copy.deepcopy(CARD)
        schedule = disclosure.select_doctor_schedule(card)
        schedule["vaccinations"][0]["vaccine"] = "changed"
        schedule["vaccinations"].append({"vaccine": "extra", "date": "extra"})
        schedule["child_id"] = "changed"
        self.assertEqual(card, CARD)


class FormatDenialTests(unittest.TestCase):
    def test_every_non_allowed_outcome_has_empty_fields(self):
        for outcome in (OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING):
            with self.subTest(outcome):
                response = disclosure.format_denial(outcome, "NO_CONSENT", TX_HASH)
                self.assertEqual(response, {"outcome": outcome, "fields": {}, "reason": "NO_CONSENT", "transaction_hash": TX_HASH})

    def test_defaults_are_empty(self):
        self.assertEqual(
            disclosure.format_denial(OUTCOME_PENDING),
            {"outcome": OUTCOME_PENDING, "fields": {}, "reason": "", "transaction_hash": ""},
        )

    def test_reason_member_becomes_its_name(self):
        self.assertEqual(disclosure.format_denial(OUTCOME_DENIED, Reason.REVOKED, TX_HASH)["reason"], "REVOKED")

    def test_reason_code_becomes_its_name(self):
        # web3 decodes the Reason enum in an event as a plain int
        self.assertEqual(disclosure.format_denial(OUTCOME_DENIED, 7, TX_HASH)["reason"], "HASH_MISMATCH")
        with self.assertRaises(ValueError):
            disclosure.format_denial(OUTCOME_DENIED, 8, TX_HASH)

    def test_allowed_and_unknown_values_are_refused(self):
        for outcome, reason in ((OUTCOME_ALLOWED, ""), ("unimplemented", ""), (OUTCOME_DENIED, "revoked"), (OUTCOME_DENIED, "7")):
            with self.subTest(outcome=outcome, reason=reason):
                with self.assertRaises(ValueError):
                    disclosure.format_denial(outcome, reason)


class DenialMessageTests(unittest.TestCase):
    def test_denied_names_the_reason_and_transaction(self):
        response = disclosure.format_denial(OUTCOME_DENIED, "REVOKED", TX_HASH)
        self.assertEqual(disclosure.denial_message(response), f"denied: REVOKED (logged on-chain in {TX_HASH})")

    def test_unavailable_ignores_the_event_reason(self):
        # covers the zero hash that Python sends itself when the local record fails
        for reason in ("", "HASH_MISMATCH", "NO_CONSENT", "MISSING_EVIDENCE"):
            with self.subTest(reason=reason):
                response = disclosure.format_denial(OUTCOME_UNAVAILABLE, reason, TX_HASH)
                self.assertEqual(
                    disclosure.denial_message(response),
                    "unavailable: local record could not be verified, not a clinical result",
                )

    def test_pending(self):
        response = disclosure.format_denial(OUTCOME_PENDING)
        self.assertEqual(disclosure.denial_message(response), "pending: not confirmed, nothing released")

    def test_allowed_has_no_denial_message(self):
        allowed = {"outcome": OUTCOME_ALLOWED, "fields": {"measles_status": "verified"}, "reason": "ALLOWED", "transaction_hash": TX_HASH}
        with self.assertRaises(ValueError):
            disclosure.denial_message(allowed)

    def test_not_implemented_is_a_message_not_an_outcome(self):
        self.assertEqual(disclosure.NOT_IMPLEMENTED_MESSAGE, "not implemented yet")
        with self.assertRaises(ValueError):
            disclosure.format_denial(disclosure.NOT_IMPLEMENTED_MESSAGE)

    def test_no_health_values_in_any_denial(self):
        for outcome in (OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING):
            for reason in [""] + [reason.name for reason in Reason]:
                with self.subTest(outcome=outcome, reason=reason):
                    response = disclosure.format_denial(outcome, reason, TX_HASH)
                    self.assertEqual(response["fields"], {})
                    text = repr(response) + disclosure.denial_message(response)
                    for value in HEALTH_TEXT:
                        self.assertNotIn(value, text)
