# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Offline tests for evaluation/measure.py. No node: the scenario runs against the fake chain with the
deploy steps and web3-only helpers mocked, and the table and CSV code gets hand-made samples. The real
request path (_request through app.chain with a mocked contract), the reward check and the warm-up are
tested on their own. Only the event-decoding and request tests need web3; they are skipped when it is not
installed. Files go to temporary folders. The real measured tables come from python -m evaluation.measure
on a node.
"""
import contextlib
import csv
import io
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import chain, disclosure
from app.models import Reason, ChainUnavailable, Web3NotInstalled, TransactionRejected, TransactionPending
from evaluation import measure
from scripts import deploy_local
from tests.fake_chain import FakeChain, install
from tests.test_chain import access_log, mock_contract, raw_receipt

try:
    from hexbytes import HexBytes
    from web3 import Web3
except ImportError:
    Web3 = None

ROLES = {"deployer": 0, "clinic": 1, "guardian": 2, "school": 3, "doctor": 4}
REQUESTER_INDICES = list(range(5, 15))
DEPLOY_GAS = {"IdentityRegistry": 236_456, "ConsentRewardToken": 233_933, "ConsentManager": 585_978}
TX_HASH = "0x" + "ab" * 32


def receipt(gas, status=1, block=7):
    return {"transaction_hash": TX_HASH, "status": status, "gas_used": gas, "block_number": block, "logs": [], "contract_address": None}


def raiser(error):
    def send(*args):
        raise error
    return send


def sample(gas, status="ok", count=1, scenario="s", role="requester", function="requestAccess", order=(1, 0), event=0.002):
    ok = status == "ok"
    return {
        "requester_count": count, "role": role, "contract": "ConsentManager", "function": function, "scenario": scenario,
        "status": status, "gas_used": gas if ok else None, "receipt_seconds": 0.001 if ok else None,
        "event_seconds": event if ok else None, "order": order, "started_at": 10.0 + order[0], "finished_at": 10.5 + order[0],
    }


class ScenarioChain(FakeChain):
    """FakeChain with positive gas, receipts kept by hash, the rewards each grant minted, and a reset for
    the fresh contracts of every run.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self.receipts = {}
        self.minted = {}

    def reset(self):
        self.users, self.consents, self.rewarded, self.balances, self.events = {}, {}, set(), {}, []

    def grant_consent(self, manager, guardian, requester, scope, duration_days):
        before = sum(self.balances.values())
        result = super().grant_consent(manager, guardian, requester, scope, duration_days)
        self.minted[result["transaction_hash"]] = sum(self.balances.values()) - before
        return result

    def _receipt(self):
        result = super()._receipt()
        result["gas_used"] = 40_000 + self.transactions
        self.receipts[result["transaction_hash"]] = result
        return result


class MeasureTestCase(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "rpc_url": "http://127.0.0.1:8545", "expected_chain_id": 31337,
            "actor_account_indices": dict(ROLES), "scenario_requester_account_indices": list(REQUESTER_INDICES),
        }
        self.client = SimpleNamespace(
            eth=SimpleNamespace(chain_id=31337, accounts=[f"0x{index:040x}" for index in range(20)]),
            provider=SimpleNamespace(make_request=self.make_request),
        )
        self.node_requests = []
        self.use_fake(ScenarioChain)
        self.patch(chain, "call_view", lambda call: call())
        self.patch(measure, "_warm_up", lambda *args: None)
        self.patch(measure, "_contract", self.contract)
        self.patch(measure, "_one_event", lambda contract, name, receipt: {"event": name})
        self.patch(measure, "_grant_events", self.grant_events)
        self.patch(measure, "_request", self.request)
        self.patch(measure, "_set_node_time", self.set_node_time)
        self.deploys = []
        for name, step in (
            ("IdentityRegistry", "deploy_registry"), ("ConsentRewardToken", "deploy_reward_token"),
            ("ConsentManager", "deploy_consent_manager"),
        ):
            self.patch(deploy_local, step, self.deployer(name))
        self.patch(deploy_local, "configure_minter", lambda *args: receipt(47_919))

    def use_fake(self, fake_class):
        # requester labels as requester_accounts builds them, so the fake's select_account knows them
        labels = dict(ROLES, **{f"requester {number}": index for number, index in enumerate(REQUESTER_INDICES, 1)})
        self.fake = fake_class({"actor_account_indices": labels})
        install(self, self.fake)
        # every chain helper is the fake's, but connect gives the client with the raw node calls
        self.patch(chain, "connect", lambda settings: self.client)

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_request(self, method, params):
        self.node_requests.append(method)
        return {"result": {"hardhat_getAutomine": True, "web3_clientVersion": "HardhatNetwork/3 fake"}.get(method)}

    def deployer(self, name):
        def deploy(*args):
            self.deploys.append(name)
            if name == "IdentityRegistry":
                # fresh contracts for every run
                self.fake.reset()
            return f"0x{len(self.deploys):040x}", receipt(DEPLOY_GAS[name])
        return deploy

    def contract(self, client, deploy_local_module, name, address):
        fake = self.fake
        functions = SimpleNamespace(
            totalSupply=lambda: (lambda: sum(fake.balances.values())),
            hasReceivedReward=lambda guardian, requester, scope: (lambda: (guardian.lower(), requester.lower(), int(scope)) in fake.rewarded),
        )
        return SimpleNamespace(name=name, address=address, functions=functions)

    def grant_events(self, manager, token, grant_receipt, guardian, requester):
        return {"granted": {"requester": requester}, "rewards": self.fake.minted[grant_receipt["transaction_hash"]]}

    def request(self, client, manager, requester, owner, observed_hash):
        # like measure._request: the send is timed to the receipt, the event separately
        decoded = {}

        def send():
            decoded["event"] = self.fake.request_access(manager, requester=requester, owner=owner, scope=measure.SCOPE, observed_hash=observed_hash)
            return self.fake.receipts[decoded["event"]["transaction_hash"]]

        return measure.measure_transaction(send, lambda request_receipt: decoded["event"])

    def set_node_time(self, client, timestamp):
        self.node_requests.append(("time", timestamp))
        self.fake.now = timestamp


