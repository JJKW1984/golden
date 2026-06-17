"""
Onboarding service (Phase 9): the six-step setup that gates the app until
``Settings.setup_complete = TRUE``.

Steps: welcome+income -> categories -> debt -> emergency fund -> mission queue
-> complete. Money arrives already as cents / basis points (converted at the
HTTP boundary in the router, per the money-discipline rule). Every function
takes ``ctx`` first and is scoped by ``ctx.account_id``.
"""
from datetime import datetime, UTC

from finapp.deps import AccountContext
from finapp.models import Settings, BudgetCategory, DebtAccount, SavingsGoal, Mission
from finapp.services.seeds import seed_default_categories
from finapp.services.balances import get_savings_goal_balance_cents

# System categories that may never be hidden or deleted (§5 Step 3).
SYSTEM_CATEGORY_NAMES = {"Income", "Housing", "Debt", "Emergency Fund"}


def get_or_create_settings(ctx: AccountContext) -> Settings:
    """Return this account's Settings row, creating an empty one if absent."""
    settings = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).first()
    if settings is None:
        settings = Settings(account_id=ctx.account_id, setup_complete=False)
        ctx.db.add(settings)
        ctx.db.commit()
    return settings


def is_setup_complete(ctx: AccountContext) -> bool:
    settings = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).first()
    return bool(settings and settings.setup_complete)


# --- Step 1 & 2: welcome + income -------------------------------------------

def save_settings(
    ctx: AccountContext,
    user_name: str,
    monthly_income_cents: int,
    pay_frequency: str,
    pay_day: int,
    hourly_wage_cents: int | None = None,
) -> Settings:
    """Persist name + income setup. Idempotent: updates the existing row."""
    settings = get_or_create_settings(ctx)
    settings.user_name = user_name
    settings.monthly_income_cents = monthly_income_cents
    settings.pay_frequency = pay_frequency
    settings.pay_day = pay_day
    settings.hourly_wage_cents = hourly_wage_cents
    ctx.db.commit()
    return settings


# --- Step 3: categories ------------------------------------------------------

def get_categories(ctx: AccountContext) -> list[BudgetCategory]:
    """Return the account's categories, seeding the 8 defaults if none exist."""
    existing = ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    if not existing:
        seed_default_categories(ctx.db, ctx.account_id)
        existing = ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    return sorted(existing, key=lambda c: (c.sort_order or 0))


def confirm_categories(
    ctx: AccountContext,
    renames: dict[int, str] | None = None,
    additions: list[str] | None = None,
    hidden_ids: list[int] | None = None,
) -> list[BudgetCategory]:
    """
    Apply category edits: rename by id, add up to 3 custom categories, hide
    unused. System categories (Income/Housing/Debt/Emergency Fund) cannot be
    hidden — attempting to raises ValueError.
    """
    renames = renames or {}
    additions = additions or []
    hidden_ids = hidden_ids or []

    cats = {c.id: c for c in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()}

    for cat_id in hidden_ids:
        cat = cats.get(cat_id)
        if cat and cat.is_system:
            raise ValueError(f"Cannot hide system category '{cat.name}'")

    for cat_id, new_name in renames.items():
        cat = cats.get(cat_id)
        if cat:
            cat.name = new_name

    for cat_id in hidden_ids:
        cat = cats.get(cat_id)
        if cat:
            cat.is_active = False

    max_order = max((c.sort_order or 0) for c in cats.values()) if cats else 0
    for i, name in enumerate(additions[:3], start=1):
        ctx.db.add(BudgetCategory(
            account_id=ctx.account_id,
            name=name,
            kind="spending",
            is_system=False,
            is_active=True,
            sort_order=max_order + i,
            created_at=datetime.now(UTC),
        ))

    ctx.db.commit()
    return get_categories(ctx)


# --- Step 4: debt ------------------------------------------------------------

def add_debts(
    ctx: AccountContext,
    debts: list[dict],
    method: str | None,
) -> list[DebtAccount]:
    """
    Create debt accounts and record the chosen payoff method (no default).
    ``sort_order`` is auto-set by method: snowball = smallest balance first,
    avalanche = highest interest rate first.
    """
    if method:
        settings = get_or_create_settings(ctx)
        settings.debt_method = method

    if not debts:
        ctx.db.commit()
        return []

    if method == "avalanche":
        ordered = sorted(debts, key=lambda d: -d["interest_rate_bps"])
    else:  # snowball (default ordering when method is snowball or unspecified)
        ordered = sorted(debts, key=lambda d: d["balance_cents"])

    created = []
    for i, d in enumerate(ordered, start=1):
        debt = DebtAccount(
            account_id=ctx.account_id,
            name=d["name"],
            creditor=d.get("creditor"),
            opening_balance_cents=d["balance_cents"],
            cached_balance_cents=d["balance_cents"],
            interest_rate_bps=d["interest_rate_bps"],
            minimum_payment_cents=d["minimum_payment_cents"],
            sort_order=i,
            is_active=True,
        )
        ctx.db.add(debt)
        created.append(debt)

    ctx.db.commit()
    return created


# --- Step 5: emergency fund --------------------------------------------------

def set_emergency_fund(
    ctx: AccountContext,
    current_balance_cents: int,
    target_cents: int,
) -> SavingsGoal:
    """Create (or update) the emergency-fund savings goal."""
    goal = ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, goal_type="emergency_fund"
    ).first()
    if goal is None:
        goal = SavingsGoal(
            account_id=ctx.account_id,
            name="Emergency Fund",
            goal_type="emergency_fund",
        )
        ctx.db.add(goal)

    goal.opening_balance_cents = current_balance_cents
    goal.cached_balance_cents = current_balance_cents
    goal.target_cents = target_cents
    goal.is_active = True
    ctx.db.commit()
    return goal


# --- Step 6: mission queue ---------------------------------------------------

def generate_mission_queue(ctx: AccountContext) -> list[Mission]:
    """
    Auto-generate the mission queue (§5 Step 6): emergency fund first if it is
    under target, then active debts in payoff order. Returns the created
    missions in queue order.
    """
    order = 0
    created = []

    ef = ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, goal_type="emergency_fund", is_active=True
    ).first()
    if ef and get_savings_goal_balance_cents(ctx, ef.id) < ef.target_cents:
        order += 1
        m = Mission(
            account_id=ctx.account_id,
            name="Build Emergency Fund",
            mission_type="emergency_fund",
            link_type="savings",
            link_id=ef.id,
            status="active",
            sort_order=order,
        )
        ctx.db.add(m)
        created.append(m)

    debts = ctx.db.query(DebtAccount).filter_by(
        account_id=ctx.account_id, is_active=True
    ).order_by(DebtAccount.sort_order).all()
    for debt in debts:
        order += 1
        m = Mission(
            account_id=ctx.account_id,
            name=f"Pay off {debt.name}",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt.id,
            status="active",
            sort_order=order,
        )
        ctx.db.add(m)
        created.append(m)

    ctx.db.commit()
    return created


# --- Setup complete ----------------------------------------------------------

def complete_setup(ctx: AccountContext) -> Settings:
    """Flip ``setup_complete`` to True — the gate that unlocks the app."""
    settings = get_or_create_settings(ctx)
    settings.setup_complete = True
    ctx.db.commit()
    return settings
