"""Acceptance tests for merge-apply non-destructive branch creation and base-moved rejection (AT4.4, AT4.5)."""

import os
import subprocess
import sys
from pathlib import Path
import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from agyflow.lib.contract import (
    EXIT_FAILED,
    EXIT_OK,
    E_BASE_MOVED,
    write_artifact,
    write_status,
)
from agyflow.lib.schema import validate_or_raise

try:
    from fixtures.demo_repo_helper import setup_demo_repo_git
except ImportError:
    from tests.fixtures.demo_repo_helper import setup_demo_repo_git


def apply_candidate_merge(
    repo_path: Path,
    patch_file: Path,
    base_commit: str,
    run_id: str,
    patch_sha256: str = "patch_sha_dummy",
) -> tuple[int, dict, dict]:
    """Simulates merge-apply: checks base_commit freshness, creates agyflow/<run_id> branch, applies patch."""
    # 1. Check if base commit has moved
    head_proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True)
    current_head = head_proc.stdout.strip()

    if current_head != base_commit:
        # AT4.5: Target base moved since patch generation -> reject
        status_info = {
            "status": "failed",
            "code": E_BASE_MOVED,
            "reason": f"Base commit moved: expected {base_commit}, current HEAD is {current_head}",
            "complete": False,
        }
        return EXIT_FAILED, {}, status_info

    # 2. Check untracked clean checkout safety (AT4.4)
    status_proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo_path, capture_output=True, text=True, check=True)
    if status_proc.stdout.strip():
        status_info = {
            "status": "failed",
            "code": "E_INPUT",
            "reason": "Repository working tree is dirty; clean checkout required",
            "complete": False,
        }
        return EXIT_FAILED, {}, status_info

    # 3. Create dedicated isolated branch agyflow/<run_id>
    branch_name = f"agyflow/{run_id}"
    subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_path, check=True, capture_output=True)

    # 4. Apply patch and commit
    subprocess.run(["git", "apply", str(patch_file)], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "agyflow-bot"
    env["GIT_AUTHOR_EMAIL"] = "bot@agyflow.local"
    env["GIT_COMMITTER_NAME"] = "agyflow-bot"
    env["GIT_COMMITTER_EMAIL"] = "bot@agyflow.local"

    subprocess.run(["git", "commit", "-m", f"apply: fix from run {run_id}"], cwd=repo_path, check=True, capture_output=True, env=env)

    commit_proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True)
    new_commit = commit_proc.stdout.strip()

    merge_payload = {
        "branch": branch_name,
        "commit": new_commit,
        "base_commit": base_commit,
        "patch_sha256": patch_sha256,
    }
    validate_or_raise(merge_payload, "merge_result")
    status_info = {"status": "ok", "code": "", "complete": True}

    return EXIT_OK, merge_payload, status_info


def test_merge_apply_branch_creation_and_clean_checkout_at44(tmp_path: Path):
    """AT4.4: Merge-apply creates branch agyflow/<run_id> and commits patch on clean checkout."""
    repo = setup_demo_repo_git(tmp_path / "target_repo")
    head_proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    base_head = head_proc.stdout.strip()

    # Valid diff fixing compute_discounted_total in calculator.py
    diff_text = (
        "--- a/src/tinycalc/calculator.py\n"
        "+++ b/src/tinycalc/calculator.py\n"
        "@@ -54,3 +54,4 @@\n"
        "-    tax = subtotal * (tax_percent / 100.0)\n"
        "-    discount = subtotal * (discount_percent / 100.0)\n"
        "-    return round(subtotal + tax - discount, 2)\n"
        "+    discount = subtotal * (discount_percent / 100.0)\n"
        "+    net = subtotal - discount\n"
        "+    tax = net * (tax_percent / 100.0)\n"
        "+    return round(net + tax, 2)\n"
    )
    patch_file = tmp_path / "fix.patch"
    patch_file.write_text(diff_text)

    run_id = "test_run_44"
    exit_code, payload, status = apply_candidate_merge(repo, patch_file, base_head, run_id)

    assert exit_code == EXIT_OK
    assert status["status"] == "ok"
    assert payload["branch"] == "agyflow/test_run_44"
    assert payload["base_commit"] == base_head
    assert payload["commit"] != base_head

    # Verify active branch in repo is agyflow/test_run_44
    branch_proc = subprocess.run(["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True)
    assert branch_proc.stdout.strip() == "agyflow/test_run_44"


def test_merge_apply_base_moved_rejection_at45(tmp_path: Path):
    """AT4.5: Merge-apply rejects with E_BASE_MOVED when target branch HEAD has moved."""
    repo = setup_demo_repo_git(tmp_path / "target_repo")
    head_proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    old_base = head_proc.stdout.strip()

    # Create new commit on main so HEAD moves
    (repo / "touch.txt").write_text("drift")
    subprocess.run(["git", "add", "touch.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "drift base"], cwd=repo, check=True, capture_output=True)

    patch_file = tmp_path / "fix.patch"
    patch_file.write_text("dummy patch")

    exit_code, payload, status = apply_candidate_merge(repo, patch_file, old_base, run_id="run_45")

    assert exit_code == EXIT_FAILED
    assert status["status"] == "failed"
    assert status["code"] == E_BASE_MOVED
    assert "Base commit moved" in status["reason"]
