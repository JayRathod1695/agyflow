"""JUnit XML parser block: extracts structured test metrics with exit-code fallback."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any
import xml.etree.ElementTree as ET

from agyflow.lib.contract import (
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)


def _parse_xml(xml_path: Path, max_bytes: int) -> dict[str, Any] | None:
    if not xml_path.is_file():
        return None
    if xml_path.stat().st_size > max_bytes or xml_path.stat().st_size == 0:
        return None

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception:
        return None

    total = 0
    passed = 0
    failed = 0
    errors = 0
    skipped = 0
    failed_ids: list[str] = []
    duration_s = 0.0

    testcases = root.findall(".//testcase")
    if not testcases and root.tag == "testcase":
        testcases = [root]

    for tc in testcases:
        total += 1
        classname = tc.get("classname", "").strip()
        name = tc.get("name", "").strip()
        test_id = f"{classname}::{name}" if classname else (name or f"test_{total}")

        try:
            duration_s += float(tc.get("time", 0.0))
        except ValueError:
            pass

        if tc.find("failure") is not None:
            failed += 1
            failed_ids.append(test_id)
        elif tc.find("error") is not None:
            errors += 1
            failed_ids.append(test_id)
        elif tc.find("skipped") is not None:
            skipped += 1
        else:
            passed += 1

    if total == 0:
        return None

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "failed_ids": failed_ids,
        "duration_s": round(duration_s, 3),
        "source": "junit",
    }


def main() -> int:
    args = parse_cli_args()
    run_result = load_input_payload(args.in_dir, "run_result")

    junit_name = str(args.cfg.get("junit_file", "junit.xml")).strip()
    max_bytes = int(args.cfg.get("max_bytes", 20 * 1024 * 1024))

    candidates: list[Path] = []
    junit_from_res = run_result.get("junit_path")
    if junit_from_res:
        candidates.append(Path(junit_from_res))
    candidates.append(args.in_dir / "run_files" / junit_name)
    candidates.append(args.in_dir / junit_name)

    xml_file: Path | None = None
    for cand in candidates:
        if cand.is_file():
            xml_file = cand
            break

    summary: dict[str, Any] | None = None
    if xml_file:
        summary = _parse_xml(xml_file, max_bytes)

    if summary is None:
        exit_code = run_result.get("exit_code")
        timed_out = bool(run_result.get("timed_out", False))
        failed_count = 1 if (exit_code != 0 or timed_out) else 0
        summary = {
            "total": 1,
            "passed": 0 if failed_count else 1,
            "failed": failed_count,
            "errors": 0,
            "skipped": 0,
            "failed_ids": ["<exit_code_nonzero>"] if failed_count else [],
            "duration_s": float(run_result.get("duration_s", 0.0)),
            "source": "exit_code",
        }

    write_artifact(
        out_dir=args.out_dir,
        name="test_summary",
        schema_type="test_summary",
        payload=summary,
        step=args.ctx.get("step", "parse_junit"),
        block="parse-junit",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
