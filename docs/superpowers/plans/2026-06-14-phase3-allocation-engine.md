# Phase 3: Allocation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the cumulative monthly envelope (D12) and the income/allocation flow, including the income-under-obligations triage edge case.

**Architecture:** The allocation engine operates on derived values (no new stored fields beyond `BudgetAllocation.target_cents`). Income transactions are written through the ledger service, which updates the cached `BudgetPeriod.income_received_cents`. The allocation service then computes `funded_cents` per category in priority order (Debt → Emergency Fund → Housing → ... → Everything Else). Zero-based budgeting triggers when `income_received_cents >= essentials_target`; below that threshold, the triage flow engages (priority funding with visibility into unfunded targets).

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0 (existing), Pydantic v2 (existing), pytest (existing).

---

## File Structure

### New Files
- `finapp/services/allocation.py` — Core allocation logic (target setting, funded computation, triage)
- `finapp/routers/allocation.py` — HTTP routes for allocation ritual, income entry, and zero-based checks
- `tests/test_allocation.py` — Allocation service unit tests (TDD-driven)
- `tests/integration/test_allocation_flow.py` — End-to-end allocation + income flow integration tests

### Modified Files
- `finapp/models.py` — No changes (BudgetAllocation already supports `target_cents`)
- `finapp/main.py` — Register `allocation` router
- `finapp/services/balances.py` — Add `get_essentials_target_cents()` helper
- `finapp/schemas.py` — Add Pydantic models for allocation requests/responses

---

## Implementation Tasks

### Task 1: Create BudgetAllocation Response Schema & Allocation Request Models

**Files:**
- Modify: `finapp/schemas.py`

**Steps:**

- [ ] **Step 1: Read current schemas.py**

Run:
```bash
cd d:\golden
type finapp\schemas.py
```

- [ ] **Step 2: Add allocation-related Pydantic models to schemas.py**

Add this content before the final line of `finapp/schemas.py`:

```python
class BudgetAllocationResponse(BaseModel):
    """Response model for a single budget allocation."""
    id: int
    category_id: int
    category_name: str
    target_cents: int
    funded_cents: int  # Derived, not stored
    spent_cents: int  # Derived
    remaining_cents: int  # Derived: funded - spent
    
    class Config:
        from_attributes = True


class AllocationRitualRequest(BaseModel):
    """Request to set initial targets for a period (allocation ritual)."""
    period_id: int
    allocations: list[dict]  # [{category_id, target_cents}, ...]


class AllocationRitualResponse(BaseModel):
    """Response after setting targets."""
    period_id: int
    allocations: list[BudgetAllocationResponse]
    total_target_cents: int
    income_received_cents: int
    unallocated_cents: int  # income_received - sum(target)
    zero_based_block: bool  # True if unallocated != 0


class IncomeEntryRequest(BaseModel):
    """Request to log income."""
    date: date
    amount_cents: int
    payee: str = None
    memo: str = None


class AllocationSummaryResponse(BaseModel):
    """Summary of allocation state for a period."""
    period_id: int
    income_received_cents: int
    total_target_cents: int
    total_funded_cents: int
    total_spent_cents: int
    unallocated_cents: int
    is_zero_based: bool
    allocations: list[BudgetAllocationResponse]
    has_insufficient_income: bool  # True when income < essentials


class AllocationTriageResponse(BaseModel):
    """Response when income < essentials (triage path)."""
    message: str
    income_received_cents: int
    essentials_target_cents: int
    shortfall_cents: int
    funded_allocations: list[BudgetAllocationResponse]
    unfunded_allocations: list[BudgetAllocationResponse]
    options: list[str]  # ["lower_target", "defer_emergency_fund", "expect_more_income"]
```

- [ ] **Step 3: Verify schemas.py has no syntax errors**

Run:
```bash
cd d:\golden
python -m py_compile finapp/schemas.py
```

Expected: No output (success)

- [ ] **Step 4: Commit**

```bash
cd d:\golden
git add finapp/schemas.py
git commit -m "feat(phase3): add allocation request/response schemas"
```

---

