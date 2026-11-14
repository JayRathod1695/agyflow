"""agyflow CLI entry point for workflow management, DAG runs, gates, and diagnostics."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time
import tomllib
from typing import Any

from agyflow.engine.launcher import get_launcher
from agyflow.engine.lock import acquire
from agyflow.engine.pipeline import load_pipeline, save_expanded_pipeline
from agyflow.engine.policy import BudgetExceededError
from agyflow.engine.runner import Pause, Scope, StepFailed, Stop, exec_seq
from agyflow.engine.state import RunState, load_state, repair_state, save_state


def _find_flow_dir(repo: Path | None = None) -> Path:
    cand = (repo or Path.cwd()).resolve() / ".flow"
    cand.mkdir(parents=True, exist_ok=True); return cand


def _latest_run_id(flow_dir: Path) -> str:
    runs = sorted([d.name for d in (flow_dir / "runs").iterdir() if d.is_dir()]) if (flow_dir / "runs").is_dir() else []
    if not runs: raise FileNotFoundError("No active runs in .flow/runs")
    return runs[-1]


def cmd_init(args: argparse.Namespace) -> int:
    fd = _find_flow_dir(Path(args.repo) if args.repo else None)
    for sub in ("locks", "runs", "worktrees"): (fd / sub).mkdir(parents=True, exist_ok=True)
    pf, df = fd / "project.toml", fd / "decisions.md"
    if not pf.exists(): pf.write_text('[project]\nname = "demo"\n\n[limits]\nmax_rounds = 3\n\n[budget]\nminutes = 60\nagent_calls = 30\n')
    if not df.exists(): df.write_text("# Decisions\n")
    gi = fd.parent / ".gitignore"
    if gi.is_file():
        t = gi.read_text(encoding="utf-8")
        adds = [l for l in (".flow/runs/", ".flow/worktrees/", ".flow/locks/") if l not in t]
        if adds: gi.write_text(t.rstrip() + "\n" + "\n".join(adds) + "\n")
    print(f"Initialized agyflow at {fd}"); return 0


def _run_engine(run_dir: Path, flow_dir: Path, tree: Any, state: RunState, proj_cfg: dict, launcher_name: str, wait_gate: bool) -> int:
    scope = Scope(run_dir, state, proj_cfg, tree.params, exports=dict(tree.exports))
    launcher = get_launcher(launcher_name)
    while True:
        try:
            exec_seq(tree.steps, scope, launcher)
            state.status = "done"; save_state(run_dir, state); print(f"Run {state.run_id} done."); return 0
        except Pause as p:
            state.status = p.kind; save_state(run_dir, state)
            gate_name = p.xid.split("/")[-1]
            print(f"Run paused ({p.kind}) at {p.xid}. Review: {run_dir / 'gates' / f'{gate_name}.review.md'}")
            if wait_gate and p.kind == "paused_gate":
                app_path = run_dir / "gates" / f"{gate_name}.approval.json"
                print("Waiting for approval file...");
                while not app_path.is_file(): time.sleep(1.0)
                print(f"Gate {gate_name} approved. Resuming..."); continue
            return 30
        except (Stop, BudgetExceededError) as s:
            is_budget = isinstance(s, BudgetExceededError) or (isinstance(s, Stop) and getattr(s, "kind", "") == "stopped_budget")
            state.status = "stopped_budget" if is_budget else "failed"
            save_state(run_dir, state); print(f"Run {state.run_id} {state.status}: {s}")
            for node in tree.finally_:
                try: exec_seq([node], scope, launcher)
                except Exception: pass
            return 2 if is_budget else 1
        except StepFailed as s:
            state.status = "failed"; save_state(run_dir, state); print(f"Run {state.run_id} failed: {s}")
            for node in tree.finally_:
                try: exec_seq([node], scope, launcher)
                except Exception: pass
            return 1


def cmd_run(args: argparse.Namespace) -> int:
    fd = _find_flow_dir()
    rid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rdir = fd / "runs" / rid; rdir.mkdir(parents=True, exist_ok=True)
    handle = acquire(fd, rid)
    try:
        pcfg = tomllib.loads((fd / "project.toml").read_text(encoding="utf-8")) if (fd / "project.toml").is_file() else {}
        tree = load_pipeline(args.pipeline, pcfg, dict(kv.split("=", 1) for kv in args.set) if args.set else {}, fd.parent)
        save_expanded_pipeline(tree, rdir / "pipeline.expanded.yaml")
        (rdir / "run.toml").write_text(f'run_id = "{rid}"\npipeline = "{tree.name}"\n')
        st = RunState(run_id=rid, pipeline=tree.name); save_state(rdir, st)
        return _run_engine(rdir, fd, tree, st, pcfg, args.launcher, args.wait_gate)
    finally: handle.release()


def cmd_resume(args: argparse.Namespace) -> int:
    fd = _find_flow_dir()
    rid = args.run_id or _latest_run_id(fd); rdir = fd / "runs" / rid
    handle = acquire(fd, rid)
    try:
        st = load_state(rdir)
        pcfg = tomllib.loads((fd / "project.toml").read_text(encoding="utf-8")) if (fd / "project.toml").is_file() else {}
        tree = load_pipeline(st.pipeline, pcfg, repo_root=fd.parent)
        print(f"Resuming run {rid} (current: {st.status})...")
        return _run_engine(rdir, fd, tree, st, pcfg, "subprocess", getattr(args, "wait_gate", False))
    finally: handle.release()


def cmd_status(args: argparse.Namespace) -> int:
    fd = _find_flow_dir(); rid = args.run_id or _latest_run_id(fd)
    st = load_state(fd / "runs" / rid)
    print(f"Run ID:   {st.run_id}\nPipeline: {st.pipeline}\nStatus:   {st.status}")
    if st.pending.get("gate"):
        g = st.pending["gate"]
        print(f"Pending Gate: {g}\nReview Path:  {fd / 'runs' / rid / 'gates' / f'{g}.review.md'}")
    print(f"Counters: agent_calls={st.counters.get('agent_calls',0)}, active_sec={st.counters.get('active_seconds',0)}")
    for xid, rec in st.steps.items(): print(f"  [{rec.status.upper()}] {xid} (attempt {rec.attempt})")
    return 0


def _write_gate_response(gate: str, decision: str, picks: list[str], accept: list[str], reject: list[str], note: str) -> int:
    fd = _find_flow_dir(); rid = _latest_run_id(fd); gdir = fd / "runs" / rid / "gates"
    req_file = gdir / f"{gate}.request.json"
    if not req_file.is_file(): print(f"Request file not found: {req_file}"); return 1
    sub_hash = json.loads(req_file.read_text(encoding="utf-8")).get("payload", {}).get("subject_hash", "")
    data = {"gate": gate, "decision": decision, "subject_hash": sub_hash, "picks": picks, "accept": accept, "reject": reject, "note": note, "decided_at": datetime.now(timezone.utc).isoformat()}
    (gdir / f"{gate}.approval.json").write_text(json.dumps(data, indent=2))
    print(f"{decision.capitalize()}d gate {gate} for run {rid}"); return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _write_gate_response(args.gate, "approve", args.pick.split(",") if args.pick else [],
                                args.accept.split(",") if getattr(args, "accept", None) else [],
                                args.reject.split(",") if getattr(args, "reject", None) else [], args.note or "")


def cmd_reject(args: argparse.Namespace) -> int:
    return _write_gate_response(args.gate, "reject", [], [], [], args.note or "")


def cmd_resolve(args: argparse.Namespace) -> int:
    fd = _find_flow_dir(); rid = args.run_id or getattr(args, "opt_run_id", "") or _latest_run_id(fd)
    rdir = fd / "runs" / rid; st = load_state(rdir); xid = args.step
    if args.discard:
        sdir = rdir / "steps" / xid
        if (sdir / "out").exists(): shutil.rmtree(sdir / "out")
        if xid in st.steps: del st.steps[xid]
        st.status = "running"; save_state(rdir, st)
        print(f"Resolved step {xid} (--discard): cleared for re-run")
    elif args.accept:
        st.mark_committed(xid, "synthetic:accepted")
        st.status = "running"; save_state(rdir, st)
        print(f"Resolved step {xid} (--accept): marked committed")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    fd = _find_flow_dir(); rid = args.run_id or _latest_run_id(fd)
    st = repair_state(fd / "runs" / rid)
    print(f"Repaired state for run {rid}: status={st.status}, {len(st.steps)} steps"); return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agyflow", description="agyflow DAG Workflow Engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init"); p_init.add_argument("--repo", default=".")
    p_run = sub.add_parser("run"); p_run.add_argument("pipeline"); p_run.add_argument("--set", action="append")
    p_run.add_argument("--wait-gate", action="store_true"); p_run.add_argument("--launcher", default="subprocess")
    p_res = sub.add_parser("resume"); p_res.add_argument("run_id", nargs="?"); p_res.add_argument("--wait-gate", action="store_true")
    p_stat = sub.add_parser("status"); p_stat.add_argument("run_id", nargs="?")
    p_app = sub.add_parser("approve"); p_app.add_argument("gate"); p_app.add_argument("--pick"); p_app.add_argument("--accept"); p_app.add_argument("--reject"); p_app.add_argument("--note")
    p_rej = sub.add_parser("reject"); p_rej.add_argument("gate"); p_rej.add_argument("--note")
    p_rsl = sub.add_parser("resolve"); p_rsl.add_argument("step"); p_rsl.add_argument("run_id", nargs="?", default=""); p_rsl.add_argument("--discard", action="store_true"); p_rsl.add_argument("--accept", action="store_true"); p_rsl.add_argument("--run-id", dest="opt_run_id", default="")
    p_rep = sub.add_parser("repair"); p_rep.add_argument("run_id", nargs="?")

    args = p.parse_args(argv)
    disp = {"init": cmd_init, "run": cmd_run, "resume": cmd_resume, "status": cmd_status, "approve": cmd_approve, "reject": cmd_reject, "resolve": cmd_resolve, "repair": cmd_repair}
    return disp[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
