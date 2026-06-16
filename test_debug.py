"""Debug script to understand the fixture issue."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from finapp.db import Base
from finapp.models import Account
from finapp.deps import AccountContext

# Create engine and session
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

# Create account
account = Account(id=1, display_name="Test User")
session.add(account)
session.commit()

print(f"Account created: {account}")
print(f"Account ID: {account.id}")

# Now create AccountContext like the fixture does
ctx = AccountContext(account_id=account.id, db=session)
print(f"AccountContext created: {ctx}")
print(f"AccountContext account_id: {ctx.account_id}")

# Now try to use session in a different thread (simulating TestClient)
import threading

def use_session():
    try:
        result = session.query(Account).filter_by(id=1).first()
        print(f"Query in thread succeeded: {result}")
    except Exception as e:
        print(f"Query in thread failed: {e}")

thread = threading.Thread(target=use_session)
thread.start()
thread.join()

# Now try accessing test_account.id in the thread context
def access_account_id():
    try:
        print(f"Accessing account.id in thread: {account.id}")
    except Exception as e:
        print(f"Accessing account.id in thread failed: {e}")

thread = threading.Thread(target=access_account_id)
thread.start()
thread.join()

print("All done!")
