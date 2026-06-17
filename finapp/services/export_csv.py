"""
Export service: CSV export of transactions for backup and external analysis.

export_transactions_csv(ctx) returns CSV text of all non-deleted transactions.
The export format is compatible with import via csv_import.parse_csv + build_preview.
"""
import csv
import io
from finapp.deps import AccountContext
from finapp.models import Transaction, BudgetCategory


def export_transactions_csv(ctx: AccountContext) -> str:
    """
    Return CSV text of all non-deleted (is_deleted==False) transactions for
    ctx.account_id.

    Header row (lowercase): date,amount,direction,payee,category,memo,mood_tag

    For each transaction:
      - date: ISO format (YYYY-MM-DD)
      - amount: SIGNED dollar string. direction 'out' -> negative (e.g. '-45.00'),
        direction 'in' -> positive (e.g. '120000' cents -> '1200.00'). Format with
        exactly 2 decimals. (amount_cents is a magnitude; divide by 100.)
      - direction: 'in' or 'out'
      - payee: payee or ''
      - category: the BudgetCategory.name or '' if uncategorized
      - memo: memo or ''
      - mood_tag: mood_tag or ''

    This must round-trip through finapp.services.csv_import.parse_csv +
    build_preview(spent_is_negative=True) — see tests/test_export_csv.py.
    """
    # Query all non-deleted transactions for this account, ordered by date
    transactions = (
        ctx.db.query(Transaction)
        .filter_by(account_id=ctx.account_id, is_deleted=False)
        .order_by(Transaction.date)
        .all()
    )

    # Use StringIO and csv.writer for CSV generation
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(["date", "amount", "direction", "payee", "category", "memo", "mood_tag"])

    # Write each transaction
    for txn in transactions:
        # Get category name or empty string
        category_name = ""
        if txn.category_id:
            category = (
                ctx.db.query(BudgetCategory)
                .filter_by(id=txn.category_id, account_id=ctx.account_id)
                .first()
            )
            if category:
                category_name = category.name

        # Compute signed dollar string with integer arithmetic (no float — money
        # discipline). amount_cents is a magnitude; sign is carried by direction.
        whole, frac = divmod(txn.amount_cents, 100)
        sign = "-" if txn.direction == "out" else ""
        amount_str = f"{sign}{whole}.{frac:02d}"

        # Payee and memo default to empty string
        payee = txn.payee or ""
        memo = txn.memo or ""
        mood_tag = txn.mood_tag or ""

        writer.writerow(
            [
                txn.date.isoformat(),
                amount_str,
                txn.direction,
                payee,
                category_name,
                memo,
                mood_tag,
            ]
        )

    return output.getvalue()
