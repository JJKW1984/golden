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
    return account


@pytest.fixture(scope="function")
def ctx(db, account):
    return AccountContext(account_id=account.id, db=db)
