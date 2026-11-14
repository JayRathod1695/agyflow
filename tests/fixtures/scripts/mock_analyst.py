#!/usr/bin/env python3.11
"""Scripted fake analyst agent response generator."""

import argparse
import json
import sys
from pathlib import Path

SAMPLE_PATH = Path(__file__).resolve().parent / "samples" / "findings.json"


def generate_analyst_findings() -> dict:
    """Load and return standard mock analyst findings artifact."""
    if not SAMPLE_PATH.exists():
        return {
            "schema_version": "1.0.0",
            "role": "analyst",
            "defect_summary": "Logic defect in compute_discounted_total",
            "target_file": "src/tinycalc/calculator.py",
        }
    with open(SAMPLE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock Analyst Script")
    parser.add_argument("--in", dest="in_dir", help="Input directory")
    parser.add_argument("--out", dest="out_dir", help="Output directory")
    parser.add_argument("--cfg", dest="cfg", help="Config JSON or string")
    parser.add_argument("--stdout", action="store_true", help="Print to stdout")
    args = parser.parse_args()

    findings = generate_analyst_findings()

    if args.out_dir:
        out_path = Path(args.out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        envelope = {
            "schema_version": "1.0.0",
            "block_name": "agent-call-fake",
            "status": "ok",
            "data": findings,
        }
        with open(out_path / "artifact.json", "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2)

    if args.stdout or not args.out_dir:
        sys.stdout.write(json.dumps(findings, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
