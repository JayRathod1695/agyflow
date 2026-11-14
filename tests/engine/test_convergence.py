"""Acceptance tests for convergence detection and loop termination."""

from agyflow.lib.schema import validate_or_raise


def evaluate_convergence(current_items: list[dict], round_num: int, prev_fingerprints: set[str] | None = None) -> dict:
    """Evaluates whether verification loop has converged."""
    seen_fps = prev_fingerprints or set()
    new_high_ids: list[str] = []
    repeated_fps: list[str] = []

    if not current_items:
        payload = {
            "done": True,
            "reason": "zero_findings",
            "round": round_num,
            "new_high_ids": [],
            "repeated_fingerprints": [],
            "zero_findings": True,
        }
        validate_or_raise(payload, "convergence")
        return payload

    for item in current_items:
        fp = item.get("fingerprint", "")
        sev = item.get("severity", "LOW")
        if fp in seen_fps:
            repeated_fps.append(fp)
        else:
            if sev == "HIGH":
                new_high_ids.append(item["id"])

    # Convergence rule: Round >= 2 without new HIGH findings terminates loop
    done = round_num >= 2 and len(new_high_ids) == 0
    reason = "no_new_high_findings" if done else ("initial_round" if round_num == 1 else "new_high_discovered")

    payload = {
        "done": done,
        "reason": reason,
        "round": round_num,
        "new_high_ids": new_high_ids,
        "repeated_fingerprints": repeated_fps,
        "zero_findings": False,
    }
    validate_or_raise(payload, "convergence")
    return payload


def test_round_1_initial_continues():
    """Verify round 1 with HIGH findings continues loop."""
    items = [{"id": "F-01", "fingerprint": "fp1", "severity": "HIGH"}]
    conv = evaluate_convergence(items, round_num=1, prev_fingerprints=set())
    assert conv["done"] is False
    assert conv["new_high_ids"] == ["F-01"]
    assert conv["round"] == 1


def test_round_2_without_new_high_stops_loop():
    """Verify round 2 with only repeated or non-HIGH findings stops loop."""
    prev_fps = {"fp1"}
    # Round 2 has only repeated fp1 and a LOW severity finding
    items = [
        {"id": "F-01", "fingerprint": "fp1", "severity": "HIGH"},
        {"id": "F-02", "fingerprint": "fp2", "severity": "LOW"},
    ]
    conv = evaluate_convergence(items, round_num=2, prev_fingerprints=prev_fps)
    assert conv["done"] is True
    assert conv["reason"] == "no_new_high_findings"
    assert conv["new_high_ids"] == []
    assert conv["repeated_fingerprints"] == ["fp1"]


def test_round_2_with_new_high_continues():
    """Verify round 2 with a new HIGH finding continues loop."""
    prev_fps = {"fp1"}
    items = [
        {"id": "F-01", "fingerprint": "fp1", "severity": "HIGH"},
        {"id": "F-03", "fingerprint": "fp3", "severity": "HIGH"},  # New HIGH!
    ]
    conv = evaluate_convergence(items, round_num=2, prev_fingerprints=prev_fps)
    assert conv["done"] is False
    assert conv["new_high_ids"] == ["F-03"]
    assert conv["repeated_fingerprints"] == ["fp1"]


def test_zero_findings_stops_immediately():
    """Verify zero findings in round stops loop immediately."""
    conv = evaluate_convergence([], round_num=2, prev_fingerprints={"fp1"})
    assert conv["done"] is True
    assert conv["zero_findings"] is True
    assert conv["reason"] == "zero_findings"
