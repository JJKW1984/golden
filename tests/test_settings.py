"""
Phase 10 — Settings service tests.

The settings service owns: get/update the single Settings row, the remembered
CSV column map (JSON in Settings.csv_column_map), and category management
(add / rename / hide / reorder). System categories are locked (cannot be
renamed or hidden).
"""
import pytest
from finapp.deps import AccountContext
from finapp.models import Settings, BudgetCategory
from finapp.services.seeds import seed_default_categories
from finapp.services import settings as settings_svc


@pytest.fixture
def settings_ctx(db, account):
    seed_default_categories(db, account.id)
    return AccountContext(account_id=account.id, db=db)


def test_get_settings_creates_row_if_missing(settings_ctx):
    s = settings_svc.get_settings(settings_ctx)
    assert isinstance(s, Settings)
    assert s.account_id == settings_ctx.account_id
    # Idempotent: a second call returns the same row, not a duplicate.
    s2 = settings_svc.get_settings(settings_ctx)
    assert s2.id == s.id
    count = settings_ctx.db.query(Settings).filter_by(account_id=settings_ctx.account_id).count()
    assert count == 1


def test_update_settings_changes_fields(settings_ctx):
    settings_svc.update_settings(
        settings_ctx,
        user_name="Joseph",
        debt_method="avalanche",
        review_day="sunday",
        currency_symbol="$",
    )
    s = settings_svc.get_settings(settings_ctx)
    assert s.user_name == "Joseph"
    assert s.debt_method == "avalanche"
    assert s.review_day == "sunday"


def test_update_settings_ignores_none(settings_ctx):
    settings_svc.update_settings(settings_ctx, user_name="Joseph")
    settings_svc.update_settings(settings_ctx, debt_method="snowball")  # user_name not passed
    s = settings_svc.get_settings(settings_ctx)
    assert s.user_name == "Joseph"  # preserved
    assert s.debt_method == "snowball"


def test_csv_column_map_round_trips(settings_ctx):
    mapping = {"date": "Date", "amount": "Amount", "payee": "Description"}
    settings_svc.save_csv_column_map(settings_ctx, mapping)
    loaded = settings_svc.get_csv_column_map(settings_ctx)
    assert loaded == mapping


def test_csv_column_map_defaults_to_empty(settings_ctx):
    assert settings_svc.get_csv_column_map(settings_ctx) == {}


def test_add_category_appends_with_next_sort_order(settings_ctx):
    before = settings_svc.list_categories(settings_ctx)
    max_sort = max(c.sort_order for c in before)
    cat = settings_svc.add_category(settings_ctx, name="Pets", emoji="🐾")
    assert cat.name == "Pets"
    assert cat.sort_order == max_sort + 1
    assert cat.kind == "spending"
    assert cat.is_active is True
    assert cat.is_system is False


def test_rename_category(settings_ctx):
    cat = settings_svc.add_category(settings_ctx, name="Pets")
    settings_svc.rename_category(settings_ctx, cat.id, "Animals")
    refetched = settings_ctx.db.query(BudgetCategory).get(cat.id)
    assert refetched.name == "Animals"


def test_rename_system_category_is_blocked(settings_ctx):
    income = settings_ctx.db.query(BudgetCategory).filter_by(
        account_id=settings_ctx.account_id, name="Income"
    ).first()
    assert income.is_system is True
    with pytest.raises(ValueError):
        settings_svc.rename_category(settings_ctx, income.id, "Money")


def test_hide_category_sets_inactive(settings_ctx):
    cat = settings_svc.add_category(settings_ctx, name="Pets")
    settings_svc.hide_category(settings_ctx, cat.id)
    refetched = settings_ctx.db.query(BudgetCategory).get(cat.id)
    assert refetched.is_active is False
    # Hidden categories are excluded from the default list.
    active = settings_svc.list_categories(settings_ctx)
    assert all(c.id != cat.id for c in active)
    # But available when include_inactive=True.
    allc = settings_svc.list_categories(settings_ctx, include_inactive=True)
    assert any(c.id == cat.id for c in allc)


def test_hide_system_category_is_blocked(settings_ctx):
    income = settings_ctx.db.query(BudgetCategory).filter_by(
        account_id=settings_ctx.account_id, name="Income"
    ).first()
    with pytest.raises(ValueError):
        settings_svc.hide_category(settings_ctx, income.id)


def test_reorder_categories(settings_ctx):
    cats = settings_svc.list_categories(settings_ctx)
    ids = [c.id for c in cats]
    reversed_ids = list(reversed(ids))
    settings_svc.reorder_categories(settings_ctx, reversed_ids)
    after = settings_svc.list_categories(settings_ctx)
    # First in reversed_ids should now have the lowest sort_order.
    after_sorted = sorted(after, key=lambda c: c.sort_order)
    assert [c.id for c in after_sorted] == reversed_ids


def test_list_categories_scoped_by_account(settings_ctx):
    cats = settings_svc.list_categories(settings_ctx)
    assert all(c.account_id == settings_ctx.account_id for c in cats)
