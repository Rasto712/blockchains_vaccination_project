"""Developer 4: deployment orchestration outline, not a working deployment script.
Compile via Hardhat first. Every deployment operation is intentionally blocked until implemented.

All workflow methods are placeholders. They raise NotImplementedError rather than
returning fake data or pretending that authorization has succeeded.
"""
from pathlib import Path
from typing import Any


def deploy_registry(client: Any, deployer: str, trusted_clinic: str) -> str:
    """Load compiled ABI/bytecode and deploy IdentityRegistry with its clinic constructor argument.
    Await success and verify runtime bytecode; return the actual address. Constructors currently revert.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("deploy_registry is an implementation task; see docs/tasks.")


def deploy_reward_token(client: Any, deployer: str) -> str:
    """Deploy ConsentRewardToken and await successful receipt; return its actual address.
    Do not invent a placeholder address or attempt deployment of unfinished constructors.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("deploy_reward_token is an implementation task; see docs/tasks.")


def deploy_consent_manager(client: Any, deployer: str, registry_address: str, token_address: str) -> str:
    """Deploy the manager with the registry/token addresses from this exact local deployment.
    Await successful receipt and verify network identity.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("deploy_consent_manager is an implementation task; see docs/tasks.")


def configure_minter(client: Any, deployer: str, token_address: str, manager_address: str) -> None:
    """Call setMinterOnce from the deployer only after manager deployment succeeds.
    Verify the configured address and document receipt gas; never leave public unrestricted minting.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("configure_minter is an implementation task; see docs/tasks.")


def save_deployment(path: Path, addresses: dict[str, str], chain_id: int) -> None:
    """Save actual addresses, ABI references and chain ID to ignored runtime configuration.
    Do not store private keys or overwrite an existing deployment without explicit reset workflow.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("save_deployment is an implementation task; see docs/tasks.")


def main() -> None:
    """Run registry -> token -> manager -> one-time minter configuration on the verified local node.
    Print addresses and receipts only after real success; this entry remains a placeholder.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("main is an implementation task; see docs/tasks.")


if __name__ == "__main__":
    main()
