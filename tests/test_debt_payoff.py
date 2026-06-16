import pytest
from finapp.services.debt_payoff import amortize_month


def test_amortize_month_principal_and_interest():
    """
    Given: opening balance 10000 cents, interest_rate_bps=1200 (12%), payment=300 cents
    When: amortize_month is called
    Then: returns principal_cents, interest_cents where:
    - interest = opening * (rate_bps / 10000) / 12, rounded half-up
    - principal = payment - interest

    Expected: interest = 10000 * 1200 / 10000 / 12 = 100 cents
    principal = 300 - 100 = 200 cents
    """
    opening_cents = 10000
    interest_rate_bps = 1200  # 12% annual = 1% monthly = 100 bps
    payment_cents = 300

    principal, interest = amortize_month(opening_cents, interest_rate_bps, payment_cents)

    assert interest == 100, f"Expected interest=100 cents, got {interest}"
    assert principal == 200, f"Expected principal=200 cents, got {principal}"


def test_amortize_month_payment_less_than_interest():
    """
    Edge case: when payment is less than calculated interest.

    Given: opening balance 100000 cents ($1000), interest_rate_bps=2000 (20% annual)
    monthly interest = 100000 * 2000 / 10000 / 12 ≈ 333 cents
    If payment is only 50 cents, then principal should be 0.

    When: amortize_month is called with payment < interest
    Then: principal=0, interest=50 (capped at payment)
    """
    opening_cents = 100000  # $1000
    interest_rate_bps = 2000  # 20% annual
    payment_cents = 50  # Much less than monthly interest

    principal, interest = amortize_month(opening_cents, interest_rate_bps, payment_cents)

    assert principal == 0, f"Expected principal=0 (payment ≤ interest), got {principal}"
    assert interest == 50, f"Expected interest=50 (capped at payment), got {interest}"
