"""Acceptance tests for engine foreach execution, collection staging, and item_fail isolation (AT4.1, AT4.7)."""

import json
from pathlib import Path
import pytest
from agyflow.engine.launcher import LaunchResult
from agyflow.engine.pipeline import PipelineNode
from agyflow.engine.runner import Scope, StepFailed, exec_seq
from agyflow.engine.state import RunState


class MockForeachLauncher:
    """Simulates block execution inside foreach loops with per-item outcomes."""

    def __init__(self, failing_items: set[str] | None = None):
        self.failing_items = failing_items or set()
        self.executed_items: list[str] = []

    def run(self, cmd: list[str], cwd=None, env=None, stdout_log=None, stderr_log=None) -> LaunchResult:
        out_idx = cmd.index("--out") + 1
        out_dir = Path(cmd[out_idx])
        out_dir.mkdir(parents=True, exist_ok=True)

        # Detect item id from step execution path
        item_id = ""
        for part in out_dir.parts:
            if part.startswith("item."):
                item_id = part.partition(".")[2]
                break

        if item_id:
            self.executed_items.append(item_id)

        if item_id in self.failing_items:
            (out_dir / "_status.json").write_text(json.dumps({
                "status": "failed",
                "code": "E_FAILED",
                "reason": f"Simulated failure for {item_id}",
                "complete": False,
            }))
            return LaunchResult(exit_code=1, duration_s=0.1, timed_out=False, stdout="", stderr="Failed")

        (out_dir / "patch.json").write_text(json.dumps({
            "schema_version": "1.0.0",
            "item": item_id,
            "status": "ok",
        }))
        (out_dir / "_status.json").write_text(json.dumps({"status": "ok", "complete": True}))
        return LaunchResult(exit_code=0, duration_s=0.1, timed_out=False, stdout="", stderr="")


def test_foreach_sequential_execution_and_limit(tmp_path: Path):
    """Verify foreach iterates sequentially and enforces limit parameter."""
    run_dir = tmp_path / "run_foreach"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_foreach", pipeline="test_pipeline")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})

    # Pre-seed items in collection artifact
    collection_dir = run_dir / "steps" / "cands" / "out"
    collection_dir.mkdir(parents=True)
    collection_payload = {"items": ["cand_A", "cand_B", "cand_C", "cand_D"]}
    (collection_dir / "candidates.json").write_text(json.dumps({"payload": collection_payload}))

    launcher = MockForeachLauncher()
    foreach_node = PipelineNode(
        kind="foreach",
        id="cands_loop",
        data={"items": "$cands.candidates.items", "limit": 2},
        steps=[PipelineNode(kind="step", id="act", data={"block": "agent-call-fake"})],
    )

    exec_seq([foreach_node], scope, launcher)

    # limit: 2 bounds execution to first two items
    assert launcher.executed_items == ["cand_A", "cand_B"]


def test_foreach_item_fail_continue_isolation_at47(tmp_path: Path):
    """AT4.7: item_fail: continue isolates single item failure, allowing subsequent items to proceed."""
    run_dir = tmp_path / "run_continue"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_continue", pipeline="test_pipeline")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})

    collection_dir = run_dir / "steps" / "cands" / "out"
    collection_dir.mkdir(parents=True)
    (collection_dir / "candidates.json").write_text(json.dumps({"payload": {"items": ["cand_1", "cand_2", "cand_3"]}}))

    # cand_1 fails, cand_2 and cand_3 succeed
    launcher = MockForeachLauncher(failing_items={"cand_1"})
    foreach_node = PipelineNode(
        kind="foreach",
        id="cands_loop",
        data={"items": "$cands.candidates.items", "item_fail": "continue"},
        steps=[PipelineNode(kind="step", id="act", data={"block": "agent-call-fake"})],
    )

    exec_seq([foreach_node], scope, launcher)

    # All 3 attempted despite cand_1 failure
    assert launcher.executed_items == ["cand_1", "cand_2", "cand_3"]
    assert state.steps["item.cand_1/act"].status == "failed"
    assert state.steps["item.cand_2/act"].status == "committed"
    assert state.steps["item.cand_3/act"].status == "committed"


def test_foreach_item_fail_stop_at41(tmp_path: Path):
    """AT4.1: item_fail: stop raises StepFailed and halts pipeline immediately on first failure."""
    run_dir = tmp_path / "run_stop"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_stop", pipeline="test_pipeline")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})

    collection_dir = run_dir / "steps" / "cands" / "out"
    collection_dir.mkdir(parents=True)
    (collection_dir / "candidates.json").write_text(json.dumps({"payload": {"items": ["cand_1", "cand_2", "cand_3"]}}))

    launcher = MockForeachLauncher(failing_items={"cand_1"})
    foreach_node = PipelineNode(
        kind="foreach",
        id="cands_loop",
        data={"items": "$cands.candidates.items", "item_fail": "stop"},
        steps=[PipelineNode(kind="step", id="act", data={"block": "agent-call-fake"})],
    )

    with pytest.raises(StepFailed):
        exec_seq([foreach_node], scope, launcher)

    # Halts after cand_1 without running cand_2 or cand_3
    assert launcher.executed_items == ["cand_1"]
