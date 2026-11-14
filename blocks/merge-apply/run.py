"""Merge apply block: commits candidate patch to dedicated agyflow branch via temporary worktree."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sys

from agyflow.lib.contract import (
    E_BASE_MOVED,
    E_NEEDS_HUMAN,
    E_POLICY,
    EXIT_FAILED,
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)
from agyflow.lib.proc import run_command
from agyflow.lib.schema import content_hash


def main() -> int:
    args = parse_cli_args()
    approval = load_input_payload(args.in_dir, "approval")
    patch_meta = load_input_payload(args.in_dir, "patch_meta")
    snapshot = load_input_payload(args.in_dir, "snapshot")

    # 1. Approval decision check
    if approval.get("decision") != "approve":
        write_status(args.out_dir, "failed", E_POLICY, "Approval decision is not 'approve'", complete=False)
        return EXIT_FAILED

    # 2. Subject hash verification
    subj_file = args.in_dir / "subject.json"
    if subj_file.is_file():
        _, subj_data = read_artifact(subj_file)
        if content_hash(subj_data) != approval.get("subject_hash"):
            write_status(args.out_dir, "needs_human", E_NEEDS_HUMAN, "Subject content hash mismatch against approval", complete=False)
            return EXIT_NEEDS_HUMAN

    # 3. Patch SHA check
    patch_file = args.in_dir / "patch.patch"
    if not patch_file.is_file():
        for cand in args.in_dir.glob("*.patch"):
            patch_file = cand
            break
    patch_bytes = patch_file.read_bytes() if patch_file.is_file() else b""
    actual_sha = hashlib.sha256(patch_bytes).hexdigest()
    if actual_sha != patch_meta.get("patch_sha256"):
        write_status(args.out_dir, "failed", E_POLICY, "Patch SHA mismatch against patch_meta", complete=False)
        return EXIT_FAILED

    # 4. Base commit check
    repo_path = Path(args.ctx.get("repo", ".")).resolve()
    head_res = run_command(["git", "rev-parse", "HEAD"], cwd=repo_path)
    if head_res.stdout.strip() != snapshot.get("head"):
        write_status(args.out_dir, "needs_human", E_BASE_MOVED, "Main repository HEAD has moved; rebase required", complete=False)
        return EXIT_NEEDS_HUMAN

    run_id = str(args.ctx.get("run_id", "run"))
    branch_name = f"agyflow/{run_id}"

    # Check if branch exists
    b_res = run_command(["git", "rev-parse", "--verify", branch_name], cwd=repo_path)
    if b_res.returncode == 0:
        write_status(args.out_dir, "needs_human", E_NEEDS_HUMAN, f"Branch '{branch_name}' already exists", complete=False)
        return EXIT_NEEDS_HUMAN

    # Create temporary isolated worktree
    temp_wt = (args.out_dir / "_temp_merge_wt").resolve()
    temp_wt.parent.mkdir(parents=True, exist_ok=True)
    if temp_wt.exists():
        shutil.rmtree(temp_wt, ignore_errors=True)

    wt_add_res = run_command(
        ["git", "worktree", "add", "-b", branch_name, str(temp_wt), snapshot["head"]],
        cwd=repo_path,
    )
    if wt_add_res.returncode != 0:
        write_status(args.out_dir, "failed", E_POLICY, f"Failed to create worktree branch: {wt_add_res.stderr}", complete=False)
        return EXIT_FAILED

    try:
        # Apply patch in temp worktree
        apply_res = run_command(["git", "apply", "--index", str(patch_file.resolve())], cwd=temp_wt)
        if apply_res.returncode != 0:
            write_status(args.out_dir, "failed", E_POLICY, f"Failed to apply patch to branch: {apply_res.stderr}", complete=False)
            return EXIT_FAILED

        # Commit changes
        commit_msg = f"agyflow: {approval.get('note') or 'apply candidate patch'} (run {run_id})"
        commit_res = run_command(["git", "commit", "-m", commit_msg], cwd=temp_wt)
        if commit_res.returncode != 0:
            write_status(args.out_dir, "failed", E_POLICY, f"Git commit failed: {commit_res.stderr}", complete=False)
            return EXIT_FAILED

        commit_hash_res = run_command(["git", "rev-parse", "HEAD"], cwd=temp_wt)
        commit_hash = commit_hash_res.stdout.strip()
    finally:
        # Clean up temporary worktree
        run_command(["git", "worktree", "remove", "--force", str(temp_wt)], cwd=repo_path)
        run_command(["git", "worktree", "prune"], cwd=repo_path)
        if temp_wt.exists():
            shutil.rmtree(temp_wt, ignore_errors=True)

    payload = {
        "branch": branch_name,
        "commit": commit_hash,
        "base_commit": str(snapshot["head"]),
        "patch_sha256": actual_sha,
    }

    step = args.ctx.get("step", "merge")
    write_artifact(args.out_dir, "merge_result", "merge_result", payload, step=step, block="merge-apply")
    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
