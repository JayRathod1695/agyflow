"""Unit tests for src/agyflow/lib/schema.py."""

from pathlib import Path
import pytest
from agyflow.lib.schema import (
    canonical_json,
    content_hash,
    hash_dir,
    hash_file,
    load_schema,
    validate_data,
    validate_or_raise,
)


def test_canonical_json_determinism() -> None:
    d1 = {"b": 2, "a": 1, "nested": {"y": 20, "x": 10}}
    d2 = {"nested": {"x": 10, "y": 20}, "a": 1, "b": 2}
    assert canonical_json(d1) == canonical_json(d2)
    assert canonical_json(d1) == '{"a":1,"b":2,"nested":{"x":10,"y":20}}'


def test_content_hash() -> None:
    d1 = {"foo": "bar"}
    assert content_hash(d1) == content_hash({"foo": "bar"})
    assert len(content_hash(d1)) == 64


def test_hash_file_and_dir(tmp_path: Path) -> None:
    f1 = tmp_path / "test.txt"
    f1.write_text("hello world", encoding="utf-8")
    assert len(hash_file(f1)) == 64
    assert len(hash_dir(tmp_path)) == 64


def test_load_and_validate_snapshot() -> None:
    schema = load_schema("snapshot")
    assert schema["title"] == "SnapshotPayload"
    valid = {
        "repo_path": "/repo", "head": "abcdef123456", "branch": "main",
        "dirty": False, "dirty_diff_sha256": None, "tracked_files": 42,
        "base_tree": "tree123456", "created_at": "2026-11-14T00:00:00Z",
    }
    assert validate_data(valid, "snapshot") == []
    validate_or_raise(valid, "snapshot")
    invalid = valid.copy()
    invalid["head"] = "short"
    assert len(validate_data(invalid, "snapshot")) > 0


def test_m2_verification_schemas() -> None:
    run_res = {
        "cmd": ["pytest"], "cwd": "/repo", "exit_code": 0, "timed_out": False,
        "signal": None, "duration_s": 1.25, "stdout_path": "stdout.txt",
        "stderr_path": "stderr.txt", "stdout_tail": "passed", "stderr_tail": "",
        "truncated": False, "junit_path": "junit.xml",
    }
    validate_or_raise(run_res, "run_result")

    assertion = {"ok": True, "expected": "exit 0", "actual": "exit 0", "detail": "ok"}
    validate_or_raise(assertion, "assertion")

    summary = {
        "total": 10, "passed": 9, "failed": 1, "errors": 0, "skipped": 0,
        "failed_ids": ["test_math::test_div"], "duration_s": 2.5, "source": "junit",
    }
    validate_or_raise(summary, "test_summary")

    manifest = {"paths": ["tests/"], "files": {"tests/a.py": "sha"}, "root_hash": "root"}
    validate_or_raise(manifest, "test_manifest")

    conv = {
        "done": True, "reason": "converged", "round": 2, "new_high_ids": [],
        "repeated_fingerprints": ["fp1"], "zero_findings": False,
    }
    validate_or_raise(conv, "convergence")

    kb = {"items": [{"claim_key": "loss bug", "file": "loss.py", "status": "CONFIRMED"}]}
    validate_or_raise(kb, "known_brief")


def test_m3_planning_and_gate_schemas() -> None:
    # 1. approval
    appr = {
        "gate": "findings", "decision": "approve", "subject_hash": "hash123",
        "picks": ["A"], "accept": ["fp1"], "reject": [], "note": "ok",
        "decided_at": "2026-11-14T12:00:00Z",
    }
    validate_or_raise(appr, "approval")

    # 2. approach
    appr_obj = {
        "id": "A", "title": "Normalize reward before KL", "differs_on": "loss",
        "differs_detail": "Swap order of scaling", "summary": "Fix scale order",
        "steps": ["Step 1", "Step 2"], "files_to_change": ["rl/loss.py"],
        "max_files_changed": 1, "risks": [{"risk": "div by zero", "mitigation": "eps"}],
        "success_criteria": ["reward std > 0.01"], "addresses": ["fp1"], "effort": "S",
    }
    validate_or_raise(appr_obj, "approach")

    # 3. plan
    plan_obj = {
        "revision": 1, "goal": "Fix reward collapse", "approaches": [appr_obj],
        "unknowns": [{"id": "U-1", "question": "Why?", "why_it_matters": "Risk", "approach_ids": ["A"]}],
        "not_doing": ["No full rewrite"],
    }
    validate_or_raise(plan_obj, "plan")

    # 4. research
    res_obj = {
        "items": [{
            "unknown_id": "U-1", "answer_summary": "Prior paper shows stability",
            "confidence": "high", "contradicts_plan": False,
            "sources": [{"url": "https://arxiv.org", "title": "Paper", "supports": "Yes", "opened": True}],
        }],
        "unresolved": [],
    }
    validate_or_raise(res_obj, "research")
