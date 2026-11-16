"""End-to-end full workflow acceptance test on demo_repo with scripted gates."""

import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from agyflow.cli import _run_engine
from agyflow.engine.pipeline import load_pipeline
from agyflow.engine.state import RunState, load_state, save_state

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

try:
    from fixtures.demo_repo_helper import setup_demo_repo_git
except ImportError:
    from tests.fixtures.demo_repo_helper import setup_demo_repo_git

FIXED_CALC = '''"""Core calculator operations with fixed discount logic."""

def add(a: float, b: float) -> float:
    return a + b

def subtract(a: float, b: float) -> float:
    return a - b

def multiply(a: float, b: float) -> float:
    return a * b

def divide(a: float, b: float) -> float:
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b

def compute_discounted_total(subtotal: float, discount_percent: float, tax_percent: float) -> float:
    if subtotal < 0 or discount_percent < 0 or tax_percent < 0:
        raise ValueError("Inputs must be non-negative")
    if discount_percent > 100:
        raise ValueError("Discount cannot exceed 100%")
    net = subtotal * (1.0 - discount_percent / 100.0)
    return round(net * (1.0 + tax_percent / 100.0), 2)
'''


def _create_fixtures(fix_dir: Path) -> None:
    fix_dir.mkdir(parents=True, exist_ok=True)
    (fix_dir / "context_builder.json").write_text(json.dumps({
        "built_at_commit": "HEAD",
        "repo_summary": "Tinycalc math library with discount defect",
        "modules": [{"path": "src/tinycalc/calculator.py", "purpose": "Calc", "key_symbols": ["compute_discounted_total"], "depends_on": []}],
        "data_flow": ["subtotal -> discount -> tax -> total"],
        "key_equations_or_invariants": [{"where": "calculator.py:35", "statement": "Discount before tax"}],
        "entry_points": [{"cmd": "pytest", "purpose": "run tests"}],
        "test_layout": {"runner": "pytest", "paths": ["tests"], "notes": "unit tests"},
        "known_risks": ["compound precision"],
        "glossary": [{"term": "subtotal", "meaning": "net amount"}]
    }))
    repro = "import sys\nfrom tinycalc.calculator import compute_discounted_total\n" \
            "if compute_discounted_total(100.0, 10.0, 10.0) != 99.0:\n" \
            "    print('EVIDENCE: Defect reproduced!')\n    sys.exit(0)\nsys.exit(1)"
    (fix_dir / "analyst.json").write_text(json.dumps({
        "round": 1, "findings": [{
            "id": "F-001", "severity": "HIGH", "category": "logic", "claim": "Tax applied before discount",
            "claim_key": "tax_order",
            "evidence": {"file": "src/tinycalc/calculator.py", "line": 55, "end_line": 57,
                         "quote": "tax = subtotal * (tax_percent / 100.0)\n    discount = subtotal * (discount_percent / 100.0)\n    return round(subtotal + tax - discount, 2)"},
            "repro": {"kind": "python_snippet", "code": repro},
            "expected_if_true": "Discount before tax", "confidence": "high"
        }],
        "coverage": {"files_read": ["src/tinycalc/calculator.py"], "files_skipped": [], "notes": "Inspected calculator.py"}
    }))
    (fix_dir / "planner.json").write_text(json.dumps({
        "revision": 1, "goal": "Fix discount calculation ordering",
        "approaches": [{
            "id": "A", "title": "Discount net before tax", "differs_on": "algorithm",
            "differs_detail": "Compute net subtotal first", "summary": "Apply discount then tax",
            "steps": ["edit calc.py", "run tests"], "files_to_change": ["src/tinycalc/calculator.py"],
            "max_files_changed": 1, "risks": [{"risk": "precision", "mitigation": "round"}],
            "success_criteria": ["pytest passes"], "addresses": ["F-001"], "effort": "S"
        }], "unknowns": [], "not_doing": []
    }))
    (fix_dir / "actor.json").write_text(json.dumps({
        "edits": [{"path": "src/tinycalc/calculator.py", "content": FIXED_CALC}],
        "payload": {
            "approach_id": "A", "status": "done",
            "summary": "Fixed discount calculation", "files_changed": ["src/tinycalc/calculator.py"],
            "commands_run": [{"cmd": "pytest", "exit_code": 0}],
            "smoke": {"ran": True, "exit_code": 0, "notes": "ok"},
            "deviations_from_plan": [], "open_issues": []
        }
    }))
    (fix_dir / "reviewer.json").write_text(json.dumps({
        "verdict": "approve", "issues": [], "residual_risks": []
    }))


