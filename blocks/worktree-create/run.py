"""Worktree creation block: provisions isolated git worktree with optional readonly mode."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys

from agyflow.lib.contract import (
    E_BASE_MOVED,
    E_INPUT,
    E_INTERNAL,
    E_POLICY,
    EXIT_FAILED,
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)
from agyflow.lib.gitutil import (
    GitError,
    compute_dirty_diff_sha256,
    create_worktree,
    make_readonly,
    remove_worktree,
)
from agyflow.lib.proc import run_command


def main() -> int:
    args = parse_cli_args()
    snapshot = load_input_payload(args.in_dir, "snapshot")
    head = snapshot["head"]
    repo_path = Path(args.ctx.get("repo", ".")).resolve()

    flow_raw = args.ctx.get("flow_dir")
    flow_dir = Path(flow_raw).resolve() if flow_raw else repo_path / ".flow"
    worktrees_root = (flow_dir / "worktrees").resolve()

    run_id = args.ctx.get("run_id", "default_run")
    wt_name = args.cfg.get("name", "wt")
    target_wt = (worktrees_root / run_id / wt_name).resolve()

    try:
        target_wt.relative_to(worktrees_root)
    except ValueError:
        write_status(
            args.out_dir,
            status="failed",
            code=E_POLICY,
            reason=f"Target worktree path {target_wt} is outside {worktrees_root}",
            complete=False,
        )
        return EXIT_FAILED

    can_reuse = False
    if target_wt.exists():
        rev_res = run_command(["git", "rev-parse", "HEAD"], cwd=target_wt)
        st_res = run_command(["git", "status", "--porcelain=v1"], cwd=target_wt)
        if rev_res.returncode == 0 and rev_res.stdout.strip() == head and not st_res.stdout.strip():
            can_reuse = True
        else:
            remove_worktree(repo_path, target_wt)

    if not can_reuse:
        try:
            create_worktree(repo_path, target_wt, head, readonly=False)
        except GitError as err:
            write_status(
                args.out_dir,
                status="failed",
                code=E_INPUT,
                reason=f"Failed to create git worktree: {err}",
                complete=False,
            )
            return EXIT_FAILED
        except OSError as err:
            write_status(
                args.out_dir,
                status="failed",
                code=E_INTERNAL,
                reason=f"Filesystem error during worktree creation: {err}",
                complete=False,
            )
            return EXIT_FAILED

    apply_dirty = bool(args.cfg.get("apply_dirty", False))
    dirty_applied = False
    if apply_dirty and snapshot.get("dirty"):
        diff_res = run_command(["git", "diff", "HEAD", "--binary"], cwd=repo_path)
        if diff_res.stdout.strip():
            patch_tmp = target_wt / ".agyflow_dirty.patch"
            patch_tmp.write_text(diff_res.stdout, encoding="utf-8")
            apply_res = run_command(["git", "apply", "--binary", str(patch_tmp)], cwd=target_wt)
            patch_tmp.unlink(missing_ok=True)
            if apply_res.returncode != 0:
                write_status(
                    args.out_dir,
                    status="needs_human",
                    code=E_BASE_MOVED,
                    reason="Failed to apply dirty diff in worktree",
                    complete=False,
                )
                return EXIT_NEEDS_HUMAN

        st_res = run_command(["git", "status", "--porcelain=v1"], cwd=repo_path)
        for line in st_res.stdout.splitlines():
            if line.startswith("?? "):
                rel_path = line[3:].strip()
                src = repo_path / rel_path
                dst = target_wt / rel_path
                if src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        wt_dirty_sha = compute_dirty_diff_sha256(target_wt)
        if wt_dirty_sha != snapshot.get("dirty_diff_sha256"):
            write_status(
                args.out_dir,
                status="needs_human",
                code=E_BASE_MOVED,
                reason="Worktree dirty SHA mismatch against snapshot; base moved",
                complete=False,
            )
            return EXIT_NEEDS_HUMAN
        dirty_applied = True

    readonly = bool(args.cfg.get("readonly", False))
    if readonly:
        make_readonly(target_wt)

    payload = {
        "name": wt_name,
        "path": str(target_wt),
        "base_commit": head,
        "branch": snapshot.get("branch"),
        "readonly": readonly,
        "dirty_applied": dirty_applied,
    }

    write_artifact(
        out_dir=args.out_dir,
        name="wt",
        schema_type="wt",
        payload=payload,
        step=args.ctx.get("step", "ro_wt"),
        block="worktree-create",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
