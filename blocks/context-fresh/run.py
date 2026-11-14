"""Context freshness block: checks if existing context is fresh vs HEAD."""

from __future__ import annotations

from pathlib import Path
import sys

from agyflow.lib.contract import (
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)
from agyflow.lib.proc import run_command


def _commit_exists(repo_path: Path, commit: str) -> bool:
    res = run_command(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo_path)
    return res.returncode == 0


def _count_commits_behind(repo_path: Path, base: str, head: str) -> int:
    res = run_command(["git", "rev-list", "--count", f"{base}..{head}"], cwd=repo_path)
    if res.returncode != 0:
        return 0
    try:
        return int(res.stdout.strip())
    except ValueError:
        return 0


def main() -> int:
    args = parse_cli_args()
    snapshot = load_input_payload(args.in_dir, "snapshot")
    head = snapshot["head"]
    repo_path = Path(args.ctx.get("repo", ".")).resolve()

    context_prev_file = args.in_dir / "context_prev.json"
    if not context_prev_file.is_file():
        fresh = False
        reason = "no_previous_context"
        context_commit = None
        commits_behind = None
        prev_payload = None
    else:
        prev_payload = load_input_payload(args.in_dir, "context_prev")
        built_at = prev_payload.get("built_at_commit")
        context_commit = built_at

        if not built_at or not _commit_exists(repo_path, built_at):
            fresh = False
            reason = "built_at_commit_not_in_repo"
            commits_behind = None
        elif built_at == head:
            fresh = True
            reason = "identical_commit"
            commits_behind = 0
        else:
            behind = _count_commits_behind(repo_path, built_at, head)
            commits_behind = behind
            mode = args.cfg.get("mode", "paths")

            if mode == "commits":
                max_behind = int(args.cfg.get("max_commits_behind", 0))
                fresh = behind <= max_behind
                reason = "within_commits_tolerance" if fresh else "too_many_commits_behind"
            else:
                # paths mode: check if any module path changed
                diff_res = run_command(["git", "diff", "--name-only", f"{built_at}..{head}"], cwd=repo_path)
                changed = [line.strip() for line in diff_res.stdout.splitlines() if line.strip()]
                modules = prev_payload.get("modules", [])
                mod_paths = [m.get("path") for m in modules if isinstance(m, dict) and "path" in m]

                stale = False
                for cfile in changed:
                    for mpath in mod_paths:
                        if cfile == mpath or cfile.startswith(mpath.rstrip("/") + "/"):
                            stale = True
                            break
                    if stale:
                        break

                fresh = not stale
                reason = "no_module_paths_changed" if fresh else "module_paths_modified"

    if fresh and prev_payload is not None:
        write_artifact(
            out_dir=args.out_dir,
            name="context",
            schema_type="context",
            payload=prev_payload,
            step=args.ctx.get("step", "ctx_fresh"),
            block="context-fresh",
        )

    freshness_payload = {
        "fresh": fresh,
        "reason": reason,
        "context_commit": context_commit,
        "head": head,
        "commits_behind": commits_behind,
    }

    write_artifact(
        out_dir=args.out_dir,
        name="freshness",
        schema_type="freshness",
        payload=freshness_payload,
        step=args.ctx.get("step", "ctx_fresh"),
        block="context-fresh",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
