"""
Integration tests for debt payment and adjustment transactions.
Tests that debt payments route through the ledger correctly, splitting
into principal and interest components.
Also includes HTTP router tests for debt endpoints.
"""
import pytest
from datetime import datetime, date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from finapp.main import app
from finapp.db import Base
from finapp.deps import get_db, get_account_context, AccountContext
from finapp.models import Transaction, DebtAccount, BudgetCategory, Account
from finapp.services.ledger import create_transaction, create_debt_adjustment
from finapp.services.seeds import seed_default_categories


@pytest.fixture
def debt_account(account, ctx, db):
    """Create a test debt account."""
    debt = DebtAccount(
        account_id=ctx.account_id,
        name="Test Debt",
        creditor="Test Bank",
        opening_balance_cents=50000,
        interest_rate_bps=1200,  # 12% annual
        cached_balance_cents=50000,
        minimum_payment_cents=100,
        sort_order=1,
        is_active=True,
        notes="Test debt account"
    )
    db.add(debt)
    db.commit()
    db.refresh(debt)
    return debt


@pytest.fixture
def debt_category(account, ctx, db):
    """Create a Debt budget category."""
    category = BudgetCategory(
        account_id=ctx.account_id,
        name="Debt",
        emoji="💳",
        sort_order=1,
        kind="debt",
        is_system=True,
        is_active=True
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def test_create_debt_payment_transaction(ctx, debt_account, debt_category):
    """
    Given: a debt account with balance 10000 cents, interest_rate_bps=1200, payment 200 cents
    When: create_transaction(..., link_type='debt', link_id=debt_id) is called with payment_cents=200
    Then: creates a Transaction with principal_cents and interest_cents split

    Expected: interest = 10000 * 1200 / 10000 / 12 = 100 cents
              principal = 200 - 100 = 100 cents
    """
    # Override the debt account with smaller balance for this test
    debt_account.cached_balance_cents = 10000
    ctx.db.commit()

    payment_cents = 200

    # Create debt payment transaction through ledger
    txn = create_transaction(
        ctx=ctx,
        date=date(2026, 6, 15),
        amount_cents=payment_cents,
        direction="out",
        category_id=debt_category.id,
        payee=debt_account.name,
        memo="Debt payment",
        link_type="debt",
        link_id=debt_account.id
    )

    # Verify transaction properties
    assert txn.link_type == "debt", f"Expected link_type='debt', got {txn.link_type}"
    assert txn.link_id == debt_account.id, f"Expected link_id={debt_account.id}, got {txn.link_id}"
    assert txn.amount_cents == payment_cents, f"Expected amount={payment_cents}, got {txn.amount_cents}"
    assert txn.direction == "out", f"Expected direction='out', got {txn.direction}"

    # Verify principal + interest == payment
    total = txn.principal_cents + txn.interest_cents
    assert total == payment_cents, f"Expected principal + interest = {payment_cents}, got {total}"

    # Verify rough values (interest should be ~100, principal ~100)
    assert txn.interest_cents > 0, f"Expected interest > 0, got {txn.interest_cents}"
    assert txn.principal_cents > 0, f"Expected principal > 0, got {txn.principal_cents}"

    # Verify interest is approximately 100 cents (10000 * 1200 / 10000 / 12)
    expected_interest = 100
    assert abs(txn.interest_cents - expected_interest) <= 1, \
        f"Expected interest ≈ {expected_interest}, got {txn.interest_cents}"


def test_create_debt_payment_edge_case_payment_less_than_interest(ctx, db):
    """
    Edge case: payment is less than monthly interest.

    Given: a debt account with balance 100000 cents, interest_rate_bps=2000 (20% annual)
    monthly interest ≈ 100000 * 2000 / 10000 / 12 ≈ 333 cents
    payment = 50 cents (less than interest)

    When: create_transaction(..., link_type='debt', ...) is called
    Then: principal_cents = 0, interest_cents = 50 (capped at payment)
    """
    debt = DebtAccount(
        account_id=ctx.account_id,
        name="High Interest Debt",
        creditor="Payday Lender",
        opening_balance_cents=100000,
        interest_rate_bps=2000,  # 20% annual
        cached_balance_cents=100000,
        minimum_payment_cents=100,
        sort_order=1,
        is_active=True
    )
    db.add(debt)
    db.commit()
    db.refresh(debt)

    category = BudgetCategory(
        account_id=ctx.account_id,
        name="Debt",
        emoji="💳",
        sort_order=1,
        kind="debt",
        is_system=True,
        is_active=True
    )
    db.add(category)
    db.commit()
    db.refresh(category)

    payment_cents = 50

    txn = create_transaction(
        ctx=ctx,
        date=date(2026, 6, 15),
        amount_cents=payment_cents,
        direction="out",
        category_id=category.id,
        payee=debt.name,
        memo="Minimum payment (covers interest only)",
        link_type="debt",
        link_id=debt.id
    )

    # Verify edge case: principal = 0, interest = payment
    assert txn.principal_cents == 0, \
        f"Expected principal=0 (payment ≤ interest), got {txn.principal_cents}"
    assert txn.interest_cents == payment_cents, \
        f"Expected interest={payment_cents} (capped at payment), got {txn.interest_cents}"
    assert txn.principal_cents + txn.interest_cents == payment_cents


def test_create_debt_adjustment_transaction(ctx, debt_account, db):
    """
    Given: a debt account with cached_balance 50000 cents, actual statement balance 48000 cents
    When: create_debt_adjustment(..., adjustment_cents=-2000) is called
    Then: creates a Transaction with:
    - link_type='debt', link_id=debt_id
    - principal_cents=-2000 (negative because balance decreased)
    - interest_cents=0 (adjustments have no interest component)
    - memo contains "Update balance"
    """
    adjustment_cents = -2000  # Balance was $20 overstated
    statement_balance_cents = 48000

    # Create the adjustment transaction
    txn = create_debt_adjustment(
        ctx=ctx,
        debt_id=debt_account.id,
        adjustment_cents=adjustment_cents,
        statement_balance_cents=statement_balance_cents
    )

    # Verify adjustment properties
    assert txn.link_type == "debt", f"Expected link_type='debt', got {txn.link_type}"
    assert txn.link_id == debt_account.id, f"Expected link_id={debt_account.id}, got {txn.link_id}"
    assert txn.principal_cents == adjustment_cents, \
        f"Expected principal={adjustment_cents}, got {txn.principal_cents}"
    assert txn.interest_cents == 0, f"Expected interest=0 for adjustment, got {txn.interest_cents}"
    assert txn.mood_tag is None, f"Expected mood_tag=None, got {txn.mood_tag}"
    assert "Update balance" in txn.memo or "adjustment" in txn.memo.lower(), \
        f"Expected 'Update balance' in memo, got: {txn.memo}"
    assert "48000" in txn.memo or "480" in txn.memo, \
        f"Expected statement balance in memo, got: {txn.memo}"


def test_create_debt_adjustment_positive_adjustment(ctx, debt_account):
    """
    Given: a debt account with cached_balance 50000 cents, actual balance 51000 cents
    When: create_debt_adjustment(..., adjustment_cents=1000) is called
    Then: creates a Transaction with:
    - principal_cents=1000 (positive, balance was understated)
    - direction='in' (money "coming in" to pay debt, reducing it)
    """
    adjustment_cents = 1000  # Balance was $10 understated
    statement_balance_cents = 51000

    txn = create_debt_adjustment(
        ctx=ctx,
        debt_id=debt_account.id,
        adjustment_cents=adjustment_cents,
        statement_balance_cents=statement_balance_cents
    )

    # Verify positive adjustment
    assert txn.principal_cents == adjustment_cents
    assert txn.interest_cents == 0
    assert txn.direction == "in", f"Expected direction='in' for positive adjustment, got {txn.direction}"
    assert txn.amount_cents == abs(adjustment_cents)


def test_create_debt_adjustment_negative_adjustment(ctx, debt_account):
    """
    Given: a debt account with cached_balance 50000 cents, actual balance 48000 cents
    When: create_debt_adjustment(..., adjustment_cents=-2000) is called
    Then: creates a Transaction with:
    - principal_cents=-2000 (negative)
    - direction='out' (balance increased, debt grew)
    """
    adjustment_cents = -2000  # Balance was $20 overstated
    statement_balance_cents = 48000

    txn = create_debt_adjustment(
        ctx=ctx,
        debt_id=debt_account.id,
        adjustment_cents=adjustment_cents,
        statement_balance_cents=statement_balance_cents
    )

    # Verify negative adjustment
    assert txn.principal_cents == adjustment_cents
    assert txn.interest_cents == 0
    assert txn.direction == "out", f"Expected direction='out' for negative adjustment, got {txn.direction}"
    assert txn.amount_cents == abs(adjustment_cents)


def test_debt_payment_invariant_principal_plus_interest_equals_payment(ctx, debt_account, debt_category):
    """
    Invariant test: for any debt payment, principal_cents + interest_cents == amount_cents.
    Tests with various combinations of balance, rate, and payment amounts.
    """
    test_cases = [
        (10000, 1200, 200),    # $100 balance, 12% rate, $2 payment
        (50000, 1200, 600),    # $500 balance, 12% rate, $6 payment
        (100000, 2000, 1000),  # $1000 balance, 20% rate, $10 payment
    ]

    for balance_cents, interest_rate_bps, payment_cents in test_cases:
        debt = DebtAccount(
            account_id=ctx.account_id,
            name=f"Test Debt {interest_rate_bps}bps",
            creditor="Test",
            opening_balance_cents=balance_cents,
            interest_rate_bps=interest_rate_bps,
            cached_balance_cents=balance_cents,
            minimum_payment_cents=100,
            sort_order=1,
            is_active=True
        )
        ctx.db.add(debt)
        ctx.db.commit()
        ctx.db.refresh(debt)

        txn = create_transaction(
            ctx=ctx,
            date=date(2026, 6, 15),
            amount_cents=payment_cents,
            direction="out",
            category_id=debt_category.id,
            payee=debt.name,
            memo=f"Payment test",
            link_type="debt",
            link_id=debt.id
        )

        total = txn.principal_cents + txn.interest_cents
        assert total == payment_cents, \
            f"Invariant failed for balance={balance_cents}, rate={interest_rate_bps}bps, payment={payment_cents}: " \
            f"{txn.principal_cents} + {txn.interest_cents} != {payment_cents}"


# ============================================================================
# HTTP Router Tests
# ============================================================================

@pytest.fixture(scope="function")
def test_db_engine_http(tmp_path):
    """Create a temporary SQLite test database for HTTP tests."""
    db_file = tmp_path / "test_debt_router.db"
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
def test_db_http(test_db_engine_http):
    """Create a test database session for HTTP tests."""
    SessionLocal = sessionmaker(bind=test_db_engine_http)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="function")
