"""
Savings router: HTTP endpoints for savings goals, asset accounts, and net worth.

Endpoints:
- GET  /savings: Savings screen (Emergency Fund card + Other Goals) — HTML
- POST /savings/contribution: -> ledger (link_type='savings', direction='out')
- POST /savings/withdrawal: -> ledger (link_type='savings', direction='in')
- GET  /api/savings/emergency: Emergency Fund card data — JSON
- GET  /assets: Asset account list — HTML
- POST /assets: Add/update an asset account balance snapshot
- GET  /api/networth: Net worth summary — JSON
"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from finapp.deps import get_account_context, AccountContext
from finapp.models import SavingsGoal, AssetAccount
from finapp.services.ledger import create_transaction
from finapp.services.balances import (
    get_savings_goal_balance_cents,
    get_days_of_expenses_coverage,
    estimate_savings_completion,
    get_net_worth_cents,
)
from finapp.schemas import (
    SavingsContributionRequest,
    SavingsWithdrawalRequest,
    AssetAccountRequest,
)

templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["savings"])


def _get_goal_or_404(ctx: AccountContext, goal_id: int) -> SavingsGoal:
    goal = ctx.db.query(SavingsGoal).filter_by(
        id=goal_id, account_id=ctx.account_id
    ).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Savings goal not found")
    return goal


@router.post("/savings/contribution")
def post_savings_contribution(
    request: SavingsContributionRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """Record a contribution to a savings goal."""
    goal = _get_goal_or_404(ctx, request.goal_id)

    create_transaction(
        ctx=ctx,
        date=date.today(),
        amount_cents=request.amount_cents,
        direction="out",
        category_id=None,
        payee="",
        memo=request.memo or f"Contribution to {goal.name}",
        link_type="savings",
        link_id=goal.id,
    )

    ctx.db.refresh(goal)
    return {"goal_id": goal.id, "new_balance_cents": goal.cached_balance_cents}


@router.post("/savings/withdrawal")
def post_savings_withdrawal(
    request: SavingsWithdrawalRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """Record a withdrawal from a savings goal."""
    goal = _get_goal_or_404(ctx, request.goal_id)

    create_transaction(
        ctx=ctx,
        date=date.today(),
        amount_cents=request.amount_cents,
        direction="in",
        category_id=None,
        payee="",
        memo=request.memo or f"Withdrawal from {goal.name}",
        link_type="savings",
        link_id=goal.id,
    )

    ctx.db.refresh(goal)
    return {"goal_id": goal.id, "new_balance_cents": goal.cached_balance_cents}


def _emergency_fund_goal(ctx: AccountContext) -> SavingsGoal | None:
    return ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, goal_type="emergency_fund", is_active=True
    ).first()


@router.get("/api/savings/emergency")
def get_emergency_fund(ctx: AccountContext = Depends(get_account_context)) -> dict:
    """Get Emergency Fund card data (Screen 5)."""
    goal = _emergency_fund_goal(ctx)
    if not goal:
        raise HTTPException(status_code=404, detail="No emergency fund goal found")

    balance = get_savings_goal_balance_cents(ctx, goal.id)
    days, estimated = get_days_of_expenses_coverage(ctx, goal.id)
    months, est_date = estimate_savings_completion(ctx, goal.id)

    percent_complete = 0
    if goal.target_cents > 0:
        percent_complete = int(100 * balance / goal.target_cents)

    return {
        "goal_id": goal.id,
        "balance_cents": balance,
        "target_cents": goal.target_cents,
        "percent_complete": percent_complete,
        "days_of_coverage": days,
        "days_of_coverage_estimated": estimated,
        "est_complete_months": months,
        "est_complete_date": est_date,
    }


@router.get("/savings", response_class=HTMLResponse)
def get_savings_screen(
    request: Request,
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """Savings screen: Emergency Fund card + Other Goals."""
    goals = ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, is_active=True
    ).all()

    emergency = None
    other_goals = []

    for goal in goals:
        balance = get_savings_goal_balance_cents(ctx, goal.id)
        percent_complete = int(100 * balance / goal.target_cents) if goal.target_cents > 0 else 0

        if goal.goal_type == "emergency_fund":
            days, estimated = get_days_of_expenses_coverage(ctx, goal.id)
            months, est_date = estimate_savings_completion(ctx, goal.id)
            emergency = {
                "id": goal.id,
                "name": goal.name,
                "balance": balance / 100,
                "target": goal.target_cents / 100,
                "percent_complete": percent_complete,
                "days_of_coverage": days,
                "days_estimated": estimated,
                "est_complete_date": est_date,
                "is_complete": goal.is_complete,
            }
        else:
            other_goals.append({
                "id": goal.id,
                "name": goal.name,
                "balance": balance / 100,
                "target": goal.target_cents / 100,
                "percent_complete": percent_complete,
                "is_complete": goal.is_complete,
            })

    return templates.TemplateResponse(
        "savings.html",
        {
            "request": request,
            "emergency": emergency,
            "other_goals": other_goals,
        },
    )
