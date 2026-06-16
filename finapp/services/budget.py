"""
Budget service: read-only helpers for budget summary, history, and reallocation queries.

All amounts are INTEGER cents. All queries are account-scoped via ctx.account_id.
Balances are derived from transactions via services.allocation and services.balances.
"""
from sqlalchemy import func
from fastapi import HTTPException

from finapp.deps import AccountContext
from finapp.models import (
    BudgetPeriod,
    BudgetAllocation,
    Transaction,
)
from finapp.services.balances import get_budget_spent_cents
from finapp.services.allocation import (
    compute_funded_cents_per_category,
    compute_unallocated_cents,
)


def get_budget_summary(ctx: AccountContext, period_id: int) -> dict:
    """
    Return comprehensive budget summary for a period.

    Returns:
        {
            'period_id': int,
            'year': int,
            'month': int,
            'income_received_cents': int,
            'total_target_cents': int,
            'total_funded_cents': int,
            'total_spent_cents': int,
            'total_remaining_cents': int,
            'unallocated_cents': int,
            'is_zero_based': bool,
            'categories': [
                {
                    'category_id': int,
                    'category_name': str,
                    'target_cents': int,
                    'funded_cents': int,
                    'spent_cents': int,
                    'remaining_cents': int,
                    'is_overspent': bool,
                },
                ...
            ]
        }

    Raises:
        HTTPException 404 if period not found.
    """
    # Fetch period, raise 404 if not found
    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id
    ).first()

    if not period:
        raise HTTPException(status_code=404, detail="Period not found")

    # Get funded amounts per category
    funded_dict = compute_funded_cents_per_category(ctx, period_id)

    # Pre-compute all spent amounts in a single query
    spent_rows = (
        ctx.db.query(
            Transaction.category_id,
            func.sum(Transaction.amount_cents).label("total"),
        )
        .filter(
            Transaction.account_id == ctx.account_id,
            Transaction.period_id == period_id,
            Transaction.direction == "out",
            Transaction.is_deleted == False,
            Transaction.link_type == None,
        )
        .group_by(Transaction.category_id)
        .all()
    )
    spent_by_category = {row.category_id: row.total for row in spent_rows}

    # Get all allocations for this period (with targets)
    allocations = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all()

    # Build category details
    categories = []
    total_target_cents = 0
    total_funded_cents = 0
    total_spent_cents = 0

    for alloc in allocations:
        category = alloc.category

        if not category:
            continue

        target_cents = alloc.target_cents
        funded_cents = funded_dict.get(alloc.category_id, 0)
        spent_cents = spent_by_category.get(alloc.category_id, 0)
        remaining_cents = funded_cents - spent_cents
        is_overspent = spent_cents > funded_cents

        categories.append({
            "category_id": category.id,
            "category_name": category.name,
            "target_cents": target_cents,
            "funded_cents": funded_cents,
            "spent_cents": spent_cents,
            "remaining_cents": remaining_cents,
            "is_overspent": is_overspent,
        })

        total_target_cents += target_cents
        total_funded_cents += funded_cents
        total_spent_cents += spent_cents

    # Compute unallocated and zero-based flag
    unallocated_cents = compute_unallocated_cents(ctx, period_id)
    is_zero_based = unallocated_cents == 0
    total_remaining_cents = total_funded_cents - total_spent_cents

    return {
        "period_id": period.id,
        "year": period.year,
        "month": period.month,
        "income_received_cents": period.income_received_cents,
        "total_target_cents": total_target_cents,
        "total_funded_cents": total_funded_cents,
        "total_spent_cents": total_spent_cents,
        "total_remaining_cents": total_remaining_cents,
        "unallocated_cents": unallocated_cents,
        "is_zero_based": is_zero_based,
        "categories": categories,
    }


def get_budget_history(ctx: AccountContext, year: int | None = None) -> list[dict]:
    """
    Return list of budget periods (both active and closed), ordered by year DESC, month DESC.

    Args:
        ctx: AccountContext
        year: Optional filter to a specific year

    Returns:
        List of period dicts: {period_id, year, month, income_received_cents,
        total_spent_cents, total_target_cents}
    """
    query = ctx.db.query(BudgetPeriod).filter_by(account_id=ctx.account_id)

    if year is not None:
        query = query.filter(BudgetPeriod.year == year)

    periods = query.order_by(
        BudgetPeriod.year.desc(),
        BudgetPeriod.month.desc(),
    ).all()

    # Pre-compute all period targets in one query
    target_rows = (
        ctx.db.query(
            BudgetAllocation.period_id,
            func.sum(BudgetAllocation.target_cents).label("total"),
        )
        .filter(BudgetAllocation.account_id == ctx.account_id)
        .group_by(BudgetAllocation.period_id)
        .all()
    )
    targets_by_period = {row.period_id: row.total for row in target_rows}

    # Pre-compute all period spending in one query
    spent_rows = (
        ctx.db.query(
            Transaction.period_id,
            func.sum(Transaction.amount_cents).label("total"),
        )
        .filter(
            Transaction.account_id == ctx.account_id,
            Transaction.direction == "out",
            Transaction.is_deleted == False,
            Transaction.link_type == None,
        )
        .group_by(Transaction.period_id)
        .all()
    )
    spents_by_period = {row.period_id: row.total for row in spent_rows}

    result = []
    for period in periods:
        total_target = targets_by_period.get(period.id, 0)
        total_spent = spents_by_period.get(period.id, 0)

        result.append({
            "period_id": period.id,
            "year": period.year,
            "month": period.month,
            "income_received_cents": period.income_received_cents,
            "total_spent_cents": total_spent,
            "total_target_cents": total_target,
        })

    return result


def get_available_categories_for_reallocation(ctx: AccountContext, period_id: int) -> list[dict]:
    """
    Return categories with headroom (target - spent > 0), sorted by headroom descending.

    Headroom = target - spent. Only includes categories where headroom > 0.

    Args:
        ctx: AccountContext
        period_id: BudgetPeriod id

    Returns:
        List of dicts: {category_id, category_name, target_cents, spent_cents, headroom_cents}
        Sorted by headroom_cents DESC.

    Raises:
        HTTPException 404 if period not found.
    """
    # Check if period exists
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")

    # Fetch all allocations for this period
    allocations = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all()

    available = []

    for alloc in allocations:
        # Get category details
        category = alloc.category

        if not category:
            continue

        # Get spent for this category
        spent_cents = get_budget_spent_cents(ctx, period_id, alloc.category_id)

        # Calculate headroom
        headroom_cents = alloc.target_cents - spent_cents

        # Only include if headroom > 0
        if headroom_cents > 0:
            available.append({
                "category_id": category.id,
                "category_name": category.name,
                "target_cents": alloc.target_cents,
                "spent_cents": spent_cents,
                "headroom_cents": headroom_cents,
            })

    # Sort by headroom_cents DESC
    available.sort(key=lambda x: x["headroom_cents"], reverse=True)

    return available
