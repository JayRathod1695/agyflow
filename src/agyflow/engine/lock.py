"""Repository lock management with flock, PID validation, and stale lock takeover."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)


class LockError(Exception):
    """Raised when the repository lock cannot be acquired."""


def is_pid_alive(pid: int) -> bool:
    """Check if process with pid is running on this host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        return e.errno == errno.EPERM


class LockHandle:
    """Active lock handle holding the locked file descriptor."""

    def __init__(self, path: Path, fd: int, info: dict[str, Any]) -> None:
        self.path = path
        self.fd = fd
        self.info = info
        self._released = False

    def release(self) -> None:
        """Release the file lock and close the descriptor."""
        if self._released:
            return
        self._released = True
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> LockHandle:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


def _read_lock_info(path: Path) -> dict[str, Any]:
    """Read lock metadata if available."""
    try:
        content = path.read_text(encoding="utf-8").strip()
        if content.startswith("{"):
            return json.loads(content)
        lines = content.splitlines()
        return {"pid": int(lines[0]) if lines else 0, "run_id": lines[1] if len(lines) > 1 else ""}
    except Exception:
        return {}


def acquire(flow_dir: Path | str, run_id: str) -> LockHandle:
    """Acquire exclusive non-blocking lock on <flow_dir>/locks/repo.lock."""
    locks_dir = Path(flow_dir) / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_file = locks_dir / "repo.lock"

    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        info = _read_lock_info(lock_file)
        pid = info.get("pid", 0)
        holder_run = info.get("run_id", "unknown")

        if pid and not is_pid_alive(pid):
            logger.warning("Overriding stale lock from dead PID %d (run %s)", pid, holder_run)
            try:
                # Truncate and retry flock
                os.close(fd)
                fd = os.open(lock_file, os.O_RDWR | os.O_TRUNC, 0o644)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                os.close(fd)
                raise LockError(f"Stale lock race: locked by PID {pid} (run {holder_run})")
        else:
            os.close(fd)
            raise LockError(f"Repository is locked by PID {pid} (run {holder_run})")

    lock_data = {
        "pid": os.getpid(),
        "run_id": run_id,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = json.dumps(lock_data, separators=(",", ":"))
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload.encode("utf-8"))
    os.fsync(fd)

    return LockHandle(lock_file, fd, lock_data)


@contextmanager
def locked_repo(flow_dir: Path | str, run_id: str) -> Generator[LockHandle, None, None]:
    """Context manager wrapper for acquire."""
    handle = acquire(flow_dir, run_id)
    try:
        yield handle
    finally:
        handle.release()
