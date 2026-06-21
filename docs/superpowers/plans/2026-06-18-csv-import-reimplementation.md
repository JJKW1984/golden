# CSV Import Reimplementation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Settings CSV import workflow complete end-to-end — upload, preview, inline-edit rows, select rows, and confirm — backed by a server-stored import draft.

**Architecture:** Add a server-backed draft model (`import_drafts` table) that holds normalized preview rows. The preview endpoint parses/normalizes/dedups rows, persists a draft, and returns it; the confirm endpoint reloads the draft by `account_id + draft_id`, applies user edits, re-validates, re-checks dedup against current DB state, imports the selected valid non-duplicate rows through the ledger, and returns a partial-success summary. The browser renders an editable table and posts only `draft_id + edits + selection`.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, SQLite (WAL), Jinja2 + vanilla JS, pytest.

## Global Constraints

- **Endpoint paths are frozen:** `POST /settings/import/csv` and `POST /settings/import/confirm`. No renames.
- **Money:** amounts stored/compared as `INTEGER` cents; `amount_cents` is always a non-negative magnitude; `direction` (`"in"`/`"out"`) carries sign. Conversions only at the HTTP boundary via `finapp/money.py` (`to_cents`, `to_display`).
- **One write path:** every imported `Transaction` is created via `finapp/services/ledger.py::create_transaction(..., is_imported=True)`. No other code writes a `Transaction`.
- **Dedup hash:** always `finapp/services/ledger.py::compute_import_hash(date, amount_cents, payee)` so preview matches stored state. Case-insensitive on payee.
- **Account-scoped everything:** every query filters by `ctx.account_id`; every service function takes `ctx: AccountContext` first.
- **Calm by design:** no red for normal states; duplicates/invalid rows are informational, never guilt copy.
- **Reconciliation gate:** `recompute_balances(db, account_id)` must report `total_drift == 0` after import integration tests.
- **Loopback only:** app binds `127.0.0.1:5000`.

---

## File Structure

- `finapp/models.py` — add `ImportDraft` model + `Account.import_drafts` relationship.
- `alembic/versions/c4d5e6f7a8b9_add_import_drafts.py` — new migration (down_revision `f2a19aaf069f`, the current head).
- `finapp/services/csv_import.py` — add safe parsers, `normalize_rows`, `mark_duplicates`, `build_normalized_preview`, `summarize`, draft persistence (`create_draft`, `load_draft`), confirm logic (`confirm_import`, `_apply_edit`), and `DraftNotFoundError` / `DraftExpiredError`. Keep existing `parse_csv`, `build_preview`, `list_needs_category`, `needs_category_count`.
- `finapp/routers/settings.py` — rewrite `import_csv` response shape and `confirm_import` request/response shape; add `ConfirmImportRequest` / `RowEdit` Pydantic models and exception→HTTP mapping.
- `finapp/templates/settings.html` — add JS-driven editable preview panel below the import form and the supporting `<script>`.
- `tests/test_import_draft.py` — new unit tests for normalization, draft persistence/expiry, and confirm logic.
- `tests/test_csv_import.py` — unchanged (legacy `build_preview` coverage stays green).
- `tests/integration/test_phase10_import_settings.py` — update upload/confirm tests to the new contract; add draft-metadata, expiry, and inline-edit flow tests.

Normalized-row dict schema (the single contract shared by every task — preview rows, draft `rows_json`, and confirm input):

```python
{
    "row_id": int,            # stable id within a draft; equals the 0-based input row index
    "date": str,              # "YYYY-MM-DD" when valid; original raw string when the date failed to parse
    "amount_cents": int,      # magnitude >= 0; 0 when amount failed to parse
    "direction": str,         # "in" | "out"
    "payee": str,             # may be ""
    "import_hash": str | None,# compute_import_hash(...) when valid, else None
    "is_duplicate": bool,
    "selected": bool,         # default True; False when duplicate or invalid
    "issues": list[str],      # subset of ["date", "amount", "direction"]; empty => valid
    "valid": bool,            # issues == []
}
```

---

### Task 1: `ImportDraft` model + migration

**Files:**
- Modify: `finapp/models.py` (add model + `Account.import_drafts` relationship)
- Create: `alembic/versions/c4d5e6f7a8b9_add_import_drafts.py`
- Test: `tests/test_import_draft.py`

**Interfaces:**
- Consumes: `finapp.db.Base`, `Account` (FK target).
- Produces: `ImportDraft` ORM class with columns `id, account_id, created_at, expires_at, column_map_json, spent_is_negative, rows_json, version`. Migration head becomes `c4d5e6f7a8b9`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_import_draft.py`:

```python
"""Unit tests for the server-backed CSV import draft model, normalization,
persistence, and confirm logic."""
from datetime import date, datetime, timedelta
import pytest

from finapp.deps import AccountContext
from finapp.models import ImportDraft
from finapp.services.seeds import seed_default_categories


@pytest.fixture
def import_ctx(db, account):
    seed_default_categories(db, account.id)
    return AccountContext(account_id=account.id, db=db)


def test_import_draft_table_round_trips(import_ctx):
    draft = ImportDraft(
        account_id=import_ctx.account_id,
        created_at=datetime(2026, 6, 18, 12, 0, 0),
        expires_at=datetime(2026, 6, 18, 13, 0, 0),
        column_map_json='{"date": "Date"}',
        spent_is_negative=True,
        rows_json="[]",
        version=1,
    )
    import_ctx.db.add(draft)
    import_ctx.db.commit()

    loaded = import_ctx.db.query(ImportDraft).filter_by(
        account_id=import_ctx.account_id
    ).first()
    assert loaded is not None
    assert loaded.column_map_json == '{"date": "Date"}'
    assert loaded.spent_is_negative is True
    assert loaded.rows_json == "[]"
    assert loaded.version == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_import_draft.py::test_import_draft_table_round_trips -v`
Expected: FAIL with `ImportError: cannot import name 'ImportDraft' from 'finapp.models'`

- [ ] **Step 3: Add the model**

In `finapp/models.py`, add this class after the `Settings` class (anywhere among the model definitions is fine; place it after `Settings`):

```python
class ImportDraft(Base):
    __tablename__ = "import_drafts"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    expires_at = Column(DateTime, nullable=False)
    column_map_json = Column(Text, nullable=False)
    spent_is_negative = Column(Boolean, nullable=False, default=True)
    rows_json = Column(Text, nullable=False)  # JSON list of normalized row dicts
    version = Column(Integer, nullable=False, default=1)

    account = relationship("Account", back_populates="import_drafts")
