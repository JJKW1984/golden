"""
Debt payoff service: amortization calculations and payoff projections.
Handles splitting payments into principal and interest, tracking debt balances.
"""
from decimal import Decimal, ROUND_HALF_UP


def amortize_month(
    opening_cents: int, interest_rate_bps: int, payment_cents: int
) -> tuple[int, int]:
    """
    Compute principal and interest portions of a debt payment in a single month.

    Args:
        opening_cents: opening balance in cents (INTEGER)
        interest_rate_bps: annual interest rate in basis points (INTEGER)
        payment_cents: amount paid this month in cents (INTEGER)

    Returns:
        (principal_cents, interest_cents) tuple, both INTEGER cents

    Rules:
    - interest = opening * (interest_rate_bps / 10000) / 12, rounded half-up to nearest cent
    - principal = payment - interest (can be 0 if payment ≤ interest)
    - If principal = 0, payment went entirely to interest (edge case flagged)
    """
    # Calculate monthly interest using Decimal to avoid float precision issues
    # interest_cents = opening_cents * interest_rate_bps / 10000 / 12
    calculated_interest = int(
        (
            Decimal(opening_cents)
            * Decimal(interest_rate_bps)
            / Decimal(10000)
            / Decimal(12)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    # Cap interest at payment amount (handles payment ≤ interest case)
    interest_cents = min(calculated_interest, payment_cents)
    principal_cents = payment_cents - interest_cents

    return (principal_cents, interest_cents)
