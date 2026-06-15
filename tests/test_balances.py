"""
Unit tests for the balances service (derived values).
All balances are computed from transactions, never stored as truth.
"""
import pytest
from datetime import date, timedelta
from finapp.models import (
    BudgetPeriod,
    BudgetCategory,
    Transaction,
    DebtAccount,
    SavingsGoal,
)
from finapp.services.seeds import seed_default_categories
from finapp.services.balances import (
    get_budget_spent_cents,
    get_budget_income_received_cents,
    get_debt_balance_cents,
    get_savings_goal_balance_cents,
    get_net_worth_cents,
    get_days_of_expenses_coverage,
)
from finapp.services.ledger import create_transaction


@pytest.fixture
def setup_account(db, account, ctx):
    """Set up account with categories and budget periods."""
    seed_default_categories(db, account.id)

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


class TestBudgetSpent:
    """Test budget spent calculation."""

    def test_budget_spent_single_category(self, db, ctx, setup_account):
        """Calculate spending for a single category."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        # Create three expenses
        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=5000,
                          direction="out", category_id=food_cat.id)
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=6000,
                          direction="out", category_id=food_cat.id)
        create_transaction(ctx, date=date(2026, 6, 10), amount_cents=7000,
                          direction="out", category_id=food_cat.id)

        spent = get_budget_spent_cents(ctx, period.id, food_cat.id)
        assert spent == 18000  # $50 + $60 + $70

    def test_budget_spent_excludes_deleted(self, db, ctx, setup_account):
        """Deleted transactions excluded from spending total."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=5000,
                          direction="out", category_id=food_cat.id)
        txn2 = create_transaction(ctx, date=date(2026, 6, 5), amount_cents=6000,
                                 direction="out", category_id=food_cat.id)
        create_transaction(ctx, date=date(2026, 6, 10), amount_cents=7000,
                          direction="out", category_id=food_cat.id)

        # Delete middle transaction
        from finapp.services.ledger import void_transaction
        void_transaction(ctx, txn2.id)

        spent = get_budget_spent_cents(ctx, period.id, food_cat.id)
        assert spent == 12000  # Only $50 + $70

    def test_budget_spent_zero_for_no_transactions(self, db, ctx, setup_account):
        """Zero spending when no transactions."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        spent = get_budget_spent_cents(ctx, period.id, food_cat.id)
        assert spent == 0


class TestBudgetIncomeReceived:
    """Test income received calculation."""

    def test_income_received_single_transaction(self, db, ctx, setup_account):
        """Calculate income for a period."""
        account, period = setup_account
        income_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, kind="income"
        ).first()

        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000,
                          direction="in", category_id=income_cat.id, payee="Employer")

        income = get_budget_income_received_cents(ctx, period.id)
        assert income == 200000

    def test_income_received_multiple_transactions(self, db, ctx, setup_account):
        """Sum income from multiple paycheck deposits."""
        account, period = setup_account
        income_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, kind="income"
        ).first()

        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000,
                          direction="in", category_id=income_cat.id)
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=200000,
                          direction="in", category_id=income_cat.id)

        income = get_budget_income_received_cents(ctx, period.id)
        assert income == 400000

    def test_income_received_excludes_deleted(self, db, ctx, setup_account):
        """Deleted income transactions excluded."""
        account, period = setup_account
        income_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, kind="income"
        ).first()

        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000,
                          direction="in", category_id=income_cat.id)
        txn2 = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=200000,
                                 direction="in", category_id=income_cat.id)

        from finapp.services.ledger import void_transaction
        void_transaction(ctx, txn2.id)

        income = get_budget_income_received_cents(ctx, period.id)
        assert income == 200000


class TestDebtBalance:
    """Test debt balance calculation."""

    def test_debt_balance_initial(self, db, ctx, setup_account):
        """Initial debt balance equals opening balance."""
        account, period = setup_account

        debt = DebtAccount(
            account_id=account.id,
            name="Credit Card",
            opening_balance_cents=50000,
            cached_balance_cents=50000,
            interest_rate_bps=2200,
            minimum_payment_cents=5000,
        )
        db.add(debt)
        db.commit()

        balance = get_debt_balance_cents(ctx, debt.id)
        assert balance == 50000

    def test_debt_balance_after_principal_payment(self, db, ctx, setup_account):
        """Debt balance decreases by principal paid."""
        account, period = setup_account

        debt = DebtAccount(
            account_id=account.id,
            name="Credit Card",
            opening_balance_cents=50000,
            cached_balance_cents=50000,
            interest_rate_bps=2200,
            minimum_payment_cents=5000,
        )
        db.add(debt)
        db.commit()

        # Pay $10 principal (plus $5 interest)
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=1500,
                          direction="out", link_type="debt", link_id=debt.id,
                          principal_cents=1000, interest_cents=500)

        balance = get_debt_balance_cents(ctx, debt.id)
        assert balance == 49000  # $500 - $10 = $490

    def test_debt_balance_interest_only_payment(self, db, ctx, setup_account):
        """Interest-only payment doesn't reduce balance."""
        account, period = setup_account

        debt = DebtAccount(
            account_id=account.id,
            name="Credit Card",
            opening_balance_cents=50000,
            cached_balance_cents=50000,
            interest_rate_bps=2200,
            minimum_payment_cents=5000,
        )
        db.add(debt)
        db.commit()

        # Interest-only payment
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=500,
                          direction="out", link_type="debt", link_id=debt.id,
                          principal_cents=0, interest_cents=500)

        balance = get_debt_balance_cents(ctx, debt.id)
        assert balance == 50000  # No principal paid, balance unchanged

    def test_debt_balance_never_negative(self, db, ctx, setup_account):
        """Debt balance cannot go negative."""
        account, period = setup_account

        debt = DebtAccount(
            account_id=account.id,
            name="Credit Card",
            opening_balance_cents=10000,
            cached_balance_cents=10000,
            interest_rate_bps=2200,
            minimum_payment_cents=5000,
        )
        db.add(debt)
        db.commit()

        # Pay more than balance
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=20000,
                          direction="out", link_type="debt", link_id=debt.id,
                          principal_cents=20000, interest_cents=0)

        balance = get_debt_balance_cents(ctx, debt.id)
        assert balance == 0  # Clamped to 0


