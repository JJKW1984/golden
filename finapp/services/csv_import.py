"""
CSV Import service: parse and preview CSV uploads before committing to the ledger.

Handles column mapping, date/amount parsing, direction inference, and duplicate detection.
Uses the ledger's compute_import_hash for dedup so preview matches stored state.
"""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from finapp.deps import AccountContext
from finapp.money import to_cents
from finapp.models import Transaction
from finapp.services.ledger import compute_import_hash


@dataclass
class PreviewRow:
    """A row in the CSV import preview, with dedup and selection state."""
    row_index: int      # 0-based position in the input rows list
    date: date
    amount_cents: int   # magnitude, ALWAYS >= 0
    direction: str      # 'in' or 'out'
    payee: str
    import_hash: str
    is_duplicate: bool
    selected: bool      # default True; set False when is_duplicate is True


def parse_csv(content: str) -> list[dict]:
    """
    Parse CSV text into a list of dict rows keyed by header name.
    Uses stdlib csv.DictReader over io.StringIO(content).
    Skips completely blank rows (rows where all values are empty/None).
    """
    rows = []
    reader = csv.DictReader(io.StringIO(content))

    for row in reader:
        # Skip completely blank rows (all values are None or empty string)
        if row and not all(v is None or str(v).strip() == "" for v in row.values()):
            rows.append(row)

    return rows


def build_preview(
    ctx: AccountContext,
    rows: list[dict],
    column_map: dict,
    spent_is_negative: bool = True
) -> list[PreviewRow]:
    """
    Build a preview of CSV rows with dedup detection.

    column_map maps logical field -> CSV header name, e.g.
      {'date': 'Date', 'amount': 'Amount', 'payee': 'Description'}

    For each row (in order):
      - date: read row[column_map['date']]; parse trying these formats in order:
        '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%d/%m/%Y'. Return a datetime.date.
      - raw amount string: read row[column_map['amount']]; strip '$', ',', and spaces.
        Convert to Decimal. amount_cents = to_cents(abs(decimal_value)).
      - direction:
          * If 'direction' is in column_map: read that column's value, lowercase/strip it;
            if it is one of {'debit','withdrawal','payment','out','expense','w'} -> 'out',
            else -> 'in'. (amount is taken as magnitude regardless of sign.)
          * Else use the sign of the parsed amount with the spent_is_negative toggle:
            if spent_is_negative is True:  negative amount -> 'out', zero/positive -> 'in'.
            if spent_is_negative is False: positive amount -> 'out', negative -> 'in'.
      - payee: read row[column_map['payee']] (or '' if missing).
      - import_hash = compute_import_hash(parsed_date, amount_cents, payee)
      - is_duplicate: True if this import_hash already exists on any Transaction for
        ctx.account_id in the DB (query Transaction.import_hash), OR if this same hash
        already appeared on an EARLIER row in this same batch.
      - selected = not is_duplicate

    Return list[PreviewRow] in input order.
    """
    preview_rows = []
    seen_hashes = set()  # Track hashes in this batch to detect intra-batch dupes

    for row_index, row in enumerate(rows):
        # Parse date
        date_str = row.get(column_map.get("date", "")).strip()
        parsed_date = _parse_date(date_str)

        # Parse amount
        amount_str = row.get(column_map.get("amount", "")).strip()
        amount_decimal = _parse_amount_to_decimal(amount_str)
        amount_cents = to_cents(abs(amount_decimal))

        # Determine direction
        if "direction" in column_map:
            direction_str = row.get(column_map["direction"], "").strip().lower()
            _out_words = {"debit", "withdrawal", "payment", "out", "expense", "w"}
            direction = "out" if direction_str in _out_words else "in"
        else:
            # Use sign of amount with toggle
            if spent_is_negative:
                direction = "out" if amount_decimal < 0 else "in"
            else:
                direction = "out" if amount_decimal > 0 else "in"

        # Parse payee
        payee = row.get(column_map.get("payee", ""), "").strip()

        # Compute hash
        import_hash = compute_import_hash(parsed_date, amount_cents, payee)

        # Check for duplicates (DB or intra-batch)
        is_duplicate_db = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id,
            import_hash=import_hash
        ).first() is not None

        is_duplicate_batch = import_hash in seen_hashes
        is_duplicate = is_duplicate_db or is_duplicate_batch

        # Track this hash
        seen_hashes.add(import_hash)

        # selected = not is_duplicate
        selected = not is_duplicate

        preview_row = PreviewRow(
            row_index=row_index,
            date=parsed_date,
            amount_cents=amount_cents,
            direction=direction,
            payee=payee,
            import_hash=import_hash,
            is_duplicate=is_duplicate,
            selected=selected,
        )
        preview_rows.append(preview_row)

    return preview_rows


def _parse_date(date_str: str) -> date:
    """
    Parse date string using multiple format attempts.
    Tries in order: '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%d/%m/%Y'.
    """
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"Could not parse date: {date_str}")


def _parse_amount_to_decimal(amount_str: str) -> Decimal:
    """
    Parse an amount string, stripping '$', ',', and spaces.
    Returns a signed Decimal.
    """
    # Remove currency symbols, commas, and spaces
    cleaned = amount_str.replace("$", "").replace(",", "").replace(" ", "")
    return Decimal(cleaned)


def list_needs_category(ctx: AccountContext):
    """
    Return non-deleted transactions for ctx.account_id with category_id IS NULL,
    ordered by date desc. (The 'Needs Category' inbox.)
    """
    return ctx.db.query(Transaction).filter_by(
        account_id=ctx.account_id,
        is_deleted=False,
        category_id=None
    ).order_by(Transaction.date.desc()).all()


def needs_category_count(ctx: AccountContext) -> int:
    """
    Count of non-deleted, uncategorized (category_id IS NULL) transactions for ctx.account_id.
    """
    return ctx.db.query(Transaction).filter_by(
        account_id=ctx.account_id,
        is_deleted=False,
        category_id=None
    ).count()
