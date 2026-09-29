# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Offline tests for scripts/deploy_local.py: no node; the client, the chain connection and the deploy
steps are mocked. Covers the existing-file check before any transaction, --reset keeping the old file until
every step succeeded, the atomic write, readable failures with the Solidity error name, the trustedClinic,
minter and runtime-code checks, the recorded deploy block, and printed paths that never show the home
folder. Skipped when web3 is not installed.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import chain, disclosure, records
from app.models import ChainUnavailable, Web3NotInstalled, TransactionRejected
from scripts import deploy_local

try:
    from hexbytes import HexBytes
    from web3 import Web3
    from web3.exceptions import ContractCustomError
except ImportError:
    Web3 = None

DEPLOYER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
CLINIC = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
ADDRESSES = {
    "IdentityRegistry": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
    "ConsentRewardToken": "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
    "ConsentManager": "0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
}
RUNTIME = "0x6080604052348015600e575f5ffd5b50"
TX_HASH = "0x" + "cd" * 32
BLOCK = {"number": 1, "hash": "0x" + "b1" * 32}


def receipt(block, gas, address=None):
    return {"transaction_hash": TX_HASH, "status": 1, "gas_used": gas, "block_number": block, "logs": [], "contract_address": address}


def raw_receipt(address):
    return {"status": 1, "transactionHash": HexBytes(TX_HASH), "gasUsed": 500000, "blockNumber": 3, "logs": [], "contractAddress": address}


