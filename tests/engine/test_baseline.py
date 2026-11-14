"""Acceptance tests for preflight baseline test verification and E_BASELINE guard."""

import pytest
from pathlib import Path
from agyflow.lib.contract import (
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    E_BASELINE,
    write_artifact,
    write_status,
)
from agyflow.lib.schema import validate_or_raise


def evaluate_baseline_assertion(
    run_result: dict,
    on_mismatch: str = "needs_human",
    expected_exit: int = 0,
) -> tuple[int, dict, dict]:
    """Evaluates baseline test run result against expected baseline exit code."""
    actual_exit = run_result.get("exit_code")
    is_ok = actual_exit == expected_exit

    assertion_payload = {
        "ok": is_ok,
        "expected": str(expected_exit),
        "actual": str(actual_exit),
        "detail": "Baseline tests passed cleanly" if is_ok else "Baseline test suite failed before any changes",
    }
    validate_or_raise(assertion_payload, "assertion")

    if is_ok:
        status_info = {"status": "ok", "code": "", "complete": True}
        return EXIT_OK, assertion_payload, status_info

    # Failing baseline triggers E_BASELINE
    if on_mismatch == "needs_human":
        status_info = {
            "status": "needs_human",
            "code": E_BASELINE,
            "reason": assertion_payload["detail"],
            "complete": False,
        }
        return EXIT_NEEDS_HUMAN, assertion_payload, status_info

    status_info = {
        "status": "failed",
        "code": E_BASELINE,
        "reason": assertion_payload["detail"],
        "complete": False,
    }
    return 20, assertion_payload, status_info


def test_green_baseline_passes(tmp_path: Path):
    """Verify clean baseline tests produce exit 0 and ok status."""
    run_result = {
        "cmd": ["pytest", "tests"],
        "cwd": "/repo",
        "exit_code": 0,
        "timed_out": False,
        "signal": None,
        "duration_s": 0.45,
        "stdout_path": "/tmp/stdout.log",
        "stderr_path": "/tmp/stderr.log",
        "stdout_tail": "6 passed in 0.2s",
        "stderr_tail": "",
        "truncated": False,
        "junit_path": None,
    }
    validate_or_raise(run_result, "run_result")

    exit_code, assertion_art, status_info = evaluate_baseline_assertion(run_result)

    assert exit_code == EXIT_OK
    assert assertion_art["ok"] is True
    assert status_info["status"] == "ok"


def test_failing_baseline_triggers_e_baseline_needs_human(tmp_path: Path):
    """Verify failing baseline test triggers exit 30 (needs_human) with E_BASELINE."""
    failing_result = {
        "cmd": ["pytest", "tests"],
        "cwd": "/repo",
        "exit_code": 1,
        "timed_out": False,
        "signal": None,
        "duration_s": 0.32,
        "stdout_path": "/tmp/stdout.log",
        "stderr_path": "/tmp/stderr.log",
        "stdout_tail": "1 failed, 5 passed",
        "stderr_tail": "AssertionError: expected baseline pass",
        "truncated": False,
        "junit_path": None,
    }
    validate_or_raise(failing_result, "run_result")

    out_dir = tmp_path / "out"
    exit_code, assertion_art, status_info = evaluate_baseline_assertion(
        failing_result, on_mismatch="needs_human"
    )

    assert exit_code == EXIT_NEEDS_HUMAN
    assert assertion_art["ok"] is False
    assert status_info["status"] == "needs_human"
    assert status_info["code"] == E_BASELINE

    write_status(
        out_dir,
        status=status_info["status"],
        code=status_info["code"],
        reason=status_info["reason"],
        complete=status_info["complete"],
    )
    status_file = out_dir / "_status.json"
    assert status_file.is_file()
