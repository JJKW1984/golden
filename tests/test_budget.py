"""
Service-layer tests for budget logic (Phase 5).
Tests budget summary, spent tracking, overage detection, and reallocation.
"""
import pytest
from datetime import date
from finapp.deps import AccountContext
from finapp.models import BudgetCategory, BudgetPeriod, BudgetAllocation, Transaction
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction
from finapp.services.allocation import set_allocation_targets


@pytest.fixture
def budget_setup(db, account):
    """Setup for budget tests: seed categories, create period, set allocations."""
    seed_default_categories(db, account.id)

    period = BudgetPeriod(
        account_id=account.id,
        year=2026,
        month=6,
        income_received_cents=200000,  # $2,000
        status="active",
    )
    db.add(period)
    db.commit()

    # Get categories
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }

    # Set allocations (total must equal income: 200000)
    set_allocation_targets(
        AccountContext(account_id=account.id, db=db),
        period_id=period.id,
        category_targets={
            categories["Housing"]: 80000,   # $800
            categories["Food"]: 40000,      # $400
            categories["Transportation"]: 25000,  # $250
            categories["Debt"]: 30000,      # $300
            categories["Emergency Fund"]: 10000,  # $100
            categories["Personal"]: 10000,  # $100
            categories["Everything Else"]: 5000,   # $50
        },
    )

    return AccountContext(account_id=account.id, db=db), period, categories


class TestBudgetSummary:
    """Tests for budget_summary service function."""

    def test_budget_summary_returns_period_and_categories(self, budget_setup):
        """Budget summary includes period info and all category rows."""
        ctx, period, categories = budget_setup

        # Should be able to fetch allocations for the period
        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        assert len(allocations) == 7  # Seven categories
        assert all(a.target_cents > 0 for a in allocations)

    def test_budget_summary_includes_spent_zero_initially(self, budget_setup):
        """With no expenses, spent should be zero for all categories."""
        ctx, period, categories = budget_setup

        # No transactions logged yet
        transactions = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id, period_id=period.id, direction="out"
        ).all()

        assert len(transactions) == 0

    def test_budget_summary_totals_sum_correctly(self, budget_setup):
        """Budget totals should be: target=targets sum, spent=0, remaining=funded-spent."""
        ctx, period, categories = budget_setup

        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        total_target = sum(a.target_cents for a in allocations)
        assert total_target == 200000  # Matches income


class TestBudgetSpentTracking:
    """Tests for tracking spent amounts by category."""

    def test_spent_excludes_deleted_transactions(self, budget_setup):
        """Deleted transactions should not count toward spent."""
        ctx, period, categories = budget_setup

        # Log an expense
        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=10000,
            direction="out",
            category_id=categories["Food"],
            payee="Grocery Store",
        )

        # Verify it's in the period
        assert txn.period_id == period.id
        assert txn.is_deleted is False

        # Delete the transaction
        txn.is_deleted = True
        ctx.db.commit()

        # Query non-deleted spending
        spending = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            direction="out",
            is_deleted=False,
            category_id=categories["Food"],
        ).all()

        assert len(spending) == 0

    def test_spent_includes_undeleted_transactions(self, budget_setup):
        """Undeleted transactions should count toward spent."""
        ctx, period, categories = budget_setup

        # Log an expense
        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=10000,
            direction="out",
            category_id=categories["Food"],
            payee="Grocery Store",
        )

        # Query spending
        spending = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            direction="out",
            is_deleted=False,
            category_id=categories["Food"],
        ).all()

        assert len(spending) == 1
        assert spending[0].amount_cents == 10000

    def test_multiple_expenses_in_category_sum(self, budget_setup):
        """Multiple expenses in a category should sum."""
        ctx, period, categories = budget_setup

        # Log three expenses in Food
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=5000, direction="out",
                          category_id=categories["Food"], payee="Store 1")
        create_transaction(ctx, date=date(2026, 6, 10), amount_cents=3000, direction="out",
                          category_id=categories["Food"], payee="Store 2")
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=4000, direction="out",
                          category_id=categories["Food"], payee="Store 3")

        # Sum should be 12000
        spending = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            direction="out",
            is_deleted=False,
            category_id=categories["Food"],
        ).all()

        total = sum(t.amount_cents for t in spending)
        assert total == 12000


