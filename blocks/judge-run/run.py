"""Candidate judging block: evaluates patch against seeds, locked tests, and metrics in scratch worktree."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import sys
import time
from typing import Any
import xml.etree.ElementTree as ET

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
from agyflow.lib.proc import run_command


def _parse_junit(xml_path: Path) -> tuple[int, int, int, list[str], list[str]]:
    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return 0, 0, 0, [], []
    tcs = root.findall(".//testcase") or ([root] if root.tag == "testcase" else [])
    passed, failed, errors = 0, 0, 0
    p_ids, f_ids = [], []
    for tc in tcs:
        tid = f"{tc.get('classname', '')}::{tc.get('name', '')}".strip(":") or f"tc_{passed+failed+errors}"
        if tc.find("failure") is not None:
            failed += 1; f_ids.append(tid)
        elif tc.find("error") is not None:
            errors += 1; f_ids.append(tid)
        elif tc.find("skipped") is None:
            passed += 1; p_ids.append(tid)
    return passed, failed, errors, p_ids, f_ids


def main() -> int:
    args = parse_cli_args()
    try:
        snapshot = load_input_payload(args.in_dir, "snapshot")
        head = snapshot["head"]
    except Exception as err:
        write_status(args.out_dir, "failed", E_INPUT, f"Missing or invalid snapshot: {err}", complete=False)
        return EXIT_FAILED

    patch_meta: dict[str, Any] = {}
    try:
        patch_meta = load_input_payload(args.in_dir, "patch_meta")
    except Exception:
        pass

    baseline: dict[str, Any] = {}
    for b_name in ("baseline", "test_summary"):
        b_file = args.in_dir / f"{b_name}.json"
        if b_file.is_file():
            try:
                _, baseline = read_artifact(b_file); break
            except Exception: pass
    base_failing = set(baseline.get("failed_ids", []))

    patch_file = args.in_dir / "patch.patch"
    if not patch_file.is_file():
        patches = list(args.in_dir.glob("*.patch"))
        if patches: patch_file = patches[0]

    repo = Path(args.ctx.get("repo", ".")).resolve()
    scratch = (args.out_dir / "_scratch").resolve()
    shutil.rmtree(scratch, ignore_errors=True)

    add_res = run_command(["git", "worktree", "add", "--detach", str(scratch), head], cwd=repo)
    if add_res.returncode != 0:
        write_status(args.out_dir, "failed", E_INPUT, f"Scratch worktree add failed: {add_res.stderr}", complete=False)
        return EXIT_FAILED

    try:
        if patch_file.is_file():
            run_command(["git", "apply", "--index", str(patch_file.resolve())], cwd=scratch)

        locked_dir = args.in_dir / "locked_tests"
        if locked_dir.is_dir():
            for p in locked_dir.rglob("*"):
                if p.is_file():
                    dest = scratch / p.relative_to(locked_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, dest)

        h_dir = args.cfg.get("holdout_dir")
        if h_dir and Path(h_dir).is_dir():
            for p in Path(h_dir).rglob("*"):
                if p.is_file():
                    dest = scratch / p.relative_to(Path(h_dir))
                    dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, dest)

        test_cmd = args.cfg.get("test_cmd", "pytest")
        repeat = int(args.cfg.get("repeat", 5))
        raw_seeds = args.cfg.get("seeds", [11, 22, 33, 44, 55])
        seeds = [int(s) for s in (raw_seeds if isinstance(raw_seeds, list) else [raw_seeds])][:repeat]
        timeout_s = float(args.cfg.get("timeout_s", 60.0))

        runs: list[dict[str, Any]] = []
        test_outcomes: dict[str, set[str]] = {}

        for seed in seeds:
            t0 = time.time()
            res = run_command(shlex.split(test_cmd), cwd=scratch, env_overrides={"SEED": str(seed), "PYTHONHASHSEED": str(seed)}, timeout_sec=timeout_s)
            dur = round(time.time() - t0, 3)

            xml_candidates = list(scratch.rglob("*.xml"))
            if xml_candidates:
                passed, failed, errors, p_ids, f_ids = _parse_junit(xml_candidates[0])
            else:
                passed = 1 if res.returncode == 0 else 0
                failed = 0 if res.returncode == 0 else 1
                errors = 0
                p_ids = [f"test_seed_{seed}"] if passed else []
                f_ids = [f"test_seed_{seed}"] if failed else []

            for tid in p_ids: test_outcomes.setdefault(tid, set()).add("PASS")
            for tid in f_ids: test_outcomes.setdefault(tid, set()).add("FAIL")
            runs.append({"seed": seed, "exit_code": res.returncode, "passed": passed, "failed": failed, "errors": errors, "duration_s": dur})

        if args.cfg.get("extra_cmd"):
            run_command(shlex.split(args.cfg["extra_cmd"]), cwd=scratch, timeout_sec=timeout_s)

        metrics: dict[str, float] = {}
        if args.cfg.get("metric_cmd"):
            m_res = run_command(shlex.split(args.cfg["metric_cmd"]), cwd=scratch, timeout_sec=timeout_s)
            try:
                metrics = {k: float(v) for k, v in json.loads(m_res.stdout).items()}
            except Exception: pass

        flaky: list[str] = []
        new_failures: list[str] = []
        for tid, outcomes in sorted(test_outcomes.items()):
            if tid in base_failing: continue
            if "PASS" in outcomes and "FAIL" in outcomes:
                flaky.append(tid)
            elif outcomes == {"FAIL"}:
                new_failures.append(tid)

        req_failures: list[str] = []
        required = args.cfg.get("required", ["tests_pass", "no_new_failures"])
        if "tests_pass" in required and any(r["exit_code"] != 0 or r["failed"] > 0 for r in runs):
            req_failures.append("tests_pass")
        if "no_new_failures" in required and new_failures:
            req_failures.append("no_new_failures")

        score_payload = {
            "item_id": str(patch_meta.get("approach_id") or args.ctx.get("item_id", "cand")),
            "passed": len(req_failures) == 0,
            "patch_sha256": str(patch_meta.get("patch_sha256", "")),
            "required_failures": req_failures,
            "runs": runs,
            "flaky_tests": flaky,
            "new_failures": new_failures,
            "metrics": metrics,
            "lines_changed": int(patch_meta.get("insertions", 0) + patch_meta.get("deletions", 0)),
        }
    finally:
        run_command(["git", "worktree", "remove", "--force", str(scratch)], cwd=repo)
        run_command(["git", "worktree", "prune"], cwd=repo)
        shutil.rmtree(scratch, ignore_errors=True)

    write_artifact(args.out_dir, "score", "score", score_payload, step=args.ctx.get("step", "judge"), block="judge-run")
    write_status(args.out_dir, "ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
