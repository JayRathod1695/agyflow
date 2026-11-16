"""Interactive terminal UI dashboard for live agyflow workflow monitoring."""

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


def _format_node(xid: str, status: str, attempt: int = 1) -> str:
    short = xid.split("/")[-1].split(".")[-1]
    if status == "committed":
        return f"[✓ {short}]"
    if status == "started":
        return f"[► {short} #{attempt}]"
    if status == "failed":
        return f"[✗ {short}]"
    if status == "skipped":
        return f"[- {short}]"
    return f"[· {short}]"


def render_dashboard(stdscr: Any, run_dir: Path) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.use_default_colors()

    # Define color pairs if available
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(1, curses.COLOR_GREEN, -1)   # Committed / Success
        curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Started / Running
        curses.init_pair(3, curses.COLOR_RED, -1)     # Failed
        curses.init_pair(4, curses.COLOR_CYAN, -1)    # Title / Headers
        curses.init_pair(5, curses.COLOR_MAGENTA, -1) # Paused / Gate

    while True:
        try:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            if h < 10 or w < 40:
                stdscr.addstr(0, 0, "Terminal too small")
                stdscr.refresh()
                time.sleep(0.5)
                continue

            state: RunState | None = None
            try:
                state = load_state(run_dir)
            except Exception:
                pass

            # Header
            rid = state.run_id if state else run_dir.name
            st = state.status.upper() if state else "UNKNOWN"
            pipe = state.pipeline if state else "pipeline"
            header = f" AGYFLOW 2D COCKPIT  |  Run: {rid}  |  Pipeline: {pipe}  |  Status: {st} "
            stdscr.addstr(0, 0, header[:w], curses.color_pair(4) | curses.A_BOLD)
            stdscr.addstr(1, 0, "═" * min(w - 1, len(header)))

            row = 3
            if state:
                # Metrics Bar
                calls = state.counters.get("agent_calls", 0)
                sec = state.counters.get("active_seconds", 0)
                retries = state.counters.get("retries", 0)
                gate = state.pending.get("gate")
                gate_str = f" [PAUSED AT GATE: {gate}]" if gate else ""
                metrics_line = f"Agent Calls: {calls}  |  Active: {sec}s  |  Retries: {retries}{gate_str}"
                stdscr.addstr(row, 2, metrics_line[:w - 4], curses.color_pair(5) if gate else curses.A_BOLD)
                row += 2

                # 2D Live Stage Map
                stdscr.addstr(row, 2, "LIVE STAGES:", curses.color_pair(4) | curses.A_BOLD)
                row += 1

                sections: dict[str, list[tuple[str, str, int]]] = {
                    "PREFLIGHT": [],
                    "ANALYZE": [],
                    "PLAN": [],
                    "ACT": [],
                    "MERGE & REPORT": [],
                }

                for xid, rec in state.steps.items():
                    lx = xid.lower()
                    if "pre" in lx or "snap" in lx or "base" in lx or "ctx" in lx or "test" in lx:
                        sections["PREFLIGHT"].append((xid, rec.status, rec.attempt))
                    elif "ana" in lx or "evid" in lx or "repro" in lx or "converge" in lx:
                        sections["ANALYZE"].append((xid, rec.status, rec.attempt))
                    elif "plan" in lx or "sel" in lx or "gate1" in lx or "v1" in lx or "research" in lx or "gate2" in lx:
                        sections["PLAN"].append((xid, rec.status, rec.attempt))
                    elif "act" in lx or "judge" in lx or "rank" in lx or "guard" in lx or "patch" in lx:
                        sections["ACT"].append((xid, rec.status, rec.attempt))
                    else:
                        sections["MERGE & REPORT"].append((xid, rec.status, rec.attempt))

                for sname, items in sections.items():
                    if row >= h - 4:
                        break
                    nodes_str = " -> ".join(_format_node(x, stt, att) for x, stt, att in items) if items else "[Pending]"
                    stdscr.addstr(row, 4, f"{sname:<14} ", curses.A_BOLD)
                    stdscr.addstr(row, 20, nodes_str[:max(0, w - 22)])
                    row += 1

                row += 1
                # Live Events Stream
                stdscr.addstr(row, 2, "RECENT EVENTS:", curses.color_pair(4) | curses.A_BOLD)
                row += 1

                events_file = run_dir / "events.jsonl"
                if events_file.is_file():
                    try:
                        lines = [l.strip() for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                        for line in lines[-max(1, h - row - 2):]:
                            ev = json.loads(line)
                            ev_str = f"• [{ev.get('ts', '')[-8:]}] {ev.get('ev', '').upper():<10} step={ev.get('step', '')} attempt={ev.get('attempt', 1)}"
                            color = curses.color_pair(1) if ev.get("ev") == "committed" else curses.color_pair(2)
                            stdscr.addstr(row, 4, ev_str[:w - 6], color)
                            row += 1
                    except Exception:
                        pass

            # Bottom status
            stdscr.addstr(h - 1, 0, " Press 'q' to exit dashboard ".center(min(w - 1, 80)), curses.A_REVERSE)
            stdscr.refresh()

            # Handle user input non-blockingly
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
        # Split pane quietly to the right
        split_cmd = [herdr, "pane", "split", "--current", "--direction", "right", "--cwd", str(repo_root), "--no-focus"]
        res = subprocess.run(split_cmd, capture_output=True, text=True, check=True)
        pane_id = json.loads(res.stdout).get("result", {}).get("pane", {}).get("pane_id")
        if not pane_id:
            return False
        # Launch dashboard in the created pane
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
