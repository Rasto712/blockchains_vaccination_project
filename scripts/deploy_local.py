"""Developer 4: local Hardhat deployment helpers."""
import argparse
import json
from pathlib import Path
from typing import Any
from app.chain import wait_for_receipt
from app.models import Receipt
from app.records import PROJECT_ROOT

CONTRACT_NAMES = ("IdentityRegistry", "ConsentManager", "ConsentRewardToken")


def load_artifact(name: str) -> dict[str, Any]:
    """Load one compiled contract artifact from Hardhat's standard output path."""
    artifact_path = (
        PROJECT_ROOT
        / "artifacts"
        / "contracts"
        / f"{name}.sol"
        / f"{name}.json"
    )
    with artifact_path.open("r", encoding="utf-8") as file:
        artifact = json.load(file)
    return artifact


def _deploy_contract(
    client: Any,
    name: str,
    deployer: str,
    constructor_args: tuple[Any, ...] = (),
) -> tuple[str, Receipt]:
    artifact = load_artifact(name)
    factory = client.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    transaction_hash = factory.constructor(*constructor_args).transact({"from": deployer})
    receipt = wait_for_receipt(client, transaction_hash)
    raw_receipt = client.eth.get_transaction_receipt(transaction_hash)
    address = raw_receipt["contractAddress"]
    if not address or client.eth.get_code(address) in (b"", b"\x00"):
        raise RuntimeError("deployment has no runtime code")
    return client.to_checksum_address(address), receipt


def deploy_registry(client: Any, deployer: str, trusted_clinic: str) -> tuple[str, Receipt]:
    """Load compiled ABI/bytecode and deploy IdentityRegistry with its clinic constructor argument.
    Await success and verify runtime bytecode; return the actual address and its receipt. Constructors currently revert.

    """
    return _deploy_contract(client, "IdentityRegistry", deployer, (trusted_clinic,))


def deploy_reward_token(client: Any, deployer: str) -> tuple[str, Receipt]:
    """Deploy ConsentRewardToken and await successful receipt; return its actual address and the receipt.
    Do not invent a placeholder address or attempt deployment of unfinished constructors.

    """
    return _deploy_contract(client, "ConsentRewardToken", deployer)


def deploy_consent_manager(client: Any, deployer: str, registry_address: str, token_address: str) -> tuple[str, Receipt]:
    """Deploy the manager with the registry/token addresses from this exact local deployment.
    Await successful receipt and verify network identity; return the address and the receipt.

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
    artifact = load_artifact("ConsentRewardToken")
    token = client.eth.contract(
        address=client.to_checksum_address(token_address),
        abi=artifact["abi"],
    )
    transaction_hash = token.functions.setMinterOnce(manager_address).transact(
        {"from": deployer}
    )
    receipt = wait_for_receipt(client, transaction_hash)
    configured_minter = token.functions.minter().call()
    if configured_minter.lower() != manager_address.lower():
        raise RuntimeError("configured minter mismatch")
    return receipt


def save_deployment(path: Path, addresses: dict[str, str], chain_id: int) -> None:
    """Save actual addresses, chain ID and artifacts_dir to ignored runtime configuration.
    Do not store private keys or overwrite an existing deployment without explicit reset workflow.

    """
    if path.exists():
        raise FileExistsError("deployment file already exists; use --reset")
    if set(addresses) != set(CONTRACT_NAMES) or not all(addresses.values()):
        raise ValueError("all contract addresses are required")
    path.parent.mkdir(parents=True, exist_ok=True)
    deployment = {
        "chain_id": chain_id,
        "artifacts_dir": "artifacts/contracts",
        "contracts": addresses,
    }
    path.write_text(json.dumps(deployment, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    """Run registry -> token -> manager -> one-time minter configuration on the verified local node.
    Print addresses and receipts only after real success. Pass the three deploy receipts to
    evaluation.measure.record_deployment_cost; keep the setMinterOnce receipt for the per-function gas table.
    Use --reset after restarting the local Hardhat node.
    """
    parser = argparse.ArgumentParser(description="Deploy the local vaccination contracts")
    parser.add_argument("--reset", action="store_true", help="replace stale deployment metadata")
    args = parser.parse_args()

    settings_path = PROJECT_ROOT / "config" / "settings.json"
    with settings_path.open("r", encoding="utf-8") as file:
        settings = json.load(file)

    from app.chain import connect, select_account

    client = connect(settings)
    deployer = select_account(client, "deployer", settings)
    trusted_clinic = select_account(client, "clinic", settings)
    deployment_path = PROJECT_ROOT / settings["deployment_file"]
    if args.reset and deployment_path.exists():
        deployment_path.unlink()

    registry_address, registry_receipt = deploy_registry(client, deployer, trusted_clinic)
    token_address, token_receipt = deploy_reward_token(client, deployer)
    manager_address, manager_receipt = deploy_consent_manager(
        client, deployer, registry_address, token_address
    )
    minter_receipt = configure_minter(client, deployer, token_address, manager_address)
    save_deployment(
        deployment_path,
        {
            "IdentityRegistry": registry_address,
            "ConsentRewardToken": token_address,
            "ConsentManager": manager_address,
        },
        int(client.eth.chain_id),
    )

    print(f"IdentityRegistry: {registry_address} (gas {registry_receipt['gas_used']})")
    print(f"ConsentRewardToken: {token_address} (gas {token_receipt['gas_used']})")
    print(f"ConsentManager: {manager_address} (gas {manager_receipt['gas_used']})")
    print(f"setMinterOnce: gas {minter_receipt['gas_used']}")


if __name__ == "__main__":
    main()
