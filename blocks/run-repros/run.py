"""Repro runner block: executes candidate defect repros in isolated worktree."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
from typing import Any

from agyflow.lib.contract import (
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)
from agyflow.lib.proc import run_command, scrub_env


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().split())


def main() -> int:
    args = parse_cli_args()
    findings = load_input_payload(args.in_dir, "findings")
    wt = load_input_payload(args.in_dir, "wt")
    wt_path = Path(wt["path"]).resolve()

    rnd = int(findings.get("round", 1))
    repeat = int(args.cfg.get("repeat", 2))
    timeout_s = float(args.cfg.get("timeout_s", 60.0))
    env_allow = list(args.cfg.get("env_allow", []))

    repro_dir = wt_path / ".agyflow_repro"
    repro_dir.mkdir(parents=True, exist_ok=True)

    scratch_home = args.out_dir / "_scratch_home"
    scratch_home.mkdir(parents=True, exist_ok=True)
    env = scrub_env(env_allow=env_allow, needs_home=False)
    env["HOME"] = str(scratch_home)

    verified_items: list[dict[str, Any]] = []

    for f in findings.get("findings", []):
        file_path = str(f.get("evidence", {}).get("file", ""))
        claim_key = str(f.get("claim_key", ""))
        fp_str = f"{file_path}|{_norm(claim_key)}"
        fp = hashlib.sha256(fp_str.encode("utf-8")).hexdigest()[:16]

        repro = f.get("repro", {})
        kind = repro.get("kind")

        if kind == "reasoning":
            status = "UNRUNNABLE"
            argument = str(repro.get("argument", ""))
            repro_result = None
        else:
            runs: list[dict[str, Any]] = []
            last_stdout = ""
            for _ in range(repeat):
                if kind == "python_snippet":
                    script = repro_dir / f"{f['id']}.py"
                    script.write_text(str(repro.get("code", "")), encoding="utf-8")
                    res = run_command(["python3", str(script)], cwd=wt_path, env=env, timeout_s=timeout_s)
                elif kind == "pytest_node":
                    node = str(repro.get("node", "")).strip()
                    res = run_command(["python3", "-m", "pytest", "-x", "-q", node], cwd=wt_path, env=env, timeout_s=timeout_s)
                else:
                    res = run_command(["python3", "-c", "exit(1)"], cwd=wt_path, env=env, timeout_s=timeout_s)

                last_stdout = res.stdout
                ev_line: str | None = None
                for line in res.stdout.splitlines():
                    if line.strip().startswith("EVIDENCE:"):
                        ev_line = line.strip()
                        break

                runs.append({
                    "exit_code": res.returncode,
                    "evidence_line": ev_line,
                    "timed_out": res.timed_out,
                    "duration_s": res.duration_s,
                })

            if all(r["exit_code"] == 0 and r["evidence_line"] for r in runs):
                status = "CONFIRMED"
            elif all(r["exit_code"] == 1 for r in runs):
                status = "DROPPED"
            else:
                status = "ERROR"

            argument = None
            repro_result = {
                "runs": runs,
                "stdout_tail": last_stdout[-1000:] if last_stdout else "",
            }

        ev = f.get("evidence", {})
        item = {
            "id": f.get("id", "F-000"),
            "fingerprint": fp,
            "severity": f.get("severity", "LOW"),
            "category": f.get("category", "code"),
            "claim": f.get("claim", ""),
            "claim_key": claim_key,
            "evidence": {
                "file": file_path,
                "line": int(ev.get("line", 1)),
                "end_line": int(ev.get("end_line", ev.get("line", 1))),
                "quote": str(ev.get("quote", "")),
            },
            "status": status,
            "repro_result": repro_result,
            "argument": argument,
            "first_round": rnd,
            "last_round": rnd,
        }
        verified_items.append(item)

    payload = {
        "round": rnd,
        "items": verified_items,
    }

    write_artifact(
        out_dir=args.out_dir,
        name="verified",
        schema_type="verified",
        payload=payload,
        step=args.ctx.get("step", "repros"),
        block="run-repros",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
