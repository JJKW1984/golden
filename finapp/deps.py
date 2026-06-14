from dataclasses import dataclass
from fastapi import Depends
from sqlalchemy.orm import Session
from finapp.db import get_db


@dataclass
class AccountContext:
    account_id: int
    db: Session


def get_account_context(db: Session = Depends(get_db)) -> AccountContext:
    # v1 single-user seam — flip this one line for multi-user
    return AccountContext(account_id=1, db=db)
