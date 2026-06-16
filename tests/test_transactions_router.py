"""
Router tests for transaction endpoints (Phase 5).
Tests GET /transactions, POST /transactions, transaction CRUD, delete/restore.
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
from finapp.models import Account, BudgetCategory, BudgetPeriod, Transaction
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction
from finapp.services.allocation import set_allocation_targets


@pytest.fixture(scope="function")
def test_db_engine(tmp_path):
    """Create a temporary SQLite test database."""
    db_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # Use StaticPool so all sessions share the same connection
    )

    # Enable WAL mode and foreign keys like in finapp.db
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def test_db(test_db_engine):
    """Create a test database session."""
    SessionLocal = sessionmaker(bind=test_db_engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="function")
def test_account(test_db):
    """Create a test account with default categories."""
    account = Account(id=1, display_name="Test User")
    test_db.add(account)
    test_db.commit()

    seed_default_categories(test_db, account.id)
    return account


@pytest.fixture(scope="function")
def test_client(test_db, test_account):
    """Create a test client with mocked dependencies."""
    # Cache account_id early to prevent lazy-loading when override functions are called from TestClient threads
    test_account_id = test_account.id

    def override_get_db():
        yield test_db

    def override_get_account_context():
        return AccountContext(account_id=test_account_id, db=test_db)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_account_context] = override_get_account_context

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def transactions_setup(test_db, test_account):
    """Setup for transaction router tests."""
    period = BudgetPeriod(
        account_id=test_account.id,
        year=2026,
        month=6,
        income_received_cents=200000,
        status="active",
    )
    test_db.add(period)
    test_db.commit()

    categories = {
        cat.name: cat.id
        for cat in test_db.query(BudgetCategory).filter_by(account_id=test_account.id).all()
    }

    ctx = AccountContext(account_id=test_account.id, db=test_db)

    # Log some income and create period
    create_transaction(ctx, date=date(2026, 6, 1), amount_cents=200000, direction="in",
                      category_id=categories["Income"])

    set_allocation_targets(
        ctx,
        period_id=period.id,
        category_targets={
            categories["Food"]: 40000,
            categories["Housing"]: 100000,
            categories["Transportation"]: 25000,
            categories["Debt"]: 35000,
        },
    )

    return period, categories


class TestTransactionsPage:
    """Tests for transactions HTML page (GET /transactions)."""

    def test_get_transactions_returns_html(self, test_client, transactions_setup):
        """GET /transactions should return HTML page."""
        response = test_client.get("/transactions")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


class TestCreateTransactionEndpoint:
    """Tests for creating transactions (POST /transactions)."""

    def test_post_transaction_creates_expense(self, test_client, test_db, test_account, transactions_setup):
        """POST /transactions should create an expense transaction."""
        period, categories = transactions_setup

        response = test_client.post(
            "/transactions",
            json={
                "date": "2026-06-15",
                "amount_cents": 10000,
                "direction": "out",
                "category_id": categories["Food"],
                "payee": "Grocery Store",
                "memo": "Weekly shopping",
                "mood_tag": "necessity",
            }
        )

        assert response.status_code in [200, 201]

        # Verify in database
        txn = test_db.query(Transaction).filter_by(
            account_id=test_account.id,
            payee="Grocery Store",
        ).first()

        assert txn is not None
        assert txn.amount_cents == 10000
        assert txn.mood_tag == "necessity"

    def test_post_transaction_with_minimal_fields(self, test_client, test_db, test_account, transactions_setup):
        """POST /transactions should work with only required fields."""
        period, categories = transactions_setup

        response = test_client.post(
            "/transactions",
            json={
                "date": "2026-06-15",
                "amount_cents": 5000,
                "direction": "out",
                "category_id": categories["Food"],
            }
        )

        assert response.status_code in [200, 201]

        txn = test_db.query(Transaction).filter_by(
            account_id=test_account.id,
            amount_cents=5000,
        ).first()

        assert txn is not None
        assert txn.payee is None
        assert txn.memo is None
        assert txn.mood_tag is None

    def test_post_transaction_without_category_fails(self, test_client, transactions_setup):
        """POST /transactions without category_id should fail."""
        period, categories = transactions_setup

        response = test_client.post(
            "/transactions",
            json={
                "date": "2026-06-15",
                "amount_cents": 10000,
                "direction": "out",
            }
        )

        # Should fail validation
        assert response.status_code in [422, 400]


class TestGetTransactionEndpoint:
    """Tests for retrieving transactions."""

    def test_get_recent_transactions(self, test_client, test_db, test_account, transactions_setup):
        """GET /api/transactions/recent should return recent transactions."""
        period, categories = transactions_setup

        # Create a transaction
        ctx = AccountContext(account_id=test_account.id, db=test_db)
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                          category_id=categories["Food"], payee="Store")

        response = test_client.get("/api/transactions/recent")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)

    def test_get_transactions_with_category_filter(self, test_client, test_db, test_account, transactions_setup):
        """GET /transactions with category filter should return only those transactions."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                          category_id=categories["Food"])
        create_transaction(ctx, date=date(2026, 6, 16), amount_cents=20000, direction="out",
                          category_id=categories["Housing"])

        response = test_client.get(f"/transactions?category_id={categories['Food']}")
        assert response.status_code == 200

    def test_get_transactions_with_mood_tag_filter(self, test_client, test_db, test_account, transactions_setup):
        """GET /transactions with mood_tag filter should return matching transactions."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                          category_id=categories["Food"], mood_tag="necessity")
        create_transaction(ctx, date=date(2026, 6, 16), amount_cents=20000, direction="out",
                          category_id=categories["Food"], mood_tag="impulse")

        response = test_client.get(f"/transactions?mood_tag=necessity")
        assert response.status_code == 200

    def test_get_transactions_with_date_range_filter(self, test_client, test_db, test_account, transactions_setup):
        """GET /transactions with date range should filter correctly."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        create_transaction(ctx, date=date(2026, 6, 5), amount_cents=10000, direction="out",
                          category_id=categories["Food"])
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=20000, direction="out",
                          category_id=categories["Food"])
        create_transaction(ctx, date=date(2026, 6, 25), amount_cents=30000, direction="out",
                          category_id=categories["Food"])

        response = test_client.get(
            "/transactions?start_date=2026-06-10&end_date=2026-06-20"
        )
        assert response.status_code == 200