```

Then add this line inside the `Account` class relationship block (next to the other `relationship(...)` lines, e.g. after `net_worth_snapshots`):

```python
    import_drafts = relationship("ImportDraft", back_populates="account", cascade="all, delete-orphan")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_import_draft.py::test_import_draft_table_round_trips -v`
Expected: PASS

- [ ] **Step 5: Write the migration**

Create `alembic/versions/c4d5e6f7a8b9_add_import_drafts.py`:

```python
"""add import drafts

Revision ID: c4d5e6f7a8b9
Revises: f2a19aaf069f
Create Date: 2026-06-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'f2a19aaf069f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'import_drafts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('column_map_json', sa.Text(), nullable=False),
        sa.Column('spent_is_negative', sa.Boolean(), nullable=False),
        sa.Column('rows_json', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('import_drafts')
```

- [ ] **Step 6: Verify migration applies cleanly**

Run: `uv run alembic upgrade head && uv run alembic current`
Expected: output ends with revision `c4d5e6f7a8b9 (head)` and no errors.

- [ ] **Step 7: Commit**

```bash
git add finapp/models.py alembic/versions/c4d5e6f7a8b9_add_import_drafts.py tests/test_import_draft.py
git commit -m "feat: add ImportDraft model and migration"
```

---

### Task 2: Safe parsers + per-row normalization

**Files:**
- Modify: `finapp/services/csv_import.py`
- Test: `tests/test_import_draft.py`

**Interfaces:**
- Consumes: existing `_parse_date`, `_parse_amount_to_decimal`, `to_cents`, `compute_import_hash`.
- Produces:
  - `_safe_parse_date(s: str) -> date | None`
  - `_safe_parse_amount(s: str) -> Decimal | None`
  - `_resolve_direction(amount_decimal: Decimal, direction_raw: str, has_direction_col: bool, spent_is_negative: bool) -> str`
  - `normalize_rows(rows: list[dict], column_map: dict, spent_is_negative: bool = True) -> list[dict]` — returns normalized-row dicts (schema above) with `is_duplicate=False`, `selected=valid`. No DB access.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_draft.py`:

```python
from finapp.services.csv_import import normalize_rows


def test_normalize_rows_valid_row():
    rows = [{"Date": "2026-06-01", "Amount": "-45.00", "Description": "Coffee Shop"}]
    cmap = {"date": "Date", "amount": "Amount", "payee": "Description"}
    out = normalize_rows(rows, cmap, spent_is_negative=True)
    assert len(out) == 1
    r = out[0]
    assert r["row_id"] == 0
    assert r["date"] == "2026-06-01"
    assert r["amount_cents"] == 4500
    assert r["direction"] == "out"
    assert r["payee"] == "Coffee Shop"
    assert r["issues"] == []
    assert r["valid"] is True
    assert r["selected"] is True
    assert r["import_hash"] is not None


def test_normalize_rows_bad_date_is_issue_not_crash():
    rows = [{"Date": "not-a-date", "Amount": "-45.00", "Description": "X"}]
    cmap = {"date": "Date", "amount": "Amount", "payee": "Description"}
    out = normalize_rows(rows, cmap)
    r = out[0]
    assert "date" in r["issues"]
    assert r["valid"] is False
    assert r["selected"] is False
    assert r["import_hash"] is None


def test_normalize_rows_bad_amount_is_issue():
    rows = [{"Date": "2026-06-01", "Amount": "abc", "Description": "X"}]
    cmap = {"date": "Date", "amount": "Amount", "payee": "Description"}
    out = normalize_rows(rows, cmap)
    r = out[0]
    assert "amount" in r["issues"]
    assert r["valid"] is False
    assert r["amount_cents"] == 0


def test_normalize_rows_positive_toggle():
    rows = [{"Date": "2026-06-01", "Amount": "45.00", "Description": "Store"}]
    cmap = {"date": "Date", "amount": "Amount", "payee": "Description"}
    out = normalize_rows(rows, cmap, spent_is_negative=False)
    assert out[0]["direction"] == "out"
    assert out[0]["amount_cents"] == 4500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_import_draft.py -k normalize_rows -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_rows'`

- [ ] **Step 3: Add parsers, direction helper, and `normalize_rows`**

In `finapp/services/csv_import.py`, add the out-words constant near the top (after imports) and the new functions below `_parse_amount_to_decimal`:

```python
_OUT_WORDS = {"debit", "withdrawal", "payment", "out", "expense", "w"}


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
```

Add `InvalidOperation` to the decimal import at the top of the file. Change:

```python
from decimal import Decimal
```

to:

```python
from decimal import Decimal, InvalidOperation
```

- [ ] **Step 4: Refactor `build_preview` to reuse `_resolve_direction` (DRY)**

In the existing `build_preview`, replace the direction block:

```python
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
```

with:

```python
        # Determine direction
        has_direction_col = "direction" in column_map
        direction_raw = row.get(column_map.get("direction", ""), "") if has_direction_col else ""
        direction = _resolve_direction(
            amount_decimal, direction_raw, has_direction_col, spent_is_negative
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_import_draft.py -k normalize_rows tests/test_csv_import.py -v`
Expected: PASS (new normalization tests pass AND the legacy `build_preview` tests still pass)

- [ ] **Step 6: Commit**

```bash
git add finapp/services/csv_import.py tests/test_import_draft.py
git commit -m "feat: add safe parsers and per-row CSV normalization"
```

---

### Task 3: Duplicate marking + preview assembly + summary

**Files:**
- Modify: `finapp/services/csv_import.py`
- Test: `tests/test_import_draft.py`

**Interfaces:**
- Consumes: `normalize_rows`, `Transaction`, `AccountContext`.
- Produces:
  - `mark_duplicates(ctx: AccountContext, normalized: list[dict]) -> list[dict]` — mutates and returns; sets `is_duplicate` (DB hash OR earlier in batch) and clears `selected` for duplicates.
  - `build_normalized_preview(ctx, rows, column_map, spent_is_negative=True) -> list[dict]` — `normalize_rows` then `mark_duplicates`.
  - `summarize(rows: list[dict]) -> dict` — `{"total", "valid", "invalid", "duplicate"}` where `valid` = importable (parsed OK AND not duplicate).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_draft.py`:

```python
from datetime import date as _date
from finapp.services.ledger import create_transaction
from finapp.services.csv_import import build_normalized_preview, summarize


def test_build_normalized_preview_flags_db_and_batch_dupes(import_ctx):
    create_transaction(
        import_ctx, date=_date(2026, 6, 1), amount_cents=4500,
        direction="out", payee="Coffee Shop", is_imported=True,
    )
    rows = [
        {"Date": "2026-06-01", "Amount": "-45.00", "Description": "Coffee Shop"},  # db dup
        {"Date": "2026-06-03", "Amount": "-9.99", "Description": "New"},           # new
        {"Date": "2026-06-03", "Amount": "-9.99", "Description": "New"},           # batch dup
    ]
    cmap = {"date": "Date", "amount": "Amount", "payee": "Description"}
    out = build_normalized_preview(import_ctx, rows, cmap, spent_is_negative=True)
    assert out[0]["is_duplicate"] is True and out[0]["selected"] is False
    assert out[1]["is_duplicate"] is False and out[1]["selected"] is True
    assert out[2]["is_duplicate"] is True and out[2]["selected"] is False


def test_summarize_counts(import_ctx):
    rows = [
        {"Date": "2026-06-01", "Amount": "-45.00", "Description": "A"},   # valid
        {"Date": "bad", "Amount": "-1.00", "Description": "B"},           # invalid
        {"Date": "2026-06-01", "Amount": "-45.00", "Description": "A"},   # batch dup of first
    ]
    cmap = {"date": "Date", "amount": "Amount", "payee": "Description"}
    out = build_normalized_preview(import_ctx, rows, cmap)
    s = summarize(out)
    assert s == {"total": 3, "valid": 1, "invalid": 1, "duplicate": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_import_draft.py -k "normalized_preview or summarize" -v`
Expected: FAIL with `ImportError: cannot import name 'build_normalized_preview'`

- [ ] **Step 3: Add the functions**

In `finapp/services/csv_import.py`, add below `normalize_rows`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_import_draft.py -k "normalized_preview or summarize" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add finapp/services/csv_import.py tests/test_import_draft.py
git commit -m "feat: add duplicate marking, preview assembly, and summary"
```

---

### Task 4: Draft persistence + expiry

**Files:**
- Modify: `finapp/services/csv_import.py`
- Test: `tests/test_import_draft.py`

**Interfaces:**
- Consumes: `ImportDraft`, `AccountContext`.
- Produces:
  - `DRAFT_TTL_SECONDS = 3600`
  - `class DraftNotFoundError(Exception)`, `class DraftExpiredError(Exception)`
  - `create_draft(ctx, column_map: dict, spent_is_negative: bool, rows: list[dict]) -> ImportDraft`
  - `load_draft(ctx, draft_id: int) -> ImportDraft` — raises `DraftNotFoundError` (wrong/unknown id or other account) or `DraftExpiredError` (now > expires_at).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_draft.py`:

```python
import json
from datetime import datetime as _dt, timedelta as _td
from finapp.services.csv_import import (
    create_draft, load_draft, DraftNotFoundError, DraftExpiredError,
)


def test_create_and_load_draft_round_trip(import_ctx):
    rows = [{"row_id": 0, "date": "2026-06-01", "amount_cents": 4500,
             "direction": "out", "payee": "A", "import_hash": "h",
             "is_duplicate": False, "selected": True, "issues": [], "valid": True}]
    draft = create_draft(import_ctx, {"date": "Date"}, True, rows)
    assert draft.id is not None

    loaded = load_draft(import_ctx, draft.id)
    assert loaded.id == draft.id
    assert json.loads(loaded.rows_json)[0]["payee"] == "A"


def test_load_unknown_draft_raises_not_found(import_ctx):
    with pytest.raises(DraftNotFoundError):
        load_draft(import_ctx, 99999)


def test_load_expired_draft_raises_expired(import_ctx):
    draft = create_draft(import_ctx, {"date": "Date"}, True, [])
    draft.expires_at = _dt(2000, 1, 1, 0, 0, 0)  # force into the past
    import_ctx.db.commit()
    with pytest.raises(DraftExpiredError):
        load_draft(import_ctx, draft.id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_import_draft.py -k "draft_round_trip or not_found or expired" -v`
Expected: FAIL with `ImportError: cannot import name 'create_draft'`

- [ ] **Step 3: Add persistence code**

At the top of `finapp/services/csv_import.py`, extend imports:

```python
import json
from datetime import date, datetime, timedelta
```

(The file already imports `from datetime import date, datetime` — replace that line with the one above. Add `import json` near the other stdlib imports.) Also add to the existing model import:

```python
from finapp.models import Transaction, ImportDraft
```

Then append to the file:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_import_draft.py -k "draft_round_trip or not_found or expired" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add finapp/services/csv_import.py tests/test_import_draft.py
git commit -m "feat: add CSV import draft persistence and expiry"
```

---

### Task 5: Confirm — apply edits, revalidate, re-dedup, import, summarize

**Files:**
- Modify: `finapp/services/csv_import.py`
- Test: `tests/test_import_draft.py`

**Interfaces:**
- Consumes: `load_draft`, `compute_import_hash`, `create_transaction`, `to_cents`, `_safe_parse_date`, `_safe_parse_amount`, `Transaction`.
- Produces:
  - `_apply_edit(row: dict, edit: dict) -> dict` — returns a new normalized row with edited `date`/`amount`/`direction`/`payee` applied, issues + import_hash recomputed.
  - `confirm_import(ctx, draft_id: int, selected_row_ids: list[int], edits: dict[str, dict]) -> dict` — returns `{"imported_count", "skipped_duplicate_count", "skipped_invalid_count", "skipped_unselected_count", "ignored_row_ids", "errors"}`. Deletes the draft when done.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_draft.py`:

```python
from finapp.services.csv_import import confirm_import
from finapp.models import Transaction


def _preview_rows():
    return [
        {"row_id": 0, "date": "2026-06-01", "amount_cents": 4500, "direction": "out",
         "payee": "Coffee", "import_hash": None, "is_duplicate": False,
         "selected": True, "issues": [], "valid": True},
        {"row_id": 1, "date": "2026-06-02", "amount_cents": 150000, "direction": "in",
         "payee": "Paycheck", "import_hash": None, "is_duplicate": False,
         "selected": True, "issues": [], "valid": True},
        {"row_id": 2, "date": "bad", "amount_cents": 0, "direction": "out",
         "payee": "Broken", "import_hash": None, "is_duplicate": False,
         "selected": False, "issues": ["date"], "valid": False},
    ]


def _make_draft(ctx):
    # import_hash is recomputed on confirm, so None placeholders above are fine.
    return create_draft(ctx, {"date": "Date"}, True, _preview_rows())


def test_confirm_imports_only_selected_valid_rows(import_ctx):
    draft = _make_draft(import_ctx)
    summary = confirm_import(import_ctx, draft.id, selected_row_ids=[0, 1], edits={})
    assert summary["imported_count"] == 2
    assert summary["skipped_unselected_count"] == 1  # row 2 not selected
    txns = import_ctx.db.query(Transaction).filter_by(account_id=import_ctx.account_id).all()
    assert len(txns) == 2
    assert all(t.is_imported for t in txns)


def test_confirm_skips_selected_invalid_row(import_ctx):
    draft = _make_draft(import_ctx)
    summary = confirm_import(import_ctx, draft.id, selected_row_ids=[0, 2], edits={})
    assert summary["imported_count"] == 1
    assert summary["skipped_invalid_count"] == 1
    assert any(e["row_id"] == 2 and e["reason"] == "invalid" for e in summary["errors"])


def test_confirm_redups_against_current_db(import_ctx):
    # Row 0 becomes a duplicate of a txn created AFTER the preview was built.
    create_transaction(import_ctx, date=_date(2026, 6, 1), amount_cents=4500,
                       direction="out", payee="Coffee", is_imported=True)
    draft = _make_draft(import_ctx)
    summary = confirm_import(import_ctx, draft.id, selected_row_ids=[0, 1], edits={})
    assert summary["imported_count"] == 1          # only the paycheck
    assert summary["skipped_duplicate_count"] == 1  # coffee now a dup


def test_confirm_applies_edits_and_revalidates(import_ctx):
    draft = _make_draft(import_ctx)
    # Fix the broken row 2's date via an edit, then select it.
    summary = confirm_import(
        import_ctx, draft.id, selected_row_ids=[2],
        edits={"2": {"date": "2026-06-05", "amount": "12.50", "direction": "out", "payee": "Fixed"}},
    )
    assert summary["imported_count"] == 1
    txn = import_ctx.db.query(Transaction).filter_by(payee="Fixed").first()
    assert txn is not None
    assert txn.amount_cents == 1250
    assert txn.date == _date(2026, 6, 5)


def test_confirm_ignores_unknown_row_ids(import_ctx):
    draft = _make_draft(import_ctx)
    summary = confirm_import(import_ctx, draft.id, selected_row_ids=[0, 999],
                             edits={"888": {"payee": "Ghost"}})
    assert summary["imported_count"] == 1
    assert 999 in summary["ignored_row_ids"]
    assert 888 in summary["ignored_row_ids"]


def test_confirm_deletes_draft(import_ctx):
    draft = _make_draft(import_ctx)
    confirm_import(import_ctx, draft.id, selected_row_ids=[0], edits={})
    with pytest.raises(DraftNotFoundError):
        load_draft(import_ctx, draft.id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_import_draft.py -k confirm -v`
Expected: FAIL with `ImportError: cannot import name 'confirm_import'`

- [ ] **Step 3: Add `_apply_edit` and `confirm_import`**

Append to `finapp/services/csv_import.py`:

```python
def _apply_edit(row: dict, edit: dict) -> dict:
    """Return a new normalized row with edit fields applied and issues +
    import_hash recomputed. edit may carry date (str), amount (display string),
    direction ('in'/'out'), payee (str). Missing keys keep the existing value."""
    new = dict(row)

    date_str = edit.get("date", row["date"])
    payee = edit.get("payee", row["payee"]) or ""
    direction = edit.get("direction", row["direction"])

    if "amount" in edit and edit["amount"] is not None:
        amount_decimal = _safe_parse_amount(str(edit["amount"]))
        amount_cents = to_cents(abs(amount_decimal)) if amount_decimal is not None else None
    else:
        amount_cents = row["amount_cents"]

    issues = []
    parsed_date = _safe_parse_date(date_str) if date_str else None
    if parsed_date is None:
        issues.append("date")
    if amount_cents is None:
        issues.append("amount")
    if direction not in ("in", "out"):
        issues.append("direction")

    valid = not issues
    new["date"] = parsed_date.isoformat() if parsed_date else date_str
    new["amount_cents"] = amount_cents if amount_cents is not None else 0
    new["direction"] = direction
    new["payee"] = payee
    new["issues"] = issues
    new["valid"] = valid
    new["import_hash"] = (
        compute_import_hash(parsed_date, new["amount_cents"], payee) if valid else None
    )
    return new


def confirm_import(ctx: AccountContext, draft_id: int,
                   selected_row_ids: list[int], edits: dict) -> dict:
    """Apply edits, revalidate, re-check dedup against current DB state, import
    selected valid non-duplicate rows through the ledger, and return a
    partial-success summary. Deletes the draft on completion."""
    draft = load_draft(ctx, draft_id)
    rows = json.loads(draft.rows_json)
    by_id = {r["row_id"]: r for r in rows}

    ignored_row_ids = []

    # Apply edits (unknown row ids are ignored + reported).
    for key, edit in (edits or {}).items():
        rid = int(key)
        if rid not in by_id:
            ignored_row_ids.append(rid)
            continue
        by_id[rid] = _apply_edit(by_id[rid], edit)

    # Resolve selection (unknown ids ignored + reported).
    selected = set()
    for rid in (selected_row_ids or []):
        if rid in by_id:
            selected.add(rid)
        else:
            ignored_row_ids.append(rid)

    imported = duplicate = invalid = unselected = 0
    errors = []
    seen_hashes = set()  # intra-confirm dedup

    for rid, r in by_id.items():
        if rid not in selected:
            unselected += 1
            continue
        if not r["valid"]:
            invalid += 1
            errors.append({"row_id": rid, "reason": "invalid", "issues": r["issues"]})
            continue

        h = r["import_hash"]
        db_dup = ctx.db.query(Transaction).filter_by(
            account_id=ctx.account_id, import_hash=h
        ).first() is not None
        if db_dup or h in seen_hashes:
            duplicate += 1
            errors.append({"row_id": rid, "reason": "duplicate"})
            continue
        seen_hashes.add(h)

        create_transaction(
            ctx,
            date=date.fromisoformat(r["date"]),
            amount_cents=r["amount_cents"],
            direction=r["direction"],
            payee=r["payee"] or None,
            is_imported=True,
        )
        imported += 1

    ctx.db.delete(draft)
    ctx.db.commit()

    return {
        "imported_count": imported,
        "skipped_duplicate_count": duplicate,
        "skipped_invalid_count": invalid,
        "skipped_unselected_count": unselected,
        "ignored_row_ids": ignored_row_ids,
        "errors": errors,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_import_draft.py -k confirm -v`
Expected: PASS

- [ ] **Step 5: Run the full draft + legacy unit suite**

Run: `uv run pytest tests/test_import_draft.py tests/test_csv_import.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add finapp/services/csv_import.py tests/test_import_draft.py
git commit -m "feat: add confirm flow with edits, re-dedup, and partial-success summary"
```

---

### Task 6: Router — draft-backed preview + confirm contracts

**Files:**
- Modify: `finapp/routers/settings.py`
- Modify: `tests/integration/test_phase10_import_settings.py`

**Interfaces:**
- Consumes: `csv_import_svc.build_normalized_preview`, `create_draft`, `summarize`, `confirm_import`, `DraftNotFoundError`, `DraftExpiredError`.
- Produces:
  - `POST /settings/import/csv` → `{"draft_id": int, "expires_at": str, "rows": [normalized rows], "summary": {...}}`
  - `POST /settings/import/confirm` body `{"draft_id", "selected_row_ids", "edits", "version?"}` → confirm summary dict. `404` (`{"code":"draft_not_found"}`) / `410` (`{"code":"draft_expired", ...}`) on draft errors.
  - Pydantic `RowEdit` and `ConfirmImportRequest`.

- [ ] **Step 1: Update the integration tests to the new contracts (write the failing tests)**

In `tests/integration/test_phase10_import_settings.py`, replace `test_import_upload_returns_preview_and_remembers_mapping`, `test_confirm_imports_into_correct_periods_and_inbox`, and `test_reconciliation_green_after_mixed_duplicate_import` with the versions below, and add the three new tests. (Leave `test_import_preview_flags_existing_duplicate` as-is — it only reads `payee`/`is_duplicate`/`selected`, which the new rows still carry.)

```python
def test_import_upload_returns_draft_and_remembers_mapping(client, ctx):
    resp = _upload(client)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["draft_id"], int)
    assert "expires_at" in body
    assert len(body["rows"]) == 3
    assert body["summary"]["total"] == 3
    # Each row exposes the editable contract fields.
    assert {"row_id", "date", "amount_cents", "direction", "payee",
            "is_duplicate", "selected", "issues", "valid"} <= set(body["rows"][0])
    saved = settings_svc.get_csv_column_map(ctx)
    assert saved.get("date") == "Date"
    assert saved.get("amount") == "Amount"
    assert saved.get("payee") == "Description"


def _confirm_all_valid(client):
    """Upload, then confirm every row the preview marked selected."""
    preview = _upload(client).json()
    selected = [r["row_id"] for r in preview["rows"] if r["selected"]]
    return client.post("/settings/import/confirm", json={
        "draft_id": preview["draft_id"],
        "selected_row_ids": selected,
        "edits": {},
    })


def test_confirm_imports_into_correct_periods_and_inbox(client, ctx):
    resp = _confirm_all_valid(client)
    assert resp.status_code == 200
    assert resp.json()["imported_count"] == 3

    txns = ctx.db.query(Transaction).filter_by(account_id=ctx.account_id).all()
    assert len(txns) == 3
    assert all(t.is_imported for t in txns)
    assert all(t.category_id is None for t in txns)
    by_payee = {t.payee: t for t in txns}
    assert (by_payee["Old Thing"].period.year, by_payee["Old Thing"].period.month) == (2026, 5)
    assert (by_payee["Coffee Shop"].period.year, by_payee["Coffee Shop"].period.month) == (2026, 6)

    inbox = client.get("/api/needs-category")
    assert inbox.status_code == 200
    assert inbox.json()["count"] == 3


def test_confirm_excludes_unselected_duplicate(client, ctx):
    # Pre-existing imported txn duplicates the first CSV row.
    create_transaction(ctx, date=date(2026, 6, 1), amount_cents=4500,
                       direction="out", payee="Coffee Shop", is_imported=True)
    preview = _upload(client).json()
    # The duplicate row defaults to unselected; confirm the selected ones.
    selected = [r["row_id"] for r in preview["rows"] if r["selected"]]
    resp = client.post("/settings/import/confirm", json={
        "draft_id": preview["draft_id"],
        "selected_row_ids": selected,
        "edits": {},
    })
    assert resp.json()["imported_count"] == 2  # Old Thing + Paycheck


def test_confirm_inline_edit_flow(client, ctx):
    preview = _upload(client).json()
    coffee = next(r for r in preview["rows"] if r["payee"] == "Coffee Shop")
    resp = client.post("/settings/import/confirm", json={
        "draft_id": preview["draft_id"],
        "selected_row_ids": [coffee["row_id"]],
        "edits": {str(coffee["row_id"]): {"payee": "Cafe Edited", "amount": "50.00"}},
    })
    assert resp.json()["imported_count"] == 1
    txn = ctx.db.query(Transaction).filter_by(payee="Cafe Edited").first()
    assert txn is not None
    assert txn.amount_cents == 5000


def test_confirm_expired_draft_returns_410(client, ctx):
    from finapp.models import ImportDraft
    preview = _upload(client).json()
    draft = ctx.db.query(ImportDraft).filter_by(id=preview["draft_id"]).first()
    from datetime import datetime as _dt
    draft.expires_at = _dt(2000, 1, 1)
    ctx.db.commit()
    resp = client.post("/settings/import/confirm", json={
        "draft_id": preview["draft_id"], "selected_row_ids": [0], "edits": {},
    })
    assert resp.status_code == 410
    assert resp.json()["detail"]["code"] == "draft_expired"


def test_reconciliation_green_after_mixed_duplicate_import(client, ctx):
    create_transaction(ctx, date=date(2026, 6, 1), amount_cents=4500,
                       direction="out", payee="Coffee Shop", is_imported=True)
    _confirm_all_valid(client)  # duplicate auto-excluded, new rows imported
    report = recompute_balances(ctx.db, ctx.account_id)
    assert report["total_drift"] == 0
```

- [ ] **Step 2: Run the integration tests to verify they fail**

Run: `uv run pytest tests/integration/test_phase10_import_settings.py -v`
Expected: FAIL — new-contract tests fail because the router still returns the old `{"rows", "duplicate_count"}` / accepts old `{"rows": [...]}` shapes.

- [ ] **Step 3: Rewrite the `import_csv` router**

In `finapp/routers/settings.py`, replace the entire body of `import_csv` (everything after `# Read file content`) with:

```python
    # Read file content
    content = (await file.read()).decode("utf-8")

    # Build column map
    column_map = {
        "date": map_date,
        "amount": map_amount,
        "payee": map_payee,
    }
    if map_direction and map_direction.strip():
        column_map["direction"] = map_direction

    # Save mapping for next time
    settings_svc.save_csv_column_map(ctx, column_map)

    # Parse + normalize + dedup
    rows = csv_import_svc.parse_csv(content)
    spent_neg = spent_is_negative.lower() in ("true", "1", "yes", "on")
    preview = csv_import_svc.build_normalized_preview(
        ctx, rows, column_map, spent_is_negative=spent_neg
    )

    # Persist a draft and return it
    draft = csv_import_svc.create_draft(ctx, column_map, spent_neg, preview)

    return {
        "draft_id": draft.id,
        "expires_at": draft.expires_at.isoformat(),
        "rows": preview,
        "summary": csv_import_svc.summarize(preview),
    }
```

Update the `import_csv` docstring's "Returns:" block to describe `draft_id`, `expires_at`, `rows`, `summary` (replace the old `rows`/`duplicate_count` description).

- [ ] **Step 4: Add Pydantic models and rewrite the `confirm_import` router**

In `finapp/routers/settings.py`, add these models near `SettingsUpdateRequest` (after it):

```python
class RowEdit(BaseModel):
    """A single inline edit. Any subset of fields may be provided."""
    date: Optional[str] = None
    amount: Optional[str] = None   # display string, e.g. "45.00"
    direction: Optional[str] = None
    payee: Optional[str] = None


class ConfirmImportRequest(BaseModel):
    draft_id: int
    selected_row_ids: list[int] = []
    edits: dict[str, RowEdit] = {}
    version: Optional[int] = None
```

Then replace the entire `confirm_import` function with:

```python
@router.post("/settings/import/confirm")
def confirm_import(
    body: ConfirmImportRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Confirm a draft-backed import.

    Request body:
    {
        "draft_id": 12,
        "selected_row_ids": [0, 1, 3],
        "edits": {"3": {"date": "2026-06-05", "amount": "12.50",
                         "direction": "out", "payee": "Fixed"}},
        "version": 1            # optional
    }

    Returns the import summary:
    {
        "imported_count": int,
        "skipped_duplicate_count": int,
        "skipped_invalid_count": int,
        "skipped_unselected_count": int,
        "ignored_row_ids": [int, ...],
        "errors": [{"row_id": int, "reason": "invalid"|"duplicate", ...}]
    }
    """
    edits = {k: v.dict(exclude_unset=True) for k, v in body.edits.items()}
    try:
        return csv_import_svc.confirm_import(
            ctx, body.draft_id, body.selected_row_ids, edits
        )
    except csv_import_svc.DraftNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "draft_not_found"})
    except csv_import_svc.DraftExpiredError:
        raise HTTPException(
            status_code=410,
            detail={"code": "draft_expired",
                    "message": "Your import preview expired. Please re-upload the file."},
        )
