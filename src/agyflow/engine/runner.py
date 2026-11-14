"""DAG execution runner, scope management, step interpret, and gate suspension."""

from __future__ import annotations

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
from agyflow.lib.schema import validate_or_raise


class Pause(Exception):
    def __init__(self, kind: str, xid: str = "", reason: str = "") -> None:
        super().__init__(f"Pause({kind}, {xid}, {reason})"); self.kind, self.xid, self.reason = kind, xid, reason

class Stop(Exception):
    def __init__(self, kind: str, reason: str = "") -> None:
        super().__init__(f"Stop({kind}, {reason})"); self.kind, self.reason = kind, reason

class StepFailed(Exception): pass


@dataclass
class Scope:
    """Execution scope holding run directory, active state, and reference resolutions."""

    run_dir: Path
    state: RunState
    project_cfg: dict[str, Any]
    params: dict[str, Any]
    loop_id: str = ""
    loop_iter: int = 0
    item_id: str = ""
    in_foreach: bool = False
    exports: dict[str, Any] = field(default_factory=dict)

    def child(self, loop: str = "", iter: int = 0, item: str = "") -> Scope:
        return Scope(self.run_dir, self.state, self.project_cfg, self.params,
                     loop or self.loop_id, iter or self.loop_iter, item or self.item_id,
                     bool(item) or self.in_foreach, self.exports)

    def exec_id(self, step_id: str) -> str:
        pfx = f"{self.loop_id}.{self.loop_iter}/" if self.loop_id else ""
        return f"{pfx}item.{self.item_id}/{step_id}" if self.item_id else f"{pfx}{step_id}"

    def lookup_dict(self) -> dict[str, Any]:
        d = {k: v for sec in ("project", "limits", "budget") for k, v in self.project_cfg.get(sec, {}).items()}
        for k, v in self.params.items(): d[f"params.{k}"] = d[k] = v
        d["loop.iter"], d["item.id"] = self.loop_iter, self.item_id
        return d

    def resolve_ref_path(self, ref: str) -> Path | None:
        for opt in [r.strip().lstrip("$") for r in ref.split("||")]:
            if opt in self.exports:
                exp = self.exports[opt]
                if isinstance(exp, Path) and exp.is_file(): return exp
                if isinstance(exp, str) and (res := self.resolve_ref_path(exp)): return res
            if opt.startswith("project."):
                cand = self.run_dir.parent.parent / (f"{opt[8:]}.json" if opt[8:] == "CONTEXT" else f"{opt[8:]}.md")
                if cand.is_file(): return cand
            if opt.startswith("prev."):
                if self.loop_iter <= 1: continue
                sname, _, art = opt[5:].partition(".")
                cand = self.run_dir / "steps" / f"{self.loop_id}.{self.loop_iter - 1}" / sname / "out" / (art if art.endswith(".json") else f"{art}.json")
                if cand.is_file(): return cand
                continue
            sname, _, art = opt.partition(".")
            fname = art if art.endswith(".json") else f"{art}.json"
            if self.loop_id and (c := self.run_dir / "steps" / f"{self.loop_id}.{self.loop_iter}" / sname / "out" / fname).is_file(): return c
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


def interpret_attempt(res: Any, att: store.AttemptDirs, manifest: registry.BlockManifest) -> dict[str, Any]:
    if res.timed_out: return {"ok": False, "retryable": True, "code": "E_TIMEOUT", "reason": "Timed out"}
    sf = att.out_dir / "_status.json"
    if not sf.is_file(): return {"ok": False, "retryable": False, "code": "E_CONTRACT", "reason": "Missing _status.json"}
    try: sdata = json.loads(sf.read_text(encoding="utf-8"))
    except Exception: return {"ok": False, "retryable": False, "code": "E_CONTRACT", "reason": "Malformed _status.json"}
    if res.exit_code == 30 or sdata.get("status") == "needs_human":
        return {"ok": False, "needs_human": True, "code": sdata.get("code", "E_NEEDS_HUMAN"), "reason": sdata.get("reason", "")}
    if res.exit_code != 0 or not sdata.get("complete"):
        code = sdata.get("code") or ("E_CONTRACT" if res.exit_code not in (0, 10, 20, 30) else "E_FAILED")
        return {"ok": False, "retryable": policy.is_retriable_code(code), "code": code, "reason": sdata.get("reason", "")}
    for out_name, out_spec in manifest.outputs.items():
        if not out_spec.get("optional", False):
            of = att.out_dir / f"{out_name}.json"
            if not of.is_file(): return {"ok": False, "retryable": True, "code": "E_SCHEMA", "reason": f"Missing {out_name}"}
            try: validate_or_raise(json.loads(of.read_text(encoding="utf-8")).get("payload", {}), out_spec.get("schema", out_name))
            except Exception as e: return {"ok": False, "retryable": True, "code": "E_SCHEMA", "reason": str(e)}
    return {"ok": True, "metrics": sdata.get("metrics", {})}


