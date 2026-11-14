"""Tests for nested loop execution, $prev export resolution, budget enforcement, and capability flags."""

import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from agyflow.engine.launcher import Launcher, LaunchResult, SubprocessLauncher
from agyflow.engine.pipeline import PipelineNode
from agyflow.engine.runner import Scope, Stop, exec_seq
from agyflow.engine.state import RunState
from agyflow.cli import _run_engine
from agyflow.lib import agentlib


class NestedMockLauncher(Launcher):
    def __init__(self):
        self.call_history: list[str] = []

    def run(self, cmd: list[str], cwd=None, env=None, stdout_log=None, stderr_log=None) -> LaunchResult:
        out_dir = Path(cmd[cmd.index("--out") + 1])
        in_dir = Path(cmd[cmd.index("--in") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)

        self.call_history.append(str(out_dir))

        # Echo inputs or create output
        if "act_sum" in out_dir.parts:
            (out_dir / "attempt_summary.json").write_text(json.dumps({"payload": {"attempt": 1, "action": "restart"}}))
        elif "plan" in out_dir.parts:
            in_summary = ""
            if (in_dir / "prev_summary.json").is_file():
                in_summary = json.loads((in_dir / "prev_summary.json").read_text()).get("payload", {}).get("action", "")
            (out_dir / "plan.json").write_text(json.dumps({"payload": {"planned_action": in_summary}}))
        else:
            (out_dir / "data.json").write_text(json.dumps({"payload": {"status": "ok"}}))

        (out_dir / "_status.json").write_text(json.dumps({"status": "ok", "complete": True, "metrics": {"agent_calls": 1, "active_seconds": 10}}))
        return LaunchResult(exit_code=0, duration_s=0.1, timed_out=False, stdout="", stderr="")


def test_nested_loops_path_and_scoping(tmp_path: Path):
    """Verify nested loops correctly track hierarchical paths (attempts.1/rounds.1)."""
    run_dir = tmp_path / "run_nested"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_nested", pipeline="nested_pipe")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})
    launcher = NestedMockLauncher()

    pipe = PipelineNode(
        kind="loop",
        id="attempts",
        data={"max": 2},
        steps=[
            PipelineNode(kind="step", id="plan", data={"block": "agent-call-fake", "in": {}}),
            PipelineNode(
                kind="loop",
                id="rounds",
                data={"max": 2},
                steps=[
                    PipelineNode(kind="step", id="act", data={"block": "agent-call-fake", "in": {}})
                ]
            )
        ]
    )

    exec_seq([pipe], scope, launcher)

    # Verify call history has paths from both attempts and rounds
    history_str = " ".join(launcher.call_history)
    assert "attempts.1/plan" in history_str
    assert "attempts.1/rounds.1/act" in history_str
    assert "attempts.1/rounds.2/act" in history_str
    assert "attempts.2/plan" in history_str
    assert "attempts.2/rounds.1/act" in history_str
    assert "attempts.2/rounds.2/act" in history_str


def test_prev_act_summary_export_resolution(tmp_path: Path):
    """Verify $prev.act.summary resolves to previous attempt's exported summary artifact."""
    run_dir = tmp_path / "run_prev_exp"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_prev_exp", pipeline="prev_pipe")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})
    launcher = NestedMockLauncher()

    seed_dir = run_dir / "steps" / "seed" / "out"
    seed_dir.mkdir(parents=True)
    (seed_dir / "summary.json").write_text(json.dumps({"payload": {"attempt": 0, "action": "initial"}}))

    pipe = PipelineNode(
        kind="loop",
        id="act",
        data={
            "max": 2,
            "exports": {"summary": "$act_sum.attempt_summary"}
        },
        steps=[
            PipelineNode(
                kind="step",
                id="plan",
                data={
                    "block": "agent-call-fake",
                    "in": {"prev_summary": "$prev.act.summary || $seed.summary"}
                }
            ),
            PipelineNode(
                kind="step",
                id="act_sum",
                data={"block": "agent-call-fake", "in": {}}
            )
        ]
    )

    exec_seq([pipe], scope, launcher)

    # In attempt 1, plan received $seed.summary ("initial")
    plan1_f = run_dir / "steps" / "act.1" / "plan" / "out" / "plan.json"
    assert json.loads(plan1_f.read_text())["payload"]["planned_action"] == "initial"

    # In attempt 2, plan received $prev.act.summary ("restart" from act.1/act_sum)
    plan2_f = run_dir / "steps" / "act.2" / "plan" / "out" / "plan.json"
    assert json.loads(plan2_f.read_text())["payload"]["planned_action"] == "restart"


def test_budget_exhaustion_cli_exit_code_2(tmp_path: Path):
    """Verify budget exhaustion triggers status='stopped_budget', runs finally, and exits code 2."""
    run_dir = tmp_path / "run_budget_cli"
    run_dir.mkdir(parents=True)
    flow_dir = tmp_path / ".flow"
    flow_dir.mkdir(parents=True)

    state = RunState(run_id="run_budget_cli", pipeline="budget_pipe")
    state.counters["agent_calls"] = 15  # Already exceeded budget of 10

    proj_cfg = {"budget": {"agent_calls": 10}}
    launcher = NestedMockLauncher()

    finally_ran = []
    finally_node = PipelineNode(
        kind="step",
        id="report",
        data={"block": "agent-call-fake", "in": {}}
    )

    tree = SimpleNamespace(
        name="budget_pipe",
        params={},
        exports={},
        steps=[PipelineNode(kind="step", id="step1", data={"block": "agent-call-fake", "in": {}})],
        finally_=[finally_node]
    )

    exit_code = _run_engine(run_dir, flow_dir, tree, state, proj_cfg, "subprocess", False)

    assert exit_code == 2
    assert state.status == "stopped_budget"


def test_stream_guard_capability():
    """Verify stream_guard capability is recognized across launcher and agentlib."""
    sub_launcher = SubprocessLauncher()
    assert sub_launcher.has_capability("stream_guard") is True
    assert agentlib.has_capability("stream_guard") is True
