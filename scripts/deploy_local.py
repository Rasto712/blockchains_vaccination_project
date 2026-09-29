# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Local Hardhat deployment helpers.
Run from the project root after npm run compile, with npm run node running:
    .venv/bin/python -m scripts.deploy_local [--reset] [--settings PATH]
Order: registry (clinic as trustedClinic), token, manager (registry and token addresses), then
setMinterOnce(manager) from the deployer. Each step prints its address, transaction hash, block and gas
as soon as it is mined, and is verified before the next one starts.
Everything that needs no transaction is checked first: settings, an existing deployment file (only
replaced with --reset), artifacts, node and accounts. deployment.json is written once, atomically, after
every step succeeded, so a failed run leaves the previous file as it was. It records chain_id,
artifacts_dir, deploy_block (number and hash of the block holding the IdentityRegistry deploy), the three
addresses and each contract's runtime-code keccak, which chain.load_contract compares to reject a stale
file, including one from before a node restart. Transactions and errors go through app/chain.py, so a
revert shows its Solidity error name, for example "ConsentManager deploy failed: NotAContract".
Printed paths are relative to the project or <data_root>/..., never with the home folder.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from app import chain, disclosure, records
from app.models import (
    Receipt, ChainUnavailable, Web3NotInstalled, DeploymentUnavailable, TransactionRejected, TransactionPending,
)
from app.records import PROJECT_ROOT
from evaluation.measure import record_deployment_cost

CONTRACT_NAMES = chain.CONTRACT_NAMES
ARTIFACTS_DIR = chain.DEFAULT_ARTIFACTS_DIR
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.json"


class DeployError(RuntimeError):
    """A deployment step failed or did not verify. The message is short and safe to print."""


def load_artifact(name: str) -> dict[str, Any]:
    """Load one compiled contract artifact from Hardhat's standard output path.
    A missing artifact raises DeploymentUnavailable; run npm run compile first.
    """
    return chain.read_artifact(name, ARTIFACTS_DIR)


def _deploy_contract(
    client: Any,
    name: str,
    deployer: str,
    constructor_args: tuple[Any, ...] = (),
) -> tuple[str, Receipt]:
    """Deploy one artifact from the deployer, await success and check that the runtime code at the new
    address is exactly the compiled contract. Reverts raise TransactionRejected with the error name.
    """
    artifact = load_artifact(name)
    factory = client.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    receipt = chain.send_transaction(client, factory.constructor(*constructor_args), deployer)
    address = receipt["contract_address"]
    if not address:
        raise DeployError("no contract address in the receipt")
    if not chain.matches_artifact(chain.runtime_code(client, address), artifact):
        raise DeployError("runtime code is not the compiled contract")
    return address, receipt


def deploy_registry(client: Any, deployer: str, trusted_clinic: str) -> tuple[str, Receipt]:
    """Load compiled ABI/bytecode and deploy IdentityRegistry with its clinic constructor argument.
    Await success and verify runtime bytecode and trustedClinic(); return the actual address and its receipt.

    """
    address, receipt = _deploy_contract(client, "IdentityRegistry", deployer, (trusted_clinic,))
    registry = client.eth.contract(address=address, abi=load_artifact("IdentityRegistry")["abi"])
    configured_clinic = chain.call_view(registry.functions.trustedClinic())
    if str(configured_clinic).lower() != trusted_clinic.lower():
        raise DeployError("trustedClinic() is not the clinic account")
    return address, receipt


def deploy_reward_token(client: Any, deployer: str) -> tuple[str, Receipt]:
    """Deploy ConsentRewardToken and await successful receipt; return its actual address and the receipt.
    Never invent a placeholder address; a reverting constructor stops the run with its error name.

    """
    return _deploy_contract(client, "ConsentRewardToken", deployer)


def deploy_consent_manager(client: Any, deployer: str, registry_address: str, token_address: str) -> tuple[str, Receipt]:
    """Deploy the manager with the registry/token addresses from this exact local deployment.
    Await successful receipt and verify its runtime code; return the address and the receipt.
    ConsentManager has no getters for its registry or token, so those addresses cannot be read back.

    """
    return _deploy_contract(
        client,
        "ConsentManager",
        deployer,
        (registry_address, token_address),
    )


