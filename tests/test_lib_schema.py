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
    errors = validate_data(valid_payload, "snapshot")
    assert errors == []
    validate_or_raise(valid_payload, "snapshot")

    invalid_payload = valid_payload.copy()
    invalid_payload["head"] = "short"  # minLength 7
    errs = validate_data(invalid_payload, "snapshot")
    assert len(errs) > 0
    with pytest.raises(SchemaValidationError):
        validate_or_raise(invalid_payload, "snapshot")
