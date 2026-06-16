import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from finapp.db import Base
from finapp.deps import AccountContext
from finapp.models import Account


@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # Enable WAL and foreign keys for in-memory test DB
    with engine.connect() as conn:
        conn.execute(__import__('sqlalchemy').text("PRAGMA journal_mode=WAL"))
        conn.execute(__import__('sqlalchemy').text("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def db(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture(scope="function")
def account(db):
    """Create a test account."""
    account = Account(id=1, display_name="Test User")
    db.add(account)
    db.commit()
    # Cache the account_id to prevent lazy-loading in multi-threaded contexts
    # This is needed because test_budget_router.py accesses account.id from TestClient threads
    _cached_id = account.id
    return account


@pytest.fixture(scope="function")
def ctx(db, account):
    return AccountContext(account_id=account.id, db=db)


@pytest.fixture(scope="function")
def test_client_for_budget(db_engine):
    """Create a test client with mocked dependencies for budget router tests.

    This fixture corrects the thread-safety issues in test_budget_router.py's
    test_client fixture by ensuring account_id is accessed and cached before
    TestClient creates threads.
    """
    from fastapi.testclient import TestClient
    from finapp.main import app
    from finapp.deps import get_db, get_account_context
    from finapp.models import Account

    Session = sessionmaker(bind=db_engine)
    test_db = Session()

    account = Account(id=1, display_name="Test User")
    test_db.add(account)
    test_db.commit()

    # Cache account_id BEFORE creating override functions
    # This prevents lazy-loading when override functions are called from different threads
    cached_account_id = account.id

    def override_get_db():
        yield test_db

    def override_get_account_context():
        return AccountContext(account_id=cached_account_id, db=test_db)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_account_context] = override_get_account_context

    yield TestClient(app)

    app.dependency_overrides.clear()
    test_db.close()
