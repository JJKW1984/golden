"""
Transactions routers: HTTP endpoints for transaction CRUD.

Endpoints:
- GET /transactions: List transactions (HTML page with filters)
- POST /transactions: Create a transaction
- GET /api/transactions/{id}: Get single transaction (JSON)
- POST /transactions/{id}: Update transaction
- POST /transactions/{id}/delete: Soft delete transaction
- POST /transactions/{id}/restore: Restore deleted transaction
- GET /api/transactions/recent: Recent transactions (JSON)
"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pydantic import BaseModel
from finapp.deps import get_account_context, AccountContext
from finapp.schemas import TransactionRequest, TransactionResponse
from finapp.services.transactions import (
    create_transaction_in_period,
    update_transaction,
    soft_delete_transaction,
    restore_transaction,
    get_transaction_by_id,
    list_transactions,
)
from finapp.services.ledger import get_or_create_period
from finapp.models import BudgetCategory, Transaction

# Initialize templates
templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["transactions"])


class TransactionUpdateRequest(BaseModel):
    """Partial transaction update request - all fields optional."""
    date: Optional[date] = None
    amount_cents: Optional[int] = None
    category_id: Optional[int] = None
    payee: Optional[str] = None
    memo: Optional[str] = None
    mood_tag: Optional[str] = None


@router.get("/transactions", response_class=HTMLResponse)
def get_transactions_page(
    request: Request,
    category_id: int = Query(None),
    mood_tag: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """
    Get transactions page (Screen 3).
    Renders HTML with list of current period transactions and filters.

    Query params:
    - category_id: Filter by budget category ID
    - mood_tag: Filter by mood tag ('necessity', 'impulse', etc.)
    - start_date: Start date for range filter (YYYY-MM-DD)
    - end_date: End date for range filter (YYYY-MM-DD)

    Returns:
        HTML page with transaction list and filter panel
    """
    # Parse date filters
    parsed_start_date = None
    parsed_end_date = None
    if start_date:
        try:
            parsed_start_date = date.fromisoformat(start_date)
        except ValueError:
            pass
    if end_date:
        try:
            parsed_end_date = date.fromisoformat(end_date)
        except ValueError:
            pass

    # List transactions with filters
    transactions = list_transactions(
        ctx,
        category_id=category_id,
        mood_tag=mood_tag,
        start_date=parsed_start_date,
        end_date=parsed_end_date,
    )

    # Build response models with category names
    categories_map = {
        cat.id: cat
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }

    transaction_responses = []
    for txn in transactions:
        cat_name = categories_map.get(txn.category_id, "Unknown").name if txn.category_id else "Uncategorized"
        transaction_responses.append(
            TransactionResponse(
                id=txn.id,
                date=txn.date,
                amount_cents=txn.amount_cents,
                direction=txn.direction,
                category_id=txn.category_id,
                category_name=cat_name,
                payee=txn.payee,
                memo=txn.memo,
                mood_tag=txn.mood_tag,
                link_type=txn.link_type,
                link_id=txn.link_id,
                is_deleted=txn.is_deleted,
                created_at=txn.created_at.isoformat() if txn.created_at else "",
                updated_at=txn.updated_at.isoformat() if txn.updated_at else "",
            )
        )

    # Get all categories for dropdown (create list of dicts to avoid lazy-load issues)
    all_categories_list = []
    for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all():
        all_categories_list.append({
            "id": cat.id,
            "name": cat.name,
            "emoji": cat.emoji,
        })

    return templates.TemplateResponse(
        "transactions.html",
        {
            "request": request,
            "transactions": transaction_responses,
            "categories": all_categories_list,
            "selected_category_id": category_id,
            "selected_mood_tag": mood_tag,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@router.post("/transactions")
def create_transaction(
    request: TransactionRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> TransactionResponse:
    """
    Create a new transaction.

    Request body:
    {
        "date": "2026-06-15",
        "amount_cents": 10000,
        "direction": "out",
        "category_id": 5,
        "payee": "Grocery Store",
        "memo": "Weekly shopping",
        "mood_tag": "necessity"
    }

    Returns:
        Created TransactionResponse
    """
    # Get or create the period for the given date
    period = get_or_create_period(ctx.db, ctx.account_id, request.date)

    # Create transaction via service
    txn = create_transaction_in_period(
        ctx,
        period_id=period.id,
        txn_date=request.date,
        amount_cents=request.amount_cents,
        direction=request.direction,
        category_id=request.category_id,
        payee=request.payee,
        memo=request.memo,
        mood_tag=request.mood_tag,
    )

    # Build response with category name
    cat = ctx.db.query(BudgetCategory).filter_by(id=txn.category_id).first() if txn.category_id else None
    cat_name = cat.name if cat else "Uncategorized"

    return TransactionResponse(
        id=txn.id,
        date=txn.date,
        amount_cents=txn.amount_cents,
        direction=txn.direction,
        category_id=txn.category_id,
        category_name=cat_name,
        payee=txn.payee,
        memo=txn.memo,
        mood_tag=txn.mood_tag,
        link_type=txn.link_type,
        link_id=txn.link_id,
        is_deleted=txn.is_deleted,
        created_at=txn.created_at.isoformat() if txn.created_at else "",
        updated_at=txn.updated_at.isoformat() if txn.updated_at else "",
    )


@router.get("/api/transactions/recent")
def get_recent_transactions(
    ctx: AccountContext = Depends(get_account_context),
):
    """
    Get recent transactions (JSON).

    Returns all transactions for the current account, sorted by date DESC (most recent first).

    Returns:
        List[TransactionResponse]
    """
    txns = list_transactions(ctx, sort_order='desc')

    # Build response models with category names
    categories_map = {
        cat.id: cat
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }

    transaction_responses = []
    for txn in txns:
        cat_name = categories_map.get(txn.category_id, "Unknown").name if txn.category_id else "Uncategorized"
        transaction_responses.append(
            TransactionResponse(
                id=txn.id,
                date=txn.date,
                amount_cents=txn.amount_cents,
                direction=txn.direction,
                category_id=txn.category_id,
                category_name=cat_name,
                payee=txn.payee,
                memo=txn.memo,
                mood_tag=txn.mood_tag,
                link_type=txn.link_type,
                link_id=txn.link_id,
                is_deleted=txn.is_deleted,
                created_at=txn.created_at.isoformat() if txn.created_at else "",
                updated_at=txn.updated_at.isoformat() if txn.updated_at else "",
            )
        )

    return transaction_responses


@router.get("/api/transactions/{transaction_id}")
def get_transaction(
    transaction_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> TransactionResponse:
    """
    Get a single transaction by ID (JSON).

    Returns:
        TransactionResponse or 404 if not found
    """
    txn = get_transaction_by_id(ctx, transaction_id)

    # Build response with category name
    cat = ctx.db.query(BudgetCategory).filter_by(id=txn.category_id).first() if txn.category_id else None
    cat_name = cat.name if cat else "Uncategorized"

    return TransactionResponse(
        id=txn.id,
        date=txn.date,
        amount_cents=txn.amount_cents,
        direction=txn.direction,
        category_id=txn.category_id,
        category_name=cat_name,
        payee=txn.payee,
        memo=txn.memo,
        mood_tag=txn.mood_tag,
        link_type=txn.link_type,
        link_id=txn.link_id,
        is_deleted=txn.is_deleted,
        created_at=txn.created_at.isoformat() if txn.created_at else "",
        updated_at=txn.updated_at.isoformat() if txn.updated_at else "",
    )


@router.post("/transactions/{transaction_id}")
def update_transaction_endpoint(
    transaction_id: int,
    request: TransactionUpdateRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> TransactionResponse:
    """
    Update a transaction (partial update allowed).

    Request body can include any of:
    {
        "date": "2026-06-16",
        "amount_cents": 15000,
        "category_id": 6,
        "payee": "New Store",
        "memo": "Updated memo",
        "mood_tag": "impulse"
    }

    Returns:
        Updated TransactionResponse or 404 if not found
    """
    txn = update_transaction(
        ctx,
        transaction_id=transaction_id,
        date=request.date,
        amount_cents=request.amount_cents,
        category_id=request.category_id,
        payee=request.payee,
        memo=request.memo,
        mood_tag=request.mood_tag,
    )

    # Build response with category name
    cat = ctx.db.query(BudgetCategory).filter_by(id=txn.category_id).first() if txn.category_id else None
    cat_name = cat.name if cat else "Uncategorized"

    return TransactionResponse(
        id=txn.id,
        date=txn.date,
        amount_cents=txn.amount_cents,
        direction=txn.direction,
        category_id=txn.category_id,
        category_name=cat_name,
        payee=txn.payee,
        memo=txn.memo,
        mood_tag=txn.mood_tag,
        link_type=txn.link_type,
        link_id=txn.link_id,
        is_deleted=txn.is_deleted,
        created_at=txn.created_at.isoformat() if txn.created_at else "",
        updated_at=txn.updated_at.isoformat() if txn.updated_at else "",
    )


@router.post("/transactions/{transaction_id}/delete")
def delete_transaction(
    transaction_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Soft delete a transaction.

    Sets is_deleted = TRUE; transaction can be restored within the undo window (~10s).

    Returns:
        {"status": "deleted", "transaction_id": id}
    """
    txn = soft_delete_transaction(ctx, transaction_id)

    return {
        "status": "deleted",
        "transaction_id": txn.id,
    }


@router.post("/transactions/{transaction_id}/restore")
def restore_transaction_endpoint(
    transaction_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Restore a soft-deleted transaction.

    Sets is_deleted = FALSE; must be called within the undo window (~10s of deletion).

    Returns:
        {"status": "restored", "transaction_id": id}
    """
    txn = restore_transaction(ctx, transaction_id)

    return {
        "status": "restored",
        "transaction_id": txn.id,
    }
