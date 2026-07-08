import socket
import threading
import time
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from finapp.db import Base, get_db
from finapp.deps import AccountContext, get_account_context
from finapp.main import app
from finapp.models import Account, BudgetCategory, Settings


@dataclass
class LiveServer:
    url: str
    db: Session


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="function")
def live_server():
    # Fresh in-memory DB shared across threads (server thread + test thread)
    # via StaticPool, mirroring tests/conftest.py::db_engine.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)

    # Seed the bare account (id=1) the single-user seam hardcodes.
    seed_db = SessionLocal()
    seed_db.add(Account(id=1, display_name="Test User"))
    seed_db.commit()

    # Server thread gets a fresh session per request (Sessions are not
    # thread-safe); all sessions share one connection via StaticPool, so
    # data committed on the test thread is visible to the server thread.
    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    def override_get_account_context():
        s = SessionLocal()
        try:
            yield AccountContext(account_id=1, db=s)
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_account_context] = override_get_account_context

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("E2E server did not become ready within 10s")

    try:
        yield LiveServer(url=base_url, db=seed_db)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        app.dependency_overrides.clear()
        seed_db.close()
        engine.dispose()


def seed_account(db, *, setup_complete=True, user_name="Seeded Sam", with_categories=False):
    """Direct-DB seeding for flow tests that need pre-existing state without
    walking onboarding in the browser. Operates on account_id=1 (the seam's
    hardcoded single user). Commits before returning."""
    db.add(
        Settings(
            account_id=1,
            setup_complete=setup_complete,
            user_name=user_name,
            currency_symbol="$",
        )
    )
    if with_categories:
        db.add_all(
            [
                BudgetCategory(account_id=1, name="Groceries", kind="spending",
                               sort_order=1, is_active=True),
                BudgetCategory(account_id=1, name="Transport", kind="spending",
                               sort_order=2, is_active=True),
            ]
        )
    db.commit()
