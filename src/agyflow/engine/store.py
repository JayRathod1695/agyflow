"""Run directory layout, input staging with read-only chmod, content hashing, and atomic commits."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any

from agyflow.lib.contract import atomic_write_json
from agyflow.lib.schema import canonical_json, hash_dir, hash_file


@dataclass(frozen=True)
class AttemptDirs:
    """Paths for a single execution attempt of a step."""

    attempt_dir: Path
    in_dir: Path
    out_dir: Path
    cfg_file: Path
    stdout_log: Path
    stderr_log: Path


def get_step_dir(run_dir: Path | str, xid: str) -> Path:
    """Get steps/<xid> directory path."""
    return Path(run_dir) / "steps" / xid


def new_attempt(run_dir: Path | str, xid: str, attempt: int) -> AttemptDirs:
    """Create directory structure for attempt n of step xid."""
    step_dir = get_step_dir(run_dir, xid)
    att_dir = step_dir / "attempts" / str(attempt)
    in_dir = att_dir / "in"
    out_dir = att_dir / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    return AttemptDirs(
        attempt_dir=att_dir,
        in_dir=in_dir,
        out_dir=out_dir,
        cfg_file=att_dir / "cfg.json",
        stdout_log=att_dir / "stdout.log",
        stderr_log=att_dir / "stderr.log",
    )


def compute_path_hash(path: Path | str) -> str:
    """Compute sha256 hex digest for a file or directory."""
    p = Path(path)
    if not p.exists():
        return ""
    return hash_dir(p) if p.is_dir() else hash_file(p)


def inputs_hash(block: str, cfg: dict[str, Any], inputs: dict[str, Path], prompt: str = "", profile: str = "") -> str:
    """Calculate inputs_hash = sha256(canonical_json({...}))."""
    clean_cfg = {k: v for k, v in cfg.items() if k != "_ctx"}
    input_hashes = {name: compute_path_hash(path) for name, path in sorted(inputs.items())}
    payload = {
        "block": block,
        "cfg": clean_cfg,
        "inputs": input_hashes,
        "profile": profile,
        "prompt": prompt,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _make_readonly(p: Path) -> None:
    """Recursively set read-only permissions (0o444 for files, 0o555 for dirs)."""
    if p.is_dir():
        for item in p.rglob("*"):
            if item.is_dir():
                item.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
            else:
                item.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        p.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    elif p.is_file():
        p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def stage_inputs(in_dir: Path, inputs: dict[str, Path], retry_info: dict[str, Any] | None = None) -> None:
    """Stage input artifacts into in_dir with read-only permissions, and optional _retry.json."""
    for name, src_path in inputs.items():
        if not src_path.exists():
            continue
        dest = in_dir / name if src_path.is_dir() else in_dir / (name if name.endswith(".json") else f"{name}.json")
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if src_path.is_dir():
            shutil.copytree(src_path, dest)
        else:
            shutil.copy2(src_path, dest)
        _make_readonly(dest)

    if retry_info:
        atomic_write_json(in_dir / "_retry.json", retry_info)


def write_cfg(cfg_file: Path, cfg: dict[str, Any]) -> None:
    """Write cfg dictionary to cfg_file atomically."""
    atomic_write_json(cfg_file, cfg)


def commit_attempt(run_dir: Path | str, xid: str, att: AttemptDirs) -> Path:
    """Atomically commit att.out_dir to steps/<xid>/out via os.rename."""
    step_dir = get_step_dir(run_dir, xid)
    final_out = step_dir / "out"
    if final_out.exists():
        if final_out.is_dir():
            shutil.rmtree(final_out)
        else:
            final_out.unlink()
    os.rename(att.out_dir, final_out)
    return final_out


def check_adoptable(run_dir: Path | str, xid: str, expected_ih: str) -> tuple[bool, Path | None]:
    """Check if step output is adoptable from existing committed out or finished attempt."""
    step_dir = get_step_dir(run_dir, xid)
    final_out = step_dir / "out"
    if final_out.is_dir():
        status_file = final_out / "_status.json"
        if status_file.is_file():
            try:
                st = json.loads(status_file.read_text(encoding="utf-8"))
                if st.get("complete") is True:
                    return True, final_out
            except Exception:
                pass

    attempts_dir = step_dir / "attempts"
    if attempts_dir.is_dir():
        for att_dir in sorted(attempts_dir.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0, reverse=True):
            status_file = att_dir / "out" / "_status.json"
            if status_file.is_file():
                try:
                    st = json.loads(status_file.read_text(encoding="utf-8"))
                    if st.get("complete") is True:
                        return True, att_dir / "out"
                except Exception:
                    pass
    return False, None
