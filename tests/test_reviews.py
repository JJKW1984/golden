import os
from datetime import date, datetime, timedelta, UTC
from finapp.models import Settings, Review, BudgetCategory, BudgetAllocation, BudgetPeriod
from finapp.services.ledger import create_transaction
from finapp.services.seeds import seed_default_categories
from finapp.services.reviews import (
    REFLECTION_PROMPTS,
    get_reflection_prompt,
    ensure_weekly_review_due,
    get_weekly_review_data,
    complete_weekly_review,
    get_review_streak,
)


def test_get_reflection_prompt_rotates_by_month():
    p1 = get_reflection_prompt(1)
    p2 = get_reflection_prompt(2)
    assert p1 == REFLECTION_PROMPTS[1 % len(REFLECTION_PROMPTS)]
    assert p2 == REFLECTION_PROMPTS[2 % len(REFLECTION_PROMPTS)]


def test_ensure_weekly_review_due_creates_pending_review_on_review_day(db, ctx, account):
    settings = Settings(account_id=ctx.account_id, review_day=date.today().strftime("%A").lower())
    db.add(settings)
    db.commit()

    review = ensure_weekly_review_due(ctx)
    assert review is not None
    assert review.review_type == "weekly"

    # Idempotent: calling again on the same day does not create a duplicate
    again = ensure_weekly_review_due(ctx)
    assert again.id == review.id
    count = db.query(Review).filter_by(account_id=ctx.account_id).count()
    assert count == 1


def test_ensure_weekly_review_due_noop_on_non_review_day(db, ctx, account):
    not_today = (date.today() + timedelta(days=1)).strftime("%A").lower()
    settings = Settings(account_id=ctx.account_id, review_day=not_today)
    db.add(settings)
    db.commit()

    review = ensure_weekly_review_due(ctx)
    assert review is None


def test_complete_weekly_review_sets_prompt_and_notes(db, ctx, account):
    settings = Settings(account_id=ctx.account_id, review_day=date.today().strftime("%A").lower())
    db.add(settings)
    db.commit()
    ensure_weekly_review_due(ctx)

    result = complete_weekly_review(ctx, intention="Cook at home more.", quick=False)
    assert result.notes == "Cook at home more."
    assert result.prompt_shown is not None
    assert result.completed_at is not None


def test_review_streak_counts_consecutive_completed_weeks(db, ctx, account):
    today = date.today()
    for weeks_ago in range(3):
        week_start = today - timedelta(days=today.weekday() + 7 * weeks_ago)
        review = Review(
            account_id=ctx.account_id, review_type="weekly", week_start=week_start,
            prompt_shown="done", completed_at=datetime.now(UTC),
        )
        db.add(review)
    db.commit()

    streak = get_review_streak(ctx)
    assert streak == 3


def test_get_weekly_review_data_uses_over_budget_status(db, ctx, account):
    seed_default_categories(db, ctx.account_id)

    settings = Settings(account_id=ctx.account_id, review_day=date.today().strftime("%A").lower())
    db.add(settings)
    db.commit()

    period = BudgetPeriod(
        account_id=ctx.account_id,
        year=date.today().year,
        month=date.today().month,
        income_received_cents=4300,
        status="active",
    )
    db.add(period)
    db.commit()

    spending_cat = db.query(BudgetCategory).filter(
        BudgetCategory.account_id == ctx.account_id,
        BudgetCategory.kind == "spending",
    ).first()

    alloc = BudgetAllocation(
        account_id=ctx.account_id,
        period_id=period.id,
        category_id=spending_cat.id,
        target_cents=4300,
    )
    db.add(alloc)
    db.commit()

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    create_transaction(
        ctx,
        date=week_start,
        amount_cents=5000,
        direction="out",
        category_id=spending_cat.id,
        payee="Grocer",
    )

    data = get_weekly_review_data(ctx)
    assert data["status"] == "amber"


from finapp.models import DebtAccount, NetWorthSnapshot, Milestone
from finapp.services.reviews import (
    get_monthly_reset_data, close_month, start_month_shortcut,
)


def _make_period(db, ctx, year, month, status="active"):
    period = BudgetPeriod(account_id=ctx.account_id, year=year, month=month, status=status)
    db.add(period)
    db.commit()
    return period


def test_close_month_marks_period_closed_and_creates_next(db, ctx, account, tmp_path, monkeypatch):
    monkeypatch.setattr("finapp.services.reviews.BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("finapp.services.reviews.SOURCE_DB_PATH", str(tmp_path / "finance.db"))
    import sqlite3
    sqlite3.connect(str(tmp_path / "finance.db")).close()

    period = _make_period(db, ctx, 2026, 5)

    result = close_month(ctx, period_id=period.id, notes="Good month.", sweep_to_mission=False)

    db.refresh(period)
    assert period.status == "closed"
    assert period.closed_at is not None
    assert period.notes == "Good month."
    assert result["reconciliation"]["total_drift"] == 0
    assert os.path.exists(result["backup_path"])

    snapshot = db.query(NetWorthSnapshot).filter_by(
        account_id=ctx.account_id, year=2026, month=5,
    ).first()
    assert snapshot is not None


def test_close_month_creates_two_snapshots_for_two_months(db, ctx, account, tmp_path, monkeypatch):
    monkeypatch.setattr("finapp.services.reviews.BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("finapp.services.reviews.SOURCE_DB_PATH", str(tmp_path / "finance.db"))
    import sqlite3
    sqlite3.connect(str(tmp_path / "finance.db")).close()

    period = _make_period(db, ctx, 2026, 5)
    result1 = close_month(ctx, period_id=period.id, notes=None, sweep_to_mission=False)

    # close_month auto-creates June; use the returned next_period_id
    period2 = db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=result1["next_period_id"]
    ).first()
    close_month(ctx, period_id=period2.id, notes=None, sweep_to_mission=False)

    snapshots = db.query(NetWorthSnapshot).filter_by(account_id=ctx.account_id).all()
    assert len(snapshots) == 2


def test_start_month_shortcut_creates_period_if_missing(db, ctx, account):
    period = start_month_shortcut(ctx)
    assert period.year == date.today().year
    assert period.month == date.today().month

    again = start_month_shortcut(ctx)
    assert again.id == period.id
