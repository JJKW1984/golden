"""
Debt router: HTTP endpoints for debt management.

Endpoints:
- GET /debt: List all debt accounts (HTML page)
- POST /debt/{id}/payment: Record a debt payment
- GET /api/debt/projection/{id}: Get payoff projection scenarios (JSON)
- POST /debt/{id}/adjustment: Record a balance adjustment
"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel

from finapp.deps import get_account_context, AccountContext
from finapp.models import DebtAccount
from finapp.services.ledger import create_transaction, create_debt_adjustment
from finapp.services.debt_payoff import project_debt_payoff

# Initialize templates
templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["debt"])


class DebtPaymentRequest(BaseModel):
    """Debt payment request."""
    payment_cents: int
    memo: Optional[str] = None


class DebtAdjustmentRequest(BaseModel):
    """Debt balance adjustment request."""
    adjustment_cents: int
    statement_balance_cents: int
    memo: Optional[str] = None


@router.get("/debt", response_class=HTMLResponse)
def get_debt_list(
    request: Request,
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """
    Get debt list page (HTML).

    Returns:
        HTML page with all debt accounts sorted by sort_order
    """
    debts = ctx.db.query(DebtAccount).filter_by(
        account_id=ctx.account_id,
        is_active=True
    ).order_by(DebtAccount.sort_order).all()

    # Convert to dicts for template rendering
    debt_list = []
    for debt in debts:
        debt_list.append({
            "id": debt.id,
            "name": debt.name,
            "creditor": debt.creditor,
            "balance": debt.cached_balance_cents / 100,
            "interest_rate": debt.interest_rate_bps / 100,
            "minimum_payment": debt.minimum_payment_cents / 100,
        })

    return templates.TemplateResponse(
        "debt.html",
        {
            "request": request,
            "debts": debt_list,
        },
    )


@router.post("/debt/{debt_id}/payment")
def post_debt_payment(
    debt_id: int,
    request: DebtPaymentRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Record a debt payment.

    Args:
        debt_id: ID of the debt account
        request: {
            "payment_cents": 50000,
            "memo": "Extra payment"
        }

    Returns:
        {
            "transaction_id": id,
            "principal": amount in dollars,
            "interest": amount in dollars,
            "new_balance": balance after payment in dollars
        }

    Raises:
        404: if debt not found
    """
    # Verify debt exists and belongs to account
    debt = ctx.db.query(DebtAccount).filter_by(
        id=debt_id,
        account_id=ctx.account_id
    ).first()

    if not debt:
        raise HTTPException(status_code=404, detail="Debt not found")

    # Create payment transaction through ledger
    txn = create_transaction(
        ctx=ctx,
        date=date.today(),
        amount_cents=request.payment_cents,
        direction="out",
        category_id=None,  # Debt payments don't use budget categories
        payee=debt.name,
        memo=request.memo or f"Payment to {debt.name}",
        mood_tag=None,
        link_type="debt",
        link_id=debt_id
    )

    # Refresh debt to get updated cached_balance_cents
    ctx.db.refresh(debt)

    return {
        "transaction_id": txn.id,
        "principal": txn.principal_cents / 100,
        "interest": txn.interest_cents / 100,
        "new_balance": debt.cached_balance_cents / 100
    }


@router.post("/debt/{debt_id}/adjustment")
def post_debt_adjustment(
    debt_id: int,
    request: DebtAdjustmentRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Record a debt balance adjustment.

    Used when reconciling against a statement balance.

    Args:
        debt_id: ID of the debt account
        request: {
            "adjustment_cents": -2000,
            "statement_balance_cents": 48000,
            "memo": "Reconcile with statement"
        }

    Returns:
        {
            "transaction_id": id,
            "adjustment": adjustment amount in dollars,
            "new_balance": balance after adjustment in dollars
        }

    Raises:
        404: if debt not found
    """
    # Verify debt exists and belongs to account
    debt = ctx.db.query(DebtAccount).filter_by(
        id=debt_id,
        account_id=ctx.account_id
    ).first()

    if not debt:
        raise HTTPException(status_code=404, detail="Debt not found")

    # Create adjustment transaction through ledger
    txn = create_debt_adjustment(
        ctx=ctx,
        debt_id=debt_id,
        adjustment_cents=request.adjustment_cents,
        statement_balance_cents=request.statement_balance_cents
    )

    # Refresh debt to get updated cached_balance_cents
    ctx.db.refresh(debt)

    return {
        "transaction_id": txn.id,
        "adjustment": request.adjustment_cents / 100,
        "new_balance": debt.cached_balance_cents / 100
    }


@router.get("/api/debt/projection/{debt_id}")
def get_debt_projection(
    debt_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Get payoff projection scenarios for a debt.

    Returns JSON with 3 scenarios:
    - minimum: minimum payment
    - +50: minimum + $50
    - +100: minimum + $100

    Args:
        debt_id: ID of the debt account

    Returns:
        {
            "scenarios": [
                {
                    "name": "minimum",
                    "payment": amount in dollars,
                    "months": months to payoff,
                    "total_interest": total interest in dollars,
                    "payoff_date": "YYYY-MM-DD" or "Never",
                    "payment_gte_interest": bool
                },
                ...
            ]
        }

    Raises:
        404: if debt not found
    """
    # Verify debt exists and belongs to account
    debt = ctx.db.query(DebtAccount).filter_by(
        id=debt_id,
        account_id=ctx.account_id
    ).first()

    if not debt:
        raise HTTPException(status_code=404, detail="Debt not found")

    minimum = debt.minimum_payment_cents or 0

    scenarios = []
    for payment_delta, label in [
        (0, "minimum"),
        (50, "+$50"),
        (100, "+$100")
    ]:
        payment = minimum + payment_delta
        months, total_interest, payoff_date, payment_gte_interest = project_debt_payoff(
            opening_balance_cents=debt.cached_balance_cents,
            interest_rate_bps=debt.interest_rate_bps,
            monthly_payment_cents=payment,
            scenario=label
        )

        scenarios.append({
            "name": label,
            "payment": payment / 100,
            "months": months,
            "total_interest": total_interest / 100,
            "payoff_date": payoff_date,
            "payment_gte_interest": payment_gte_interest
        })

    return {"scenarios": scenarios}
