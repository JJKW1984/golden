import pytest
from finapp.services.debt_payoff import amortize_month, project_debt_payoff


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


def test_project_minimum_payment():
    """
    Given: opening balance 50000 cents ($500), interest_rate_bps=1200 (12%), minimum_payment=200 cents
    When: project_debt_payoff(..., scenario='minimum') is called
    Then: returns (months_to_payoff, total_interest_cents, payoff_date_str, payment_gte_interest_flag)

    The projection should:
    - Calculate number of months until balance reaches 0
    - Sum total interest paid across all months
    - Return payoff date as "YYYY-MM-DD" or "Never" if unpayable
    - Flag whether payment always covered interest (payment_gte_interest=True)
    """
    opening_balance = 50000
    interest_rate_bps = 1200
    minimum_payment = 200

    result = project_debt_payoff(
        opening_balance_cents=opening_balance,
        interest_rate_bps=interest_rate_bps,
        monthly_payment_cents=minimum_payment,
        scenario="minimum",
    )

    months, total_interest, payoff_date, payment_gte_interest = result

    # Rough check: at 1% monthly interest, minimum payment scenario should take many months
    # Interest per month ≈ 50000 * 1200 / 10000 / 12 = 50 cents
    # Payment is 200 cents, so principal reduction ≈ 150 cents/month
    # Rough estimate: 50000 / 150 ≈ 333 months
    assert months > 100, f"Expected many months (>100), got {months}"
    assert total_interest > 0, f"Expected positive interest, got {total_interest}"
    assert isinstance(payoff_date, str), f"payoff_date should be string, got {type(payoff_date)}"
    assert isinstance(payment_gte_interest, bool), f"payment_gte_interest should be bool, got {type(payment_gte_interest)}"


def test_project_all_scenarios():
    """
    Given: opening balance 100000 cents, interest_rate_bps=1200, minimum=1500, current_allocation=2000
    When: project_debt_payoff is called with 3 scenarios (minimum, +50, +100)
    Then: payoff months should be: minimum > +50 > +100 (more payment = faster payoff)
    And: total interest should follow same order (more payment = less total interest)

    Note: Monthly interest on $1000 at 12% annual = 1000 * 0.12 / 12 = $10 (1000 cents)
    So minimum payment of $15 (1500 cents) comfortably exceeds interest and makes progress.
    """
    opening = 100000
    interest_rate_bps = 1200
    minimum = 1500
    current = 2000

    result_min = project_debt_payoff(opening, interest_rate_bps, minimum, "minimum")
    result_50 = project_debt_payoff(opening, interest_rate_bps, current + 50, "+50")
    result_100 = project_debt_payoff(opening, interest_rate_bps, current + 100, "+100")

    months_min, interest_min, _, _ = result_min
    months_50, interest_50, _, _ = result_50
    months_100, interest_100, _, _ = result_100

    # More payment = fewer months to payoff
    assert months_min > months_50, f"minimum ({months_min}) should > +50 ({months_50})"
    assert months_50 > months_100, f"+50 ({months_50}) should > +100 ({months_100})"

    # More payment = less total interest
    assert interest_min > interest_50, f"interest minimum ({interest_min}) should > +50 ({interest_50})"
    assert interest_50 > interest_100, f"interest +50 ({interest_50}) should > +100 ({interest_100})"
