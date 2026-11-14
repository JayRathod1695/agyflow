"""State tracking, atomic state.json persistence, events.jsonl append, and crash repair."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from agyflow.lib.contract import atomic_write_json


@dataclass
class StepRecord:
    """Record of a single step execution within a run."""

    status: str  # started | committed | failed | skipped
    attempt: int = 1
    inputs_hash: str = ""
    code: str | None = None
    reason: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at: str | None = None


@dataclass
class RunState:
    """Complete runtime state of an agyflow pipeline execution."""

    run_id: str
    pipeline: str
    schema_version: int = 1
    status: str = "running"  # running, paused_gate, needs_human, failed, stopped_budget, aborted, done
    steps: dict[str, StepRecord] = field(default_factory=dict)
    counters: dict[str, int] = field(
        default_factory=lambda: {"agent_calls": 0, "active_seconds": 0, "retries": 0}
    )
    pending: dict[str, Any] = field(default_factory=lambda: {"gate": None, "subject_hash": None})
    error: dict[str, Any] | None = None

    def mark_started(self, xid: str, attempt: int, ih: str) -> None:
        rec = self.steps.get(xid)
        if rec:
            rec.status = "started"
            rec.attempt = attempt
            rec.inputs_hash = ih
        else:
            self.steps[xid] = StepRecord(status="started", attempt=attempt, inputs_hash=ih)

    def mark_committed(self, xid: str, ih: str) -> None:
        rec = self.steps.get(xid)
        now_iso = datetime.now(timezone.utc).isoformat()
        if rec:
            rec.status = "committed"
            rec.inputs_hash = ih
            rec.ended_at = now_iso
        else:
            self.steps[xid] = StepRecord(status="committed", inputs_hash=ih, ended_at=now_iso)

    def mark_failed(self, xid: str, code: str = "", reason: str = "") -> None:
        rec = self.steps.get(xid)
        now_iso = datetime.now(timezone.utc).isoformat()
        if rec:
            rec.status = "failed"
            rec.code = code
            rec.reason = reason
            rec.ended_at = now_iso
        else:
            self.steps[xid] = StepRecord(status="failed", code=code, reason=reason, ended_at=now_iso)

    def mark_skipped(self, xid: str) -> None:
        self.steps[xid] = StepRecord(status="skipped", ended_at=datetime.now(timezone.utc).isoformat())

    def add_metrics(self, metrics: dict[str, Any] | None) -> None:
        if not metrics:
            return
        self.counters["agent_calls"] += int(metrics.get("agent_calls", 0))
        self.counters["active_seconds"] += int(metrics.get("duration_s", 0))


def save_state(run_dir: Path | str, state: RunState) -> None:
    """Save state to state.json atomically."""
    target = Path(run_dir) / "state.json"
    data = asdict(state)
    atomic_write_json(target, data)


def load_state(run_dir: Path | str) -> RunState:
    """Load state from state.json, or raise FileNotFoundError."""
    target = Path(run_dir) / "state.json"
    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)

    steps = {
        k: StepRecord(
            status=v.get("status", "started"),
            attempt=v.get("attempt", 1),
            inputs_hash=v.get("inputs_hash", ""),
            code=v.get("code"),
            reason=v.get("reason"),
            started_at=v.get("started_at", ""),
            ended_at=v.get("ended_at"),
        )
        for k, v in data.get("steps", {}).items()
    }
    return RunState(
        run_id=data.get("run_id", ""),
        pipeline=data.get("pipeline", ""),
        schema_version=data.get("schema_version", 1),
        status=data.get("status", "running"),
        steps=steps,
        counters=data.get("counters", {"agent_calls": 0, "active_seconds": 0, "retries": 0}),
        pending=data.get("pending", {"gate": None, "subject_hash": None}),
        error=data.get("error"),
    )


def append_event(run_dir: Path | str, event: str, step: str = "", attempt: int = 0, **extra: Any) -> None:
    """Append event to events.jsonl with immediate fsync."""
    events_file = Path(run_dir) / "events.jsonl"
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ev": event,
        "step": step,
        "attempt": attempt,
        **extra,
    }
    line = json.dumps(record, separators=(",", ":")) + "\n"
    with open(events_file, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def repair_state(run_dir: Path | str) -> RunState:
    """Repair corrupted or missing state.json from steps/ and events.jsonl."""
    p = Path(run_dir)
    run_id = p.name
    pipeline_name = "unknown"
    run_toml = p / "run.toml"
    if run_toml.is_file():
        for line in run_toml.read_text(encoding="utf-8").splitlines():
            if line.startswith("pipeline ="):
                pipeline_name = line.split("=", 1)[1].strip().strip('"\'')

    st = RunState(run_id=run_id, pipeline=pipeline_name)
    steps_dir = p / "steps"
    if steps_dir.is_dir():
        for sdir in steps_dir.iterdir():
            if not sdir.is_dir():
                continue
            xid = sdir.name
            out_status = sdir / "out" / "_status.json"
            if out_status.is_file():
                try:
                    s_data = json.loads(out_status.read_text(encoding="utf-8"))
                    if s_data.get("complete") is True:
                        st.steps[xid] = StepRecord(status="committed")
                        continue
                except Exception:
                    pass
            st.steps[xid] = StepRecord(status="failed", reason="repaired_incomplete")

    events_file = p / "events.jsonl"
    if events_file.is_file():
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    ev_type = ev.get("ev")
                    step_id = ev.get("step")
                    if ev_type == "started" and step_id and step_id not in st.steps:
                        st.steps[step_id] = StepRecord(status="started", attempt=ev.get("attempt", 1))
                    elif ev_type == "committed" and step_id:
                        st.steps[step_id] = StepRecord(status="committed", attempt=ev.get("attempt", 1))
                except Exception:
                    continue
    save_state(p, st)
    return st
