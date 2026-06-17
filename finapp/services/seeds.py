from datetime import datetime, UTC
from sqlalchemy.orm import Session
from finapp.models import Account, BudgetCategory


def seed_default_categories(db: Session, account_id: int):
    """
    Seed the 8 default categories for an account.
    These are the standard categories that users start with.
    """
    default_categories = [
        {
            "name": "Income",
            "kind": "income",
            "is_system": True,
            "sort_order": 1,
            "emoji": "💰",
        },
        {
            "name": "Housing",
            "kind": "spending",
            "is_system": True,
            "sort_order": 2,
            "emoji": "🏠",
        },
        {
            "name": "Food",
            "kind": "spending",
            "is_system": False,
            "sort_order": 3,
            "emoji": "🍽️",
        },
        {
            "name": "Transportation",
            "kind": "spending",
            "is_system": False,
            "sort_order": 4,
            "emoji": "🚗",
        },
        {
            "name": "Debt",
            "kind": "debt",
            "is_system": True,
            "sort_order": 5,
            "emoji": "💳",
        },
        {
            "name": "Emergency Fund",
            "kind": "savings",
            "is_system": True,
            "sort_order": 6,
            "emoji": "🛡️",
        },
        {
            "name": "Personal",
            "kind": "spending",
            "is_system": False,
            "sort_order": 7,
            "emoji": "👤",
        },
        {
            "name": "Everything Else",
            "kind": "spending",
            "is_system": False,
            "sort_order": 8,
            "emoji": "🎯",
        },
    ]

    for cat_data in default_categories:
        category = BudgetCategory(
            account_id=account_id,
            name=cat_data["name"],
            kind=cat_data["kind"],
            is_system=cat_data["is_system"],
            sort_order=cat_data["sort_order"],
            emoji=cat_data.get("emoji"),
            is_active=True,
            created_at=datetime.now(UTC),
        )
        db.add(category)

    db.commit()