### Task 2: Write Unit Tests for Allocation Logic (TDD Step 1)

**Files:**
- Create: `tests/test_allocation.py`
- Create: `tests/integration/test_allocation_flow.py`

**Steps:**

- [ ] **Step 1: Create test_allocation.py with first test (allocation ritual setup)**

Create file `tests/test_allocation.py`:

```python
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
    assert triage["shortfall_cents"] == 1150000 - 60000
    
    # Assert: no hard block on negative remainder
    assert triage["allows_soft_allocation"] == True
    
    # Assert: offers options
    assert "lower_target" in triage["options"]
    assert "defer_emergency_fund" in triage["options"]
    assert "expect_more_income" in triage["options"]
```

- [ ] **Step 2: Create integration tests**

Create file `tests/integration/test_allocation_flow.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail (TDD)**

Run:
```bash
cd d:\golden
pytest tests/test_allocation.py -v
```

Expected: Tests FAIL with "ModuleNotFoundError: No module named 'finapp.services.allocation'" or similar.

- [ ] **Step 4: Commit test files**

```bash
cd d:\golden
git add tests/test_allocation.py tests/integration/test_allocation_flow.py
git commit -m "test(phase3): add allocation unit and integration tests (TDD)"
```

---

### Task 3: Implement Core Allocation Service

**Files:**
- Create: `finapp/services/allocation.py`

**Steps:**

- [ ] **Step 1: Create allocation.py with core functions**

Create file `finapp/services/allocation.py`:

```python
"""
Allocation service: cumulative monthly envelope and income/allocation flow.
This is Phase 3's core business logic.

Key concepts:
- target_cents: planned amount per category for the month (set once per month)
- funded_cents: portion of received income that has been claimed by targets
- spent_cents: actual expenses in the category
- remaining_cents: funded - spent (can be negative if overspent)
- unallocated_cents: total income - sum(targets)

Allocation is deterministic and priority-ordered:
1. Debt (minimum payment obligations)
2. Emergency Fund (savings goal)
3. Housing (essential fixed)
4. Food (essential variable)
5. Transportation (essential)
6. Personal (discretionary)
7. Everything Else (catch-all for leftovers)

When income >= essentials: zero-based hard block (must allocate all income).
When income < essentials: triage path (priority funding, visibility into unfunded).
"""

from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import func

from finapp.deps import AccountContext
from finapp.models import (
    BudgetAllocation,
    BudgetCategory,
    BudgetPeriod,
    Transaction,
    DebtAccount,
    SavingsGoal,
)
from finapp.services.balances import get_budget_spent_cents


# ============================================================================
# DERIVED VALUES (never stored, always computed from ledger)
# ============================================================================

