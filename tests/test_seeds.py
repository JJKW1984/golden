import pytest
from finapp.models import BudgetCategory
from finapp.services.seeds import seed_default_categories


def test_seed_default_categories(db, account):
    """Test that seed_default_categories creates 8 categories with correct kinds."""
    seed_default_categories(db, account.id)

    categories = db.query(BudgetCategory).filter_by(account_id=account.id).all()

    # Verify exactly 8 categories
    assert len(categories) == 8, f"Expected 8 categories, got {len(categories)}"

    # Verify the categories exist with correct kinds
    expected_categories = {
        "Income": "income",
        "Housing": "spending",
        "Food": "spending",
        "Transportation": "spending",
        "Debt": "debt",
        "Emergency Fund": "savings",
        "Personal": "spending",
        "Everything Else": "spending",
    }

    category_map = {cat.name: cat.kind for cat in categories}
    assert category_map == expected_categories, f"Categories mismatch: {category_map}"

    # Verify system categories
    system_categories = {"Income", "Housing", "Debt", "Emergency Fund"}
    for cat in categories:
        if cat.name in system_categories:
            assert cat.is_system, f"{cat.name} should be marked as system"
        else:
            assert not cat.is_system, f"{cat.name} should not be marked as system"


def test_seed_default_categories_sort_order(db, account):
    """Test that seed_default_categories respects sort_order."""
    seed_default_categories(db, account.id)

    categories = db.query(BudgetCategory).filter_by(account_id=account.id).order_by(BudgetCategory.sort_order).all()

    # Verify sort order is sequential 1-8
    for i, cat in enumerate(categories, 1):
        assert cat.sort_order == i, f"Expected sort_order {i}, got {cat.sort_order}"


def test_seed_default_categories_all_active(db, account):
    """Test that all seeded categories are active by default."""
    seed_default_categories(db, account.id)

    categories = db.query(BudgetCategory).filter_by(account_id=account.id).all()

    for cat in categories:
        assert cat.is_active, f"{cat.name} should be active by default"
