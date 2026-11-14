"""Data models and constants for agyflow doctor probes."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

CAPABILITY_PROBE_KEYS = [
    "print_json_envelope",
    "json_schema_enforced",
    "exit_code_on_schema_violation",
    "timeout_kills_children",
    "plan_mode_blocks_writes",
    "plan_mode_blocks_exec",
    "sandbox_blocks_outside_reads",
    "add_dir_is_additive",
    "skip_permissions_needed_for_edit",
    "parallel_safe",
    "stream_json_tool_events",
    "web_tool_available",
    "conversation_isolation",
]


class ProbeStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    UNTESTED = "untested"


@dataclass
class ProbeResult:
    key: str
    name: str
    status: ProbeStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
        }
