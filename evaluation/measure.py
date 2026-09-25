"""Developer 4: gas and timing measurement outline.
Use actual transaction receipts. Blank CSV templates are not measured results.

All workflow methods are placeholders. They raise NotImplementedError rather than
returning fake data or pretending that authorization has succeeded.
"""
from pathlib import Path
from typing import Any
from app.models import Receipt


def record_deployment_cost(contract_name: str, receipt: Receipt) -> dict[str, Any]:
    """Extract actual gasUsed for one successful deployment, not an estimate or fiat conversion.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("record_deployment_cost is an implementation task; see docs/tasks.")


def measure_transaction(send_transaction: Any) -> dict[str, Any]:
    """Use a monotonic clock before send, after successful receipt and after decoding the event.
    Return elapsed seconds and actual gasUsed; report failed/pending calls separately.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("measure_transaction is an implementation task; see docs/tasks.")


def run_requester_scenario(settings: dict[str, Any], requester_count: int) -> list[dict[str, Any]]:
    """Run a documented local scenario for 1, 5 or 10 requesters with independent accounts.
    Keep first rewarded grants, unrewarded regrants, allowed and denied access in distinct groups.
    Every role acts: deployer (deploy, set minter), clinic (attest), guardian (register, grant, revoke),
    requesters from scenario_requester_account_indices (register, request). Redeploy fresh contracts
    before each run, then re-register and re-attest. Report mean gas and time per operation and role.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("run_requester_scenario is an implementation task; see docs/tasks.")


def summarize_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute sample count, total/average gas and average timings from like-for-like measured rows.
    Do not double count internal token mint gas already included in a grant transaction.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("summarize_samples is an implementation task; see docs/tasks.")


def write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write observed results with scenario, sample count and units. Never prefill fabricated success/cost.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("write_results is an implementation task; see docs/tasks.")


def main() -> None:
    """Collect three deployment costs and core-function/scaling measurements after implementation.
    Read-only RPC queries have no transaction receipt fee; describe those separately.

    Current behavior: unimplemented. Replace with the documented workflow.
    """
    raise NotImplementedError("main is an implementation task; see docs/tasks.")


if __name__ == "__main__":
    main()
