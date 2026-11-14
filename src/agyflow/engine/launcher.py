"""Process launcher interface and SubprocessLauncher implementation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agyflow.lib.proc import run_command


@dataclass(frozen=True)
class LaunchResult:
    """Raw result of a block execution without status interpretation."""

    exit_code: int
    duration_s: float
    timed_out: bool
    stdout: str
    stderr: str


class Launcher(Protocol):
    """Protocol for executing block commands."""

    def run(
        self,
        cmd: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
        stdout_log: Path | str | None = None,
        stderr_log: Path | str | None = None,
        timeout_s: float = 900.0,
    ) -> LaunchResult:
        ...


class SubprocessLauncher:
    """Subprocess launcher running commands via process groups with log capture."""

    def run(
        self,
        cmd: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
        stdout_log: Path | str | None = None,
        stderr_log: Path | str | None = None,
        timeout_s: float = 900.0,
    ) -> LaunchResult:
        res = run_command(
            cmd=cmd,
            cwd=cwd,
            timeout_s=timeout_s,
            env=env,
        )

        stdout_text = res.stdout or ""
        stderr_text = res.stderr or ""

        if stdout_log:
            p_out = Path(stdout_log)
            p_out.parent.mkdir(parents=True, exist_ok=True)
            p_out.write_text(stdout_text, encoding="utf-8")

        if stderr_log:
            p_err = Path(stderr_log)
            p_err.parent.mkdir(parents=True, exist_ok=True)
            p_err.write_text(stderr_text, encoding="utf-8")

        code = res.returncode if res.returncode is not None else (124 if res.timed_out else 1)

        return LaunchResult(
            exit_code=code,
            duration_s=res.duration_s,
            timed_out=res.timed_out,
            stdout=stdout_text,
            stderr=stderr_text,
        )

    @property
    def capabilities(self) -> dict[str, bool]:
        return {"stream_guard": True, "subprocess": True}

    def has_capability(self, cap: str) -> bool:
        return self.capabilities.get(cap, False)


def get_launcher(name: str = "subprocess") -> Launcher:
    """Factory to retrieve a configured Launcher."""
    if name == "subprocess":
        return SubprocessLauncher()
    if name == "herdr":
        raise NotImplementedError("HerdrLauncher is scheduled for M6")
    raise ValueError(f"Unknown launcher type: '{name}'")
