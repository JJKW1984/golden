"""
Reviews service: Weekly Review (6.5) and Monthly Reset (6.6) flows, plus the
monthly reflection prompt rotation (9.6).

Weekly review "due" detection: ensure_weekly_review_due() creates a pending
Review row the first time it's called on the account's configured review_day
each week — idempotent per week.
"""
import os
from datetime import date, datetime, timedelta

from finapp.deps import AccountContext
from finapp.models import Settings, Review, BudgetPeriod

REFLECTION_PROMPTS = [
    "What's one thing about money that felt easier this month?",
    "What's one purchase you're glad you made?",
    "Did anything surprise you about your spending this month?",
    "What would make next month feel calmer?",
    "What's one habit you want to keep doing?",
    "Where did your money go that you didn't expect?",
    "What's one thing you're proud of this month?",
    "Is there a category that needs a different target next month?",
    "What's something you said no to that felt good?",
    "How does your progress toward your mission feel right now?",
    "What's one small win from this month?",
    "What do you want to remember about this month?",
]

BACKUPS_DIR = "backups"
SOURCE_DB_PATH = "finance.db"


def get_reflection_prompt(month: int) -> str:
    """Rotate the monthly reflection prompt by calendar month number (1-12)."""
    return REFLECTION_PROMPTS[month % len(REFLECTION_PROMPTS)]


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def ensure_weekly_review_due(ctx: AccountContext) -> Review | None:
    """
    If today is the account's configured review_day and no Review exists
    yet for this week, create one and return it. If a Review already exists
    for this week, return it (idempotent). If today isn't the review day,
    return None.
    """
    settings = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).first()
    if not settings or not settings.review_day:
        return None

    today = date.today()
    if today.strftime("%A").lower() != settings.review_day.lower():
        return None

    week_start = _week_start(today)
    existing = ctx.db.query(Review).filter_by(
        account_id=ctx.account_id, review_type="weekly", week_start=week_start,
    ).first()
    if existing:
        return existing

    review = Review(
        account_id=ctx.account_id,
        review_type="weekly",
        week_start=week_start,
        prompt_shown=None,
        completed_at=datetime.utcnow(),
    )
    ctx.db.add(review)
    ctx.db.commit()
    return review


def get_weekly_review_data(ctx: AccountContext) -> dict:
    """
    Assemble weekly review screen data: this-week pulse, active missions,
    and the current review streak.
    """
    from finapp.services.dashboard import _get_this_week_pulse
    from finapp.services.missions import get_active_missions

    pulse = _get_this_week_pulse(ctx)
    status = "amber" if pulse["is_over_budget"] else "teal"

    return {
        "this_week_pulse": pulse,
        "status": status,
        "active_missions": get_active_missions(ctx),
        "review_streak": get_review_streak(ctx),
    }


def complete_weekly_review(ctx: AccountContext, intention: str | None, quick: bool) -> Review:
    """
    Complete this week's review: ensures the pending row exists, stamps
    prompt_shown, stores the optional intention in notes, and fires the
    first_review milestone the first time ever.
    """
    from finapp.services.milestones import create_milestone_if_new

    review = ensure_weekly_review_due(ctx)
    if review is None:
        week_start = _week_start(date.today())
        review = ctx.db.query(Review).filter_by(
            account_id=ctx.account_id, review_type="weekly", week_start=week_start,
        ).first()
        if review is None:
            review = Review(
                account_id=ctx.account_id, review_type="weekly", week_start=week_start,
                completed_at=datetime.utcnow(),
            )
            ctx.db.add(review)

    is_first_ever = ctx.db.query(Review).filter_by(
        account_id=ctx.account_id,
    ).filter(Review.prompt_shown.isnot(None)).count() == 0

    review.prompt_shown = "What's one intention for next week?"
    review.notes = intention
    review.completed_at = datetime.utcnow()
    ctx.db.commit()

    if is_first_ever:
        create_milestone_if_new(
            ctx, milestone_type="first_review", title="Your first review!",
            description="You completed your first weekly review.",
        )

    return review


