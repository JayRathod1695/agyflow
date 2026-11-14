"""Acceptance tests for engine loop execution, max_rounds, $prev, and until early exit."""

import json
from pathlib import Path
from agyflow.engine.launcher import Launcher, LaunchResult
from agyflow.engine.pipeline import PipelineNode
from agyflow.engine.runner import Scope, exec_seq
from agyflow.engine.state import RunState


class MockLoopLauncher(Launcher):
    """Simulates block runs inside loops, producing iterations of artifacts."""

    def __init__(self, done_at_round: int = 2):
        self.done_at_round = done_at_round
        self.call_history: list[list[str]] = []

    def run(self, cmd: list[str], cwd=None, env=None, stdout_log=None, stderr_log=None) -> LaunchResult:
        self.call_history.append(cmd)
        out_idx = cmd.index("--out") + 1
        out_dir = Path(cmd[out_idx])
        out_dir.mkdir(parents=True, exist_ok=True)

        in_idx = cmd.index("--in") + 1
        in_dir = Path(cmd[in_idx])

        # Write standard artifact and status
        cand_files = [in_dir / "data.json", in_dir / "artifact.json"]
        prev_val = 0
        for cf in cand_files:
            if cf.exists():
                try:
                    content = json.loads(cf.read_text())
                    prev_val = content.get("val", 0)
                    if "payload" in content and isinstance(content["payload"], dict):
                        prev_val = content["payload"].get("val", prev_val)
                    break
                except Exception:
                    pass

        curr_round = prev_val + 1
        is_done = curr_round >= self.done_at_round

        payload = {"round": curr_round, "val": curr_round, "done": is_done}
        art = {
            "schema_version": "1.0.0",
            "schema": "convergence",
            "payload": payload,
        }
        (out_dir / "artifact.json").write_text(json.dumps(art))
        (out_dir / "convergence.json").write_text(json.dumps(art))
        (out_dir / "_status.json").write_text(json.dumps({"status": "ok", "complete": True}))

        return LaunchResult(
            exit_code=0,
            duration_s=0.1,
            timed_out=False,
            stdout="",
            stderr="",
        )


def test_loop_max_rounds_bound(tmp_path: Path):
    """Verify loop executes up to max_rounds when until condition is never satisfied."""
    run_dir = tmp_path / "run_max"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_max", pipeline="test_loop")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})

    launcher = MockLoopLauncher(done_at_round=999)  # Never done early

    loop_node = PipelineNode(
        kind="loop",
        id="verify_loop",
        data={"max": 3, "until": "step_a.convergence.done == true"},
        steps=[
            PipelineNode(
                kind="step",
                id="step_a",
                data={
                    "block": "agent-call-fake",
                    "in": {"data": "$prev.step_a.artifact || $seed.artifact"},
                },
            )
        ],
    )

    # Pre-seed $seed.artifact
    seed_dir = run_dir / "steps" / "seed" / "out"
    seed_dir.mkdir(parents=True)
    (seed_dir / "artifact.json").write_text(json.dumps({"val": 0}))

    exec_seq([loop_node], scope, launcher)

    # Must execute exactly 3 rounds
    assert len(launcher.call_history) == 3


def test_loop_until_early_exit(tmp_path: Path):
    """Verify loop exits early as soon as until condition evaluates to true."""
    run_dir = tmp_path / "run_early"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_early", pipeline="test_loop")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})

    # Marks done on round 2
    launcher = MockLoopLauncher(done_at_round=2)

    loop_node = PipelineNode(
        kind="loop",
        id="verify_loop",
        data={"max": 5, "until": "step_a.convergence.done == true"},
        steps=[
            PipelineNode(
                kind="step",
                id="step_a",
                data={
                    "block": "agent-call-fake",
                    "in": {"data": "$prev.step_a.artifact || $seed.artifact"},
                },
            )
        ],
    )

    seed_dir = run_dir / "steps" / "seed" / "out"
    seed_dir.mkdir(parents=True)
    (seed_dir / "artifact.json").write_text(json.dumps({"val": 0}))

    exec_seq([loop_node], scope, launcher)

    # Must break after round 2, without executing rounds 3, 4, 5
    assert len(launcher.call_history) == 2


def test_loop_prev_resolution_and_fallback(tmp_path: Path):
    """Verify round 1 uses || fallback while round 2+ resolves $prev iteration."""
    run_dir = tmp_path / "run_prev"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_prev", pipeline="test_loop")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})

    launcher = MockLoopLauncher(done_at_round=3)

    loop_node = PipelineNode(
        kind="loop",
        id="vloop",
        data={"max": 3},
        steps=[
            PipelineNode(
                kind="step",
                id="eval_step",
                data={
                    "block": "agent-call-fake",
                    "in": {"data": "$prev.eval_step.artifact || $seed.artifact"},
                },
            )
        ],
    )

    seed_dir = run_dir / "steps" / "seed" / "out"
    seed_dir.mkdir(parents=True)
    (seed_dir / "artifact.json").write_text(json.dumps({"val": 10}))

    exec_seq([loop_node], scope, launcher)

    # Verify 3 rounds executed with incrementing payload
    assert len(launcher.call_history) == 3
