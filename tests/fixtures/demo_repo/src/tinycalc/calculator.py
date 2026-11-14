"""Core calculator operations with a seeded logic defect."""


def add(a: float, b: float) -> float:
    """Return sum of a and b."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Return difference of a and b."""
    return a - b


def multiply(a: float, b: float) -> float:
    """Return product of a and b."""
    return a * b


def divide(a: float, b: float) -> float:
    """Return quotient of a and b.

    Raises:
        ZeroDivisionError: If b is zero.
    """
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b


def compute_discounted_total(
    subtotal: float, discount_percent: float, tax_percent: float
) -> float:
    """Compute final order total with discount and sales tax.

    Business Rule:
        Discount must reduce the subtotal first, and tax is applied
        only to the net discounted amount:
            net_subtotal = subtotal * (1 - discount_percent / 100)
            final_total = net_subtotal * (1 + tax_percent / 100)

    SEEDED DEFECT:
        Computes tax on raw subtotal and subtracts nominal discount
        without compounding:
            tax = subtotal * (tax_percent / 100.0)
            discount = subtotal * (discount_percent / 100.0)
            total = subtotal + tax - discount
        Latent when tax == 0 or discount == 0.
    """
    if subtotal < 0 or discount_percent < 0 or tax_percent < 0:
        raise ValueError("Inputs must be non-negative")
    if discount_percent > 100:
        raise ValueError("Discount cannot exceed 100%")

    # Seeded Defect: Tax applied to pre-discount subtotal
    tax = subtotal * (tax_percent / 100.0)
    discount = subtotal * (discount_percent / 100.0)
    return round(subtotal + tax - discount, 2)
