"""Unit and integration tests for agyflow live 2D box cockpit UI."""

from __future__ import annotations

from pathlib import Path

from agyflow.engine.state import RunState, save_state
from agyflow.ui.dashboard import _draw_box_row, _node_state, launch_in_herdr_sidebar


def test_node_state():
    sym, txt = _node_state("committed")
    assert sym == "▓" and txt == "DONE"

    sym, txt = _node_state("started")
    assert sym == "▒" and txt == "WORKING"

    sym, txt = _node_state("failed")
    assert sym == "✗" and txt == "FAILED"

    sym, txt = _node_state("wait")
    assert sym == "░" and txt == "WAIT"


def test_draw_box_row():
    nodes = [("snap", "committed"), ("tests", "started")]
    lines = _draw_box_row(nodes, indent=2)
    assert len(lines) == 4
    assert "┌──────────┐" in lines[0]
    assert "─▶" in lines[1]
    assert "▓" in lines[2]
    assert "▒" in lines[2]
    assert "└──────────┘" in lines[3]


def test_launch_in_herdr_sidebar_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    res = launch_in_herdr_sidebar(tmp_path, tmp_path)
    assert res is False


def test_dashboard_state_renderable(tmp_path: Path):
    rdir = tmp_path / "run_test"
    rdir.mkdir(parents=True)
    st = RunState(run_id="run_test", pipeline="full")
    st.mark_started("snap", 1, "ih1")
    st.mark_committed("snap", "ih1")
    st.mark_started("analyst", 1, "ih2")
    save_state(rdir, st)

    assert (rdir / "state.json").is_file()
