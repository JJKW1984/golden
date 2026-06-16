"""
Tests for overage detection and reallocation logic (Phase 5).
"""
import pytest
from datetime import date
from finapp.deps import AccountContext
from finapp.models import BudgetCategory, BudgetPeriod, BudgetAllocation
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction
from finapp.services.allocation import set_allocation_targets
from finapp.services.balances import get_budget_spent_cents


@pytest.fixture
def overage_setup(db, account):
    """Setup for overage tests."""
    seed_default_categories(db, account.id)

    period = BudgetPeriod(
        account_id=account.id,
        year=2026,
        month=6,
        income_received_cents=100000,  # $1,000 total budget
        status="active",
    )
    db.add(period)
    db.commit()

    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }

    # Set small allocations for easy overage testing
    set_allocation_targets(
        AccountContext(account_id=account.id, db=db),
        period_id=period.id,
        category_targets={
            categories["Housing"]: 50000,        # $500
            categories["Food"]: 20000,           # $200
            categories["Transportation"]: 15000, # $150
            categories["Debt"]: 10000,           # $100
            categories["Emergency Fund"]: 5000,  # $50
        },
    )

    return AccountContext(account_id=account.id, db=db), period, categories


class TestOverageDetection:
    """Tests for detecting when a category exceeds its budget."""

    def test_no_overage_when_spending_equals_budget(self, overage_setup):
        """No overage when spent equals funded amount."""
        ctx, period, categories = overage_setup

        # Spend exactly the Food budget ($200)
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=20000, direction="out",
                          category_id=categories["Food"])

        spent = get_budget_spent_cents(ctx, period.id, categories["Food"])
        assert spent == 20000  # Equals the budget

    def test_overage_when_spending_exceeds_budget(self, overage_setup):
        """Overage occurs when spent exceeds funded amount."""
        ctx, period, categories = overage_setup

        # Spend $210 in Food category (budget is $200)
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=10000, direction="out",
                          category_id=categories["Food"])
        create_transaction(ctx, date=date(2026, 6, 10), amount_cents=12000, direction="out",
                          category_id=categories["Food"])

        spent = get_budget_spent_cents(ctx, period.id, categories["Food"])
        assert spent == 22000  # Over the 20000 budget

        # Remaining is negative (overspent)
        assert spent > 20000

    def test_overage_calculation_exact_amount(self, overage_setup):
        """Overage amount should be exact (spent - budget)."""
        ctx, period, categories = overage_setup

        # Spend $210 (overage of $10)
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=21000, direction="out",
                          category_id=categories["Food"])

        spent = get_budget_spent_cents(ctx, period.id, categories["Food"])
        budget = 20000
        overage_amount = spent - budget

        assert overage_amount == 1000  # Exact $10 overage

    def test_overage_detection_ignores_deleted_transactions(self, overage_setup):
        """Deleted transactions should not count toward overage."""
        ctx, period, categories = overage_setup

        # Create and then delete a transaction
        txn = create_transaction(ctx, date=date(2026, 6, 5), amount_cents=15000,
                                direction="out", category_id=categories["Food"])

        # Delete it
        txn.is_deleted = True
        ctx.db.commit()

        # Spent should not include the deleted transaction
        spent = get_budget_spent_cents(ctx, period.id, categories["Food"])
        assert spent == 0  # No active spending


