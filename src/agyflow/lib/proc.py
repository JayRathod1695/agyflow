"""Process execution with process group management, timeout escalation, output cap, and env scrubbing."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

# Base allowlist according to Section 4.8 of spec
BASE_ENV_ALLOW = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TERM",
    "AGYFLOW_RUN_ID",
    "AGYFLOW_STEP",
    "AGYFLOW_ATTEMPT",
)


@dataclass(frozen=True)
class ProcResult:
    """Result of subprocess execution."""

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    truncated: bool = False


def scrub_env(
    env_allow: list[str] | tuple[str, ...] | None = None,
    extra_env: dict[str, str] | None = None,
    needs_home: bool = False,
    home_dir: Path | str | None = None,
) -> dict[str, str]:
    """Rebuild clean environment from allowlist and scratch HOME."""
    allowed = set(BASE_ENV_ALLOW)
    if env_allow:
        allowed.update(env_allow)

    cleaned: dict[str, str] = {}
    for key in allowed:
        if key in os.environ:
            cleaned[key] = os.environ[key]

    if needs_home:
        if "HOME" in os.environ:
            cleaned["HOME"] = os.environ["HOME"]
    elif home_dir:
        p = Path(home_dir)
        p.mkdir(parents=True, exist_ok=True)
        cleaned["HOME"] = str(p.resolve())
    else:
        cleaned["HOME"] = "/tmp"

    if extra_env:
        cleaned.update(extra_env)

    return cleaned


def kill_process_group(pid: int, sig: signal.Signals = signal.SIGTERM) -> None:
    """Send signal to process group led by pid."""
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run_command(
    cmd: list[str],
    cwd: Path | str | None = None,
    timeout_s: float = 60.0,
    max_output_bytes: int = 1_000_000,
    env: dict[str, str] | None = None,
    env_allow: list[str] | None = None,
    needs_home: bool = False,
    home_dir: Path | str | None = None,
    grace_period_s: float = 2.0,
) -> ProcResult:
    """Run command with process group, timeout kill, output cap, and environment isolation."""
    if not cmd or not isinstance(cmd, list):
        raise ValueError(f"cmd must be a non-empty list of arguments, got: {cmd}")

    run_env = env if env is not None else scrub_env(
        env_allow=env_allow, needs_home=needs_home, home_dir=home_dir
    )
    work_dir = Path(cwd).resolve() if cwd else Path.cwd()

    start_time = time.monotonic()
    timed_out = False

    proc = subprocess.Popen(
        cmd,
        cwd=work_dir,
        env=run_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    stdout_bytes = b""
    stderr_bytes = b""

    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_group(proc.pid, signal.SIGTERM)
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=grace_period_s)
        except subprocess.TimeoutExpired:
            kill_process_group(proc.pid, signal.SIGKILL)
            stdout_bytes, stderr_bytes = proc.communicate()

    duration = time.monotonic() - start_time
    truncated = (len(stdout_bytes) > max_output_bytes) or (len(stderr_bytes) > max_output_bytes)

    out_str = stdout_bytes[:max_output_bytes].decode("utf-8", errors="replace")
    err_str = stderr_bytes[:max_output_bytes].decode("utf-8", errors="replace")

    return ProcResult(
        returncode=proc.returncode,
        stdout=out_str,
        stderr=err_str,
        timed_out=timed_out,
        duration_s=round(duration, 3),
        truncated=truncated,
    )
