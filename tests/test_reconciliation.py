import pytest
from datetime import datetime, date
from finapp.models import (
    Account,
    BudgetPeriod,
    BudgetCategory,
    Transaction,
    DebtAccount,
    SavingsGoal,
    BudgetAllocation,
)
from finapp.services.reconciliation import (
    compute_budget_spent_cents,
    compute_budget_income_received_cents,
    compute_debt_balance_cents,
    compute_savings_goal_balance_cents,
    recompute_balances,
)
from finapp.services.seeds import seed_default_categories


@pytest.fixture
def populated_account(db, account):
    """Create an account with a budget period, categories, and some transactions."""
    # Seed default categories
    seed_default_categories(db, account.id)

    # Create a budget period
    period = BudgetPeriod(
        account_id=account.id,
        year=2026,
        month=6,
        income_received_cents=0,
        status="active",
    )
    db.add(period)
    db.commit()

    return account, period


def test_compute_budget_income_received_cents(db, populated_account):
    """Test computing income received in a period."""
    account, period = populated_account

    # Get Income category
    income_cat = db.query(BudgetCategory).filter_by(
        account_id=account.id, kind="income"
    ).first()

    # Create an income transaction
    txn = Transaction(
        account_id=account.id,
        period_id=period.id,
        date=date(2026, 6, 1),
        amount_cents=100000,  # $1000
        direction="in",
        category_id=income_cat.id,
        is_deleted=False,
    )
    db.add(txn)
    db.commit()

    # Compute and verify
    derived = compute_budget_income_received_cents(db, account.id, period.id)
    assert derived == 100000


def test_compute_budget_spent_cents(db, populated_account):
    """Test computing spending in a category for a period."""
    account, period = populated_account

    # Get Food category
    food_cat = db.query(BudgetCategory).filter_by(
        account_id=account.id, name="Food"
    ).first()

    # Create expense transactions
    for i in range(3):
        txn = Transaction(
            account_id=account.id,
            period_id=period.id,
            date=date(2026, 6, 1 + i),
            amount_cents=5000 + i * 1000,  # $50, $60, $70
            direction="out",
            category_id=food_cat.id,
            is_deleted=False,
        )
        db.add(txn)
    db.commit()

    # Compute and verify: $50 + $60 + $70 = $180 = 18000 cents
    derived = compute_budget_spent_cents(db, account.id, period.id, food_cat.id)
    assert derived == 18000


def test_compute_budget_spent_excludes_deleted(db, populated_account):
    """Test that deleted transactions are excluded from spent computation."""
    account, period = populated_account

    food_cat = db.query(BudgetCategory).filter_by(
        account_id=account.id, name="Food"
    ).first()

    # Create two transactions, mark one as deleted
    txn1 = Transaction(
        account_id=account.id,
        period_id=period.id,
        date=date(2026, 6, 1),
        amount_cents=5000,
        direction="out",
        category_id=food_cat.id,
        is_deleted=False,
    )
    txn2 = Transaction(
        account_id=account.id,
        period_id=period.id,
        date=date(2026, 6, 2),
        amount_cents=3000,
        direction="out",
        category_id=food_cat.id,
        is_deleted=True,  # Deleted
    )
    db.add(txn1)
    db.add(txn2)
    db.commit()

    # Compute: should only count txn1 ($50, not $80)
    derived = compute_budget_spent_cents(db, account.id, period.id, food_cat.id)
    assert derived == 5000


def test_compute_debt_balance(db, populated_account):
    """Test computing debt balance from opening balance and principal payments."""
    account, period = populated_account

    # Create a debt account with $10,000 opening balance
    debt = DebtAccount(
        account_id=account.id,
        name="Credit Card",
        opening_balance_cents=1000000,  # $10,000
        cached_balance_cents=1000000,
        interest_rate_bps=2199,  # 21.99%
        minimum_payment_cents=25000,
    )
    db.add(debt)
    db.commit()

    # Create a debt payment: $500 principal, $100 interest
    payment = Transaction(
        account_id=account.id,
        period_id=period.id,
        date=date(2026, 6, 1),
        amount_cents=60000,  # $600 total
        direction="out",
        link_type="debt",
        link_id=debt.id,
        principal_cents=50000,  # $500 principal
        interest_cents=10000,   # $100 interest
        is_deleted=False,
    )
    db.add(payment)
    db.commit()

    # Compute: $10,000 - $500 = $9,500
    derived = compute_debt_balance_cents(db, account.id, debt.id)
    assert derived == 950000


def test_compute_savings_goal_balance(db, populated_account):
    """Test computing savings goal balance from contributions and withdrawals."""
    account, period = populated_account

    # Create a savings goal with $1,000 opening balance
    goal = SavingsGoal(
        account_id=account.id,
        name="Emergency Fund",
        goal_type="emergency_fund",
        target_cents=500000,  # $5,000
        opening_balance_cents=100000,  # $1,000
        cached_balance_cents=100000,
    )
    db.add(goal)
    db.commit()

    # Create contributions
    contrib1 = Transaction(
        account_id=account.id,
        period_id=period.id,
        date=date(2026, 6, 1),
        amount_cents=50000,  # $500 out
        direction="out",
        link_type="savings",
        link_id=goal.id,
        is_deleted=False,
    )
    contrib2 = Transaction(
        account_id=account.id,
        period_id=period.id,
        date=date(2026, 6, 5),
        amount_cents=25000,  # $250 out
        direction="out",
        link_type="savings",
        link_id=goal.id,
        is_deleted=False,
    )
    db.add(contrib1)
    db.add(contrib2)
    db.commit()

    # Compute: $1,000 + $500 + $250 = $1,750
    derived = compute_savings_goal_balance_cents(db, account.id, goal.id)
    assert derived == 175000


def test_recompute_balances_no_drift(db, populated_account):
    """Test that recompute_balances finds no drift when caches are correct."""
    account, period = populated_account

    report = recompute_balances(db, account.id)
    assert report["total_drift"] == 0
    assert len(report["drifts"]) == 0


def test_recompute_balances_detects_drift(db, populated_account):
    """Test that recompute_balances detects drift in cached values."""
    account, period = populated_account

    # Create an income transaction
    income_cat = db.query(BudgetCategory).filter_by(
        account_id=account.id, kind="income"
    ).first()
    txn = Transaction(
        account_id=account.id,
        period_id=period.id,
        date=date(2026, 6, 1),
        amount_cents=100000,  # $1,000
        direction="in",
        category_id=income_cat.id,
        is_deleted=False,
    )
    db.add(txn)
    db.commit()

    # Now the period has income_received_cents=0 but derived should be 100000
    report = recompute_balances(db, account.id)

    # Should detect drift
    assert report["total_drift"] == 1
    assert len(report["drifts"]) == 1
    drift = report["drifts"][0]
    assert drift["table"] == "BudgetPeriod"
    assert drift["field"] == "income_received_cents"
    assert drift["cached"] == 0
    assert drift["derived"] == 100000