def compute_funded_cents_per_category(
    ctx: AccountContext, period_id: int
) -> dict[int, int]:
    """
    Compute funded_cents for each category in priority order.
    
    Returns: {category_id: funded_cents}
    
    Algorithm:
    1. Get targets from BudgetAllocation (set during ritual)
    2. Iterate targets in priority order
    3. Each target claims min(remaining_income, target_cents)
    4. Update remaining_income
    5. Categories not in the ordered list get 0 funded
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()
    
    if not period:
        return {}
    
    # Get income received this period
    income_received = period.income_received_cents
    
    # Fetch all allocations for this period
    allocations = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all()
    
    # Build priority-ordered list of (category_id, target_cents)
    # Order: Debt, Emergency Fund, Housing, Food, Transportation, Personal, Everything Else
    priority_order = ["Debt", "Emergency Fund", "Housing", "Food", "Transportation", "Personal", "Everything Else"]
    
    cat_by_name = {
        cat.name: cat.id
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }
    
    alloc_by_cat = {a.category_id: a.target_cents for a in allocations}
    
    # Compute funded in priority order
    funded = {}
    remaining_income = income_received
    
    for cat_name in priority_order:
        cat_id = cat_by_name.get(cat_name)
        if cat_id is None:
            continue
        
        target = alloc_by_cat.get(cat_id, 0)
        if target == 0:
            funded[cat_id] = 0
            continue
        
        # This category claims up to its target from remaining income
        claimed = min(remaining_income, target)
        funded[cat_id] = claimed
        remaining_income -= claimed
    
    # Any category not in priority order gets 0 funded
    for cat_id in alloc_by_cat.keys():
        if cat_id not in funded:
            funded[cat_id] = 0
    
    return funded


def compute_remaining_cents_per_category(
    ctx: AccountContext, period_id: int
) -> dict[int, int]:
    """
    Compute remaining_cents for each category: funded - spent.
    Can be negative (overspent).
    """
    funded = compute_funded_cents_per_category(ctx, period_id)
    
    remaining = {}
    for cat_id, funded_cents in funded.items():
        spent_cents = get_budget_spent_cents(ctx, period_id, cat_id)
        remaining[cat_id] = funded_cents - spent_cents
    
    return remaining


def compute_essentials_target_cents(ctx: AccountContext, period_id: int) -> int:
    """
    Compute sum of "essential" category targets: Debt, Emergency Fund, Housing, Food, Transportation.
    Used for triage logic: when income < essentials, engage soft-allocation.
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()
    
    if not period:
        return 0
    
    allocations = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all()
    
    essentials = ["Debt", "Emergency Fund", "Housing", "Food", "Transportation"]
    
    cat_by_name = {
        cat.name: cat.id
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }
    
    alloc_by_cat = {a.category_id: a.target_cents for a in allocations}
    
    total = 0
    for cat_name in essentials:
        cat_id = cat_by_name.get(cat_name)
        if cat_id:
            total += alloc_by_cat.get(cat_id, 0)
    
    return total


def compute_unallocated_cents(ctx: AccountContext, period_id: int) -> int:
    """
    Compute unallocated_cents: income_received - sum(targets).
    Can be negative if targets > income (triage case).
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()
    
    if not period:
        return 0
    
    allocations = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all()
    
    total_target = sum(a.target_cents for a in allocations)
    return period.income_received_cents - total_target


# ============================================================================
# WRITES: Allocation Ritual (Setting Targets)
# ============================================================================

def has_allocations_for_period(ctx: AccountContext, period_id: int) -> bool:
    """Check if period already has targets set (ritual already ran)."""
    count = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).count()
    return count > 0


def set_allocation_targets(
    ctx: AccountContext,
    period_id: int,
    category_targets: dict[int, int],  # {category_id: target_cents}
) -> dict:
    """
    Set targets for the period (allocation ritual).
    
    Returns: {
        "status": "targets_set",
        "total_target_cents": int,
    }
    
    Idempotent: if targets already exist, updates them.
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()
    
    if not period:
        raise ValueError(f"Period {period_id} not found")
    
    # Create or update allocations
    for cat_id, target_cents in category_targets.items():
        alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id, period_id=period_id, category_id=cat_id
        ).first()
        
        if alloc:
            alloc.target_cents = target_cents
        else:
            alloc = BudgetAllocation(
                account_id=ctx.account_id,
                period_id=period_id,
                category_id=cat_id,
                target_cents=target_cents,
            )
            ctx.db.add(alloc)
    
    ctx.db.commit()
    
    total_target = sum(category_targets.values())
    
    return {
        "status": "targets_set",
        "total_target_cents": total_target,
    }


def compute_default_targets(
    ctx: AccountContext,
    period_id: int,
    income_cents: int,
    emergency_fund_monthly_target_cents: int = 0,
) -> dict[int, int]:
    """
    Pre-compute default targets for "Pay Yourself First" ritual.
    
    Rules:
    - Debt target = sum of active minimum_payment_cents
    - Emergency Fund target = min(configured monthly amount, remaining income after debt)
    - All others = 0 (user fills in)
    
    Returns: {category_id: target_cents}
    """
    # Get Debt and Emergency Fund categories
    categories = {
        cat.name: cat.id
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }
    
    debt_cat_id = categories.get("Debt")
    emergency_cat_id = categories.get("Emergency Fund")
    
    defaults = {}
    
    # Debt target = sum of active minimum payments
    if debt_cat_id:
        debt_accounts = ctx.db.query(DebtAccount).filter_by(
            account_id=ctx.account_id
        ).all()
        debt_minimum = sum(d.minimum_payment_cents for d in debt_accounts if d.minimum_payment_cents)
        defaults[debt_cat_id] = debt_minimum
    
    # Emergency Fund target = min(monthly_target, remaining after debt)
    if emergency_cat_id:
        debt_target = defaults.get(debt_cat_id, 0)
        remaining_after_debt = income_cents - debt_target
        ef_target = min(emergency_fund_monthly_target_cents, max(0, remaining_after_debt))
        defaults[emergency_cat_id] = ef_target
    
    return defaults


