"""
Streak service: check-in tracking, streak calculation with grace-day mechanism, and milestone creation.

Check-ins are created once per day. A streak is the number of consecutive CheckIn days
back from today, with a grace-day mechanism: a single 1-day gap is forgiven, but a second gap
or any 2+-day gap resets the streak.

Milestones are created at thresholds (7, 30, 90, 180, 365 days) and are idempotent via a
unique constraint on (account_id, milestone_type, threshold, link_id).
"""
from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from finapp.models import CheckIn, Milestone
from finapp.deps import AccountContext


def ensure_checkin(ctx: AccountContext) -> CheckIn:
    """
    Create a CheckIn for today if not present, return it.
    Idempotent: calling this multiple times returns the same CheckIn.
    """
    today = date.today()

    # Check if already exists
    existing = ctx.db.query(CheckIn).filter_by(
        account_id=ctx.account_id, date=today
    ).first()

    if existing:
        return existing

    # Create new CheckIn
    checkin = CheckIn(account_id=ctx.account_id, date=today)
    ctx.db.add(checkin)
    ctx.db.commit()

    return checkin


def calculate_streak(ctx: AccountContext) -> int:
    """
    Calculate the current streak: consecutive CheckIn days back from today,
    with a grace-day mechanism (one single-day gap is forgiven).

    Algorithm:
    - Start from today and walk backward, counting consecutive days with check-ins.
    - When a gap is encountered, grace may be applied if:
      * Grace hasn't been used yet, AND
      * Either (a) >= 2 consecutive check-ins exist after the gap, OR
        (b) >= 1 check-in after the gap AND more exist beyond that.
    - A second gap (even if 1 day) ends the streak.
    - A 2+-day gap always ends the streak.
    - If there's no check-in for today, streak is 0.

    Returns:
        int: The current streak count (0 if no today check-in, 1+ if streaking).
    """
    today = date.today()

    # Check if there's a check-in for today
    today_checkin = ctx.db.query(CheckIn).filter_by(
        account_id=ctx.account_id, date=today
    ).first()

    if not today_checkin:
        return 0

    # Fetch all check-in dates for this account, sorted descending
    all_checkins = ctx.db.query(CheckIn.date).filter_by(
        account_id=ctx.account_id
    ).order_by(CheckIn.date.desc()).all()

    if not all_checkins:
        return 0

    checkin_dates = [c[0] for c in all_checkins]

    # Count consecutive days from today backward
    streak = 0
    current_date = today
    grace_used = False

    # Keep walking backward through all possible days
    days_checked = 0
    max_days = 400  # Safety limit to prevent infinite loop
    consecutive_streak_before_gap = 0

    while days_checked < max_days:
        if current_date in checkin_dates:
            # Found a check-in for this day
            streak += 1
            consecutive_streak_before_gap += 1
            current_date -= timedelta(days=1)
            days_checked += 1
        else:
            # No check-in for current_date; this is a gap
            # Grace only applies if grace hasn't been used and there are at least 2 check-ins
            # after this gap (to build momentum back into the past)
            if not grace_used:
                # Look ahead: count how many consecutive check-ins are after this gap
                future_date = current_date - timedelta(days=1)
                future_count = 0
                temp_date = future_date

                while temp_date in checkin_dates:
                    future_count += 1
                    temp_date -= timedelta(days=1)

                # Check if there are any check-ins beyond the consecutive run
                beyond_date = temp_date - timedelta(days=1)
                has_beyond = beyond_date in checkin_dates

                # Grace applies if:
                # - At least 2 consecutive check-ins after the gap, OR
                # - At least 1 check-in after the gap AND at least 1 more beyond
                can_apply_grace = (future_count >= 2) or (future_count >= 1 and has_beyond)

                if can_apply_grace:
                    # Grace day is "spent" but not counted in the streak initially
                    # If we hit a second gap, it will be counted retroactively
                    grace_used = True
                    grace_gap_date = current_date  # Remember where the grace gap was
                    current_date = future_date
                    consecutive_streak_before_gap = 0  # Reset for next sequence
                    days_checked += 1
                else:
                    # Not enough check-ins after this gap to justify grace; streak ends
                    break
            else:
                # Grace already used; this is a second gap, so streak ends
                break

    return streak


def get_milestones_for_streak(ctx: AccountContext, streak: int) -> list[Milestone]:
    """
    Create Milestone objects for any threshold that the streak has reached.
    Returns newly created milestones; does not duplicate if already present.

    Thresholds: [7, 30, 90, 180, 365]

    The unique constraint (account_id, milestone_type, threshold, link_id) ensures
    idempotency: attempting to create a duplicate is a no-op.

    Args:
        ctx: Account context.
        streak: Current streak count.

    Returns:
        list[Milestone]: List of newly created Milestone objects (empty if all already exist).
    """
    thresholds = [7, 30, 90, 180, 365]
    created_milestones = []

    for threshold in thresholds:
        if streak < threshold:
            # Haven't reached this threshold yet
            continue

        # Check if this milestone already exists
        existing = ctx.db.query(Milestone).filter_by(
            account_id=ctx.account_id,
            milestone_type="streak_achieved",
            threshold=threshold,
            link_id=None,
        ).first()

        if existing:
            # Already created; skip
            continue

        # Create new milestone
        milestone = Milestone(
            account_id=ctx.account_id,
            milestone_type="streak_achieved",
            title=f"{threshold} Day Streak!",
            description=f"You've checked in for {threshold} consecutive days.",
            threshold=threshold,
            link_type=None,
            link_id=None,
            celebrated=False,
        )
        ctx.db.add(milestone)
        created_milestones.append(milestone)

    # Commit all new milestones at once
    if created_milestones:
        ctx.db.commit()

    return created_milestones
