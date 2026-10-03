# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Endpoint tests for the web UI's guided demo and demo controls (ui/demo.py), over real HTTP with the fake
node of tests/test_demo.py, as in tests/test_ui.py. The mocked deploy clears the fake's contract state, so
every start of the guided demo gets "fresh contracts" while node time goes on, as on a real node.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

from app import records
from app.models import Reason, ChainUnavailable, DeploymentUnavailable
from integration import demo_workflow
from tests.test_ui import UITestCase

STEP_ROLES = ["deployer", "guardian", "clinic", "school", "guardian", "school", "guardian", "doctor", "doctor",
              "school", "guardian", "school"]


class GuidedTestCase(UITestCase):
    def setUp(self):
        super().setUp()
        self.deploy.side_effect = self.fresh_contracts

    def fresh_contracts(self, *args, **kwargs):
        # new contracts: nothing registered, granted, rewarded or logged; node time is kept
        for state in (self.fake.users, self.fake.consents, self.fake.balances):
            state.clear()
        self.fake.rewarded.clear()
        self.fake.events.clear()
        return self.deployed()

    def step(self, status="ok"):
        answer = self.post("/api/demo/next")
        self.assertEqual(answer["status"], status, answer["message"])
        return answer["details"]["step"]

    def play(self, until=11):
        """Start, then run the steps up to until; returns every step entry."""
        answer = self.post("/api/demo/start")
        self.assertEqual(answer["status"], "ok", answer["message"])
        return [answer["details"]["step"]] + [self.step() for _ in range(until)]

    def audit(self):
        labels = {address.lower(): label for label, address in self.fake.addresses.items()}
        return [
            (labels[event["requester"].lower()], event["scope"], event["allowed"], Reason(event["reason"]).name)
            for event in self.fake.events
        ]

    def tamper_folder(self):
        return records.data_root(self.settings) / "tamper"


