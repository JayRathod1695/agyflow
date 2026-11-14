"""Process launcher interface, SubprocessLauncher, and HerdrLauncher."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil
import sys
import time
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


def _prepare_exec(cmd: list[str], env: dict[str, str] | None) -> tuple[list[str], dict[str, str]]:
    exec_cmd = list(cmd)
    if exec_cmd and exec_cmd[0] in ("python", "python3"): exec_cmd[0] = sys.executable
    run_env = dict(os.environ if env is None else env)
    if "PYTHONPATH" not in run_env:
        run_env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent.parent)
    return exec_cmd, run_env


class Launcher(Protocol):
    def run(self, cmd: list[str], cwd: Path | str, env: dict[str, str] | None = None,
            stdout_log: Path | str | None = None, stderr_log: Path | str | None = None, timeout_s: float = 900.0) -> LaunchResult: ...


class SubprocessLauncher:
    """Subprocess launcher running commands via process groups with log capture."""

    def run(self, cmd: list[str], cwd: Path | str, env: dict[str, str] | None = None,
            stdout_log: Path | str | None = None, stderr_log: Path | str | None = None, timeout_s: float = 900.0) -> LaunchResult:
        exec_cmd, run_env = _prepare_exec(cmd, env)
        res = run_command(cmd=exec_cmd, cwd=cwd, timeout_s=timeout_s, env=run_env)
        out_txt, err_txt = res.stdout or "", res.stderr or ""

        if stdout_log:
            p_out = Path(stdout_log); p_out.parent.mkdir(parents=True, exist_ok=True); p_out.write_text(out_txt, encoding="utf-8")
        if stderr_log:
            p_err = Path(stderr_log); p_err.parent.mkdir(parents=True, exist_ok=True); p_err.write_text(err_txt, encoding="utf-8")

        code = res.returncode if res.returncode is not None else (124 if res.timed_out else 1)
        return LaunchResult(code, res.duration_s, res.timed_out, out_txt, err_txt)

    @property
    def capabilities(self) -> dict[str, bool]: return {"stream_guard": True, "subprocess": True}
    def has_capability(self, cap: str) -> bool: return self.capabilities.get(cap, False)


class HerdrLauncher:
    """Herdr pane launcher with exitcode tracking and automatic subprocess fallback."""

    def __init__(self, fallback: SubprocessLauncher | None = None) -> None:
        self._fallback = fallback or SubprocessLauncher()

    def run(self, cmd: list[str], cwd: Path | str, env: dict[str, str] | None = None,
            stdout_log: Path | str | None = None, stderr_log: Path | str | None = None, timeout_s: float = 900.0) -> LaunchResult:
        herdr_bin = shutil.which("herdr")
        if not herdr_bin:
            sys.stderr.write("Warning: herdr binary not found in PATH; falling back to subprocess launcher\n")
            return self._fallback.run(cmd, cwd, env, stdout_log, stderr_log, timeout_s)

        exec_cmd, run_env = _prepare_exec(cmd, env)
        att_dir = Path(stdout_log).parent if stdout_log else Path(cwd); att_dir.mkdir(parents=True, exist_ok=True)
        exit_file = att_dir / "exitcode"
        if exit_file.exists(): exit_file.unlink()

        out_path = Path(stdout_log) if stdout_log else (att_dir / "stdout.log")
        err_path = Path(stderr_log) if stderr_log else (att_dir / "stderr.log")

        cmd_str = " ".join(shlex.quote(c) for c in exec_cmd)
        wrapped_sh = f"{cmd_str} > {shlex.quote(str(out_path))} 2> {shlex.quote(str(err_path))} ; echo $? > {shlex.quote(str(exit_file))}"

        t0 = time.time()
        pane_res = run_command([herdr_bin, "run", "--", "sh", "-c", wrapped_sh], cwd=cwd, env=run_env, timeout_s=timeout_s)

        deadline = time.time() + timeout_s; timed_out = False
        while not exit_file.is_file():
            if time.time() > deadline: timed_out = True; break
            time.sleep(0.1)

        dur = time.time() - t0
        exit_code = 124 if timed_out else (int(exit_file.read_text().strip()) if exit_file.is_file() else (pane_res.returncode or 1))
        out_txt = out_path.read_text(encoding="utf-8") if out_path.is_file() else pane_res.stdout
        err_txt = err_path.read_text(encoding="utf-8") if err_path.is_file() else pane_res.stderr
        return LaunchResult(exit_code, dur, timed_out, out_txt, err_txt)

    @property
    def capabilities(self) -> dict[str, bool]: return {"stream_guard": True, "herdr": bool(shutil.which("herdr"))}
    def has_capability(self, cap: str) -> bool: return self.capabilities.get(cap, False)


def get_launcher(name: str = "subprocess") -> Launcher:
    if name == "subprocess": return SubprocessLauncher()
    if name == "herdr": return HerdrLauncher()
    raise ValueError(f"Unknown launcher type: '{name}'")
