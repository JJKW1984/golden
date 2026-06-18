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
