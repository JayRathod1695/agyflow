"""Unit tests for src/agyflow/lib/contract.py."""

import json
from pathlib import Path
import pytest
from agyflow.lib.contract import (
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)


def test_parse_cli_args(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text('{"foo": "bar", "_ctx": {"run_id": "r1"}}', encoding="utf-8")

    args = parse_cli_args(["--in", str(in_dir), "--out", str(out_dir), "--cfg", str(cfg_file)])
    assert args.in_dir == in_dir
    assert args.out_dir == out_dir
    assert args.cfg["foo"] == "bar"
    assert args.ctx["run_id"] == "r1"


def test_write_and_read_artifact(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    payload = {
        "repo_path": "/repo",
        "head": "abcdef1234",
        "branch": "main",
        "dirty": False,
        "dirty_diff_sha256": None,
        "tracked_files": 10,
        "base_tree": "tree123",
        "created_at": "2026-11-14T10:00:00Z",
    }
    art_path = write_artifact(
        out_dir=out_dir,
        name="snapshot",
        schema_type="snapshot",
        payload=payload,
        step="snap",
        block="repo-snapshot",
    )
    assert art_path.exists()
    assert art_path.name == "snapshot.json"

    env, p = read_artifact(art_path)
    assert env["schema"] == "snapshot"
    assert env["produced_by"]["step"] == "snap"
    assert p["head"] == "abcdef1234"

    loaded = load_input_payload(out_dir, "snapshot")
    assert loaded["head"] == "abcdef1234"


def test_write_status(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    status_path = write_status(
        out_dir=out_dir,
        status="ok",
        code="",
        reason="success",
        complete=True,
    )
    assert status_path.exists()
    assert status_path.name == "_status.json"

    with open(status_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "ok"
    assert data["complete"] is True