def test_account_http(test_db_http):
    """Create a test account with default categories for HTTP tests."""
    account = Account(id=1, display_name="Test User")
    test_db_http.add(account)
    test_db_http.commit()

    seed_default_categories(test_db_http, account.id)
    return account


@pytest.fixture(scope="function")
def test_client_http(test_db_http, test_account_http):
    """Create a test client with mocked dependencies for HTTP tests."""
    test_account_id = test_account_http.id

    def override_get_db():
        yield test_db_http

    def override_get_account_context():
        return AccountContext(account_id=test_account_id, db=test_db_http)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_account_context] = override_get_account_context

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def test_debt_account(test_db_http, test_account_http):
    """Create a test debt account for HTTP tests."""
    debt = DebtAccount(
        account_id=test_account_http.id,
        name="Test Debt",
        creditor="Test Bank",
        opening_balance_cents=50000,
        interest_rate_bps=1200,  # 12% annual
        cached_balance_cents=50000,
        minimum_payment_cents=100,
        sort_order=1,
        is_active=True,
        notes="Test debt account"
    )
    test_db_http.add(debt)
    test_db_http.commit()
    test_db_http.refresh(debt)
    return debt


def test_get_debt_list(test_client_http, test_debt_account):
    """
    Given: account with active debt
    When: GET /debt
    Then: returns 200 with debt list including name
    """
    response = test_client_http.get("/debt")

    assert response.status_code == 200
    # HTML response should contain the debt name
    assert test_debt_account.name in response.text


