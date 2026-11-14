"""Baseline test suite for tinycalc - all tests pass."""

import pytest
from tinycalc.calculator import (
    add,
    subtract,
    multiply,
    divide,
    compute_discounted_total,
)


def test_basic_arithmetic():
    assert add(10, 5) == 15
    assert subtract(10, 4) == 6
    assert multiply(3, 7) == 21
    assert divide(20, 4) == 5.0


def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(5, 0)


def test_discount_only():
    """Validates discount calculation when tax is zero."""
    assert compute_discounted_total(100.0, 10.0, 0.0) == 90.0
    assert compute_discounted_total(200.0, 25.0, 0.0) == 150.0


def test_tax_only():
    """Validates tax calculation when discount is zero."""
    assert compute_discounted_total(100.0, 0.0, 8.0) == 108.0
    assert compute_discounted_total(50.0, 0.0, 10.0) == 55.0


def test_zero_discount_and_tax():
    """Validates baseline with zero adjustments."""
    assert compute_discounted_total(100.0, 0.0, 0.0) == 100.0


def test_input_validation():
    with pytest.raises(ValueError):
        compute_discounted_total(-10.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        compute_discounted_total(100.0, 150.0, 0.0)
