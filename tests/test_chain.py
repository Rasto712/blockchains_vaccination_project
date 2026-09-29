# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Offline tests for the real app/chain.py. Contracts, clients and receipts are mocked or built by hand,
so no node is needed; the web3 parts are skipped when web3 is not installed.
Covers the one error mapping (revert names, panics, send-time reverts, connection errors, timeouts, a
node that stops answering after the send, and bugs that must not look like a node failure), argument
handling, the AccessAttempt checks in request_access, load_contract's provenance checks (the deploy block
included), block_hash and get_consent. The last class checks that tests/fake_chain.py keeps the real
signatures.
"""
import http.server
import inspect
import io
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

from app import chain
from app import main
from app.models import (
    Scope, Reason, ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, ArtifactUnavailable, TransactionRejected,
    TransactionPending,
)
from tests.fake_chain import FakeChain, FAKED

try:
    import requests
    from hexbytes import HexBytes
    from web3 import Web3
    from web3.exceptions import (
        ContractCustomError, ContractLogicError, ContractPanicError, TimeExhausted, Web3RPCError, BadFunctionCallOutput,
        BlockNotFound,
    )
except ImportError:
    Web3 = None

GUARDIAN = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
SCHOOL = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
CLINIC = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
MANAGER = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
OTHER_CONTRACT = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
TX_HASH = "0x" + "aa" * 32
BLOCK_HASH = "0x" + "b1" * 32
ERRORS = ("NotTrustedClinic", "AlreadyRegistered", "EvidenceAlreadyRegistered", "ZeroHash", "UnsupportedScope", "InvalidDuration", "NotMinter")
# selectors as Hardhat reports them, so the keccak and signature spelling are pinned
KNOWN_SELECTORS = {
    "0x82936d47": "NotTrustedClinic", "0x3a81d6fc": "AlreadyRegistered",
    "0x822289e8": "EvidenceAlreadyRegistered", "0xf1ae58d5": "ZeroHash",
}
ACCESS_ATTEMPT_ABI = {
    "anonymous": False, "name": "AccessAttempt", "type": "event",
    "inputs": [
        {"indexed": True, "name": "owner", "type": "address"},
        {"indexed": True, "name": "requester", "type": "address"},
        {"indexed": True, "name": "scope", "type": "uint8"},
        {"indexed": False, "name": "timestamp", "type": "uint256"},
        {"indexed": False, "name": "allowed", "type": "bool"},
        {"indexed": False, "name": "reason", "type": "uint8"},
    ],
}

CHECK_ACCESS_ABI = {
    "type": "function", "name": "checkAccess", "stateMutability": "view",
    "inputs": [{"name": "owner", "type": "address"}, {"name": "requester", "type": "address"}, {"name": "scope", "type": "uint8"}],
    "outputs": [{"name": "allowed", "type": "bool"}, {"name": "reason", "type": "uint8"}],
}


def error_abi(*names):
    return [{"type": "error", "name": name, "inputs": []} for name in names]


def selector(name):
    return Web3.to_hex(Web3.keccak(text=f"{name}()")[:4])


def custom_error(name):
    data = selector(name)
    return ContractCustomError(data, data=data)


def send_time_revert(name):
    # what Hardhat 3 answers eth_sendTransaction with when the mined transaction reverts
    data = selector(name)
    return Web3RPCError("rpc error", rpc_response={
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": 3, "message": f"VM Exception while processing transaction: reverted with custom error '{name}()'", "data": data},
    })


def raw_receipt(status=1, logs=(), contract_address=None):
    return {
        "status": status, "transactionHash": HexBytes(TX_HASH), "gasUsed": 21000, "blockNumber": 7,
        "logs": list(logs), "contractAddress": contract_address,
    }


def access_log(address=MANAGER, owner=GUARDIAN, requester=SCHOOL, scope=1, allowed=True, reason=0, log_index=0):
    def topic(value):
        return HexBytes(bytes(12) + bytes.fromhex(value[2:]))

    return {
        "address": address,
        "topics": [
            Web3.keccak(text="AccessAttempt(address,address,uint8,uint256,bool,uint8)"),
            topic(owner), topic(requester), HexBytes(scope.to_bytes(32, "big")),
        ],
        "data": HexBytes(Web3().codec.encode(["uint256", "bool", "uint8"], [1_700_000_000, allowed, reason])),
        "logIndex": log_index, "transactionIndex": 0, "transactionHash": HexBytes(TX_HASH),
        "blockHash": HexBytes("0x" + "bb" * 32), "blockNumber": 7,
    }


def mock_contract(address=MANAGER):
    """A contract whose functions are mocks, with a real events object so logs decode like on the node."""
    contract = mock.MagicMock()
    contract.address = address
    contract.events = Web3().eth.contract(address=address, abi=[ACCESS_ATTEMPT_ABI]).events
    return contract


@unittest.skipIf(Web3 is None, "web3 is not installed")
class ChainTestCase(unittest.TestCase):
    def setUp(self):
        # a private selector map, taught from a small ABI instead of the repo's artifacts
        for name, value in (("_error_names", {}), ("_default_errors_loaded", True)):
            patcher = mock.patch.object(chain, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        chain._learn_errors(error_abi(*ERRORS))
        self.contract = mock_contract()

    def transact_raises(self, function, error):
        getattr(self.contract.functions, function).return_value.transact.side_effect = error

    def call_raises(self, function, error):
        getattr(self.contract.functions, function).return_value.call.side_effect = error

    def receipt_is(self, receipt):
        self.contract.functions.requestAccess.return_value.transact.return_value = HexBytes(TX_HASH)
        self.contract.w3.eth.wait_for_transaction_receipt.return_value = receipt

    def rejected(self, action, *args, **kwargs):
        with self.assertRaises(TransactionRejected) as caught:
            action(*args, **kwargs)
        return caught.exception.args


class ErrorNameTests(ChainTestCase):
    def test_selectors_are_the_ones_hardhat_reports(self):
        for data, name in KNOWN_SELECTORS.items():
            self.assertEqual(chain.error_name(data), name)

    def test_error_arguments_are_part_of_the_signature(self):
        chain._learn_errors([{"type": "error", "name": "Custom", "inputs": [
            {"type": "uint256"}, {"type": "tuple[]", "components": [{"type": "address"}, {"type": "bytes32"}]},
        ]}])
        data = Web3.to_hex(Web3.keccak(text="Custom(uint256,(address,bytes32)[])")[:4]) + "00" * 64
        self.assertEqual(chain.error_name(data), "Custom")

    def test_panic_unknown_and_malformed_data(self):
        self.assertEqual(chain.error_name("0x4e487b71" + "00" * 31 + "11"), "Panic")
        self.assertEqual(chain.error_name("0x12345678"), "Reverted")
        for data in (None, "", "0x", "0x1234", 42, {"message": "no data"}):
            with self.subTest(data=data):
                self.assertEqual(chain.error_name(data), "Reverted")
        self.assertEqual(chain.error_name({"data": selector("ZeroHash")}), "ZeroHash")

    def test_default_artifacts_are_read_once_and_only_when_needed(self):
        with mock.patch.object(chain, "_error_names", {}), mock.patch.object(chain, "_default_errors_loaded", False), \
                mock.patch.object(chain, "read_artifact", side_effect=DeploymentUnavailable()) as read:
            self.assertEqual(chain.error_name(selector("NotMinter")), "Reverted")
            self.assertEqual(chain.error_name(selector("NotMinter")), "Reverted")
        self.assertEqual([call.args[0] for call in read.call_args_list], list(chain.CONTRACT_NAMES))


class ErrorMappingTests(ChainTestCase):
    def test_custom_error_becomes_its_solidity_name(self):
        self.transact_raises("registerVaccination", custom_error("NotTrustedClinic"))
        args = self.rejected(chain.register_vaccination, self.contract, clinic=GUARDIAN, guardian=GUARDIAN, record_hash=b"\x01" * 32)
        self.assertEqual(args, ("NotTrustedClinic",))
        self.transact_raises("registerUser", custom_error("AlreadyRegistered"))
        self.assertEqual(self.rejected(chain.register_user, self.contract, account=SCHOOL, identity_hash=b"\x01" * 32), ("AlreadyRegistered",))

    def test_a_second_attestation_is_not_an_authorisation_refusal(self):
        self.transact_raises("registerVaccination", custom_error("EvidenceAlreadyRegistered"))
        args = self.rejected(chain.register_vaccination, self.contract, clinic=CLINIC, guardian=GUARDIAN, record_hash=b"\x01" * 32)
        self.assertEqual(args, ("EvidenceAlreadyRegistered",))

    def test_panic_and_unknown_reverts(self):
        self.transact_raises("grantConsent", ContractPanicError("overflow", data="0x4e487b71" + "00" * 31 + "11"))
        self.assertEqual(self.rejected(chain.grant_consent, self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=30), ("Panic",))
        self.transact_raises("grantConsent", ContractLogicError("execution reverted", data=None))
        self.assertEqual(self.rejected(chain.grant_consent, self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=30), ("Reverted",))

    def test_send_time_revert_from_hardhat_is_decoded(self):
        self.transact_raises("registerUser", send_time_revert("ZeroHash"))
        self.assertEqual(self.rejected(chain.register_user, self.contract, account=SCHOOL, identity_hash=b"\x01" * 32), ("ZeroHash",))

    def test_token_error_bubbling_through_the_manager_is_named(self):
        self.transact_raises("grantConsent", custom_error("NotMinter"))
        self.assertEqual(self.rejected(chain.grant_consent, self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=30), ("NotMinter",))

    def test_other_rpc_errors_are_unavailable(self):
        error = Web3RPCError("rpc error", rpc_response={"error": {"code": -32000, "message": "nonce too low"}})
        self.transact_raises("registerUser", error)
        with self.assertRaises(ChainUnavailable):
            chain.register_user(self.contract, account=SCHOOL, identity_hash=b"\x01" * 32)

    def test_connection_errors_and_timeouts_are_unavailable_everywhere(self):
        failures = (requests.ConnectionError("http://127.0.0.1:8545 refused"), requests.ReadTimeout("read timed out"), OSError("reset"))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                contract = mock_contract()
                for function in ("registerUser", "registerVaccination", "grantConsent", "revokeConsent", "requestAccess"):
                    getattr(contract.functions, function).return_value.transact.side_effect = failure
                for function in ("getUserInfo", "checkAccess", "getConsent", "balanceOf"):
                    getattr(contract.functions, function).return_value.call.side_effect = failure
                client = mock.MagicMock()
                type(client.eth).accounts = mock.PropertyMock(side_effect=failure)
                client.eth.get_block.side_effect = failure
                calls = (
                    lambda: chain.select_account(client, "guardian", {"actor_account_indices": {"guardian": 2}}),
                    lambda: chain.register_user(contract, account=SCHOOL, identity_hash=b"\x01" * 32),
                    lambda: chain.register_vaccination(contract, clinic=CLINIC, guardian=GUARDIAN, record_hash=b"\x01" * 32),
                    lambda: chain.get_user_info(contract, account=GUARDIAN),
                    lambda: chain.grant_consent(contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=30),
                    lambda: chain.revoke_consent(contract, guardian=GUARDIAN, requester=SCHOOL, scope=1),
                    lambda: chain.check_access(contract, owner=GUARDIAN, requester=SCHOOL, scope=1),
                    lambda: chain.get_consent(contract, owner=GUARDIAN, requester=SCHOOL, scope=1),
                    lambda: chain.request_access(contract, requester=SCHOOL, owner=GUARDIAN, scope=1, observed_hash=b"\x01" * 32),
                    lambda: chain.get_reward_balance(contract, account=GUARDIAN),
                    lambda: chain.block_hash(client, 1),
                )
                for number, call in enumerate(calls):
                    with self.subTest(call=number):
                        with self.assertRaises(ChainUnavailable) as caught:
                            call()
                        self.assertNotIsInstance(caught.exception, DeploymentUnavailable)
                        self.assertNotIn("127.0.0.1", repr(caught.exception.args))

    def test_node_failure_after_the_send_is_pending_not_unavailable(self):
        # the node took the transaction, then stopped answering: it may still be mined, so nothing is known
        failures = (requests.ConnectionError("http://127.0.0.1:8545 refused"), requests.ReadTimeout("read timed out"), OSError("reset"))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                contract = mock_contract()
                contract.w3.eth.wait_for_transaction_receipt.side_effect = failure
                calls = (
                    lambda: chain.register_user(contract, account=SCHOOL, identity_hash=b"\x01" * 32),
                    lambda: chain.register_vaccination(contract, clinic=CLINIC, guardian=GUARDIAN, record_hash=b"\x01" * 32),
                    lambda: chain.grant_consent(contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=30),
                    lambda: chain.revoke_consent(contract, guardian=GUARDIAN, requester=SCHOOL, scope=1),
                    lambda: chain.request_access(contract, requester=SCHOOL, owner=GUARDIAN, scope=1, observed_hash=b"\x01" * 32),
                    lambda: chain.wait_for_receipt(contract.w3, TX_HASH),
                )
                for number, call in enumerate(calls):
                    with self.subTest(call=number):
                        with self.assertRaises(TransactionPending) as caught:
                            call()
                        self.assertEqual(caught.exception.args, ("transaction receipt pending",))
                self.assertEqual(contract.functions.revokeConsent.return_value.transact.call_count, 1)

    def test_bugs_are_not_reported_as_a_node_failure(self):
        # a wrong contract object or a programming error must show as "failed: <type>", not "not reachable"
        registry_abi = [{
            "type": "function", "name": "getUserInfo", "stateMutability": "view",
            "inputs": [{"name": "account", "type": "address"}],
            "outputs": [{"name": "registered", "type": "bool"}, {"name": "identityHash", "type": "bytes32"},
                        {"name": "vaccinationHash", "type": "bytes32"}],
        }]
        manager = Web3().eth.contract(address=MANAGER, abi=[ACCESS_ATTEMPT_ABI, CHECK_ACCESS_ABI])
        registry = Web3().eth.contract(address=MANAGER, abi=registry_abi)
        for label, call, error in (
            ("registerUser on the manager", lambda: chain.register_user(manager, account=SCHOOL, identity_hash=b"\x01" * 32), AttributeError),
            ("getUserInfo on the manager", lambda: chain.get_user_info(manager, account=GUARDIAN), AttributeError),
            ("grantConsent on the registry", lambda: chain.grant_consent(registry, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=1), AttributeError),
        ):
            with self.subTest(label):
                with self.assertRaises(error) as caught:
                    call()
                self.assertEqual(type(caught.exception).__name__, "ABIFunctionNotFound")
                self.assertNotIsInstance(caught.exception, ChainUnavailable)
        for error in (KeyError("x"), TypeError("x"), AttributeError("x")):
            with self.subTest(type(error).__name__):
                self.call_raises("getUserInfo", error)
                with self.assertRaises(type(error)):
                    chain.get_user_info(self.contract, account=GUARDIAN)
        # a view that answers the wrong shape is a bug in the ABI, not the node
        self.contract.functions.getUserInfo.return_value.call.side_effect = None
        self.contract.functions.getUserInfo.return_value.call.return_value = (True, b"\x01" * 32)
        with self.assertRaises(ValueError):
            chain.get_user_info(self.contract, account=GUARDIAN)

    def test_menu_names_a_bug_by_its_type(self):
        manager = Web3().eth.contract(address=MANAGER, abi=[ACCESS_ATTEMPT_ABI, CHECK_ACCESS_ABI])

        def register_on_the_manager(actor_label, settings):
            chain.register_user(manager, account=SCHOOL, identity_hash=b"\x01" * 32)

        with mock.patch.dict(main.HANDLERS, {"register": register_on_the_manager}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            main.dispatch_action("register", "guardian", {})
        self.assertEqual(output.getvalue(), "failed: ABIFunctionNotFound\n")

    def test_receipt_timeout_is_pending(self):
        self.transact_raises("revokeConsent", None)
        self.contract.w3.eth.wait_for_transaction_receipt.side_effect = TimeExhausted("not in the chain")
        with self.assertRaises(TransactionPending):
            chain.revoke_consent(self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1)

    def test_failed_receipt_is_reverted(self):
        self.contract.w3.eth.wait_for_transaction_receipt.return_value = raw_receipt(status=0)
        self.assertEqual(self.rejected(chain.revoke_consent, self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1), ("Reverted",))

    def test_wrong_contract_at_the_address_is_unavailable(self):
        self.call_raises("getUserInfo", BadFunctionCallOutput("could not decode"))
        with self.assertRaises(ChainUnavailable):
            chain.get_user_info(self.contract, account=GUARDIAN)

    def test_reward_balance_revert_is_rejected_not_unavailable(self):
        self.call_raises("balanceOf", custom_error("NotMinter"))
        self.assertEqual(self.rejected(chain.get_reward_balance, self.contract, account=GUARDIAN), ("NotMinter",))


class ArgumentTests(ChainTestCase):
    def test_lowercase_addresses_are_checksummed_not_refused(self):
        self.contract.functions.getUserInfo.return_value.call.return_value = (True, b"\x01" * 32, bytes(32))
        info = chain.get_user_info(self.contract, account=GUARDIAN.lower())
        self.contract.functions.getUserInfo.assert_called_once_with(GUARDIAN)
        self.assertEqual(info, {"registered": True, "identity_hash": b"\x01" * 32, "vaccination_hash": bytes(32)})
        self.contract.w3.eth.wait_for_transaction_receipt.return_value = raw_receipt()
        chain.grant_consent(self.contract, guardian=GUARDIAN.lower(), requester=SCHOOL.upper().replace("0X", "0x"), scope=Scope.MEASLES_STATUS, duration_days=30)
        self.contract.functions.grantConsent.assert_called_once_with(SCHOOL, 1, 30)
        self.contract.functions.grantConsent.return_value.transact.assert_called_once_with({"from": GUARDIAN})

    def test_caller_mistakes_are_value_errors_and_send_nothing(self):
        mistakes = (
            lambda: chain.get_user_info(self.contract, account="guardian"),
            lambda: chain.get_user_info(self.contract, account=None),
            lambda: chain.register_user(self.contract, account=SCHOOL, identity_hash=b"\x01" * 31),
            lambda: chain.register_user(self.contract, account=SCHOOL, identity_hash="00" * 32),
            lambda: chain.grant_consent(self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=256, duration_days=30),
            lambda: chain.grant_consent(self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=True, duration_days=30),
            lambda: chain.grant_consent(self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=30.5),
            lambda: chain.grant_consent(self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=65536),
            lambda: chain.grant_consent(self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=-1),
            lambda: chain.revoke_consent(self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=-1),
            lambda: chain.check_access(self.contract, owner=GUARDIAN, requester=SCHOOL, scope="1"),
            lambda: chain.get_consent(self.contract, owner=GUARDIAN, requester=SCHOOL, scope=300),
            lambda: chain.request_access(self.contract, requester=SCHOOL, owner=GUARDIAN, scope=256, observed_hash=bytes(32)),
            lambda: chain.request_access(self.contract, requester=SCHOOL, owner=GUARDIAN, scope=1, observed_hash=bytes(33)),
            lambda: chain.list_access_events(self.contract, from_block=-1),
            lambda: chain.list_access_events(self.contract, from_block=True),
        )
        for number, mistake in enumerate(mistakes):
            with self.subTest(mistake=number):
                with self.assertRaises(ValueError):
                    mistake()
        for function in ("registerUser", "grantConsent", "revokeConsent", "requestAccess"):
            getattr(self.contract.functions, function).return_value.transact.assert_not_called()

    def test_business_rules_are_left_to_the_contract(self):
        # scope 3 and 0 or 366 days reach the contract, which names the problem
        self.transact_raises("grantConsent", custom_error("UnsupportedScope"))
        self.assertEqual(self.rejected(chain.grant_consent, self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=3, duration_days=30), ("UnsupportedScope",))
        self.transact_raises("grantConsent", custom_error("InvalidDuration"))
        for days in (0, 366):
            self.assertEqual(self.rejected(chain.grant_consent, self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=1, duration_days=days), ("InvalidDuration",))
        self.transact_raises("revokeConsent", custom_error("UnsupportedScope"))
        self.assertEqual(self.rejected(chain.revoke_consent, self.contract, guardian=GUARDIAN, requester=SCHOOL, scope=3), ("UnsupportedScope",))
        self.contract.functions.checkAccess.return_value.call.return_value = (False, 2)
        self.assertEqual(chain.check_access(self.contract, owner=GUARDIAN, requester=SCHOOL, scope=3), {"allowed": False, "reason": Reason.UNSUPPORTED_SCOPE})
        self.contract.functions.checkAccess.assert_called_once_with(GUARDIAN, SCHOOL, 3)


class RequestAccessTests(ChainTestCase):
    def request(self, **overrides):
        arguments = {"requester": SCHOOL, "owner": GUARDIAN, "scope": Scope.MEASLES_STATUS, "observed_hash": b"\x07" * 32}
        arguments.update(overrides)
        return chain.request_access(self.contract, **arguments)

    def assert_unavailable(self, logs, **overrides):
        self.receipt_is(raw_receipt(logs=logs))
        with self.assertRaises(ChainUnavailable):
            self.request(**overrides)

    def test_one_matching_event_is_returned(self):
        self.receipt_is(raw_receipt(logs=[access_log(allowed=False, reason=4)]))
        event = self.request(requester=SCHOOL.lower(), owner=GUARDIAN.lower())
        self.assertEqual(event, {
            "owner": GUARDIAN, "requester": SCHOOL, "scope": 1, "timestamp": 1_700_000_000,
            "allowed": False, "reason": Reason.NO_CONSENT, "transaction_hash": TX_HASH,
        })
        self.contract.functions.requestAccess.assert_called_once_with(GUARDIAN, 1, b"\x07" * 32)
        self.contract.functions.requestAccess.return_value.transact.assert_called_once_with({"from": SCHOOL})

    def test_unsupported_scope_reaches_the_contract_and_is_logged(self):
        self.receipt_is(raw_receipt(logs=[access_log(scope=3, allowed=False, reason=2)]))
        event = self.request(scope=3)
        self.assertEqual((event["scope"], event["reason"]), (3, Reason.UNSUPPORTED_SCOPE))

    def test_wrong_emitter_is_unavailable(self):
        self.assert_unavailable([access_log(address=OTHER_CONTRACT)])

    def test_no_event_or_two_events_is_unavailable(self):
        self.assert_unavailable([])
        self.assert_unavailable([access_log(log_index=0), access_log(log_index=1)])

    def test_owner_requester_or_scope_mismatch_is_unavailable(self):
        self.assert_unavailable([access_log(owner=SCHOOL)])
        self.assert_unavailable([access_log(requester=GUARDIAN)])
        self.assert_unavailable([access_log(scope=2)])

    def test_other_manager_events_are_dropped_without_a_warning(self):
        other = dict(access_log(), topics=[HexBytes("0x" + "11" * 32)], logIndex=0)
        self.receipt_is(raw_receipt(logs=[other, access_log(log_index=1)]))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            event = self.request()
        self.assertTrue(event["allowed"])
        self.assertEqual(caught, [])

    def test_decoding_uses_the_loaded_manager_not_the_artifacts_folder(self):
        self.receipt_is(raw_receipt(logs=[access_log()]))
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(chain, "PROJECT_ROOT", Path(empty)):
            self.assertTrue(self.request()["allowed"])

    def test_reverted_request_is_rejected(self):
        self.receipt_is(raw_receipt(status=0))
        self.assertEqual(self.rejected(self.request), ("Reverted",))


class ReadTests(ChainTestCase):
    def test_get_consent(self):
        self.contract.functions.getConsent.return_value.call.return_value = (1_702_592_000, True)
        consent = chain.get_consent(self.contract, owner=GUARDIAN.lower(), requester=SCHOOL, scope=Scope.MEASLES_STATUS)
        self.assertEqual(consent, {"expires_at": 1_702_592_000, "revoked": True})
        self.assertIs(type(consent["expires_at"]), int)
        self.contract.functions.getConsent.assert_called_once_with(GUARDIAN, SCHOOL, 1)

    def test_get_consent_errors(self):
        self.call_raises("getConsent", custom_error("UnsupportedScope"))
        self.assertEqual(self.rejected(chain.get_consent, self.contract, owner=GUARDIAN, requester=SCHOOL, scope=1), ("UnsupportedScope",))
        self.call_raises("getConsent", requests.ConnectionError())
        with self.assertRaises(ChainUnavailable):
            chain.get_consent(self.contract, owner=GUARDIAN, requester=SCHOOL, scope=1)

    def test_receipt_keeps_every_key_and_adds_the_contract_address(self):
        client = mock.MagicMock()
        client.eth.wait_for_transaction_receipt.return_value = raw_receipt(contract_address=MANAGER.lower())
        receipt = chain.wait_for_receipt(client, TX_HASH)
        self.assertEqual(receipt, {
            "transaction_hash": TX_HASH, "status": 1, "gas_used": 21000, "block_number": 7, "logs": [],
            "contract_address": MANAGER,
        })
        client.eth.wait_for_transaction_receipt.return_value = raw_receipt()
        self.assertIsNone(chain.wait_for_receipt(client, TX_HASH)["contract_address"])

    def test_audit_keeps_unknown_scopes(self):
        log = {"args": {"owner": GUARDIAN, "requester": SCHOOL, "scope": 3, "timestamp": 5, "allowed": False, "reason": 2},
               "transactionHash": HexBytes(TX_HASH)}
        contract = mock.MagicMock()
        contract.events.AccessAttempt.return_value.get_logs.return_value = [log]
        self.assertEqual(chain.list_access_events(contract, from_block=0)[0]["scope"], 3)
        contract.events.AccessAttempt.return_value.get_logs.side_effect = requests.ConnectionError()
        with self.assertRaises(ChainUnavailable):
            chain.list_access_events(contract, from_block=0)


@unittest.skipIf(Web3 is None, "web3 is not installed")
class ConnectTests(unittest.TestCase):
    def free_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def test_no_node_is_unavailable_at_once(self):
        settings = {"rpc_url": f"http://127.0.0.1:{self.free_port()}", "expected_chain_id": 31337}
        started = time.monotonic()
        with self.assertRaises(ChainUnavailable) as caught:
            chain.connect(settings)
        self.assertLess(time.monotonic() - started, 2)
        self.assertNotIn("127.0.0.1", repr(caught.exception.args))

    def test_a_node_that_stalls_after_connect_fails_fast(self):
        # answers the connect handshake, then never answers eth_call, which web3 would retry 5 times by default
        release = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                results = {"web3_clientVersion": "stub", "eth_chainId": "0x7a69"}
                if request["method"] not in results:
                    release.wait(5)
                    return
                body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": results[request["method"]]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        settings = {"rpc_url": f"http://127.0.0.1:{server.server_address[1]}", "expected_chain_id": 31337}
        with mock.patch.object(chain, "RPC_TIMEOUT_SECONDS", 0.3):
            client = chain.connect(settings)
            registry = client.eth.contract(address=MANAGER, abi=[{
                "type": "function", "name": "getUserInfo", "stateMutability": "view",
                "inputs": [{"name": "account", "type": "address"}],
                "outputs": [{"name": "registered", "type": "bool"}, {"name": "identityHash", "type": "bytes32"},
                            {"name": "vaccinationHash", "type": "bytes32"}],
            }])
            started = time.monotonic()
            with self.assertRaises(ChainUnavailable):
                chain.get_user_info(registry, account=GUARDIAN)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_provider_has_a_timeout_and_no_retries(self):
        created = []
        real_provider = Web3.HTTPProvider

        def record(*args, **kwargs):
            created.append(kwargs)
            return real_provider(*args, **kwargs)

        with mock.patch.object(Web3, "HTTPProvider", side_effect=record), self.assertRaises(ChainUnavailable):
            chain.connect({"rpc_url": f"http://127.0.0.1:{self.free_port()}", "expected_chain_id": 31337})
        self.assertEqual(created, [{"request_kwargs": {"timeout": chain.RPC_TIMEOUT_SECONDS}, "exception_retry_configuration": None}])

    def test_without_web3_connect_is_unavailable(self):
        with mock.patch.dict(sys.modules, {"web3": None}), self.assertRaises(Web3NotInstalled) as caught:
            chain.connect({"rpc_url": "http://127.0.0.1:8545", "expected_chain_id": 31337})
        # still a ChainUnavailable for callers that only know that
        self.assertIsInstance(caught.exception, ChainUnavailable)
        self.assertEqual(caught.exception.args, (chain.WEB3_MISSING,))

    def test_missing_or_empty_rpc_url_never_falls_back_to_another_endpoint(self):
        with mock.patch.object(Web3, "HTTPProvider") as provider:
            for settings in ({"expected_chain_id": 31337}, {"rpc_url": "", "expected_chain_id": 31337}, {"rpc_url": None}, None):
                with self.subTest(settings=settings):
                    with self.assertRaises(ChainUnavailable) as caught:
                        chain.connect(settings)
                    self.assertEqual(caught.exception.args, ("settings unavailable",))
        provider.assert_not_called()


@unittest.skipIf(Web3 is None, "web3 is not installed")
class LoadContractTests(unittest.TestCase):
    """A deployment.json and artifacts in a temporary project root; the node is a mock client."""

    RUNTIME = bytes.fromhex("6080604052" + "00" * 32 + "5f5ffd")
    IMMUTABLE = {"7": [{"start": 5, "length": 32}]}

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patcher = mock.patch.object(chain, "PROJECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        # the deployed code carries an immutable value where the artifact has zeros
        self.code = self.RUNTIME[:5] + b"\x42" * 32 + self.RUNTIME[37:]
        self.write_artifact()
        self.deployment_path = self.root / "runtime-data" / "deployment.json"
        self.write_deployment()
        self.client = mock.MagicMock()
        self.client.eth.chain_id = 31337
        self.client.eth.get_code.return_value = HexBytes(self.code)
        self.client.eth.get_block.return_value = {"number": 1, "hash": HexBytes(BLOCK_HASH)}

    def write_artifact(self, runtime=None, name="ConsentManager"):
        folder = self.root / "artifacts" / "contracts" / f"{name}.sol"
        folder.mkdir(parents=True, exist_ok=True)
        artifact = {
            "abi": [ACCESS_ATTEMPT_ABI] + error_abi("NoConsentToRevoke"), "bytecode": "0x6000",
            "deployedBytecode": Web3.to_hex(runtime or self.RUNTIME), "immutableReferences": self.IMMUTABLE,
        }
        (folder / f"{name}.json").write_text(json.dumps(artifact))

    def write_deployment(self, **changes):
        deployment = {
            "chain_id": 31337, "artifacts_dir": "artifacts/contracts",
            "contracts": {"IdentityRegistry": CLINIC, "ConsentManager": MANAGER.lower(), "ConsentRewardToken": SCHOOL},
            "code_hashes": {"ConsentManager": chain.code_hash(self.code)},
            "deploy_block": {"number": 1, "hash": BLOCK_HASH.upper().replace("0X", "0x")},
        }
        deployment.update(changes)
        self.deployment_path.parent.mkdir(parents=True, exist_ok=True)
        self.deployment_path.write_text(json.dumps(deployment))

    def load(self):
        return chain.load_contract(self.client, "ConsentManager", self.deployment_path)

    def assert_stale(self):
        with self.assertRaises(DeploymentUnavailable) as caught:
            self.load()
        # existing "except ChainUnavailable" blocks still catch it
        self.assertIsInstance(caught.exception, ChainUnavailable)
        self.client.eth.contract.assert_not_called()

    def test_matching_deployment_loads(self):
        contract = self.load()
        self.assertIs(contract, self.client.eth.contract.return_value)
        self.client.eth.get_code.assert_called_once_with(MANAGER)
        self.client.eth.get_block.assert_called_once_with(1)
        self.assertEqual(self.client.eth.contract.call_args.kwargs["address"], MANAGER)

    def test_restarted_node_with_the_same_contracts_redeployed(self):
        # after a restart, python -m evaluation.measure puts byte-identical contracts at the same addresses;
        # only the recorded block tells them apart, because the new blocks were mined at other times
        self.client.eth.get_block.return_value = {"number": 1, "hash": HexBytes("0x" + "c2" * 32)}
        self.assert_stale()

    def test_restarted_node_without_the_deploy_block(self):
        self.client.eth.get_block.side_effect = BlockNotFound("no block 1")
        self.assert_stale()

    def test_missing_or_malformed_deploy_block(self):
        for block in (None, {}, {"number": 1}, {"number": -1, "hash": BLOCK_HASH}, {"number": True, "hash": BLOCK_HASH},
                      {"number": 1, "hash": "0x1234"}, {"number": 1, "hash": "0x" + "zz" * 32}):
            with self.subTest(block=block):
                self.write_deployment(deploy_block=block)
                self.assert_stale()
        self.write_deployment()
        deployment = json.loads(self.deployment_path.read_text())
        del deployment["deploy_block"]
        self.deployment_path.write_text(json.dumps(deployment))
        self.assert_stale()

    def test_missing_or_unreadable_deployment(self):
        self.deployment_path.unlink()
        self.assert_stale()
        self.deployment_path.write_text("{not json")
        self.assert_stale()

    def test_null_address_or_missing_code_hash(self):
        self.write_deployment(contracts={"ConsentManager": None})
        self.assert_stale()
        self.write_deployment(code_hashes={})
        self.assert_stale()

    def test_deployment_for_another_chain(self):
        self.write_deployment(chain_id=1)
        self.assert_stale()

    def test_restarted_node_without_code_at_the_address(self):
        self.client.eth.get_code.return_value = HexBytes(b"")
        self.assert_stale()

    def test_restarted_node_with_another_contract_at_the_address(self):
        self.client.eth.get_code.return_value = HexBytes(self.code[:-1] + b"\xfe")
        self.assert_stale()

    def test_same_contract_from_another_deployment(self):
        # the compiled code matches, but its immutable differs from what deploy_local recorded
        self.client.eth.get_code.return_value = HexBytes(self.RUNTIME[:5] + b"\x43" * 32 + self.RUNTIME[37:])
        self.assert_stale()

    def test_recompiled_contract_does_not_match(self):
        self.write_artifact(runtime=self.RUNTIME[:-1] + b"\xfe")
        self.assert_stale()

    def test_missing_artifact(self):
        (self.root / "artifacts" / "contracts" / "ConsentManager.sol" / "ConsentManager.json").unlink()
        with self.assertRaises(ArtifactUnavailable) as caught:
            self.load()
        # existing "except DeploymentUnavailable" blocks still catch it
        self.assertIsInstance(caught.exception, DeploymentUnavailable)
        with self.assertRaises(ArtifactUnavailable):
            chain.read_artifact("ConsentManager")

    def test_a_stale_deployment_is_not_an_artifact_problem(self):
        self.write_deployment(chain_id=1)
        with self.assertRaises(DeploymentUnavailable) as caught:
            self.load()
        self.assertNotIsInstance(caught.exception, ArtifactUnavailable)

    def test_node_down_is_not_a_deployment_problem(self):
        for target in (self.client.eth.get_code, self.client.eth.get_block):
            with self.subTest(target=str(target)):
                target.side_effect = requests.ConnectionError()
                with self.assertRaises(ChainUnavailable) as caught:
                    self.load()
                self.assertNotIsInstance(caught.exception, DeploymentUnavailable)
                target.side_effect = None

    def test_block_hash(self):
        self.assertEqual(chain.block_hash(self.client, 1), BLOCK_HASH)
        self.client.eth.get_block.side_effect = BlockNotFound("no block 9")
        self.assertIsNone(chain.block_hash(self.client, 9))
        for number in (-1, True, 1.0, "1", None):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    chain.block_hash(self.client, number)

    def test_loaded_abi_teaches_its_error_names(self):
        with mock.patch.object(chain, "_error_names", {}), mock.patch.object(chain, "_default_errors_loaded", True):
            self.load()
            self.assertEqual(chain.error_name(selector("NoConsentToRevoke")), "NoConsentToRevoke")

    def test_immutable_bytes_are_the_only_ones_masked(self):
        artifact = {"deployedBytecode": Web3.to_hex(self.RUNTIME), "immutableReferences": self.IMMUTABLE}
        self.assertTrue(chain.matches_artifact(self.code, artifact))
        self.assertFalse(chain.matches_artifact(self.code + b"\x00", artifact))
        self.assertFalse(chain.matches_artifact(b"\x61" + self.code[1:], artifact))
        self.assertFalse(chain.matches_artifact(b"", {"deployedBytecode": "0x"}))


class FakeMatchesRealTests(unittest.TestCase):
    def test_fake_signatures_match_app_chain(self):
        for name in FAKED:
            with self.subTest(name):
                real = list(inspect.signature(getattr(chain, name)).parameters)
                fake = list(inspect.signature(getattr(FakeChain, name)).parameters)[1:]
                self.assertEqual(fake, real)

    def test_fake_returns_canonical_addresses_and_value_errors(self):
        settings = {"actor_account_indices": {"guardian": 2, "school": 3}}
        fake = FakeChain(settings)
        guardian, school = fake.addresses["guardian"], fake.addresses["school"]
        self.assertNotEqual(guardian, guardian.lower())
        event = fake.request_access("ConsentManager", requester=school.lower(), owner=guardian.upper().replace("0X", "0x"), scope=3, observed_hash=bytes(32))
        self.assertEqual((event["owner"], event["requester"], event["reason"]), (guardian, school, Reason.UNSUPPORTED_SCOPE))
        for mistake in (
            lambda: fake.request_access("ConsentManager", requester=school, owner=guardian, scope=256, observed_hash=bytes(32)),
            lambda: fake.register_user("IdentityRegistry", account=school, identity_hash=bytes(31)),
            lambda: fake.grant_consent("ConsentManager", guardian=guardian, requester=school, scope=1, duration_days=30.5),
            lambda: fake.get_user_info("IdentityRegistry", account="guardian"),
        ):
            with self.assertRaises(ValueError):
                mistake()


if __name__ == "__main__":
    unittest.main()
