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


def test_canonical_json_and_hashes(tmp_path: Path) -> None:
    d1 = {"b": 2, "a": 1, "nested": {"y": 20, "x": 10}}
    assert canonical_json(d1) == '{"a":1,"b":2,"nested":{"x":10,"y":20}}'
    assert len(content_hash(d1)) == 64
    f = tmp_path / "t.txt"
    f.write_text("hello", encoding="utf-8")
    assert len(hash_file(f)) == 64
    assert len(hash_dir(tmp_path)) == 64


def test_load_and_validate_snapshot() -> None:
    valid = {
        "repo_path": "/repo", "head": "abcdef123456", "branch": "main",
        "dirty": False, "dirty_diff_sha256": None, "tracked_files": 42,
        "base_tree": "tree123456", "created_at": "2026-11-14T00:00:00Z",
    }
    assert validate_data(valid, "snapshot") == []
    validate_or_raise(valid, "snapshot")


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
    appr = {
        "gate": "findings", "decision": "approve", "subject_hash": "hash123",
        "picks": ["A"], "accept": ["fp1"], "reject": [], "note": "ok",
        "decided_at": "2026-11-14T12:00:00Z",
    }
    validate_or_raise(appr, "approval")
    appr_obj = {
        "id": "A", "title": "Normalize reward before KL", "differs_on": "loss",
        "differs_detail": "Swap order of scaling", "summary": "Fix scale order",
        "steps": ["Step 1", "Step 2"], "files_to_change": ["rl/loss.py"],
        "max_files_changed": 1, "risks": [{"risk": "div by zero", "mitigation": "eps"}],
        "success_criteria": ["reward std > 0.01"], "addresses": ["fp1"], "effort": "S",
    }
    validate_or_raise(appr_obj, "approach")
    plan_obj = {
        "revision": 1, "goal": "Fix reward collapse", "approaches": [appr_obj],
        "unknowns": [{"id": "U-1", "question": "Why?", "why_it_matters": "Risk", "approach_ids": ["A"]}],
        "not_doing": ["No full rewrite"],
    }
    validate_or_raise(plan_obj, "plan")
    res_obj = {
        "items": [{
            "unknown_id": "U-1", "answer_summary": "Prior paper shows stability",
            "confidence": "high", "contradicts_plan": False,
            "sources": [{"url": "https://arxiv.org", "title": "Paper", "supports": "Yes", "opened": True}],
        }],
        "unresolved": [],
    }
    validate_or_raise(res_obj, "research")


def test_m4_candidate_and_merge_schemas() -> None:
    # 1. result
    res = {
        "approach_id": "A", "status": "done", "summary": "Applied fix",
        "files_changed": ["rl/loss.py"], "commands_run": [{"cmd": "pytest", "exit_code": 0}],
        "smoke": {"ran": True, "exit_code": 0, "notes": "fast smoke passed"},
        "deviations_from_plan": [], "open_issues": [],
    }
    validate_or_raise(res, "result")

    # 2. patch_meta
    pm = {
        "approach_id": "A", "base_commit": "abcdef123",
        "files": [{"path": "rl/loss.py", "insertions": 5, "deletions": 2, "status": "M"}],
        "insertions": 5, "deletions": 2, "patch_sha256": "sha123", "binary_files": [],
    }
    validate_or_raise(pm, "patch_meta")

    # 3. guard
    gd = {"ok": True, "violations": []}
    validate_or_raise(gd, "guard")

    # 4. merge_result
    mr = {"branch": "agyflow/run1", "commit": "c123", "base_commit": "b123", "patch_sha256": "p123"}
    validate_or_raise(mr, "merge_result")

    # 5. item
    it = {"id": "A"}
    validate_or_raise(it, "item")

    # 6. foreach_index
    idx = {"items": [{"id": "A", "status": "ok"}, {"id": "B", "status": "failed"}]}
    validate_or_raise(idx, "foreach_index")
