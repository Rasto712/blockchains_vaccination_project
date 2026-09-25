"""Developer 4: deployment orchestration outline, not a working deployment script.
Compile via Hardhat first. Every deployment operation is intentionally blocked until implemented.

All workflow methods are placeholders. They raise NotImplementedError rather than
returning fake data or pretending that authorization has succeeded.
"""
from pathlib import Path
from typing import Any
from app.models import Receipt


def deploy_registry(client: Any, deployer: str, trusted_clinic: str) -> tuple[str, Receipt]:
    """Load compiled ABI/bytecode and deploy IdentityRegistry with its clinic constructor argument.
    Await success and verify runtime bytecode; return the actual address and its receipt. Constructors currently revert.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("deploy_registry is an implementation task; see docs/tasks.")


def deploy_reward_token(client: Any, deployer: str) -> tuple[str, Receipt]:
    """Deploy ConsentRewardToken and await successful receipt; return its actual address and the receipt.
    Do not invent a placeholder address or attempt deployment of unfinished constructors.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("deploy_reward_token is an implementation task; see docs/tasks.")


def deploy_consent_manager(client: Any, deployer: str, registry_address: str, token_address: str) -> tuple[str, Receipt]:
    """Deploy the manager with the registry/token addresses from this exact local deployment.
    Await successful receipt and verify network identity; return the address and the receipt.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("deploy_consent_manager is an implementation task; see docs/tasks.")


def configure_minter(client: Any, deployer: str, token_address: str, manager_address: str) -> Receipt:
    """Call setMinterOnce from the deployer only after manager deployment succeeds.
    Verify the configured address and return the receipt so its gas can be recorded; never leave public unrestricted minting.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("configure_minter is an implementation task; see docs/tasks.")


def save_deployment(path: Path, addresses: dict[str, str], chain_id: int) -> None:
    """Save actual addresses, chain ID and artifacts_dir to ignored runtime configuration.
    Do not store private keys or overwrite an existing deployment without explicit reset workflow.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("save_deployment is an implementation task; see docs/tasks.")


def main() -> None:
    """Run registry -> token -> manager -> one-time minter configuration on the verified local node.
    Print addresses and receipts only after real success. Pass the three deploy receipts to
    evaluation.measure.record_deployment_cost; keep the setMinterOnce receipt for the per-function gas table.
    This entry remains a placeholder.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("main is an implementation task; see docs/tasks.")


if __name__ == "__main__":
    main()
