"""Contract test runner for all 9 M1 agyflow blocks.

Validates that each block adheres strictly to the Section 4 Unix contract:
  --in <dir> --out <dir> --cfg <json>
Exit codes: 0 (ok), 10 (retryable), 20 (failed), 30 (needs_human).
"""

import sys
from pathlib import Path
import pytest

CONTRACT_DIR = Path(__file__).resolve().parent
if str(CONTRACT_DIR) not in sys.path:
    sys.path.insert(0, str(CONTRACT_DIR))

from contract_harness import (
    M1_BLOCK_NAMES,
    find_block_entrypoint,
    check_block_help_contract,
    check_missing_args_contract,
    check_bad_json_cfg_contract,
    check_nonexistent_in_dir_contract,
)


@pytest.mark.parametrize("block_name", M1_BLOCK_NAMES)
def test_m1_block_help(block_name: str):
    """Verify block provides --help and exits 0."""
    entry = find_block_entrypoint(block_name)
    if not entry:
        pytest.skip(f"Block '{block_name}' not yet implemented")
    res = check_block_help_contract(block_name)
    assert res.passed, f"Block {block_name} --help failed: {res.message}"


@pytest.mark.parametrize("block_name", M1_BLOCK_NAMES)
def test_m1_block_missing_args(block_name: str):
    """Verify block rejects invocation with missing arguments."""
    entry = find_block_entrypoint(block_name)
    if not entry:
        pytest.skip(f"Block '{block_name}' not yet implemented")
    res = check_missing_args_contract(block_name)
    assert res.passed, f"Block {block_name} allowed missing args: exit {res.exit_code}"


@pytest.mark.parametrize("block_name", M1_BLOCK_NAMES)
def test_m1_block_bad_json_cfg(block_name: str):
    """Verify block rejects invalid JSON in --cfg with exit code 20."""
    entry = find_block_entrypoint(block_name)
    if not entry:
        pytest.skip(f"Block '{block_name}' not yet implemented")
    res = check_bad_json_cfg_contract(block_name)
    assert res.passed, f"Block {block_name} bad cfg did not exit 20: got {res.exit_code}"


@pytest.mark.parametrize("block_name", M1_BLOCK_NAMES)
def test_m1_block_nonexistent_in_dir(block_name: str):
    """Verify block rejects missing input directory with exit code 20."""
    entry = find_block_entrypoint(block_name)
    if not entry:
        pytest.skip(f"Block '{block_name}' not yet implemented")
    res = check_nonexistent_in_dir_contract(block_name)
    assert res.passed, f"Block {block_name} missing --in did not exit 20: got {res.exit_code}"


def run_contract_suite() -> int:
    """CLI runner printing contract compliance status for all 9 blocks."""
    print("=" * 70)
    print("agyflow Milestone 1: Section 4 Block Contract Validation Suite")
    print("=" * 70)

    total_blocks = len(M1_BLOCK_NAMES)
    implemented_count = 0
    passed_count = 0

    for name in M1_BLOCK_NAMES:
        entry = find_block_entrypoint(name)
        if not entry:
            print(f"[-] {name:20} PENDING (blocks/{name}/run.py not found)")
            continue

        implemented_count += 1
        h_res = check_block_help_contract(name)
        m_res = check_missing_args_contract(name)
        c_res = check_bad_json_cfg_contract(name)
        i_res = check_nonexistent_in_dir_contract(name)

        if h_res.passed and m_res.passed and c_res.passed and i_res.passed:
            passed_count += 1
            print(f"[+] {name:20} PASS (all 4 contract checks satisfied)")
        else:
            failures = [r.test_name for r in (h_res, m_res, c_res, i_res) if not r.passed]
            print(f"[x] {name:20} FAIL (failed: {', '.join(failures)})")

    print("-" * 70)
    print(f"Summary: {implemented_count}/{total_blocks} implemented, {passed_count}/{total_blocks} fully passing.")
    print("=" * 70)
    return 0 if implemented_count == passed_count else 1


if __name__ == "__main__":
    sys.exit(run_contract_suite())
