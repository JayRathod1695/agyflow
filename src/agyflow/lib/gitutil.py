"""Git utilities for worktree lifecycle, status inspection, diff generation, and patching."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess
from typing import Any

from agyflow.lib.proc import run_command


class GitError(Exception):
    """Raised when a git command fails."""


def _run_git(args: list[str], cwd: Path | str) -> str:
    """Run git command and return stdout. Raise GitError on failure."""
    cmd = ["git"] + args
    res = run_command(cmd, cwd=cwd, timeout_s=30.0)
    if res.returncode != 0:
        raise GitError(f"Git command failed ({' '.join(cmd)}): {res.stderr.strip()}")
    return res.stdout.strip()


def get_repo_status(repo_path: Path | str) -> dict[str, Any]:
    """Inspect git repository state at repo_path."""
    cwd = Path(repo_path).resolve()
    head = _run_git(["rev-parse", "HEAD"], cwd=cwd)
    branch_out = _run_git(["branch", "--show-current"], cwd=cwd)
    branch = branch_out if branch_out else None

    porcelain = _run_git(["status", "--porcelain=v1"], cwd=cwd)
    is_dirty = bool(porcelain.strip())

    base_tree = _run_git(["rev-parse", "HEAD^{tree}"], cwd=cwd)
    ls_files = _run_git(["ls-files"], cwd=cwd)
    tracked_count = len(ls_files.splitlines()) if ls_files else 0

    return {
        "repo_path": str(cwd),
        "head": head,
        "branch": branch,
        "dirty": is_dirty,
        "tracked_files": tracked_count,
        "base_tree": base_tree,
    }


def compute_dirty_diff_sha256(repo_path: Path | str) -> str | None:
    """Compute sha256 of git diff HEAD --binary plus sorted untracked non-ignored files."""
    cwd = Path(repo_path).resolve()
    diff_out = _run_git(["diff", "HEAD", "--binary"], cwd=cwd)
    status_out = _run_git(["status", "--porcelain=v1"], cwd=cwd)
    if not diff_out and not status_out:
        return None

    h = hashlib.sha256()
    h.update(diff_out.encode("utf-8"))

    # Untracked files
    untracked_lines = [line[3:].strip() for line in status_out.splitlines() if line.startswith("??")]
    for rel_name in sorted(untracked_lines):
        fpath = cwd / rel_name
        if fpath.is_file():
            h.update(rel_name.encode("utf-8"))
            with open(fpath, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def make_readonly(target_dir: Path | str) -> None:
    """Recursively remove write permissions from directory contents, preserving traversal."""
    root = Path(target_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in Path(dirpath).parts:
            continue
        for d in dirnames:
            if d != ".git":
                p = Path(dirpath) / d
                cur_mode = p.stat().st_mode
                p.chmod(cur_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH | stat.S_IXUSR)
        for f in filenames:
            p = Path(dirpath) / f
            cur_mode = p.stat().st_mode
            p.chmod(cur_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)


def make_writable(target_dir: Path | str) -> None:
    """Recursively restore write permissions to directory contents."""
    root = Path(target_dir)
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            p = Path(dirpath) / d
            try:
                p.chmod(p.stat().st_mode | stat.S_IRWXU)
            except OSError:
                pass
        for f in filenames:
            p = Path(dirpath) / f
            try:
                p.chmod(p.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
    try:
        root.chmod(root.stat().st_mode | stat.S_IRWXU)
    except OSError:
        pass


def create_worktree(
    repo_path: Path | str,
    wt_path: Path | str,
    commit_or_branch: str,
    readonly: bool = False,
) -> None:
    """Create detached git worktree and apply readonly mode if requested."""
    cwd = Path(repo_path).resolve()
    wt = Path(wt_path).resolve()

    if wt.exists():
        remove_worktree(cwd, wt)

    wt.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["worktree", "add", "--detach", str(wt), commit_or_branch], cwd=cwd)

    marker_written = False
    git_target = wt / ".git"
    if git_target.is_file():
        gitdir_line = git_target.read_text(encoding="utf-8").strip()
        if gitdir_line.startswith("gitdir:"):
            gitdir_path = Path(gitdir_line.split("gitdir:", 1)[1].strip())
            if gitdir_path.exists():
                (gitdir_path / "agyflow_wt").write_text("agyflow\n", encoding="utf-8")
                marker_written = True
    elif git_target.is_dir():
        (git_target / "agyflow_wt").write_text("agyflow\n", encoding="utf-8")
        marker_written = True

    if readonly:
        make_readonly(wt)


def remove_worktree(repo_path: Path | str, wt_path: Path | str) -> None:
    """Safely remove a git worktree, restoring write permissions first."""
    cwd = Path(repo_path).resolve()
    wt = Path(wt_path).resolve()
    if not wt.exists():
        _run_git(["worktree", "prune"], cwd=cwd)
        return

    make_writable(wt)
    try:
        _run_git(["worktree", "remove", "--force", str(wt)], cwd=cwd)
    except GitError:
        pass
    _run_git(["worktree", "prune"], cwd=cwd)

    if wt.exists():
        import shutil
        shutil.rmtree(wt, ignore_errors=True)


def get_diff(repo_path: Path | str, base_ref: str = "HEAD", cached: bool = False) -> str:
    """Get git diff output against base_ref."""
    args = ["diff"]
    if cached:
        args.append("--cached")
    args.extend(["--binary", "--full-index", base_ref])
    return _run_git(args, cwd=repo_path)


def apply_patch(repo_path: Path | str, patch_file: Path | str, index: bool = False) -> None:
    """Apply unified patch file to repository."""
    args = ["apply"]
    if index:
        args.append("--index")
    args.append(str(Path(patch_file).resolve()))
    _run_git(args, cwd=repo_path)
