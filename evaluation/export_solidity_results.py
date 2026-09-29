# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""Run the Solidity unit tests and export the REAL results as CSV rows.

Usage, from the project root (after `npm ci`):

    python -m evaluation.export_solidity_results
    python -m evaluation.export_solidity_results --config some.config.ts   # optional extra global Hardhat args

It runs `npx hardhat test solidity`, saves the raw console log to evaluation/results/solidity_test_run.log,
and writes evaluation/results/solidity_test_results.csv with the shared header
(test_id, requirement, why_critical, expected, actual, status, evidence). It writes only the SOL rows, in
their own file, so it can never overwrite the Python (PY) rows kept elsewhere under the same header.

Nothing is prefilled: `requirement`, `why_critical` and `expected` come from each test's NatSpec
(@custom:requirement, "@dev Why it matters:", @notice), while `actual` and `status` come only from
the run output. Status is "pass" or "fail":
- a test Hardhat lists as passing is "pass"; one it lists as failing is "fail" with Hardhat's error line;
- when a suite's setUp() fails, none of its tests runs and Hardhat counts the suite as one failure. Each of
  its tests is still written as "fail", with actual "not run: setUp() failed: ...", so a broken fixture
  can never look like a skipped (missing) row;
- a test that Hardhat lists as skipped, or that does not appear in the output at all, gets no row, so a
  missing row means "not run".

The exporter refuses to write a CSV that could disagree with Hardhat:
- before running, every test function in test/*.t.sol must carry exactly one `@notice [SOL-XX-nn]` id
  (in a /// or /** */ NatSpec block), and no id may repeat;
- after running, every test Hardhat lists must match a tagged function, and the pass/fail/skip counts
  must equal Hardhat's own "N passing" / "M failing" / "K skipped" summary.
If the run gives no usable results, the previous CSV is deleted so it cannot be mistaken for this run's
evidence (the raw log of the failed run is kept). Exit status: 0 when the CSV was written and every test
passed, 1 otherwise. Standard library only.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
RESULTS_DIR = ROOT / "evaluation" / "results"
CSV_PATH = RESULTS_DIR / "solidity_test_results.csv"
LOG_PATH = RESULTS_DIR / "solidity_test_run.log"
HEADER = ["test_id", "requirement", "why_critical", "expected", "actual", "status", "evidence"]

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SUITE_LINE = re.compile(r"^\s{2}(test/\S+\.t\.sol):(\w+)\s*$")
# "    ✔ testName()" or "    ✔ testFuzz(address) (runs: 256)" (the tick glyph varies by terminal)
PASS_LINE = re.compile(r"^\s{4}(?!-\s)[^\w\s]{1,3}\s+(test\w*)\([^)]*\)")
SKIP_LINE = re.compile(r"^\s{4}-\s+(test\w*)\([^)]*\)")  # "    - testName()" (vm.skip)
FAIL_LINE = re.compile(r"^\s{4}(\d+)\)\s+(\w+)\([^)]*\)")  # "    3) testName()" or "    1) setUp()"
FAIL_DETAIL = re.compile(r"^\s{2}(\d+)\)\s+(\w+)#(\w+)\([^)]*\)")  # "  3) Contract#testName()"
SUMMARY = re.compile(r"^\s*(\d+)\s+(passing|failing|skipped)\b")

CONTRACT_DECL = re.compile(r"^\s*(?:abstract\s+)?contract\s+(\w+)", re.MULTILINE)
# forge runs public/external functions whose name starts with "test"; group 2 is the header up to the body.
TEST_FUNCTION = re.compile(r"^[ \t]*function\s+(test\w*)\s*\(([^{;]*)", re.MULTILINE)
RUNNABLE = re.compile(r"\b(public|external)\b")
ID_TAG = re.compile(r"@notice\s+\[([^\]]*)\]\s*(.*)", re.DOTALL)
ID_FORMAT = re.compile(r"SOL-[A-Z]{2}-\d{2}")


class ExportError(Exception):
    """The results cannot be exported without risking a CSV that disagrees with Hardhat."""


@dataclass
class TestMeta:
    test_id: str
    file: str
    contract: str
    function: str
    expected: str
    requirement: str
    why: str


def _natspec_above(text: str, position: int) -> str:
    """Return the /// or /** */ NatSpec block directly above `position`, as plain lines ("" if none)."""
    lines = text[:position].splitlines()
    block: list[str] = []
    while lines and lines[-1].strip().startswith("///"):
        block.insert(0, lines.pop().strip()[3:].strip())
    if block:
        return "\n".join(block)
    if lines and lines[-1].strip().endswith("*/"):
        end = text.rfind("*/", 0, position)
        start = text.rfind("/**", 0, end)
        if start != -1:
            inner = text[start + 3 : end].splitlines()
            return "\n".join(line.strip().lstrip("*").strip() for line in inner)
    return ""


