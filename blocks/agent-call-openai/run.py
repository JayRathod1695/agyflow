"""OpenAI-compatible agent adapter block: calls chat completions endpoint, extracts JSON, checks policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import tomllib
from typing import Any
import urllib.error
import urllib.request

from agyflow.lib.agentlib import assemble_prompt, extract_payload
from agyflow.lib.contract import (
    E_AUTH,
    E_CLI,
    E_INPUT,
    E_POLICY,
    E_PROVIDER,
    E_RATE_LIMIT,
    E_SCHEMA,
    E_TIMEOUT,
    EXIT_FAILED,
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    EXIT_RETRYABLE,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)
from agyflow.lib.proc import run_command


def _load_inputs(in_dir: Path) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for fpath in in_dir.glob("*.json"):
        if fpath.name.startswith("_"):
            continue
        try:
            _, payload = read_artifact(fpath)
            inputs[fpath.stem] = payload
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
    shared_file, role_file = prompts_dir / "_shared.md", prompts_dir / f"{role}.md"
    if not shared_file.is_file() or not role_file.is_file():
        write_status(args.out_dir, "failed", E_INPUT, f"Prompt files missing for role '{role}'", complete=False)
        return EXIT_FAILED

    profile_name = str(args.cfg.get("profile", "openai"))
    profile_data = _load_profile(repo_path, profile_name)
    defaults = profile_data.get("defaults", {})
    role_prof = profile_data.get("roles", {}).get(role, {})
    model_name = str(role_prof.get("model", defaults.get("model", profile_data.get("profile", {}).get("model", "gpt-4o-mini"))))
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
        prompt = assemble_prompt(
            shared_file.read_text(encoding="utf-8"),
            role_file.read_text(encoding="utf-8"),
            inputs, vars_dict, int(args.cfg.get("inline_max_bytes", 6000)), retry_info,
        )
    except Exception as err:
        write_status(args.out_dir, "failed", E_INPUT, f"Prompt assembly failed: {err}", complete=False)
        return EXIT_FAILED

    (args.out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    api_key = os.environ.get("OPENAI_API_KEY", "mock-key")
    base_url = os.environ.get("OPENAI_BASE_URL", str(args.cfg.get("base_url", "https://api.openai.com/v1"))).rstrip("/")
    timeout_s = float(args.cfg.get("timeout_s", 60.0))

    req_data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": f"You are acting as role: {role}. Follow instructions strictly."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    t0 = time.time()
    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(req_data).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp_body = resp.read().decode("utf-8")
            raw_json = json.loads(resp_body)
            raw_text = raw_json["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as he:
        code = E_AUTH if he.code in (401, 403) else (E_RATE_LIMIT if he.code == 429 else E_PROVIDER)
        status = "needs_human" if code == E_AUTH else "retryable"
        write_status(args.out_dir, status, code, f"HTTP {he.code}: {he.reason}", complete=False)
        return EXIT_NEEDS_HUMAN if code == E_AUTH else EXIT_RETRYABLE
    except urllib.error.URLError as ue:
        write_status(args.out_dir, "retryable", E_TIMEOUT if "timed out" in str(ue).lower() else E_CLI, str(ue), complete=False)
        return EXIT_RETRYABLE
    except Exception as ex:
        write_status(args.out_dir, "failed", E_PROVIDER, str(ex), complete=False)
        return EXIT_FAILED

    duration = time.time() - t0
    payload = extract_payload(raw_text)
    if payload is None:
        write_status(args.out_dir, "retryable", E_SCHEMA, "Agent reply was not valid JSON", complete=False, metrics={"agent_calls": 1, "duration_s": duration})
        return EXIT_RETRYABLE

    mode = str(args.cfg.get("mode", "readonly"))
    wt_payload = inputs.get("wt")
    if mode == "readonly" and wt_payload and isinstance(wt_payload, dict) and "path" in wt_payload:
        st_res = run_command(["git", "status", "--porcelain=v1"], cwd=Path(wt_payload["path"]))
        if st_res.stdout.strip():
            write_status(args.out_dir, "failed", E_POLICY, "agent modified read-only tree", complete=False)
            return EXIT_FAILED

    out_decls = args.cfg.get("out", {}) or {role: role}
    for out_name, out_type in out_decls.items():
        write_artifact(
            out_dir=args.out_dir, name=out_name, schema_type=out_type, payload=payload,
            step=args.ctx.get("step", "agent_openai"), block="agent-call-openai", profile=profile_name,
        )

    write_status(args.out_dir, status="ok", complete=True, metrics={"agent_calls": 1, "duration_s": duration})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
