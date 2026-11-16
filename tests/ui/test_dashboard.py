"""Unit and integration tests for agyflow live 2D cockpit UI."""

from __future__ import annotations

import json
from pathlib import Path

from agyflow.engine.state import RunState, save_state
from agyflow.ui.dashboard import _format_node, launch_in_herdr_sidebar


def test_format_node():
    assert "✓" in _format_node("pre/snap", "committed")
    assert "►" in _format_node("ana/analyst", "started", attempt=2)
    assert "#2" in _format_node("ana/analyst", "started", attempt=2)
    assert "✗" in _format_node("act/judge", "failed")
    assert "-" in _format_node("act/stress", "skipped")
    assert "·" in _format_node("merge/publish", "unknown")


def test_launch_in_herdr_sidebar_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    res = launch_in_herdr_sidebar(tmp_path, tmp_path)
    assert res is False


def test_dashboard_state_renderable(tmp_path: Path):
    rdir = tmp_path / "run_test"
    rdir.mkdir(parents=True)
    st = RunState(run_id="run_test", pipeline="full")
    st.mark_started("preflight/snap", 1, "ih1")
    st.mark_committed("preflight/snap", "ih1")
    st.mark_started("analyze/analyst", 1, "ih2")
    save_state(rdir, st)

    events_file = rdir / "events.jsonl"
    events_file.write_text(json.dumps({"ts": "12:00:00", "ev": "started", "step": "preflight/snap", "attempt": 1}) + "\n")

    assert (rdir / "state.json").is_file()
    assert events_file.is_file()
