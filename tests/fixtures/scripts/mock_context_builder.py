#!/usr/bin/env python3.11
"""Scripted fake context_builder agent response generator."""

import argparse
import json
import sys
from pathlib import Path

SAMPLE_PATH = Path(__file__).resolve().parent / "samples" / "context.json"


def generate_context_bundle() -> dict:
    """Load and return standard mock context artifact."""
    if not SAMPLE_PATH.exists():
        return {
            "schema_version": "1.0.0",
            "role": "context_fresh",
            "files": ["src/tinycalc/calculator.py"],
            "token_count": 256,
        }
    with open(SAMPLE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock Context Builder Script")
    parser.add_argument("--in", dest="in_dir", help="Input directory")
    parser.add_argument("--out", dest="out_dir", help="Output directory")
    parser.add_argument("--cfg", dest="cfg", help="Config JSON or string")
    parser.add_argument("--stdout", action="store_true", help="Print to stdout")
    args = parser.parse_args()

    context_data = generate_context_bundle()

    if args.out_dir:
        out_path = Path(args.out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        envelope = {
            "schema_version": "1.0.0",
            "block_name": "agent-call-fake",
            "status": "ok",
            "data": context_data,
        }
        with open(out_path / "artifact.json", "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2)

    if args.stdout or not args.out_dir:
        sys.stdout.write(json.dumps(context_data, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
