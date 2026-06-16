"""
Debt payoff service: amortization calculations and payoff projections.
Handles splitting payments into principal and interest, tracking debt balances.
"""
from datetime import datetime, timedelta
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


def project_debt_payoff(
    opening_balance_cents: int,
    interest_rate_bps: int,
    monthly_payment_cents: int,
    scenario: str = "minimum",
) -> tuple[int, int, str, bool]:
    """
    Project debt payoff timeline under a fixed monthly payment.

    Args:
        opening_balance_cents: opening balance in cents (INTEGER)
        interest_rate_bps: annual interest rate in basis points (INTEGER)
        monthly_payment_cents: fixed monthly payment in cents (INTEGER)
        scenario: label for display ('minimum', 'current', '+50', '+100')

    Returns:
        (months_to_payoff, total_interest_cents, payoff_date_str, payment_gte_interest_flag)
        - months_to_payoff: number of months until balance reaches 0
        - total_interest_cents: sum of all interest paid
        - payoff_date_str: "YYYY-MM-DD" or "Never" if balance never reaches 0
        - payment_gte_interest_flag: True if payment always covered interest each month, False if ever (payment ≤ interest)

    Rules:
    - Iterates month by month, applying amortize_month() each iteration
    - Stops when balance <= 0 or max 1200 months (safety limit)
    - Flags if payment ≤ interest any month (payment_gte_interest_flag = False)
    """
    balance = opening_balance_cents
    total_interest = 0
    months = 0
    payment_always_gte_interest = True
    today = datetime.now()

    max_months = 1200  # 100 years safety limit

    while balance > 0 and months < max_months:
        principal, interest = amortize_month(balance, interest_rate_bps, monthly_payment_cents)

        # Flag if principal becomes 0 (payment ≤ interest)
        if principal == 0:
            payment_always_gte_interest = False

        balance -= principal
        total_interest += interest
        months += 1

    # Calculate payoff date or "Never"
    if balance > 0:
        payoff_date_str = "Never"
    else:
        payoff_date = today + timedelta(days=30 * months)
        payoff_date_str = payoff_date.strftime("%Y-%m-%d")

    return (months, total_interest, payoff_date_str, payment_always_gte_interest)
