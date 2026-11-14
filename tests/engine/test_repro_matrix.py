"""Acceptance tests for reproduction evaluation matrix and verified schema conformance."""

from agyflow.lib.schema import validate_or_raise


def evaluate_finding_repro(finding: dict, repro_exec_fn) -> dict:
    """Core verification loop repro evaluator evaluating finding into verified item."""
    repro_cmd = finding.get("repro_cmd")
    if not repro_cmd:
        return {
            "id": finding["id"],
            "fingerprint": finding["fingerprint"],
            "severity": finding["severity"],
            "category": finding.get("category", "logic"),
            "claim": finding["claim"],
            "claim_key": finding.get("claim_key", "default_key"),
            "evidence": finding["evidence"],
            "status": "UNRUNNABLE",
            "repro_result": None,
            "argument": finding.get("argument", "Reasoning only, no runnable repro"),
            "first_round": 1,
            "last_round": 1,
        }

    exec_res = repro_exec_fn(repro_cmd)
    runs = [{
        "exit_code": exec_res["exit_code"],
        "evidence_line": exec_res.get("evidence_line"),
        "timed_out": exec_res.get("timed_out", False),
        "duration_s": exec_res.get("duration_s", 0.1),
    }]

    if exec_res.get("timed_out") or exec_res["exit_code"] not in (0, 1):
        status = "ERROR"
    elif exec_res["exit_code"] == 0 and exec_res.get("evidence_line"):
        status = "CONFIRMED"
    else:
        status = "DROPPED"

    return {
        "id": finding["id"],
        "fingerprint": finding["fingerprint"],
        "severity": finding["severity"],
        "category": finding.get("category", "logic"),
        "claim": finding["claim"],
        "claim_key": finding.get("claim_key", "default_key"),
        "evidence": finding["evidence"],
        "status": status,
        "repro_result": {"runs": runs, "stdout_tail": exec_res.get("stdout_tail", "")},
        "argument": None,
        "first_round": 1,
        "last_round": 1,
    }


def make_finding(fid: str, cmd: list[str] | None = None) -> dict:
    return {
        "id": fid,
        "fingerprint": f"fp_{fid}",
        "severity": "HIGH",
        "category": "math_defect",
        "claim": f"Claim for {fid}",
        "claim_key": f"key_{fid}",
        "evidence": {
            "file": "src/tinycalc/calculator.py",
            "line": 42,
            "quote": "total = subtotal + tax - discount",
        },
        "repro_cmd": cmd,
    }


def test_repro_exit_0_with_evidence_confirmed():
    """Verify exit 0 with matching evidence confirms finding."""
    finding = make_finding("F-01", ["python", "repro.py"])
    res = evaluate_finding_repro(
        finding,
        lambda _: {"exit_code": 0, "evidence_line": "AssertionError: 100.0 != 99.0", "stdout_tail": "output"},
    )
    assert res["status"] == "CONFIRMED"
    payload = {"round": 1, "items": [res]}
    validate_or_raise(payload, "verified")


def test_repro_exit_1_dropped():
    """Verify exit 1 (assertion failure / not reproduced) drops finding."""
    finding = make_finding("F-02", ["python", "repro.py"])
    res = evaluate_finding_repro(
        finding,
        lambda _: {"exit_code": 1, "evidence_line": None, "stdout_tail": "test passed, no defect"},
    )
    assert res["status"] == "DROPPED"
    payload = {"round": 1, "items": [res]}
    validate_or_raise(payload, "verified")


def test_repro_crash_or_timeout_error():
    """Verify timeout or execution crash produces ERROR status."""
    finding_timeout = make_finding("F-03", ["python", "repro.py"])
    res_to = evaluate_finding_repro(
        finding_timeout,
        lambda _: {"exit_code": None, "timed_out": True, "duration_s": 30.0},
    )
    assert res_to["status"] == "ERROR"

    finding_crash = make_finding("F-04", ["python", "repro.py"])
    res_crash = evaluate_finding_repro(
        finding_crash,
        lambda _: {"exit_code": 139, "timed_out": False, "duration_s": 0.2},
    )
    assert res_crash["status"] == "ERROR"

    payload = {"round": 1, "items": [res_to, res_crash]}
    validate_or_raise(payload, "verified")


def test_repro_reasoning_only_unrunnable():
    """Verify finding without executable repro command produces UNRUNNABLE."""
    finding = make_finding("F-05", cmd=None)
    finding["argument"] = "Analysis deduced bug by visual inspection."
    res = evaluate_finding_repro(finding, None)
    assert res["status"] == "UNRUNNABLE"
    assert res["repro_result"] is None
    payload = {"round": 1, "items": [res]}
    validate_or_raise(payload, "verified")
