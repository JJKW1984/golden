"""
Router tests for budget endpoints (Phase 5).
Tests GET /budget, POST /budget/income, GET/POST /budget/allocate, POST /budget/reallocate, etc.
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
from finapp.models import Account, BudgetCategory, BudgetPeriod
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
def budget_with_period(test_db, test_account):
    """Create a budget period with allocation targets."""
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

    set_allocation_targets(
        AccountContext(account_id=test_account.id, db=test_db),
        period_id=period.id,
        category_targets={
            categories["Housing"]: 80000,
            categories["Food"]: 40000,
            categories["Transportation"]: 25000,
            categories["Debt"]: 30000,
            categories["Emergency Fund"]: 10000,
            categories["Personal"]: 10000,
            categories["Everything Else"]: 5000,
        },
    )

    return period, categories


class TestBudgetEndpoints:
    """Tests for budget HTML endpoints."""

    def test_get_budget_returns_html(self, test_client, budget_with_period):
        """GET /budget should return HTML page."""
        period, _ = budget_with_period

        response = test_client.get("/budget")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_get_budget_history_by_period(self, test_client, budget_with_period):
        """GET /budget/{year}/{month} should return HTML for that period."""
        period, _ = budget_with_period

        response = test_client.get(f"/budget/2026/6")
        assert response.status_code == 200


class TestBudgetAPIEndpoints:
    """Tests for budget JSON API endpoints."""

    def test_get_budget_summary_returns_json(self, test_client, budget_with_period):
        """GET /api/budget/summary/{period_id} should return JSON with all category data."""
        period, _ = budget_with_period

        response = test_client.get(f"/api/budget/summary/{period.id}")
        assert response.status_code == 200

        data = response.json()
        assert "period_id" in data
        assert "categories" in data
        assert "total_target_cents" in data
        assert "total_spent_cents" in data
        assert "total_remaining_cents" in data
        assert "unallocated_cents" in data
        assert "is_zero_based" in data
        assert data["period_id"] == period.id

    def test_get_budget_summary_includes_all_categories(self, test_client, budget_with_period):
        """Budget summary should include all allocated categories."""
        period, _ = budget_with_period

        response = test_client.get(f"/api/budget/summary/{period.id}")
        data = response.json()

        # Should have 7 default categories
        assert len(data["categories"]) == 7

        # Each category should have required fields
        for cat in data["categories"]:
            assert "category_id" in cat
            assert "category_name" in cat
            assert "target_cents" in cat
            assert "funded_cents" in cat
            assert "spent_cents" in cat
            assert "remaining_cents" in cat
            assert "is_overspent" in cat

    def test_get_budget_summary_with_expenses(self, test_client, test_db, test_account, budget_with_period):
        """Budget summary should reflect logged expenses."""
        period, categories = budget_with_period

        # Log an income transaction first (to establish income_received_cents)
        income_cat = test_db.query(BudgetCategory).filter_by(
            account_id=test_account.id, kind="income"
        ).first()
        create_transaction(
            AccountContext(account_id=test_account.id, db=test_db),
            date=date(2026, 6, 1),
            amount_cents=200000,
            direction="in",
            category_id=income_cat.id,
            payee="Employer",
        )

        # Log an expense
        create_transaction(
            AccountContext(account_id=test_account.id, db=test_db),
            date=date(2026, 6, 15),
            amount_cents=10000,
            direction="out",
            category_id=categories["Food"],
            payee="Grocery",
        )
        # Commit so the TestClient's session can see the transaction
        test_db.commit()

        response = test_client.get(f"/api/budget/summary/{period.id}")
        data = response.json()

        # Find Food in the response
        food_cat = next((cat for cat in data["categories"] if cat["category_name"] == "Food"), None)
        assert food_cat is not None
        assert food_cat["spent_cents"] == 10000
        assert food_cat["remaining_cents"] == 30000  # 40000 - 10000

    def test_get_budget_summary_nonexistent_period_returns_404(self, test_client):
        """GET /api/budget/summary/{bad_id} should return 404."""
        response = test_client.get("/api/budget/summary/99999")
        assert response.status_code == 404


class TestBudgetIncomeEndpoint:
    """Tests for logging income (POST /budget/income)."""

    def test_post_income_logs_transaction(self, test_client, test_db, test_account):
        """POST /budget/income should create an income transaction."""
        # Note: This endpoint should be POST /api/allocation/income based on Phase 3
        # But we're testing the budget flow integration

        period = BudgetPeriod(
            account_id=test_account.id,
            year=2026,
            month=6,
            income_received_cents=0,
            status="active",
        )
        test_db.add(period)
        test_db.commit()

        income_cat = test_db.query(BudgetCategory).filter_by(
            account_id=test_account.id, kind="income"
        ).first()

        response = test_client.post(
            "/api/allocation/income",
            json={
                "date": "2026-06-01",
                "amount_cents": 200000,
                "payee": "Employer",
                "memo": "Biweekly",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "amount_cents" in data
        assert data["amount_cents"] == 200000


class TestBudgetAllocationEndpoint:
    """Tests for allocation ritual (GET/POST /budget/allocate)."""

    def test_post_allocation_sets_targets(self, test_client, test_db, test_account):
        """POST /budget/allocate should set category targets."""
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

        response = test_client.post(
            "/api/allocation/targets",
            json={
                "period_id": period.id,
                "allocations": [
                    {"category_id": categories["Housing"], "target_cents": 100000},
                    {"category_id": categories["Food"], "target_cents": 40000},
                ],
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "allocations" in data
        assert "unallocated_cents" in data
        assert "zero_based_block" in data


class TestBudgetReallocateEndpoint:
    """Tests for reallocation (POST /budget/reallocate)."""

    def test_post_reallocate_moves_funds(self, test_client, test_db, test_account, budget_with_period):
        """POST /budget/reallocate should move funds between categories."""
        period, categories = budget_with_period

        response = test_client.post(
            "/budget/reallocate",
            json={
                "period_id": period.id,
                "from_category_id": categories["Transportation"],
                "to_category_id": categories["Food"],
                "amount_cents": 5000,
            }
        )

        # Status code depends on implementation
        # Should return 200 or redirect
        assert response.status_code in [200, 302]


class TestBudgetPulseEndpoint:
    """Tests for weekly pulse (GET /api/budget/pulse)."""

    def test_get_pulse_returns_weekly_spending(self, test_client, test_db, test_account, budget_with_period):
        """GET /api/budget/pulse should return this week's spending vs target."""
        period, categories = budget_with_period

        # Log some expenses this week
        ctx = AccountContext(account_id=test_account.id, db=test_db)
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=15000, direction="out",
                          category_id=categories["Food"])

        response = test_client.get("/api/budget/pulse")
        assert response.status_code == 200

        data = response.json()
        assert "spent_cents" in data
        assert "target_cents" in data
        assert "percentage" in data