```

Remove the now-unused `date` import usage check: `date` is still used elsewhere in the file (`get_needs_category` does not use it, but keep the existing `from datetime import date, datetime` import — `datetime` is used by backup). No import change needed.

- [ ] **Step 5: Run the integration tests to verify they pass**

Run: `uv run pytest tests/integration/test_phase10_import_settings.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add finapp/routers/settings.py tests/integration/test_phase10_import_settings.py
git commit -m "feat: draft-backed CSV import preview and confirm endpoints"
```

---

### Task 7: Settings UI — editable preview panel + JS

**Files:**
- Modify: `finapp/templates/settings.html`
- Modify: `tests/integration/test_phase10_import_settings.py`

**Interfaces:**
- Consumes: `POST /settings/import/csv` (returns `draft_id`, `rows`, `summary`), `POST /settings/import/confirm`.
- Produces: a preview panel (`id="import-preview"`), a results banner (`id="import-result"`), and JS functions `previewImport()`, `renderPreview(data)`, `confirmImport()`, `selectAllValid()`, `unselectAll()`, `resetEdits()`.

- [ ] **Step 1: Write the failing test (page contains the new UI hooks)**

Add to `tests/integration/test_phase10_import_settings.py`:

```python
def test_settings_page_has_import_preview_ui(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="import-preview"' in html
    assert 'id="import-result"' in html
    assert "function previewImport" in html
    assert "function confirmImport" in html
    assert "selectAllValid" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_phase10_import_settings.py::test_settings_page_has_import_preview_ui -v`
Expected: FAIL (`assert 'id="import-preview"' in html` is False)

- [ ] **Step 3: Make the form trigger JS preview instead of a full-page POST**

In `finapp/templates/settings.html`, change the import form opening tag and submit button. Replace:

```html
        <form action="/settings/import/csv" method="post" enctype="multipart/form-data" class="space-y-4">
```

with:

```html
        <form id="import-form" onsubmit="event.preventDefault(); previewImport();" enctype="multipart/form-data" class="space-y-4">
```

Replace:

```html
            <button type="submit" class="btn-add-transaction">Preview import</button>
```

with:

```html
            <button type="submit" class="btn-add-transaction">Preview import</button>
            <p id="import-error" class="text-sm mt-2" style="color: var(--amber, #F59E0B);"></p>
```

- [ ] **Step 4: Add the preview panel markup**

In `finapp/templates/settings.html`, immediately AFTER the closing `</form>` of the import form and BEFORE the `<p class="text-xs text-muted mt-3">Duplicates already in your records...` line, insert:

```html
        <div id="import-preview" class="mt-6 hidden">
            <div class="flex flex-wrap items-center gap-3 mb-3">
                <span id="import-summary" class="text-sm text-muted"></span>
                <button type="button" onclick="selectAllValid()" class="text-sm underline">Select all valid</button>
                <button type="button" onclick="unselectAll()" class="text-sm underline">Unselect all</button>
                <button type="button" onclick="resetEdits()" class="text-sm underline">Reset edits</button>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-sm">
                    <thead>
                        <tr class="text-left text-muted">
                            <th class="py-1 pr-2"></th>
                            <th class="py-1 pr-2">Date</th>
                            <th class="py-1 pr-2">Amount</th>
                            <th class="py-1 pr-2">Direction</th>
                            <th class="py-1 pr-2">Payee</th>
                            <th class="py-1 pr-2">Status</th>
                        </tr>
                    </thead>
                    <tbody id="import-rows"></tbody>
                </table>
            </div>
            <button type="button" onclick="confirmImport()" class="btn-add-transaction mt-4">Confirm import</button>
        </div>
        <p id="import-result" class="text-sm mt-3"></p>
```

- [ ] **Step 5: Add the JS**

In `finapp/templates/settings.html`, inside the existing `<script>` block (before the closing `</script>`), add:

```javascript
    let importDraft = null;     // {draft_id, expires_at, rows, summary}
    let importOriginal = {};    // row_id -> original {date, amount, direction, payee}

    function _dollars(cents) { return (cents / 100).toFixed(2); }

    async function previewImport() {
        const form = document.getElementById('import-form');
        const errEl = document.getElementById('import-error');
        const resultEl = document.getElementById('import-result');
        errEl.textContent = '';
        resultEl.textContent = '';
        const fd = new FormData();
        fd.append('file', form.file.files[0]);
        fd.append('map_date', form.map_date.value);
        fd.append('map_amount', form.map_amount.value);
        fd.append('map_payee', form.map_payee.value);
        fd.append('spent_is_negative', form.spent_is_negative.value);
        try {
            const resp = await fetch('/settings/import/csv', { method: 'POST', body: fd });
            if (!resp.ok) { errEl.textContent = 'Could not read that file. Check your column names.'; return; }
            const data = await resp.json();
            importDraft = data;
            renderPreview(data);
        } catch (e) {
            errEl.textContent = 'Could not upload the file.';
        }
    }

    function renderPreview(data) {
        importOriginal = {};
        const tbody = document.getElementById('import-rows');
        tbody.innerHTML = '';
        for (const r of data.rows) {
            importOriginal[r.row_id] = {
                date: r.date, amount: _dollars(r.amount_cents),
                direction: r.direction, payee: r.payee,
            };
            const status = !r.valid ? 'Invalid' : (r.is_duplicate ? 'Duplicate' : 'Valid');
            const tr = document.createElement('tr');
            tr.dataset.rowId = r.row_id;
            tr.innerHTML = `
                <td class="py-1 pr-2"><input type="checkbox" class="row-select" ${r.selected ? 'checked' : ''}></td>
                <td class="py-1 pr-2"><input class="row-date form-input px-2 py-1 rounded" value="${r.date}"></td>
                <td class="py-1 pr-2"><input class="row-amount form-input px-2 py-1 rounded w-24" value="${_dollars(r.amount_cents)}"></td>
                <td class="py-1 pr-2">
                    <select class="row-direction form-input px-2 py-1 rounded">
                        <option value="out" ${r.direction === 'out' ? 'selected' : ''}>out</option>
                        <option value="in" ${r.direction === 'in' ? 'selected' : ''}>in</option>
                    </select>
                </td>
                <td class="py-1 pr-2"><input class="row-payee form-input px-2 py-1 rounded" value="${r.payee.replace(/"/g, '&quot;')}"></td>
                <td class="py-1 pr-2 text-muted">${status}</td>`;
            tbody.appendChild(tr);
        }
        const s = data.summary;
        document.getElementById('import-summary').textContent =
            `${s.total} rows · ${s.valid} ready · ${s.duplicate} duplicate · ${s.invalid} invalid`;
        document.getElementById('import-preview').classList.remove('hidden');
    }

    function _eachRow(fn) {
        document.querySelectorAll('#import-rows tr').forEach(fn);
    }

    function selectAllValid() {
        const valid = new Set(
            importDraft.rows.filter(r => r.valid && !r.is_duplicate).map(r => r.row_id)
        );
        _eachRow(tr => {
            tr.querySelector('.row-select').checked = valid.has(parseInt(tr.dataset.rowId, 10));
        });
    }

    function unselectAll() {
        _eachRow(tr => { tr.querySelector('.row-select').checked = false; });
    }

    function resetEdits() {
        _eachRow(tr => {
            const orig = importOriginal[parseInt(tr.dataset.rowId, 10)];
            tr.querySelector('.row-date').value = orig.date;
            tr.querySelector('.row-amount').value = orig.amount;
            tr.querySelector('.row-direction').value = orig.direction;
            tr.querySelector('.row-payee').value = orig.payee;
        });
    }

    async function confirmImport() {
        if (!importDraft) return;
        const resultEl = document.getElementById('import-result');
        const selected = [];
        const edits = {};
        _eachRow(tr => {
            const id = parseInt(tr.dataset.rowId, 10);
            if (tr.querySelector('.row-select').checked) selected.push(id);
            const cur = {
                date: tr.querySelector('.row-date').value,
                amount: tr.querySelector('.row-amount').value,
                direction: tr.querySelector('.row-direction').value,
                payee: tr.querySelector('.row-payee').value,
            };
            const orig = importOriginal[id];
            const changed = {};
            for (const k of ['date', 'amount', 'direction', 'payee']) {
                if (cur[k] !== orig[k]) changed[k] = cur[k];
            }
            if (Object.keys(changed).length) edits[id] = changed;
        });

        const resp = await fetch('/settings/import/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ draft_id: importDraft.draft_id, selected_row_ids: selected, edits }),
        });

        if (resp.status === 410) {
            resultEl.textContent = 'Your preview expired — please upload the file again.';
            document.getElementById('import-preview').classList.add('hidden');
            importDraft = null;
            return;
        }
        if (!resp.ok) { resultEl.textContent = 'Import could not be completed.'; return; }

        const r = await resp.json();
        resultEl.textContent =
            `Imported ${r.imported_count} · skipped ${r.skipped_duplicate_count} duplicate, ` +
            `${r.skipped_invalid_count} invalid, ${r.skipped_unselected_count} unselected.`;
        document.getElementById('import-preview').classList.add('hidden');
        importDraft = null;
    }
