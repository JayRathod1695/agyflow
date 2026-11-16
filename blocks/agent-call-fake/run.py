"""Fake agent adapter block: returns pre-recorded JSON responses for tests and doctor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from agyflow.lib.contract import (
    E_EMPTY,
    E_INPUT,
    E_PROVIDER,
    E_RATE_LIMIT,
    E_SCHEMA,
    E_TIMEOUT,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_RETRYABLE,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)

RETRYABLE_CODES = {E_TIMEOUT, E_RATE_LIMIT, E_PROVIDER, E_SCHEMA, E_EMPTY}


def _determine_attempt(args_in_dir: Path, default_attempt: int) -> int:
    retry_file = args_in_dir / "_retry.json"
    if retry_file.is_file():
        try:
            data = json.loads(retry_file.read_text(encoding="utf-8"))
            return int(data.get("attempt", default_attempt))
        except Exception:
            return default_attempt
    return default_attempt


def _check_scripted_failure(fail_cfg: dict[str, Any], attempt: int) -> tuple[int, str, str] | None:
    if not fail_cfg:
        return None
    # Format 1: {"E_TIMEOUT": 2} -> fail with E_TIMEOUT for attempts <= 2
    for code, max_att in fail_cfg.items():
        if isinstance(max_att, int) and attempt <= max_att:
            exit_code = EXIT_RETRYABLE if code in RETRYABLE_CODES else EXIT_FAILED
            return exit_code, code, f"Scripted failure: {code} on attempt {attempt}"
    # Format 2: {"1": "E_SCHEMA"} -> fail specific attempt
    attempt_key = str(attempt)
    if attempt_key in fail_cfg:
        code = str(fail_cfg[attempt_key])
        exit_code = EXIT_RETRYABLE if code in RETRYABLE_CODES else EXIT_FAILED
        return exit_code, code, f"Scripted failure: {code} on attempt {attempt}"
    return None


def main() -> int:
    args = parse_cli_args()
    role = str(args.cfg.get("role", "")).strip()
    attempt = _determine_attempt(args.in_dir, int(args.ctx.get("attempt", 1)))

    fail_check = _check_scripted_failure(args.cfg.get("fail", {}), attempt)
    if fail_check:
        exit_code, fail_code, fail_reason = fail_check
        write_status(
            args.out_dir,
            status="retryable" if exit_code == EXIT_RETRYABLE else "failed",
            code=fail_code,
            reason=fail_reason,
            complete=False,
            metrics={"agent_calls": 1, "duration_s": 0.01},
        )
        return exit_code

    script_dir_val = args.cfg.get("script_dir") or os.environ.get("AGYFLOW_FIXTURES_DIR")
    if script_dir_val:
        script_dir = Path(script_dir_val).resolve()
    else:
        repo_path = Path(args.ctx.get("repo", ".")).resolve()
        script_dir = repo_path / "tests" / "fixtures"

    candidates = [
        script_dir / f"{role}.{attempt}.json",
        script_dir / f"{role}.json",
    ]
    script_file: Path | None = None
    for cand in candidates:
        if cand.is_file():
            script_file = cand
            break

    if script_file is None:
        write_status(
            args.out_dir,
            status="failed",
            code=E_INPUT,
            reason=f"Fake script not found for role '{role}' in {script_dir}",
            complete=False,
        )
        return EXIT_FAILED

    try:
        raw_data = json.loads(script_file.read_text(encoding="utf-8"))
    except Exception as err:
        write_status(
            args.out_dir,
            status="failed",
            code=E_INPUT,
            reason=f"Failed to parse script JSON from {script_file}: {err}",
            complete=False,
        )
        return EXIT_FAILED

    wt_file = args.in_dir / "wt.json"
    if wt_file.is_file() and "edits" in raw_data:
        try:
            _, wt_data = read_artifact(wt_file)
            wt_root = Path(wt_data.get("path", "."))
            for edit in raw_data.get("edits", []):
                target_p = wt_root / edit["path"]
                target_p.parent.mkdir(parents=True, exist_ok=True)
                target_p.write_text(edit["content"], encoding="utf-8")
        except Exception:
            pass

    payload = raw_data.get("payload", raw_data)

    out_decls: dict[str, str] = args.cfg.get("out", {})
    if not out_decls:
        default_name = role if role else "output"
        out_decls = {default_name: default_name}

    for out_name, out_type in out_decls.items():
        write_artifact(
            out_dir=args.out_dir,
            name=out_name,
            schema_type=out_type,
            payload=payload,
            step=args.ctx.get("step", "agent_fake"),
            block="agent-call-fake",
        )

    write_status(
        args.out_dir,
        status="ok",
        complete=True,
        metrics={"agent_calls": 1, "duration_s": 0.05},
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
