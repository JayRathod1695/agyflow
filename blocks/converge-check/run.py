"""Convergence check block: merges findings across rounds, detects stability, and outputs known_brief."""

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

STATUS_PRIORITY = {"CONFIRMED": 3, "ERROR": 2, "UNRUNNABLE": 2, "DROPPED": 1}


def _token_jaccard(s1: str, s2: str) -> float:
    t1 = set(s1.lower().split())
    t2 = set(s2.lower().split())
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def _merge_status(old_st: str, new_st: str) -> str:
    if STATUS_PRIORITY.get(new_st, 0) > STATUS_PRIORITY.get(old_st, 0):
        return new_st
    return old_st


def _find_similar(
    it: dict[str, Any], candidates: list[dict[str, Any]], threshold: float
) -> dict[str, Any] | None:
    target_file = it.get("evidence", {}).get("file")
    target_key = str(it.get("claim_key", ""))
    best_cand: dict[str, Any] | None = None
    best_score = 0.0

    for cand in candidates:
        if cand.get("evidence", {}).get("file") == target_file:
            score = _token_jaccard(target_key, str(cand.get("claim_key", "")))
            if score >= threshold and score > best_score:
                best_score = score
                best_cand = cand

    return best_cand


def main() -> int:
    args = parse_cli_args()
    now_payload = load_input_payload(args.in_dir, "now")
    curr_round = int(now_payload.get("round", 1))

    known: dict[str, dict[str, Any]] = {}
    prev_file = args.in_dir / "prev.json"
    if prev_file.is_file():
        try:
            _, prev_payload = read_artifact(prev_file)
            for it in prev_payload.get("items", []):
                known[it["fingerprint"]] = dict(it)
        except Exception:
            pass

    similarity = float(args.cfg.get("similarity", 0.7))
    new_high_ids: list[str] = []
    repeated: list[str] = []

    for it in now_payload.get("items", []):
        fp = it.get("fingerprint", "")
        match = known.get(fp) or _find_similar(it, list(known.values()), similarity)

        if match:
            match["last_round"] = curr_round
            match["status"] = _merge_status(match.get("status", ""), it.get("status", ""))
            repeated.append(fp)
        else:
            item_copy = dict(it)
            item_copy["first_round"] = curr_round
            item_copy["last_round"] = curr_round
            known[fp] = item_copy

            if it.get("severity") == "HIGH" and it.get("status") != "DROPPED":
                new_high_ids.append(it.get("id", "F-???"))

    zero = (len(now_payload.get("items", [])) == 0)
    done = zero or (curr_round > 1 and len(new_high_ids) == 0)

    if zero:
        reason = "zero_findings"
    elif done:
        reason = "no_new_high_severity_findings"
    else:
        reason = f"new_high_severity_found_{len(new_high_ids)}"

    convergence_payload = {
        "done": done,
        "reason": reason,
        "round": curr_round,
        "new_high_ids": new_high_ids,
        "repeated_fingerprints": repeated,
        "zero_findings": zero,
    }

    known_payload = {
        "round": curr_round,
        "items": list(known.values()),
    }

    brief_items = [
        {
            "claim_key": it.get("claim_key", ""),
            "file": it.get("evidence", {}).get("file", ""),
            "status": it.get("status", ""),
        }
        for it in known.values()
    ]
    known_brief_payload = {"items": brief_items}

    step = args.ctx.get("step", "converge")
    write_artifact(args.out_dir, "convergence", "convergence", convergence_payload, step=step, block="converge-check")
    write_artifact(args.out_dir, "known", "verified", known_payload, step=step, block="converge-check")
    write_artifact(args.out_dir, "known_brief", "known_brief", known_brief_payload, step=step, block="converge-check")

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
