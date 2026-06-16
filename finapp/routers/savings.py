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
