"""Command runner block: executes CLI commands in process groups with scrubbed environment."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
from typing import Any

from agyflow.lib.contract import (
    E_CLI,
    E_INPUT,
    EXIT_FAILED,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)
from agyflow.lib.proc import run_command, scrub_env


def main() -> int:
    args = parse_cli_args()
    wt_file = args.in_dir / "wt.json"
    if wt_file.is_file():
        wt_payload = load_input_payload(args.in_dir, "wt")
        base_cwd = Path(wt_payload["path"]).resolve()
    else:
        base_cwd = Path(args.ctx.get("repo", ".")).resolve()

    cwd_rel = str(args.cfg.get("cwd_rel", "")).strip()
    cwd = (base_cwd / cwd_rel).resolve() if cwd_rel else base_cwd

    raw_cmd = args.cfg.get("cmd", [])
    if isinstance(raw_cmd, str):
        cmd = [raw_cmd]
    else:
        cmd = list(raw_cmd)

    if not cmd:
        write_status(args.out_dir, "failed", E_INPUT, "cfg.cmd is empty", complete=False)
        return EXIT_FAILED

    run_files_dir = args.out_dir / "run_files"
    run_files_dir.mkdir(parents=True, exist_ok=True)

    junit_in_wt = base_cwd / ".agyflow_junit.xml"
    if junit_in_wt.is_file():
        try:
            junit_in_wt.unlink()
        except OSError:
            pass

    expanded_cmd: list[str] = []
    has_junit = False
    for token in cmd:
        tok_str = str(token)
        if "{junit}" in tok_str:
            expanded_cmd.append(tok_str.replace("{junit}", str(junit_in_wt)))
            has_junit = True
        else:
            expanded_cmd.append(tok_str)

    env_allow = list(args.cfg.get("env_allow", []))
    env = scrub_env(env_allow=env_allow, needs_home=False)
    scratch_home = args.out_dir / "_home"
    scratch_home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(scratch_home)

    set_env = args.cfg.get("set_env", {})
    if isinstance(set_env, dict):
        for k, v in set_env.items():
            env[str(k)] = str(v)

    timeout_s = float(args.cfg.get("timeout_s", 900.0))
    max_bytes = int(args.cfg.get("max_output_bytes", 1_000_000))

    try:
        proc_res = run_command(
            expanded_cmd,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            max_output_bytes=max_bytes,
        )
    except FileNotFoundError as err:
        write_status(
            args.out_dir,
            status="failed",
            code=E_CLI,
            reason=f"Executable not found: {err}",
            complete=False,
        )
        return EXIT_FAILED
    except Exception as err:
        write_status(
            args.out_dir,
            status="failed",
            code=E_INPUT,
            reason=f"Failed to launch command: {err}",
            complete=False,
        )
        return EXIT_FAILED

    stdout_file = run_files_dir / "stdout.txt"
    stderr_file = run_files_dir / "stderr.txt"
    stdout_file.write_text(proc_res.stdout, encoding="utf-8")
    stderr_file.write_text(proc_res.stderr, encoding="utf-8")

    junit_path_out: str | None = None
    if has_junit and junit_in_wt.is_file():
        dest_junit = run_files_dir / "junit.xml"
        shutil.copy2(junit_in_wt, dest_junit)
        junit_path_out = str(dest_junit)

    stdout_tail = proc_res.stdout[-1000:] if proc_res.stdout else ""
    stderr_tail = proc_res.stderr[-1000:] if proc_res.stderr else ""

    signal_str: str | None = None
    if proc_res.returncode and proc_res.returncode < 0:
        signal_str = f"SIG{abs(proc_res.returncode)}"

    payload: dict[str, Any] = {
        "cmd": expanded_cmd,
        "cwd": str(cwd),
        "exit_code": proc_res.returncode,
        "timed_out": proc_res.timed_out,
        "signal": signal_str,
        "duration_s": proc_res.duration_s,
        "stdout_path": str(stdout_file),
        "stderr_path": str(stderr_file),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "truncated": proc_res.truncated,
        "junit_path": junit_path_out,
    }

    write_artifact(
        out_dir=args.out_dir,
        name="run_result",
        schema_type="run_result",
        payload=payload,
        step=args.ctx.get("step", "run_cmd"),
        block="run-cmd",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
