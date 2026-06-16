import pytest
from datetime import date
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from finapp.db import Base
from finapp.models import Account, SavingsGoal, AssetAccount
from finapp.deps import AccountContext


@pytest.fixture
def db_engine(tmp_path):
    """Create a temporary SQLite test database (file-backed, StaticPool) for HTTP tests.

    A plain ':memory:' engine without StaticPool gives each thread its own
    connection, so the TestClient's request thread can't see tables created
    by the test thread. StaticPool (mirroring test_debt_router.py's pattern)
    ensures all sessions share a single connection.
    """
    db_file = tmp_path / "test_savings_router.db"
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


@pytest.fixture
def test_client(db_engine):
    """TestClient with overridden db/account dependencies (mirrors test_client_for_budget pattern)."""
    from fastapi.testclient import TestClient
    from finapp.main import app
    from finapp.deps import get_db, get_account_context

    Session = sessionmaker(bind=db_engine)
    test_db = Session()

    account = Account(id=1, display_name="Test User")
    test_db.add(account)
    test_db.commit()
    cached_account_id = account.id

    def override_get_db():
        yield test_db

    def override_get_account_context():
        return AccountContext(account_id=cached_account_id, db=test_db)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_account_context] = override_get_account_context

    yield TestClient(app), test_db, cached_account_id

    app.dependency_overrides.clear()
    test_db.close()


@pytest.fixture
def savings_goal(test_client):
    client, db, account_id = test_client
    goal = SavingsGoal(
        account_id=account_id,
        name="Emergency Fund",
        goal_type="emergency_fund",
        target_cents=300000,
        opening_balance_cents=100000,
        cached_balance_cents=100000,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def test_post_savings_contribution(test_client, savings_goal):
    client, db, account_id = test_client

    response = client.post("/savings/contribution", json={
        "goal_id": savings_goal.id,
        "amount_cents": 5000,
        "memo": "Paycheck sweep",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["new_balance_cents"] == 105000

    db.refresh(savings_goal)
    assert savings_goal.cached_balance_cents == 105000


def test_post_savings_withdrawal(test_client, savings_goal):
    client, db, account_id = test_client

    response = client.post("/savings/withdrawal", json={
        "goal_id": savings_goal.id,
        "amount_cents": 20000,
        "memo": "Car repair",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["new_balance_cents"] == 80000

    db.refresh(savings_goal)
    assert savings_goal.cached_balance_cents == 80000


def test_post_savings_contribution_unknown_goal_404s(test_client):
    client, db, account_id = test_client

    response = client.post("/savings/contribution", json={
        "goal_id": 9999,
        "amount_cents": 5000,
    })

    assert response.status_code == 404


def test_get_emergency_fund_api(test_client, savings_goal):
    client, db, account_id = test_client

    response = client.get("/api/savings/emergency")

    assert response.status_code == 200
    data = response.json()
    assert data["goal_id"] == savings_goal.id
    assert data["balance_cents"] == 100000
    assert data["target_cents"] == 300000
    assert data["percent_complete"] == 33  # 100000/300000 rounded down


def test_get_emergency_fund_api_no_goal_404s(test_client):
    client, db, account_id = test_client

    response = client.get("/api/savings/emergency")

    assert response.status_code == 404


def test_get_savings_screen(test_client, savings_goal):
    client, db, account_id = test_client

    other_goal = SavingsGoal(
        account_id=account_id, name="Vacation", goal_type="sinking_fund",
        target_cents=50000, opening_balance_cents=10000, cached_balance_cents=10000,
    )
    db.add(other_goal)
    db.commit()

    response = client.get("/savings")

    assert response.status_code == 200
    assert "Emergency Fund" in response.text
    assert "Vacation" in response.text


def test_post_assets_creates_new_asset(test_client):
    client, db, account_id = test_client

    response = client.post("/assets", json={"name": "Checking", "balance_cents": 250000})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Checking"
    assert data["balance_cents"] == 250000

    asset = db.query(AssetAccount).filter_by(account_id=account_id, name="Checking").first()
    assert asset is not None
    assert asset.balance_cents == 250000


def test_post_assets_updates_existing_asset(test_client):
    client, db, account_id = test_client

    asset = AssetAccount(account_id=account_id, name="Cash", balance_cents=10000)
    db.add(asset)
    db.commit()
    db.refresh(asset)

    response = client.post("/assets", json={"id": asset.id, "name": "Cash", "balance_cents": 15000})

    assert response.status_code == 200
    data = response.json()
    assert data["balance_cents"] == 15000

    db.refresh(asset)
    assert asset.balance_cents == 15000


def test_get_assets_screen(test_client):
    client, db, account_id = test_client

    db.add(AssetAccount(account_id=account_id, name="Checking", balance_cents=250000))
    db.commit()

    response = client.get("/assets")

    assert response.status_code == 200
    assert "Checking" in response.text


def test_get_networth(test_client, savings_goal):
    client, db, account_id = test_client

    db.add(AssetAccount(account_id=account_id, name="Checking", balance_cents=500000))
    db.commit()

    response = client.get("/api/networth")

    assert response.status_code == 200
    data = response.json()
    assert data["total_assets_cents"] == 500000
    assert data["total_debt_cents"] == 0
    assert data["net_worth_cents"] == 500000
    assert data["trend"] == []