def parse_test_metadata(test_dir: Path = TEST_DIR) -> dict[tuple[str, str], TestMeta]:
    """Read the id and descriptions from the NatSpec above every test function; raise ExportError on a
    function without a well-formed id or on a repeated id."""
    metas: dict[tuple[str, str], TestMeta] = {}
    problems: list[str] = []
    seen_ids: dict[str, str] = {}
    for path in sorted(test_dir.glob("*.t.sol")):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        contracts = [(m.start(), m.group(1)) for m in CONTRACT_DECL.finditer(text)]
        for match in TEST_FUNCTION.finditer(text):
            function = match.group(1)
            if not RUNNABLE.search(match.group(2)):
                continue  # an internal helper named test*: forge does not run it
            owners = [name for start, name in contracts if start < match.start()]
            contract = owners[-1] if owners else "?"
            where = f"{relative}:{contract}#{function}()"
            doc = _natspec_above(text, match.start())
            tag = ID_TAG.search(doc)
            if not tag:
                problems.append(f"{where}: no `@notice [SOL-XX-nn] ...` line in the NatSpec directly above it")
                continue
            test_id, expected = tag.group(1).strip(), " ".join(tag.group(2).split("\n@")[0].split())
            if not ID_FORMAT.fullmatch(test_id):
                problems.append(f"{where}: id [{test_id}] is not in the SOL-XX-nn format")
                continue
            if test_id in seen_ids:
                problems.append(f"{where}: id {test_id} is already used by {seen_ids[test_id]}")
                continue
            seen_ids[test_id] = where
            requirement = re.search(r"@custom:requirement\s+(.+)", doc)
            why = re.search(r"Why it matters:\s*(.+)", doc)
            metas[(contract, function)] = TestMeta(
                test_id=test_id,
                file=relative,
                contract=contract,
                function=function,
                expected=expected,
                requirement=requirement.group(1).strip() if requirement else "",
                why=why.group(1).strip() if why else "",
            )
    if problems:
        raise ExportError("test ids are missing or ambiguous:\n  " + "\n  ".join(problems))
    return metas


def hardhat_command(extra_args: list[str]) -> list[str]:
    return ["npx", "hardhat", *extra_args, "test", "solidity"]


def run_tests(command: list[str]) -> tuple[int, str]:
    """Run Hardhat and return (exit code, console output without colour codes)."""
    if not (ROOT / "node_modules" / "hardhat" / "package.json").is_file():
        # Without this check npx would offer to download Hardhat, and the question would vanish into
        # the captured output.
        raise ExportError("Hardhat is not installed in this project: run `npm ci` in the project root first")
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            shell=(os.name == "nt"),  # npx is npx.cmd on Windows
        )
    except FileNotFoundError as error:
        raise ExportError("npx was not found: install Node.js (see package.json engines) and run `npm ci`") from error
    return completed.returncode, ANSI.sub("", completed.stdout + completed.stderr)


def parse_output(output: str, metas: dict[tuple[str, str], TestMeta]):
    """Return ({(contract, function): (status, actual)}, counts) for every test in the output.

    counts holds Hardhat's own summary ("passing", "failing", "skipped"; a missing key was not printed)
    plus what was parsed from the listing ("failure_entries", "skipped_entries").
    Raise ExportError when a listed test cannot be matched to a tagged function.
    """
    results: dict[tuple[str, str], tuple[str, str]] = {}
    failure_index: dict[str, tuple[str, str]] = {}  # "3" -> (contract, function)
    failure_error: dict[str, str] = {}
    counts: dict[str, int] = {}
    skipped_tests: set[tuple[str, str]] = set()
    current_contract = None
    listing = True  # suite listings come first; the numbered failure details follow the summary
    lines = output.splitlines()

    for i, line in enumerate(lines):
        summary = SUMMARY.match(line)
        if summary:
            counts[summary.group(2)] = int(summary.group(1))
            listing = False
            continue
        detail = FAIL_DETAIL.match(line)
        if detail and not listing:
            number, contract, function = detail.groups()
            failure_index[number] = (contract, function)
            failure_error[number] = next(
                (l.strip() for l in lines[i + 1 : i + 6] if l.strip().startswith("Error")), "failed"
            )
            continue
        if not listing:
            continue
        suite = SUITE_LINE.match(line)
        if suite:
            current_contract = suite.group(2)
            continue
        if current_contract is None:
            continue
        passed = PASS_LINE.match(line)
        if passed:
            results[(current_contract, passed.group(1))] = ("pass", "passed")
            continue
        skipped = SKIP_LINE.match(line)
        if skipped:
            skipped_tests.add((current_contract, skipped.group(1)))
            continue
        failed = FAIL_LINE.match(line)
        if failed:
            failure_index.setdefault(failed.group(1), (current_contract, failed.group(2)))

    for number, (contract, function) in failure_index.items():
        error = failure_error.get(number, "failed")
        if function == "setUp":
            for meta_contract, meta_function in metas:
                if meta_contract == contract:
                    results[(meta_contract, meta_function)] = ("fail", f"not run: setUp() failed: {error}")
        else:
            results[(contract, function)] = ("fail", error)

    listed = set(results) | skipped_tests
    unknown = sorted(f"{contract}#{function}()" for contract, function in listed if (contract, function) not in metas)
    if unknown:
        raise ExportError("Hardhat ran tests that have no tagged function in test/*.t.sol: " + ", ".join(unknown))
    counts["failure_entries"] = len(failure_index)
    counts["skipped_entries"] = len(skipped_tests)
    return results, counts