# ============================================================================
# ZERO-BASED & TRIAGE LOGIC
# ============================================================================

def is_zero_based_confirmed(ctx: AccountContext, period_id: int) -> tuple[bool, int]:
    """
    Check if zero-based budgeting is confirmed (unallocated = 0).
    
    Returns: (is_confirmed: bool, unallocated_cents: int)
    
    When income >= essentials and is_confirmed=False, zero-based block is active.
    """
    unallocated = compute_unallocated_cents(ctx, period_id)
    is_confirmed = (unallocated == 0)
    return is_confirmed, unallocated


def get_triage_state(ctx: AccountContext, period_id: int) -> dict:
    """
    Compute triage state when income < essentials.
    
    Returns: {
        "has_insufficient_income": bool,
        "income_received_cents": int,
        "essentials_target_cents": int,
        "shortfall_cents": int,  # essentials - income (0 if income >= essentials)
        "allows_soft_allocation": bool,  # Always True in triage
        "options": ["lower_target", "defer_emergency_fund", "expect_more_income"],
    }
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()
    
    income = period.income_received_cents if period else 0
    essentials = compute_essentials_target_cents(ctx, period_id)
    
    has_insufficient = income < essentials
    shortfall = max(0, essentials - income)
    
    return {
        "has_insufficient_income": has_insufficient,
        "income_received_cents": income,
        "essentials_target_cents": essentials,
        "shortfall_cents": shortfall,
        "allows_soft_allocation": True,  # Triage always allows soft allocation
        "options": [
            "lower_target",  # User lowers a target
            "defer_emergency_fund",  # Skip emergency fund this month
            "expect_more_income",  # Don't fully allocate, expect more income
        ],
    }
```

- [ ] **Step 2: Run tests to verify they pass (TDD)**

Run:
```bash
cd d:\golden
pytest tests/test_allocation.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Run integration tests**

Run:
```bash
cd d:\golden
pytest tests/integration/test_allocation_flow.py -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit allocation service**

```bash
cd d:\golden
git add finapp/services/allocation.py
git commit -m "feat(phase3): implement core allocation service (TDD)"
```

---

### Task 4: Add Allocation Helper to Balances Service

**Files:**
- Modify: `finapp/services/balances.py`

**Steps:**

- [ ] **Step 1: Read current balances.py**

Run:
```bash
cd d:\golden
type finapp\services\balances.py
```

- [ ] **Step 2: Add essentials target helper**

At the end of `finapp/services/balances.py`, add:

```python


def get_essentials_target_cents(ctx: AccountContext, period_id: int) -> int:
    """
    Helper: sum of essential category targets (Debt, Emergency Fund, Housing, Food, Transportation).
    Used for triage logic.
    """
    from finapp.services.allocation import compute_essentials_target_cents
    return compute_essentials_target_cents(ctx, period_id)
```

- [ ] **Step 3: Commit**

```bash
cd d:\golden
git add finapp/services/balances.py
git commit -m "feat(phase3): add essentials target helper to balances service"
```

---

### Task 5: Create HTTP Routers for Allocation

**Files:**
- Create: `finapp/routers/allocation.py`

**Steps:**

- [ ] **Step 1: Create allocation router**

Create file `finapp/routers/allocation.py`:

```python
"""
Allocation routers: HTTP endpoints for income entry and allocation ritual.
"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from finapp.deps import get_account_context, AccountContext, get_db
from finapp.schemas import (
    IncomeEntryRequest,
    AllocationRitualRequest,
    AllocationRitualResponse,
    AllocationSummaryResponse,
    AllocationTriageResponse,
    BudgetAllocationResponse,
)
from finapp.services.ledger import create_transaction, get_or_create_period
from finapp.services.allocation import (
    set_allocation_targets,
    compute_funded_cents_per_category,
    compute_remaining_cents_per_category,
    compute_unallocated_cents,
    compute_essentials_target_cents,
    is_zero_based_confirmed,
    get_triage_state,
)
from finapp.services.balances import get_budget_spent_cents
from finapp.models import BudgetCategory, BudgetPeriod, BudgetAllocation

