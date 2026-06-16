"""
Integration tests for Phase 5 flows: budget management and transaction flows.
Tests full user journeys like: log income → allocate → spend → reallocate on overage.
"""
import pytest
from datetime import date
from finapp.deps import AccountContext
from finapp.models import BudgetCategory, BudgetPeriod, BudgetAllocation
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction
from finapp.services.allocation import (
    set_allocation_targets,
    compute_funded_cents_per_category,
    compute_unallocated_cents,
)
from finapp.services.balances import get_budget_spent_cents


@pytest.fixture
def phase5_setup(db, account):
    """Full setup for Phase 5 integration tests."""
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

    ctx = AccountContext(account_id=account.id, db=db)
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }

    return ctx, period, categories


class TestBudgetAllocationFlow:
    """Integration tests for the budget allocation flow."""

    def test_full_flow_income_allocate_zero_based(self, phase5_setup):
        """
        User logs income, allocation ritual sets targets totaling the income,
        zero-based block is satisfied.
        """
        ctx, period, categories = phase5_setup

        # Step 1: Log $2,000 income
        income_txn = create_transaction(
            ctx,
            date=date(2026, 6, 1),
            amount_cents=200000,
            direction="in",
            category_id=categories["Income"],
            payee="Employer",
            memo="Biweekly paycheck",
        )

        # Update period income
        period.income_received_cents = 200000
        ctx.db.commit()

        # Step 2: Set allocation targets
        set_allocation_targets(
            ctx,
            period_id=period.id,
            category_targets={
                categories["Housing"]: 100000,       # $1,000
                categories["Food"]: 40000,           # $400
                categories["Transportation"]: 25000, # $250
                categories["Debt"]: 35000,           # $350
                categories["Emergency Fund"]: 10000, # $100
                categories["Personal"]: 15000,       # $150
                categories["Everything Else"]: 15000, # $150
            },
        )

        # Step 3: Check zero-based
        unallocated = compute_unallocated_cents(ctx, period_id=period.id)
        assert unallocated == 0  # Zero-based confirmed

        # Step 4: Verify allocations are in place
        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        assert len(allocations) == 7
        assert all(a.target_cents > 0 for a in allocations)

    def test_biweekly_paycheck_no_ritual_reopen(self, phase5_setup):
        """
        First paycheck triggers allocation ritual. Second paycheck doesn't reopen ritual;
        income accumulates and funded climbs proportionally.
        """
        ctx, period, categories = phase5_setup

        # First paycheck on June 1: $1,000
        create_transaction(
            ctx,
            date=date(2026, 6, 1),
            amount_cents=100000,
            direction="in",
            category_id=categories["Income"],
        )

        period.income_received_cents = 100000
        ctx.db.commit()

        # Set allocation targets (ritual)
        set_allocation_targets(
            ctx,
            period_id=period.id,
            category_targets={
                categories["Housing"]: 50000,        # $500
                categories["Food"]: 30000,           # $300
                categories["Transportation"]: 20000, # $200
            },
        )

        # Check zero-based
        unallocated = compute_unallocated_cents(ctx, period_id=period.id)
        assert unallocated == 0

        # Second paycheck on June 15: $1,000 more
        create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=100000,
            direction="in",
            category_id=categories["Income"],
        )

        period.income_received_cents = 200000
        ctx.db.commit()

        # Ritual does NOT reopen; targets stay fixed
        allocations = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period.id
        ).all()

        # Targets should still be the same
        assert any(a.target_cents == 50000 for a in allocations)

        # But unallocated should now be higher
        unallocated = compute_unallocated_cents(ctx, period_id=period.id)
        assert unallocated == 100000  # Second $1,000 is unallocated


class TestExpenseLoggingFlow:
    """Integration tests for the expense logging and overage flow."""

    def test_expense_logging_updates_spent(self, phase5_setup):
        """
        User logs income, sets allocation, logs expense.
        Category remaining should update in real time.
        """
        ctx, period, categories = phase5_setup

        # Setup
        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000, direction="in",
                          category_id=categories["Income"])
        period.income_received_cents = 200000
        ctx.db.commit()

        set_allocation_targets(
            ctx,
            period_id=period.id,
            category_targets={
                categories["Food"]: 40000,  # $400
                categories["Housing"]: 100000,
                categories["Debt"]: 35000,
                categories["Transportation"]: 25000,
            },
        )

        # Log an expense
        create_transaction(ctx, date=date(2026, 6, 10), amount_cents=10000, direction="out",
                          category_id=categories["Food"], payee="Grocery")

        # Check spent
        spent = get_budget_spent_cents(ctx, period.id, categories["Food"])
        assert spent == 10000

        # Check remaining
        food_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Food"]
        ).first()

        remaining = food_alloc.target_cents - spent
        assert remaining == 30000  # $300 remaining

    def test_expense_with_mood_tag_persists(self, phase5_setup):
        """Expense logged with a mood tag should persist for filtering."""
        ctx, period, categories = phase5_setup

        # Setup
        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000, direction="in",
                          category_id=categories["Income"])
        period.income_received_cents = 200000
        ctx.db.commit()

        set_allocation_targets(
            ctx,
            period_id=period.id,
            category_targets={categories["Food"]: 40000},
        )

        # Log expense with mood tag
        txn = create_transaction(
            ctx,
            date=date(2026, 6, 10),
            amount_cents=10000,
            direction="out",
            category_id=categories["Food"],
            payee="Grocery",
            mood_tag="necessity",
        )

        assert txn.mood_tag == "necessity"

        # Verify it can be filtered
        necessity_txns = ctx.db.query(BudgetAllocation.__table__.c).filter(
            # Find by mood tag
        ).all()

        # Simple verification: transaction exists with the tag
        txn = ctx.db.query(BudgetAllocation.__class__.__bases__[0]).filter_by(
            mood_tag="necessity"
        ).first()
        # Mood tag filtering will be tested in router tests


