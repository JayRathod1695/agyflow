"""Harness for testing block compliance with agyflow Section 4 Unix contract."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

BLOCKS_ROOT = Path(__file__).resolve().parent.parent.parent / "blocks"

M1_BLOCK_NAMES = [
    "repo-snapshot",
    "context-fresh",
    "project-publish",
    "worktree-create",
    "worktree-destroy",
    "agent-call-agy",
    "agent-call-fake",
    "validate-evidence",
    "report-write",
]

M2_BLOCK_NAMES = [
    "test-lock",
    "run-cmd",
    "assert-run",
    "parse-junit",
    "run-repros",
    "select-findings",
    "converge-check",
]

M3_BLOCK_NAMES = [
    "gate-human",
    "validate-distinct",
    "validate-urls",
]

M4_BLOCK_NAMES = [
    "pick-item",
    "patch-export",
    "diff-guard",
    "merge-apply",
]

ALL_BLOCK_NAMES = M1_BLOCK_NAMES + M2_BLOCK_NAMES + M3_BLOCK_NAMES + M4_BLOCK_NAMES


class ContractTestResult(NamedTuple):
    block_name: str
    test_name: str
    passed: bool
    exit_code: int
    message: str


def find_block_entrypoint(block_name: str) -> Path | None:
    """Locate the executable or run.py for a block."""
    block_dir = BLOCKS_ROOT / block_name
    candidates = [
        block_dir / "run.py",
        block_dir / "main.py",
        block_dir / f"{block_name.replace('-', '_')}.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def run_block_command(
    cmd: list[str], timeout_sec: float = 10.0
) -> subprocess.CompletedProcess:
    """Execute a block command under Python 3.11 with timeout."""
    env = os.environ.copy()
    src_dir = str(BLOCKS_ROOT.parent / "src")
    if "PYTHONPATH" in env and env["PYTHONPATH"]:
        env["PYTHONPATH"] = f"{src_dir}:{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = src_dir

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=env,
    )


def check_block_help_contract(block_name: str) -> ContractTestResult:
    entry = find_block_entrypoint(block_name)
    if not entry:
        return ContractTestResult(block_name, "help_contract", False, -1, "Block missing")
    res = run_block_command([sys.executable, str(entry), "--help"])
    passed = res.returncode == 0 and "help" in (res.stdout + res.stderr).lower()
    return ContractTestResult(
        block_name, "help_contract", passed, res.returncode, res.stderr or res.stdout
    )


def check_missing_args_contract(block_name: str) -> ContractTestResult:
    entry = find_block_entrypoint(block_name)
    if not entry:
        return ContractTestResult(block_name, "missing_args", False, -1, "Block missing")
    res = run_block_command([sys.executable, str(entry)])
    # Contract specifies 20 (failed/contract breach) or non-zero arg parse exit
    passed = res.returncode in (2, 20)
    return ContractTestResult(
        block_name, "missing_args", passed, res.returncode, "Rejected missing arguments"
    )


def check_bad_json_cfg_contract(block_name: str) -> ContractTestResult:
    entry = find_block_entrypoint(block_name)
    if not entry:
        return ContractTestResult(block_name, "bad_cfg", False, -1, "Block missing")
    with tempfile.TemporaryDirectory() as in_dir, tempfile.TemporaryDirectory() as out_dir:
        res = run_block_command([
            sys.executable,
            str(entry),
            "--in",
            in_dir,
            "--out",
            out_dir,
            "--cfg",
            "{ not_valid_json }",
        ])
        # Contract requires rejection (exit code 20 preferred, 1/2 acceptable on unhandled error)
        passed = res.returncode in (1, 2, 20)
        msg = f"Rejected malformed cfg with exit code {res.returncode}"
        return ContractTestResult(block_name, "bad_cfg", passed, res.returncode, msg)


def check_nonexistent_in_dir_contract(block_name: str) -> ContractTestResult:
    entry = find_block_entrypoint(block_name)
    if not entry:
        return ContractTestResult(block_name, "bad_in_dir", False, -1, "Block missing")
    with tempfile.TemporaryDirectory() as out_dir:
        missing_in = str(Path(out_dir) / "does_not_exist")
        res = run_block_command([
            sys.executable,
            str(entry),
            "--in",
            missing_in,
            "--out",
            out_dir,
            "--cfg",
            "{}",
        ])
        # Blocks requiring input reject non-zero; gate-human pauses with 30; root/cleanup tolerate optional input
        if block_name in (
            "context-fresh",
            "worktree-create",
            "agent-call-agy",
            "agent-call-fake",
            "validate-evidence",
            "assert-run",
            "parse-junit",
            "run-repros",
            "select-findings",
            "converge-check",
            "validate-distinct",
            "validate-urls",
            "pick-item",
            "patch-export",
            "diff-guard",
            "merge-apply",
        ):
            passed = res.returncode in (1, 2, 20)
        elif block_name == "gate-human":
            passed = res.returncode in (20, 30)
        else:
            passed = res.returncode in (0, 1, 2, 20, 30)
        msg = f"Handled non-existent --in with exit code {res.returncode}"
        return ContractTestResult(block_name, "bad_in_dir", passed, res.returncode, msg)
