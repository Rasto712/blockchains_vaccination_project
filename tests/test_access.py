"""Tests for perform_access and its two wrappers in app/disclosure.py, run against the fake chain.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
import unittest

from app import disclosure, records
from app.models import (
    Scope, ChainUnavailable, TransactionRejected, TransactionPending,
    OUTCOME_ALLOWED, OUTCOME_DENIED, OUTCOME_UNAVAILABLE, OUTCOME_PENDING,
)
from tests.fake_chain import FakeChain, install, prepare_demo, ZERO_HASH, DAY
from tests.support import temp_runtime

HEALTH_TEXT = ("MMR", "2026-03-12", "ABC123-DEMO", "Clinic A", "child-demo-001", "measles")


class AccessTestCase(unittest.TestCase):
    def setUp(self):
        self.root, self.settings = temp_runtime(self)
        self.fake = FakeChain(self.settings)
        install(self, self.fake)
        self.commitment = prepare_demo(self.fake, self.settings)
        self.guardian = self.fake.addresses["guardian"]
        self.school = self.fake.addresses["school"]
        self.doctor = self.fake.addresses["doctor"]

    def grant(self, requester, scope, days=30):
        self.fake.grant_consent("ConsentManager", guardian=self.guardian, requester=requester, scope=scope, duration_days=days)
        self.fake.calls.clear()

    def school_check(self, owner=None):
        return disclosure.perform_access(self.settings, "school", owner or self.guardian, Scope.MEASLES_STATUS)

    def assert_nothing_released(self, response):
        self.assertEqual(response["fields"], {})
        for value in HEALTH_TEXT:
            self.assertNotIn(value, repr(response))

    def sent_hashes(self):
        return [call["observed_hash"] for call in self.fake.calls_to("request_access")]


class ReleaseRuleTests(AccessTestCase):
    def test_no_consent_is_denied_with_one_request(self):
        response = self.school_check()
        self.assertEqual(response["outcome"], OUTCOME_DENIED)
        self.assertEqual(response["reason"], "NO_CONSENT")
        self.assertEqual(response["transaction_hash"], self.fake.events[-1]["transaction_hash"])
        self.assert_nothing_released(response)
        self.assertEqual(self.sent_hashes(), [self.commitment])

    def test_consent_gives_exactly_the_school_view(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        response = self.school_check()
        self.assertEqual(response, {
            "outcome": OUTCOME_ALLOWED,
            "fields": {"measles_status": "verified"},
            "reason": "ALLOWED",
            "transaction_hash": self.fake.events[-1]["transaction_hash"],
        })
        self.assertEqual(self.sent_hashes(), [self.commitment])
        self.assertEqual(len(self.fake.calls_to("get_user_info")), 1)
        self.assertEqual(len(self.fake.calls_to("check_access")), 1)

    def test_consent_gives_exactly_the_doctor_view(self):
        self.grant(self.doctor, Scope.VACCINATION_SCHEDULE)
        response = disclosure.perform_access(self.settings, "doctor", self.guardian, Scope.VACCINATION_SCHEDULE)
        self.assertEqual(response["outcome"], OUTCOME_ALLOWED)
        self.assertEqual(response["fields"], {"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]})

    def test_consent_for_one_scope_does_not_open_the_other(self):
        self.grant(self.doctor, Scope.MEASLES_STATUS)
        response = disclosure.perform_access(self.settings, "doctor", self.guardian, Scope.VACCINATION_SCHEDULE)
        self.assertEqual((response["outcome"], response["reason"]), (OUTCOME_DENIED, "NO_CONSENT"))
        self.assert_nothing_released(response)

    def test_deleted_record_sends_the_zero_hash_and_is_unavailable(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        records.settings_path(self.settings, "vaccination_file").unlink()
        response = self.school_check()
        self.assertEqual(response["outcome"], OUTCOME_UNAVAILABLE)
        # the event itself says HASH_MISMATCH, but Python sent the zero hash, so it is never a clinical answer
        self.assertEqual(response["reason"], "HASH_MISMATCH")
        self.assertEqual(self.sent_hashes(), [ZERO_HASH])
        self.assert_nothing_released(response)

    def test_missing_salt_sends_the_zero_hash(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        records.settings_path(self.settings, "vaccination_salt_file").unlink()
        response = self.school_check()
        self.assertEqual(response["outcome"], OUTCOME_UNAVAILABLE)
        self.assertEqual(self.sent_hashes(), [ZERO_HASH])

    def test_invalid_record_sends_the_zero_hash(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        path = records.settings_path(self.settings, "vaccination_file")
        path.write_bytes(path.read_bytes()[:100])
        response = self.school_check()
        self.assertEqual(response["outcome"], OUTCOME_UNAVAILABLE)
        self.assertEqual(self.sent_hashes(), [ZERO_HASH])

    def test_other_owner_sends_the_zero_hash(self):
        response = self.school_check(owner=self.doctor)
        self.assertEqual(response["outcome"], OUTCOME_UNAVAILABLE)
        self.assertEqual(self.sent_hashes(), [ZERO_HASH])
        self.assert_nothing_released(response)

    def test_owner_address_case_does_not_matter(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        response = self.school_check(owner=self.guardian.upper().replace("0X", "0x"))
        self.assertEqual(response["outcome"], OUTCOME_ALLOWED)

    def test_changed_record_is_hash_mismatch(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        path = records.settings_path(self.settings, "vaccination_file")
        path.write_bytes(path.read_bytes().replace(b"ABC123", b"ABC124"))
        response = self.school_check()
        self.assertEqual((response["outcome"], response["reason"]), (OUTCOME_DENIED, "HASH_MISMATCH"))
        self.assert_nothing_released(response)
        self.assertNotIn(ZERO_HASH, self.sent_hashes())

    def test_revoke_between_the_two_checks_is_denied_with_two_requests(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        self.fake.before["check_access"] = lambda: self.fake.revoke_consent(
            "ConsentManager", guardian=self.guardian, requester=self.school, scope=Scope.MEASLES_STATUS)
        response = self.school_check()
        self.assertEqual((response["outcome"], response["reason"]), (OUTCOME_DENIED, "REVOKED"))
        self.assertEqual(len(self.sent_hashes()), 2)
        self.assertEqual(response["transaction_hash"], self.fake.events[-1]["transaction_hash"])
        self.assertEqual([event["allowed"] for event in self.fake.events], [True, False])
        self.assert_nothing_released(response)

    def test_view_comes_from_the_checked_snapshot(self):
        # change the file after the commitment check; the released view must still come from the checked bytes
        self.grant(self.doctor, Scope.VACCINATION_SCHEDULE)
        path = records.settings_path(self.settings, "vaccination_file")
        self.fake.before["check_access"] = lambda: path.write_bytes(path.read_bytes().replace(b"2026-03-12", b"2025-01-01"))
        response = disclosure.get_doctor_schedule(self.settings, self.guardian)
        self.assertEqual(response["fields"], {"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]})

    def test_revoked_and_expired(self):
        self.grant(self.school, Scope.MEASLES_STATUS, days=1)
        self.fake.now += DAY
        self.assertEqual(self.school_check()["reason"], "EXPIRED")
        self.fake.revoke_consent("ConsentManager", guardian=self.guardian, requester=self.school, scope=Scope.MEASLES_STATUS)
        self.assertEqual(self.school_check()["reason"], "REVOKED")

    def test_registered_commitment_that_differs_is_unavailable(self):
        self.grant(self.school, Scope.MEASLES_STATUS)

        def change_registered_hash():
            self.fake.users[self.guardian.lower()]["vaccination_hash"] = b"\x01" * 32

        self.fake.before["get_user_info"] = change_registered_hash
        response = self.school_check()
        self.assertEqual(response["outcome"], OUTCOME_UNAVAILABLE)
        self.assert_nothing_released(response)
        self.assertEqual(self.fake.calls_to("check_access"), [])

    def test_unsupported_scope_is_logged_and_denied(self):
        response = disclosure.perform_access(self.settings, "school", self.guardian, 3)
        self.assertEqual((response["outcome"], response["reason"]), (OUTCOME_DENIED, "UNSUPPORTED_SCOPE"))
        self.assertEqual(len(self.sent_hashes()), 1)


class FailureTests(AccessTestCase):
    def test_unknown_label_is_unavailable_with_no_chain_call(self):
        for label in ("teacher", "", "School"):
            with self.subTest(label):
                response = disclosure.perform_access(self.settings, label, self.guardian, Scope.MEASLES_STATUS)
                self.assertEqual(response, {"outcome": OUTCOME_UNAVAILABLE, "fields": {}, "reason": "", "transaction_hash": ""})
        self.assertEqual(self.fake.calls, [])

    def test_chain_errors_become_unavailable_or_pending(self):
        cases = (
            ("connect", ChainUnavailable(), OUTCOME_UNAVAILABLE),
            ("request_access", ChainUnavailable(), OUTCOME_UNAVAILABLE),
            ("request_access", TransactionRejected("SomeError"), OUTCOME_UNAVAILABLE),
            ("request_access", TransactionPending(), OUTCOME_PENDING),
            ("get_user_info", ChainUnavailable(), OUTCOME_UNAVAILABLE),
            ("check_access", ChainUnavailable(), OUTCOME_UNAVAILABLE),
        )
        self.grant(self.school, Scope.MEASLES_STATUS)
        for name, error, outcome in cases:
            with self.subTest(name=name, error=type(error).__name__):
                self.fake.failures[name] = error
                response = self.school_check()
                self.assertEqual(response["outcome"], outcome)
                self.assert_nothing_released(response)

    def test_late_request_failing_still_releases_nothing(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        self.fake.before["check_access"] = lambda: self.fake.revoke_consent(
            "ConsentManager", guardian=self.guardian, requester=self.school, scope=Scope.MEASLES_STATUS)

        def fail_second_request():
            self.fake.failures["request_access"] = TransactionPending()

        self.fake.before["revoke_consent"] = fail_second_request
        response = self.school_check()
        self.assertEqual(response["outcome"], OUTCOME_PENDING)
        self.assert_nothing_released(response)

    def test_not_implemented_is_passed_on(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        self.fake.failures["check_access"] = NotImplementedError("check_access is an implementation task")
        with self.assertRaises(NotImplementedError):
            self.school_check()


class WrapperTests(AccessTestCase):
    def test_school_wrapper_uses_the_school_account_and_status_scope(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        response = disclosure.verify_for_school(self.settings, self.guardian)
        self.assertEqual(response["fields"], {"measles_status": "verified"})
        request = self.fake.calls_to("request_access")[0]
        self.assertEqual((request["requester"], request["owner"], request["scope"]), (self.school, self.guardian, Scope.MEASLES_STATUS))

    def test_doctor_wrapper_uses_the_doctor_account_and_schedule_scope(self):
        self.grant(self.doctor, Scope.VACCINATION_SCHEDULE)
        response = disclosure.get_doctor_schedule(self.settings, self.guardian)
        self.assertEqual(response["fields"], {"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]})
        request = self.fake.calls_to("request_access")[0]
        self.assertEqual((request["requester"], request["scope"]), (self.doctor, Scope.VACCINATION_SCHEDULE))

    def test_school_consent_does_not_let_the_doctor_in(self):
        self.grant(self.school, Scope.MEASLES_STATUS)
        response = disclosure.get_doctor_schedule(self.settings, self.guardian)
        self.assertEqual((response["outcome"], response["reason"]), (OUTCOME_DENIED, "NO_CONSENT"))
        self.assert_nothing_released(response)
