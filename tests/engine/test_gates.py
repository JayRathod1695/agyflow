"""Acceptance tests for human gate pausing, approve/resume lifecycle, and hash-binding (AT3.2)."""

import json
from pathlib import Path
import pytest
from agyflow.engine.launcher import LaunchResult
from agyflow.engine.pipeline import PipelineNode
from agyflow.engine.runner import Pause, Scope, exec_seq
from agyflow.engine.state import RunState
from agyflow.lib.schema import content_hash, validate_or_raise


class MockGateLauncher:
    """Simulates a gate block evaluating approval against subject artifact."""

    def __init__(self, subject_data: dict, approval_data: dict | None = None):
        self.subject_data = subject_data
        self.approval_data = approval_data

    def run(self, cmd: list[str], cwd=None, env=None, stdout_log=None, stderr_log=None) -> LaunchResult:
        out_idx = cmd.index("--out") + 1
        out_dir = Path(cmd[out_idx])
        out_dir.mkdir(parents=True, exist_ok=True)

        expected_hash = content_hash(self.subject_data)

        if not self.approval_data:
            # First run: pause gate, write request & review
            req = {
                "schema_version": "1.0.0",
                "gate": "G1",
                "subject_hash": expected_hash,
                "summary": "Requesting human review for findings",
            }
            (out_dir / "request.json").write_text(json.dumps({"payload": req}))
            (out_dir / "review.md").write_text("# Gate G1 Review\nPlease approve findings.\n")
            (out_dir / "_status.json").write_text(json.dumps({
                "status": "needs_human",
                "code": "E_NEEDS_HUMAN",
                "reason": "Awaiting human review",
                "complete": False,
            }))
            return LaunchResult(exit_code=30, duration_s=0.1, timed_out=False, stdout="", stderr="")

        # Approval provided: verify subject_hash binding
        actual_hash = self.approval_data.get("subject_hash")
        if actual_hash != expected_hash:
            (out_dir / "_status.json").write_text(json.dumps({
                "status": "failed",
                "code": "E_INPUT",
                "reason": f"Approval subject_hash mismatch: expected {expected_hash}, got {actual_hash}",
                "complete": False,
            }))
            return LaunchResult(exit_code=20, duration_s=0.1, timed_out=False, stdout="", stderr="Hash mismatch")

        # Hash matches: approval accepted
        (out_dir / "approval.json").write_text(json.dumps({"payload": self.approval_data}))
        (out_dir / "_status.json").write_text(json.dumps({"status": "ok", "complete": True}))
        return LaunchResult(exit_code=0, duration_s=0.1, timed_out=False, stdout="", stderr="")


def test_gate_pauses_with_exit_30_and_records_pending(tmp_path: Path):
    """Verify unapproved gate raises Pause('paused_gate') with exit 30 and records request."""
    run_dir = tmp_path / "run_gate_pause"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_gate_pause", pipeline="test_pipeline")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})

    subject = {"findings": [{"id": "F-01", "severity": "HIGH"}]}
    launcher = MockGateLauncher(subject_data=subject, approval_data=None)

    gate_node = PipelineNode(
        kind="step",
        id="gate_findings",
        data={"block": "agent-call-fake"},
    )

    with pytest.raises(Pause) as exc_info:
        exec_seq([gate_node], scope, launcher)

    assert exc_info.value.kind == "paused_gate"
    assert state.pending["gate"] == "gate_findings"
    assert state.pending["subject_hash"] == content_hash(subject)
    assert (run_dir / "gates" / "gate_findings.request.json").is_file()
    assert (run_dir / "gates" / "gate_findings.review.md").is_file()


def test_gate_approve_and_resume_flow(tmp_path: Path):
    """Verify gate resumes cleanly when valid approval matching subject_hash is supplied."""
    run_dir = tmp_path / "run_gate_resume"
    run_dir.mkdir(parents=True)
    state = RunState(run_id="run_gate_resume", pipeline="test_pipeline")
    scope = Scope(run_dir=run_dir, state=state, project_cfg={}, params={})

    subject = {"findings": [{"id": "F-01", "severity": "HIGH"}]}
    valid_hash = content_hash(subject)
    approval_payload = {
        "gate": "G1",
        "decision": "approve",
        "subject_hash": valid_hash,
        "picks": ["F-01"],
        "accept": ["F-01"],
        "reject": [],
        "note": "Approved by engineering lead",
        "decided_at": "2026-11-14T18:00:00Z",
    }
    validate_or_raise(approval_payload, "approval")

    launcher = MockGateLauncher(subject_data=subject, approval_data=approval_payload)
    gate_node = PipelineNode(kind="step", id="gate_findings", data={"block": "agent-call-fake"})

    # Must execute to completion without pause
    exec_seq([gate_node], scope, launcher)
    assert state.steps["gate_findings"].status == "committed"
    assert state.pending.get("gate") is None


def test_gate_rejects_invalid_subject_hash_at32(tmp_path: Path):
    """AT3.2: Verify approval is rejected when subject_hash does not match subject artifact."""
    subject = {"findings": [{"id": "F-01", "severity": "HIGH"}]}
    stale_hash = "0000000000000000000000000000000000000000000000000000000000000000"

    approval_payload = {
        "gate": "G1",
        "decision": "approve",
        "subject_hash": stale_hash,
        "picks": [],
        "accept": [],
        "reject": [],
        "note": "Stale approval from old run",
        "decided_at": "2026-11-14T18:00:00Z",
    }
    validate_or_raise(approval_payload, "approval")

    launcher = MockGateLauncher(subject_data=subject, approval_data=approval_payload)
    res = launcher.run(["main.py", "--out", str(tmp_path / "out")])
    assert res.exit_code == 20
    assert "Hash mismatch" in res.stderr