```

- [ ] **Step 6: Run the UI hook test to verify it passes**

Run: `uv run pytest tests/integration/test_phase10_import_settings.py::test_settings_page_has_import_preview_ui -v`
Expected: PASS

- [ ] **Step 7: Manual browser verification**

Run: `uv run python finapp/run.py`
Then in the browser at `http://127.0.0.1:5000/settings`:
1. Upload a CSV with columns Date/Amount/Description; click **Preview import**.
2. Confirm the table renders with checkboxes, editable cells, and per-row status.
3. Edit one row's amount, uncheck a duplicate, click **Confirm import**.
4. Confirm the result banner shows the imported/skipped counts and the Needs Category section reflects the new transactions after a refresh.

Expected: full upload → preview → edit → select → confirm works in the browser. Stop the server when done.

- [ ] **Step 8: Commit**

```bash
git add finapp/templates/settings.html tests/integration/test_phase10_import_settings.py
git commit -m "feat: editable CSV import preview panel in Settings"
```

---

### Task 8: Done gate — full suite, reconciliation, lint

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (all unit + integration tests, including the existing `test_csv_import.py` and the new `test_import_draft.py`).

- [ ] **Step 2: Confirm the reconciliation gate is green**

The reconciliation assertion lives in `test_reconciliation_green_after_mixed_duplicate_import`. Confirm it passes specifically:

