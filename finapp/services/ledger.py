"""
Ledger service: the single write path for all transactions.
All transaction modifications (create, edit, void, restore) go through here.
This ensures consistent handling of period assignment, dedup hashing, and soft deletes.
After every write, reconciliation is run to update cached balances.
"""
import hashlib
from datetime import date, datetime
from sqlalchemy.orm import Session
from finapp.models import Transaction, BudgetPeriod, DebtAccount
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
    Handles debt payment split into principal/interest.
    """
    # Get or create the period for this transaction's date
    period = get_or_create_period(ctx.db, ctx.account_id, date)

    # Compute import hash if this is an imported transaction
    import_hash = None
    if is_imported:
        import_hash = compute_import_hash(date, amount_cents, payee)

    # Handle debt payment: split into principal and interest
    if link_type == "debt" and link_id is not None:
        if principal_cents is None or interest_cents is None:
            # Fetch the debt account to get balance and interest rate
            debt = ctx.db.query(DebtAccount).filter_by(
                id=link_id, account_id=ctx.account_id
            ).first()
            if debt:
                from finapp.services.debt_payoff import amortize_month
                principal_cents, interest_cents = amortize_month(
                    opening_cents=debt.cached_balance_cents,
                    interest_rate_bps=debt.interest_rate_bps,
                    payment_cents=amount_cents
                )
                # Verify invariant: principal + interest = amount
                assert principal_cents + interest_cents == amount_cents, \
                    f"Amortization invariant failed: {principal_cents} + {interest_cents} != {amount_cents}"

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


def create_debt_adjustment(
    ctx: AccountContext,
    debt_id: int,
    adjustment_cents: int,
    statement_balance_cents: int
) -> Transaction:
    """
    Create a debt adjustment transaction when actual balance differs from cached.

    Used when reconciling a debt account's cached balance with the statement balance.
    Creates a special transaction with:
    - link_type='debt', link_id=debt_id
    - principal_cents=-adjustment_cents (negated to make the balance math work)
    - interest_cents=0 (adjustments have no interest component)
    - mood_tag=None (adjustments don't get mood tags)
    - kind='adjustment'

    The principal_cents is NEGATED because:
    - If adjustment_cents = -2000 (balance was overstated by $20)
    - We need total_principal to increase by 2000 to account for the error
    - So principal_cents = -(-2000) = 2000

    Args:
        ctx: AccountContext for account scoping
        debt_id: ID of the DebtAccount being adjusted
        adjustment_cents: negative if balance was overstated, positive if understated
        statement_balance_cents: the correct balance per statement

    Returns:
        Transaction created with link_type='debt', principal_cents=-adjustment_cents, interest_cents=0
    """
    debt = ctx.db.query(DebtAccount).filter_by(
        id=debt_id, account_id=ctx.account_id
    ).first()
    if not debt:
        raise ValueError(f"Debt account {debt_id} not found for account {ctx.account_id}")

    # Get or create the period for today's date
    today = datetime.now().date()
    period = get_or_create_period(ctx.db, ctx.account_id, today)

    # Direction is "in" if adjustment positive (balance was understated, reducing the debt)
    # Direction is "out" if adjustment negative (balance was overstated, increasing the debt)
    direction = "in" if adjustment_cents > 0 else "out"

    # CRITICAL: principal_cents must be NEGATED
    # The reconciliation formula is: derived_balance = opening_balance - total_principal
    # If adjustment_cents = -2000 (we were $20 over), we need total_principal to go UP by 2000
    # So principal_cents = -(-2000) = 2000
    principal_for_ledger = -adjustment_cents

    txn = Transaction(
        account_id=ctx.account_id,
        period_id=period.id,
        date=today,
        amount_cents=abs(adjustment_cents),
        direction=direction,
        category_id=None,
        payee="",
        memo=f"Update balance to match your statement: ${statement_balance_cents/100:.2f}",
        mood_tag=None,
        link_type="debt",
        link_id=debt_id,
        principal_cents=principal_for_ledger,
        interest_cents=0,
        is_imported=False,
        import_hash=None,
        is_deleted=False
    )
    ctx.db.add(txn)
    ctx.db.commit()

    # Refresh cached balances after write
    _refresh_balances_after_write(ctx.db, ctx.account_id)

    return txn
