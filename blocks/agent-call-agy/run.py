"""agy CLI agent adapter block: runs agy --print, extracts typed JSON, and checks policy."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tomllib
from typing import Any

from agyflow.lib.agentlib import assemble_prompt, classify_agent_error, extract_payload
from agyflow.lib.contract import (
    E_INPUT,
    E_POLICY,
    E_SCHEMA,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_RETRYABLE,
    load_input_payload,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)
from agyflow.lib.proc import run_command, scrub_env


def _load_inputs(in_dir: Path) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for fpath in in_dir.glob("*.json"):
        if fpath.name.startswith("_"):
            continue
        stem = fpath.stem
        try:
            _, payload = read_artifact(fpath)
            inputs[stem] = payload
        except Exception:
            pass
    return inputs


def _load_profile(repo_path: Path, profile_name: str) -> dict[str, Any]:
    prof_file = repo_path / "profiles" / f"{profile_name}.toml"
    if prof_file.is_file():
        with open(prof_file, "rb") as f:
            return tomllib.load(f)
    return {}


def main() -> int:
    args = parse_cli_args()
    role = str(args.cfg.get("role", "")).strip()
    if not role:
        write_status(args.out_dir, "failed", E_INPUT, "Missing required cfg.role", complete=False)
        return EXIT_FAILED

    repo_path = Path(args.ctx.get("repo", ".")).resolve()
    prompts_dir = repo_path / "prompts"
    shared_file = prompts_dir / "_shared.md"
    role_file = prompts_dir / f"{role}.md"

    if not shared_file.is_file() or not role_file.is_file():
        write_status(
            args.out_dir, "failed", E_INPUT, f"Prompt files missing for role '{role}'", complete=False
        )
        return EXIT_FAILED

    profile_name = str(args.cfg.get("profile", "flash"))
    profile_data = _load_profile(repo_path, profile_name)
    defaults = profile_data.get("defaults", {})
    role_prof = profile_data.get("roles", {}).get(role, {})
    effort = str(role_prof.get("effort", defaults.get("effort", "medium")))
    tool_cap = int(role_prof.get("tool_call_cap", defaults.get("tool_call_cap", 25)))

    inputs = _load_inputs(args.in_dir)
    vars_dict: dict[str, Any] = {
        "tool_call_cap": tool_cap,
        "tool_call_cap_minus3": max(1, tool_cap - 3),
        "head": inputs.get("snapshot", {}).get("head", ""),
        **args.cfg.get("vars", {}),
    }

    retry_info: dict[str, Any] | None = None
    retry_file = args.in_dir / "_retry.json"
    if retry_file.is_file():
        try:
            retry_info = json.loads(retry_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        shared_rules = shared_file.read_text(encoding="utf-8")
        role_content = role_file.read_text(encoding="utf-8")
        inline_max = int(args.cfg.get("inline_max_bytes", 6000))
        prompt = assemble_prompt(shared_rules, role_content, inputs, vars_dict, inline_max, retry_info)
    except Exception as err:
        write_status(args.out_dir, "failed", E_INPUT, f"Prompt assembly failed: {err}", complete=False)
        return EXIT_FAILED

    (args.out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    # Working directory determination
    mode = str(args.cfg.get("mode", "readonly"))
    wt_payload = inputs.get("wt")
    if wt_payload and isinstance(wt_payload, dict) and "path" in wt_payload:
        cwd_path = Path(wt_payload["path"]).resolve()
    else:
        cwd_path = args.out_dir / "_scratch"
        cwd_path.mkdir(parents=True, exist_ok=True)

    step_timeout = int(args.cfg.get("timeout_s", 900))
    print_timeout = max(30, step_timeout - 20)

    cmd = ["agy", "--print", prompt, "--output-format", "json", "--effort", effort]
    cmd.extend(["--print-timeout", f"{print_timeout}s"])
    cmd.append("--disable-slash-commands")

    model_id = profile_data.get("profile", {}).get("model", "")
    if model_id:
        cmd.extend(["--model", str(model_id)])

    if mode == "edit":
        cmd.extend(["--mode", "accept-edits"])
        if args.cfg.get("skip_permissions", False):
            cmd.append("--dangerously-skip-permissions")
    else:
        cmd.extend(["--mode", "plan"])

    env_allow = list(profile_data.get("profile", {}).get("env_allow", []))
    env = scrub_env(env_allow=env_allow, needs_home=True)

    proc_res = run_command(cmd, cwd=cwd_path, env=env, timeout_s=float(step_timeout))
    if proc_res.timed_out or proc_res.returncode != 0:
        exit_code, code, reason = classify_agent_error(proc_res)
        write_status(
            args.out_dir, status="retryable" if exit_code == EXIT_RETRYABLE else "failed",
            code=code, reason=reason, complete=False,
            metrics={"agent_calls": 1, "duration_s": proc_res.duration_s},
        )
        return exit_code

    payload = extract_payload(proc_res.stdout)
    if payload is None:
        write_status(
            args.out_dir, status="retryable", code=E_SCHEMA,
            reason="Agent reply was not valid JSON", complete=False,
            metrics={"agent_calls": 1, "duration_s": proc_res.duration_s},
        )
        return EXIT_RETRYABLE

    if mode == "readonly" and wt_payload:
        st_res = run_command(["git", "status", "--porcelain=v1"], cwd=cwd_path)
        if st_res.stdout.strip():
            write_status(args.out_dir, "failed", E_POLICY, "agent modified read-only tree", complete=False)
            return EXIT_FAILED

    out_decls = args.cfg.get("out", {}) or {role: role}
    for out_name, out_type in out_decls.items():
        write_artifact(
            out_dir=args.out_dir, name=out_name, schema_type=out_type, payload=payload,
            step=args.ctx.get("step", "agent_agy"), block="agent-call-agy", profile=profile_name,
        )

    write_status(args.out_dir, status="ok", complete=True, metrics={"agent_calls": 1, "duration_s": proc_res.duration_s})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
