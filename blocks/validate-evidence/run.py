"""Evidence validator guard block: deterministically checks quotes, line ranges, and repros."""

from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Any

from agyflow.lib.contract import (
    E_INPUT,
    EXIT_FAILED,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)

FORBIDDEN_RE = re.compile(r"\b(socket|requests|urllib|subprocess|shutil\.rmtree)\b")


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def _validate_finding(
    f: dict[str, Any], wt_path: Path, ctx_lines: int, seen_ids: set[str]
) -> tuple[bool, str, str]:
    fid = str(f.get("id", ""))
    if fid in seen_ids:
        return False, "DUPLICATE_ID", f"Duplicate finding id: {fid}"
    seen_ids.add(fid)

    evidence = f.get("evidence", {})
    rel_path_str = str(evidence.get("file", ""))
    p = Path(rel_path_str)
    if p.is_absolute() or ".." in p.parts:
        return False, "BAD_PATH", f"Forbidden or traversal path: {rel_path_str}"

    target_file = wt_path / p
    if not target_file.is_file():
        return False, "NO_FILE", f"File does not exist in worktree: {rel_path_str}"

    try:
        lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as err:
        return False, "NO_FILE", f"Cannot read file {rel_path_str}: {err}"

    line_no = evidence.get("line", 0)
    if line_no < 1 or line_no > len(lines):
        return False, "LINE_RANGE", f"Line {line_no} out of bounds (file has {len(lines)} lines)"

    end_line = evidence.get("end_line", line_no)
    if end_line < line_no or end_line > len(lines):
        return False, "LINE_RANGE", f"End line {end_line} out of bounds"

    lo = max(1, line_no - ctx_lines)
    hi = min(len(lines), end_line + ctx_lines)
    window = _norm_ws("\n".join(lines[lo - 1 : hi]))
    quote = _norm_ws(str(evidence.get("quote", "")))

    if not quote or quote not in window:
        return False, "QUOTE_MISMATCH", f"Verbatim quote not found in lines {lo}-{hi}"

    repro = f.get("repro", {})
    kind = repro.get("kind")
    if kind == "python_snippet":
        code = str(repro.get("code", ""))
        if "EVIDENCE:" not in code:
            return False, "BAD_REPRO", "python_snippet repro does not print EVIDENCE:"
        if FORBIDDEN_RE.search(code):
            return False, "BAD_REPRO", "python_snippet uses forbidden import"
    elif kind == "reasoning":
        arg = str(repro.get("argument", ""))
        if len(arg.strip()) < 40:
            return False, "BAD_REPRO", "reasoning argument shorter than 40 characters"
    elif kind == "pytest_node":
        node = str(repro.get("node", ""))
        if not node.strip():
            return False, "BAD_REPRO", "pytest_node missing node identifier"
    else:
        return False, "BAD_REPRO", f"Unknown repro kind: {kind}"

    return True, "", ""


def main() -> int:
    args = parse_cli_args()
    try:
        findings_payload = load_input_payload(args.in_dir, "findings")
        wt_payload = load_input_payload(args.in_dir, "wt")
    except Exception as err:
        write_status(args.out_dir, "failed", E_INPUT, f"Missing inputs: {err}", complete=False)
        return EXIT_FAILED

    wt_path = Path(wt_payload["path"]).resolve()
    if not wt_path.is_dir():
        write_status(args.out_dir, "failed", E_INPUT, f"Worktree not found: {wt_path}", complete=False)
        return EXIT_FAILED

    ctx_lines = int(args.cfg.get("context_lines", 2))
    raw_findings = findings_payload.get("findings", [])

    clean_findings: list[dict[str, Any]] = []
    dropped_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for item in raw_findings:
        is_valid, reason_code, detail = _validate_finding(item, wt_path, ctx_lines, seen_ids)
        if is_valid:
            clean_findings.append(item)
        else:
            dropped_items.append({"id": str(item.get("id", "")), "reason_code": reason_code, "detail": detail})

    total_in = len(raw_findings)
    total_dropped = len(dropped_items)
    drop_ratio = round(total_dropped / max(1, total_in), 4)

    clean_payload = {
        "round": findings_payload.get("round", 1),
        "findings": clean_findings,
        "coverage": findings_payload.get("coverage", {"files_read": [], "files_skipped": [], "notes": ""}),
    }
    write_artifact(args.out_dir, "findings_clean", "findings", clean_payload, block="validate-evidence")

    dropped_payload = {
        "items": dropped_items,
        "total_in": total_in,
        "total_dropped": total_dropped,
        "drop_ratio": drop_ratio,
    }
    write_artifact(args.out_dir, "dropped", "dropped", dropped_payload, block="validate-evidence")

    warnings: list[str] = []
    max_drop = float(args.cfg.get("max_drop_ratio", 0.5))
    if drop_ratio > max_drop:
        warnings.append(f"analyst_unreliable: {total_dropped}/{total_in} dropped")

    write_status(args.out_dir, status="ok", complete=True, warnings=warnings)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