def get_review_streak(ctx: AccountContext) -> int:
    """
    Count consecutive completed weekly reviews (prompt_shown set), counting
    back from the most recent week with no gap.
    """
    reviews = ctx.db.query(Review).filter(
        Review.account_id == ctx.account_id,
        Review.review_type == "weekly",
        Review.prompt_shown.isnot(None),
    ).order_by(Review.week_start.desc()).all()

    if not reviews:
        return 0

    streak = 0
    expected_week = reviews[0].week_start
    for r in reviews:
        if r.week_start == expected_week:
            streak += 1
            expected_week = expected_week - timedelta(days=7)
        else:
            break

    return streak


def get_monthly_reset_data(ctx: AccountContext, period_id: int) -> dict:
    """
    Last-month summary for the Monthly Reset screen.
    """
    from finapp.models import Transaction
    from finapp.services.balances import get_budget_income_received_cents
    from finapp.services.missions import get_active_missions

    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id,
    ).first()
    if not period:
        raise ValueError(f"Period {period_id} not found")

    income_received = get_budget_income_received_cents(ctx, period.id)

    spent = ctx.db.query(Transaction).filter_by(
        account_id=ctx.account_id, period_id=period.id, direction="out", is_deleted=False,
    ).all()
    total_spent = sum(t.amount_cents for t in spent)

    return {
        "period_id": period.id,
        "year": period.year,
        "month": period.month,
        "income_received_cents": income_received,
        "total_spent_cents": total_spent,
        "active_missions": get_active_missions(ctx),
        "reflection_prompt": get_reflection_prompt(period.month),
    }


def close_month(
    ctx: AccountContext, period_id: int, notes: str | None, sweep_to_mission: bool,
) -> dict:
    """
    Monthly Reset close: marks period closed, opens next month's period,
    runs recompute_balances as the reconciliation gate, captures a net-worth
    snapshot, and writes a backup.
    """
    from sqlalchemy import func
    from finapp.models import AssetAccount, DebtAccount, NetWorthSnapshot
    from finapp.services.reconciliation import apply_recomputed_balances
    from finapp.services.balances import get_debt_balance_cents
    from finapp.services.backup import create_backup
    from finapp.services.ledger import get_or_create_period

    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id,
    ).first()
    if not period:
        raise ValueError(f"Period {period_id} not found")

    period.status = "closed"
    period.closed_at = datetime.utcnow()
    if notes:
        period.notes = notes
    ctx.db.commit()

    # Open next month's period (no-op if it already exists)
    next_month_date = date(period.year, period.month, 1) + timedelta(days=32)
    next_period = get_or_create_period(ctx.db, ctx.account_id, next_month_date.replace(day=1))

    # Reconciliation gate
    reconciliation = apply_recomputed_balances(ctx.db, ctx.account_id)

    # Net-worth snapshot for the closing period
    total_assets = ctx.db.query(func.sum(AssetAccount.balance_cents)).filter_by(
        account_id=ctx.account_id, is_active=True,
    ).scalar() or 0
    debts = ctx.db.query(DebtAccount).filter_by(account_id=ctx.account_id, is_active=True).all()
    total_debt = sum(get_debt_balance_cents(ctx, d.id) for d in debts)
    net_worth = total_assets - total_debt

    existing_snapshot = ctx.db.query(NetWorthSnapshot).filter_by(
        account_id=ctx.account_id, year=period.year, month=period.month,
    ).first()
    if existing_snapshot:
        existing_snapshot.net_worth_cents = net_worth
        existing_snapshot.total_assets_cents = total_assets
        existing_snapshot.total_debt_cents = total_debt
    else:
        ctx.db.add(NetWorthSnapshot(
            account_id=ctx.account_id, year=period.year, month=period.month,
            net_worth_cents=net_worth, total_assets_cents=total_assets, total_debt_cents=total_debt,
        ))
    ctx.db.commit()

    # Backup
    backup_path = create_backup(
        source_db_path=SOURCE_DB_PATH, backups_dir=BACKUPS_DIR,
        year=period.year, month=period.month,
    )

    return {
        "period_id": period.id,
        "next_period_id": next_period.id,
        "reconciliation": reconciliation,
        "net_worth_cents": net_worth,
        "backup_path": backup_path,
    }


def start_month_shortcut(ctx: AccountContext) -> BudgetPeriod:
    """
    T1: 'Start this month' abbreviated reset. Opens this month's period
    without running the full Monthly Reset ritual.
    """
    from finapp.services.ledger import get_or_create_period
    today = date.today()
    return get_or_create_period(ctx.db, ctx.account_id, today)
