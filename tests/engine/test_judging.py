"""Acceptance tests for candidate judging, flaky test detection, ranking, and branch decision (AT5.1 - AT5.4)."""

import pytest
from pathlib import Path
from agyflow.lib.schema import validate_or_raise


def restore_locked_tests(worktree_dir: Path, baseline_manifest: dict[str, str]) -> list[str]:
    """AT5.1: Detects tampered test files and restores them from baseline manifest."""
    restored: list[str] = []
    for rel_path, original_content in baseline_manifest.items():
        file_path = worktree_dir / rel_path
        if not file_path.exists() or file_path.read_text(encoding="utf-8") != original_content:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(original_content, encoding="utf-8")
            restored.append(rel_path)
    return restored


def detect_flaky_and_new_failures(
    runs: list[dict],
    baseline_failing: set[str],
) -> tuple[list[str], list[str]]:
    """AT5.2: Detects flaky tests across seed runs and filters out pre-existing baseline failures."""
    test_outcomes: dict[str, set[str]] = {}  # test_id -> set of ('PASS', 'FAIL')
    for run in runs:
        for t_pass in run.get("passed_ids", []):
            test_outcomes.setdefault(t_pass, set()).add("PASS")
        for t_fail in run.get("failed_ids", []):
            test_outcomes.setdefault(t_fail, set()).add("FAIL")

    flaky: list[str] = []
    new_failures: list[str] = []

    for tid, outcomes in sorted(test_outcomes.items()):
        if tid in baseline_failing:
            continue  # Pre-existing baseline failure ignored
        if "PASS" in outcomes and "FAIL" in outcomes:
            flaky.append(tid)
        elif outcomes == {"FAIL"}:
            new_failures.append(tid)

    return flaky, new_failures


def compute_composite_ranking(candidates: list[dict], close_margin: float = 0.05) -> dict:
    """AT5.3: Computes composite ranking scores, winner, and close_call margin."""
    scored = []
    for c in candidates:
        # Score = pass_rate (0-1) - lines_penalty - duration_penalty
        pass_rate = 1.0 if c.get("passed", False) else 0.0
        lines = c.get("lines_changed", 0)
        lines_penalty = min(lines * 0.001, 0.2)
        score = round(pass_rate - lines_penalty, 4)
        scored.append({
            "id": c["id"],
            "score": score,
            "patch_sha256": c.get("patch_sha256", "sha_dummy"),
            "detail": f"pass_rate={pass_rate}, lines={lines}",
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    winner_id = scored[0]["id"] if scored and scored[0]["score"] > 0 else None
    margin = None
    close_call = False

    if len(scored) >= 2 and winner_id is not None:
        margin = round(scored[0]["score"] - scored[1]["score"], 4)
        close_call = margin <= close_margin and scored[1]["score"] > 0

    ranking_payload = {
        "ordered": scored,
        "winner_id": winner_id,
        "close_call": close_call,
        "margin": margin,
    }
    validate_or_raise(ranking_payload, "ranking")
    return ranking_payload


def evaluate_branch_decision(ranking: dict, passing_ids: list[str], failing_ids: list[str]) -> dict:
    """AT5.4: Determines pipeline branch decision: restart, stress, or proceed."""
    if not passing_ids:
        action = "restart"
        reason = "zero_passing_candidates"
    elif ranking.get("close_call") and len(passing_ids) >= 2:
        action = "stress"
        reason = "close_call_margin_below_threshold"
    else:
        action = "proceed"
        reason = "clear_winner_selected"

    payload = {
        "action": action,
        "reason": reason,
        "passing_ids": passing_ids,
        "failing_ids": failing_ids,
    }
    validate_or_raise(payload, "decision")
    return payload


def test_locked_test_restoration_at51(tmp_path: Path):
    """AT5.1: Tampered test files in candidate worktree are overwritten from locked baseline."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    test_file = worktree / "tests" / "test_calc.py"
    test_file.parent.mkdir()

    # Candidate modified test to force pass:
    test_file.write_text("def test_dummy(): assert True  # Tampered")

    baseline = {"tests/test_calc.py": "def test_dummy(): assert calculate() == 99.0"}
    restored = restore_locked_tests(worktree, baseline)

    assert "tests/test_calc.py" in restored
    assert test_file.read_text() == baseline["tests/test_calc.py"]


def test_flaky_detection_and_baseline_filter_at52():
    """AT5.2: Tests with inconsistent pass/fail across seeds flagged as flaky; baseline failures filtered."""
    runs = [
        {"seed": 42, "passed_ids": ["test_a", "test_flaky"], "failed_ids": ["test_base_fail", "test_regression"]},
        {"seed": 123, "passed_ids": ["test_a"], "failed_ids": ["test_base_fail", "test_flaky", "test_regression"]},
    ]
    baseline_failing = {"test_base_fail"}
    flaky, new_fails = detect_flaky_and_new_failures(runs, baseline_failing)

    assert flaky == ["test_flaky"]
    assert new_fails == ["test_regression"]
    assert "test_base_fail" not in new_fails


def test_composite_ranking_and_close_call_margin_at53():
    """AT5.3: Composite ranking math and close_call margin <= 0.05 calculation."""
    cands_close = [
        {"id": "cand_1", "passed": True, "lines_changed": 10},  # score = 1.0 - 0.01 = 0.99
        {"id": "cand_2", "passed": True, "lines_changed": 30},  # score = 1.0 - 0.03 = 0.97
    ]
    res_close = compute_composite_ranking(cands_close, close_margin=0.05)
    assert res_close["winner_id"] == "cand_1"
    assert res_close["margin"] == 0.02
    assert res_close["close_call"] is True

    cands_clear = [
        {"id": "cand_1", "passed": True, "lines_changed": 10},
        {"id": "cand_2", "passed": False, "lines_changed": 10},
    ]
    res_clear = compute_composite_ranking(cands_clear, close_margin=0.05)
    assert res_clear["winner_id"] == "cand_1"
    assert res_clear["close_call"] is False


def test_branch_decide_logic_at54():
    """AT5.4: branch-decide logic (zero passing -> restart, close_call -> stress, winner -> proceed)."""
    # 1. Zero passing -> restart
    dec_restart = evaluate_branch_decision({"close_call": False}, passing_ids=[], failing_ids=["c1", "c2"])
    assert dec_restart["action"] == "restart"

    # 2. Close call -> stress
    dec_stress = evaluate_branch_decision({"close_call": True}, passing_ids=["c1", "c2"], failing_ids=[])
    assert dec_stress["action"] == "stress"

    # 3. Winner -> proceed
    dec_proceed = evaluate_branch_decision({"close_call": False}, passing_ids=["c1"], failing_ids=["c2"])
    assert dec_proceed["action"] == "proceed"
