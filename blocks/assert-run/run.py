"""Assertion block: verifies run_result exit code and timeout against expectations."""

from __future__ import annotations

import sys

from agyflow.lib.contract import (
    E_BASELINE,
    E_POLICY,
    EXIT_FAILED,
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)


def main() -> int:
    args = parse_cli_args()
    run_result = load_input_payload(args.in_dir, "run_result")

    expect_exit = int(args.cfg.get("expect_exit", 0))
    forbid_timeout = bool(args.cfg.get("forbid_timeout", True))
    actual_exit = run_result.get("exit_code")
    timed_out = bool(run_result.get("timed_out", False))

    is_exit_match = (actual_exit == expect_exit)
    is_timeout_ok = not (forbid_timeout and timed_out)
    ok = is_exit_match and is_timeout_ok

    expected_str = f"exit_code={expect_exit}" + (", no_timeout" if forbid_timeout else "")
    actual_str = f"exit_code={actual_exit}, timed_out={timed_out}"

    stderr_tail = str(run_result.get("stderr_tail", ""))
    reason_detail = stderr_tail[-300:] if stderr_tail else "command output mismatch"
    detail = "Assertion passed" if ok else f"Assertion failed: {reason_detail}"

    assertion_payload = {
        "ok": ok,
        "expected": expected_str,
        "actual": actual_str,
        "detail": detail,
    }

    write_artifact(
        out_dir=args.out_dir,
        name="assertion",
        schema_type="assertion",
        payload=assertion_payload,
        step=args.ctx.get("step", "assert_run"),
        block="assert-run",
    )

    if ok:
        write_status(args.out_dir, status="ok", complete=True)
        return EXIT_OK

    on_mismatch = str(args.cfg.get("on_mismatch", "failed")).lower()
    code = str(args.cfg.get("code", E_BASELINE if on_mismatch == "needs_human" else E_POLICY))

    if on_mismatch == "needs_human":
        write_status(
            args.out_dir,
            status="needs_human",
            code=code,
            reason=f"Baseline assertion failed: expected {expected_str}, got {actual_str}. Stderr: {reason_detail}",
            complete=False,
        )
        return EXIT_NEEDS_HUMAN

    write_status(
        args.out_dir,
        status="failed",
        code=code,
        reason=f"Run assertion failed: expected {expected_str}, got {actual_str}. Stderr: {reason_detail}",
        complete=False,
    )
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
