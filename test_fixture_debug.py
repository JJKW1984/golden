"""Debug script to reproduce the fixture issue."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from finapp.db import Base
from finapp.models import Account
from finapp.deps import get_account_context, AccountContext, get_db
from finapp.main import app
from fastapi.testclient import TestClient

# Set up test fixtures like the test does
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(engine)

SessionLocal = sessionmaker(bind=engine)
test_db = SessionLocal()

account = Account(id=1, display_name="Test User")
test_db.add(account)
test_db.commit()

# Now set up the overrides
def override_get_db():
    yield test_db

def override_get_account_context():
    print(f"override_get_account_context called")
    print(f"test_account object: {account}")
    print(f"test_account __dict__: {account.__dict__}")
    try:
        account_id = account.id
        print(f"Successfully accessed account.id: {account_id}")
    except Exception as e:
        print(f"Error accessing account.id: {e}")
        raise
    return AccountContext(account_id=account_id, db=test_db)

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_account_context] = override_get_account_context

# Create test client
test_client = TestClient(app)

# Make a request
print("Making request to /budget...")
response = test_client.get("/budget")
print(f"Response status: {response.status_code}")

app.dependency_overrides.clear()
test_db.close()