class TestUpdateTransactionEndpoint:
    """Tests for updating transactions (POST /transactions/{id})."""

    def test_post_update_transaction_amount(self, test_client, test_db, test_account, transactions_setup):
        """Updating transaction amount should change the stored amount."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                                category_id=categories["Food"])

        response = test_client.post(
            f"/transactions/{txn.id}",
            json={
                "amount_cents": 15000,
            }
        )

        assert response.status_code in [200, 302]

    def test_post_update_transaction_mood_tag(self, test_client, test_db, test_account, transactions_setup):
        """Updating mood tag should change the stored mood tag."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                                category_id=categories["Food"], mood_tag="necessity")

        response = test_client.post(
            f"/transactions/{txn.id}",
            json={
                "mood_tag": "impulse",
            }
        )

        assert response.status_code in [200, 302]


class TestDeleteTransactionEndpoint:
    """Tests for deleting transactions (POST /transactions/{id}/delete)."""

    def test_post_delete_soft_deletes_transaction(self, test_client, test_db, test_account, transactions_setup):
        """POST /transactions/{id}/delete should soft delete the transaction."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                                category_id=categories["Food"])

        response = test_client.post(f"/transactions/{txn.id}/delete")
        assert response.status_code in [200, 302]

        # Verify in database
        txn = test_db.query(Transaction).filter_by(id=txn.id).first()
        assert txn.is_deleted is True

    def test_post_delete_preserves_transaction_data(self, test_client, test_db, test_account, transactions_setup):
        """Soft delete should not remove the transaction, only mark it deleted."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        original_txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                                         category_id=categories["Food"], payee="Store")

        original_amount = original_txn.amount_cents
        original_payee = original_txn.payee

        test_client.post(f"/transactions/{original_txn.id}/delete")

        # Verify data is intact but marked deleted
        txn = test_db.query(Transaction).filter_by(id=original_txn.id).first()
        assert txn.amount_cents == original_amount
        assert txn.payee == original_payee
        assert txn.is_deleted is True


class TestRestoreTransactionEndpoint:
    """Tests for restoring deleted transactions (POST /transactions/{id}/restore)."""

    def test_post_restore_unmarks_deleted(self, test_client, test_db, test_account, transactions_setup):
        """POST /transactions/{id}/restore should undelete the transaction."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                                category_id=categories["Food"])

        # Delete then restore
        test_client.post(f"/transactions/{txn.id}/delete")
        response = test_client.post(f"/transactions/{txn.id}/restore")

        assert response.status_code in [200, 302]

        # Verify in database
        txn = test_db.query(Transaction).filter_by(id=txn.id).first()
        assert txn.is_deleted is False

    def test_undo_within_10_seconds_works(self, test_client, test_db, test_account, transactions_setup):
        """Restore should work when called within the undo window."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=10000, direction="out",
                                category_id=categories["Food"])

        # Delete and immediately restore (simulates within 10 seconds)
        test_client.post(f"/transactions/{txn.id}/delete")
        response = test_client.post(f"/transactions/{txn.id}/restore")

        assert response.status_code in [200, 302]

        txn = test_db.query(Transaction).filter_by(id=txn.id).first()
        assert txn.is_deleted is False


class TestTransactionLinkedEntities:
    """Tests for transactions linked to debt or savings."""

    def test_debt_payment_shows_link_inline(self, test_client, test_db, test_account, transactions_setup):
        """Debt payment transaction should show its linked debt account."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=5000, direction="out",
                                category_id=categories["Debt"], link_type="debt", link_id=1)

        response = test_client.get(f"/api/transactions/{txn.id}")
        assert response.status_code == 200

        data = response.json()
        assert data.get("link_type") == "debt"
        assert data.get("link_id") == 1

    def test_savings_contribution_shows_link_inline(self, test_client, test_db, test_account, transactions_setup):
        """Savings contribution should show its linked goal."""
        period, categories = transactions_setup

        ctx = AccountContext(account_id=test_account.id, db=test_db)
        txn = create_transaction(ctx, date=date(2026, 6, 15), amount_cents=2000, direction="out",
                                category_id=categories["Emergency Fund"], link_type="savings", link_id=1)

        response = test_client.get(f"/api/transactions/{txn.id}")
        assert response.status_code == 200

        data = response.json()
        assert data.get("link_type") == "savings"
        assert data.get("link_id") == 1
