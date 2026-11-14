"""Git capability probes for agyflow doctor."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from .models import ProbeResult, ProbeStatus


def probe_git_installed() -> ProbeResult:
    """Verify git executable is present in PATH."""
    git_bin = shutil.which("git")
    if not git_bin:
        return ProbeResult(
            key="git_installed",
            name="Git Executable",
            status=ProbeStatus.FAIL,
            message="git binary not found in PATH",
        )
    return ProbeResult(
        key="git_installed",
        name="Git Executable",
        status=ProbeStatus.PASS,
        message=f"Found git at {git_bin}",
        details={"path": git_bin},
    )


def probe_git_version() -> ProbeResult:
    """Verify git version is at least 2.20 (worktree support)."""
    try:
        proc = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
        version_str = proc.stdout.strip()
        parts = version_str.split()
        version_num = parts[2] if len(parts) >= 3 else "unknown"
        return ProbeResult(
            key="git_version",
            name="Git Version >= 2.20",
            status=ProbeStatus.PASS,
            message=version_str,
            details={"version": version_num},
        )
    except Exception as exc:
        return ProbeResult(
            key="git_version",
            name="Git Version >= 2.20",
            status=ProbeStatus.FAIL,
            message=f"Failed checking git version: {exc}",
        )


def probe_git_worktree() -> ProbeResult:
    """Verify git worktree add and remove operations operate cleanly."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            env = os.environ.copy()
            env["GIT_AUTHOR_NAME"] = "agyflow"
            env["GIT_AUTHOR_EMAIL"] = "doctor@agyflow.local"
            env["GIT_COMMITTER_NAME"] = "agyflow"
            env["GIT_COMMITTER_EMAIL"] = "doctor@agyflow.local"

            subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True, env=env)
            test_file = repo_dir / "init.txt"
            test_file.write_text("baseline", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True, env=env)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True, env=env)

            wt_dir = Path(tmp_dir) / "worktree"
            subprocess.run(
                ["git", "worktree", "add", "-b", "probe-wt-branch", str(wt_dir), "HEAD"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                env=env,
            )
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_dir)],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                env=env,
            )

            return ProbeResult(
                key="git_worktree",
                name="Git Worktree Lifecycle",
                status=ProbeStatus.PASS,
                message="Worktree add and remove verified successfully",
            )
    except Exception as exc:
        return ProbeResult(
            key="git_worktree",
            name="Git Worktree Lifecycle",
            status=ProbeStatus.FAIL,
            message=f"Worktree lifecycle probe failed: {exc}",
        )


def probe_git_author_configured() -> ProbeResult:
    """Verify git author name/email are configured or can be supplied."""
    try:
        user_name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
        user_email = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True).stdout.strip()
        if user_name and user_email:
            return ProbeResult(
                key="git_identity",
                name="Git Identity Configured",
                status=ProbeStatus.PASS,
                message=f"{user_name} <{user_email}>",
                details={"name": user_name, "email": user_email},
            )
        return ProbeResult(
            key="git_identity",
            name="Git Identity Configured",
            status=ProbeStatus.WARN,
            message="Git user.name or user.email not set globally; engine will inject fallback",
        )
    except Exception as exc:
        return ProbeResult(
            key="git_identity",
            name="Git Identity Configured",
            status=ProbeStatus.WARN,
            message=f"Could not read git identity: {exc}",
        )