class DeploymentCostTests(unittest.TestCase):
    def test_gas_and_metadata_come_from_the_receipt(self):
        row = measure.record_deployment_cost("IdentityRegistry", dict(receipt(236_456), contract_address="0x" + "12" * 20))
        self.assertEqual(row, {
            "contract": "IdentityRegistry", "gas_used": 236_456, "transaction_hash": TX_HASH, "block_number": 7,
            "contract_address": "0x" + "12" * 20,
        })

    def test_unsuccessful_or_odd_receipts_are_refused(self):
        for bad in (receipt(0), receipt(-5), receipt(True), receipt(1.5), receipt(10, status=0), {"status": 1}, None, "receipt"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    measure.record_deployment_cost("IdentityRegistry", bad)

    def test_only_the_three_contracts(self):
        with self.assertRaises(ValueError):
            measure.record_deployment_cost("Token", receipt(1))


class MeasureTransactionTests(unittest.TestCase):
    def test_success_with_and_without_an_event(self):
        # a clock that ticks 1 ms per reading: time.monotonic only advances every ~16 ms on Windows,
        # so an instant fake transaction would measure 0 seconds there
        with mock.patch.object(measure.time, "monotonic", side_effect=itertools.count(100.0, 0.001)):
            measured = measure.measure_transaction(lambda: receipt(100), lambda r: {"reason": 0})
        self.assertEqual((measured["status"], measured["gas_used"], measured["event"]), ("ok", 100, {"reason": 0}))
        self.assertGreaterEqual(measured["event_seconds"], measured["receipt_seconds"])
        self.assertGreater(measured["receipt_seconds"], 0)
        self.assertLessEqual(measured["started_at"], measured["finished_at"])
        plain = measure.measure_transaction(lambda: receipt(100))
        self.assertIsNone(plain["event_seconds"])
        self.assertEqual(plain["transaction_hash"], TX_HASH)

    def test_failures_are_reported_apart_and_never_measured(self):
        for error, status, text in (
            (TransactionRejected("ConsentStillActive"), "rejected", "ConsentStillActive"),
            (TransactionRejected(), "rejected", "Reverted"),
            (TransactionPending("x"), "pending", "no receipt in time"),
            (ChainUnavailable("x"), "unavailable", "node, receipt or deployment unavailable"),
        ):
            with self.subTest(status=status):
                measured = measure.measure_transaction(raiser(error))
                self.assertEqual((measured["status"], measured["error"]), (status, text))
                self.assertIsNone(measured["gas_used"])
                self.assertIsNone(measured["receipt_seconds"])
                self.assertIsNone(measured["event_seconds"])

    def test_missing_event_is_a_failure_without_gas(self):
        def no_event(r):
            raise ChainUnavailable("event unavailable")

        measured = measure.measure_transaction(lambda: receipt(5), no_event)
        self.assertEqual(measured["status"], "no_event")
        self.assertIsNone(measured["gas_used"])
        self.assertIsNone(measured["event_seconds"])

    def test_other_errors_are_not_swallowed(self):
        with self.assertRaises(KeyError):
            measure.measure_transaction(raiser(KeyError("x")))


class TableTests(unittest.TestCase):
    def test_summary_leaves_failures_out_and_blanks_what_was_not_measured(self):
        first, second = measure.summarize_samples([
            sample(10), sample(20), sample(None, status="pending"), sample(None, "rejected", scenario="t", order=(2, 0)),
        ])
        self.assertEqual(
            (first["sample_count"], first["total_gas_used"], first["average_gas_used"], first["min_gas_used"], first["max_gas_used"], first["failures"]),
            (2, 30, 15, 10, 20, 1),
        )
        self.assertEqual(
            (second["sample_count"], second["total_gas_used"], second["average_gas_used"], second["average_receipt_seconds"], second["failures"]),
            (0, None, None, None, 1),
        )

    def test_summary_keeps_scenario_order(self):
        rows = measure.summarize_samples([sample(1, scenario="later", order=(5, 0)), sample(1, scenario="earlier", order=(2, 3))])
        self.assertEqual([row["scenario"] for row in rows], ["earlier", "later"])

    def test_event_mean_only_over_transactions_with_an_event(self):
        row = measure.summarize_samples([sample(10, event=None), sample(10, event=0.004)])[0]
        self.assertEqual((row["event_count"], row["average_event_seconds"]), (1, 0.004))

    def test_gas_table_blank_for_an_all_failed_group(self):
        rows = measure.gas_table([sample(10), sample(None, "rejected", scenario="t", order=(2, 0))])
        self.assertIsNone(rows[1]["total_gas_used"])
        self.assertIsNone(rows[1]["average_gas_used"])
        self.assertIn("not measured: every call failed", rows[1]["notes"])
        self.assertEqual(rows[0]["scenario"], "requester: s")

    def test_gas_table_notes_the_compiler_under_deployments(self):
        deploy = dict(sample(236_456, role="deployer", function="deployment"), contract="IdentityRegistry")
        row = measure.gas_table([deploy])[0]
        self.assertIn(measure.COMPILER_NOTE, row["notes"])
        self.assertEqual(row["average_gas_used"], "236456")

    def test_timing_table_has_one_row_per_operation_only(self):
        samples = [sample(10, order=(1, 0)), sample(30, order=(1, 1)), sample(50, role="guardian", function="grantConsent", order=(2, 0))]
        rows = measure.timing_table(samples)
        self.assertEqual([row["operation"] for row in rows], ["requester: requestAccess", "guardian: grantConsent"])
        self.assertEqual(rows[0]["average_gas_used"], "20")
        # every transaction is counted once, so the sample counts add up
        self.assertEqual(sum(row["sample_count"] for row in rows), len(samples))

    def test_timing_summary_has_the_role_and_run_rows(self):
        samples = [sample(10, order=(1, 0)), sample(30, order=(1, 1)), sample(50, role="guardian", function="grantConsent", order=(2, 0))]
        rows = measure.timing_summary_table(samples)
        self.assertEqual([row["operation"] for row in rows], [
            "guardian: all transactions", "requester: all transactions", "all roles: all transactions",
        ])
        self.assertEqual(rows[-1]["sample_count"], 3)
        self.assertIn("wall clock 1.500 s", rows[-1]["notes"])

    def test_deploy_rows_say_they_are_not_send_to_receipt(self):
        deploy = dict(sample(236_456, role="deployer", function="deployment", event=None), contract="IdentityRegistry")
        row = measure.timing_table([deploy])[0]
        self.assertTrue(row["notes"].startswith("send to verified deploy, not send to receipt"))
        self.assertIn("artifact file read", row["notes"])

    def test_gas_and_seconds_formatting(self):
        self.assertEqual((measure._gas(None), measure._gas(15.0), measure._gas(15.25)), (None, "15", "15.2"))
        self.assertEqual((measure._seconds(None), measure._seconds(0.0012345)), (None, "0.001234"))


class WriteResultsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.rows = measure.gas_table([sample(10), sample(None, "rejected", scenario="t", order=(2, 0))])

    def test_template_header_and_blank_for_not_measured(self):
        path = self.folder / "gas_results.csv"
        measure.write_results(path, self.rows)
        text = path.read_text()
        lines = list(csv.reader(text.splitlines()))
        template = (measure.TEMPLATES_DIR / "gas_results.csv").read_text().splitlines()[0].split(",")
        self.assertEqual(lines[0], template)
        self.assertEqual(lines[2][4:6], ["", ""])
        self.assertNotIn("\r", text)
        self.assertEqual(sorted(item.name for item in self.folder.iterdir()), ["gas_results.csv"])

    def test_empty_table_and_unknown_file_are_refused(self):
        with self.assertRaises(ValueError):
            measure.write_results(self.folder / "timing_results.csv", [])
        with self.assertRaises(ValueError):
            measure.write_results(self.folder / "unknown.csv", self.rows)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_given_header_is_used(self):
        path = self.folder / "any.csv"
        measure.write_results(path, [{"a": 1, "b": None}], header=["a", "b"])
        self.assertEqual(path.read_text(), "a,b\n1,\n")


class SyntheticDataTests(unittest.TestCase):
    def test_synthetic_hashes_are_fixed_nonzero_and_distinct(self):
        hashes = [measure.synthetic_hash(f"requester {number} identity, run N=10") for number in range(1, 11)]
        self.assertEqual(len(set(hashes)), 10)
        self.assertTrue(all(len(value) == 32 and any(value) for value in hashes))
        self.assertEqual(measure.synthetic_hash("x"), measure.synthetic_hash("x"))


class RequesterAccountTests(MeasureTestCase):
    def test_first_accounts_of_the_scenario_list(self):
        accounts = measure.requester_accounts(self.client, self.settings, 3)
        self.assertEqual(accounts, [self.fake.addresses[f"requester {number}"] for number in (1, 2, 3)])

    def test_bad_account_lists_are_refused(self):
        for indices, count in (([5, 6], 3), ([5, 5, 6], 3), ([5, 2, 6], 3), (None, 1)):
            with self.subTest(indices=indices):
                with self.assertRaises(measure.MeasureError):
                    measure.requester_accounts(self.client, dict(self.settings, scenario_requester_account_indices=indices), count)

    def test_index_the_node_does_not_have(self):
        self.fake.failures["select_account"] = ValueError("demo account index unavailable")
        with self.assertRaises(measure.MeasureError) as caught:
            measure.requester_accounts(self.client, self.settings, 1)
        self.assertEqual(str(caught.exception), "a requester account index is not one of the node's accounts")


class ScenarioTests(MeasureTestCase):
    def run_scenario(self, count):
        return measure.run_requester_scenario(self.settings, count)

    def test_every_role_acts_and_outcomes_are_grouped(self):
        samples = self.run_scenario(2)
        # 6 single transactions, then 11 phases with one transaction per requester
        self.assertEqual(len(samples), 6 + 11 * 2)
        self.assertTrue(all(item["status"] == "ok" for item in samples))
        self.assertEqual({item["role"] for item in samples}, {"deployer", "clinic", "guardian", "requester"})
        self.assertEqual(self.deploys, ["IdentityRegistry", "ConsentRewardToken", "ConsentManager"])
        by_scenario = {}
        for item in samples:
            by_scenario.setdefault(item["scenario"], []).append(item)
        counts = {scenario: len(items) for scenario, items in by_scenario.items()}
        self.assertEqual(counts[measure.FIRST_REWARD], 1)
        self.assertEqual(counts[measure.LATER_REWARD], 1)
        self.assertEqual(counts[measure.REGRANT_AFTER_REVOKE], 2)
        self.assertEqual(counts[measure.REGRANT_AFTER_EXPIRY], 2)
        for reason in (Reason.MISSING_EVIDENCE, Reason.NO_CONSENT, Reason.ALLOWED, Reason.HASH_MISMATCH, Reason.REVOKED, Reason.EXPIRED):
            self.assertEqual(counts[measure.REQUESTS[reason]], 2, reason.name)
        deployments = [item["gas_used"] for item in samples if item["function"] == "deployment"]
        self.assertEqual(deployments, [DEPLOY_GAS[name] for name in measure.DEPLOY_ORDER])
        # one reward per requester, all to the guardian
        self.assertEqual(self.fake.balances, {self.fake.addresses["guardian"].lower(): 2})

    def test_samples_follow_the_transaction_order(self):
        samples = self.run_scenario(1)
        self.assertEqual([item["order"] for item in samples], sorted(item["order"] for item in samples))
        self.assertEqual([item["function"] for item in samples[:5]], ["deployment"] * 3 + ["setMinterOnce", "registerUser"])
        self.assertEqual(samples[-1]["scenario"], measure.REGRANT_AFTER_EXPIRY)

    def test_node_time_moves_once_to_the_latest_expiry(self):
        self.run_scenario(2)
        times = [request for request in self.node_requests if isinstance(request, tuple)]
        self.assertEqual(len(times), 1)
        expiries = [consent["expires_at"] for consent in self.fake.consents.values()]
        # the last regrants started a new 30-day term after the time step
        self.assertEqual(times[0][1] + measure.GRANT_DAYS * 86400, max(expiries))

    def test_tables_from_a_scenario(self):
        samples = self.run_scenario(1) + self.run_scenario(5)
        gas = measure.gas_table(samples)
        timing = measure.timing_table(samples)
        self.assertTrue(all(row["sample_count"] > 0 for row in gas))
        self.assertEqual({row["requester_count"] for row in timing}, {1, 5})
        deploy_row = next(row for row in gas if row["contract"] == "ConsentManager" and row["function"] == "deployment")
        self.assertEqual(deploy_row["average_gas_used"], str(DEPLOY_GAS["ConsentManager"]))
        self.assertEqual(deploy_row["sample_count"], 2)

    def test_bad_requester_count(self):
        for count in (0, -1, True, 1.0, "5"):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    self.run_scenario(count)
        self.assertEqual(self.deploys, [])

    def test_failed_deploy_stops_the_run(self):
        self.patch(deploy_local, "deploy_registry", raiser(TransactionRejected("ZeroAddress")))
        with self.assertRaises(measure.MeasureError) as caught:
            self.run_scenario(1)
        self.assertEqual(str(caught.exception), "run N=1: IdentityRegistry deploy failed: ZeroAddress")

    def test_wrong_denial_reason_stops_the_run(self):
        class NoHashCheck(ScenarioChain):
            def request_access(self, manager, requester, owner, scope, observed_hash):
                return super().request_access(manager, requester, owner, scope, self.users[owner.lower()]["vaccination_hash"])

        self.use_fake(NoHashCheck)
        with self.assertRaises(measure.MeasureError) as caught:
            self.run_scenario(1)
        self.assertEqual(str(caught.exception), "run N=1: requester 1 expected HASH_MISMATCH, got ALLOWED")

    def test_reward_on_a_regrant_stops_the_run(self):
        class RewardsAgain(ScenarioChain):
            def grant_consent(self, manager, guardian, requester, scope, duration_days):
                self.rewarded.clear()
                return super().grant_consent(manager, guardian, requester, scope, duration_days)

        self.use_fake(RewardsAgain)
        with self.assertRaises(measure.MeasureError) as caught:
            self.run_scenario(1)
        self.assertEqual(str(caught.exception), "run N=1: requester 1 grant minted 1 rewards, expected 0")

    def test_allowed_flag_that_contradicts_the_reason_stops_the_run(self):
        class AllowedDenial(ScenarioChain):
            def request_access(self, manager, requester, owner, scope, observed_hash):
                event = super().request_access(manager, requester, owner, scope, observed_hash)
                return dict(event, allowed=True) if event["reason"] == Reason.NO_CONSENT else event

        self.use_fake(AllowedDenial)
        with self.assertRaises(measure.MeasureError) as caught:
            self.run_scenario(1)
        self.assertEqual(str(caught.exception), "run N=1: requester 1 expected NO_CONSENT, got NO_CONSENT with allowed=True")

    def test_pending_revoke_is_a_failure_and_the_next_check_stops_the_run(self):
        self.fake.failures["revoke_consent"] = TransactionPending()
        with self.assertRaises(measure.MeasureError) as caught:
            self.run_scenario(1)
        self.assertEqual(str(caught.exception), "run N=1: requester 1 expected REVOKED, got ALLOWED")


class NodeCheckTests(unittest.TestCase):
    def client(self, chain_id=31337, automine=True, answer=None):
        def make_request(method, params):
            if isinstance(answer, Exception):
                raise answer
            return answer if answer is not None else {"result": automine}
        return SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id), provider=SimpleNamespace(make_request=make_request))

    def test_local_node_with_automine_passes(self):
        self.assertIsNone(measure._require_local_node(self.client()))

    def test_other_chain_or_automine_off_is_refused(self):
        with self.assertRaises(measure.MeasureError) as caught:
            measure._require_local_node(self.client(chain_id=1))
        self.assertEqual(str(caught.exception), "measurements run only on the local Hardhat chain 31337")
        with self.assertRaises(measure.MeasureError) as caught:
            measure._require_local_node(self.client(automine=False))
        self.assertIn("automine is off", str(caught.exception))

    def test_node_errors_carry_no_details(self):
        with self.assertRaises(ChainUnavailable) as caught:
            measure._require_local_node(self.client(answer=ConnectionError("http://secret:1/path")))
        self.assertEqual(caught.exception.args, ("RPC request failed",))

    def test_set_node_time_sets_then_mines(self):
        requests = []
        client = SimpleNamespace(provider=SimpleNamespace(make_request=lambda method, params: requests.append((method, params)) or {"result": None}))
        measure._set_node_time(client, 1_800_000_000)
        self.assertEqual(requests, [("evm_setNextBlockTimestamp", [1_800_000_000]), ("evm_mine", [])])

    def test_set_node_time_refused_by_the_node(self):
        with self.assertRaises(measure.MeasureError) as caught:
            measure._set_node_time(self.client(answer={"error": {"message": "x"}}), 1)
        self.assertEqual(str(caught.exception), "the node refused evm_setNextBlockTimestamp")
        with self.assertRaises(ChainUnavailable):
            measure._set_node_time(self.client(answer=OSError("down")), 1)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "settings.json"

    def read(self, content):
        self.path.write_text(content)
        return measure._read_settings(self.path)

    def test_bad_settings_files(self):
        full = {
            "rpc_url": "x", "expected_chain_id": 31337, "actor_account_indices": {"deployer": 0, "clinic": 1, "guardian": 2},
            "scenario_requester_account_indices": [],
        }
        for content, text in (
            ("{", "is not valid JSON"),
            ("[]", "needs rpc_url, expected_chain_id"),
            (json.dumps({"rpc_url": "x"}), "needs rpc_url, expected_chain_id"),
            (json.dumps(dict(full, expected_chain_id=1)), "only on the local Hardhat chain 31337"),
            (json.dumps(dict(full, actor_account_indices={"deployer": 0, "guardian": 2})), measure.NO_ROLES),
            (json.dumps(dict(full, actor_account_indices=[0, 1, 2])), measure.NO_ROLES),
        ):
            with self.subTest(content=content):
                with self.assertRaises(measure.MeasureError) as caught:
                    self.read(content)
                self.assertIn(text, str(caught.exception))
        self.assertEqual(self.read(json.dumps(full)), full)

    def test_missing_settings_file(self):
        with self.assertRaises(measure.MeasureError) as caught:
            measure._read_settings(self.path)
        self.assertIn("no settings file at", str(caught.exception))
        # the file name only: the temporary folder is outside the project
        self.assertNotIn(str(self.path.parent), str(caught.exception))


