# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Endpoint tests for the web UI (ui/server.py and ui/actions.py), over real HTTP on a free port of 127.0.0.1.
No node: FakeNode from tests/test_demo.py stands in for app.chain (plus block times), and deploy_local.run
is mocked. The same pages were also clicked through on a real node.
"""
import base64
import contextlib
import http.client
import io
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from app import disclosure, records
from app.models import (
    Scope, ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, ArtifactUnavailable, TransactionPending,
)
from scripts import deploy_local
from tests.fake_chain import install, prepare_demo, DAY
from tests.support import temp_runtime
from tests.test_demo import FakeNode, RECORD_ONLY_TEXT
from ui import actions, server

NODE_DOWN = "unavailable: local node not reachable or wrong chain"
NO_DEPLOYMENT = "unavailable: no deployment for this node: run python -m scripts.deploy_local --reset"
NO_ARTIFACTS = "unavailable: compiled contracts not found: run npm run compile first"
NO_WEB3 = "unavailable: web3 not installed: run python -m pip install -r requirements.txt with the venv's Python"


class UITestCase(unittest.TestCase):
    """A temporary runtime folder, a settings file, FakeNode in app.chain, and the server on a free port."""

    def setUp(self):
        self.root, self.settings = temp_runtime(self)
        self.settings.update(rpc_url="http://127.0.0.1:8545", expected_chain_id=31337)
        self.settings_file = self.root / "settings.json"
        self.settings_file.write_text(json.dumps(self.settings))
        self.fake = FakeNode(self.settings)
        install(self, self.fake)
        patcher = mock.patch.object(deploy_local, "run", return_value=self.deployed())
        self.deploy = patcher.start()
        self.addCleanup(patcher.stop)
        # started after install, so it stops (cleanups run last in, first out) while the fake is still in place
        self.server = server.UIServer(self.settings, self.settings_file, port=0)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        # every JSON body the server sent except the guardian's own record; checked after each test, while
        # the temporary files still exist
        self.answers = []
        self.addCleanup(self.assert_nothing_leaked)

    def assert_nothing_leaked(self):
        forbidden = [*RECORD_ONLY_TEXT, str(self.root), "/Users/somebody", "Traceback"]
        for path in Path(self.settings["identity_salt_directory"]).glob("*.json"):
            salt = records.load_salt(path)
            forbidden += [base64.b64encode(salt).decode(), salt.hex()]
        for path in Path(self.settings["identity_directory"]).glob("*.json"):
            forbidden += json.loads(path.read_text()).values()
        text = "".join(self.answers)
        for value in forbidden:
            self.assertNotIn(value, text)

    def deployed(self):
        # the shape deploy_local.run returns (deploy_all), and a deployment file with a new deploy block each time
        names = ("IdentityRegistry", "ConsentRewardToken", "ConsentManager", "setMinterOnce")
        self.deploys = getattr(self, "deploys", 0) + 1
        block = {"number": self.deploys, "hash": f"0x{self.deploys:064x}"}
        path = Path(self.settings["deployment_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"deploy_block": block}))
        return {
            "deploy_block": block,
            # the addresses the fake node's contracts have
            "addresses": {name: self.fake.contracts[name].address for name in names[:3]},
            "receipts": {name: {"transaction_hash": f"0x{index:064x}"} for index, name in enumerate(names, 1)},
        }

    def request(self, method, path, body=None, headers=None):
        """Send one request; returns (status, headers, raw body)."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = None
            sent = dict(headers or {})
            if body is not None:
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                sent.setdefault("Content-Type", "application/json")
            connection.request(method, path, body=payload, headers=sent)
            response = connection.getresponse()
            return response.status, {name.lower(): value for name, value in response.getheaders()}, response.read()
        finally:
            connection.close()

    def call(self, method, path, body=None, headers=None, status=200):
        code, _, raw = self.request(method, path, body, headers)
        answer = json.loads(raw)
        self.assertEqual(code, status, answer)
        if urlsplit(path).path != "/api/record":
            self.answers.append(raw.decode())
        return answer

    def get_state(self):
        return self.call("GET", "/api/state")

    def post(self, path, body=None, status=200):
        return self.call("POST", path, body if body is not None else {}, status=status)

    def ready(self):
        """Local files, the three registrations and the clinic attestation, as after setup."""
        return prepare_demo(self.fake, self.settings)

    def grant(self, requester, scope, days=30):
        return self.post("/api/grant", {"role": "guardian", "requester": requester, "scope": scope, "days": days})


