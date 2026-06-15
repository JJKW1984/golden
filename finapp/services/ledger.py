"""
Ledger service: the single write path for all transactions.
All transaction modifications (create, edit, void, restore) go through here.
This ensures consistent handling of period assignment, dedup hashing, and soft deletes.
After every write, reconciliation is run to update cached balances.
"""
import hashlib
from datetime import date
from sqlalchemy.orm import Session
from finapp.models import Transaction, BudgetPeriod
from finapp.deps import AccountContext


def _refresh_balances_after_write(db: Session, account_id: int) -> None:
    """
    Internal: after a transaction write, update all cached balances.
    This ensures caches stay fresh and the reconciliation gate shows zero drift.
    """
    from finapp.services.reconciliation import apply_recomputed_balances
    apply_recomputed_balances(db, account_id)


def compute_import_hash(txn_date: date, amount_cents: int, payee: str) -> str:
    """
    Compute deterministic dedup hash from date, amount, and payee.
    Used to detect duplicate imports. Case-insensitive on payee.
    """
    payee_norm = (payee or "").lower().strip()
    key = f"{txn_date.isoformat()}|{amount_cents}|{payee_norm}"
    return hashlib.sha256(key.encode()).hexdigest()


def get_or_create_period(db: Session, account_id: int, txn_date: date) -> BudgetPeriod:
    """
    Get or create the BudgetPeriod matching the transaction date.
    Resolves the "which month does a transaction belong to?" question.
    """
    year = txn_date.year
    month = txn_date.month

    period = db.query(BudgetPeriod).filter_by(
        account_id=account_id, year=year, month=month
    ).first()

    if period:
        return period

    # Create new period if it doesn't exist
    period = BudgetPeriod(
        account_id=account_id,
        year=year,
        month=month,
        income_received_cents=0,
        status="active",
    )
    db.add(period)
    db.commit()
    return period


def create_transaction(
    ctx: AccountContext,
    date: date,
    amount_cents: int,
    direction: str,
    category_id: int = None,
    payee: str = None,
    memo: str = None,
    mood_tag: str = None,
    link_type: str = None,
    link_id: int = None,
    principal_cents: int = None,
    interest_cents: int = None,
    is_imported: bool = False,
) -> Transaction:
    """
    Create a new transaction through the single write path.
    Automatically assigns period from date.
    Computes import hash if imported.
    """
    # Get or create the period for this transaction's date
    period = get_or_create_period(ctx.db, ctx.account_id, date)

    # Compute import hash if this is an imported transaction
    import_hash = None
    if is_imported:
        import_hash = compute_import_hash(date, amount_cents, payee)

    txn = Transaction(
        account_id=ctx.account_id,
        period_id=period.id,
        date=date,
        amount_cents=amount_cents,
        direction=direction,
        category_id=category_id,
        payee=payee,
        memo=memo,
        mood_tag=mood_tag,
        link_type=link_type,
        link_id=link_id,
        principal_cents=principal_cents,
        interest_cents=interest_cents,
        is_imported=is_imported,
        import_hash=import_hash,
        is_deleted=False,
    )

    ctx.db.add(txn)
    ctx.db.commit()

    # Refresh cached balances after write
    _refresh_balances_after_write(ctx.db, ctx.account_id)

    return txn


def edit_transaction(
    ctx: AccountContext,
    txn_id: int,
    date: date = None,
    amount_cents: int = None,
    direction: str = None,
    category_id: int = None,
    payee: str = None,
    memo: str = None,
    mood_tag: str = None,
    link_type: str = None,
    link_id: int = None,
    principal_cents: int = None,
    interest_cents: int = None,
) -> Transaction:
    """
    Edit an existing transaction.
    If date is changed, period is re-derived automatically.
    Only non-None arguments are updated.
    """
    txn = ctx.db.query(Transaction).filter_by(
        id=txn_id, account_id=ctx.account_id
    ).first()

    if not txn:
        raise ValueError(f"Transaction {txn_id} not found")

    # Update fields
    if date is not None and date != txn.date:
        # Date changed: re-derive period
        period = get_or_create_period(ctx.db, ctx.account_id, date)
        txn.period_id = period.id
        txn.date = date
    elif date is not None:
        txn.date = date

    if amount_cents is not None:
        txn.amount_cents = amount_cents

    if direction is not None:
        txn.direction = direction

    if category_id is not None:
        txn.category_id = category_id

    if payee is not None:
        txn.payee = payee

    if memo is not None:
        txn.memo = memo

    if mood_tag is not None:
        txn.mood_tag = mood_tag

    if link_type is not None:
        txn.link_type = link_type

    if link_id is not None:
        txn.link_id = link_id

    if principal_cents is not None:
        txn.principal_cents = principal_cents

    if interest_cents is not None:
        txn.interest_cents = interest_cents

    ctx.db.commit()

    # Refresh cached balances after write
    _refresh_balances_after_write(ctx.db, ctx.account_id)

    return txn


def void_transaction(ctx: AccountContext, txn_id: int) -> Transaction:
    """
    Soft-delete a transaction (mark is_deleted = True).
    Does not remove the row; allows restoration.
    """
    txn = ctx.db.query(Transaction).filter_by(
        id=txn_id, account_id=ctx.account_id
    ).first()

    if not txn:
        raise ValueError(f"Transaction {txn_id} not found")

    txn.is_deleted = True
    ctx.db.commit()

    # Refresh cached balances after write
    _refresh_balances_after_write(ctx.db, ctx.account_id)

    return txn


def restore_transaction(ctx: AccountContext, txn_id: int) -> Transaction:
    """
    Restore a soft-deleted transaction (mark is_deleted = False).
    """
    txn = ctx.db.query(Transaction).filter_by(
        id=txn_id, account_id=ctx.account_id
    ).first()

    if not txn:
        raise ValueError(f"Transaction {txn_id} not found")

    txn.is_deleted = False
    ctx.db.commit()

    # Refresh cached balances after write
    _refresh_balances_after_write(ctx.db, ctx.account_id)

    return txn
