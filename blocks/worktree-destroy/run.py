"""Worktree destruction block: safely restores permissions and cleans up worktrees."""

from __future__ import annotations

from pathlib import Path
import sys

from agyflow.lib.contract import (
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_status,
)
from agyflow.lib.gitutil import remove_worktree


def main() -> int:
    args = parse_cli_args()
    repo_path = Path(args.ctx.get("repo", ".")).resolve()

    flow_raw = args.ctx.get("flow_dir")
    flow_dir = Path(flow_raw).resolve() if flow_raw else repo_path / ".flow"
    worktrees_root = (flow_dir / "worktrees").resolve()

    all_for_run = bool(args.cfg.get("all_for_run", False))
    run_id = args.ctx.get("run_id", "default_run")

    if all_for_run:
        run_wt_dir = (worktrees_root / run_id).resolve()
        if run_wt_dir.is_dir():
            for child in run_wt_dir.iterdir():
                if child.is_dir():
                    try:
                        child.resolve().relative_to(run_wt_dir)
                        remove_worktree(repo_path, child)
                    except ValueError:
                        continue
            try:
                run_wt_dir.rmdir()
            except OSError:
                pass
    else:
        wt_file = args.in_dir / "wt.json"
        if wt_file.is_file():
            wt_payload = load_input_payload(args.in_dir, "wt")
            target_path = Path(wt_payload["path"]).resolve()
            try:
                target_path.relative_to(worktrees_root)
                remove_worktree(repo_path, target_path)
            except ValueError:
                pass

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
