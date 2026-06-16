"""
Transaction service layer (Phase 5).
Provides CRUD operations for transactions with filtering, sorting, and soft-delete awareness.
All functions take ctx: AccountContext first and scope queries by ctx.account_id.
"""
from datetime import date
from fastapi import HTTPException
from sqlalchemy.orm import Query
from finapp.deps import AccountContext
from finapp.models import Transaction, BudgetPeriod
from finapp.services.ledger import create_transaction, get_or_create_period


def create_transaction_in_period(
    ctx: AccountContext,
    period_id: int,
    txn_date: date,
    amount_cents: int,
    direction: str,
    category_id: int = None,
    payee: str = None,
    memo: str = None,
    mood_tag: str = None,
    link_type: str = None,
    link_id: int = None,
) -> Transaction:
    """
    Create a new transaction in the specified period.
    Verifies the period exists before creating the transaction.

    Args:
        ctx: AccountContext with account_id and db session
        period_id: Budget period ID to assign to the transaction
        txn_date: Transaction date
        amount_cents: Amount in integer cents (never float)
        direction: 'in' or 'out'
        category_id: Budget category ID
        payee: Optional payee name
        memo: Optional memo/description
        mood_tag: Optional mood tag ('necessity', 'impulse', etc.)
        link_type: Optional link type ('debt' or 'savings')
        link_id: Optional link ID (debt account or savings goal)

    Returns:
        Created Transaction model

    Raises:
        HTTPException 404 if period not found or belongs to different account
    """
    # Verify period exists and belongs to this account
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id,
        account_id=ctx.account_id
    ).first()

    if not period:
        raise HTTPException(status_code=404, detail="Budget period not found")

    # Create transaction through the single write path
    txn = create_transaction(
        ctx,
        date=txn_date,
        amount_cents=amount_cents,
        direction=direction,
        category_id=category_id,
        payee=payee,
        memo=memo,
        mood_tag=mood_tag,
        link_type=link_type,
        link_id=link_id,
    )

    return txn


def update_transaction(
    ctx: AccountContext,
    transaction_id: int,
    date: date = None,
    amount_cents: int = None,
    category_id: int = None,
    payee: str = None,
    memo: str = None,
    mood_tag: str = None,
) -> Transaction:
    """
    Update transaction fields.
    If date changes, period_id is automatically re-derived.

    Args:
        ctx: AccountContext with account_id and db session
        transaction_id: ID of transaction to update
        date: Optional new transaction date
        amount_cents: Optional new amount in integer cents
        category_id: Optional new category ID
        payee: Optional new payee
        memo: Optional new memo
        mood_tag: Optional new mood tag

    Returns:
        Updated Transaction model

    Raises:
        HTTPException 404 if transaction not found or belongs to different account
    """
    # Fetch transaction, scoped by account
    txn = ctx.db.query(Transaction).filter_by(
        id=transaction_id,
        account_id=ctx.account_id
    ).first()

    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Update date and re-derive period if needed
    if date is not None and date != txn.date:
        period = get_or_create_period(ctx.db, ctx.account_id, date)
        txn.period_id = period.id
        txn.date = date
    elif date is not None:
        txn.date = date

    # Update other fields
    if amount_cents is not None:
        txn.amount_cents = amount_cents

    if category_id is not None:
        txn.category_id = category_id

    if payee is not None:
        txn.payee = payee

    if memo is not None:
        txn.memo = memo

    if mood_tag is not None:
        txn.mood_tag = mood_tag

    ctx.db.commit()

    # Refresh cached balances after write
    from finapp.services.ledger import _refresh_balances_after_write
    _refresh_balances_after_write(ctx.db, ctx.account_id)

    return txn


def soft_delete_transaction(ctx: AccountContext, transaction_id: int) -> Transaction:
    """
    Soft-delete a transaction (set is_deleted = TRUE).
    Transaction row is not physically deleted; can be restored.

    Args:
        ctx: AccountContext with account_id and db session
        transaction_id: ID of transaction to delete

    Returns:
        Soft-deleted Transaction model

    Raises:
        HTTPException 404 if transaction not found or belongs to different account
    """
    txn = ctx.db.query(Transaction).filter_by(
        id=transaction_id,
        account_id=ctx.account_id
    ).first()

    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    txn.is_deleted = True
    ctx.db.commit()

    # Refresh cached balances after write
    from finapp.services.ledger import _refresh_balances_after_write
    _refresh_balances_after_write(ctx.db, ctx.account_id)

    return txn


def restore_transaction(ctx: AccountContext, transaction_id: int) -> Transaction:
    """
    Restore a soft-deleted transaction (set is_deleted = FALSE).

    Args:
        ctx: AccountContext with account_id and db session
        transaction_id: ID of transaction to restore

    Returns:
        Restored Transaction model

    Raises:
        HTTPException 404 if transaction not found or belongs to different account
    """
    txn = ctx.db.query(Transaction).filter_by(
        id=transaction_id,
        account_id=ctx.account_id
    ).first()

    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    txn.is_deleted = False
    ctx.db.commit()

    # Refresh cached balances after write
    from finapp.services.ledger import _refresh_balances_after_write
    _refresh_balances_after_write(ctx.db, ctx.account_id)

    return txn


def get_transaction_by_id(ctx: AccountContext, transaction_id: int) -> Transaction:
    """
    Fetch a single transaction by ID.
    Returns 404 if not found, is_deleted, or belongs to different account.

    Args:
        ctx: AccountContext with account_id and db session
        transaction_id: ID of transaction to fetch

    Returns:
        Transaction model

    Raises:
        HTTPException 404 if transaction not found, is_deleted, or belongs to different account
    """
    txn = ctx.db.query(Transaction).filter_by(
        id=transaction_id,
        account_id=ctx.account_id,
        is_deleted=False
    ).first()

    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return txn


def list_transactions(
    ctx: AccountContext,
    period_id: int = None,
    category_id: int = None,
    direction: str = None,
    mood_tag: str = None,
    start_date: date = None,
    end_date: date = None,
    sort_order: str = 'desc',
) -> list[Transaction]:
    """
    List transactions with optional filtering.
    Default sort: date DESC (most recent first).
    Soft-delete aware: excludes is_deleted = TRUE by default.

    Args:
        ctx: AccountContext with account_id and db session
        period_id: Optional period ID to filter by
        category_id: Optional category ID to filter by
        direction: Optional direction ('in' or 'out') to filter by
        mood_tag: Optional mood tag to filter by
        start_date: Optional start date for range filter (inclusive)
        end_date: Optional end date for range filter (inclusive)
        sort_order: 'asc' or 'desc' (default 'desc')

    Returns:
        List of Transaction models, sorted by date (desc by default)
    """
    # Base query: scoped by account, exclude deleted
    query = ctx.db.query(Transaction).filter_by(
        account_id=ctx.account_id,
        is_deleted=False
    )

    # Apply optional filters
    if period_id is not None:
        query = query.filter_by(period_id=period_id)

    if category_id is not None:
        query = query.filter_by(category_id=category_id)

    if direction is not None:
        query = query.filter_by(direction=direction)

    if mood_tag is not None:
        query = query.filter_by(mood_tag=mood_tag)

    if start_date is not None:
        query = query.filter(Transaction.date >= start_date)

    if end_date is not None:
        query = query.filter(Transaction.date <= end_date)

    # Sort by date
    if sort_order.lower() == 'asc':
        query = query.order_by(Transaction.date.asc())
    else:
        query = query.order_by(Transaction.date.desc())

    return query.all()