def exec_step(step_node: PipelineNode, scope: Scope, launcher: Launcher) -> None:
    xid = scope.exec_id(step_node.id)
    policy.check_budget(scope.state.counters, scope.project_cfg.get("budget"))
    if step_node.data.get("when") and not eval_expr(step_node.data["when"], scope):
        scope.state.mark_skipped(xid); return
    block = registry.resolve_block(step_node.data["block"], scope.project_cfg, scope.run_dir.parent.parent)
    inputs: dict[str, Path] = {}
    for in_name, in_ref in step_node.data.get("in", {}).items():
        p = scope.resolve_ref_path(in_ref)
        if p: inputs[in_name] = p
        elif not block.inputs.get(in_name, {}).get("optional", False):
            raise StepFailed(f"Step {xid} missing input '{in_name}' ({in_ref})")

    cfg = substitute_variables(step_node.data.get("cfg", {}), scope.lookup_dict())
    ih = store.inputs_hash(f"{block.name}@{block.version}", cfg, inputs)
    rec = scope.state.steps.get(xid)
    if rec and rec.status == "committed" and rec.inputs_hash == ih: return
    adopt_ok, adopt_path = store.check_adoptable(scope.run_dir, xid, ih)
    if adopt_ok and adopt_path:
        dest = store.get_step_dir(scope.run_dir, xid) / "out"
        if adopt_path != dest: shutil.copytree(adopt_path, dest, dirs_exist_ok=True)
        scope.state.mark_committed(xid, ih); return

    lvl = policy.effective_level(step_node.data.get("side_effects"), block.side_effects)
    if rec and rec.status == "started" and not policy.rerun_allowed(lvl, block): raise Pause("needs_human", xid, "Interrupted step")

    retry_info: dict[str, Any] | None = None
    for attempt in range(1, policy.max_attempts(step_node.data.get("retry")) + 1):
        scope.state.mark_started(xid, attempt, ih); save_state(scope.run_dir, scope.state)
        att = store.new_attempt(scope.run_dir, xid, attempt)
        store.stage_inputs(att.in_dir, inputs, retry_info)
        app_file = scope.run_dir / "gates" / f"{step_node.id}.approval.json"
        if app_file.is_file(): shutil.copy2(app_file, att.in_dir / "_gate_response.json")
        store.write_cfg(att.cfg_file, cfg | {"_ctx": {"run_id": scope.state.run_id, "step": xid, "attempt": attempt}})
        res = launcher.run(list(block.command) + ["--in", str(att.in_dir), "--out", str(att.out_dir), "--cfg", str(att.cfg_file)],
                           cwd=block.dir, stdout_log=att.stdout_log, stderr_log=att.stderr_log)
        v = interpret_attempt(res, att, block)
        scope.state.add_metrics(v.get("metrics"))

        if v.get("needs_human"):
            gate_dir = scope.run_dir / "gates"; gate_dir.mkdir(parents=True, exist_ok=True)
            sub_hash = ""
            for gf in ("request.json", "review.md"):
                if (sf := att.out_dir / gf).is_file():
                    shutil.copy2(sf, gate_dir / f"{step_node.id}.{gf}")
                    if gf == "request.json":
                        try: sub_hash = json.loads(sf.read_text(encoding="utf-8")).get("payload", {}).get("subject_hash", "")
                        except Exception: pass
            scope.state.pending = {"gate": step_node.id, "subject_hash": sub_hash}
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
            last_inner = scope
            for it in range(1, max_r + 1):
                last_inner = scope.child(loop=node.id, iter=it)
                exec_seq(node.steps, last_inner, launcher)
                if node.data.get("until") and eval_expr(node.data["until"], last_inner): break
            for exp_name, exp_ref in node.data.get("exports", {}).items():
                p = last_inner.resolve_ref_path(exp_ref)
                if p: scope.exports[f"{node.id}.{exp_name}"] = p
        elif node.kind == "foreach":
            items = (scope.resolve_ref_value(node.data.get("items", "").lstrip("$")) or [])[: int(node.data.get("limit", 999999))]
            for item in items:
                try: exec_seq(node.steps, scope.child(item=str(item)), launcher)
                except StepFailed:
                    if node.data.get("item_fail") == "stop": raise
