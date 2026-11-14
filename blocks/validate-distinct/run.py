"""Plan distinctness and policy guard block: ensures approaches differ and obey file boundaries."""

from __future__ import annotations

import fnmatch
from pathlib import Path
import sys
from typing import Any

from agyflow.lib.contract import (
    E_POLICY,
    EXIT_FAILED,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)


def _token_jaccard(s1: str, s2: str) -> float:
    t1 = set(s1.lower().split())
    t2 = set(s2.lower().split())
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def _matches_forbidden(file_path: str, forbidden_globs: list[str]) -> bool:
    p = file_path.strip().lstrip("/")
    for glob in forbidden_globs:
        g = glob.strip().lstrip("/")
        if fnmatch.fnmatch(p, g) or fnmatch.fnmatch(Path(p).name, g):
            return True
        if p.startswith(g.rstrip("/*") + "/"):
            return True
    return False


def main() -> int:
    args = parse_cli_args()
    plan = load_input_payload(args.in_dir, "plan")

    forbidden = list(args.cfg.get("forbidden", [".flow", "tests", "data", "*.ckpt", "*.pt"]))
    similarity = float(args.cfg.get("similarity", 0.6))
    min_approaches = int(args.cfg.get("min_approaches", 2))

    raw_approaches = plan.get("approaches", [])
    clean_approaches: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for app in raw_approaches:
        aid = str(app.get("id", ""))
        files = list(app.get("files_to_change", []))
        max_f = int(app.get("max_files_changed", len(files)))

        if len(files) > max_f:
            dropped.append({"id": aid, "reason_code": "INCONSISTENT", "detail": f"files_to_change ({len(files)}) exceeds max ({max_f})"})
            continue

        hit_forbidden = False
        for f in files:
            if _matches_forbidden(f, forbidden):
                dropped.append({"id": aid, "reason_code": "FORBIDDEN_PATH", "detail": f"Path '{f}' matches forbidden glob"})
                hit_forbidden = True
                break
        if hit_forbidden:
            continue

        text_curr = f"{app.get('title', '')} {app.get('summary', '')} {' '.join(app.get('steps', []))}"
        is_duplicate = False
        for accepted in clean_approaches:
            if app.get("differs_on") == accepted.get("differs_on"):
                text_prev = f"{accepted.get('title', '')} {accepted.get('summary', '')} {' '.join(accepted.get('steps', []))}"
                sim = _token_jaccard(text_curr, text_prev)
                if sim >= similarity:
                    dropped.append({"id": aid, "reason_code": "NOT_DISTINCT", "detail": f"Similar ({sim:.2f} >= {similarity}) to approach {accepted.get('id')}"})
                    is_duplicate = True
                    break
        if is_duplicate:
            continue

        clean_approaches.append(app)

    if not clean_approaches:
        write_status(args.out_dir, "failed", E_POLICY, "Zero valid approaches remain after validation", complete=False)
        return EXIT_FAILED

    valid_ids = {a["id"] for a in clean_approaches}
    unknowns: list[dict[str, Any]] = []
    for u in plan.get("unknowns", []):
        u_copy = dict(u)
        u_copy["approach_ids"] = [aid for aid in u.get("approach_ids", []) if aid in valid_ids]
        unknowns.append(u_copy)

    plan_clean = {
        "revision": plan.get("revision", 1),
        "goal": plan.get("goal", ""),
        "approaches": clean_approaches,
        "unknowns": unknowns,
        "not_doing": plan.get("not_doing", []),
    }

    total_in = len(raw_approaches)
    issues_payload = {
        "items": dropped,
        "total_in": total_in,
        "total_dropped": len(dropped),
        "drop_ratio": round(len(dropped) / max(1, total_in), 4),
    }

    step = args.ctx.get("step", "validate_distinct")
    write_artifact(args.out_dir, "plan_clean", "plan", plan_clean, step=step, block="validate-distinct")
    write_artifact(args.out_dir, "issues", "dropped", issues_payload, step=step, block="validate-distinct")

    warnings: list[str] = []
    if len(clean_approaches) < min_approaches:
        warnings.append("few_approaches")

    write_status(args.out_dir, status="ok", complete=True, warnings=warnings)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