router = APIRouter(prefix="/api/allocation", tags=["allocation"])


@router.post("/income")
def log_income(
    request: IncomeEntryRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Log income transaction for the given date.
    Updates period.income_received_cents.
    """
    # Get Income category
    income_cat = ctx.db.query(BudgetCategory).filter_by(
        account_id=ctx.account_id, kind="income"
    ).first()
    
    if not income_cat:
        raise HTTPException(status_code=400, detail="Income category not found")
    
    # Create transaction
    txn = create_transaction(
        ctx,
        date=request.date,
        amount_cents=request.amount_cents,
        direction="in",
        category_id=income_cat.id,
        payee=request.payee,
        memo=request.memo,
    )
    
    # Get the period that was created/updated
    period = get_or_create_period(ctx.db, ctx.account_id, request.date)
    
    return {
        "status": "income_logged",
        "amount_cents": request.amount_cents,
        "period_id": period.id,
    }


@router.post("/targets")
def set_targets(
    request: AllocationRitualRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> AllocationRitualResponse:
    """
    Set allocation targets for a period (allocation ritual).
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=request.period_id, account_id=ctx.account_id
    ).first()
    
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    
    # Convert request format {category_id: target_cents}
    targets = {item["category_id"]: item["target_cents"] for item in request.allocations}
    
    set_allocation_targets(ctx, period_id=request.period_id, category_targets=targets)
    
    # Build response
    categories = {
        cat.id: cat
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }
    
    funded = compute_funded_cents_per_category(ctx, period_id=request.period_id)
    
    allocations = []
    total_target = 0
    
    for alloc in ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=request.period_id
    ).all():
        cat = categories.get(alloc.category_id)
        if not cat:
            continue
        
        spent = get_budget_spent_cents(ctx, request.period_id, alloc.category_id)
        funded_cents = funded.get(alloc.category_id, 0)
        remaining = funded_cents - spent
        
        allocations.append(
            BudgetAllocationResponse(
                id=alloc.id,
                category_id=alloc.category_id,
                category_name=cat.name,
                target_cents=alloc.target_cents,
                funded_cents=funded_cents,
                spent_cents=spent,
                remaining_cents=remaining,
            )
        )
        total_target += alloc.target_cents
    
    unallocated = compute_unallocated_cents(ctx, period_id=request.period_id)
    zero_based_block = (unallocated != 0)
    
    return AllocationRitualResponse(
        period_id=request.period_id,
        allocations=allocations,
        total_target_cents=total_target,
        income_received_cents=period.income_received_cents,
        unallocated_cents=unallocated,
        zero_based_block=zero_based_block,
    )