@unittest.skipIf(Web3 is None, "web3 is not installed")
class DeployTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        for target, name, value in (
            (chain, "PROJECT_ROOT", self.root), (records, "PROJECT_ROOT", self.root),
            (chain, "_error_names", {}), (chain, "_default_errors_loaded", True),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in chain.CONTRACT_NAMES:
            self.write_artifact(name)
        self.deployment_path = self.root / "runtime-data" / "deployment.json"
        self.settings_path = self.root / "settings.json"
        self.settings_path.write_text(json.dumps({
            "rpc_url": "http://127.0.0.1:8545", "expected_chain_id": 31337,
            "deployment_file": str(self.deployment_path),
            "actor_account_indices": {"deployer": 0, "clinic": 1, "guardian": 2},
        }))

    def write_artifact(self, name, runtime=RUNTIME):
        folder = self.root / "artifacts" / "contracts" / f"{name}.sol"
        folder.mkdir(parents=True, exist_ok=True)
        abi = [{"type": "error", "name": "ZeroAddress", "inputs": []}]
        artifact = {"abi": abi, "bytecode": "0x6000", "deployedBytecode": runtime, "immutableReferences": {}}
        (folder / f"{name}.json").write_text(json.dumps(artifact))

    def run_main(self, *arguments):
        """Run main with mocked connection; returns (exit code, stdout, stderr)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                deploy_local.main(["--settings", str(self.settings_path), *arguments])
            except SystemExit as exit:
                code = exit.code
        return code, stdout.getvalue(), stderr.getvalue()

    def mock_chain(self, deploy=None):
        """Patch connect, select_account and deploy_all; returns the deploy_all mock."""
        accounts = {"deployer": DEPLOYER, "clinic": CLINIC}
        self.connect = self.patch(chain, "connect", return_value=mock.MagicMock())
        self.patch(chain, "select_account", side_effect=lambda client, label, settings: accounts[label])
        result = {
            "addresses": dict(ADDRESSES), "receipts": {}, "code_hashes": {name: "0x" + "11" * 32 for name in ADDRESSES},
            "deploy_block": dict(BLOCK),
        }
        return self.patch(deploy_local, "deploy_all", side_effect=deploy or (lambda *args: result))

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()


class ExistingFileTests(DeployTestCase):
    def setUp(self):
        super().setUp()
        self.deployment_path.parent.mkdir(parents=True)
        self.deployment_path.write_text('{"old": true}\n')

    def test_without_reset_nothing_is_sent(self):
        deploy_all = self.mock_chain()
        code, _, error = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("already exists; rerun with --reset", error)
        self.assertIn("Nothing was sent", error)
        self.connect.assert_not_called()
        deploy_all.assert_not_called()
        self.assertEqual(self.deployment_path.read_text(), '{"old": true}\n')

    def test_reset_keeps_the_old_file_until_every_step_succeeded(self):
        seen = []

        def fail(*args):
            seen.append(self.deployment_path.read_text())
            raise deploy_local.DeployError("ConsentRewardToken deploy failed: ZeroAddress")

        self.mock_chain(deploy=fail)
        code, _, error = self.run_main("--reset")
        self.assertEqual(code, 1)
        self.assertEqual(seen, ['{"old": true}\n'])
        self.assertIn("ConsentRewardToken deploy failed: ZeroAddress", error)
        self.assertIn("nothing was saved: runtime-data/deployment.json is unchanged", error)
        self.assertEqual(self.deployment_path.read_text(), '{"old": true}\n')

    def test_reset_replaces_the_file_after_success(self):
        self.mock_chain()
        code, output, _ = self.run_main("--reset")
        self.assertEqual(code, 0)
        saved = json.loads(self.deployment_path.read_text())
        self.assertEqual(saved["chain_id"], 31337)
        self.assertEqual(saved["artifacts_dir"], "artifacts/contracts")
        self.assertEqual(saved["contracts"], ADDRESSES)
        self.assertEqual(set(saved["code_hashes"]), set(ADDRESSES))
        self.assertEqual(saved["deploy_block"], BLOCK)
        self.assertIn("saved runtime-data/deployment.json for chain 31337", output)
        self.assertEqual([path.name for path in self.deployment_path.parent.iterdir()], ["deployment.json"])


class PreflightTests(DeployTestCase):
    def test_first_failed_run_creates_no_file(self):
        def fail(*args):
            raise deploy_local.DeployError("IdentityRegistry deploy failed: ZeroAddress")

        self.mock_chain(deploy=fail)
        code, _, error = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("runtime-data/deployment.json was not created", error)
        self.assertFalse(self.deployment_path.exists())

    def test_missing_settings_points_to_the_example(self):
        self.settings_path.unlink()
        code, _, error = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("copy config/settings.example.json to config/settings.json", error)
        self.assertNotIn("Traceback", error)

    def test_missing_artifacts_stop_before_connecting(self):
        deploy_all = self.mock_chain()
        (self.root / "artifacts" / "contracts" / "ConsentManager.sol" / "ConsentManager.json").unlink()
        code, _, error = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("run npm run compile first", error)
        self.connect.assert_not_called()
        deploy_all.assert_not_called()

    def test_unreachable_node_says_how_to_start_it(self):
        deploy_all = self.mock_chain()
        self.connect.side_effect = ChainUnavailable("RPC connection failed")
        code, _, error = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("start it with npm run node", error)
        deploy_all.assert_not_called()

    def test_missing_web3_says_how_to_install_it_not_to_start_the_node(self):
        deploy_all = self.mock_chain()
        self.connect.side_effect = Web3NotInstalled(chain.WEB3_MISSING)
        code, _, error = self.run_main()
        self.assertEqual((code, error), (1, disclosure.NO_WEB3_MESSAGE + "\n"))
        deploy_all.assert_not_called()

    def test_paths_outside_the_project_are_shown_without_the_home_folder(self):
        # data_root outside the project: the deployment file is shown as <data_root>/..., anything else by name
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        outside = Path(folder.name).resolve()
        settings = json.loads(self.settings_path.read_text())
        settings.update(data_root=str(outside), deployment_file=str(outside / "deployment.json"))
        self.settings_path.write_text(json.dumps(settings))
        self.mock_chain()
        code, output, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("saved <data_root>/deployment.json for chain 31337", output)
        code, _, exists = self.run_main()
        self.assertEqual(code, 1)
        self.assertTrue(exists.startswith("<data_root>/deployment.json already exists"), exists)
        settings.update(deployment_file="/dev/null/deployment.json")
        self.settings_path.write_text(json.dumps(settings))
        code, _, error = self.run_main("--reset")
        self.assertEqual(error, "could not write deployment.json (deployment_file); the contracts above are deployed but not saved\n")
        for text in (output, exists, error):
            self.assertNotIn(str(outside), text)
            self.assertNotIn("/dev/null", text)

    def test_default_settings_file(self):
        run = self.patch(deploy_local, "run")
        deploy_local.main([])
        run.assert_called_once_with(deploy_local.SETTINGS_FILE, False)


class StepTests(DeployTestCase):
    def test_each_step_prints_as_it_lands_and_a_failure_names_step_and_error(self):
        self.patch(deploy_local, "deploy_registry", return_value=(ADDRESSES["IdentityRegistry"], receipt(1, 270183)))
        self.patch(deploy_local, "deploy_reward_token", side_effect=TransactionRejected("ZeroAddress"))
        manager = self.patch(deploy_local, "deploy_consent_manager")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(deploy_local.DeployError) as caught:
            deploy_local.deploy_all(mock.MagicMock(), DEPLOYER, CLINIC)
        self.assertEqual(str(caught.exception), "ConsentRewardToken deploy failed: ZeroAddress")
        self.assertEqual(output.getvalue(), f"IdentityRegistry: {ADDRESSES['IdentityRegistry']} (tx {TX_HASH}, block 1, gas 270183)\n")
        manager.assert_not_called()

    def full_run(self, client):
        self.patch(deploy_local, "deploy_registry", return_value=(ADDRESSES["IdentityRegistry"], receipt(1, 10)))
        self.patch(deploy_local, "deploy_reward_token", return_value=(ADDRESSES["ConsentRewardToken"], receipt(2, 20)))
        self.patch(deploy_local, "deploy_consent_manager", return_value=(ADDRESSES["ConsentManager"], receipt(3, 30)))
        self.patch(deploy_local, "configure_minter", return_value=receipt(4, 40))
        client.eth.get_code.return_value = HexBytes(RUNTIME)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = deploy_local.deploy_all(client, DEPLOYER, CLINIC)
        return result, output.getvalue()

    def test_full_run_returns_receipts_and_code_hashes(self):
        client = mock.MagicMock()
        client.eth.get_block.return_value = {"number": 1, "hash": HexBytes(BLOCK["hash"])}
        result, output = self.full_run(client)
        self.assertEqual(result["addresses"], ADDRESSES)
        self.assertEqual(set(result["receipts"]), set(ADDRESSES) | {"setMinterOnce"})
        self.assertEqual(set(result["code_hashes"].values()), {chain.code_hash(bytes.fromhex(RUNTIME[2:]))})
        self.assertEqual(output.splitlines()[-1], f"setMinterOnce: (tx {TX_HASH}, block 4, gas 40)")
        # the block of the registry deploy, whose hash a restarted node would not have
        self.assertEqual(result["deploy_block"], BLOCK)
        client.eth.get_block.assert_called_once_with(1)
        # one gas row per deploy receipt, in deploy order, straight from evaluation.measure
        self.assertEqual(
            [(row["contract"], row["gas_used"], row["block_number"]) for row in result["deployment_costs"]],
            [("IdentityRegistry", 10, 1), ("ConsentRewardToken", 20, 2), ("ConsentManager", 30, 3)],
        )

    def test_deploy_block_missing_on_the_node_fails_the_run(self):
        from web3.exceptions import BlockNotFound

        client = mock.MagicMock()
        client.eth.get_block.side_effect = BlockNotFound("no block 1")
        with self.assertRaises(deploy_local.DeployError) as caught:
            self.full_run(client)
        self.assertEqual(
            str(caught.exception), "reading the deploy block failed: the block of the IdentityRegistry deploy is not on the node",
        )


class ContractCheckTests(DeployTestCase):
    def client_deploying(self, address, runtime=RUNTIME):
        client = mock.MagicMock()
        factory = client.eth.contract.return_value
        factory.constructor.return_value.transact.return_value = HexBytes(TX_HASH)
        client.eth.wait_for_transaction_receipt.return_value = raw_receipt(address)
        client.eth.get_code.return_value = HexBytes(runtime)
        return client, factory

    def test_constructor_revert_is_named(self):
        client, factory = self.client_deploying(ADDRESSES["ConsentRewardToken"])
        data = Web3.to_hex(Web3.keccak(text="ZeroAddress()")[:4])
        factory.constructor.return_value.transact.side_effect = ContractCustomError(data, data=data)
        self.patch(deploy_local, "deploy_registry", return_value=(ADDRESSES["IdentityRegistry"], receipt(1, 10)))
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(deploy_local.DeployError) as caught:
            deploy_local.deploy_all(client, DEPLOYER, CLINIC)
        self.assertEqual(str(caught.exception), "ConsentRewardToken deploy failed: ZeroAddress")

    def test_deploy_reads_the_receipt_once_and_checks_the_code(self):
        client, factory = self.client_deploying(ADDRESSES["ConsentRewardToken"].lower())
        address, token_receipt = deploy_local.deploy_reward_token(client, DEPLOYER)
        self.assertEqual(address, ADDRESSES["ConsentRewardToken"])
        self.assertEqual(token_receipt["contract_address"], address)
        self.assertEqual((token_receipt["block_number"], token_receipt["gas_used"]), (3, 500000))
        factory.constructor.return_value.transact.assert_called_once_with({"from": DEPLOYER})
        client.eth.get_transaction_receipt.assert_not_called()
        self.assertEqual(client.eth.wait_for_transaction_receipt.call_count, 1)

    def test_code_that_is_not_the_compiled_contract_fails(self):
        client, _ = self.client_deploying(ADDRESSES["ConsentRewardToken"], runtime="0x60806040")
        with self.assertRaises(deploy_local.DeployError):
            deploy_local.deploy_reward_token(client, DEPLOYER)

    def test_trusted_clinic_is_read_back(self):
        client, factory = self.client_deploying(ADDRESSES["IdentityRegistry"])
        factory.functions.trustedClinic.return_value.call.return_value = CLINIC.lower()
        self.assertEqual(deploy_local.deploy_registry(client, DEPLOYER, CLINIC)[0], ADDRESSES["IdentityRegistry"])
        factory.constructor.assert_called_once_with(CLINIC)
        factory.functions.trustedClinic.return_value.call.return_value = DEPLOYER
        with self.assertRaises(deploy_local.DeployError) as caught:
            deploy_local.deploy_registry(client, DEPLOYER, CLINIC)
        self.assertIn("trustedClinic", str(caught.exception))

    def test_minter_is_read_back(self):
        client = mock.MagicMock()
        client.to_checksum_address.side_effect = Web3.to_checksum_address
        client.eth.wait_for_transaction_receipt.return_value = raw_receipt(None)
        token = client.eth.contract.return_value
        token.functions.minter.return_value.call.return_value = ADDRESSES["ConsentManager"]
        deploy_local.configure_minter(client, DEPLOYER, ADDRESSES["ConsentRewardToken"], ADDRESSES["ConsentManager"])
        token.functions.setMinterOnce.assert_called_once_with(ADDRESSES["ConsentManager"])
        token.functions.minter.return_value.call.return_value = DEPLOYER
        with self.assertRaises(deploy_local.DeployError):
            deploy_local.configure_minter(client, DEPLOYER, ADDRESSES["ConsentRewardToken"], ADDRESSES["ConsentManager"])


class SaveDeploymentTests(DeployTestCase):
    def save(self, replace=False, block=BLOCK):
        hashes = {name: "0x" + "22" * 32 for name in ADDRESSES}
        deploy_local.save_deployment(self.deployment_path, ADDRESSES, 31337, hashes, block, replace=replace)

    def test_saved_file_is_what_load_contract_reads(self):
        self.save()
        saved = json.loads(self.deployment_path.read_text())
        self.assertEqual(list(saved), ["chain_id", "artifacts_dir", "deploy_block", "contracts", "code_hashes"])
        self.assertEqual(saved["deploy_block"], BLOCK)
        self.assertEqual(chain._deploy_block(saved["deploy_block"]), (1, BLOCK["hash"]))

    def test_missing_deploy_block_is_refused(self):
        for block in (None, {}, {"number": 1}, {"number": -1, "hash": BLOCK["hash"]}, {"number": 1, "hash": ""}):
            with self.subTest(block=block):
                with self.assertRaises(ValueError):
                    self.save(block=block)
        self.assertFalse(self.deployment_path.exists())

    def test_existing_file_needs_replace(self):
        self.save()
        with self.assertRaises(FileExistsError):
            self.save()
        self.save(replace=True)

    def test_a_failed_write_leaves_the_old_file_and_no_temporary_file(self):
        self.deployment_path.parent.mkdir(parents=True)
        self.deployment_path.write_text("old\n")
        with mock.patch.object(deploy_local.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.save(replace=True)
        self.assertEqual(self.deployment_path.read_text(), "old\n")
        self.assertEqual([path.name for path in self.deployment_path.parent.iterdir()], ["deployment.json"])

    def test_incomplete_deployment_is_refused(self):
        with self.assertRaises(ValueError):
            deploy_local.save_deployment(self.deployment_path, {"IdentityRegistry": ADDRESSES["IdentityRegistry"]}, 31337, {}, BLOCK)
        self.assertFalse(self.deployment_path.exists())


if __name__ == "__main__":
    unittest.main()
