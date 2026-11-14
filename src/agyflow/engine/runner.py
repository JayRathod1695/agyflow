"""DAG execution runner, scope management, foreach collections, and gate suspension."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import time
from typing import Any

from agyflow.engine.expr import eval_expr
from agyflow.engine.launcher import Launcher
from agyflow.engine import policy, registry, store
from agyflow.engine.pipeline import PipelineNode, substitute_variables
from agyflow.engine.state import RunState, append_event, save_state


class Pause(Exception):
    def __init__(self, kind: str, xid: str = "", reason: str = "") -> None:
        super().__init__(f"Pause({kind}, {xid}, {reason})"); self.kind, self.xid, self.reason = kind, xid, reason

class Stop(Exception):
    def __init__(self, kind: str, reason: str = "") -> None:
        super().__init__(f"Stop({kind}, {reason})"); self.kind, self.reason = kind, reason

class StepFailed(Exception): pass


@dataclass
class Scope:
    run_dir: Path
    state: RunState
    project_cfg: dict[str, Any]
    params: dict[str, Any]
    loop_id: str = ""
    loop_iter: int = 0
    loop_path: str = ""
    item_id: str = ""
    as_name: str = ""
    in_foreach: bool = False
    exports: dict[str, Any] = field(default_factory=dict)

    def child(self, loop: str = "", iter: int = 0, item: str = "", as_var: str = "", exports: dict[str, Any] | None = None) -> Scope:
        lpath = f"{self.loop_path}/{loop}.{iter}" if (self.loop_path and loop) else (f"{loop}.{iter}" if loop else self.loop_path)
        return Scope(self.run_dir, self.state, self.project_cfg, self.params,
                     loop or self.loop_id, iter or self.loop_iter, lpath, item or self.item_id,
                     as_var or self.as_name, bool(item) or self.in_foreach, exports if exports is not None else self.exports)

    def exec_id(self, step_id: str) -> str:
        pfx = f"{self.loop_path}/" if self.loop_path else ""
        return f"{pfx}item.{self.item_id}/{step_id}" if self.item_id else f"{pfx}{step_id}"

    def lookup_dict(self) -> dict[str, Any]:
        d = {k: v for s in ("project", "limits", "budget") for k, v in self.project_cfg.get(s, {}).items()} | self.params | {f"params.{k}": v for k, v in self.params.items()}
        d["loop.iter"], d["item.id"] = self.loop_iter, self.item_id
        if self.as_name and self.item_id: d[self.as_name] = self.item_id
        return d

    def resolve_ref_path(self, ref: str) -> Path | None:
        for opt in [r.strip().lstrip("$") for r in ref.split("||")]:
            if opt in self.exports:
                exp = self.exports[opt]
                if isinstance(exp, Path) and exp.exists(): return exp
                if isinstance(exp, str) and (res := self.resolve_ref_path(exp)): return res
            if "[*]." in opt and (c := store.build_collection(self.run_dir, opt)): return c
            if opt.startswith("project."):
                cand = self.run_dir.parent.parent / (f"{opt[8:]}.json" if opt[8:] == "CONTEXT" else f"{opt[8:]}.md")
                if cand.is_file(): return cand
            if opt.startswith("prev."):
                if self.loop_iter <= 1: continue
                pfx = self.loop_path.rsplit("/", 1)[0] if "/" in self.loop_path else ""
                prev_d = self.run_dir / "steps" / (f"{pfx}/{self.loop_id}.{self.loop_iter - 1}" if pfx else f"{self.loop_id}.{self.loop_iter - 1}")
                tgt = opt[5:]; sname, _, art = tgt.partition(".")
                if (ev := self.exports.get(tgt) or self.exports.get(art)):
                    es, _, ea = str(ev).lstrip("$").partition(".")
                    if (c := prev_d / es / "out" / (ea if ea.endswith(".json") else f"{ea}.json")).is_file(): return c
                fname = art if art.endswith(".json") else f"{art}.json"
                for sub in (sname, f"{sname}_{art}"):
                    if (c := prev_d / sub / "out" / fname).is_file(): return c
                continue
            sname, _, art = opt.partition(".")
            fname = art if art.endswith(".json") else f"{art}.json"
            if self.loop_path and (c := self.run_dir / "steps" / self.loop_path / sname / "out" / fname).is_file(): return c
            if "/" in self.loop_path and (c := self.run_dir / "steps" / self.loop_path.rsplit("/", 1)[0] / sname / "out" / fname).is_file(): return c
            if (c := self.run_dir / "steps" / sname / "out" / fname).is_file(): return c
        return None

    def resolve_ref_value(self, ref_path: str) -> Any:
        clean = ref_path.lstrip("$")
        pfx, rest_ref = ("prev.", clean[5:]) if clean.startswith("prev.") else ("", clean)
        sname, _, rest = rest_ref.partition(".")
        art, _, field = rest.partition(".")
        p = self.resolve_ref_path(f"${pfx}{sname}.{art}")
        if not p or not p.is_file(): return None
        try:
            curr = json.loads(p.read_text(encoding="utf-8")).get("payload", {})
            for f in [x for x in field.split(".") if x]: curr = curr.get(f) if isinstance(curr, dict) else None
            return curr
        except Exception: return None


def exec_step(step_node: PipelineNode, scope: Scope, launcher: Launcher) -> None:
    xid = scope.exec_id(step_node.id)
    try: policy.check_budget(scope.state.counters, scope.project_cfg.get("budget"))
    except policy.BudgetExceededError as e: raise Stop("stopped_budget", str(e))
    if step_node.data.get("when") and not eval_expr(step_node.data["when"], scope):
        scope.state.mark_skipped(xid); return
    block = registry.resolve_block(step_node.data["block"], scope.project_cfg, scope.run_dir.parent.parent)
    inputs: dict[str, Path] = {}
    for in_name, in_ref in step_node.data.get("in", {}).items():
        if (p := scope.resolve_ref_path(in_ref)): inputs[in_name] = p
        elif not block.inputs.get(in_name, {}).get("optional", False): raise StepFailed(f"Step {xid} missing {in_name}")

    cfg = substitute_variables(step_node.data.get("cfg", {}), scope.lookup_dict())
    ih = store.inputs_hash(f"{block.name}@{block.version}", cfg, inputs)
    rec = scope.state.steps.get(xid)
    if rec and rec.status == "committed" and rec.inputs_hash == ih: return
    adopt_ok, adopt_p = store.check_adoptable(scope.run_dir, xid, ih)
    if adopt_ok and adopt_p:
        dest = store.get_step_dir(scope.run_dir, xid) / "out"
        if adopt_p != dest: shutil.copytree(adopt_p, dest, dirs_exist_ok=True)
        scope.state.mark_committed(xid, ih); return

    lvl = policy.effective_level(step_node.data.get("side_effects"), block.side_effects)
    if rec and rec.status == "started" and not policy.rerun_allowed(lvl, block):
        raise Pause("needs_human", xid, f"Interrupted step {xid} requires operator resolution")

    retry_info: dict[str, Any] | None = None
    for attempt in range(1, policy.max_attempts(step_node.data.get("retry")) + 1):
        scope.state.mark_started(xid, attempt, ih); save_state(scope.run_dir, scope.state)
        att = store.new_attempt(scope.run_dir, xid, attempt)
        store.stage_inputs(att.in_dir, inputs, retry_info)
        if (app := scope.run_dir / "gates" / f"{step_node.id}.approval.json").is_file(): shutil.copy2(app, att.in_dir / "_gate_response.json")
        store.write_cfg(att.cfg_file, cfg | {"_ctx": {"run_id": scope.state.run_id, "step": xid, "attempt": attempt}})
        res = launcher.run(list(block.command) + ["--in", str(att.in_dir), "--out", str(att.out_dir), "--cfg", str(att.cfg_file)],
                           cwd=block.dir, stdout_log=att.stdout_log, stderr_log=att.stderr_log)
        v = store.interpret_attempt(res, att, block)
        scope.state.add_metrics(v.get("metrics"))

        if v.get("needs_human"):
            sub_h = store.stage_gate(scope.run_dir, step_node.id, att.out_dir)
            scope.state.pending = {"gate": step_node.id, "subject_hash": sub_h}
            raise Pause("paused_gate", xid, v.get("reason", ""))
        if v["ok"]:
            store.commit_attempt(scope.run_dir, xid, att)
            scope.state.mark_committed(xid, ih); append_event(scope.run_dir, "committed", step=xid, attempt=attempt)
            if scope.state.pending.get("gate") == step_node.id: scope.state.pending = {"gate": None, "subject_hash": None}
            return
        if v.get("retryable") and attempt < policy.max_attempts(step_node.data.get("retry")) and policy.retry_safe(lvl, block):
            delay, retry_info = policy.calculate_backoff(step_node.data.get("retry"), attempt, v.get("code", ""))
            time.sleep(min(delay, 2.0)); continue
        break

    scope.state.mark_failed(xid, v.get("code", "E_FAILED"), v.get("reason", ""))
    if step_node.data.get("on_fail") != "continue": raise StepFailed(f"Step {xid} failed: {v.get('reason')}")


def exec_seq(nodes: list[PipelineNode], scope: Scope, launcher: Launcher) -> None:
    for node in nodes:
        if node.kind == "step":
            exec_step(node, scope, launcher)
        elif node.kind == "loop":
            if node.data.get("when") and not eval_expr(node.data["when"], scope): continue
            max_r = int(substitute_variables(node.data.get("max", 1), scope.lookup_dict()))
            loop_exp = dict(scope.exports) | node.data.get("exports", {}) | {f"{node.id}.{k}": v for k, v in node.data.get("exports", {}).items()}
            last_inner = scope
            for it in range(1, max_r + 1):
                last_inner = scope.child(loop=node.id, iter=it, exports=loop_exp)
                exec_seq(node.steps, last_inner, launcher)
                if node.data.get("until") and eval_expr(node.data["until"], last_inner): break
            for exp_name, exp_ref in node.data.get("exports", {}).items():
                if (p := last_inner.resolve_ref_path(exp_ref)):
                    scope.exports[f"{node.id}.{exp_name}"] = p; scope.exports[exp_name] = p
        elif node.kind == "foreach":
            if node.data.get("when") and not eval_expr(node.data["when"], scope): continue
            items = store.resolve_items(node.data.get("items", []), scope.resolve_ref_path, scope.resolve_ref_value)[: int(node.data.get("limit", 999999))]
            max_p = int(substitute_variables(node.data.get("max_parallel", 1), scope.lookup_dict()) or 1)
            is_safe = bool(scope.project_cfg.get("capabilities", {}).get("parallel_safe", False))
            workers = max_p if (max_p > 1 and is_safe) else 1

            def _run_one(it: Any) -> dict[str, str]:
                try:
                    exec_seq(node.steps, scope.child(item=str(it), as_var=node.data.get("as", "item")), launcher)
                    return {"id": str(it), "status": "ok"}
                except StepFailed:
                    if node.data.get("item_fail") == "stop": raise
                    return {"id": str(it), "status": "failed"}

            if workers > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool: item_statuses = list(pool.map(_run_one, items))
            else:
                item_statuses = [_run_one(it) for it in items]

            idx_dir = scope.run_dir / "steps" / node.id / "out"; idx_dir.mkdir(parents=True, exist_ok=True)
            (idx_dir / "index.json").write_text(json.dumps({"payload": {"items": item_statuses}}))
            scope.exports[f"{node.id}.index"] = idx_dir / "index.json"
