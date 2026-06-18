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