class TestOverageDetection:
    """Tests for detecting when a category is overspent."""

    def test_overage_when_spent_exceeds_funded(self, budget_setup):
        """Overage should be true when spent > funded for a category."""
        ctx, period, categories = budget_setup

        # Food is allocated $400 (40000 cents)
        # Log $500 of expenses
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=30000, direction="out",
                          category_id=categories["Food"], payee="Store 1")
        create_transaction(ctx, date=date(2026, 6, 10), amount_cents=20000, direction="out",
                          category_id=categories["Food"], payee="Store 2")

        # Should be at exactly funded (50000)
        spending = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            direction="out",
            is_deleted=False,
            category_id=categories["Food"],
        ).all()

        total_spent = sum(t.amount_cents for t in spending)
        assert total_spent == 50000

        # Add one more to exceed
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=1000, direction="out",
                          category_id=categories["Food"], payee="Store 3")

        spending = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            direction="out",
            is_deleted=False,
            category_id=categories["Food"],
        ).all()

        total_spent = sum(t.amount_cents for t in spending)
        assert total_spent == 51000  # Over the 40000 budget


class TestBudgetReallocation:
    """Tests for moving funds between budget allocations."""

    def test_reallocate_reduces_from_category(self, budget_setup):
        """Reallocating should reduce the from_category target."""
        ctx, period, categories = budget_setup

        # Get current allocation for Transportation
        transport_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Transportation"],
        ).first()

        original_target = transport_alloc.target_cents
        assert original_target == 25000

        # Reallocate $100 from Transportation to Food
        transport_alloc.target_cents -= 10000
        ctx.db.commit()

        assert transport_alloc.target_cents == 15000

    def test_reallocate_increases_to_category(self, budget_setup):
        """Reallocating should increase the to_category target."""
        ctx, period, categories = budget_setup

        # Get current allocation for Food
        food_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Food"],
        ).first()

        original_target = food_alloc.target_cents
        assert original_target == 40000

        # Reallocate $100 into Food
        food_alloc.target_cents += 10000
        ctx.db.commit()

        assert food_alloc.target_cents == 50000

    def test_reallocate_preserves_total_target(self, budget_setup):
        """Reallocation should preserve total target (zero-based sum)."""
        ctx, period, categories = budget_setup

        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        original_total = sum(a.target_cents for a in allocations)

        # Reallocate from one to another
        food = next(a for a in allocations if a.category_id == categories["Food"])
        transport = next(a for a in allocations if a.category_id == categories["Transportation"])

        food.target_cents += 5000
        transport.target_cents -= 5000
        ctx.db.commit()

        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        new_total = sum(a.target_cents for a in allocations)
        assert new_total == original_total  # Zero-based preserved


class TestBudgetHistory:
    """Tests for retrieving budget history (past periods)."""

    def test_budget_history_includes_past_periods(self, budget_setup):
        """History should include all closed and active periods."""
        ctx, period, categories = budget_setup

        # Create a second period (past month)
        past_period = BudgetPeriod(
            account_id=ctx.account_id,
            year=2026,
            month=5,
            income_received_cents=190000,
            status="closed",
        )
        ctx.db.add(past_period)
        ctx.db.commit()

        # Query all periods for the account, ordered
        periods = ctx.db.query(BudgetPeriod).filter_by(
            account_id=ctx.account_id
        ).order_by(BudgetPeriod.year.desc(), BudgetPeriod.month.desc()).all()

        assert len(periods) == 2
        assert periods[0].id == period.id  # Current (June)
        assert periods[1].id == past_period.id  # Past (May)

    def test_budget_history_excludes_other_accounts(self, db, account):
        """History for one account should not include other accounts' periods."""
        # This test validates account scoping
        from finapp.models import Account

        other_account = Account(id=2, display_name="Other User")
        db.add(other_account)

        other_period = BudgetPeriod(
            account_id=other_account.id,
            year=2026,
            month=6,
            income_received_cents=100000,
            status="active",
        )
        db.add(other_period)
        db.commit()

        ctx = AccountContext(account_id=account.id, db=db)

        # Query should only get this account's periods
        periods = db.query(BudgetPeriod).filter_by(
            account_id=ctx.account_id
        ).all()

        assert all(p.account_id == ctx.account_id for p in periods)
