"""
Settings service: manage application settings, CSV column mapping, and budget categories.

The settings service owns:
- The single Settings row per account (create if missing)
- The remembered CSV column map (JSON stored in Settings.csv_column_map)
- Budget category management (add/rename/hide/reorder)

System categories are locked (cannot be renamed or hidden).
All queries are account-scoped via ctx.account_id.
"""
import json
from finapp.deps import AccountContext
from finapp.models import Settings, BudgetCategory


def get_settings(ctx: AccountContext) -> Settings:
    """
    Return the Settings row for ctx.account_id; create it (and commit) if missing.
    Idempotent — never creates a duplicate.
    """
    settings = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).first()

    if settings is None:
        settings = Settings(account_id=ctx.account_id)
        ctx.db.add(settings)
        ctx.db.commit()

    return settings


def update_settings(ctx: AccountContext, **fields) -> Settings:
    """
    Update the Settings row. Only apply keyword args whose value is not None.
    Allowed fields: user_name, debt_method, review_day, currency_symbol,
    hourly_wage_cents, pay_frequency, pay_day.
    Commit. Return the Settings row.
    """
    allowed_fields = {
        "user_name",
        "debt_method",
        "review_day",
        "currency_symbol",
        "hourly_wage_cents",
        "pay_frequency",
        "pay_day",
    }

    settings = get_settings(ctx)

    for key, value in fields.items():
        if key in allowed_fields and value is not None:
            setattr(settings, key, value)

    ctx.db.commit()
    return settings


def get_csv_column_map(ctx: AccountContext) -> dict:
    """Parse Settings.csv_column_map JSON; return {} if null/empty."""
    settings = get_settings(ctx)

    if not settings.csv_column_map:
        return {}

    try:
        return json.loads(settings.csv_column_map)
    except (json.JSONDecodeError, TypeError):
        return {}


def save_csv_column_map(ctx: AccountContext, column_map: dict) -> Settings:
    """Store column_map as JSON in Settings.csv_column_map. Commit. Return Settings."""
    settings = get_settings(ctx)
    settings.csv_column_map = json.dumps(column_map)
    ctx.db.commit()
    return settings


def list_categories(ctx: AccountContext, include_inactive: bool = False) -> list:
    """
    Return BudgetCategory rows for ctx.account_id ordered by sort_order.
    Exclude is_active==False unless include_inactive=True.
    """
    query = ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id)

    if not include_inactive:
        query = query.filter_by(is_active=True)

    return query.order_by(BudgetCategory.sort_order).all()


def add_category(
    ctx: AccountContext, name: str, emoji: str = None, kind: str = "spending"
) -> BudgetCategory:
    """
    Create a non-system, active BudgetCategory with sort_order = (max existing
    sort_order for the account) + 1.
    Commit. Return the new category.
    """
    # Find the maximum sort_order for this account
    max_sort = ctx.db.query(BudgetCategory).filter_by(
        account_id=ctx.account_id
    ).with_entities(BudgetCategory.sort_order).all()

    if max_sort:
        next_sort_order = max(order[0] for order in max_sort) + 1
    else:
        next_sort_order = 1

    category = BudgetCategory(
        account_id=ctx.account_id,
        name=name,
        emoji=emoji,
        kind=kind,
        sort_order=next_sort_order,
        is_system=False,
        is_active=True,
    )

    ctx.db.add(category)
    ctx.db.commit()
    return category


def rename_category(ctx: AccountContext, category_id: int, name: str) -> BudgetCategory:
    """
    Rename a category. Raise ValueError if it is is_system.
    Commit. Return it.
    """
    category = ctx.db.query(BudgetCategory).filter_by(
        id=category_id, account_id=ctx.account_id
    ).first()

    if not category:
        raise ValueError(f"Category {category_id} not found")

    if category.is_system:
        raise ValueError(f"Cannot rename system category: {category.name}")

    category.name = name
    ctx.db.commit()
    return category


def hide_category(ctx: AccountContext, category_id: int) -> BudgetCategory:
    """
    Set is_active=False. Raise ValueError if it is is_system.
    Commit. Return it.
    """
    category = ctx.db.query(BudgetCategory).filter_by(
        id=category_id, account_id=ctx.account_id
    ).first()

    if not category:
        raise ValueError(f"Category {category_id} not found")

    if category.is_system:
        raise ValueError(f"Cannot hide system category: {category.name}")

    category.is_active = False
    ctx.db.commit()
    return category


def reorder_categories(ctx: AccountContext, ordered_ids: list) -> None:
    """
    Set sort_order for each category to its position (index) in ordered_ids.
    Only affects categories belonging to ctx.account_id.
    Commit.
    """
    for position, category_id in enumerate(ordered_ids):
        category = ctx.db.query(BudgetCategory).filter_by(
            id=category_id, account_id=ctx.account_id
        ).first()

        if category:
            category.sort_order = position

    ctx.db.commit()
