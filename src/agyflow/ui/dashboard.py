"""Interactive 2D ASCII box terminal UI dashboard for live agyflow workflow monitoring."""

from __future__ import annotations

import curses
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

from agyflow.engine.state import RunState, load_state


def _node_state(status: str) -> tuple[str, str]:
    if status == "committed":
        return "▓", "DONE"
    if status == "started":
        return "▒", "WORKING"
    if status == "failed":
        return "✗", "FAILED"
    if status == "paused_gate":
        return "⏸", "GATE"
    if status == "skipped":
        return "-", "SKIPPED"
    return "░", "WAIT"


def _draw_box_row(nodes: list[tuple[str, str]], indent: int = 4) -> list[str]:
    if not nodes:
        return [" " * indent + "  (Pending)"]
    top = " " * indent
    mid1 = " " * indent
    mid2 = " " * indent
    bot = " " * indent
    for idx, (label, status) in enumerate(nodes):
        sym, state_txt = _node_state(status)
        lbl_trunc = (label[:8]).center(8)
        st_trunc = (f"{sym} {state_txt}"[:8]).center(8)
        top += "┌──────────┐"
        mid1 += f"│ {lbl_trunc} │"
        mid2 += f"│ {st_trunc} │"
        bot += "└──────────┘"
        if idx < len(nodes) - 1:
            top += "  "
            mid1 += "─▶"
            mid2 += "  "
            bot += "  "
    return [top, mid1, mid2, bot]


def render_dashboard(stdscr: Any, run_dir: Path) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.use_default_colors()

    if curses.has_colors():
        curses.start_color()
        curses.init_pair(1, curses.COLOR_GREEN, -1)   # DONE
        curses.init_pair(2, curses.COLOR_YELLOW, -1)  # WORKING
        curses.init_pair(3, curses.COLOR_RED, -1)     # FAILED
        curses.init_pair(4, curses.COLOR_CYAN, -1)    # Headers & Section titles
        curses.init_pair(5, curses.COLOR_MAGENTA, -1) # Paused / Gate

    while True:
        try:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            if h < 18 or w < 60:
                stdscr.addstr(0, 0, f"Terminal size ({w}x{h}) too small. Need at least 80x24.")
                stdscr.refresh()
                time.sleep(0.5)
                continue

            state: RunState | None = None
            try:
                state = load_state(run_dir)
            except Exception:
                pass

            rid = state.run_id if state else run_dir.name
            st = state.status.upper() if state else "RUNNING"
            title_line = f" AGYFLOW LIVE DASHBOARD                  Run: {rid}  {st} "
            border = "═" * min(w - 2, 79)

            row = 0
            stdscr.addstr(row, 0, f"+{border}+"[:w], curses.color_pair(4) | curses.A_BOLD)
            row += 1
            stdscr.addstr(row, 0, f"|{title_line:<{len(border)}}|"[:w], curses.A_BOLD)
            row += 1
            stdscr.addstr(row, 0, f"+{border}+"[:w], curses.color_pair(4) | curses.A_BOLD)
            row += 1

            step_map: dict[str, str] = {x: rec.status for x, rec in state.steps.items()} if state else {}

            sections: list[tuple[str, list[str]]] = [
                ("PREFLIGHT", ["snap", "tests", "base_wt", "base_run", "base_ok"]),
                ("PREFLIGHT CONTEXT", ["ro_wt", "ctx_frsh", "ctx_build"]),
                ("ANALYZE (Loop)", ["analyst", "evid", "repro_wt", "repros", "converge"]),
                ("PLAN", ["gate1", "sel", "plan1", "v1", "gate2"]),
                ("ACT (foreach)", ["pick", "wt", "act", "patch", "guard", "judge"]),
                ("MERGE & PUBLISH", ["gate3", "chosen", "merge", "publish", "report"]),
            ]

            for sname, node_names in sections:
                if row >= h - 5:
                    break
                stdscr.addstr(row, 2, f"  {sname}"[:w - 4], curses.color_pair(4) | curses.A_BOLD)
                row += 1

                nodes_data = [(n, step_map.get(n, "wait")) for n in node_names]
                # Fallback fuzzy match for scoped IDs like "pre/snap" or "ana/analyst"
                resolved = []
                for n, stt in nodes_data:
                    curr_stt = stt
                    if curr_stt == "wait":
                        for k, v in step_map.items():
                            if k.endswith(f"/{n}") or k.endswith(f".{n}") or k == n:
                                curr_stt = v
                                break
                    resolved.append((n, curr_stt))

                box_lines = _draw_box_row(resolved, indent=4)
                for bl in box_lines:
                    if row >= h - 4:
                        break
                    stdscr.addstr(row, 0, bl[:w - 1])
                    row += 1
                row += 1

            if row < h - 3:
                legend = "  Legend: ▓ DONE  ▒ WORKING  ░ WAIT  ✗ FAILED  ⏸ GATE PAUSED"
                stdscr.addstr(row, 0, legend[:w - 1], curses.A_DIM)
                row += 1

            if state and row < h - 2:
                calls = state.counters.get("agent_calls", 0)
                sec = state.counters.get("active_seconds", 0)
                retries = state.counters.get("retries", 0)
                gate_str = f"  |  PAUSED: {state.pending.get('gate')}" if state.pending.get("gate") else ""
                stats = f"  Agent Calls: {calls}/30    Active: {sec}s/3600s    Retries: {retries}{gate_str}"
                stdscr.addstr(row, 0, stats[:w - 1], curses.A_BOLD)
                row += 1

            stdscr.addstr(h - 1, 0, " [ Press 'q' to exit cockpit ] ".center(min(w - 1, 80)), curses.A_REVERSE)
            stdscr.refresh()

            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                break
            time.sleep(0.5)

        except KeyboardInterrupt:
            break
        except Exception:
            time.sleep(0.5)


def run_dashboard_ui(run_dir: Path) -> int:
    curses.wrapper(lambda scr: render_dashboard(scr, run_dir))
    return 0


def launch_in_herdr_sidebar(run_dir: Path, repo_root: Path) -> bool:
    herdr = shutil.which("herdr")
    if not herdr or not run_dir.exists():
        return False
    try:
        split_cmd = [herdr, "pane", "split", "--current", "--direction", "right", "--cwd", str(repo_root), "--no-focus"]
        res = subprocess.run(split_cmd, capture_output=True, text=True, check=True)
        pane_id = json.loads(res.stdout).get("result", {}).get("pane", {}).get("pane_id")
        if not pane_id:
            return False
        dash_cmd = f"python3.11 -m agyflow.ui.dashboard {run_dir.resolve()}"
        time.sleep(0.2)
        subprocess.run([herdr, "pane", "run", pane_id, dash_cmd], capture_output=True, check=True)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_dashboard_ui(Path(sys.argv[1]))
    else:
        print("Usage: python3.11 -m agyflow.ui.dashboard <run_dir>")
