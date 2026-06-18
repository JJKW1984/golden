"""
CSV Import service: parse and preview CSV uploads before committing to the ledger.

Handles column mapping, date/amount parsing, direction inference, and duplicate detection.
Uses the ledger's compute_import_hash for dedup so preview matches stored state.
"""
import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from finapp.deps import AccountContext
from finapp.money import to_cents
from finapp.models import Transaction, ImportDraft
from finapp.services.ledger import compute_import_hash


_OUT_WORDS = {"debit", "withdrawal", "payment", "out", "expense", "w"}


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
        has_direction_col = "direction" in column_map
        direction_raw = row.get(column_map.get("direction", ""), "") if has_direction_col else ""
        direction = _resolve_direction(
            amount_decimal, direction_raw, has_direction_col, spent_is_negative
        )

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


def _safe_parse_date(date_str: str):
    """Like _parse_date but returns None instead of raising on bad input."""
    if not date_str or not date_str.strip():
        return None
    try:
        return _parse_date(date_str.strip())
    except ValueError:
        return None


def _safe_parse_amount(amount_str: str):
    """Like _parse_amount_to_decimal but returns None instead of raising."""
    if amount_str is None or str(amount_str).strip() == "":
        return None
    try:
        return _parse_amount_to_decimal(str(amount_str).strip())
    except (InvalidOperation, ValueError):
        return None


def _resolve_direction(amount_decimal, direction_raw, has_direction_col, spent_is_negative):
    """Resolve 'in'/'out'. With a direction column, classify by out-words;
    otherwise use the amount's sign with the spent_is_negative toggle."""
    if has_direction_col:
        return "out" if (direction_raw or "").strip().lower() in _OUT_WORDS else "in"
    value = amount_decimal if amount_decimal is not None else Decimal(0)
    if spent_is_negative:
        return "out" if value < 0 else "in"
    return "out" if value > 0 else "in"


def normalize_rows(rows: list[dict], column_map: dict, spent_is_negative: bool = True) -> list[dict]:
    """Normalize raw CSV rows into the shared normalized-row schema.

    Records per-row parse problems in 'issues' instead of aborting the batch.
    Does NOT touch the database; duplicate flags are applied later by
    mark_duplicates(). amount_cents is always a non-negative magnitude.
    """
    has_direction_col = "direction" in column_map
    normalized = []

    for row_id, row in enumerate(rows):
        issues = []

        raw_date = row.get(column_map.get("date", ""), "") or ""
        parsed_date = _safe_parse_date(raw_date)
        if parsed_date is None:
            issues.append("date")

        raw_amount = row.get(column_map.get("amount", ""), "") or ""
        amount_decimal = _safe_parse_amount(raw_amount)
        if amount_decimal is None:
            issues.append("amount")
            amount_cents = 0
        else:
            amount_cents = to_cents(abs(amount_decimal))

        direction_raw = row.get(column_map.get("direction", ""), "") if has_direction_col else ""
        direction = _resolve_direction(
            amount_decimal, direction_raw, has_direction_col, spent_is_negative
        )

        payee = (row.get(column_map.get("payee", ""), "") or "").strip()

        valid = not issues
        import_hash = (
            compute_import_hash(parsed_date, amount_cents, payee) if valid else None
        )

        normalized.append({
            "row_id": row_id,
            "date": parsed_date.isoformat() if parsed_date else raw_date.strip(),
            "amount_cents": amount_cents,
            "direction": direction,
            "payee": payee,
            "import_hash": import_hash,
            "is_duplicate": False,
            "selected": valid,
            "issues": issues,
            "valid": valid,
        })

    return normalized


def mark_duplicates(ctx: AccountContext, normalized: list[dict]) -> list[dict]:
    """Set is_duplicate (DB hash match OR earlier-in-batch match) on valid rows
    and clear their selection. Invalid rows are never duplicates."""
    seen_hashes = set()
    for r in normalized:
        if not r["valid"]:
            r["is_duplicate"] = False
            continue
        h = r["import_hash"]
        db_dup = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id, import_hash=h
        ).first() is not None
        is_dup = db_dup or h in seen_hashes
        seen_hashes.add(h)
        r["is_duplicate"] = is_dup
        if is_dup:
            r["selected"] = False
    return normalized


def build_normalized_preview(
    ctx: AccountContext, rows: list[dict], column_map: dict, spent_is_negative: bool = True
) -> list[dict]:
    """Normalize raw rows then apply duplicate detection. Returns the normalized
    rows ready to persist in a draft and return to the browser."""
    normalized = normalize_rows(rows, column_map, spent_is_negative)
    return mark_duplicates(ctx, normalized)


def summarize(rows: list[dict]) -> dict:
    """Summary counts for a normalized preview. 'valid' = importable
    (parsed OK and not a duplicate)."""
    total = len(rows)
    invalid = sum(1 for r in rows if not r["valid"])
    duplicate = sum(1 for r in rows if r["is_duplicate"])
    importable = sum(1 for r in rows if r["valid"] and not r["is_duplicate"])
    return {"total": total, "valid": importable, "invalid": invalid, "duplicate": duplicate}


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


DRAFT_TTL_SECONDS = 3600


class DraftNotFoundError(Exception):
    """Raised when a draft id does not exist for this account."""


class DraftExpiredError(Exception):
    """Raised when a draft exists but has passed its expires_at."""


def _utcnow_naive() -> datetime:
    """Naive UTC timestamp. SQLite stores DateTime without tzinfo, so we keep
    draft timestamps naive to compare them safely."""
    return datetime.utcnow()


def create_draft(ctx: AccountContext, column_map: dict, spent_is_negative: bool,
                 rows: list[dict]) -> ImportDraft:
    """Persist a normalized preview as a draft scoped to ctx.account_id."""
    now = _utcnow_naive()
    draft = ImportDraft(
        account_id=ctx.account_id,
        created_at=now,
        expires_at=now + timedelta(seconds=DRAFT_TTL_SECONDS),
        column_map_json=json.dumps(column_map),
        spent_is_negative=spent_is_negative,
        rows_json=json.dumps(rows),
        version=1,
    )
    ctx.db.add(draft)
    ctx.db.commit()
    return draft


def load_draft(ctx: AccountContext, draft_id: int) -> ImportDraft:
    """Load a draft by account + id. Raises DraftNotFoundError or
    DraftExpiredError."""
    draft = ctx.db.query(ImportDraft).filter_by(
        id=draft_id, account_id=ctx.account_id
    ).first()
    if draft is None:
        raise DraftNotFoundError(draft_id)
    if _utcnow_naive() > draft.expires_at:
        raise DraftExpiredError(draft_id)
    return draft
