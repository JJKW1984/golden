"""
Phase 10 — CSV export service tests.

export_transactions_csv(ctx) returns CSV text of all non-deleted transactions.
It must round-trip: exporting then re-parsing (via the csv_import service) yields
the same logical transactions (date, magnitude in cents, direction, payee).
"""
import pytest
from datetime import date
from finapp.deps import AccountContext
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction
from finapp.services.export_csv import export_transactions_csv
from finapp.services.csv_import import parse_csv, build_preview


@pytest.fixture
def export_ctx(db, account):
    seed_default_categories(db, account.id)
    return AccountContext(account_id=account.id, db=db)


def test_export_has_header_and_rows(export_ctx):
    create_transaction(export_ctx, date=date(2026, 6, 1), amount_cents=4500,
                       direction="out", payee="Coffee Shop")
    create_transaction(export_ctx, date=date(2026, 6, 2), amount_cents=120000,
                       direction="in", payee="Paycheck")
    csv_text = export_transactions_csv(export_ctx)
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    assert len(lines) == 3  # header + 2 rows
    header = lines[0].lower()
    assert "date" in header
    assert "amount" in header
    assert "payee" in header


def test_export_excludes_soft_deleted(export_ctx):
    t = create_transaction(export_ctx, date=date(2026, 6, 1), amount_cents=4500,
                           direction="out", payee="Coffee")
    from finapp.services.ledger import void_transaction
    void_transaction(export_ctx, t.id)
    csv_text = export_transactions_csv(export_ctx)
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    assert len(lines) == 1  # header only


def test_export_round_trips_through_import(export_ctx):
    create_transaction(export_ctx, date=date(2026, 6, 1), amount_cents=4500,
                       direction="out", payee="Coffee Shop")
    create_transaction(export_ctx, date=date(2026, 6, 2), amount_cents=120000,
                       direction="in", payee="Paycheck")

    csv_text = export_transactions_csv(export_ctx)
    rows = parse_csv(csv_text)
    column_map = {"date": "date", "amount": "amount", "payee": "payee"}
    preview = build_preview(export_ctx, rows, column_map, spent_is_negative=True)

    by_payee = {p.payee: p for p in preview}
    assert by_payee["Coffee Shop"].amount_cents == 4500
    assert by_payee["Coffee Shop"].direction == "out"
    assert by_payee["Coffee Shop"].date == date(2026, 6, 1)
    assert by_payee["Paycheck"].amount_cents == 120000
    assert by_payee["Paycheck"].direction == "in"
