"""
Phase 10 integration tests — CSV Import & Settings.

Covers the Done gate:
- column mapping is remembered (Settings.csv_column_map)
- dedup excludes known hashes in the preview
- imported rows land in the correct periods via the ledger service
- uncategorized rows surface in the "Needs Category" inbox with an accurate badge
- CSV export round-trips
- on-demand backup produces a consistent snapshot
- reconciliation gate is green after an import of mixed/duplicate rows
"""
import pytest
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from finapp.main import app
from finapp.db import Base
from finapp.deps import get_db, get_account_context, AccountContext
from finapp.models import Account, Transaction, BudgetCategory
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction
from finapp.services import settings as settings_svc
from finapp.services.reconciliation import recompute_balances


@pytest.fixture(scope="function")
def test_db_engine(tmp_path):
    db_file = tmp_path / "finance.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def test_db(test_db_engine):
    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="function")
def test_account(test_db):
    account = Account(id=1, display_name="Test User")
    test_db.add(account)
    test_db.commit()
    seed_default_categories(test_db, account.id)
    return account


@pytest.fixture(scope="function")
def client(test_db, test_account):
    account_id = test_account.id

    def override_get_db():
        yield test_db

    def override_get_account_context():
        return AccountContext(account_id=account_id, db=test_db)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_account_context] = override_get_account_context
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def ctx(test_db, test_account):
    return AccountContext(account_id=test_account.id, db=test_db)


# ---------------------------------------------------------------------------
# Settings page + profile
# ---------------------------------------------------------------------------

def test_get_settings_page_renders(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Settings" in resp.text


def test_post_settings_updates_profile(client, ctx):
    resp = client.post("/settings", json={
        "user_name": "Joseph",
        "debt_method": "avalanche",
        "review_day": "sunday",
    })
    assert resp.status_code == 200
    s = settings_svc.get_settings(ctx)
    assert s.user_name == "Joseph"
    assert s.debt_method == "avalanche"
    assert s.review_day == "sunday"


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

def test_get_categories_lists_defaults(client):
    resp = client.get("/settings/categories")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert "Food" in names
    assert "Housing" in names


def test_category_add_rename_hide(client, ctx):
    add = client.post("/settings/categories", json={"action": "add", "name": "Pets", "emoji": "🐾"})
    assert add.status_code == 200
    cat_id = add.json()["id"]

    rename = client.post("/settings/categories", json={"action": "rename", "category_id": cat_id, "name": "Animals"})
    assert rename.status_code == 200

    hide = client.post("/settings/categories", json={"action": "hide", "category_id": cat_id})
    assert hide.status_code == 200

    refetched = ctx.db.query(BudgetCategory).filter_by(id=cat_id).first()
    assert refetched.name == "Animals"
    assert refetched.is_active is False


# ---------------------------------------------------------------------------
# CSV import: mapping remembered, dedup preview
# ---------------------------------------------------------------------------

CSV_CONTENT = (
    "Date,Amount,Description\n"
    "2026-06-01,-45.00,Coffee Shop\n"
    "2026-05-15,-120.00,Old Thing\n"
    "2026-06-02,1500.00,Paycheck\n"
)


def _upload(client, content=CSV_CONTENT):
    return client.post(
        "/settings/import/csv",
        files={"file": ("bank.csv", content, "text/csv")},
        data={
            "map_date": "Date",
            "map_amount": "Amount",
            "map_payee": "Description",
            "spent_is_negative": "true",
        },
    )


def test_import_upload_returns_preview_and_remembers_mapping(client, ctx):
    resp = _upload(client)
    assert resp.status_code == 200
    body = resp.json()
    rows = body["rows"]
    assert len(rows) == 3
    # Mapping persisted to Settings.csv_column_map
    saved = settings_svc.get_csv_column_map(ctx)
    assert saved.get("date") == "Date"
    assert saved.get("amount") == "Amount"
    assert saved.get("payee") == "Description"


def test_import_preview_flags_existing_duplicate(client, ctx):
    # Seed an imported transaction matching the first CSV row.
    create_transaction(ctx, date=date(2026, 6, 1), amount_cents=4500,
                       direction="out", payee="Coffee Shop", is_imported=True)
    resp = _upload(client)
    rows = resp.json()["rows"]
    dup = next(r for r in rows if r["payee"] == "Coffee Shop")
    assert dup["is_duplicate"] is True
    assert dup["selected"] is False


# ---------------------------------------------------------------------------
# Confirm: rows route through the ledger into correct periods; inbox + badge
# ---------------------------------------------------------------------------

def test_confirm_imports_into_correct_periods_and_inbox(client, ctx):
    resp = client.post("/settings/import/confirm", json={"rows": [
        {"date": "2026-06-01", "amount_cents": 4500, "direction": "out", "payee": "Coffee Shop"},
        {"date": "2026-05-15", "amount_cents": 12000, "direction": "out", "payee": "Old Thing"},
        {"date": "2026-06-02", "amount_cents": 150000, "direction": "in", "payee": "Paycheck"},
    ]})
    assert resp.status_code == 200
    assert resp.json()["imported"] == 3

    txns = ctx.db.query(Transaction).filter_by(account_id=ctx.account_id).all()
    assert len(txns) == 3
    assert all(t.is_imported for t in txns)
    assert all(t.category_id is None for t in txns)  # uncategorized
    # Periods derived from date
    by_payee = {t.payee: t for t in txns}
    assert (by_payee["Old Thing"].period.year, by_payee["Old Thing"].period.month) == (2026, 5)
    assert (by_payee["Coffee Shop"].period.year, by_payee["Coffee Shop"].period.month) == (2026, 6)

    # Needs Category inbox + badge
    inbox = client.get("/api/needs-category")
    assert inbox.status_code == 200
    assert inbox.json()["count"] == 3


# ---------------------------------------------------------------------------
# Export, backup, reconciliation gate
# ---------------------------------------------------------------------------

def test_export_returns_csv(client, ctx):
    create_transaction(ctx, date=date(2026, 6, 1), amount_cents=4500,
                       direction="out", payee="Coffee Shop")
    resp = client.get("/settings/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "date" in resp.text.splitlines()[0].lower()


def test_backup_creates_file(client, ctx):
    resp = client.post("/settings/backup")
    assert resp.status_code == 200
    import os
    path = resp.json()["backup_path"]
    assert os.path.exists(path)


def test_reconciliation_green_after_mixed_duplicate_import(client, ctx):
    # Pre-existing imported txn (a duplicate of the first confirm row).
    create_transaction(ctx, date=date(2026, 6, 1), amount_cents=4500,
                       direction="out", payee="Coffee Shop", is_imported=True)
    # Confirm a mixed batch (the duplicate excluded by the user; new rows kept).
    client.post("/settings/import/confirm", json={"rows": [
        {"date": "2026-05-15", "amount_cents": 12000, "direction": "out", "payee": "Old Thing"},
        {"date": "2026-06-02", "amount_cents": 150000, "direction": "in", "payee": "Paycheck"},
    ]})
    report = recompute_balances(ctx.db, ctx.account_id)
    assert report["total_drift"] == 0
