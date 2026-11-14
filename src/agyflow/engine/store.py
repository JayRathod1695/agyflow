"""Run directory layout, input staging with read-only chmod, content hashing, and atomic commits."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any, Callable

from agyflow.lib.contract import atomic_write_json
from agyflow.lib.schema import canonical_json, hash_dir, hash_file


@dataclass(frozen=True)
class AttemptDirs:
    attempt_dir: Path
    in_dir: Path
    out_dir: Path
    cfg_file: Path
    stdout_log: Path
    stderr_log: Path


def get_step_dir(run_dir: Path | str, xid: str) -> Path:
    return Path(run_dir) / "steps" / xid


def new_attempt(run_dir: Path | str, xid: str, attempt: int) -> AttemptDirs:
    step_dir = get_step_dir(run_dir, xid)
    att_dir = step_dir / "attempts" / str(attempt)
    in_dir, out_dir = att_dir / "in", att_dir / "out"
    in_dir.mkdir(parents=True, exist_ok=True); out_dir.mkdir(parents=True, exist_ok=True)
    return AttemptDirs(att_dir, in_dir, out_dir, att_dir / "cfg.json", att_dir / "stdout.log", att_dir / "stderr.log")


def compute_path_hash(path: Path | str) -> str:
    p = Path(path)
    return "" if not p.exists() else (hash_dir(p) if p.is_dir() else hash_file(p))


def inputs_hash(block: str, cfg: dict[str, Any], inputs: dict[str, Path], prompt: str = "", profile: str = "") -> str:
    clean_cfg = {k: v for k, v in cfg.items() if k != "_ctx"}
    payload = {"block": block, "cfg": clean_cfg, "inputs": {n: compute_path_hash(p) for n, p in sorted(inputs.items())}, "profile": profile, "prompt": prompt}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def stage_inputs(in_dir: Path, inputs: dict[str, Path], retry_info: dict[str, Any] | None = None) -> None:
    for name, src in inputs.items():
        dest = in_dir / name if src.is_dir() else in_dir / f"{name}.json"
        if src.is_dir(): shutil.copytree(src, dest, dirs_exist_ok=True)
        else: shutil.copy2(src, dest)
        os.chmod(dest, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    if retry_info:
        atomic_write_json(in_dir / "_retry.json", retry_info)
        os.chmod(in_dir / "_retry.json", stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def write_cfg(cfg_file: Path, cfg: dict[str, Any]) -> None:
    atomic_write_json(cfg_file, cfg)


def commit_attempt(run_dir: Path | str, xid: str, att: AttemptDirs) -> Path:
    step_dir = get_step_dir(run_dir, xid)
    dest_out = step_dir / "out"
    if dest_out.exists(): shutil.rmtree(dest_out)
    shutil.copytree(att.out_dir, dest_out)
    return dest_out


def check_adoptable(run_dir: Path | str, xid: str, expected_ih: str) -> tuple[bool, Path | None]:
    step_dir = get_step_dir(run_dir, xid)
    att_parent = step_dir / "attempts"
    if not att_parent.is_dir(): return False, None
    for ad in sorted(att_parent.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0, reverse=True):
        if not ad.is_dir(): continue
        cfg_f, st_f = ad / "cfg.json", ad / "out" / "_status.json"
        if cfg_f.is_file() and st_f.is_file():
            try:
                st = json.loads(st_f.read_text(encoding="utf-8"))
                if st.get("complete") is True and st.get("status") == "ok":
                    return True, ad / "out"
            except Exception: pass
    return False, None


def build_collection(run_dir: Path, ref_opt: str) -> Path | None:
    _, _, rest = ref_opt.partition("[*].")
    sname, _, art = rest.partition(".")
    fname = art if art.endswith(".json") else f"{art}.json"
    coll_dir = run_dir / "collections" / f"{sname}_{art}"
    coll_dir.mkdir(parents=True, exist_ok=True); count = 0
    if (steps_dir := run_dir / "steps").is_dir():
        for sdir in steps_dir.iterdir():
            if (src := sdir / sname / "out" / fname).is_file():
                item_dest = coll_dir / sdir.name.removeprefix("item.")
                item_dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, item_dest / fname); count += 1
    return coll_dir if count > 0 else None


def resolve_items(raw: Any, ref_path_fn: Callable[[str], Path | None], ref_val_fn: Callable[[str], Any]) -> list[str]:
    if isinstance(raw, list): return [str(x.get("id", x) if isinstance(x, dict) else x) for x in raw]
    if isinstance(raw, str) and "#" in raw:
        r, _, pth = raw.strip().lstrip("$").partition("#")
        p = ref_path_fn(f"${r}")
        if p and p.is_file():
            try:
                curr = json.loads(p.read_text(encoding="utf-8"))
                for part in pth.split("."):
                    if part.endswith("[*]"):
                        lst = curr.get(part[:-3], []) if isinstance(curr, dict) else curr
                        return [str(x.get("id", x) if isinstance(x, dict) else x) for x in (lst or [])]
                    curr = curr.get(part) if isinstance(curr, dict) else None
                if isinstance(curr, list): return [str(x.get("id", x) if isinstance(x, dict) else x) for x in curr]
            except Exception: pass
    val = ref_val_fn(raw.strip().lstrip("$")) if isinstance(raw, str) else None
    return [str(x.get("id", x) if isinstance(x, dict) else x) for x in val] if isinstance(val, list) else []


def interpret_attempt(res: Any, att: AttemptDirs, manifest: Any) -> dict[str, Any]:
    from agyflow.engine.policy import is_retriable_code
    if res.timed_out: return {"ok": False, "retryable": True, "code": "E_TIMEOUT", "reason": "Timed out"}
    if not (sf := att.out_dir / "_status.json").is_file(): return {"ok": False, "retryable": False, "code": "E_CONTRACT", "reason": "Missing _status.json"}
    try: sdata = json.loads(sf.read_text(encoding="utf-8"))
    except Exception: return {"ok": False, "retryable": False, "code": "E_CONTRACT", "reason": "Malformed _status.json"}
    if res.exit_code == 30 or sdata.get("status") == "needs_human":
        return {"ok": False, "needs_human": True, "code": sdata.get("code", "E_NEEDS_HUMAN"), "reason": sdata.get("reason", "")}
    if res.exit_code != 0 or not sdata.get("complete"):
        c = sdata.get("code") or ("E_CONTRACT" if res.exit_code not in (0, 10, 20, 30) else "E_FAILED")
        return {"ok": False, "retryable": is_retriable_code(c), "code": c, "reason": sdata.get("reason", "")}
    for k, spec in manifest.outputs.items():
        if not spec.get("optional", False) and not (of := att.out_dir / f"{k}.json").is_file(): return {"ok": False, "retryable": True, "code": "E_SCHEMA", "reason": f"Missing {k}"}
    return {"ok": True, "metrics": sdata.get("metrics", {})}


def stage_gate(run_dir: Path, step_id: str, att_out: Path) -> str:
    gate_dir = Path(run_dir) / "gates"; gate_dir.mkdir(parents=True, exist_ok=True); sub_h = ""
    for gf in ("request.json", "review.md"):
        if (sf := att_out / gf).is_file():
            shutil.copy2(sf, gate_dir / f"{step_id}.{gf}")
            if gf == "request.json":
                try: sub_h = json.loads(sf.read_text(encoding="utf-8")).get("payload", {}).get("subject_hash", "")
                except Exception: pass
    return sub_h