def check_counts(results, counts) -> None:
    """Compare the parsed results with Hardhat's own summary lines."""
    passed = sum(1 for status, _ in results.values() if status == "pass")
    if "passing" not in counts:
        raise ExportError("Hardhat printed no 'N passing' summary, so the parsed results cannot be checked")
    if passed != counts["passing"]:
        raise ExportError(f"parsed {passed} passing tests but Hardhat reports {counts['passing']} passing")
    if counts["failure_entries"] != counts.get("failing", 0):
        raise ExportError(
            f"parsed {counts['failure_entries']} failures but Hardhat reports {counts.get('failing', 0)} failing"
        )
    if counts["skipped_entries"] != counts.get("skipped", 0):
        raise ExportError(
            f"parsed {counts['skipped_entries']} skipped tests but Hardhat reports {counts.get('skipped', 0)} skipped"
        )


def git_revision() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "contracts", "test"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        return f"{sha}{' + uncommitted contracts/tests' if dirty else ''}" if sha else "unknown commit"
    except OSError:
        return "unknown commit"


def _discard_stale_csv() -> None:
    if CSV_PATH.exists():
        CSV_PATH.unlink()
        print(f"removed the previous {CSV_PATH.relative_to(ROOT)}: it does not match this run", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if any(arg in ("-h", "--help") for arg in argv):
        print(__doc__)
        return 0
    # Everything else goes to Hardhat unchanged and in order (global options such as --config x.ts).
    command = hardhat_command(argv)

    try:
        metas = parse_test_metadata()
        returncode, output = run_tests(command)
    except ExportError as error:
        print(f"export_solidity_results: {error}", file=sys.stderr)
        print("Nothing was run or written.", file=sys.stderr)
        return 1

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(output, encoding="utf-8")
    try:
        results, counts = parse_output(output, metas)
        if not results:
            raise ExportError("no test results in the Hardhat output (compile error?)")
        check_counts(results, counts)
    except ExportError as error:
        print(output)
        print(f"export_solidity_results: {error}", file=sys.stderr)
        print(f"CSV not written; see {LOG_PATH.relative_to(ROOT)}.", file=sys.stderr)
        _discard_stale_csv()
        return 1

    when = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    revision = git_revision()
    command_text = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    rows = []
    for key, meta in sorted(metas.items(), key=lambda item: item[1].test_id):
        if key not in results:
            continue  # not run -> no row
        status, actual = results[key]
        evidence = f"{meta.file}:{meta.contract}#{meta.function}(); {command_text}; {revision}; {when}"
        rows.append([meta.test_id, meta.requirement, meta.why, meta.expected, actual, status, evidence])

    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)

    passed = sum(1 for row in rows if row[5] == "pass")
    blocked = sum(1 for row in rows if row[4].startswith("not run: setUp() failed"))
    failed = len(rows) - passed
    blocked_note = f" ({blocked} of them because setUp() failed)" if blocked else ""
    print(f"{passed} pass, {failed} fail{blocked_note}, {len(metas) - len(rows)} not run -> {CSV_PATH.relative_to(ROOT)}")
    skipped_note = f", {counts['skipped']} skipped" if counts.get("skipped") else ""
    print(f"raw log -> {LOG_PATH.relative_to(ROOT)} (hardhat exit code {returncode}; "
          f"Hardhat summary: {counts['passing']} passing, {counts.get('failing', 0)} failing{skipped_note})")
    if failed or returncode != 0:
        if not failed:
            print("Hardhat exited with an error although every listed test passed; see the raw log.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