Run: `uv run pytest tests/integration/test_phase10_import_settings.py::test_reconciliation_green_after_mixed_duplicate_import -v`
Expected: PASS (`report["total_drift"] == 0`)

- [ ] **Step 3: Lint**

Run: `uv run flake8 finapp/ --max-line-length=100`
Expected: no output (clean). Fix any flagged lines in `finapp/services/csv_import.py` or `finapp/routers/settings.py`.

- [ ] **Step 4: Verify migrations from scratch**

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: `import_drafts` drops then recreates with no errors; final `alembic current` shows `c4d5e6f7a8b9 (head)`.

- [ ] **Step 5: Final commit (if any lint fixes were made)**

```bash
git add -A
git commit -m "chore: lint and reconciliation gate for CSV import reimplementation"
```

---

## Self-Review

**Spec coverage:**
- §4 / §5 draft model + service steps → Tasks 1–5.
- §5.3 persistence (`import_drafts`, all listed fields, account-scoped access) → Task 1 (model/migration), Task 4 (account-scoped `load_draft`).
- §6.1 preview (parse, normalize, dedup DB + intra-batch, return draft_id/expires_at/rows/summary) → Tasks 2, 3, 6.
- §6.2 edit/select (client sends intent only) → Task 7 JS diffs edits, sends selection.
- §6.3 confirm (load by account+id, apply edits, revalidate, recompute hash + DB dedup, import via ledger, summary) → Task 5, surfaced in Task 6.
- §7.1 / §7.2 stable paths + new request/response shapes → Task 6.
- §8 UI (editable table, status indicators, selection controls, reset edits, confirm, result banner) → Task 7.
- §9 validation/error handling (per-row issues, partial success, expired-draft recovery, unknown-id reporting, authoritative confirm dedup) → Tasks 2, 5, 6.
- §10 invariants (ledger single write path, integer cents/magnitude + direction sign, account scoping, shared `compute_import_hash`) → enforced across Tasks 2–5 + Global Constraints.
- §11 testing (service tests for draft/expiry/edits/dedup-race/partial-success/unknown-ids; integration tests for draft metadata, selected-only import, expiry recovery, inline-edit flow; regression of parser tests + reconciliation) → Tasks 1–8.
- §12 migration + path/compat preservation → Task 1 migration; Task 6 keeps form fields and paths.
- §13 acceptance criteria → exercised by Task 7 manual verification + Task 6/8 automated tests.

**Placeholder scan:** No TBD/“handle edge cases”/“add validation” placeholders; every code step shows complete code and every command states expected output.

**Type consistency:** The normalized-row dict schema is defined once (File Structure) and used identically by `normalize_rows` (Task 2), `mark_duplicates`/`summarize` (Task 3), `create_draft`/`load_draft` (Task 4), `_apply_edit`/`confirm_import` (Task 5), the router (Task 6), and the JS (Task 7). Function names (`build_normalized_preview`, `summarize`, `create_draft`, `load_draft`, `confirm_import`, `_apply_edit`, `DraftNotFoundError`, `DraftExpiredError`) and the confirm summary keys are referenced consistently across Tasks 5–7. Migration revision `c4d5e6f7a8b9` / down_revision `f2a19aaf069f` match the current head.
