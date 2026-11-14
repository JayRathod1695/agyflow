"""Unit tests for src/agyflow/lib/proc.py."""

from pathlib import Path
import pytest
from agyflow.lib.proc import run_command, scrub_env


def test_scrub_env(tmp_path: Path) -> None:
    env = scrub_env(env_allow=["CUSTOM_VAR"], needs_home=False, home_dir=tmp_path / "home")
    assert "PATH" in env
    assert env["HOME"] == str((tmp_path / "home").resolve())


def test_run_command_success() -> None:
    res = run_command(["echo", "hello world"])
    assert res.returncode == 0
    assert "hello world" in res.stdout
    assert not res.timed_out
    assert not res.truncated


def test_run_command_timeout() -> None:
    res = run_command(["python3.11", "-c", "import time; time.sleep(5)"], timeout_s=0.5, grace_period_s=0.2)
    assert res.timed_out is True
