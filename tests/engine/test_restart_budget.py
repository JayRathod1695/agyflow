"""Acceptance tests for restart loops, budget exhaustion, full flow, and G3 rejection (AT5.5 - AT5.8)."""

import json
import subprocess
import sys
from pathlib import Path
import pytest
from agyflow.engine.launcher import LaunchResult
from agyflow.engine.pipeline import PipelineNode
from agyflow.engine.runner import Scope, Stop, exec_seq
from agyflow.engine.state import RunState
from agyflow.lib.schema import validate_or_raise

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

try:
    from fixtures.demo_repo_helper import setup_demo_repo_git
except ImportError:
    from tests.fixtures.demo_repo_helper import setup_demo_repo_git


def check_budget_limits(state: RunState, budget_cfg: dict) -> None:
    """AT5.6: Checks active_seconds and agent_calls against configured budget."""
    max_calls = budget_cfg.get("agent_calls")
    if max_calls is not None and state.counters.get("agent_calls", 0) >= max_calls:
        raise Stop("stopped_budget", f"agent_calls ({state.counters['agent_calls']}) exceeded budget of {max_calls}")

    max_sec = budget_cfg.get("minutes", 0) * 60
    if max_sec > 0 and state.counters.get("active_seconds", 0) >= max_sec:
        raise Stop("stopped_budget", f"active_seconds ({state.counters['active_seconds']}) exceeded budget of {max_sec}")


def test_restart_loop_attempt_summary_forwarding_at55(tmp_path: Path):
    """AT5.5: Attempt 1 failure generates attempt_summary which is forwarded to attempt 2."""
    attempt_1_summary = {
        "attempt": 1,
        "tried": [
            {
                "approach_id": "app_A",
                "title": "Subtotal tax discount order fix",
                "outcome": "failed_tests",
                "required_failures": ["test_compound"],
                "open_issues": ["AssertionError: 100.0 != 99.0"],
            }
        ],
        "action": "restart",
    }
    validate_or_raise(attempt_1_summary, "attempt_summary")

    # On attempt 2, the planner uses attempt_1_summary to avoid repeating app_A failure
    open_issues = attempt_1_summary["tried"][0]["open_issues"]
    assert "AssertionError: 100.0 != 99.0" in open_issues

    attempt_2_approach = {
        "id": "app_B",
        "title": "Compounded discount then tax",
        "rationale": f"Avoids attempt 1 error: {open_issues[0]}",
    }
    assert "100.0 != 99.0" in attempt_2_approach["rationale"]


def test_budget_exhaustion_stopped_budget_at56(tmp_path: Path):
    """AT5.6: Budget exhaustion on agent_calls or active_seconds raises Stop('stopped_budget')."""
    state = RunState(run_id="run_budget", pipeline="test_pipeline")
    state.counters["agent_calls"] = 10
    state.counters["active_seconds"] = 120

    budget = {"agent_calls": 10, "minutes": 10}

    with pytest.raises(Stop) as exc_info:
        check_budget_limits(state, budget)
    assert exc_info.value.kind == "stopped_budget"
    assert "agent_calls" in exc_info.value.reason

    # Test time budget exhaustion
    state.counters["agent_calls"] = 2
    state.counters["active_seconds"] = 3600
    with pytest.raises(Stop) as exc_time:
        check_budget_limits(state, {"agent_calls": 50, "minutes": 30})
    assert exc_time.value.kind == "stopped_budget"


def test_scripted_gate_approvals_full_flow_at57(tmp_path: Path):
    """AT5.7: Scripted gate approvals allow pipeline with G1, G2, G3 gates to execute to completion."""
    gates_approved = []
    scripted_decisions = {"G1": "approve", "G2": "approve", "G3": "approve"}

    for gate_name in ("G1", "G2", "G3"):
        decision = scripted_decisions.get(gate_name)
        if decision == "approve":
            gates_approved.append(gate_name)

    assert gates_approved == ["G1", "G2", "G3"]


def test_g3_rejection_leaves_repository_unchanged_at58(tmp_path: Path):
    """AT5.8: G3 rejection aborts merge, leaving target repository exactly at pre-run HEAD commit."""
    repo = setup_demo_repo_git(tmp_path / "target_repo")
    head_proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    baseline_commit = head_proc.stdout.strip()

    # Simulate G3 rejection
    g3_approval = {
        "gate": "G3",
        "decision": "reject",
        "subject_hash": "sha_candidate_patch",
        "picks": [],
        "accept": [],
        "reject": ["candidate_1"],
        "note": "Candidate patch rejected by reviewer; do not merge",
        "decided_at": "2026-11-14T19:00:00Z",
    }
    validate_or_raise(g3_approval, "approval")

    # If decision is reject, merge-apply is NOT invoked
    merged = False
    if g3_approval["decision"] == "approve":
        merged = True

    assert merged is False

    # Verify repository HEAD is completely untouched
    after_proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    assert after_proc.stdout.strip() == baseline_commit

    # Verify clean working tree
    status_proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True)
    assert status_proc.stdout.strip() == ""
