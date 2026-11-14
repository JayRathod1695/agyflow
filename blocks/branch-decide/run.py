"""Branch decision block: decides pipeline flow (restart, stress, or proceed) based on ranking and test results."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from agyflow.lib.contract import (
    E_INPUT,
    EXIT_FAILED,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)


def _collect_scores(in_dir: Path) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    scores_dir = in_dir / "scores"
    search_dirs = [scores_dir] if scores_dir.is_dir() else [in_dir]

    for d in search_dirs:
        for json_file in sorted(d.rglob("*.json")):
            if json_file.name in ("ranking.json", "status.json", "meta.json", "index.json"):
                continue
            try:
                _, payload = read_artifact(json_file)
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict) and "passed" in item:
                            scores.append(item)
                elif isinstance(payload, dict) and "passed" in payload:
                    scores.append(payload)
            except Exception:
                continue
    return scores


def main() -> int:
    args = parse_cli_args()
    try:
        ranking = load_input_payload(args.in_dir, "ranking")
    except Exception as err:
        write_status(args.out_dir, "failed", E_INPUT, f"Missing or invalid ranking: {err}", complete=False)
        return EXIT_FAILED

    scores = _collect_scores(args.in_dir)

    passing_ids: list[str] = []
    failing_ids: list[str] = []

    if scores:
        seen = set()
        for s in scores:
            cid = str(s.get("item_id") or s.get("id", ""))
            if cid and cid not in seen:
                seen.add(cid)
                if s.get("passed") is True:
                    passing_ids.append(cid)
                else:
                    failing_ids.append(cid)
    else:
        # Fallback to ranking ordered entries
        for item in ranking.get("ordered", []):
            cid = str(item.get("id", ""))
            if cid:
                if float(item.get("score", 0.0)) > 0:
                    passing_ids.append(cid)
                else:
                    failing_ids.append(cid)

    min_pass = int(args.cfg.get("min_pass", 1))
    close_call = bool(ranking.get("close_call", False))

    if len(passing_ids) < min_pass:
        action = "restart"
        reason = "zero_passing_candidates" if len(passing_ids) == 0 else f"insufficient_passing_candidates ({len(passing_ids)} < {min_pass})"
    elif close_call and len(passing_ids) >= 2:
        action = "stress"
        reason = "close_call_margin_below_threshold"
    else:
        action = "proceed"
        reason = "clear_winner_selected"

    decision_payload = {
        "action": action,
        "reason": reason,
        "passing_ids": passing_ids,
        "failing_ids": failing_ids,
    }

    step = args.ctx.get("step", "decide")
    write_artifact(args.out_dir, "decision", "decision", decision_payload, step=step, block="branch-decide")
    write_status(args.out_dir, "ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