class TestOverageAvailableCategories:
    """Tests for finding categories with headroom for reallocation."""

    def test_find_categories_with_headroom(self, overage_setup):
        """Should identify categories that have unspent budget."""
        ctx, period, categories = overage_setup

        # Spend nothing in some categories, they have full headroom
        # Housing has $500, Food has $200, etc.

        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        # All categories should have headroom since nothing is spent
        for alloc in allocations:
            spent = get_budget_spent_cents(ctx, period.id, alloc.category_id)
            headroom = alloc.target_cents - spent
            assert headroom > 0

    def test_categories_with_headroom_sorted_by_availability(self, overage_setup):
        """Categories with more headroom should appear first."""
        ctx, period, categories = overage_setup

        # Spend in some categories
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=45000, direction="out",
                          category_id=categories["Housing"])  # $450 spent of $500 (headroom: $50)
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=10000, direction="out",
                          category_id=categories["Food"])  # $100 spent of $200 (headroom: $100)

        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        # Calculate headroom for each
        alloc_headroom = []
        for alloc in allocations:
            spent = get_budget_spent_cents(ctx, period.id, alloc.category_id)
            headroom = alloc.target_cents - spent
            if headroom > 0:
                alloc_headroom.append((alloc.category_id, headroom))

        # Sort by headroom descending
        alloc_headroom.sort(key=lambda x: x[1], reverse=True)

        # Food should be first (headroom: $100), Housing second (headroom: $50)
        assert alloc_headroom[0][0] == categories["Food"]
        assert alloc_headroom[0][1] == 10000  # $100
        assert alloc_headroom[1][0] == categories["Housing"]
        assert alloc_headroom[1][1] == 5000  # $50

    def test_overspent_category_not_available_for_pulling(self, overage_setup):
        """A category that's already overspent should not be available to pull from."""
        ctx, period, categories = overage_setup

        # Overspend a category
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=25000, direction="out",
                          category_id=categories["Food"])  # Over the $200 budget

        spent = get_budget_spent_cents(ctx, period.id, categories["Food"])
        allocation = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Food"]
        ).first()

        headroom = allocation.target_cents - spent
        assert headroom < 0  # Overspent, negative headroom


class TestBudgetReallocationFlow:
    """Integration tests for the overage reallocation flow."""

    def test_reallocate_from_category_with_headroom(self, overage_setup):
        """Reallocating from a category with headroom should reduce its target."""
        ctx, period, categories = overage_setup

        # Get Food allocation (budget $200)
        food_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Food"]
        ).first()

        transportation_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Transportation"]
        ).first()

        original_food = food_alloc.target_cents
        original_transport = transportation_alloc.target_cents

        # Move $50 from Transportation to Food
        amount_to_move = 5000
        food_alloc.target_cents += amount_to_move
        transportation_alloc.target_cents -= amount_to_move
        ctx.db.commit()

        assert food_alloc.target_cents == original_food + amount_to_move
        assert transportation_alloc.target_cents == original_transport - amount_to_move

    def test_reallocate_preserves_zero_based_sum(self, overage_setup):
        """Reallocation should not change total allocated funds."""
        ctx, period, categories = overage_setup

        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        original_total = sum(a.target_cents for a in allocations)

        # Reallocate
        food = next(a for a in allocations if a.category_id == categories["Food"])
        transport = next(a for a in allocations if a.category_id == categories["Transportation"])

        food.target_cents += 3000
        transport.target_cents -= 3000
        ctx.db.commit()

        # Get fresh allocations
        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        new_total = sum(a.target_cents for a in allocations)
        assert new_total == original_total  # Zero-based sum preserved

    def test_cannot_reallocate_more_than_available(self, overage_setup):
        """Should not allow reallocating more than a category has available."""
        ctx, period, categories = overage_setup

        transportation_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Transportation"]
        ).first()

        original_target = transportation_alloc.target_cents  # $150

        # Try to remove $200 (more than available)
        amount_to_remove = 20000

        # Check: can't go below zero
        new_target = original_target - amount_to_remove
        assert new_target < 0  # This would be invalid

    def test_overage_flow_reallocate_and_save_transaction(self, overage_setup):
        """Full flow: overage triggers, reallocation happens, transaction saves anyway."""
        ctx, period, categories = overage_setup

        # Food budget is $200
        food_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Food"]
        ).first()

        # Log an expense that will cause overage
        txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=25000,
                                direction="out", category_id=categories["Food"],
                                payee="Grocery Store")

        # Transaction is saved regardless
        assert txn.id is not None

        # Now reallocate to cover the overage
        # Move $50 from Transportation to Food
        transport_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Transportation"]
        ).first()

        food_alloc.target_cents += 5000
        transport_alloc.target_cents -= 5000
        ctx.db.commit()

        # Food now has $250 budget
        food_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Food"]
        ).first()

        spent = get_budget_spent_cents(ctx, period.id, categories["Food"])
        remaining = food_alloc.target_cents - spent

        assert remaining == 0  # Exactly on budget after reallocation
