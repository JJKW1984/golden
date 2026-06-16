import os
from datetime import date, datetime, timedelta
from finapp.models import Settings, Review, BudgetCategory, BudgetAllocation, BudgetPeriod
from finapp.services.ledger import create_transaction
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
            prompt_shown="done", completed_at=datetime.utcnow(),
        )
        db.add(review)
    db.commit()

    streak = get_review_streak(ctx)
    assert streak == 3
