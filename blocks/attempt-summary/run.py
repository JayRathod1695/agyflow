"""Attempt summary block: summarizes tried approaches, outcomes, and failures for restart iteration."""

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


def _collect_by_id(in_dir: Path, sub_name: str) -> dict[str, dict[str, Any]]:
    coll_dir = in_dir / sub_name
    search_dir = coll_dir if coll_dir.is_dir() else in_dir
    res: dict[str, dict[str, Any]] = {}

    for f in search_dir.rglob("*.json"):
        if f.name in ("status.json", "meta.json", "plan.json", "decision.json"):
            continue
        try:
            _, payload = read_artifact(f)
            if isinstance(payload, dict):
                cid = str(payload.get("approach_id") or payload.get("item_id") or payload.get("id") or f.parent.name)
                res[cid] = payload
        except Exception:
            continue
    return res


def main() -> int:
    args = parse_cli_args()
    try:
        plan = load_input_payload(args.in_dir, "plan")
    except Exception as err:
        write_status(args.out_dir, "failed", E_INPUT, f"Missing or invalid plan: {err}", complete=False)
        return EXIT_FAILED

    decision = {}
    try:
        decision = load_input_payload(args.in_dir, "decision")
    except Exception:
        pass

    guards = _collect_by_id(args.in_dir, "guards")
    results = _collect_by_id(args.in_dir, "results")
    scores = _collect_by_id(args.in_dir, "scores")

    raw_att = args.cfg.get("attempt", 1)
    try:
        attempt_num = max(1, int(raw_att))
    except Exception:
        attempt_num = 1

    action = str(decision.get("action", "proceed"))

    tried: list[dict[str, Any]] = []
    approaches = plan.get("approaches", [])
    if isinstance(approaches, list):
        for idx, ap in enumerate(approaches):
            aid = str(ap.get("id", f"app_{idx}"))
            title = str(ap.get("title", f"Approach {aid}"))

            guard = guards.get(aid) or guards.get(str(idx))
            result = results.get(aid) or results.get(str(idx))
            score = scores.get(aid) or scores.get(str(idx))

            outcome = "error"
            req_failures: list[str] = []
            open_issues: list[str] = []

            if guard and (guard.get("passed") is False or guard.get("rejections")):
                outcome = "guard_rejected"
                open_issues = [str(r) for r in guard.get("rejections", ["Diff guard rejected patch"])]
            elif result and (result.get("status") in ("blocked", "error") or result.get("open_issues")):
                outcome = "actor_blocked"
                open_issues = [str(i) for i in result.get("open_issues", ["Actor execution was blocked"])]
            elif score is not None:
                if score.get("passed") is True:
                    outcome = "passed"
                else:
                    outcome = "failed_tests"
                    req_failures = [str(rf) for rf in score.get("required_failures", [])]
                    new_f = score.get("new_failures", [])
                    if new_f:
                        open_issues.append(f"New test failures: {', '.join(str(x) for x in new_f)}")
                    flaky = score.get("flaky_tests", [])
                    if flaky:
                        open_issues.append(f"Flaky tests: {', '.join(str(x) for x in flaky)}")
            else:
                outcome = "error"
                open_issues = ["No execution artifacts recorded"]

            tried.append({
                "approach_id": aid,
                "title": title,
                "outcome": outcome,
                "required_failures": req_failures,
                "open_issues": open_issues,
            })

    summary_payload = {
        "attempt": attempt_num,
        "tried": tried,
        "action": action,
    }

    step = args.ctx.get("step", "sum")
    write_artifact(args.out_dir, "attempt_summary", "attempt_summary", summary_payload, step=step, block="attempt-summary")
    write_status(args.out_dir, "ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
