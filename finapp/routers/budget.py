"""
Budget routers: HTTP endpoints for budget screens, history, and API.

Endpoints:
- GET /budget: Budget HTML page (Screen 2)
- GET /budget/{year}/{month}: Budget history period view
- GET /api/budget/summary/{period_id}: Budget summary JSON
- GET /api/budget/pulse: Weekly spending pulse JSON
- POST /budget/reallocate: Reallocate funds between categories
"""
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from finapp.deps import get_account_context, AccountContext
from finapp.schemas import BudgetSummaryResponse, DashboardPulseResponse, BudgetReallocateRequest, BudgetReallocateResponse
from finapp.services.budget import (
    get_budget_summary,
    get_budget_history,
    get_available_categories_for_reallocation,
)
from finapp.services.ledger import get_or_create_period
from finapp.services.balances import get_budget_spent_cents
from finapp.models import BudgetAllocation, BudgetPeriod, Transaction  # Ensure models are imported for test setup

# Initialize templates
templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["budget"])


@router.get("/api/budget/summary/{period_id}", response_model=BudgetSummaryResponse)
def get_summary(
    period_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> BudgetSummaryResponse:
    """
    Get budget summary for a period (all categories, targets, funded, spent, remaining).

    Args:
        period_id: BudgetPeriod id
        ctx: AccountContext dependency

    Returns:
        BudgetSummaryResponse with all category data:
        - period_id, year, month
        - income_received_cents
        - total_target_cents, total_funded_cents, total_spent_cents, total_remaining_cents
        - unallocated_cents, is_zero_based
        - categories: list of BudgetTableRowResponse

    Status: 200 OK
    Raises: 404 if period not found
    """
    data = get_budget_summary(ctx, period_id)
    return BudgetSummaryResponse(**data)


@router.get("/budget", response_class=HTMLResponse)
def get_budget_page(
    request: Request,
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """
    Budget HTML page (Screen 2).

    Displays:
    - Current month budget with all categories (target, funded, spent, remaining)
    - Budget history tab showing past periods
    - Unallocated indicator and zero-based status
    - Reallocate buttons for moving funds between categories

    Status: 200 OK
    """
    # Get current month's period
    today = date.today()
    current_period = get_or_create_period(ctx.db, ctx.account_id, today)

    # Get budget summary for current period
    current_summary = get_budget_summary(ctx, current_period.id)

    # Get budget history (all periods)
    history = get_budget_history(ctx)

    # Render template with data
    return templates.TemplateResponse(
        request,
        "budget.html",
        {
            "current_summary": current_summary,
            "history": history,
        }
    )


@router.get("/budget/{year}/{month}", response_class=HTMLResponse)
def get_budget_history_page(
    year: int,
    month: int,
    request: Request,
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """
    Budget history page for a specific period.

    Displays budget details for the given year/month.

    Args:
        year: Budget year (e.g., 2026)
        month: Budget month (1-12)
        request: Starlette Request
        ctx: AccountContext dependency

    Returns:
        Rendered budget.html template with the specified period's data

    Status: 200 OK
    Raises: 404 if period not found
    """
    # Find period by year/month
    from finapp.models import BudgetPeriod

    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id,
        year=year,
        month=month,
    ).first()

    if not period:
        raise HTTPException(status_code=404, detail="Period not found")

    # Get budget summary for this period
    summary = get_budget_summary(ctx, period.id)

    # Get history for sidebar
    history = get_budget_history(ctx)

    # Render template
    return templates.TemplateResponse(
        request,
        "budget.html",
        {
            "current_summary": summary,
            "history": history,
        }
    )


@router.get("/api/budget/pulse", response_model=DashboardPulseResponse)
def get_pulse(
    ctx: AccountContext = Depends(get_account_context),
) -> DashboardPulseResponse:
    """
    Get weekly spending pulse (this week's spending vs target).

    Calculates:
    - spent_cents: Sum of expenses from 7 days ago to today
    - target_cents: Daily budget target × 7
    - percentage: (spent_cents / target_cents) × 100

    The target is computed as (total_allocated / days_in_month) × 7,
    where total_allocated is the sum of all allocation targets for the current period.

    Returns:
        DashboardPulseResponse with spent_cents, target_cents, percentage (0-100)

    Status: 200 OK
    """
    today = date.today()
    week_ago = today - timedelta(days=7)

    # Get current period
    current_period = get_or_create_period(ctx.db, ctx.account_id, today)

    # Calculate this week's spending
    from sqlalchemy import func
    from finapp.models import Transaction

    spent_row = ctx.db.query(func.sum(Transaction.amount_cents)).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.period_id == current_period.id,
        Transaction.direction == "out",
        Transaction.is_deleted == False,
        Transaction.link_type == None,
        Transaction.date >= week_ago,
        Transaction.date <= today,
    ).scalar()

    spent_cents = spent_row or 0

    # Calculate daily budget target
    # Sum all allocation targets for the period, divide by days in month, multiply by 7
    from finapp.models import BudgetAllocation
    import calendar

    total_allocated = ctx.db.query(func.sum(BudgetAllocation.target_cents)).filter(
        BudgetAllocation.account_id == ctx.account_id,
        BudgetAllocation.period_id == current_period.id,
    ).scalar()

    total_allocated = total_allocated or 0

    # Days in the current month
    days_in_month = calendar.monthrange(current_period.year, current_period.month)[1]

    # Daily target
    daily_target_cents = total_allocated // days_in_month if days_in_month > 0 else 0

    # Weekly target (7 days)
    target_cents = daily_target_cents * 7

    # Percentage (avoid division by zero)
    percentage = 0
    if target_cents > 0:
        percentage = min(100, (spent_cents * 100) // target_cents)

    return DashboardPulseResponse(
        spent_cents=spent_cents,
        target_cents=target_cents,
        percentage=percentage,
    )


@router.post("/budget/reallocate", response_model=BudgetReallocateResponse)
def post_reallocate(
    request_body: BudgetReallocateRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> BudgetReallocateResponse:
    """
    Move funds between budget allocations within a period.

    Decreases from_category_id's target by amount_cents and increases
    to_category_id's target by the same amount. The zero-based constraint
    is not enforced here (reallocation can increase unallocated).

    Args:
        request_body: BudgetReallocateRequest with period_id, from_category_id,
                      to_category_id, amount_cents
        ctx: AccountContext dependency

    Returns:
        BudgetReallocateResponse with updated target amounts

    Status: 200 OK
    Raises: 404 if period or allocations not found, 400 if insufficient headroom
    """
    # Verify period exists
    from finapp.models import BudgetPeriod

    period = ctx.db.query(BudgetPeriod).filter_by(
        id=request_body.period_id,
        account_id=ctx.account_id,
    ).first()

    if not period:
        raise HTTPException(status_code=404, detail="Period not found")

    # Get the from and to allocations
    from_alloc = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id,
        period_id=request_body.period_id,
        category_id=request_body.from_category_id,
    ).first()

    if not from_alloc:
        raise HTTPException(
            status_code=404,
            detail=f"From allocation not found for category {request_body.from_category_id}"
        )

    to_alloc = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id,
        period_id=request_body.period_id,
        category_id=request_body.to_category_id,
    ).first()

    if not to_alloc:
        raise HTTPException(
            status_code=404,
            detail=f"To allocation not found for category {request_body.to_category_id}"
        )

    # Check that from_category has sufficient headroom
    # Headroom = target - spent
    from_spent = get_budget_spent_cents(ctx, request_body.period_id, request_body.from_category_id)
    from_headroom = from_alloc.target_cents - from_spent

    if from_headroom < request_body.amount_cents:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient headroom in from_category. Headroom: {from_headroom}, Requested: {request_body.amount_cents}"
        )

    # Perform reallocation
    from_alloc.target_cents -= request_body.amount_cents
    to_alloc.target_cents += request_body.amount_cents

    ctx.db.commit()

    return BudgetReallocateResponse(
        from_allocation_id=from_alloc.id,
        to_allocation_id=to_alloc.id,
        from_target_cents=from_alloc.target_cents,
        to_target_cents=to_alloc.target_cents,
    )
