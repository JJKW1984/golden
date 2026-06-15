"""
Unit tests for allocation service.
Tests the cumulative envelope logic and income/allocation flow.
"""
import pytest
from datetime import date
from finapp.models import (
    BudgetPeriod,
    BudgetCategory,
    BudgetAllocation,
    Transaction,
    DebtAccount,
    SavingsGoal,
)
from finapp.services.seeds import seed_default_categories
from finapp.money import to_cents


@pytest.fixture
def allocation_setup(db, account):
    """Set up a test account with categories and a period."""
    # Seed categories
    seed_default_categories(db, account.id)

    # Create a period
    period = BudgetPeriod(
        account_id=account.id,
        year=2026,
        month=6,
        income_received_cents=0,
        status="active",
    )
    db.add(period)
    db.commit()

    # Create a debt account with minimum payment
    debt = DebtAccount(
        account_id=account.id,
        name="Credit Card",
        opening_balance_cents=100000,  # $1,000
        cached_balance_cents=100000,
        interest_rate_bps=2199,
        minimum_payment_cents=25000,  # $250 minimum
    )
    db.add(debt)
    db.commit()

    # Create a savings goal
    goal = SavingsGoal(
        account_id=account.id,
        name="Emergency Fund",
        goal_type="emergency_fund",
        target_cents=50000,  # $500 target
        opening_balance_cents=0,
        cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()

    return account, period, debt, goal


def test_allocation_ritual_sets_targets_on_first_paycheck(db, allocation_setup):
    """Test: First paycheck of month with no targets opens allocation ritual."""
    account, period, debt, goal = allocation_setup
    from finapp.deps import AccountContext
    from finapp.services.allocation import set_allocation_targets

    ctx = AccountContext(account_id=account.id, db=db)

    # Income: $2,000
    income_amount_cents = 200000

    # Set targets in priority order:
    # Debt (minimum): $250
    # Emergency Fund: $500
    # Housing: $800
    # Food: $200
    # Everything Else: $250 (leftovers)

    allocations = {
        "Debt": 25000,         # $250
        "Emergency Fund": 50000,  # $500
        "Housing": 80000,      # $800
        "Food": 20000,         # $200
        "Everything Else": 25000,  # $250
    }

    # Get category IDs
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }

    # Call allocation ritual
    result = set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            categories[name]: cents for name, cents in allocations.items()
        },
    )

    # Assert: targets are set
    assert result["status"] == "targets_set"
    assert result["total_target_cents"] == 200000

    # Verify BudgetAllocations created
    allocs = db.query(BudgetAllocation).filter_by(
        account_id=account.id, period_id=period.id
    ).all()
    assert len(allocs) == 5

    # Verify targets match input
    allocs_by_cat = {a.category_id: a.target_cents for a in allocs}
    for cat_name, expected_cents in allocations.items():
        assert allocs_by_cat[categories[cat_name]] == expected_cents


def test_allocation_ritual_presets_pay_yourself_first_targets(db, allocation_setup):
    """Test: Allocation ritual pre-fills Debt and Emergency Fund based on obligations."""
    account, period, debt, goal = allocation_setup
    from finapp.deps import AccountContext
    from finapp.services.allocation import compute_default_targets

    ctx = AccountContext(account_id=account.id, db=db)

    # Get categories
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }

    # Compute default targets
    defaults = compute_default_targets(
        ctx,
        period_id=period.id,
        income_cents=200000,  # $2,000
        emergency_fund_monthly_target_cents=50000,  # $500/month
    )

    # Assert: Debt target = minimum payment ($250)
    assert defaults[categories["Debt"]] == 25000

    # Assert: Emergency Fund target = $500 (or less if income is lower)
    assert defaults[categories["Emergency Fund"]] == 50000

    # Assert: Other categories start at 0
    assert defaults[categories["Housing"]] == 0


