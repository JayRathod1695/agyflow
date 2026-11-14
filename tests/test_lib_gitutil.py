"""Unit tests for src/agyflow/lib/gitutil.py."""

from pathlib import Path
import pytest
from agyflow.lib.gitutil import (
    create_worktree,
    get_diff,
    get_repo_status,
    make_readonly,
    make_writable,
    remove_worktree,
)
from agyflow.lib.proc import run_command


def test_git_status_and_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_command(["git", "init"], cwd=repo)
    run_command(["git", "config", "user.name", "Test"], cwd=repo)
    run_command(["git", "config", "user.email", "test@example.com"], cwd=repo)

    f = repo / "hello.txt"
    f.write_text("initial\n", encoding="utf-8")
    run_command(["git", "add", "hello.txt"], cwd=repo)
    run_command(["git", "commit", "-m", "init"], cwd=repo)

    st = get_repo_status(repo)
    assert len(st["head"]) >= 7
    assert st["dirty"] is False
    assert st["tracked_files"] == 1

    wt = tmp_path / "wt1"
    create_worktree(repo, wt, st["head"], readonly=True)
    assert wt.exists()
    assert (wt / "hello.txt").exists()

    # Attempt write should fail in readonly mode
    with pytest.raises(PermissionError):
        (wt / "hello.txt").write_text("mod", encoding="utf-8")

    remove_worktree(repo, wt)
    assert not wt.exists()