@router.get("/summary/{period_id}")
def get_allocation_summary(
    period_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> AllocationSummaryResponse:
    """
    Get current allocation state for a period (all categories, funded/spent/remaining).
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()
    
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    
    categories = {
        cat.id: cat
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }
    
    funded = compute_funded_cents_per_category(ctx, period_id=period_id)
    
    allocations = []
    total_target = 0
    total_funded = 0
    total_spent = 0
    
    for alloc in ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all():
        cat = categories.get(alloc.category_id)
        if not cat:
            continue
        
        spent = get_budget_spent_cents(ctx, period_id, alloc.category_id)
        funded_cents = funded.get(alloc.category_id, 0)
        remaining = funded_cents - spent
        
        allocations.append(
            BudgetAllocationResponse(
                id=alloc.id,
                category_id=alloc.category_id,
                category_name=cat.name,
                target_cents=alloc.target_cents,
                funded_cents=funded_cents,
                spent_cents=spent,
                remaining_cents=remaining,
            )
        )
        total_target += alloc.target_cents
        total_funded += funded_cents
        total_spent += spent
    
    unallocated = compute_unallocated_cents(ctx, period_id=period_id)
    is_zero_based = (unallocated == 0)
    
    triage = get_triage_state(ctx, period_id=period_id)
    has_insufficient_income = triage["has_insufficient_income"]
    
    return AllocationSummaryResponse(
        period_id=period_id,
        income_received_cents=period.income_received_cents,
        total_target_cents=total_target,
        total_funded_cents=total_funded,
        total_spent_cents=total_spent,
        unallocated_cents=unallocated,
        is_zero_based=is_zero_based,
        allocations=allocations,
        has_insufficient_income=has_insufficient_income,
    )


@router.get("/triage/{period_id}")
def get_allocation_triage(
    period_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> AllocationTriageResponse:
    """
    Get triage state when income < essentials (soft allocation path).
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()
    
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    
    triage = get_triage_state(ctx, period_id=period_id)
    
    if not triage["has_insufficient_income"]:
        raise HTTPException(status_code=400, detail="Income is sufficient; triage not applicable")
    
    categories = {
        cat.id: cat
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }
    
    funded = compute_funded_cents_per_category(ctx, period_id=period_id)
    
    funded_allocs = []
    unfunded_allocs = []
    
    for alloc in ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all():
        cat = categories.get(alloc.category_id)
        if not cat:
            continue
        
        spent = get_budget_spent_cents(ctx, period_id, alloc.category_id)
        funded_cents = funded.get(alloc.category_id, 0)
        remaining = funded_cents - spent
        
        alloc_response = BudgetAllocationResponse(
            id=alloc.id,
            category_id=alloc.category_id,
            category_name=cat.name,
            target_cents=alloc.target_cents,
            funded_cents=funded_cents,
            spent_cents=spent,
            remaining_cents=remaining,
        )
        
        if funded_cents >= alloc.target_cents:
            funded_allocs.append(alloc_response)
        else:
            unfunded_allocs.append(alloc_response)
    
    message = (
        f"Income ${triage['income_received_cents']/100:.2f} is short by "
        f"${triage['shortfall_cents']/100:.2f} of essentials. "
        f"You can: lower targets, defer Emergency Fund this month, or wait for more income."
    )
    
    return AllocationTriageResponse(
        message=message,
        income_received_cents=triage["income_received_cents"],
        essentials_target_cents=triage["essentials_target_cents"],
        shortfall_cents=triage["shortfall_cents"],
        funded_allocations=funded_allocs,
        unfunded_allocations=unfunded_allocs,
        options=triage["options"],
    )
```

- [ ] **Step 2: Register router in main.py**

Read current main.py:

```bash
cd d:\golden
type finapp\main.py
```

Add this import and router registration:

```python
from finapp.routers import allocation
# ... other routers ...
app.include_router(allocation.router)
```

Find the section where routers are included and add the allocation router.

- [ ] **Step 3: Verify no syntax errors**

Run:
```bash
cd d:\golden
python -m py_compile finapp/routers/allocation.py
python -m py_compile finapp/main.py
```

Expected: No output (success)

- [ ] **Step 4: Commit**

```bash
cd d:\golden
git add finapp/routers/allocation.py finapp/main.py
git commit -m "feat(phase3): add allocation HTTP routers"
```

---

### Task 6: Run All Tests and Verify Reconciliation

**Files:**
- No new files (testing existing services)

**Steps:**

- [ ] **Step 1: Run all allocation tests**

Run:
```bash
cd d:\golden
pytest tests/test_allocation.py tests/integration/test_allocation_flow.py -v
```

Expected: All tests PASS.

- [ ] **Step 2: Run reconciliation tests to verify no drift**

Run:
```bash
cd d:\golden
pytest tests/test_reconciliation.py -v
```

Expected: All tests PASS, especially `test_recompute_balances_property_zero_drift`.

- [ ] **Step 3: Run all tests (comprehensive)**

Run:
```bash
cd d:\golden
pytest tests/ -v --tb=short
```

Expected: All tests PASS. Total coverage for Phase 3:
- 5+ allocation unit tests
- 2+ integration tests
- Reconciliation gate green

- [ ] **Step 4: Commit comprehensive test run**

```bash
cd d:\golden
git add tests/
git commit -m "test(phase3): all allocation tests passing, reconciliation gate green"
```

---

### Task 7: Document Allocation Behavior in Code Comments

**Files:**
- Modify: `finapp/services/allocation.py` (add docstring examples)

**Steps:**

- [ ] **Step 1: Add example docstring to module header**

Update the module docstring in `finapp/services/allocation.py` to include an example:

```python
"""
Allocation service: cumulative monthly envelope and income/allocation flow.
This is Phase 3's core business logic.

EXAMPLE: Zero-based budgeting with cumulative envelopes
---
User receives $2,000 in June.
Targets set: Debt $250, Emergency Fund $500, Housing $800, Food $200, Everything Else $250 ($2,000 total).

Funded computation (priority order):
1. Debt claims $250 (full target), remaining income = $1,750
2. Emergency Fund claims $500 (full target), remaining = $1,250
3. Housing claims $800 (full target), remaining = $450
4. Food claims $200 (full target), remaining = $250
5. Everything Else claims $250 (full target), remaining = $0

Result: unallocated = $0, zero-based block is clear. User can proceed.

EXAMPLE: Biweekly paycheck flow (no ritual reopen)
---
First paycheck (June 1, $1,000): Targets set as above.
  Funded: Debt $250, Emergency Fund $500, Housing $250, others $0.
  Unallocated = $0.

Second paycheck (June 15, $1,000): Income now $2,000 total.
  Targets still fixed from June 1 ritual.
  Funded: Debt $250, Emergency Fund $500, Housing $800, Food $200, Everything Else $250.
  Unallocated = $0 (no ritual reopen).

EXAMPLE: Insufficient income (triage path)
---
Targets: Debt $250, Emergency Fund $500, Housing $400, Food $200 ($1,350 essentials).
Income received: $600 (insufficient).

Triage engages:
  - Funding priority: Debt $250, Emergency Fund $350, Housing $0, Food $0.
  - Shortfall: $750.
  - User options: lower targets, defer Emergency Fund, or wait for more income.
  - NO hard block (allows soft allocation).

Key concepts:
- target_cents: planned amount per category for the month (set once per month)
- funded_cents: portion of received income claimed by targets in priority order
- spent_cents: actual expenses in the category
- remaining_cents: funded - spent (can be negative if overspent)
- unallocated_cents: total income - sum(targets)

Allocation is deterministic and priority-ordered:
1. Debt (minimum payment obligations)
2. Emergency Fund (savings goal)
3. Housing (essential fixed)
4. Food (essential variable)
5. Transportation (essential)
6. Personal (discretionary)
7. Everything Else (catch-all for leftovers)

When income >= essentials: zero-based hard block (must allocate all income).
When income < essentials: triage path (priority funding, visibility into unfunded).
"""
```

- [ ] **Step 2: Commit**

```bash
cd d:\golden
git add finapp/services/allocation.py
git commit -m "docs(phase3): add allocation behavior examples to service docstring"
```

---

### Task 8: Final Verification — Phase 3 Done Gate

**Files:**
- No new files (verification only)

**Steps:**

- [ ] **Step 1: Verify unit test coverage**

Run:
```bash
cd d:\golden
pytest tests/test_allocation.py -v --tb=short
```

Verify output includes:
- `test_allocation_ritual_sets_targets_on_first_paycheck` → PASS
- `test_allocation_ritual_presets_pay_yourself_first_targets` → PASS
- `test_cumulative_funding_biweekly_paychecks` → PASS
- `test_priority_ordering_debt_before_emergency_fund` → PASS
- `test_everything_else_absorbs_leftovers` → PASS
- `test_zero_based_block_when_income_exceeds_targets` → PASS
- `test_insufficient_income_triage_path` → PASS

Expected: All 7 tests PASS.

- [ ] **Step 2: Verify integration test coverage**

Run:
```bash
cd d:\golden
pytest tests/integration/test_allocation_flow.py -v --tb=short
```

Verify output includes:
- `test_full_flow_log_income_allocate_zero_based` → PASS
- `test_biweekly_paycheck_flow` → PASS

Expected: All 2 tests PASS.

- [ ] **Step 3: Verify reconciliation gate (zero drift)**

Run:
```bash
cd d:\golden
pytest tests/test_reconciliation.py::test_recompute_balances_property_zero_drift -v --tb=short
```

Expected: PASS across all seed values (42, 123, 456, 789, 999).

- [ ] **Step 4: Verify ledger tests still pass (no regression)**

Run:
```bash
cd d:\golden
pytest tests/test_ledger.py tests/test_balances.py -v --tb=short
```

Expected: All Phase 2 tests still PASS (no regression).

- [ ] **Step 5: Full test suite green**

Run:
```bash
cd d:\golden
pytest tests/ -v --tb=short
```

Expected: ALL tests PASS. Summary should show:
- 7+ allocation unit tests
- 2+ allocation integration tests
- 5+ ledger tests
- 5+ balance tests
- 5+ reconciliation tests
- Plus seeds, money, other tests

Total: 20+ tests, all PASS.

- [ ] **Step 6: Verify allocation router endpoints respond**

Run (quick endpoint check):
```bash
cd d:\golden
python -c "
from finapp.main import app
from starlette.testclient import TestClient
client = TestClient(app)

# Verify routes are registered
routes = [route.path for route in app.routes if 'allocation' in route.path]
print('Allocation routes registered:', routes)
assert len(routes) > 0, 'No allocation routes found'
print('✓ Allocation router registered')
"
```

Expected: Output shows `/api/allocation/*` routes registered.

- [ ] **Step 7: Final commit (Phase 3 complete)**

```bash
cd d:\golden
git log --oneline -10
```

Verify last 7-8 commits include Phase 3 work:
- `docs(phase3): add allocation behavior examples`
- `test(phase3): all allocation tests passing`
- `feat(phase3): add allocation HTTP routers`
- `feat(phase3): implement core allocation service (TDD)`
- `feat(phase3): add essentials target helper`
- `feat(phase3): add allocation request/response schemas`

---

## Phase 3 Done Gate Summary

**All gates MUST be green before Phase 3 is considered complete:**

1. ✅ **Unit tests for allocation logic PASS** — 7+ tests covering:
   - Allocation ritual target setting
   - Pay Yourself First pre-fills
   - Cumulative funding across paychecks
   - Priority ordering (Debt → Emergency Fund → Housing → ...)
   - Everything Else absorbing leftovers
   - Zero-based block trigger
   - Insufficient income triage path

2. ✅ **Integration tests for user flow PASS** — 2+ tests covering:
   - Full flow: log income → set targets → zero-based confirmation
   - Biweekly paycheck accumulation (no ritual reopen)

3. ✅ **Reconciliation gate still green** — `recompute_balances(ctx)` produces zero drift
   - Property test across randomized transaction sets passes
   - No regression in Phase 2 derived values

4. ✅ **HTTP routers implemented** — POST/GET endpoints for:
   - `/api/allocation/income` → log income transaction
   - `/api/allocation/targets` → set allocation targets
   - `/api/allocation/summary/{period_id}` → get allocation state
   - `/api/allocation/triage/{period_id}` → get triage state

5. ✅ **Architecture invariants upheld:**
   - One write path (ledger.py) — allocations read only, no new writes
   - Transactions are truth, allocations derive from ledger
   - Integer cents only, no new decimals
   - Account-scoped queries (all filtered by ctx.account_id)
   - Loopback only (no changes needed, Phase 0 already set)

---

## Next Phase

**Phase 4: App Shell, Dashboard & Daily Check-In** depends on Phase 3 being complete.
Once Phase 3 is green, phase 4 can begin (no blocking dependencies).

---

*End of Phase 3 implementation plan.*
