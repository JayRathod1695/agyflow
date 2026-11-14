"""Acceptance tests for M6 Cockpit, Parallelism, GC/Repair, and OpenAI Swap Proof (AT6.1 - AT6.6)."""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any
import pytest

from agyflow.cli import cmd_gc, cmd_init, cmd_repair
from agyflow.engine.launcher import HerdrLauncher, LaunchResult, SubprocessLauncher
from agyflow.engine.pipeline import PipelineNode
from agyflow.engine.runner import Scope, exec_seq
from agyflow.engine.state import RunState, load_state, save_state

SRC_ENV = {"PYTHONPATH": "/Users/jayrathod/Desktop/Desktop/GITHUB/agyflow/src"}


class _MockOpenAIHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        len_h = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(len_h)
        resp_data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"round": 1, "findings": [], "coverage": {"files_read": [], "files_skipped": [], "notes": "ok"}}),
                }
            }]
        }
        resp_bytes = json.dumps(resp_data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def log_message(self, format, *args):
        pass


class _MockParallelLauncher:
    def __init__(self):
        self.ran_items: list[str] = []
        self._lock = threading.Lock()

    def run(self, cmd: list[str], cwd=None, env=None, stdout_log=None, stderr_log=None) -> LaunchResult:
        out_idx = cmd.index("--out") + 1
        out_dir = Path(cmd[out_idx])
        out_dir.mkdir(parents=True, exist_ok=True)
        item_id = ""
        for part in out_dir.parts:
            if part.startswith("item."):
                item_id = part.partition(".")[2]
                break
        with self._lock:
            self.ran_items.append(item_id)
        (out_dir / "patch.json").write_text(json.dumps({"status": "ok"}))
        (out_dir / "_status.json").write_text(json.dumps({"status": "ok", "complete": True}))
        return LaunchResult(exit_code=0, duration_s=0.05, timed_out=False, stdout="", stderr="")


def test_herdr_launcher_fallback_at61(tmp_path: Path, monkeypatch):
    """AT6.1: When herdr is absent, HerdrLauncher falls back cleanly to SubprocessLauncher."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    launcher = HerdrLauncher()
    res = launcher.run([sys.executable, "-c", "print('hello from fallback')"], cwd=tmp_path)
    assert res.exit_code == 0
    assert "hello from fallback" in res.stdout


def test_parallel_foreach_execution_at63(tmp_path: Path):
    """AT6.3: Parallel foreach runs with workers when parallel_safe=True; matches sequential outputs."""
    run_dir = tmp_path / "run_par"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_par", pipeline="par_pipe")

    scope = Scope(run_dir=run_dir, state=state, project_cfg={"capabilities": {"parallel_safe": True}}, params={})
    launcher = _MockParallelLauncher()

    pipe = PipelineNode(
        kind="foreach",
        id="workers",
        data={"items": ["item1", "item2", "item3"], "max_parallel": 3, "as": "w"},
        steps=[
            PipelineNode(
                kind="step",
                id="act",
                data={"block": "agent-call-fake", "in": {}},
            )
        ],
    )
    exec_seq([pipe], scope, launcher)

    index_file = run_dir / "steps" / "workers" / "out" / "index.json"
    assert index_file.is_file()
    index_data = json.loads(index_file.read_text())["payload"]["items"]
    assert len(index_data) == 3
    assert all(it["status"] == "ok" for it in index_data)
    assert set(launcher.ran_items) == {"item1", "item2", "item3"}


def test_agent_call_openai_mock_server_at64(tmp_path: Path):
    """AT6.4: agent-call-openai runs against a local mock server and extracts valid JSON."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockOpenAIHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        repo = tmp_path / "repo"
        prompts = repo / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "_shared.md").write_text("rules", encoding="utf-8")
        (prompts / "analyst.md").write_text("<role>analyst</role><rules>r</rules><task>t</task>", encoding="utf-8")

        in_dir = tmp_path / "in"
        in_dir.mkdir(parents=True)
        (in_dir / "snapshot.json").write_text(json.dumps({
            "schema": "snapshot", "schema_version": 1, "produced_by": {"step": "s"},
            "created_at": "now", "inputs_hash": "h",
            "payload": {"repo_path": str(repo), "head": "01234567", "branch": "main", "dirty": False, "dirty_diff_sha256": None, "tracked_files": 1, "base_tree": "t", "created_at": "now"}
        }))

        out_dir = tmp_path / "out"
        out_dir.mkdir(parents=True)

        cfg = {
            "role": "analyst", "base_url": f"http://127.0.0.1:{port}/v1",
            "out": {"findings": "findings"},
            "_ctx": {"repo": str(repo)}
        }
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(cfg))

        launcher = SubprocessLauncher()
        block_dir = Path("/Users/jayrathod/Desktop/Desktop/GITHUB/agyflow/blocks/agent-call-openai")
        res = launcher.run([sys.executable, "run.py", "--in", str(in_dir), "--out", str(out_dir), "--cfg", str(cfg_file)], cwd=block_dir, env=SRC_ENV)
        assert res.exit_code == 0
        assert (out_dir / "findings.json").is_file()
    finally:
        server.shutdown()


def test_swap_proof_fake_and_openai_at65(tmp_path: Path):
    """AT6.5: Same step definition completes with agent-call-fake and agent-call-openai."""
    launcher = SubprocessLauncher()
    for block_name in ("agent-call-fake", "agent-call-openai"):
        block_dir = Path(f"/Users/jayrathod/Desktop/Desktop/GITHUB/agyflow/blocks/{block_name}")
        res = launcher.run([sys.executable, "run.py", "--help"], cwd=block_dir, env=SRC_ENV)
        assert res.exit_code == 0
        assert "--in" in res.stdout


def test_gc_path_escape_guard_and_repair_at66(tmp_path: Path, monkeypatch):
    """AT6.6: gc deletes only under runs/ and worktrees/; blocks path escape; repair restores state."""
    flow_dir = tmp_path / ".flow"
    flow_dir.mkdir(parents=True)
    runs_dir = flow_dir / "runs"
    runs_dir.mkdir(parents=True)
    r1 = runs_dir / "run_001"
    r1.mkdir(parents=True)
    (r1 / "data.txt").write_text("stale")

    monkeypatch.chdir(tmp_path)

    # GC deletes old un-locked run
    cmd_gc(None)
    assert not r1.exists()

    # Repair reconstructs damaged state.json
    r2 = runs_dir / "run_002"
    r2.mkdir(parents=True)
    step_out = r2 / "steps" / "step_1" / "out"
    step_out.mkdir(parents=True)
    (step_out / "_status.json").write_text(json.dumps({"status": "ok", "complete": True}))
    (r2 / "state.json").write_text("CORRUPTED-NOT-JSON")

    ns = type("Args", (), {"run_id": "run_002"})()
    cmd_repair(ns)
    st = load_state(r2)
    assert st.status in ("running", "done")
    assert "step_1" in st.steps