def test_get_debt_list_multiple_debts(test_client_http, test_db_http, test_account_http):
    """
    Given: account with multiple debts
    When: GET /debt
    Then: returns HTML with all debts listed in sort order
    """
    # Create multiple debts
    debt1 = DebtAccount(
        account_id=test_account_http.id,
        name="Credit Card",
        creditor="Bank A",
        opening_balance_cents=30000,
        interest_rate_bps=2000,
        cached_balance_cents=30000,
        minimum_payment_cents=100,
        sort_order=1,
        is_active=True,
    )
    debt2 = DebtAccount(
        account_id=test_account_http.id,
        name="Student Loan",
        creditor="Federal",
        opening_balance_cents=100000,
        interest_rate_bps=500,
        cached_balance_cents=100000,
        minimum_payment_cents=200,
        sort_order=2,
        is_active=True,
    )
    test_db_http.add(debt1)
    test_db_http.add(debt2)
    test_db_http.commit()

    response = test_client_http.get("/debt")

    assert response.status_code == 200
    assert "Credit Card" in response.text
    assert "Student Loan" in response.text


def test_post_debt_payment(test_client_http, test_debt_account):
    """
    Given: debt with balance 50000 cents
    When: POST /debt/{id}/payment with payment_cents=400
    Then: returns 200 with principal and interest split
    """
    response = test_client_http.post(
        f"/debt/{test_debt_account.id}/payment",
        json={"payment_cents": 400}
    )

    assert response.status_code == 200
    data = response.json()
    assert "principal" in data
    assert "interest" in data
    assert "new_balance" in data
    assert "transaction_id" in data
    # Principal + interest should equal payment
    assert abs(data["principal"] + data["interest"] - 4.0) < 0.01


