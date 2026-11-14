"""Retry decisions, backoff computation, side-effect safety rules, and budget tracking."""

from __future__ import annotations

from typing import Any

from agyflow.engine.registry import BlockManifest

SIDE_EFFECT_ORDER = {
    "none": 0,
    "read_only": 1,
    "temp": 2,
    "worktree": 3,
    "repo": 4,
    "network": 5,
}

RETRIABLE_CODES = {
    "E_TIMEOUT",
    "E_RATE_LIMIT",
    "E_PROVIDER",
    "E_SCHEMA",
    "E_EMPTY",
    "E_TOOL_CAP",
}

NON_RETRIABLE_CODES = {
    "E_INPUT",
    "E_POLICY",
    "E_CLI",
    "E_AUTH",
    "E_CONTRACT",
    "E_INTERNAL",
    "E_NEEDS_HUMAN",
}


class BudgetExceededError(Exception):
    """Raised when active execution time or agent call limits are exceeded."""


def effective_level(step_level: str | None, manifest_level: str) -> str:
    """Effective side-effect is max(step_level, manifest_level); step may only raise."""
    lvl_step = SIDE_EFFECT_ORDER.get(step_level or "none", 0)
    lvl_mf = SIDE_EFFECT_ORDER.get(manifest_level, 0)
    max_val = max(lvl_step, lvl_mf)
    for k, v in SIDE_EFFECT_ORDER.items():
        if v == max_val:
            return k
    return manifest_level


def rerun_allowed(effective_lvl: str, manifest: BlockManifest) -> bool:
    """Whether an uncommitted step interrupted by crash may automatically re-run."""
    if effective_lvl in ("worktree", "repo"):
        return manifest.retry_safe
    return True


def retry_safe(effective_lvl: str, manifest: BlockManifest) -> bool:
    """Check if retry after failure is allowed given side-effect level."""
    if effective_lvl in ("worktree", "repo"):
        return manifest.retry_safe
    return True


def is_retriable_code(code: str) -> bool:
    """Check if error code is inherently retriable."""
    return code in RETRIABLE_CODES


def max_attempts(step_retry_cfg: dict[str, Any] | None) -> int:
    """Total attempts allowed (1 initial + retry.max). Default: 1 + 2 = 3."""
    if not step_retry_cfg:
        return 3
    return 1 + int(step_retry_cfg.get("max", 2))


def calculate_backoff(step_retry_cfg: dict[str, Any] | None, attempt: int, code: str) -> tuple[float, dict[str, Any]]:
    """Compute backoff sleep seconds and _retry.json payload. Pure function, never sleeps."""
    retry_info: dict[str, Any] = {"attempt": attempt, "code": code}
    if code == "E_TIMEOUT":
        retry_info["shrink"] = 0.5
        return 5.0, retry_info

    if code in ("E_SCHEMA", "E_EMPTY"):
        return 2.0, retry_info

    # Schedule-based backoff
    schedule = [30.0, 120.0, 600.0]
    if step_retry_cfg and "backoff_s" in step_retry_cfg:
        schedule = [float(x) for x in step_retry_cfg["backoff_s"]]

    idx = max(0, attempt - 1)
    delay = schedule[idx] if idx < len(schedule) else schedule[-1]
    return delay, retry_info


def check_budget(state_counters: dict[str, int], budget_cfg: dict[str, Any] | None) -> None:
    """Check active minutes and agent calls against project budget."""
    if not budget_cfg:
        return
    max_minutes = budget_cfg.get("minutes")
    if max_minutes is not None:
        active_mins = state_counters.get("active_seconds", 0) / 60.0
        if active_mins >= float(max_minutes):
            raise BudgetExceededError(f"Budget active minutes exceeded: {active_mins:.1f} >= {max_minutes}")

    max_calls = budget_cfg.get("agent_calls")
    if max_calls is not None:
        calls = state_counters.get("agent_calls", 0)
        if calls >= int(max_calls):
            raise BudgetExceededError(f"Budget agent calls exceeded: {calls} >= {max_calls}")
