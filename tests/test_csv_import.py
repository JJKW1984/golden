"""
Phase 10 — CSV Import service tests.

The csv_import service parses uploaded CSV text, applies a (remembered) column
mapping, resolves direction via the "which way means you spent money?" toggle,
computes the dedup hash via the SAME function the ledger uses (so preview
duplicate-detection matches what the ledger will store), and flags duplicates.

Invariants:
- amount_cents is always a non-negative magnitude; direction carries the sign.
- The dedup hash MUST be ledger.compute_import_hash so preview == stored.
- Duplicates (already in the DB, or earlier in the same batch) default to unselected.
"""
import pytest
from datetime import date
from finapp.deps import AccountContext
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction, compute_import_hash
from finapp.services.csv_import import parse_csv, build_preview, PreviewRow


@pytest.fixture
def import_ctx(db, account):
    seed_default_categories(db, account.id)
    return AccountContext(account_id=account.id, db=db)


def test_parse_csv_returns_list_of_dict_rows():
    content = "Date,Amount,Description\n2026-06-01,-45.00,Coffee Shop\n2026-06-02,1200.00,Paycheck\n"
    rows = parse_csv(content)
    assert len(rows) == 2
    assert rows[0]["Date"] == "2026-06-01"
    assert rows[0]["Amount"] == "-45.00"
    assert rows[0]["Description"] == "Coffee Shop"
    assert rows[1]["Description"] == "Paycheck"


def test_parse_csv_handles_blank_trailing_lines():
    content = "Date,Amount,Description\n2026-06-01,-45.00,Coffee\n\n"
    rows = parse_csv(content)
    assert len(rows) == 1


def test_build_preview_directions(import_ctx):
    rows = [
        {"Date": "2026-06-01", "Amount": "-45.00", "Description": "Coffee Shop"},
        {"Date": "2026-06-02", "Amount": "1200.00", "Description": "Paycheck"},
    ]
    column_map = {"date": "Date", "amount": "Amount", "payee": "Description"}
    preview = build_preview(import_ctx, rows, column_map, spent_is_negative=True)

    assert len(preview) == 2
    out_row = preview[0]
    assert isinstance(out_row, PreviewRow)
    assert out_row.date == date(2026, 6, 1)
    assert out_row.amount_cents == 4500          # magnitude, always >= 0
    assert out_row.direction == "out"
    assert out_row.payee == "Coffee Shop"
    assert out_row.is_duplicate is False
    assert out_row.selected is True

    in_row = preview[1]
    assert in_row.amount_cents == 120000
    assert in_row.direction == "in"


def test_build_preview_spent_is_positive_toggle(import_ctx):
    # Some exports use positive for debits. Toggle flips it.
    rows = [{"Date": "2026-06-01", "Amount": "45.00", "Description": "Store"}]
    column_map = {"date": "Date", "amount": "Amount", "payee": "Description"}
    preview = build_preview(import_ctx, rows, column_map, spent_is_negative=False)
    assert preview[0].direction == "out"
    assert preview[0].amount_cents == 4500


def test_build_preview_parses_common_date_formats(import_ctx):
    rows = [
        {"Date": "06/15/2026", "Amount": "-10.00", "Description": "A"},
        {"Date": "2026-06-16", "Amount": "-10.00", "Description": "B"},
    ]
    column_map = {"date": "Date", "amount": "Amount", "payee": "Description"}
    preview = build_preview(import_ctx, rows, column_map, spent_is_negative=True)
    assert preview[0].date == date(2026, 6, 15)
    assert preview[1].date == date(2026, 6, 16)


def test_build_preview_uses_ledger_hash(import_ctx):
    rows = [{"Date": "2026-06-01", "Amount": "-45.00", "Description": "Coffee Shop"}]
    column_map = {"date": "Date", "amount": "Amount", "payee": "Description"}
    preview = build_preview(import_ctx, rows, column_map, spent_is_negative=True)
    expected = compute_import_hash(date(2026, 6, 1), 4500, "Coffee Shop")
    assert preview[0].import_hash == expected


def test_build_preview_flags_existing_db_duplicate(import_ctx):
    # An imported transaction already exists with a matching hash.
    create_transaction(
        import_ctx,
        date=date(2026, 6, 1),
        amount_cents=4500,
        direction="out",
        payee="Coffee Shop",
        is_imported=True,
    )
    rows = [
        {"Date": "2026-06-01", "Amount": "-45.00", "Description": "Coffee Shop"},  # dup
        {"Date": "2026-06-03", "Amount": "-9.99", "Description": "New Thing"},     # new
    ]
    column_map = {"date": "Date", "amount": "Amount", "payee": "Description"}
    preview = build_preview(import_ctx, rows, column_map, spent_is_negative=True)

    assert preview[0].is_duplicate is True
    assert preview[0].selected is False
    assert preview[1].is_duplicate is False
    assert preview[1].selected is True


def test_build_preview_flags_intra_batch_duplicate(import_ctx):
    rows = [
        {"Date": "2026-06-01", "Amount": "-45.00", "Description": "Coffee Shop"},
        {"Date": "2026-06-01", "Amount": "-45.00", "Description": "Coffee Shop"},  # same as first
    ]
    column_map = {"date": "Date", "amount": "Amount", "payee": "Description"}
    preview = build_preview(import_ctx, rows, column_map, spent_is_negative=True)
    assert preview[0].is_duplicate is False
    assert preview[1].is_duplicate is True
    assert preview[1].selected is False


def test_build_preview_payee_case_insensitive_dedup(import_ctx):
    create_transaction(
        import_ctx, date=date(2026, 6, 1), amount_cents=4500,
        direction="out", payee="coffee shop", is_imported=True,
    )
    rows = [{"Date": "2026-06-01", "Amount": "-45.00", "Description": "COFFEE SHOP"}]
    column_map = {"date": "Date", "amount": "Amount", "payee": "Description"}
    preview = build_preview(import_ctx, rows, column_map, spent_is_negative=True)
    assert preview[0].is_duplicate is True
