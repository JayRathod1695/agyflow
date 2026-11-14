"""Unit tests for src/agyflow/lib/schema.py."""

from pathlib import Path
import pytest
from agyflow.lib.schema import (
    SchemaValidationError,
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
    h1 = content_hash(d1)
    h2 = content_hash({"foo": "bar"})
    assert h1 == h2
    assert len(h1) == 64


def test_hash_file_and_dir(tmp_path: Path) -> None:
    f1 = tmp_path / "test.txt"
    f1.write_text("hello world", encoding="utf-8")
    h = hash_file(f1)
    assert len(h) == 64

    dir_hash = hash_dir(tmp_path)
    assert len(dir_hash) == 64


def test_load_and_validate_snapshot() -> None:
    schema = load_schema("snapshot")
    assert schema["title"] == "SnapshotPayload"

    valid_payload = {
        "repo_path": "/path/to/repo",
        "head": "abcdef123456",
        "branch": "main",
        "dirty": False,
        "dirty_diff_sha256": None,
        "tracked_files": 42,
        "base_tree": "tree123456",
        "created_at": "2026-11-14T00:00:00Z",
    }
    assert validate_data(valid_payload, "snapshot") == []
    validate_or_raise(valid_payload, "snapshot")

    invalid = valid_payload.copy()
    invalid["head"] = "short"
    assert len(validate_data(invalid, "snapshot")) > 0


def test_m2_verification_schemas() -> None:
    # 1. run_result
    run_res = {
        "cmd": ["pytest"],
        "cwd": "/repo",
        "exit_code": 0,
        "timed_out": False,
        "signal": None,
        "duration_s": 1.25,
        "stdout_path": "stdout.txt",
        "stderr_path": "stderr.txt",
        "stdout_tail": "passed",
        "stderr_tail": "",
        "truncated": False,
        "junit_path": "junit.xml",
    }
    assert validate_data(run_res, "run_result") == []
    validate_or_raise(run_res, "run_result")

    # 2. assertion
    assertion = {
        "ok": True,
        "expected": "exit 0",
        "actual": "exit 0",
        "detail": "all tests passed",
    }
    assert validate_data(assertion, "assertion") == []
    validate_or_raise(assertion, "assertion")

    # 3. test_summary
    summary = {
        "total": 10,
        "passed": 9,
        "failed": 1,
        "errors": 0,
        "skipped": 0,
        "failed_ids": ["test_math::test_div"],
        "duration_s": 2.5,
        "source": "junit",
    }
    assert validate_data(summary, "test_summary") == []
    validate_or_raise(summary, "test_summary")

    # 4. test_manifest
    manifest = {
        "paths": ["tests/"],
        "files": {"tests/test_foo.py": "sha256abc"},
        "root_hash": "sha256root",
    }
    assert validate_data(manifest, "test_manifest") == []
    validate_or_raise(manifest, "test_manifest")

    # 5. convergence
    conv = {
        "done": True,
        "reason": "zero new high findings",
        "round": 2,
        "new_high_ids": [],
        "repeated_fingerprints": ["fp1"],
        "zero_findings": False,
    }
    assert validate_data(conv, "convergence") == []
    validate_or_raise(conv, "convergence")

    # 6. known_brief
    kb = {
        "items": [
            {"claim_key": "kl penalty before norm", "file": "rl/loss.py", "status": "CONFIRMED"}
        ]
    }
    assert validate_data(kb, "known_brief") == []
    validate_or_raise(kb, "known_brief")
