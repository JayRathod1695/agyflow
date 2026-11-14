"""Candidate ranking block: computes composite score and identifies winners / close calls."""

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
    # 1. scores/ collection directory
    scores_dir = in_dir / "scores"
    search_dirs = [scores_dir] if scores_dir.is_dir() else [in_dir]

    seen_ids: set[str] = set()
    for d in search_dirs:
        for json_file in sorted(d.rglob("*.json")):
            if json_file.name in ("status.json", "meta.json", "ranking.json"):
                continue
            try:
                _, payload = read_artifact(json_file)
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict) and "item_id" in item or "passed" in item:
                            cid = str(item.get("item_id") or item.get("id", len(scores)))
                            if cid not in seen_ids:
                                seen_ids.add(cid); scores.append(item)
                elif isinstance(payload, dict) and ("item_id" in payload or "passed" in payload or "runs" in payload):
                    cid = str(payload.get("item_id") or payload.get("id") or json_file.parent.name)
                    if cid not in seen_ids:
                        seen_ids.add(cid); scores.append(payload)
            except Exception:
                continue
    return scores


def main() -> int:
    args = parse_cli_args()
    scores = _collect_scores(args.in_dir)

    if not scores:
        write_status(args.out_dir, "failed", E_INPUT, "No valid candidate scores found", complete=False)
        return EXIT_FAILED

    raw_weights = args.cfg.get("weights") or {}
    weights = raw_weights if isinstance(raw_weights, dict) else {}
    w_pass = float(weights.get("pass_ratio", 1.0))
    w_size = float(weights.get("size", 0.1))
    noise_margin = float(args.cfg.get("noise_margin", 0.05))

    metrics_cfg = args.cfg.get("metrics") or []
    m_keys = [str(k) for k in (metrics_cfg.keys() if isinstance(metrics_cfg, dict) else metrics_cfg)]

    # Collect min/max per metric for normalization
    m_ranges: dict[str, tuple[float, float]] = {}
    for mk in m_keys:
        vals = [float(s.get("metrics", {}).get(mk, 0.0)) for s in scores if mk in s.get("metrics", {})]
        if vals:
            m_ranges[mk] = (min(vals), max(vals))

    scored: list[dict[str, Any]] = []
    for s in scores:
        cid = str(s.get("item_id") or s.get("id") or "cand")
        passed = bool(s.get("passed", False))
        runs = s.get("runs", [])

        if runs:
            p_runs = len([r for r in runs if r.get("exit_code") == 0 and r.get("failed", 0) == 0])
            pass_ratio = (p_runs / len(runs)) if passed else 0.0
        else:
            pass_ratio = 1.0 if passed else 0.0

        lines = int(s.get("lines_changed", 0))
        flaky = s.get("flaky_tests", [])
        patch_sha = str(s.get("patch_sha256", "sha_dummy"))

        # Metric normalization
        metric_sum = 0.0
        for mk in m_keys:
            w_m = float(weights.get(mk, 1.0))
            if len(scores) == 1:
                norm_m = 0.5
            elif mk in m_ranges:
                mn, mx = m_ranges[mk]
                val = float(s.get("metrics", {}).get(mk, mn))
                norm_m = (val - mn) / (mx - mn) if mx > mn else 0.5
            else:
                norm_m = 0.5
            metric_sum += w_m * norm_m

        size_penalty = w_size * min(1.0, lines / 500.0)
        flaky_penalty = 0.1 * len(flaky)

        composite = round(w_pass * pass_ratio + metric_sum - size_penalty - flaky_penalty, 4)
        detail = f"pass_ratio={pass_ratio:.2f}, lines={lines}, flaky={len(flaky)}"

        scored.append({
            "id": cid,
            "score": composite,
            "patch_sha256": patch_sha,
            "detail": detail,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    winner_id = scored[0]["id"] if scored and scored[0]["score"] > 0 else None
    margin = None
    close_call = False

    if len(scored) >= 2 and winner_id is not None:
        margin = round(scored[0]["score"] - scored[1]["score"], 4)
        close_call = margin <= noise_margin and scored[1]["score"] > 0

    ranking_payload = {
        "ordered": scored,
        "winner_id": winner_id,
        "close_call": close_call,
        "margin": margin,
    }

    step = args.ctx.get("step", "rank")
    write_artifact(args.out_dir, "ranking", "ranking", ranking_payload, step=step, block="rank")
    write_status(args.out_dir, "ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