def test_full_workflow_end_to_end(tmp_path: Path):
    """Executes full pipeline on demo_repo with scripted gate approvals and verifies merge branch."""
    repo_dir = setup_demo_repo_git(tmp_path / "repo")
    (repo_dir / ".gitignore").write_text(".flow/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "ignore flow"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL)

    flow_dir = repo_dir / ".flow"
    (flow_dir / "runs").mkdir(parents=True, exist_ok=True)
    (flow_dir / "decisions.md").write_text("# Decisions\n")

    orig_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()

    proj_cfg = {
        "project": {
            "name": "tinycalc", "allow_dirty": False, "smoke_cmd": "python3.11 -m pytest",
            "test_cmd": "python3.11 -m pytest", "test_paths": ["tests"], "test_timeout_s": 60,
            "approaches_max": 2, "max_attempts": 1, "max_parallel": 1, "judge_repeat": 1,
            "noise_margin": 0.05, "analyst_max_findings": 3, "weights": {"tests": 1.0},
            "metrics": ["tests"], "actor_skip_permissions": True, "actor_timeout_s": 60,
            "judge_timeout_s": 60, "holdout_dir": "", "forbidden": []
        },
        "blocks": {"agent-call": "agent-call-fake"},
        "budget": {"minutes": 10, "agent_calls": 50}
    }
    (flow_dir / "project.toml").write_text(
        '[project]\nname = "tinycalc"\nallow_dirty = false\nsmoke_cmd = "python3.11 -m pytest"\n'
        'test_cmd = "python3.11 -m pytest"\ntest_paths = ["tests"]\ntest_timeout_s = 60\n'
        'approaches_max = 2\nmax_attempts = 1\nmax_parallel = 1\njudge_repeat = 1\nnoise_margin = 0.05\n'
        '[blocks]\nagent-call = "agent-call-fake"\n'
        '[budget]\nminutes = 10\nagent_calls = 50\n'
    )

    fix_dir = tmp_path / "fixtures"
    _create_fixtures(fix_dir)
    os.environ["AGYFLOW_FIXTURES_DIR"] = str(fix_dir)

    pipeline_path = Path(__file__).resolve().parent.parent.parent / "pipelines" / "full.yaml"
    tree = load_pipeline(str(pipeline_path), proj_cfg, {}, repo_dir)

    run_id = "test-e2e-run"
    run_dir = flow_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(run_id=run_id, pipeline=tree.name)
    save_state(run_dir, state)

    gates_dir = run_dir / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)

    approvals = {
        "pln_gate1": {"gate": "findings", "decision": "approve", "accept": ["F-001"], "note": "Approve F-001"},
        "pln_gate2": {"gate": "plan", "decision": "approve", "picks": ["A"], "note": "Approve Approach A"},
        "gate3": {"gate": "candidate", "decision": "approve", "picks": ["A"], "note": "Approve Candidate A"}
    }

    for _ in range(6):
        res = _run_engine(run_dir, flow_dir, tree, state, proj_cfg, "subprocess", wait_gate=False)
        if res == 0:
            break
        assert res == 30, f"Unexpected non-gate exit code: {res}"
        st = load_state(run_dir)
        gate_name = st.pending.get("gate")
        assert gate_name in approvals, f"Unknown pending gate: {gate_name}"
        (gates_dir / f"{gate_name}.approval.json").write_text(json.dumps(approvals[gate_name]))
        state = load_state(run_dir)

    final_st = load_state(run_dir)
    assert final_st.status == "done"

    curr_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()
    assert curr_head == orig_head
    st_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()
    assert st_res == ""

    branch_name = f"agyflow/{run_id}"
    b_check = subprocess.run(["git", "rev-parse", "--verify", branch_name], cwd=repo_dir, capture_output=True, text=True, check=True)
    assert b_check.returncode == 0
    show_res = subprocess.run(["git", "show", branch_name], cwd=repo_dir, capture_output=True, text=True, check=True).stdout
    assert "compute_discounted_total" in show_res

    dec_text = (flow_dir / "decisions.md").read_text(encoding="utf-8")
    assert "Approve Candidate A" in dec_text

    rep_path = run_dir / "steps" / "report" / "out" / "REPORT.md"
    assert rep_path.is_file()
    assert "COMPLETED" in rep_path.read_text(encoding="utf-8")
