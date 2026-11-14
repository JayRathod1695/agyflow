"""Repository snapshot block: captures commit, branch, dirty status, and base tree."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from agyflow.lib.contract import (
    E_INPUT,
    E_INTERNAL,
    E_NEEDS_HUMAN,
    EXIT_FAILED,
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    parse_cli_args,
    write_artifact,
    write_status,
)
from agyflow.lib.gitutil import GitError, compute_dirty_diff_sha256, get_repo_status


def main() -> int:
    args = parse_cli_args()
    repo_raw = args.ctx.get("repo", ".")
    repo_path = Path(repo_raw).resolve()

    try:
        status = get_repo_status(repo_path)
    except GitError as err:
        write_status(
            args.out_dir,
            status="failed",
            code=E_INPUT,
            reason=f"Not a valid git repository: {err}",
            complete=False,
        )
        return EXIT_FAILED
    except Exception as err:
        write_status(
            args.out_dir,
            status="failed",
            code=E_INTERNAL,
            reason=f"Failed to inspect git repository: {err}",
            complete=False,
        )
        return EXIT_FAILED

    allow_dirty = bool(args.cfg.get("allow_dirty", False))
    if status["dirty"] and not allow_dirty:
        reason = "Working tree dirty: commit, stash, or enable allow_dirty in project config"
        write_status(
            args.out_dir,
            status="needs_human",
            code=E_NEEDS_HUMAN,
            reason=reason,
            complete=False,
        )
        return EXIT_NEEDS_HUMAN

    dirty_sha = compute_dirty_diff_sha256(repo_path) if status["dirty"] else None

    payload = {
        "repo_path": status["repo_path"],
        "head": status["head"],
        "branch": status["branch"],
        "dirty": status["dirty"],
        "dirty_diff_sha256": dirty_sha,
        "tracked_files": status["tracked_files"],
        "base_tree": status["base_tree"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    write_artifact(
        out_dir=args.out_dir,
        name="snapshot",
        schema_type="snapshot",
        payload=payload,
        step=args.ctx.get("step", "snap"),
        block="repo-snapshot",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
