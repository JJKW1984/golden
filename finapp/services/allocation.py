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
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from finapp.deps import AccountContext
from finapp.models import (
    BudgetAllocation,
    BudgetPeriod,
    BudgetCategory,
    DebtAccount,
)
from finapp.services.balances import get_budget_spent_cents


# Priority order: Debt → Emergency Fund → Housing → Food → Transportation → Personal → Everything Else
PRIORITY_ORDER = [
    "Debt",
    "Emergency Fund",
    "Housing",
    "Food",
    "Transportation",
    "Personal",
    "Everything Else",
]

# Essential categories: Debt, Emergency Fund, Housing, Food, Transportation
ESSENTIAL_CATEGORIES = ["Debt", "Emergency Fund", "Housing", "Food", "Transportation"]


def compute_funded_cents_per_category(ctx: AccountContext, period_id: int) -> dict:
    """
    Compute funded_cents per category for a period.

    Funded is derived from targets and received income.
    Categories claim income in priority order, cumulatively.
    Everything Else absorbs any leftover income.

    Args:
        ctx: AccountContext (account_id, db)
        period_id: BudgetPeriod id

    Returns:
        {category_id: funded_cents} where funded_cents is the portion of income
        this category has claimed so far. Can be less than target if income insufficient.
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id
    ).first()

    if not period:
        return {}

    # Get all allocations for this period
    allocations = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all()

    alloc_by_cat_id = {a.category_id: a.target_cents for a in allocations}

    # Build {category_name: category_id} for priority ordering
    categories = ctx.db.query(BudgetCategory).filter_by(
        account_id=ctx.account_id
    ).all()

    cat_by_name = {c.name: c.id for c in categories}

    # Initialize funded dict with all categories
    funded = {c.id: 0 for c in categories}

    # Allocate income in priority order
    remaining_income = period.income_received_cents

    for cat_name in PRIORITY_ORDER:
        if cat_name not in cat_by_name:
            continue

        cat_id = cat_by_name[cat_name]
        target = alloc_by_cat_id.get(cat_id, 0)

        # Special handling for Everything Else: absorbs leftover income
        if cat_name == "Everything Else":
            # Everything Else gets any remaining income
            funded[cat_id] = remaining_income
            remaining_income = 0
        else:
            # Other categories claim min(remaining_income, target)
            claim = min(remaining_income, target)
            funded[cat_id] = claim
            remaining_income -= claim

        if remaining_income <= 0:
            break

    return funded


def compute_remaining_cents_per_category(ctx: AccountContext, period_id: int) -> dict:
    """
    Compute remaining_cents per category (funded - spent).

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id

    Returns:
        {category_id: remaining_cents} where remaining can be negative if overspent.
    """
    funded = compute_funded_cents_per_category(ctx, period_id)

    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id
    ).first()

    if not period:
        return {}

    remaining = {}

    # For each category with a target, compute remaining
    allocations = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all()

    for alloc in allocations:
        cat_id = alloc.category_id
        funded_amt = funded.get(cat_id, 0)
        spent = get_budget_spent_cents(ctx, period_id, cat_id)
        remaining[cat_id] = funded_amt - spent

    return remaining


def compute_essentials_target_cents(ctx: AccountContext, period_id: int) -> int:
    """
    Compute sum of essential category targets (Debt, Emergency Fund, Housing, Food, Transportation).

    Used for triage logic: if income < essentials_target, engage soft-allocation path.

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id

    Returns:
        Sum of essential category targets (cents).
    """
    categories = ctx.db.query(BudgetCategory).filter_by(
        account_id=ctx.account_id
    ).all()

    cat_by_name = {c.name: c.id for c in categories}

    essential_cat_ids = [
        cat_by_name[name] for name in ESSENTIAL_CATEGORIES if name in cat_by_name
    ]

    if not essential_cat_ids:
        return 0

    allocations = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).filter(BudgetAllocation.category_id.in_(essential_cat_ids)).all()

    total = sum(a.target_cents for a in allocations)
    return total


def compute_unallocated_cents(ctx: AccountContext, period_id: int) -> int:
    """
    Compute unallocated income: income_received - sum(all targets).

    When positive, income exceeds targets (hard block until allocated).
    When negative, income is insufficient (triage case).

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id

    Returns:
        Unallocated cents (can be negative).
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id
    ).first()

    if not period:
        return 0

    # Sum all target allocations for this period
    total_targets = ctx.db.query(func.sum(BudgetAllocation.target_cents)).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).scalar()

    total_targets = total_targets or 0

    return period.income_received_cents - total_targets


def has_allocations_for_period(ctx: AccountContext, period_id: int) -> bool:
    """
    Check if BudgetAllocations exist for a period.

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id

    Returns:
        True if any allocations exist for the period.
    """
    count = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).count()

    return count > 0


