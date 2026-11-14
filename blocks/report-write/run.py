"""Report writing block: generates Markdown summary of run results, findings, and metrics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from agyflow.lib.contract import (
    EXIT_OK,
    parse_cli_args,
    read_artifact,
    write_status,
)


def _load_json_file(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        _, payload = read_artifact(p)
        return payload
    except Exception:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}


def main() -> int:
    args = parse_cli_args()
    run_id = args.ctx.get("run_id", "unknown_run")
    repo = args.ctx.get("repo", ".")

    run_index_file = args.in_dir / "_run_index.json"
    run_index: dict[str, Any] = {}
    if run_index_file.is_file():
        try:
            run_index = json.loads(run_index_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    steps_data: dict[str, Any] = run_index.get("steps", {})
    failed_step: str | None = None
    failed_code: str | None = None

    for step_name, step_info in steps_data.items():
        if isinstance(step_info, dict):
            status = step_info.get("status")
            if status in ("failed", "retryable"):
                failed_step = step_name
                failed_code = step_info.get("code", "ERROR")
                break

    outcome = f"FAILED({failed_step}, {failed_code})" if failed_step else "COMPLETED"

    lines: list[str] = [
        f"# agyflow Execution Report",
        "",
        f"- **Run ID:** `{run_id}`",
        f"- **Outcome:** **`{outcome}`**",
        f"- **Generated At:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"- **Repository:** `{repo}`",
        "",
        "## 1. Repository Snapshot",
    ]

    snap_path = run_index.get("artifacts", {}).get("snapshot") or (
        steps_data.get("snap", {}).get("outputs", {}).get("snapshot")
    )
    snap = _load_json_file(snap_path) if snap_path else {}
    if snap:
        lines.extend([
            f"- **HEAD Commit:** `{snap.get('head', 'unknown')}`",
            f"- **Branch:** `{snap.get('branch', 'detached')}`",
            f"- **Dirty Working Tree:** {snap.get('dirty', False)}",
            f"- **Tracked Files:** {snap.get('tracked_files', 0)}",
        ])
    else:
        lines.append("_Snapshot details unavailable._")

    lines.extend(["", "## 2. Findings & Verification"])
    clean_path = run_index.get("artifacts", {}).get("findings_clean") or (
        steps_data.get("evid", {}).get("outputs", {}).get("findings_clean")
    )
    dropped_path = run_index.get("artifacts", {}).get("dropped") or (
        steps_data.get("evid", {}).get("outputs", {}).get("dropped")
    )

    clean_data = _load_json_file(clean_path) if clean_path else {}
    dropped_data = _load_json_file(dropped_path) if dropped_path else {}

    clean_items = clean_data.get("findings", [])
    dropped_items = dropped_data.get("items", [])
    total_in = dropped_data.get("total_in", len(clean_items) + len(dropped_items))

    lines.extend([
        f"- **Total Findings Analyzed:** {total_in}",
        f"- **Verified (Clean):** {len(clean_items)}",
        f"- **Dropped by Guard:** {len(dropped_items)}",
        f"- **Drop Ratio:** {dropped_data.get('drop_ratio', 0.0)}",
        "",
    ])

    if clean_items:
        lines.extend(["### Verified Defects", ""])
        lines.append("| ID | Severity | Category | File:Line | Claim |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for f in clean_items:
            fid = f.get("id", "F-???")
            sev = f.get("severity", "LOW")
            cat = f.get("category", "other")
            ev = f.get("evidence", {})
            loc = f"{ev.get('file', '')}:{ev.get('line', '')}"
            claim = str(f.get("claim", "")).replace("|", "\\|")
            lines.append(f"| `{fid}` | **{sev}** | {cat} | `{loc}` | {claim} |")
        lines.append("")

    if dropped_items:
        lines.extend(["### Dropped Findings (Guard Filter)", ""])
        lines.append("| ID | Reason Code | Detail |")
        lines.append("| :--- | :--- | :--- |")
        for d in dropped_items:
            fid = d.get("id", "F-???")
            code = d.get("reason_code", "DROPPED")
            detail = str(d.get("detail", "")).replace("|", "\\|")
            lines.append(f"| `{fid}` | `{code}` | {detail} |")
        lines.append("")

    lines.extend(["## 3. Pipeline Step Log", ""])
    lines.append("| Step | Status | Duration (s) | Warnings |")
    lines.append("| :--- | :--- | :--- | :--- |")
    for step_name, step_info in sorted(steps_data.items()):
        if isinstance(step_info, dict):
            st = step_info.get("status", "unknown")
            dur = step_info.get("metrics", {}).get("duration_s", "-")
            warns = ", ".join(step_info.get("warnings", [])) or "-"
            lines.append(f"| `{step_name}` | {st} | {dur} | {warns} |")

    report_content = "\n".join(lines) + "\n"
    report_file = args.out_dir / "REPORT.md"
    report_file.write_text(report_content, encoding="utf-8")

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