def test_cumulative_funding_biweekly_paychecks(db, allocation_setup):
    """Test: Multiple paychecks in same month accumulate funding, don't reopen ritual."""
    account, period, debt, goal = allocation_setup
    from finapp.deps import AccountContext
    from finapp.services.allocation import (
        set_allocation_targets,
        compute_funded_cents_per_category,
    )

    ctx = AccountContext(account_id=account.id, db=db)

    # Get categories
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }
    income_cat_id = categories["Income"]
    debt_cat_id = categories["Debt"]
    emergency_cat_id = categories["Emergency Fund"]

    # Set initial targets: Debt $250, Emergency Fund $500
    set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            debt_cat_id: 25000,
            emergency_cat_id: 50000,
        },
    )

    # Log first paycheck: $1,000
    from finapp.services.ledger import create_transaction
    txn1 = create_transaction(
        ctx,
        date=date(2026, 6, 1),
        amount_cents=100000,
        direction="in",
        category_id=income_cat_id,
    )

    # Verify: income_received_cents updated
    period = db.query(BudgetPeriod).filter_by(id=period.id).first()
    assert period.income_received_cents == 100000

    # Compute funded: should claim $100k for Debt ($25k) and Emergency Fund ($50k),
    # leaving $25k unallocated
    funded = compute_funded_cents_per_category(ctx, period_id=period.id)
    assert funded[debt_cat_id] == 25000
    assert funded[emergency_cat_id] == 50000

    # Log second paycheck: $1,000
    txn2 = create_transaction(
        ctx,
        date=date(2026, 6, 15),
        amount_cents=100000,
        direction="in",
        category_id=income_cat_id,
    )

    # Verify: income_received_cents accumulates
    period = db.query(BudgetPeriod).filter_by(id=period.id).first()
    assert period.income_received_cents == 200000

    # Compute funded: targets already reached, nothing new claimed
    # (targets are exhausted, all future income goes to unallocated)
    funded = compute_funded_cents_per_category(ctx, period_id=period.id)
    assert funded[debt_cat_id] == 25000
    assert funded[emergency_cat_id] == 50000


def test_priority_ordering_debt_before_emergency_fund(db, allocation_setup):
    """Test: Funding priority is Debt → Emergency Fund → Housing → ... → Everything Else."""
    account, period, debt, goal = allocation_setup
    from finapp.deps import AccountContext
    from finapp.services.allocation import (
        set_allocation_targets,
        compute_funded_cents_per_category,
    )

    ctx = AccountContext(account_id=account.id, db=db)

    # Get categories
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }
    income_cat_id = categories["Income"]
    debt_cat_id = categories["Debt"]
    emergency_cat_id = categories["Emergency Fund"]
    housing_cat_id = categories["Housing"]

    # Set targets: Debt $500, Emergency Fund $300, Housing $200 (total $1000)
    set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            debt_cat_id: 50000,
            emergency_cat_id: 30000,
            housing_cat_id: 20000,
        },
    )

    # Log income: $600 (partial)
    from finapp.services.ledger import create_transaction
    create_transaction(
        ctx,
        date=date(2026, 6, 1),
        amount_cents=60000,
        direction="in",
        category_id=income_cat_id,
    )

    # Compute funded with partial income
    funded = compute_funded_cents_per_category(ctx, period_id=period.id)

    # Priority: Debt fully funded ($500), Emergency Fund partially ($100), Housing unfunded
    assert funded[debt_cat_id] == 50000
    assert funded[emergency_cat_id] == 10000  # Only $600 - $500 = $100 remaining
    assert funded[housing_cat_id] == 0


def test_everything_else_absorbs_leftovers(db, allocation_setup):
    """Test: Everything Else category absorbs unallocated income when income > targets."""
    account, period, debt, goal = allocation_setup
    from finapp.deps import AccountContext
    from finapp.services.allocation import (
        set_allocation_targets,
        compute_funded_cents_per_category,
    )

    ctx = AccountContext(account_id=account.id, db=db)

    # Get categories
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }
    income_cat_id = categories["Income"]
    debt_cat_id = categories["Debt"]
    emergency_cat_id = categories["Emergency Fund"]
    everything_else_cat_id = categories["Everything Else"]

    # Set targets: Debt $250, Emergency Fund $500 (total $750)
    set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            debt_cat_id: 25000,
            emergency_cat_id: 50000,
            everything_else_cat_id: 0,  # No initial target
        },
    )

    # Log income: $1,000
    from finapp.services.ledger import create_transaction
    create_transaction(
        ctx,
        date=date(2026, 6, 1),
        amount_cents=100000,
        direction="in",
        category_id=income_cat_id,
    )

    # Compute funded
    funded = compute_funded_cents_per_category(ctx, period_id=period.id)

    # Everything Else absorbs: $1,000 - $750 = $250
    assert funded[everything_else_cat_id] == 25000


