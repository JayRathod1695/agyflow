# demo_repo (tinycalc)

Tiny target repository for `agyflow` test runs.

## Features
- Standard Python package with `src/tinycalc` and `tests/test_calculator.py`.
- Baseline test suite passes 100%.
- Contains 1 seeded logic defect in `compute_discounted_total`:
  - Fails when both discount > 0 and tax > 0 are applied.
  - Reproducer available in `defect_reproducer.py.example`.