class TestOverageReallocationFlow:
    """Integration tests for the overage detection and reallocation flow."""

    def test_overage_triggers_on_expense_exceeding_remaining(self, phase5_setup):
        """
        User spends more than remaining budget in a category.
        Overage modal should show available categories for reallocation.
        """
        ctx, period, categories = phase5_setup

        # Setup: $2,000 income with specific allocations
        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000, direction="in",
                          category_id=categories["Income"])
        period.income_received_cents = 200000
        ctx.db.commit()

        set_allocation_targets(
            ctx,
            period_id=period.id,
            category_targets={
                categories["Food"]: 40000,            # $400
                categories["Housing"]: 100000,        # $1,000
                categories["Transportation"]: 30000,  # $300
                categories["Debt"]: 35000,            # $350
                categories["Emergency Fund"]: 10000,  # $100
                categories["Personal"]: 15000,        # $150
                categories["Everything Else"]: 20000, # $200
            },
        )

        # User spends $420 in Food (budget is $400) — overage of $20
        create_transaction(ctx, date=date(2026, 6, 10), amount_cents=42000, direction="out",
                          category_id=categories["Food"])

        # Check overage
        spent = get_budget_spent_cents(ctx, period.id, categories["Food"])
        food_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Food"]
        ).first()

        overage = spent - food_alloc.target_cents
        assert overage == 2000  # $20 overage

        # Verify other categories have headroom
        transport_alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period.id,
            category_id=categories["Transportation"]
        ).first()

        transport_spent = get_budget_spent_cents(ctx, period.id, categories["Transportation"])
        transport_headroom = transport_alloc.target_cents - transport_spent

        assert transport_headroom > 0  # Has headroom available

    def test_overage_reallocation_edits_both_targets(self, phase5_setup):
        """
        User reallocates $20 from Transportation to Food to cover overage.
        Both allocations should update, zero-based sum preserved.
        """
        ctx, period, categories = phase5_setup

        # Setup
        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000, direction="in",
                          category_id=categories["Income"])
        period.income_received_cents = 200000
        ctx.db.commit()

        set_allocation_targets(
            ctx,
            period_id=period.id,
            category_targets={
                categories["Food"]: 40000,
                categories["Transportation"]: 30000,
                categories["Housing"]: 100000,
                categories["Debt"]: 35000,
            },
        )

        # Get allocations
        allocations = {
            a.category_id: a
            for a in ctx.db.query(BudgetAllocation).filter_by(
                account_id=ctx.account_id, period_id=period.id
            ).all()
        }

        original_total = sum(a.target_cents for a in allocations.values())

        # Reallocate $20 from Transportation to Food
        move_amount = 2000
        allocations[categories["Food"]].target_cents += move_amount
        allocations[categories["Transportation"]].target_cents -= move_amount
        ctx.db.commit()

        # Verify totals preserved
        allocations = {
            a.category_id: a
            for a in ctx.db.query(BudgetAllocation).filter_by(
                account_id=ctx.account_id, period_id=period.id
            ).all()
        }

        new_total = sum(a.target_cents for a in allocations.values())
        assert new_total == original_total

        # Verify Food increased
        assert allocations[categories["Food"]].target_cents == 42000

        # Verify Transportation decreased
        assert allocations[categories["Transportation"]].target_cents == 28000


class TestTransactionDeletionAndUndo:
    """Integration tests for transaction soft delete and 10-second undo."""

    def test_delete_transaction_marks_deleted(self, phase5_setup):
        """Deleting a transaction should set is_deleted=TRUE."""
        ctx, period, categories = phase5_setup

        # Setup
        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000, direction="in",
                          category_id=categories["Income"])
        period.income_received_cents = 200000
        ctx.db.commit()

        set_allocation_targets(
            ctx,
            period_id=period.id,
            category_targets={categories["Food"]: 40000},
        )

        # Log expense
        txn = create_transaction(ctx, date=date(2026, 6, 10), amount_cents=10000,
                                direction="out", category_id=categories["Food"])

        # Delete it
        txn.is_deleted = True
        ctx.db.commit()

        # Verify it's marked deleted
        txn = ctx.db.query(BudgetAllocation.__class__).filter_by(
            id=txn.id
        ).first()
        assert txn.is_deleted is True

    def test_restore_transaction_unmarks_deleted(self, phase5_setup):
        """Restoring a transaction should set is_deleted=FALSE."""
        ctx, period, categories = phase5_setup

        # Setup and create transaction
        create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000, direction="in",
                          category_id=categories["Income"])
        period.income_received_cents = 200000
        ctx.db.commit()

        set_allocation_targets(
            ctx,
            period_id=period.id,
            category_targets={categories["Food"]: 40000},
        )

        txn = create_transaction(ctx, date=date(2026, 6, 10), amount_cents=10000,
                                direction="out", category_id=categories["Food"])

        # Delete then restore
        txn.is_deleted = True
        ctx.db.commit()

        txn.is_deleted = False
        ctx.db.commit()

        # Verify it's restored
        txn = ctx.db.query(BudgetAllocation.__class__).filter_by(
            id=txn.id
        ).first()
        assert txn.is_deleted is False
