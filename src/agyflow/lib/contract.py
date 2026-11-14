"""Standard CLI contract for agyflow blocks (--in, --out, --cfg, _status.json)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
import yaml

from agyflow.lib.schema import canonical_json, content_hash, validate_or_raise

# Exit Codes (Section 4.3)
EXIT_OK = 0
EXIT_RETRYABLE = 10
EXIT_FAILED = 20
EXIT_NEEDS_HUMAN = 30
EXIT_TIMEOUT = 124
EXIT_CONTRACT_VIOLATION = 125

# Reason Codes (Section 4.5)
E_TIMEOUT = "E_TIMEOUT"
E_RATE_LIMIT = "E_RATE_LIMIT"
E_PROVIDER = "E_PROVIDER"
E_SCHEMA = "E_SCHEMA"
E_EMPTY = "E_EMPTY"
E_TOOL_CAP = "E_TOOL_CAP"
E_INPUT = "E_INPUT"
E_POLICY = "E_POLICY"
E_CLI = "E_CLI"
E_AUTH = "E_AUTH"
E_BASELINE = "E_BASELINE"
E_BASE_MOVED = "E_BASE_MOVED"
E_NEEDS_HUMAN = "E_NEEDS_HUMAN"
E_CONTRACT = "E_CONTRACT"
E_INTERNAL = "E_INTERNAL"


@dataclass(frozen=True)
class ContractArgs:
    """Parsed Unix contract CLI arguments."""

    in_dir: Path
    out_dir: Path
    cfg: dict[str, Any]
    ctx: dict[str, Any] = field(default_factory=dict)


def parse_cli_args(args: list[str] | None = None) -> ContractArgs:
    """Parse --in <dir> --out <dir> --cfg <file_or_json>."""
    parser = argparse.ArgumentParser(description="agyflow block runner")
    parser.add_argument("--in", dest="in_dir", required=True, type=Path, help="Input directory")
    parser.add_argument("--out", dest="out_dir", required=True, type=Path, help="Output directory")
    parser.add_argument("--cfg", dest="cfg", required=True, help="Path to config file or JSON string")

    parsed = parser.parse_args(args)
    in_dir = parsed.in_dir.resolve()
    out_dir = parsed.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg_val = parsed.cfg.strip()
    if os.path.exists(cfg_val):
        with open(cfg_val, "r", encoding="utf-8") as f:
            cfg_data = yaml.safe_load(f) or {}
    else:
        cfg_data = json.loads(cfg_val)

    if not isinstance(cfg_data, dict):
        raise ValueError(f"Config must be a JSON object, got: {type(cfg_data).__name__}")

    ctx = cfg_data.get("_ctx", {})
    return ContractArgs(in_dir=in_dir, out_dir=out_dir, cfg=cfg_data, ctx=ctx)


def atomic_write_json(file_path: Path, data: Any) -> None:
    """Write data to JSON file atomically via fsync and os.replace."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.parent / f".tmp_{file_path.name}_{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(canonical_json(data))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, file_path)


def write_artifact(
    out_dir: Path | str,
    name: str,
    schema_type: str,
    payload: dict[str, Any],
    step: str = "",
    block: str = "",
    block_version: str = "1",
    profile: str = "",
    prompt_version: str = "",
    inputs_hash: str = "",
    validate: bool = True,
) -> Path:
    """Wrap payload in envelope, validate, and write atomically to out_dir/<name>.json."""
    if validate:
        validate_or_raise(payload, schema_type)

    envelope = {
        "schema": schema_type,
        "schema_version": 1,
        "produced_by": {
            "step": step,
            "block": block,
            "block_version": str(block_version),
            "profile": profile,
            "prompt_version": prompt_version,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs_hash": inputs_hash,
        "payload": payload,
    }

    if validate:
        validate_or_raise(envelope, "envelope")

    filename = name if name.endswith(".json") else f"{name}.json"
    target = Path(out_dir) / filename
    atomic_write_json(target, envelope)
    return target


def read_artifact(file_path: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read an artifact file and return (envelope, payload)."""
    p = Path(file_path)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "payload" in data and "schema" in data:
        return data, data["payload"]
    return {}, data


def load_input_payload(in_dir: Path | str, name: str) -> dict[str, Any]:
    """Load the payload dictionary of an input artifact in in_dir."""
    p = Path(in_dir) / (name if name.endswith(".json") else f"{name}.json")
    if not p.is_file():
        raise FileNotFoundError(f"Required input artifact '{name}' not found at {p}")
    _, payload = read_artifact(p)
    return payload


def write_status(
    out_dir: Path | str,
    status: str,
    code: str = "",
    reason: str = "",
    complete: bool = True,
    metrics: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> Path:
    """Write _status.json atomically. complete=True is written last for atomic adoption."""
    if status not in ("ok", "retryable", "failed", "needs_human"):
        raise ValueError(f"Invalid status '{status}', must be ok|retryable|failed|needs_human")

    status_data = {
        "status": status,
        "code": code,
        "reason": reason,
        "complete": complete,
        "metrics": metrics or {
            "duration_s": 0.0,
            "agent_calls": 0,
            "tokens_in": None,
            "tokens_out": None,
            "tool_calls": None,
        },
        "warnings": warnings or [],
    }
    target = Path(out_dir) / "_status.json"
    atomic_write_json(target, status_data)
    return target