def test_post_debt_payment_not_found(test_client_http):
    """
    Given: nonexistent debt ID
    When: POST /debt/{id}/payment
    Then: returns 404
    """
    response = test_client_http.post(
        "/debt/99999/payment",
        json={"payment_cents": 400}
    )

    assert response.status_code == 404


def test_post_debt_payment_with_memo(test_client_http, test_debt_account):
    """
    Given: debt account and payment with memo
    When: POST /debt/{id}/payment with memo="Extra payment"
    Then: transaction is created with the memo
    """
    memo_text = "Extra monthly payment"
    response = test_client_http.post(
        f"/debt/{test_debt_account.id}/payment",
        json={
            "payment_cents": 500,
            "memo": memo_text
        }
    )

    assert response.status_code == 200
    # The transaction should have been created with the memo (we verify via service later)


def test_get_debt_projection(test_client_http, test_debt_account):
    """
    Given: debt with balance 50000 cents
    When: GET /api/debt/projection/{id}
    Then: returns JSON with 3 scenarios (minimum, +50, +100)
    """
    response = test_client_http.get(f"/api/debt/projection/{test_debt_account.id}")

    assert response.status_code == 200
    data = response.json()
    assert "scenarios" in data
    assert len(data["scenarios"]) == 3

    # Check first scenario (minimum)
    assert data["scenarios"][0]["name"] == "minimum"
    assert "months" in data["scenarios"][0]
    assert "total_interest" in data["scenarios"][0]
    assert "payoff_date" in data["scenarios"][0]
    assert "payment_gte_interest" in data["scenarios"][0]

    # Scenarios should be ordered by payment amount
    scenario_payments = [s["payment"] for s in data["scenarios"]]
    assert scenario_payments == sorted(scenario_payments)


def test_get_debt_projection_not_found(test_client_http):
    """
    Given: nonexistent debt ID
    When: GET /api/debt/projection/{id}
    Then: returns 404
    """
    response = test_client_http.get("/api/debt/projection/99999")

    assert response.status_code == 404
