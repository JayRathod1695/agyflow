"""agyflow CLI entry point for workflow management, DAG runs, gates, and diagnostics."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import tomllib
from typing import Any

from agyflow.engine.launcher import get_launcher
from agyflow.engine.lock import acquire
from agyflow.engine.pipeline import load_pipeline, save_expanded_pipeline
from agyflow.engine.runner import Pause, Scope, StepFailed, Stop, exec_seq
from agyflow.engine.state import RunState, load_state, repair_state, save_state
from agyflow.engine import store


def _find_flow_dir(repo: Path | None = None) -> Path:
    curr = (repo or Path.cwd()).resolve()
    cand = curr / ".flow"
    if cand.is_dir():
        return cand
    cand.mkdir(parents=True, exist_ok=True)
    return cand


def _latest_run_id(flow_dir: Path) -> str:
    runs_dir = flow_dir / "runs"
    if not runs_dir.is_dir():
        raise FileNotFoundError("No active or historical runs found in .flow/runs")
    runs = sorted([d.name for d in runs_dir.iterdir() if d.is_dir()])
    if not runs:
        raise FileNotFoundError("No runs found in .flow/runs")
    return runs[-1]


def cmd_init(args: argparse.Namespace) -> int:
    flow_dir = _find_flow_dir(Path(args.repo) if args.repo else None)
    for sub in ("locks", "runs", "worktrees"):
        (flow_dir / sub).mkdir(parents=True, exist_ok=True)
    proj_file = flow_dir / "project.toml"
    if not proj_file.exists():
        proj_file.write_text('[project]\nname = "demo"\nallow_dirty = false\n\n[limits]\nmax_rounds = 3\n\n[budget]\nminutes = 60\nagent_calls = 30\n')
    dec_file = flow_dir / "decisions.md"
    if not dec_file.exists():
        dec_file.write_text("# Project Decisions\n")
    gitignore = flow_dir.parent / ".gitignore"
    if gitignore.is_file():
        text = gitignore.read_text(encoding="utf-8")
        additions = [line for line in (".flow/runs/", ".flow/worktrees/", ".flow/locks/") if line not in text]
        if additions:
            gitignore.write_text(text.rstrip() + "\n" + "\n".join(additions) + "\n")
    print(f"Initialized agyflow at {flow_dir}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    flow_dir = _find_flow_dir()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = flow_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_handle = acquire(flow_dir, run_id)
    try:
        proj_cfg: dict[str, Any] = {}
        if (flow_dir / "project.toml").is_file():
            with open(flow_dir / "project.toml", "rb") as f:
                proj_cfg = tomllib.load(f)
        set_params = dict(kv.split("=", 1) for kv in args.set) if args.set else {}
        tree = load_pipeline(args.pipeline, proj_cfg, set_params, flow_dir.parent)
        save_expanded_pipeline(tree, run_dir / "pipeline.expanded.yaml")
        (run_dir / "run.toml").write_text(f'run_id = "{run_id}"\npipeline = "{tree.name}"\n')
        state = RunState(run_id=run_id, pipeline=tree.name)
        save_state(run_dir, state)
        scope = Scope(run_dir, state, proj_cfg, tree.params, exports=dict(tree.exports))
        launcher = get_launcher(args.launcher)

        while True:
            try:
                exec_seq(tree.steps, scope, launcher)
                state.status = "done"
                save_state(run_dir, state)
                print(f"Run {run_id} completed successfully.")
                return 0
            except Pause as p:
                state.status = p.kind
                save_state(run_dir, state)
                print(f"Run paused: {p.kind} at {p.xid} ({p.reason})")
                if args.wait_gate and p.kind == "paused_gate":
                    print("Waiting for gate approval...")
                    time.sleep(3.0)
                    continue
                return 30
            except (Stop, StepFailed) as s:
                state.status = "failed"
                save_state(run_dir, state)
                print(f"Run {run_id} failed: {s}")
                for node in tree.finally_:
                    try: exec_seq([node], scope, launcher)
                    except Exception: pass
                return 1
    finally:
        lock_handle.release()


def cmd_status(args: argparse.Namespace) -> int:
    flow_dir = _find_flow_dir()
    rid = args.run_id or _latest_run_id(flow_dir)
    st = load_state(flow_dir / "runs" / rid)
    print(f"Run ID:   {st.run_id}\nPipeline: {st.pipeline}\nStatus:   {st.status}")
    print(f"Counters: agent_calls={st.counters.get('agent_calls',0)}, active_sec={st.counters.get('active_seconds',0)}")
    for xid, rec in st.steps.items():
        print(f"  [{rec.status.upper()}] {xid} (attempt {rec.attempt})")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    flow_dir = _find_flow_dir()
    rid = _latest_run_id(flow_dir)
    gate_dir = flow_dir / "runs" / rid / "gates"
    req_file = gate_dir / f"{args.gate}.request.json"
    subject_hash = ""
    if req_file.is_file():
        req_data = json.loads(req_file.read_text(encoding="utf-8"))
        subject_hash = req_data.get("subject_hash", "")
    app_data = {
        "gate": args.gate,
        "status": "approved",
        "subject_hash": subject_hash,
        "picks": args.pick.split(",") if args.pick else [],
        "note": args.note or "",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    (gate_dir / f"{args.gate}.approval.json").write_text(json.dumps(app_data, indent=2))
    print(f"Approved gate {args.gate} for run {rid}")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    flow_dir = _find_flow_dir()
    rid = args.run_id or _latest_run_id(flow_dir)
    st = repair_state(flow_dir / "runs" / rid)
    print(f"Repaired state for run {rid}: status={st.status}, {len(st.steps)} steps")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agyflow", description="agyflow DAG Workflow Engine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--repo", default=".")
    p_run = sub.add_parser("run")
    p_run.add_argument("pipeline")
    p_run.add_argument("--set", action="append")
    p_run.add_argument("--wait-gate", action="store_true")
    p_run.add_argument("--launcher", default="subprocess")
    p_stat = sub.add_parser("status")
    p_stat.add_argument("run_id", nargs="?")
    p_app = sub.add_parser("approve")
    p_app.add_argument("gate")
    p_app.add_argument("--pick")
    p_app.add_argument("--note")
    p_rep = sub.add_parser("repair")
    p_rep.add_argument("run_id", nargs="?")

    args = parser.parse_args(argv)
    dispatch = {"init": cmd_init, "run": cmd_run, "status": cmd_status, "approve": cmd_approve, "repair": cmd_repair}
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