class StaticTests(UITestCase):
    def test_the_page_comes_with_the_security_headers(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertTrue(body.startswith(b"<!doctype html>"))

    def test_every_kind_of_response_carries_the_security_headers(self):
        for method, path, headers in (
            ("GET", "/api/state", {}), ("GET", "/static/app.js", {}), ("GET", "/nothing", {}), ("PUT", "/api/setup", {}),
            ("GET", "/api/state", {"Host": "evil.example"}), ("GET", "/api/record?role=school", {}),
        ):
            _, sent, _ = self.request(method, path, headers=headers)
            for name, value in server.SECURITY_HEADERS:
                self.assertEqual(sent[name.lower()], value, (method, path, name))

    def test_only_the_listed_files_are_served(self):
        self.assertEqual(self.request("GET", "/static/app.js")[0], 200)
        self.assertEqual(self.request("GET", "/static/app.css")[1]["content-type"], "text/css; charset=utf-8")
        for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/ui/server.py", "/static/", "/static/missing.js",
                     "/settings.json", "/api/nothing"):
            self.assertEqual(self.request("GET", path)[0], 404, path)


class RequestGuardTests(UITestCase):
    def test_127_0_0_1_and_localhost_are_both_accepted(self):
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"LOCALHOST:{self.port}"):
            self.assertEqual(self.request("GET", "/api/state", headers={"Host": host})[0], 200, host)

    def test_another_host_name_is_refused(self):
        # a DNS-rebinding page reaches 127.0.0.1 under its own name
        for host in (f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}"):
            status, _, body = self.request("GET", "/api/state", headers={"Host": host})
            self.assertEqual(status, 403, host)
            self.assertNotIn(b"registrations", body)
        self.assertEqual(self.fake.calls, [])

    def test_a_post_from_another_site_is_refused_before_any_chain_call(self):
        self.ready()
        status, _, _ = self.request("POST", "/api/register", {"role": "guardian"}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        # a plain form post cannot send JSON
        status, _, _ = self.request(
            "POST", "/api/register", b"role=guardian", {"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 415)
        self.assertEqual(self.fake.calls, [])
        for origin in (f"http://127.0.0.1:{self.port + 1}", "http://localhost", f"https://localhost:{self.port}"):
            status, _, _ = self.request("POST", "/api/register", {"role": "guardian"}, {"Origin": origin})
            self.assertEqual(status, 403, origin)
        self.assertEqual(self.fake.calls, [])
        # the page itself sends its own origin
        for host in ("localhost", "127.0.0.1"):
            answer = self.call("POST", "/api/school-check", {}, {"Origin": f"http://{host}:{self.port}"})
            self.assertEqual(answer["status"], "denied")

    def test_port_80_also_takes_the_host_without_a_port(self):
        # browsers leave the default port out of Host and Origin
        self.assertEqual(server.allowed_hosts(8000), {"127.0.0.1:8000", "localhost:8000"})
        self.assertEqual(server.allowed_hosts(80), {"127.0.0.1:80", "localhost:80", "127.0.0.1", "localhost"})

    def test_bad_bodies_and_paths(self):
        self.assertEqual(self.request("POST", "/api/setup", b"[1, 2]")[0], 400)
        self.assertEqual(self.request("POST", "/api/setup", b"{not json")[0], 400)
        self.assertEqual(self.request("POST", "/api/setup", b"{}" + b" " * server.MAX_BODY_BYTES)[0], 400)
        self.assertEqual(self.request("POST", "/api/nothing", {})[0], 404)
        self.assertEqual(self.request("PUT", "/api/setup", {})[0], 501)
        self.assertFalse(Path(self.settings["vaccination_file"]).exists())


class StateTests(UITestCase):
    def test_a_fresh_node_before_setup_and_deploy(self):
        self.fake.failures["load_contract"] = DeploymentUnavailable("deployment unavailable")
        state = self.get_state()
        self.assertTrue(state["node"]["reachable"])
        self.assertEqual(state["node"]["chain_id"], 31337)
        self.assertEqual(state["node"]["time"], self.fake.now)
        self.assertEqual(state["accounts"]["guardian"], self.fake.addresses["guardian"])
        self.assertFalse(state["deployment"]["deployed"])
        self.assertEqual(state["deployment"]["message"], NO_DEPLOYMENT)
        self.assertFalse(state["local"]["ready"])
        self.assertEqual(state["local"]["message"], "unavailable: identity unavailable")
        self.assertEqual((state["registrations"], state["record"], state["audit"]), ({}, None, []))

    def test_node_down_artifacts_missing_and_no_web3_show_their_messages(self):
        for failure, name, key, expected in (
            (ChainUnavailable("RPC connection failed http://127.0.0.1:8545"), "connect", "node", NODE_DOWN),
            (Web3NotInstalled("web3 not installed"), "connect", "node", NO_WEB3),
            (ArtifactUnavailable("/Users/somebody/artifacts"), "load_contract", "deployment", NO_ARTIFACTS),
        ):
            self.fake.failures[name] = failure
            state = self.get_state()
            self.assertEqual(state[key]["message"], expected)
            self.assertFalse(state["deployment"]["deployed"])
        self.assertNotIn("/Users/somebody", "".join(self.answers))

    def test_a_node_that_stops_during_the_snapshot_shows_no_partial_state(self):
        self.ready()
        self.fake.failures["get_consent"] = ChainUnavailable("RPC request failed")
        state = self.get_state()
        self.assertFalse(state["node"]["reachable"])
        self.assertEqual(state["node"]["message"], NODE_DOWN)
        self.assertEqual((state["deployment"]["deployed"], state["consents"], state["registrations"]), (False, [], {}))

    def test_after_setup_registration_and_attestation(self):
        commitment = self.ready()
        state = self.get_state()
        self.assertTrue(state["local"]["ready"])
        self.assertEqual(state["local"]["commitment"], f"0x{commitment.hex()}")
        self.assertEqual(state["record"], {"attested": True, "onchain": f"0x{commitment.hex()}", "matches": True})
        for label in ("guardian", "school", "doctor"):
            registration = state["registrations"][label]
            self.assertEqual((registration["registered"], registration["matches"]), (True, True), label)
            self.assertEqual(registration["identity_hash"], state["local"]["identity_hashes"][label])
        self.assertEqual(state["deployment"]["contracts"]["ConsentManager"], self.fake.contracts["ConsentManager"].address)
        self.assertEqual(
            [(cell["requester"], cell["scope"], cell["status"]) for cell in state["consents"]],
            [("school", "MEASLES_STATUS", "none"), ("school", "VACCINATION_SCHEDULE", "none"),
             ("doctor", "MEASLES_STATUS", "none"), ("doctor", "VACCINATION_SCHEDULE", "none")],
        )
        self.assertEqual(state["rewards"], {"deployer": 0, "clinic": 0, "guardian": 0, "school": 0, "doctor": 0})

    def test_one_broken_local_file_does_not_hide_the_others(self):
        commitment = self.ready()
        Path(records.identity_paths(self.settings, "doctor")[1]).unlink()
        state = self.get_state()
        self.assertFalse(state["local"]["ready"])
        self.assertEqual(state["local"]["message"], "unavailable: salt unavailable")
        self.assertEqual(set(state["local"]["identity_hashes"]), {"guardian", "school"})
        self.assertEqual(state["local"]["commitment"], f"0x{commitment.hex()}")
        self.assertTrue(state["record"]["matches"])
        self.assertIsNone(state["registrations"]["doctor"]["matches"])

    def test_consent_cells_follow_the_contracts_order(self):
        self.ready()
        self.grant("school", "MEASLES_STATUS", 1)
        self.grant("doctor", "VACCINATION_SCHEDULE", 2)
        self.grant("doctor", "MEASLES_STATUS", 1)
        self.post("/api/revoke", {"role": "guardian", "requester": "doctor", "scope": "MEASLES_STATUS"})
        cells = {(cell["requester"], cell["scope"]): cell for cell in self.get_state()["consents"]}
        self.assertEqual(cells["school", "MEASLES_STATUS"]["status"], "active")
        self.assertTrue(cells["school", "MEASLES_STATUS"]["expires_text"].endswith(" UTC"))
        # past both 1-day grants: the school's has expired; the doctor's is past its expiry too, but revoked
        # comes first, as in the contract
        self.fake.next_time = cells["doctor", "MEASLES_STATUS"]["expires_at"]
        self.fake.mine()
        self.assertGreaterEqual(self.fake.now, cells["doctor", "MEASLES_STATUS"]["expires_at"])
        cells = {(cell["requester"], cell["scope"]): cell["status"] for cell in self.get_state()["consents"]}
        self.assertEqual(cells, {
            ("school", "MEASLES_STATUS"): "expired", ("school", "VACCINATION_SCHEDULE"): "none",
            ("doctor", "MEASLES_STATUS"): "revoked", ("doctor", "VACCINATION_SCHEDULE"): "active",
        })

    def test_audit_is_newest_first_with_labels_and_unsupported_scopes(self):
        self.ready()
        self.post("/api/school-check")
        self.fake.request_access("ConsentManager", requester=self.fake.addresses["doctor"],
                                 owner=self.fake.addresses["guardian"], scope=3, observed_hash=bytes(32))
        audit = self.get_state()["audit"]
        self.assertEqual(
            [(event["requester"], event["scope"], event["allowed"], event["reason"]) for event in audit],
            [("doctor", "scope 3 (unsupported)", False, "UNSUPPORTED_SCOPE"), ("school", "MEASLES_STATUS", False, "NO_CONSENT")],
        )
        self.assertTrue(audit[0]["tx"].startswith("0x"))
        self.assertTrue(audit[0]["time_text"].endswith(" UTC"))


class ActionTests(UITestCase):
    def test_setup_creates_the_files_and_shows_no_paths(self):
        self.assertEqual(self.post("/api/setup")["message"], "setup: created 8 local files")
        self.assertEqual(self.post("/api/setup")["message"], "setup: every local file already exists, nothing changed")
        self.assertNotIn(str(self.root), "".join(self.answers))

    def test_deploy_runs_deploy_local_with_reset(self):
        answer = self.post("/api/deploy")
        self.deploy.assert_called_once_with(self.settings_file, reset=True)
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(answer["details"]["contracts"]["IdentityRegistry"], self.fake.contracts["IdentityRegistry"].address)
        self.assertEqual(set(answer["details"]["transactions"]), {"IdentityRegistry", "ConsentRewardToken", "ConsentManager", "setMinterOnce"})
        self.assertEqual(answer["details"]["deploy_block"], f"0x{1:064x}")
        self.assertEqual(self.get_state()["deployment"]["deploy_block"], f"0x{1:064x}")

    def test_deploy_errors_show_their_first_line_only(self):
        self.deploy.side_effect = deploy_local.DeployError(
            "IdentityRegistry deploy failed: local node not reachable\nnothing was saved: <data_root>/deployment.json was not created"
        )
        answer = self.post("/api/deploy")
        self.assertEqual(answer["status"], "unavailable")
        self.assertEqual(answer["message"], "unavailable: IdentityRegistry deploy failed: local node not reachable")
        self.deploy.side_effect = deploy_local.DeployError(disclosure.NO_ARTIFACTS_MESSAGE)
        self.assertEqual(self.post("/api/deploy")["message"], NO_ARTIFACTS)
        # the few texts that name a file get fixed ones
        for text, expected in (
            ("could not write <data_root>/deployment.json (deployment_file); the contracts above are deployed but not saved",
             "unavailable: deployed, but the deployment file could not be saved: run python -m scripts.deploy_local --reset"),
            ("no settings file at settings.json: copy config/settings.example.json to config/settings.json",
             "unavailable: the settings file is missing or not valid JSON"),
            ("settings.json is not valid JSON", "unavailable: the settings file is missing or not valid JSON"),
        ):
            self.deploy.side_effect = deploy_local.DeployError(text)
            self.assertEqual(self.post("/api/deploy")["message"], expected)

    def test_register_each_role_once(self):
        self.post("/api/setup")
        for label in ("guardian", "school", "doctor"):
            answer = self.post("/api/register", {"role": label})
            self.assertEqual(answer["status"], "ok", label)
            self.assertTrue(answer["tx"].startswith("0x"))
        self.assertEqual(self.post("/api/register", {"role": "school"})["message"], "rejected: AlreadyRegistered")
        self.fake.calls.clear()
        for role in ("clinic", "deployer", "admin", None, 3):
            self.assertEqual(self.post("/api/register", {"role": role}, status=400)["status"], "invalid")
        self.assertEqual(self.fake.calls, [])

    def test_only_the_clinic_can_attest(self):
        self.post("/api/setup")
        for label in ("guardian", "school", "doctor"):
            self.post("/api/register", {"role": label})
        for role in ("guardian", "school", "doctor", "deployer"):
            self.assertEqual(self.post("/api/attest", {"role": role})["message"], "rejected: NotTrustedClinic", role)
        answer = self.post("/api/attest", {"role": "clinic"})
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(self.post("/api/attest", {"role": "clinic"})["message"], "rejected: EvidenceAlreadyRegistered")

    def test_grant_values_are_checked_before_any_chain_call(self):
        self.ready()
        for body in (
            {"requester": "school", "scope": "MEASLES_STATUS", "days": 0},
            {"requester": "school", "scope": "MEASLES_STATUS", "days": 366},
            {"requester": "school", "scope": "MEASLES_STATUS", "days": "30"},
            {"requester": "school", "scope": "MEASLES_STATUS", "days": True},
            {"requester": "school", "scope": "MEASLES_STATUS", "days": 2.5},
            {"requester": "school", "scope": "MEASLES_STATUS"},
            {"requester": "school", "scope": "ALL", "days": 30},
            {"requester": "school", "scope": 1, "days": 30},
            {"requester": "guardian", "scope": "MEASLES_STATUS", "days": 30},
            {"requester": "teacher", "scope": "MEASLES_STATUS", "days": 30},
        ):
            answer = self.post("/api/grant", dict(body, role="guardian"), status=400)
            self.assertEqual(answer["status"], "invalid", body)
        self.assertEqual(self.post("/api/grant", {"role": "guardian", "requester": "school", "scope": "MEASLES_STATUS",
                                                 "days": 0}, status=400)["message"], "days must be a whole number from 1 to 365")
        self.assertEqual(self.fake.calls, [])

    def test_first_grant_rewards_once_and_revoke(self):
        self.ready()
        answer = self.grant("school", "MEASLES_STATUS")
        self.assertEqual(answer["message"], "granted school MEASLES_STATUS for 30 days; reward balance of guardian: 0 -> 1")
        self.assertEqual((answer["details"]["reward_before"], answer["details"]["reward_after"]), (0, 1))
        self.assertEqual(answer["details"]["expires_at"], self.fake.now + 30 * DAY)
        self.assertEqual(self.grant("school", "MEASLES_STATUS")["message"], "rejected: ConsentStillActive")
        revoke = {"role": "guardian", "requester": "school", "scope": "MEASLES_STATUS"}
        self.assertEqual(self.post("/api/revoke", revoke)["message"], "revoked school MEASLES_STATUS")
        self.assertEqual(self.post("/api/revoke", revoke)["message"],
                         "revoked school MEASLES_STATUS: it was already revoked, nothing changed")
        self.assertIn("for 1 day; reward balance of guardian: 1 -> 1", self.grant("school", "MEASLES_STATUS", 1)["message"])
        self.assertEqual(self.post("/api/revoke", dict(revoke, requester="doctor"))["message"], "rejected: NoConsentToRevoke")

    def test_school_is_denied_then_gets_the_status_only(self):
        self.ready()
        answer = self.post("/api/school-check")
        self.assertEqual((answer["status"], answer["reason"], answer["fields"]), ("denied", "NO_CONSENT", {}))
        self.assertTrue(answer["message"].startswith("denied: NO_CONSENT (logged on-chain in 0x"))
        self.assertEqual(answer["tx"], self.fake.events[-1]["transaction_hash"])
        self.grant("school", "MEASLES_STATUS")
        answer = self.post("/api/school-check")
        self.assertEqual((answer["status"], answer["reason"]), ("allowed", "ALLOWED"))
        self.assertEqual(answer["fields"], {"measles_status": "verified"})
        self.assertEqual(answer["message"], "allowed: measles status verified")
        # the school's scope does not open the doctor's
        self.assertEqual(self.post("/api/doctor-view")["reason"], "NO_CONSENT")

    def test_doctor_gets_vaccine_and_date_only(self):
        self.ready()
        self.grant("doctor", "VACCINATION_SCHEDULE")
        answer = self.post("/api/doctor-view")
        self.assertEqual(answer["status"], "allowed")
        self.assertEqual(answer["fields"], {"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]})
        self.assertEqual(answer["message"], "allowed: MMR on 2026-03-12")
        self.assertEqual(self.fake.calls_to("request_access")[0]["requester"], self.fake.addresses["doctor"])

    def test_a_missing_salt_is_unavailable_and_shows_no_event_reason(self):
        self.ready()
        self.grant("school", "MEASLES_STATUS")
        Path(self.settings["vaccination_salt_file"]).unlink()
        answer = self.post("/api/school-check")
        # the logged event says HASH_MISMATCH (Python sent the zero hash), which is not a clinical answer
        self.assertEqual(self.fake.events[-1]["reason"], 7)
        self.assertEqual((answer["status"], answer["reason"], answer["fields"]), ("unavailable", "", {}))
        self.assertEqual(answer["message"], disclosure.RECORD_UNAVAILABLE_MESSAGE)
        self.assertTrue(answer["tx"])

    def test_node_down_requests_say_nothing_released(self):
        self.ready()
        for path in ("/api/school-check", "/api/doctor-view"):
            self.fake.failures["connect"] = ChainUnavailable("RPC connection failed")
            answer = self.post(path)
            self.assertEqual((answer["status"], answer["reason"], answer["tx"], answer["fields"]), ("unavailable", "", "", {}))
            self.assertEqual(answer["message"], disclosure.CHAIN_UNAVAILABLE_MESSAGE)
        self.fake.failures["request_access"] = ChainUnavailable("access event unavailable")
        self.assertEqual(self.post("/api/school-check")["message"], disclosure.CHAIN_UNAVAILABLE_MESSAGE)
        self.fake.failures["connect"] = Web3NotInstalled("web3 not installed")
        self.assertEqual(self.post("/api/school-check")["message"], NO_WEB3)
        self.assertEqual(self.fake.calls_to("request_access")[-1]["requester"], self.fake.addresses["school"])

    def test_pending_requests_release_nothing(self):
        self.ready()
        self.grant("school", "MEASLES_STATUS")
        self.grant("doctor", "VACCINATION_SCHEDULE")
        for path in ("/api/school-check", "/api/doctor-view"):
            self.fake.failures["request_access"] = TransactionPending("transaction receipt pending")
            answer = self.post(path)
            self.assertEqual(
                (answer["status"], answer["reason"], answer["fields"], answer["message"]),
                ("pending", "", {}, "pending: not confirmed, nothing released"),
            )

    def test_node_down_and_no_deployment_in_other_actions(self):
        self.ready()
        self.fake.failures["connect"] = ChainUnavailable("RPC connection failed")
        self.assertEqual(self.grant("school", "MEASLES_STATUS")["message"], NODE_DOWN)
        self.fake.failures["load_contract"] = DeploymentUnavailable("deployment unavailable")
        answer = self.post("/api/school-check")
        self.assertEqual((answer["status"], answer["message"]), ("unavailable", NO_DEPLOYMENT))
        self.assertEqual(self.fake.calls_to("request_access"), [])

    def test_pending_and_bugs_show_no_exception_text(self):
        self.ready()
        self.fake.failures["grant_consent"] = TransactionPending("transaction receipt pending")
        self.assertEqual(self.grant("school", "MEASLES_STATUS")["message"], "pending: not confirmed")
        self.fake.failures["grant_consent"] = RuntimeError("ABC123-DEMO /Users/somebody child-demo-001")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            answer = self.grant("school", "MEASLES_STATUS")
        self.assertEqual((answer["status"], answer["message"]), ("failed", "failed: RuntimeError"))
        self.assertEqual(errors.getvalue(), "ui: failed: RuntimeError\n")

    def test_a_bug_in_the_snapshot_is_a_500_with_the_type_only(self):
        with mock.patch.object(actions, "_local_state", side_effect=KeyError("/Users/somebody/salt")), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            answer = self.call("GET", "/api/state", status=500)
        self.assertEqual(answer["message"], "failed: KeyError")
        self.assertEqual(errors.getvalue(), "ui: failed: KeyError\n")


class RecordTests(UITestCase):
    def test_only_the_guardian_view_gets_the_card(self):
        for role in ("school", "doctor", "clinic", "deployer", ""):
            answer = self.call("GET", f"/api/record?role={role}", status=403)
            self.assertEqual(answer["message"], "only the guardian's own view shows the local record")
        self.assertEqual(self.call("GET", "/api/record?role=guardian")["message"], "unavailable: local record unavailable")
        self.ready()
        _, _, raw = self.request("GET", "/api/record?role=guardian")
        answer = json.loads(raw)
        self.assertEqual(answer["details"]["card"]["vaccinations"][0]["batch"], "ABC123-DEMO")
        self.assertEqual(answer["fields"], {})
        # the parsed card and nothing else: no raw bytes, salt or path
        self.assertEqual(set(answer["details"]), {"card"})
        self.assertEqual(set(answer["details"]["card"]), {"child_id", "vaccinations"})
        salt = records.load_salt(records.settings_path(self.settings, "vaccination_salt_file"))
        for value in (base64.b64encode(salt).decode(), salt.hex(), str(self.root), "salt"):
            self.assertNotIn(value, raw.decode())

    def test_no_other_answer_leaks_the_card_the_salts_or_paths(self):
        self.ready()
        self.get_state()
        for requester, scope in (("school", "MEASLES_STATUS"), ("doctor", "VACCINATION_SCHEDULE")):
            self.grant(requester, scope)
        self.post("/api/school-check")
        self.post("/api/doctor-view")
        self.post("/api/revoke", {"role": "guardian", "requester": "school", "scope": "MEASLES_STATUS"})
        self.post("/api/school-check")
        self.post("/api/setup")
        self.post("/api/register", {"role": "guardian"})
        self.get_state()
        salts = [
            base64.b64encode(records.load_salt(path)).decode()
            for path in Path(self.settings["identity_salt_directory"]).glob("*.json")
        ]
        text = "".join(self.answers)
        for value in (*RECORD_ONLY_TEXT, *salts, str(self.root), "Traceback", '"covers"'):
            self.assertNotIn(value, text)


class MainTests(unittest.TestCase):
    def run_main(self, *argv):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as stop:
            server.main(list(argv))
        return stop.exception.code, errors.getvalue()

    def test_missing_or_bad_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            code, text = self.run_main("--settings", str(path))
            self.assertEqual(code, 1)
            self.assertIn("copy config/settings.example.json to config/settings.json", text)
            self.assertNotIn(folder, text)
            path.write_text("{not json")
            self.assertEqual(self.run_main("--settings", str(path))[1].strip(), "the settings file is not valid JSON")
            path.write_text(json.dumps({"actor_account_indices": {"guardian": 2}}))
            self.assertIn("deployer, clinic, guardian, school and doctor", self.run_main("--settings", str(path))[1])

    def test_a_port_in_use(self):
        with tempfile.TemporaryDirectory() as folder, socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"actor_account_indices": dict.fromkeys(server.ACTOR_LABELS, 0)}))
            code, text = self.run_main("--settings", str(path), "--port", str(taken.getsockname()[1]))
        self.assertEqual(code, 1)
        self.assertIn("pick another with --port", text)


if __name__ == "__main__":
    unittest.main()