def configure_minter(client: Any, deployer: str, token_address: str, manager_address: str) -> Receipt:
    """Call setMinterOnce from the deployer only after manager deployment succeeds.
    Verify the configured address and return the receipt so its gas can be recorded; never leave public unrestricted minting.

    """
    token = client.eth.contract(
        address=client.to_checksum_address(token_address),
        abi=load_artifact("ConsentRewardToken")["abi"],
    )
    receipt = chain.send_transaction(client, token.functions.setMinterOnce(manager_address), deployer)
    configured_minter = chain.call_view(token.functions.minter())
    if str(configured_minter).lower() != manager_address.lower():
        raise DeployError("minter() is not the ConsentManager")
    return receipt


def save_deployment(
    path: Path,
    addresses: dict[str, str],
    chain_id: int,
    code_hashes: dict[str, str],
    deploy_block: dict[str, Any],
    replace: bool = False,
) -> None:
    """Save actual addresses, chain ID, artifacts_dir, the deploy block and runtime-code hashes to ignored
    runtime configuration. deploy_block is {"number": ..., "hash": ...} of the IdentityRegistry deploy.
    Do not store private keys or overwrite an existing deployment without explicit reset workflow (replace=True).
    The file is written to a temporary name next to it and then renamed over the target, so it is either
    the old file or the complete new one, never half written.

    """
    if path.exists() and not replace:
        raise FileExistsError("deployment file already exists; use --reset")
    for values in (addresses, code_hashes):
        if set(values) != set(CONTRACT_NAMES) or not all(values.values()):
            raise ValueError("all three contracts are required")
    try:
        number, block = deploy_block["number"], deploy_block["hash"]
    except (KeyError, TypeError):
        raise ValueError("the deploy block number and hash are required") from None
    if isinstance(number, bool) or not isinstance(number, int) or number < 0 or not isinstance(block, str) or not block:
        raise ValueError("the deploy block number and hash are required")
    deployment = {
        "chain_id": chain_id,
        "artifacts_dir": ARTIFACTS_DIR,
        "deploy_block": {"number": number, "hash": block},
        "contracts": {name: addresses[name] for name in CONTRACT_NAMES},
        "code_hashes": {name: code_hashes[name] for name in CONTRACT_NAMES},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(json.dumps(deployment, indent=2) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def deploy_all(client: Any, deployer: str, trusted_clinic: str) -> dict[str, Any]:
    """Run the four steps in order, printing each as it lands. Any failure raises DeployError naming the step.
    Returns addresses, receipts (the three deploys and setMinterOnce), runtime-code hashes, deploy_block
    (number and hash of the block holding the IdentityRegistry deploy) and deployment_costs:
    evaluation.measure.record_deployment_cost of each deploy receipt, in deploy order.
    """
    registry_address, registry_receipt = _step("IdentityRegistry deploy", deploy_registry, client, deployer, trusted_clinic)
    _print_step("IdentityRegistry", registry_address, registry_receipt)
    token_address, token_receipt = _step("ConsentRewardToken deploy", deploy_reward_token, client, deployer)
    _print_step("ConsentRewardToken", token_address, token_receipt)
    manager_address, manager_receipt = _step(
        "ConsentManager deploy", deploy_consent_manager, client, deployer, registry_address, token_address,
    )
    _print_step("ConsentManager", manager_address, manager_receipt)
    minter_receipt = _step("setMinterOnce", configure_minter, client, deployer, token_address, manager_address)
    _print_step("setMinterOnce", None, minter_receipt)

    addresses = {
        "IdentityRegistry": registry_address,
        "ConsentRewardToken": token_address,
        "ConsentManager": manager_address,
    }
    code_hashes = _step(
        "reading runtime code",
        lambda: {name: chain.code_hash(chain.runtime_code(client, address)) for name, address in addresses.items()},
    )
    deploy_block = _step("reading the deploy block", _deploy_block, client, registry_receipt["block_number"])
    receipts = {
        "IdentityRegistry": registry_receipt,
        "ConsentRewardToken": token_receipt,
        "ConsentManager": manager_receipt,
        "setMinterOnce": minter_receipt,
    }
    return {
        "addresses": addresses,
        "receipts": receipts,
        "code_hashes": code_hashes,
        "deploy_block": deploy_block,
        "deployment_costs": [
            record_deployment_cost(name, receipts[name])
            for name in ("IdentityRegistry", "ConsentRewardToken", "ConsentManager")
        ],
    }


def run(settings_path: Path, reset: bool) -> dict[str, Any]:
    """Check everything that needs no transaction, deploy, then save deployment.json. Raises DeployError."""
    settings = _read_settings(Path(settings_path))
    try:
        deployment_path = records.settings_path(settings, "deployment_file")
        chain_id = int(settings["expected_chain_id"])
    except (KeyError, TypeError, ValueError):
        raise DeployError("the settings file needs deployment_file and expected_chain_id") from None
    shown = _shown(deployment_path, settings)
    existed = deployment_path.exists()
    if existed and not reset:
        raise DeployError(
            f"{shown} already exists; rerun with --reset to replace it "
            "(for example after restarting the node). Nothing was sent."
        )
    try:
        for name in CONTRACT_NAMES:
            load_artifact(name)
    except DeploymentUnavailable:
        raise DeployError(disclosure.NO_ARTIFACTS_MESSAGE) from None

    try:
        client = chain.connect(settings)
        deployer = chain.select_account(client, "deployer", settings)
        trusted_clinic = chain.select_account(client, "clinic", settings)
    except Web3NotInstalled:
        raise DeployError(disclosure.NO_WEB3_MESSAGE) from None
    except ChainUnavailable:
        raise DeployError(
            "local node not reachable or wrong chain: start it with npm run node and check rpc_url "
            "and expected_chain_id in the settings"
        ) from None
    except (KeyError, TypeError, ValueError):
        raise DeployError("the settings have no usable deployer and clinic account indices") from None

    try:
        result = deploy_all(client, deployer, trusted_clinic)
    except DeployError as error:
        state = "is unchanged" if existed else "was not created"
        raise DeployError(f"{error}\nnothing was saved: {shown} {state}") from None

    try:
        save_deployment(
            deployment_path, result["addresses"], chain_id, result["code_hashes"], result["deploy_block"], replace=reset,
        )
    except (OSError, ValueError):
        raise DeployError(
            f"could not write {shown} (deployment_file); the contracts above are deployed but not saved"
        ) from None
    print(f"saved {shown} for chain {chain_id}")
    return result


def main(argv: list[str] | None = None) -> None:
    """Run registry -> token -> manager -> one-time minter configuration on the verified local node.
    Print addresses and receipts as each step succeeds, and save deployment.json only after all of them.
    deploy_all passes the three deploy receipts to evaluation.measure.record_deployment_cost, and run returns
    those rows (deployment_costs) with every receipt. The gas table (gas_results.csv) comes from
    python -m evaluation.measure, which deploys fresh contracts per scenario through the same steps.
    Use --reset after restarting the local Hardhat node. On failure print one readable reason and exit 1.
    """
    parser = argparse.ArgumentParser(description="Deploy the local vaccination contracts")
    parser.add_argument("--reset", action="store_true", help="replace an existing deployment file, e.g. after restarting the node")
    parser.add_argument(
        "--settings", type=Path, default=SETTINGS_FILE, metavar="PATH",
        help="settings file (default: config/settings.json)",
    )
    args = parser.parse_args(argv)

    try:
        run(args.settings, args.reset)
    except DeployError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None


def _step(label: str, action: Any, *arguments: Any) -> Any:
    # one readable line per failure: the step and the Solidity error name or a short cause
    try:
        return action(*arguments)
    except TransactionRejected as error:
        reason = error.args[0] if error.args else "Reverted"
    except TransactionPending:
        reason = "no receipt in time"
    except DeploymentUnavailable:
        reason = "compiled contract unavailable"
    except ChainUnavailable:
        reason = "local node not reachable"
    except DeployError as error:
        reason = str(error)
    raise DeployError(f"{label} failed: {reason}")


def _deploy_block(client: Any, number: int) -> dict[str, Any]:
    block = chain.block_hash(client, number)
    if block is None:
        raise DeployError("the block of the IdentityRegistry deploy is not on the node")
    return {"number": number, "hash": block}


def _print_step(name: str, address: str | None, receipt: Receipt) -> None:
    where = f"{address} " if address else ""
    print(
        f"{name}: {where}(tx {receipt['transaction_hash']}, block {receipt['block_number']}, "
        f"gas {receipt['gas_used']})",
        flush=True,
    )


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        settings = json.loads(path.read_bytes())
    except OSError:
        raise DeployError(
            f"no settings file at {_shown(path)}: copy config/settings.example.json to config/settings.json"
        ) from None
    except ValueError:
        raise DeployError(f"{_shown(path)} is not valid JSON") from None
    if not isinstance(settings, dict):
        raise DeployError(f"{_shown(path)} is not a settings object")
    return settings


def _shown(path: Path, settings: dict[str, Any] | None = None) -> str:
    return records.shown_path(path, settings)


if __name__ == "__main__":
    main()