def test_zero_based_block_when_income_exceeds_targets(db, allocation_setup):
    """Test: Zero-based hard block triggers when unallocated != 0."""
    account, period, debt, goal = allocation_setup
    from finapp.deps import AccountContext
    from finapp.services.allocation import (
        set_allocation_targets,
        is_zero_based_confirmed,
    )

    ctx = AccountContext(account_id=account.id, db=db)

    # Get categories
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }
    income_cat_id = categories["Income"]
    debt_cat_id = categories["Debt"]
    everything_else_cat_id = categories["Everything Else"]

    # Set targets: Debt $250, Everything Else $0
    set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            debt_cat_id: 25000,
            everything_else_cat_id: 0,
        },
    )

    # Log income: $1,000
    from finapp.services.ledger import create_transaction
    create_transaction(
        ctx,
        date=date(2026, 6, 1),
        amount_cents=100000,
        direction="in",
        category_id=income_cat_id,
    )

    # Test: zero-based block should be active
    blocked, unallocated_cents = is_zero_based_confirmed(ctx, period_id=period.id)

    # Unallocated should be $750
    assert not blocked
    assert unallocated_cents == 75000

    # Allocate Everything Else to $750
    alloc = db.query(BudgetAllocation).filter_by(
        account_id=account.id, period_id=period.id, category_id=everything_else_cat_id
    ).first()
    if alloc:
        alloc.target_cents = 75000
    else:
        alloc = BudgetAllocation(
            account_id=account.id,
            period_id=period.id,
            category_id=everything_else_cat_id,
            target_cents=75000,
        )
        db.add(alloc)
    db.commit()

    # Test: zero-based block should clear
    blocked, unallocated_cents = is_zero_based_confirmed(ctx, period_id=period.id)
    assert blocked
    assert unallocated_cents == 0


def test_insufficient_income_triage_path(db, allocation_setup):
    """Test: When income < essentials, triage path engages (no hard block)."""
    account, period, debt, goal = allocation_setup
    from finapp.deps import AccountContext
    from finapp.services.allocation import (
        compute_essentials_target_cents,
        get_triage_state,
    )

    ctx = AccountContext(account_id=account.id, db=db)

    # Get categories
    categories = {
        cat.name: cat.id
        for cat in db.query(BudgetCategory).filter_by(account_id=account.id).all()
    }
    income_cat_id = categories["Income"]
    debt_cat_id = categories["Debt"]
    emergency_cat_id = categories["Emergency Fund"]
    housing_cat_id = categories["Housing"]

    # Set essentials targets: Debt $250, Emergency Fund $500, Housing $400 (total $1,150)
    from finapp.services.allocation import set_allocation_targets
    set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            debt_cat_id: 25000,
            emergency_cat_id: 50000,
            housing_cat_id: 40000,
        },
    )

    # Log income: $600 (less than essentials $1,150)
    from finapp.services.ledger import create_transaction
    create_transaction(
        ctx,
        date=date(2026, 6, 1),
        amount_cents=60000,
        direction="in",
        category_id=income_cat_id,
    )

    # Get triage state
    triage = get_triage_state(ctx, period_id=period.id)

    # Assert: triage is active
    assert triage["has_insufficient_income"] == True
    assert triage["shortfall_cents"] == 115000 - 60000

    # Assert: no hard block on negative remainder
    assert triage["allows_soft_allocation"] == True

    # Assert: offers options
    assert "lower_target" in triage["options"]
    assert "defer_emergency_fund" in triage["options"]
    assert "expect_more_income" in triage["options"]