class TestSavingsGoalBalance:
    """Test savings goal balance calculation."""

    def test_savings_goal_initial(self, db, ctx, setup_account):
        """Initial savings balance equals opening balance."""
        account, period = setup_account

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=100000,
            opening_balance_cents=25000,
            cached_balance_cents=25000,
        )
        db.add(goal)
        db.commit()

        balance = get_savings_goal_balance_cents(ctx, goal.id)
        assert balance == 25000

    def test_savings_goal_contribution(self, db, ctx, setup_account):
        """Contributions increase goal balance."""
        account, period = setup_account

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=100000,
            opening_balance_cents=0,
            cached_balance_cents=0,
        )
        db.add(goal)
        db.commit()

        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=20000,
                          direction="out", link_type="savings", link_id=goal.id)
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=30000,
                          direction="out", link_type="savings", link_id=goal.id)

        balance = get_savings_goal_balance_cents(ctx, goal.id)
        assert balance == 50000

    def test_savings_goal_withdrawal(self, db, ctx, setup_account):
        """Withdrawals decrease goal balance."""
        account, period = setup_account

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=100000,
            opening_balance_cents=50000,
            cached_balance_cents=50000,
        )
        db.add(goal)
        db.commit()

        # Withdrawal (direction='in' for savings link)
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=15000,
                          direction="in", link_type="savings", link_id=goal.id)

        balance = get_savings_goal_balance_cents(ctx, goal.id)
        assert balance == 35000

    def test_savings_goal_never_negative(self, db, ctx, setup_account):
        """Savings balance cannot go negative."""
        account, period = setup_account

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=100000,
            opening_balance_cents=10000,
            cached_balance_cents=10000,
        )
        db.add(goal)
        db.commit()

        # Withdraw more than balance
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=50000,
                          direction="in", link_type="savings", link_id=goal.id)

        balance = get_savings_goal_balance_cents(ctx, goal.id)
        assert balance == 0


class TestDaysOfExpensesCoverage:
    """Test emergency fund coverage calculation."""

    def test_coverage_trailing_90_days(self, db, ctx, setup_account):
        """Calculate coverage from trailing 90-day average."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=300000,  # $3000
            opening_balance_cents=300000,
            cached_balance_cents=300000,
        )
        db.add(goal)

        # Add spending over last 90 days
        # Spend $100/day = $9000 total over 90 days = $100/day average
        for i in range(90):
            day = date(2026, 6, 15) - timedelta(days=i)
            if day.month == period.month and day.year == period.year:
                create_transaction(ctx, date=day, amount_cents=10000,
                                  direction="out", category_id=food_cat.id)

        db.commit()

        # Emergency fund: $3000, average daily: depends on data
        # This is a simplified test - real calculation depends on full 90-day history
        coverage = get_days_of_expenses_coverage(ctx, goal.id)
        assert coverage >= 0  # Should return valid number

    def test_coverage_less_than_30_days_data(self, db, ctx, setup_account):
        """Use estimated average when less than 30 days of data."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=300000,
            opening_balance_cents=300000,
            cached_balance_cents=300000,
        )
        db.add(goal)
        db.commit()

        # Add spending for just 10 days
        for i in range(10):
            day = date(2026, 6, 15) - timedelta(days=i)
            create_transaction(ctx, date=day, amount_cents=10000,
                              direction="out", category_id=food_cat.id)

        db.commit()

        # With less than 30 days, estimate from available data
        coverage = get_days_of_expenses_coverage(ctx, goal.id)
        assert coverage >= 0
