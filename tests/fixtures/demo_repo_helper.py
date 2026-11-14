"""Helper utilities to stage demo_repo as an initialized git repository."""

import os
import shutil
import subprocess
from pathlib import Path

DEMO_REPO_DIR = Path(__file__).resolve().parent / "demo_repo"


def setup_demo_repo_git(destination: Path) -> Path:
    """Copy demo_repo to destination and initialize a clean git repository.

    Args:
        destination: Path where demo_repo will be staged.

    Returns:
        Path to the initialized git repo root.
    """
    if destination.exists():
        shutil.rmtree(destination)

    shutil.copytree(
        DEMO_REPO_DIR,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "agyflow-bot"
    env["GIT_AUTHOR_EMAIL"] = "bot@agyflow.local"
    env["GIT_COMMITTER_NAME"] = "agyflow-bot"
    env["GIT_COMMITTER_EMAIL"] = "bot@agyflow.local"

    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=destination,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "agyflow-bot"],
        cwd=destination,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "config", "user.email", "bot@agyflow.local"],
        cwd=destination,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "add", "."],
        cwd=destination,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    subprocess.run(
        ["git", "commit", "-m", "init: demo_repo baseline"],
        cwd=destination,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    return destination