class GuidedDemoTests(GuidedTestCase):
    def test_the_guided_demo_ends_like_the_scripted_demo(self):
        entries = self.play()
        self.assertEqual([entry["step"] for entry in entries], list(range(12)))
        self.assertEqual([entry["role"] for entry in entries], STEP_ROLES)
        self.assertTrue(all(entry["ok"] for entry in entries))
        self.deploy.assert_called_once_with(self.settings_file, reset=True)
        # the same log and rewards as python -m integration.demo_workflow
        self.assertEqual(self.audit(), demo_workflow.EXPECTED_AUDIT)
        self.assertEqual(self.fake.balances, {self.fake.addresses["guardian"].lower(): 2})
        progress = self.get_state()["demo"]
        self.assertTrue(progress["finished"])
        self.assertEqual(progress["summary"]["message"], "the guided demo gave the same outcomes as python -m integration.demo_workflow")
        self.assertTrue(progress["summary"]["audit_ok"] and progress["summary"]["rewards_ok"])
        self.assertEqual(self.post("/api/demo/next", status=400)["message"],
                         "the guided demo is finished: start it again for fresh contracts")

    def test_each_step_shows_its_actions(self):
        entries = self.play()
        wrong, clinic = entries[2]["results"]
        self.assertEqual((wrong["key"], wrong["result"]["message"]), ("attest:guardian", "rejected: NotTrustedClinic"))
        self.assertEqual((clinic["key"], clinic["result"]["status"]), ("attest:clinic", "ok"))
        self.assertEqual(entries[3]["got"], "denied NO_CONSENT")
        self.assertEqual([entries[number]["got"] for number in (4, 6, 10)], ["reward 0 -> 1", "reward 1 -> 2", "reward 2 -> 2"])
        self.assertEqual(entries[5]["results"][0]["result"]["fields"], {"measles_status": "verified"})
        self.assertEqual(entries[7]["results"][0]["result"]["fields"], {"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]})
        # every access request is marked, so the page can highlight its audit row
        logged = [item["result"]["tx"] for entry in entries for item in entry["results"] if item["logged"]]
        self.assertEqual(logged, [event["transaction_hash"] for event in self.fake.events])

    def test_tamper_step_uses_a_copy_and_deletes_it(self):
        entries = self.play(until=8)
        commitment = self.fake.users[self.fake.addresses["guardian"].lower()]["vaccination_hash"]
        details = entries[8]["results"][0]["result"]["details"]
        self.assertEqual((details["copy_deleted"], details["original_matches"]), (True, True))
        sent = self.fake.calls_to("request_access")[-1]["observed_hash"]
        self.assertNotIn(sent, (commitment, bytes(32)))
        self.assertEqual(list(self.tamper_folder().iterdir()), [])

    def test_step_11_is_the_exact_expiry(self):
        self.play(until=10)
        expires_at = next(iter(
            consent["expires_at"] for (owner, requester, scope), consent in self.fake.consents.items()
            if requester == self.fake.addresses["school"].lower() and scope == 1
        ))
        self.fake.node_requests.clear()
        entry = self.step()
        self.assertEqual(self.fake.node_requests, [
            ("evm_setNextBlockTimestamp", [expires_at - 1]), ("evm_mine", []), ("evm_setNextBlockTimestamp", [expires_at]),
        ])
        self.assertEqual(self.fake.events[-1]["timestamp"], expires_at)
        self.assertIn("view one second before: ALLOWED", entry["got"])
        self.assertIn("View only: nothing logged or released", entry["results"][1]["result"]["message"])
        self.assertTrue(entry["got"].endswith("mined at exactly expiresAt"))

    def test_it_can_be_played_again(self):
        self.play()
        entries = self.play()
        self.assertTrue(all(entry["ok"] for entry in entries))
        self.assertEqual(self.deploy.call_count, 2)
        self.assertTrue(self.get_state()["demo"]["summary"]["audit_ok"])


class GuidedFailureTests(GuidedTestCase):
    def test_next_before_start(self):
        self.assertEqual(self.post("/api/demo/next", status=400)["message"],
                         "start the guided demo first: it begins with fresh contracts")
        self.assertEqual(self.fake.calls, [])
        state = self.get_state()["demo"]
        self.assertEqual((state["started"], state["next"], len(state["steps"])), (False, 0, 12))

    def test_a_failed_start_can_be_repeated(self):
        from scripts import deploy_local
        self.deploy.side_effect = deploy_local.DeployError("local node not reachable or wrong chain: start it with npm run node")
        answer = self.post("/api/demo/start")
        self.assertEqual(answer["status"], "mismatch")
        self.assertIn("unavailable: local node not reachable", answer["details"]["step"]["got"])
        self.assertFalse(self.get_state()["demo"]["started"])
        self.post("/api/demo/next", status=400)
        self.deploy.side_effect = self.fresh_contracts
        self.assertEqual(self.post("/api/demo/start")["status"], "ok")

    def test_a_failed_step_stays_current_and_can_be_retried(self):
        self.play(until=2)
        self.fake.failures["request_access"] = ChainUnavailable("access event unavailable")
        entry = self.step("mismatch")
        self.assertEqual((entry["step"], entry["ok"]), (3, False))
        self.assertEqual(entry["got"], "unavailable: local node or receipt problem, nothing released")
        self.assertEqual(self.get_state()["demo"]["next"], 3)
        self.assertEqual(self.step()["step"], 3)
        for _ in range(8):
            self.step()
        self.assertTrue(self.get_state()["demo"]["summary"]["ok"])

    def test_a_step_that_does_not_match_says_so(self):
        self.play(until=2)
        self.grant("school", "MEASLES_STATUS")
        answer = self.post("/api/demo/next")
        self.assertEqual(answer["status"], "mismatch")
        self.assertEqual(
            answer["message"],
            "step 3: the school is denied: expected denied NO_CONSENT, nothing released; "
            "got allowed ALLOWED {'measles_status': 'verified'}",
        )
        self.assertEqual(self.get_state()["demo"]["next"], 3)

    def test_a_try_that_failed_after_its_transaction_is_picked_up(self):
        # each failure hits a read after the step's transaction went through; "Try again" must still pass
        self.play(until=0)
        self.fake.failures["get_user_info"] = ChainUnavailable("RPC request failed")
        entry = self.step("mismatch")
        self.assertEqual([item["result"]["status"] for item in entry["results"]], ["ok", "ok", "ok", "unavailable"])
        entry = self.step()
        self.assertEqual(entry["got"], "3 registered (some by an earlier try); on-chain identity hashes match the local ones")
        # the attestation goes through, then the read of the on-chain commitment fails
        self.fake.failures["get_user_info"] = ChainUnavailable("RPC request failed")
        entry = self.step("mismatch")
        self.assertIn("could not be checked", entry["got"])
        self.assertEqual(self.step()["got"], "guardian: rejected: NotTrustedClinic; clinic: rejected: EvidenceAlreadyRegistered, "
                                             "the on-chain commitment matches the local record")
        self.step()
        # the grant goes through, then the read of its expiry fails
        self.fake.failures["get_consent"] = ChainUnavailable("RPC request failed")
        self.step("mismatch")
        self.assertEqual(self.step()["got"], "reward 0 -> 1 (granted by an earlier try)")
        for _ in range(6):
            self.step()
        # step 11: the block at expiresAt - 1 is mined, then the view fails
        self.fake.node_requests.clear()
        self.fake.failures["check_access"] = ChainUnavailable("RPC request failed")
        self.step("mismatch")
        entry = self.step()
        self.assertEqual(entry["results"][0]["result"]["message"].split(" (")[0], "the block at expiresAt - 1")
        expires_at = self.fake.events[-1]["timestamp"]
        self.assertEqual(self.fake.node_requests, [
            ("evm_setNextBlockTimestamp", [expires_at - 1]), ("evm_mine", []), ("evm_setNextBlockTimestamp", [expires_at]),
        ])
        self.assertTrue(entry["got"].endswith("mined at exactly expiresAt"))
        self.assertTrue(self.get_state()["demo"]["summary"]["ok"])

    def test_a_final_check_that_could_not_run_can_run_again(self):
        self.play(until=10)
        self.fake.failures["get_reward_balance"] = ChainUnavailable("RPC request failed")
        self.step()
        state = self.get_state()["demo"]
        self.assertTrue(state["finished"])
        self.assertEqual(state["summary"], {"ok": False, "message": "the final check could not run: "
                                            "unavailable: local node not reachable or wrong chain"})
        answer = self.post("/api/demo/next", {"step": 12})
        self.assertEqual(answer["message"], "final check: the guided demo gave the same outcomes as python -m integration.demo_workflow")
        self.assertTrue(self.get_state()["demo"]["summary"]["ok"])
        self.post("/api/demo/next", {"step": 12}, status=400)

    def test_step_11_refuses_after_a_manual_jump(self):
        self.play(until=10)
        self.assertEqual(self.post("/api/demo/expire", {"requester": "school", "scope": "MEASLES_STATUS"})["status"], "ok")
        self.fake.node_requests.clear()
        entry = self.step("mismatch")
        self.assertIn("node time is already at or past expiresAt", entry["got"])
        self.assertEqual(self.fake.node_requests, [])

    def test_the_final_check_counts_only_the_guided_requests(self):
        self.play(until=10)
        self.post("/api/school-check")
        self.step()
        summary = self.get_state()["demo"]["summary"]
        self.assertTrue(summary["ok"])
        self.assertEqual(len(self.fake.events), 7)

    def test_contracts_deployed_elsewhere_end_the_guided_demo(self):
        self.play(until=3)
        self.assertFalse(self.get_state()["demo"]["stale"])
        # deploy_local --reset, or the scripted demo, puts new contracts on the node behind the page's back
        self.fake.contracts["ConsentManager"].address = f"0x{0xD1:040x}"
        state = self.get_state()["demo"]
        self.assertTrue(state["stale"])
        self.assertNotIn("contracts", state)

    def test_a_page_that_is_behind_sends_nothing(self):
        self.play(until=3)
        self.fake.calls.clear()
        self.assertEqual(self.post("/api/demo/next", {"step": 3}, status=400)["message"],
                         "that step has already run (the page was behind): nothing was sent")
        for step in ("4", True, 4.0):
            self.assertEqual(self.post("/api/demo/next", {"step": step}, status=400)["message"], "unknown step")
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.post("/api/demo/next", {"step": 4})["details"]["step"]["step"], 4)

    def test_only_on_chain_31337(self):
        self.settings["expected_chain_id"] = 1337
        answer = self.post("/api/demo/start")
        self.assertEqual(answer["status"], "mismatch")
        self.assertIn("only runs on the local Hardhat chain 31337", answer["details"]["step"]["got"])
        self.deploy.assert_not_called()

    def test_a_restarted_node_ends_the_guided_demo(self):
        self.play(until=3)
        # the node answers, but the recorded deployment is not on it any more
        self.fake.failures["load_contract"] = DeploymentUnavailable("deployment is from another run of the node")
        self.assertTrue(self.get_state()["demo"]["stale"])
        # a node that does not answer at all is not a reason to end it
        self.fake.failures["connect"] = ChainUnavailable("RPC connection failed")
        self.assertFalse(self.get_state()["demo"]["stale"])
        # deploy_local --reset on the restarted node puts contracts at the same addresses, in another block
        Path(self.settings["deployment_file"]).write_text(json.dumps({"deploy_block": {"number": 1, "hash": f"0x{99:064x}"}}))
        state = self.get_state()
        self.assertTrue(state["deployment"]["deployed"])
        self.assertTrue(state["demo"]["stale"])

    def test_a_deploy_by_hand_resets_the_guided_demo(self):
        self.play(until=3)
        self.post("/api/deploy")
        state = self.get_state()["demo"]
        self.assertEqual((state["started"], state["next"], state["entries"][0]), (False, 0, None))


class TamperTests(GuidedTestCase):
    def test_tamper_while_the_doctor_grant_is_active(self):
        commitment = self.ready()
        original = Path(self.settings["vaccination_file"]).read_bytes()
        self.grant("doctor", "VACCINATION_SCHEDULE")
        answer = self.post("/api/demo/tamper")
        self.assertEqual((answer["status"], answer["reason"], answer["fields"]), ("denied", "HASH_MISMATCH", {}))
        self.assertEqual(answer["details"], {"copy_deleted": True, "original_matches": True})
        sent = self.fake.calls_to("request_access")[-1]
        self.assertEqual(sent["requester"], self.fake.addresses["doctor"])
        self.assertNotIn(sent["observed_hash"], (commitment, bytes(32)))
        self.assertEqual(Path(self.settings["vaccination_file"]).read_bytes(), original)
        self.assertEqual(list(self.tamper_folder().iterdir()), [])

    def test_tamper_without_the_grant_says_why(self):
        self.ready()
        answer = self.post("/api/demo/tamper")
        self.assertEqual((answer["status"], answer["reason"]), ("denied", "NO_CONSENT"))
        self.assertIn("HASH_MISMATCH shows only while the doctor's grant is active", answer["details"]["note"])

    def test_the_copy_is_deleted_even_when_the_request_fails(self):
        self.ready()
        self.grant("doctor", "VACCINATION_SCHEDULE")
        self.fake.failures["request_access"] = RuntimeError("ABC124-DEMO")
        with mock.patch("sys.stderr"):
            answer = self.post("/api/demo/tamper")
        self.assertEqual(answer["message"], "failed: RuntimeError")
        self.assertEqual(list(self.tamper_folder().iterdir()), [])

    def test_tamper_before_the_record_is_attested(self):
        self.post("/api/setup")
        answer = self.post("/api/demo/tamper")
        self.assertEqual((answer["status"], answer["reason"]), ("denied", "NOT_REGISTERED"))
        # nothing attested: unknown, not "no"
        self.assertEqual(answer["details"]["original_matches"], None)
        self.assertIn("register and attest first", answer["details"]["note"])

    def test_tamper_before_setup(self):
        self.assertEqual(self.post("/api/demo/tamper")["message"], "unavailable: local record unavailable")


class ExpireTests(GuidedTestCase):
    def expire(self, requester="school", scope="MEASLES_STATUS", status=200):
        return self.post("/api/demo/expire", {"requester": requester, "scope": scope}, status=status)

    def test_an_active_grant_expires_at_its_second(self):
        self.ready()
        expires_at = self.grant("school", "MEASLES_STATUS", 3)["details"]["expires_at"]
        answer = self.expire()
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(self.fake.node_requests, [("evm_setNextBlockTimestamp", [expires_at]), ("evm_mine", [])])
        self.assertEqual(self.fake.now, expires_at)
        cells = {(cell["requester"], cell["scope"]): cell["status"] for cell in self.get_state()["consents"]}
        self.assertEqual(cells["school", "MEASLES_STATUS"], "expired")
        self.assertEqual(self.post("/api/school-check")["reason"], "EXPIRED")

    def refused(self, requester, scope, text):
        # a refusal sends nothing and leaves node time where it was
        before = (list(self.fake.node_requests), self.fake.now, len(self.fake.block_times))
        answer = self.expire(requester, scope)
        self.assertEqual(answer["status"], "refused")
        self.assertIn(text, answer["message"])
        self.assertEqual((list(self.fake.node_requests), self.fake.now, len(self.fake.block_times)), before)

    def test_nothing_to_expire_is_refused_without_moving_time(self):
        self.ready()
        self.refused("school", "MEASLES_STATUS", "the guardian never granted school MEASLES_STATUS, so nothing expires")
        self.grant("school", "MEASLES_STATUS")
        self.post("/api/revoke", {"role": "guardian", "requester": "school", "scope": "MEASLES_STATUS"})
        self.refused("school", "MEASLES_STATUS", "is revoked")
        self.grant("doctor", "VACCINATION_SCHEDULE", 1)
        self.assertEqual(self.expire("doctor", "VACCINATION_SCHEDULE")["status"], "ok")
        self.refused("doctor", "VACCINATION_SCHEDULE", "has already expired")

    def test_only_on_the_local_chain(self):
        self.ready()
        self.grant("school", "MEASLES_STATUS")
        self.settings["expected_chain_id"] = 1
        self.assertEqual(self.expire()["message"], "refused: node time can only be moved on the local Hardhat chain 31337")
        self.assertEqual(self.fake.node_requests, [])

    def test_bad_values_are_refused_before_any_chain_call(self):
        self.ready()
        for requester, scope in (("guardian", "MEASLES_STATUS"), ("teacher", "MEASLES_STATUS"), ("school", "ALL"), (None, None)):
            self.assertEqual(self.expire(requester, scope, status=400)["status"], "invalid")
        self.assertEqual(self.fake.calls, [])

    def test_node_down(self):
        self.ready()
        self.fake.failures["connect"] = ChainUnavailable("RPC connection failed")
        self.assertEqual(self.expire()["message"], "unavailable: local node not reachable or wrong chain")


if __name__ == "__main__":
    unittest.main()
