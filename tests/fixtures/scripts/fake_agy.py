#!/usr/bin/env python3.11
"""Scripted fake agy CLI executable stub for deterministic testing.

Simulates Gemini 3.8 Flash agy CLI responses and handles error injections.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent / "samples"


def load_canned_sample(sample_name: str) -> dict:
    sample_file = SAMPLES_DIR / f"{sample_name}.json"
    if sample_file.exists():
        with open(sample_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"status": "ok", "mock_role": sample_name}


def main() -> int:
    mode = os.environ.get("FAKE_AGY_MODE", "ok").lower()

    if mode == "retryable_10":
        sys.stderr.write("FAKE_AGY: simulated rate limit or temporary error\n")
        return 10
    if mode == "failed_20":
        sys.stderr.write("FAKE_AGY: simulated fatal policy violation\n")
        return 20
    if mode == "timeout":
        time.sleep(120)
        return 124
    if mode == "malformed":
        sys.stdout.write("{ 'invalid_json': true, unterminated ...\n")
        return 0
    if mode == "empty":
        return 0

    parser = argparse.ArgumentParser(description="Fake agy CLI stub")
    parser.add_argument("--print", dest="print_mode", action="store_true")
    parser.add_argument("--schema", dest="schema_path", type=str, default="")
    parser.add_argument("--model", dest="model", type=str, default="flash")
    parser.add_argument("prompt_words", nargs="*", default=[])

    args, unknown = parser.parse_known_args()

    full_prompt = " ".join(args.prompt_words)
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read()
        full_prompt = f"{full_prompt}\n{stdin_content}"

    # Determine simulated response based on prompt contents
    prompt_lower = full_prompt.lower()
    if "analyst" in prompt_lower or "defect" in prompt_lower or "findings" in prompt_lower:
        result = load_canned_sample("findings")
    elif "context" in prompt_lower or "fresh" in prompt_lower:
        result = load_canned_sample("context")
    elif "report" in prompt_lower:
        result = load_canned_sample("report")
    else:
        result = {
            "schema_version": "1.0.0",
            "status": "ok",
            "message": "Generic fake agy response",
            "prompt_echo": full_prompt[:100],
        }

    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