def set_allocation_targets(
    ctx: AccountContext, period_id: int, category_targets: dict
) -> dict:
    """
    Create or update BudgetAllocation rows for a period.

    Idempotent: updates existing allocations, creates new ones.

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id
        category_targets: {category_id: target_cents}

    Returns:
        {"status": "targets_set", "total_target_cents": int}
    """
    total_target = 0

    for category_id, target_cents in category_targets.items():
        # Try to find existing allocation
        alloc = ctx.db.query(BudgetAllocation).filter_by(
            account_id=ctx.account_id,
            period_id=period_id,
            category_id=category_id,
        ).first()

        if alloc:
            # Update
            alloc.target_cents = target_cents
        else:
            # Create
            alloc = BudgetAllocation(
                account_id=ctx.account_id,
                period_id=period_id,
                category_id=category_id,
                target_cents=target_cents,
            )
            ctx.db.add(alloc)

        total_target += target_cents

    ctx.db.commit()

    return {
        "status": "targets_set",
        "total_target_cents": total_target,
    }


def compute_default_targets(
    ctx: AccountContext,
    period_id: int,
    income_cents: int,
    emergency_fund_monthly_target_cents: int,
) -> dict:
    """
    Compute Pay Yourself First defaults for the allocation ritual.

    Pre-fills:
    - Debt target = sum of active DebtAccount minimum_payment_cents
    - Emergency Fund target = min(monthly_target, remaining_after_debt)
    - Others = 0

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id
        income_cents: Received income (not used directly, but context)
        emergency_fund_monthly_target_cents: Monthly EF target amount

    Returns:
        {category_id: target_cents} with Debt and Emergency Fund pre-filled
    """
    categories = ctx.db.query(BudgetCategory).filter_by(
        account_id=ctx.account_id
    ).all()

    cat_by_name = {c.name: c.id for c in categories}

    defaults = {}

    # Debt target = sum of active debt minimums
    debt_cat_id = cat_by_name.get("Debt")
    if debt_cat_id:
        debt_accounts = ctx.db.query(DebtAccount).filter_by(
            account_id=ctx.account_id, is_active=True
        ).all()

        debt_minimum_total = sum(d.minimum_payment_cents for d in debt_accounts)
        defaults[debt_cat_id] = debt_minimum_total

    # Emergency Fund target = min(monthly_target, remaining after debt)
    emergency_cat_id = cat_by_name.get("Emergency Fund")
    if emergency_cat_id:
        debt_minimum = defaults.get(debt_cat_id, 0)
        remaining_after_debt = income_cents - debt_minimum

        ef_target = min(
            emergency_fund_monthly_target_cents,
            max(0, remaining_after_debt)
        )
        defaults[emergency_cat_id] = ef_target

    # All other categories default to 0
    for cat in categories:
        if cat.id not in defaults:
            defaults[cat.id] = 0

    return defaults


def is_zero_based_confirmed(ctx: AccountContext, period_id: int) -> tuple:
    """
    Check if period is zero-based confirmed (unallocated == 0).

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id

    Returns:
        (is_confirmed: bool, unallocated_cents: int)
        is_confirmed = True if unallocated == 0
    """
    unallocated = compute_unallocated_cents(ctx, period_id)

    is_confirmed = (unallocated == 0)

    return (is_confirmed, unallocated)


def get_triage_state(ctx: AccountContext, period_id: int) -> dict:
    """
    Get triage state when income < essentials.

    When received income is insufficient to cover essential category targets,
    the allocation engine engages a soft-allocation path (no hard block).

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id

    Returns:
        {
            "has_insufficient_income": bool,
            "income_received_cents": int,
            "essentials_target_cents": int,
            "shortfall_cents": int,
            "allows_soft_allocation": bool,
            "options": ["lower_target", "defer_emergency_fund", "expect_more_income"]
        }
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id
    ).first()

    if not period:
        return {
            "has_insufficient_income": False,
            "income_received_cents": 0,
            "essentials_target_cents": 0,
            "shortfall_cents": 0,
            "allows_soft_allocation": True,
            "options": [],
        }

    essentials_target = compute_essentials_target_cents(ctx, period_id)
    income = period.income_received_cents

    has_insufficient = (income < essentials_target)
    shortfall = max(0, essentials_target - income)

    return {
        "has_insufficient_income": has_insufficient,
        "income_received_cents": income,
        "essentials_target_cents": essentials_target,
        "shortfall_cents": shortfall,
        "allows_soft_allocation": True,
        "options": ["lower_target", "defer_emergency_fund", "expect_more_income"],
    }