class GitRevisionTests(unittest.TestCase):
    def test_every_path_that_decides_the_tables_is_checked(self):
        commands = []

        def output(command):
            commands.append(command)
            return {"rev-parse": "a635a30", "status": " M app/chain.py"}[command[1]]

        with mock.patch.object(measure, "_command_output", output):
            self.assertEqual(measure._git_revision(), "commit a635a30 + uncommitted changes")
        self.assertEqual(commands[1][-len(measure.CODE_PATHS):], list(measure.CODE_PATHS))
        for path in ("contracts", "app", "scripts", "evaluation/measure.py", "hardhat.config.ts", "package.json"):
            self.assertIn(path, measure.CODE_PATHS)
        with mock.patch.object(measure, "_command_output", lambda command: "a635a30" if command[1] == "rev-parse" else ""):
            self.assertEqual(measure._git_revision(), "commit a635a30")


class MainTests(MeasureTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.output = self.folder / "results"
        self.settings_file = self.folder / "settings.json"
        self.settings_file.write_text(json.dumps(self.settings))
        self.fingerprints = {name: ("sha", "build") for name in measure.DEPLOY_ORDER}
        self.patch(measure, "_artifact_fingerprints", lambda: dict(self.fingerprints))
        self.client.eth.get_block = lambda block: {"gasLimit": 30_000_000}

    def run_main(self, *argv):
        output, errors = io.StringIO(), io.StringIO()
        code = None
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                measure.main(list(argv) or ["--settings", str(self.settings_file), "--output-dir", str(self.output)])
            except SystemExit as exit_:
                code = exit_.code
        return output.getvalue(), errors.getvalue(), code

    def test_three_runs_then_four_files(self):
        output, errors, code = self.run_main()
        self.assertIsNone(code, errors)
        self.assertEqual(self.deploys.count("IdentityRegistry"), 3)
        self.assertEqual(
            sorted(path.name for path in self.output.iterdir()),
            ["ENVIRONMENT.md", "gas_results.csv", "timing_results.csv", "timing_summary.csv"],
        )
        for line in ("N=1: 17 transactions on fresh contracts, 0 failed", "N=5: 61 transactions", "N=10: 116 transactions"):
            self.assertIn(line, output)
        for name in ("gas_results.csv", "timing_results.csv", "timing_summary.csv"):
            header = (measure.TEMPLATES_DIR / name).read_text().splitlines()[0]
            self.assertEqual((self.output / name).read_text().splitlines()[0], header)
        environment = (self.output / "ENVIRONMENT.md").read_text()
        self.assertIn(measure.COMPILER_NOTE, environment)
        self.assertIn("Written by `python -m evaluation.measure --settings PATH --output-dir DIR` on ", environment)
        # the output folder is outside the project, so only the file names are printed
        self.assertIn("wrote timing_summary.csv (", output)
        self.assertNotIn(str(self.folder), output + environment)

    def test_timing_rows_add_up_to_the_transactions_of_each_run(self):
        self.run_main()
        with (self.output / "timing_results.csv").open() as file:
            rows = list(csv.DictReader(file))
        totals = {}
        for row in rows:
            totals[row["requester_count"]] = totals.get(row["requester_count"], 0) + int(row["sample_count"])
        self.assertEqual(totals, {"1": 17, "5": 61, "10": 116})
        with (self.output / "timing_summary.csv").open() as file:
            summary = list(csv.DictReader(file))
        whole = {row["requester_count"]: int(row["sample_count"]) for row in summary if row["operation"] == "all roles: all transactions"}
        self.assertEqual(whole, totals)

    def test_written_by_names_only_the_options_given(self):
        with mock.patch.object(measure, "SETTINGS_FILE", self.settings_file), mock.patch.object(measure, "RESULTS_DIR", self.output):
            _, errors, code = self.run_main("--output-dir", str(self.output))
            self.assertIsNone(code, errors)
            self.assertIn("Written by `python -m evaluation.measure --output-dir DIR` on ", (self.output / "ENVIRONMENT.md").read_text())
            (self.output / "ENVIRONMENT.md").unlink()
            with mock.patch.object(measure, "RESULTS_DIR", self.output):
                with contextlib.redirect_stdout(io.StringIO()):
                    measure.main([])
        self.assertIn("Written by `python -m evaluation.measure` on ", (self.output / "ENVIRONMENT.md").read_text())

    def test_missing_role_label_is_one_line_not_a_traceback(self):
        roles = {"deployer": 0, "guardian": 2}
        self.settings_file.write_text(json.dumps(dict(self.settings, actor_account_indices=roles)))
        output, errors, code = self.run_main()
        self.assertEqual((code, errors), (1, f"measurement stopped: {measure.NO_ROLES}\n"))
        self.assertEqual(self.deploys, [])
        self.assertFalse(self.output.exists())

    def test_role_index_the_node_does_not_have(self):
        self.fake.failures["select_account"] = ValueError("demo account index unavailable")
        with self.assertRaises(measure.MeasureError) as caught:
            measure.run_requester_scenario(self.settings, 1)
        self.assertEqual(str(caught.exception), measure.NO_ROLES)

    def test_missing_web3_says_how_to_install_it(self):
        self.patch(chain, "connect", mock.Mock(side_effect=Web3NotInstalled(chain.WEB3_MISSING)))
        _, errors, code = self.run_main()
        self.assertEqual((code, errors), (1, f"measurement stopped: {disclosure.NO_WEB3_MESSAGE}\n"))

    def test_node_down_writes_nothing(self):
        self.patch(chain, "connect", mock.Mock(side_effect=ChainUnavailable("RPC connection failed")))
        _, errors, code = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(errors, "measurement stopped: local node not reachable or wrong chain: start it with npm run node "
                                 "and check rpc_url and expected_chain_id\n")
        self.assertFalse(self.output.exists())

    def test_recompile_during_the_run_writes_nothing(self):
        real_scenario = measure.run_requester_scenario

        def scenario_then_recompile(settings, count):
            samples = real_scenario(settings, count)
            self.fingerprints["ConsentManager"] = ("other", "build")
            return samples

        self.patch(measure, "run_requester_scenario", scenario_then_recompile)
        _, errors, code = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("the compiled contracts changed during the run", errors)
        self.assertFalse(self.output.exists())

    def test_failed_run_writes_nothing(self):
        self.fake.failures["register_vaccination"] = TransactionRejected("NotTrustedClinic")
        _, errors, code = self.run_main()
        self.assertEqual(code, 1)
        self.assertTrue(errors.startswith("measurement stopped: run N=1: requester 1 expected NO_CONSENT, got MISSING_EVIDENCE"))
        self.assertFalse(self.output.exists())


@unittest.skipIf(Web3 is None, "web3 is not installed")
class EventTests(unittest.TestCase):
    """_logs_of, _one_event and _grant_events decode real log bytes with web3, as on the node."""
    MANAGER = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    TOKEN = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
    GUARDIAN = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    SCHOOL = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
    GRANTED_ABI = {"anonymous": False, "name": "ConsentGranted", "type": "event", "inputs": [
        {"indexed": True, "name": "owner", "type": "address"}, {"indexed": True, "name": "requester", "type": "address"},
        {"indexed": True, "name": "scope", "type": "uint8"}, {"indexed": False, "name": "expiresAt", "type": "uint256"},
    ]}
    MINTED_ABI = {"anonymous": False, "name": "RewardMinted", "type": "event", "inputs": [
        {"indexed": True, "name": "recipient", "type": "address"}, {"indexed": False, "name": "amount", "type": "uint256"},
    ]}

    def setUp(self):
        self.manager = Web3().eth.contract(address=self.MANAGER, abi=[self.GRANTED_ABI])
        self.token = Web3().eth.contract(address=self.TOKEN, abi=[self.MINTED_ABI])

    def topic(self, address):
        return HexBytes(bytes(12) + bytes.fromhex(address[2:]))

    def log(self, address, topics, data, index):
        return {
            "address": address, "topics": topics, "data": HexBytes(data), "logIndex": index, "transactionIndex": 0,
            "transactionHash": HexBytes(TX_HASH), "blockHash": HexBytes("0x" + "bb" * 32), "blockNumber": 7,
        }

    def granted(self, requester, address=None, index=0):
        return self.log(address or self.MANAGER, [
            Web3.keccak(text="ConsentGranted(address,address,uint8,uint256)"), self.topic(self.GUARDIAN),
            self.topic(requester), HexBytes((1).to_bytes(32, "big")),
        ], Web3().codec.encode(["uint256"], [1_800_000_000]), index)

    def minted(self, recipient, address=None, index=1):
        return self.log(address or self.TOKEN, [Web3.keccak(text="RewardMinted(address,uint256)"), self.topic(recipient)],
                        Web3().codec.encode(["uint256"], [1]), index)

    def test_rewarded_grant(self):
        events = measure._grant_events(self.manager, self.token, receipt_with(self.granted(self.SCHOOL), self.minted(self.GUARDIAN)), self.GUARDIAN, self.SCHOOL)
        self.assertEqual(events["rewards"], 1)
        self.assertEqual(events["granted"]["expiresAt"], 1_800_000_000)

    def test_regrant_without_reward(self):
        events = measure._grant_events(self.manager, self.token, receipt_with(self.granted(self.SCHOOL)), self.GUARDIAN, self.SCHOOL)
        self.assertEqual(events["rewards"], 0)

    def test_reward_to_anyone_but_the_guardian_is_refused(self):
        with self.assertRaises(ChainUnavailable):
            measure._grant_events(self.manager, self.token, receipt_with(self.granted(self.SCHOOL), self.minted(self.SCHOOL)), self.GUARDIAN, self.SCHOOL)

    def test_grant_event_for_another_requester_is_refused(self):
        with self.assertRaises(ChainUnavailable):
            measure._grant_events(self.manager, self.token, receipt_with(self.granted(self.GUARDIAN)), self.GUARDIAN, self.SCHOOL)

    def test_logs_from_another_address_are_ignored(self):
        other = "0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0"
        with self.assertRaises(ChainUnavailable):
            measure._one_event(self.manager, "ConsentGranted", receipt_with(self.granted(self.SCHOOL, address=other)))
        # two matching logs are not "one event" either
        with self.assertRaises(ChainUnavailable):
            measure._one_event(self.manager, "ConsentGranted", receipt_with(self.granted(self.SCHOOL), self.granted(self.SCHOOL, index=1)))


def receipt_with(*logs):
    return dict(receipt(90_000), logs=list(logs))


@unittest.skipIf(Web3 is None, "web3 is not installed")
class RequestTests(unittest.TestCase):
    """The real measure._request: requestAccess through app.chain, the receipt, then the decoded event."""
    GUARDIAN = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    SCHOOL = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"

    def setUp(self):
        for name, value in (("_error_names", {}), ("_default_errors_loaded", True)):
            patcher = mock.patch.object(chain, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = mock_contract()
        self.client = self.manager.w3

    def request(self, *logs, status=1):
        self.client.eth.wait_for_transaction_receipt.return_value = raw_receipt(status=status, logs=logs)
        return measure._request(self.client, self.manager, self.SCHOOL, self.GUARDIAN, b"\x07" * 32)

    def test_matching_event_is_measured(self):
        measured = self.request(access_log(owner=self.GUARDIAN, requester=self.SCHOOL, allowed=False, reason=4))
        self.assertEqual((measured["status"], measured["gas_used"]), ("ok", 21000))
        self.assertEqual((measured["event"]["reason"], measured["event"]["allowed"]), (Reason.NO_CONSENT, False))
        self.assertIsNotNone(measured["event_seconds"])
        # scope 1 from the requester's own account, with the observed hash as given
        self.manager.functions.requestAccess.assert_called_once_with(self.GUARDIAN, measure.SCOPE, b"\x07" * 32)
        self.assertEqual(int(self.manager.functions.requestAccess.call_args.args[1]), 1)
        self.manager.functions.requestAccess.return_value.transact.assert_called_once_with({"from": self.SCHOOL})

    def test_event_for_another_owner_requester_scope_or_contract_is_no_event(self):
        other = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
        for label, log in (
            ("owner", access_log(owner=self.SCHOOL, requester=self.SCHOOL)),
            ("requester", access_log(owner=self.GUARDIAN, requester=self.GUARDIAN)),
            ("scope", access_log(owner=self.GUARDIAN, requester=self.SCHOOL, scope=2)),
            ("emitter", access_log(address=other, owner=self.GUARDIAN, requester=self.SCHOOL)),
        ):
            with self.subTest(label):
                measured = self.request(log)
                self.assertEqual((measured["status"], measured["error"]), ("no_event", "expected event missing"))
                self.assertIsNone(measured["gas_used"])
                self.assertIsNone(measured["event"])

    def test_reverted_request_is_a_rejected_sample(self):
        measured = self.request(status=0)
        self.assertEqual((measured["status"], measured["error"]), ("rejected", "Reverted"))


class RewardCheckTests(unittest.TestCase):
    """_check_rewards after a run: one unit per requester, all to the guardian, every tuple marked rewarded."""
    REQUESTERS = ["requester 1", "requester 2"]

    def check(self, guardian=2, supply=2, requester_balance=0, rewarded=True):
        balances = {"guardian": guardian, **{requester: requester_balance for requester in self.REQUESTERS}}
        asked = []

        def has_received_reward(owner, requester, scope):
            asked.append((owner, requester, scope))
            return lambda: rewarded if requester == self.REQUESTERS[-1] else True

        token = SimpleNamespace(functions=SimpleNamespace(totalSupply=lambda: (lambda: supply)))
        manager = SimpleNamespace(functions=SimpleNamespace(hasReceivedReward=has_received_reward))
        with mock.patch.object(chain, "call_view", lambda call: call()), \
                mock.patch.object(chain, "get_reward_balance", lambda token, account: balances[account]):
            measure._check_rewards(token, manager, "guardian", self.REQUESTERS, "run N=2")
        return asked

    def test_one_reward_per_requester_passes(self):
        asked = self.check()
        self.assertEqual(asked, [("guardian", requester, measure.SCOPE) for requester in self.REQUESTERS])

    def test_every_mismatch_stops_the_run(self):
        for label, arguments in (
            ("guardian short", {"guardian": 1}), ("guardian over", {"guardian": 3}), ("supply", {"supply": 3}),
            ("requester rewarded", {"requester_balance": 1}), ("tuple not marked", {"rewarded": False}),
        ):
            with self.subTest(label):
                with self.assertRaises(measure.MeasureError) as caught:
                    self.check(**arguments)
                self.assertTrue(str(caught.exception).startswith("run N=2: rewards do not match one per requester"))


class WarmUpTests(unittest.TestCase):
    def test_estimates_the_registry_deploy_and_sends_nothing(self):
        client = mock.MagicMock()
        deploy = SimpleNamespace(load_artifact=mock.Mock(return_value={"abi": [], "bytecode": "0x00"}))
        measure._warm_up(client, deploy, "deployer", "clinic")
        factory = client.eth.contract.return_value
        factory.constructor.assert_called_once_with("clinic")
        factory.constructor.return_value.estimate_gas.assert_called_once_with({"from": "deployer"})
        factory.constructor.return_value.transact.assert_not_called()
        self.assertEqual([call.args[0] for call in deploy.load_artifact.call_args_list], ["IdentityRegistry", *measure.DEPLOY_ORDER])

    def test_node_failure_carries_no_details(self):
        client = mock.MagicMock()
        client.eth.contract.return_value.constructor.return_value.estimate_gas.side_effect = OSError("http://secret:1")
        deploy = SimpleNamespace(load_artifact=lambda name: {"abi": [], "bytecode": "0x00"})
        with self.assertRaises(ChainUnavailable) as caught:
            measure._warm_up(client, deploy, "deployer", "clinic")
        self.assertEqual(caught.exception.args, ("RPC request failed",))


if __name__ == "__main__":
    unittest.main()
