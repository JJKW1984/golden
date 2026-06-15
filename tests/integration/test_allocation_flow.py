"""
Integration tests for full allocation + income flow.
Tests user-facing scenarios: income entry → allocation ritual → zero-based confirmation.
"""
import pytest
from datetime import date
from finapp.deps import AccountContext
from finapp.models import BudgetCategory, BudgetPeriod
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction
from finapp.services.allocation import (
    set_allocation_targets,
    compute_funded_cents_per_category,
    is_zero_based_confirmed,
)


@pytest.fixture
def integration_setup(db, account):
    """Full setup for integration tests."""
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

    return AccountContext(account_id=account.id, db=db), period


def test_full_flow_log_income_allocate_zero_based(db, integration_setup):
    """
    Integration: User logs income, allocation ritual sets targets,
    zero-based block prevents proceeding until unallocated = $0.
    """
    ctx, period = integration_setup

    # Get categories
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }

    # Step 1: Log $2,000 income on June 1
    create_transaction(
        ctx,
        date=date(2026, 6, 1),
        amount_cents=200000,
        direction="in",
        category_id=categories["Income"],
    )

    # Step 2: Allocation ritual with targets totaling $1,750
    set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            categories["Debt"]: 25000,
            categories["Emergency Fund"]: 50000,
            categories["Housing"]: 80000,
            categories["Food"]: 20000,
            categories["Transportation"]: 20000,
            categories["Everything Else"]: 135000,  # $1,350
        },
    )

    # Step 3: Check zero-based block
    blocked, unallocated = is_zero_based_confirmed(ctx, period_id=period.id)
    assert not blocked
    assert unallocated == 0  # All income allocated

    # Step 4: Zero-based block cleared, user can proceed
    # (Phase 5 will wire this to HTMX UI)


def test_biweekly_paycheck_flow(db, integration_setup):
    """Integration: Biweekly paychecks accumulate funding, no ritual reopen."""
    ctx, period = integration_setup

    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }

    # Set targets: Debt $250, Emergency Fund $500
    set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            categories["Debt"]: 25000,
            categories["Emergency Fund"]: 50000,
        },
    )

    # First paycheck: June 1, $1,000
    create_transaction(
        ctx,
        date=date(2026, 6, 1),
        amount_cents=100000,
        direction="in",
        category_id=categories["Income"],
    )

    funded_day1 = compute_funded_cents_per_category(ctx, period_id=period.id)
    assert funded_day1[categories["Debt"]] == 25000
    assert funded_day1[categories["Emergency Fund"]] == 50000

    # Second paycheck: June 15, $1,000
    # Should not reopen ritual, income just accumulates
    create_transaction(
        ctx,
        date=date(2026, 6, 15),
        amount_cents=100000,
        direction="in",
        category_id=categories["Income"],
    )

    funded_day15 = compute_funded_cents_per_category(ctx, period_id=period.id)
    # Funded amounts unchanged (targets already met)
    assert funded_day15[categories["Debt"]] == 25000
    assert funded_day15[categories["Emergency Fund"]] == 50000

    # Total income now $2,000
    period_updated = ctx.db.query(BudgetPeriod).filter_by(id=period.id).first()
    assert period_updated.income_received_cents == 200000
