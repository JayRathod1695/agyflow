"""Findings selection block: partitions verified items into selected, needs_review, and backlog."""

from __future__ import annotations

import sys
from typing import Any

from agyflow.lib.contract import (
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)

SEVERITY_MAP = {"HIGH": 3, "MED": 2, "LOW": 1}


def main() -> int:
    args = parse_cli_args()
    known = load_input_payload(args.in_dir, "known")

    accept_fps: set[str] = set()
    reject_fps: set[str] = set()
    app_file = args.in_dir / "approval.json"
    if app_file.is_file():
        try:
            _, app_payload = read_artifact(app_file)
            accept_fps = set(app_payload.get("accept", []))
            reject_fps = set(app_payload.get("reject", []))
        except Exception:
            pass

    min_sev = str(args.cfg.get("min_severity", "HIGH")).upper()
    min_rank = SEVERITY_MAP.get(min_sev, 3)
    accept_statuses = set(args.cfg.get("accept_statuses", ["CONFIRMED"]))

    selected: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    backlog: list[dict[str, Any]] = []

    for item in known.get("items", []):
        fp = item.get("fingerprint", "")
        sev = item.get("severity", "LOW")
        sev_rank = SEVERITY_MAP.get(sev, 1)
        st = item.get("status", "")

        is_accepted = fp in accept_fps
        is_rejected = fp in reject_fps

        if is_accepted or (st in accept_statuses and sev_rank >= min_rank and not is_rejected):
            selected.append(item)
        elif st in ("UNRUNNABLE", "ERROR") and sev_rank >= min_rank and not is_accepted and not is_rejected:
            needs_review.append(item)
        elif not is_rejected and st != "DROPPED":
            backlog.append(item)

    rnd = int(known.get("round", 1))
    step = args.ctx.get("step", "select_findings")

    write_artifact(
        out_dir=args.out_dir,
        name="selected",
        schema_type="verified",
        payload={"round": rnd, "items": selected},
        step=step,
        block="select-findings",
    )
    write_artifact(
        out_dir=args.out_dir,
        name="needs_review",
        schema_type="verified",
        payload={"round": rnd, "items": needs_review},
        step=step,
        block="select-findings",
    )
    write_artifact(
        out_dir=args.out_dir,
        name="backlog",
        schema_type="verified",
        payload={"round": rnd, "items": backlog},
        step=step,
        block="select-findings",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
