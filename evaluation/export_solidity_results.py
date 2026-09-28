"""Developer 2: run the Solidity unit tests and export the REAL results as CSV rows.

Usage, from the project root (after `npm ci`):

    python -m evaluation.export_solidity_results
    python -m evaluation.export_solidity_results --config some.config.ts   # optional extra Hardhat args

It runs `npx hardhat test solidity`, saves the raw console log, and writes
evaluation/results/solidity_test_results.csv with the shared header
(test_id, requirement, why_critical, expected, actual, status, evidence).

Nothing is prefilled: `requirement`, `why_critical` and `expected` come from each test's NatSpec
(@custom:requirement, "@dev Why it matters:", @notice), while `actual` and `status` come only from
the run output. A test that did not appear in the output gets no row (a missing row means "not run").
Status is "pass" or "fail". Standard library only.

AI assistance: drafted with Claude (Anthropic) and reviewed by Developer 2.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
RESULTS_DIR = ROOT / "evaluation" / "results"
HEADER = ["test_id", "requirement", "why_critical", "expected", "actual", "status", "evidence"]

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SUITE_LINE = re.compile(r"^\s{2}(test/\S+\.t\.sol):(\w+)\s*$")
PASS_LINE = re.compile(r"^\s{4}\S+\s+(test\w*)\(\)")  # "    ✔ testName()" (tick glyph varies by terminal)
FAIL_LINE = re.compile(r"^\s{4}(\d+)\)\s+(\w+)\(\)")  # "    3) testName()" or "    1) setUp()"
FAIL_DETAIL = re.compile(r"^\s{2}(\d+)\)\s+(\w+)#(\w+)\(\)")  # "  3) Contract#testName()"


@dataclass
class TestMeta:
    test_id: str
    file: str
    contract: str
    function: str
    expected: str
    requirement: str
    why: str


def parse_test_metadata() -> dict[tuple[str, str], TestMeta]:
    """Read test IDs and descriptions from the NatSpec above every test function."""
    metas: dict[tuple[str, str], TestMeta] = {}
    for path in sorted(TEST_DIR.glob("*.t.sol")):
        text = path.read_text(encoding="utf-8")
        contract_match = re.search(r"^contract\s+(\w+)\s+is\s+Test", text, re.MULTILINE)
        if not contract_match:
            continue
        contract = contract_match.group(1)
        pattern = re.compile(
            r"((?:[ \t]*///[^\n]*\n)+)[ \t]*function\s+(test\w*)\s*\(", re.MULTILINE
        )
        for block, function in pattern.findall(text):
            lines = [ln.strip()[3:].strip() for ln in block.strip().splitlines()]
            doc = "\n".join(lines)
            id_match = re.search(r"@notice\s+\[(SOL-[A-Z]{2}-\d{2})\]\s*(.+)", doc)
            if not id_match:
                continue
            requirement = re.search(r"@custom:requirement\s+(.+)", doc)
            why = re.search(r"Why it matters:\s*(.+)", doc)
            metas[(contract, function)] = TestMeta(
                test_id=id_match.group(1),
                file=path.relative_to(ROOT).as_posix(),
                contract=contract,
                function=function,
                expected=id_match.group(2).strip(),
                requirement=requirement.group(1).strip() if requirement else "",
                why=why.group(1).strip() if why else "",
            )
    return metas


def run_tests(extra_args: list[str]) -> tuple[int, str]:
    command = ["npx", "hardhat", *extra_args, "test", "solidity"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=(os.name == "nt"),  # npx is npx.cmd on Windows
    )
    return completed.returncode, ANSI.sub("", completed.stdout + completed.stderr)


def parse_output(output: str, metas: dict[tuple[str, str], TestMeta]):
    """Return {(contract, function): (status, actual)} for every test that appears in the output."""
    results: dict[tuple[str, str], tuple[str, str]] = {}
    failure_index: dict[str, tuple[str, str]] = {}  # "3" -> (contract, function)
    failure_error: dict[str, str] = {}
    current_contract = None
    lines = output.splitlines()

    for i, line in enumerate(lines):
        suite = SUITE_LINE.match(line)
        if suite:
            current_contract = suite.group(2)
            continue
        detail = FAIL_DETAIL.match(line)
        if detail:
            number, contract, function = detail.groups()
            failure_index[number] = (contract, function)
            error = next((l.strip() for l in lines[i + 1 : i + 6] if l.strip().startswith("Error")), "failed")
            failure_error[number] = error
            continue
        if current_contract is None:
            continue
        passed = PASS_LINE.match(line)
        if passed:
            results[(current_contract, passed.group(1))] = ("pass", "passed")
            continue
        failed = FAIL_LINE.match(line)
        if failed:
            failure_index.setdefault(failed.group(1), (current_contract, failed.group(2)))

    for number, (contract, function) in failure_index.items():
        error = failure_error.get(number, "failed")
        if function == "setUp":
            # A failing setUp means none of that contract's tests could run successfully.
            for (meta_contract, meta_function) in metas:
                if meta_contract == contract:
                    results[(meta_contract, meta_function)] = ("fail", f"setUp() failed: {error}")
        else:
            results[(contract, function)] = ("fail", error)
    return results


def git_revision() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "contracts", "test"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        return f"{sha}{' + uncommitted contracts/tests' if dirty else ''}" if sha else "unknown commit"
    except OSError:
        return "unknown commit"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("hardhat_args", nargs="*", help="extra global Hardhat arguments, e.g. --config x.ts")
    args, unknown = parser.parse_known_args()
    extra = [*unknown, *args.hardhat_args]

    metas = parse_test_metadata()
    returncode, output = run_tests(extra)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "solidity_test_run.log").write_text(output, encoding="utf-8")

    results = parse_output(output, metas)
    if not results:
        print(output)
        print("No test results found in the Hardhat output (compile error?). Nothing written.", file=sys.stderr)
        return 1

    when = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    revision = git_revision()
    rows = []
    for key, meta in sorted(metas.items(), key=lambda item: item[1].test_id):
        if key not in results:
            continue  # not run -> no row
        status, actual = results[key]
        evidence = f"{meta.file}:{meta.contract}#{meta.function}(); npx hardhat test solidity; {revision}; {when}"
        rows.append([meta.test_id, meta.requirement, meta.why, meta.expected, actual, status, evidence])

    out_path = RESULTS_DIR / "solidity_test_results.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)

    passed = sum(1 for r in rows if r[5] == "pass")
    print(f"{passed} pass, {len(rows) - passed} fail, {len(metas) - len(rows)} not run -> {out_path.relative_to(ROOT)}")
    print(f"raw log -> {(RESULTS_DIR / 'solidity_test_run.log').relative_to(ROOT)} (hardhat exit code {returncode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
