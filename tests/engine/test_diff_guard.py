"""Acceptance tests for diff-guard policy violations (AT4.2)."""

import pytest
from agyflow.lib.schema import validate_or_raise


def evaluate_diff_guard(
    patch_meta: dict,
    allow_paths: list[str] | None = None,
    locked_paths: list[str] | None = None,
    max_insertions: int = 500,
    max_deletions: int = 500,
) -> dict:
    """Core diff guard logic validating patch metadata against safety policies."""
    violations: list[dict] = []
    allowed = allow_paths or ["src/**"]
    locked = locked_paths or ["tests/**", ".flow/**", "pyproject.toml"]

    # 1. Check binary files
    for bf in patch_meta.get("binary_files", []):
        violations.append({
            "code": "BINARY_FORBIDDEN",
            "detail": f"Binary file modification forbidden: {bf}",
        })

    # 2. Check total budget limits
    if patch_meta.get("insertions", 0) > max_insertions:
        violations.append({
            "code": "INSERTION_LIMIT_EXCEEDED",
            "detail": f"Insertions ({patch_meta['insertions']}) exceed limit of {max_insertions}",
        })
    if patch_meta.get("deletions", 0) > max_deletions:
        violations.append({
            "code": "DELETION_LIMIT_EXCEEDED",
            "detail": f"Deletions ({patch_meta['deletions']}) exceed limit of {max_deletions}",
        })

    # 3. Check path allowlist and locked files
    import fnmatch
    for f in patch_meta.get("files", []):
        fpath = f["path"]
        # Check if matches locked
        if any(fnmatch.fnmatch(fpath, pat) for pat in locked):
            violations.append({
                "code": "LOCKED_FILE_MODIFIED",
                "detail": f"Attempted modification of locked file: {fpath}",
            })
            continue

        # Check if inside allowlist
        if not any(fnmatch.fnmatch(fpath, pat) for pat in allowed):
            violations.append({
                "code": "FILE_OUTSIDE_ALLOWLIST",
                "detail": f"File path outside allowlist: {fpath}",
            })

    result = {
        "ok": len(violations) == 0,
        "violations": violations,
    }
    validate_or_raise(result, "guard")
    return result


def test_diff_guard_clean_patch_passes_at42():
    """AT4.2: Clean patch modifying allowed source file within budget passes cleanly."""
    patch_meta = {
        "approach_id": "app_01",
        "base_commit": "abcdef1234567890abcdef1234567890abcdef12",
        "files": [{"path": "src/tinycalc/calculator.py", "insertions": 5, "deletions": 2, "status": "M"}],
        "insertions": 5,
        "deletions": 2,
        "patch_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "binary_files": [],
    }
    validate_or_raise(patch_meta, "patch_meta")

    res = evaluate_diff_guard(patch_meta)
    assert res["ok"] is True
    assert res["violations"] == []


def test_diff_guard_file_outside_allowlist_violation_at42():
    """AT4.2: Patch modifying file outside allowlist (e.g. Makefile or deploy) is flagged."""
    patch_meta = {
        "approach_id": "app_02",
        "base_commit": "abcdef1234567890abcdef1234567890abcdef12",
        "files": [
            {"path": "src/tinycalc/calculator.py", "insertions": 2, "deletions": 1, "status": "M"},
            {"path": "scripts/deploy.sh", "insertions": 10, "deletions": 0, "status": "A"},
        ],
        "insertions": 12,
        "deletions": 1,
        "patch_sha256": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        "binary_files": [],
    }
    res = evaluate_diff_guard(patch_meta, allow_paths=["src/**"])
    assert res["ok"] is False
    codes = [v["code"] for v in res["violations"]]
    assert "FILE_OUTSIDE_ALLOWLIST" in codes


def test_diff_guard_locked_test_file_violation_at42():
    """AT4.2: Patch attempting to modify locked test suite is strictly blocked."""
    patch_meta = {
        "approach_id": "app_03",
        "base_commit": "abcdef1234567890abcdef1234567890abcdef12",
        "files": [
            {"path": "tests/test_calculator.py", "insertions": 1, "deletions": 10, "status": "M"},
        ],
        "insertions": 1,
        "deletions": 10,
        "patch_sha256": "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
        "binary_files": [],
    }
    res = evaluate_diff_guard(patch_meta, locked_paths=["tests/**"])
    assert res["ok"] is False
    codes = [v["code"] for v in res["violations"]]
    assert "LOCKED_FILE_MODIFIED" in codes


def test_diff_guard_budget_and_binary_violations_at42():
    """AT4.2: Patches with binary files or exceeding insertion limits are blocked."""
    patch_meta = {
        "approach_id": "app_04",
        "base_commit": "abcdef1234567890abcdef1234567890abcdef12",
        "files": [
            {"path": "src/tinycalc/large.py", "insertions": 800, "deletions": 0, "status": "A"},
        ],
        "insertions": 800,
        "deletions": 0,
        "patch_sha256": "abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdef",
        "binary_files": ["assets/logo.png"],
    }
    res = evaluate_diff_guard(patch_meta, max_insertions=500)
    assert res["ok"] is False
    codes = [v["code"] for v in res["violations"]]
    assert "INSERTION_LIMIT_EXCEEDED" in codes
    assert "BINARY_FORBIDDEN" in codes
