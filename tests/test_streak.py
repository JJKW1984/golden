"""
Unit tests for the streak service.
Tests check-in creation, streak calculation with grace-day logic, and milestone creation.
"""
import pytest
from datetime import date, timedelta
from finapp.models import CheckIn, Milestone
from finapp.services.seeds import seed_default_categories


@pytest.fixture
def setup_account(db, account, ctx):
    """Set up account with default categories."""
    seed_default_categories(db, account.id)
    return account, ctx


class TestEnsureCheckin:
    """Test the ensure_checkin function."""

    def test_ensure_checkin_creates_today(self, db, ctx, setup_account):
        """ensure_checkin creates a CheckIn for today if absent."""
        from finapp.services.streak import ensure_checkin

        today = date.today()

        # No checkin yet
        existing = db.query(CheckIn).filter_by(
            account_id=ctx.account_id, date=today
        ).first()
        assert existing is None

        # Ensure checkin
        checkin = ensure_checkin(ctx)

        assert checkin is not None
        assert checkin.account_id == ctx.account_id
        assert checkin.date == today

        # Verify it's in the DB
        db_checkin = db.query(CheckIn).filter_by(
            account_id=ctx.account_id, date=today
        ).first()
        assert db_checkin is not None
        assert db_checkin.id == checkin.id

    def test_ensure_checkin_returns_existing(self, db, ctx, setup_account):
        """ensure_checkin returns existing CheckIn if already present."""
        from finapp.services.streak import ensure_checkin

        today = date.today()

        # Create a checkin manually
        existing_checkin = CheckIn(account_id=ctx.account_id, date=today)
        db.add(existing_checkin)
        db.commit()

        # Call ensure_checkin
        returned = ensure_checkin(ctx)

        assert returned.id == existing_checkin.id
        assert returned.date == today

    def test_ensure_checkin_idempotent(self, db, ctx, setup_account):
        """Calling ensure_checkin twice returns the same CheckIn."""
        from finapp.services.streak import ensure_checkin

        checkin1 = ensure_checkin(ctx)
        checkin2 = ensure_checkin(ctx)

        assert checkin1.id == checkin2.id


class TestCalculateStreak:
    """Test streak calculation with grace-day logic."""

    def test_calculate_streak_clean_run(self, db, ctx, setup_account):
        """Streak counts consecutive check-ins from today backward."""
        from finapp.services.streak import calculate_streak

        today = date.today()

        # Create 5 consecutive check-ins (today, -1, -2, -3, -4)
        for i in range(5):
            day = today - timedelta(days=i)
            checkin = CheckIn(account_id=ctx.account_id, date=day)
            db.add(checkin)
        db.commit()

        streak = calculate_streak(ctx)
        assert streak == 5

    def test_calculate_streak_single_checkin(self, db, ctx, setup_account):
        """Streak is 1 with only today's check-in."""
        from finapp.services.streak import calculate_streak

        today = date.today()
        checkin = CheckIn(account_id=ctx.account_id, date=today)
        db.add(checkin)
        db.commit()

        streak = calculate_streak(ctx)
        assert streak == 1

    def test_calculate_streak_one_day_gap_ends_consecutive(self, db, ctx, setup_account):
        """Without a check-in yesterday, streak resets to 1."""
        from finapp.services.streak import calculate_streak

        today = date.today()

        # Create checkin for today but skip yesterday
        CheckIn(account_id=ctx.account_id, date=today)
        # No checkin for today - 1
        CheckIn(account_id=ctx.account_id, date=today - timedelta(days=2))
        db.add_all([
            CheckIn(account_id=ctx.account_id, date=today),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=2)),
        ])
        db.commit()

        streak = calculate_streak(ctx)
        # Only today counts; yesterday is missing (gap)
        assert streak == 1

    def test_calculate_streak_grace_day_single_gap_survives(self, db, ctx, setup_account):
        """A single 1-day gap is forgiven; streak continues across it."""
        from finapp.services.streak import calculate_streak

        today = date.today()

        # Create check-ins: today, today-2 (gap at -1), today-3, today-4, today-5
        # This models: today checked in, yesterday missed (grace), then 3 consecutive before
        db.add_all([
            CheckIn(account_id=ctx.account_id, date=today),
            # gap at today - 1 (forgiven by grace)
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=2)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=3)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=4)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=5)),
        ])
        db.commit()

        streak = calculate_streak(ctx)
        # Streak: today + grace gap + 3 consecutive = 5 days total in the streak
        assert streak == 5

    def test_calculate_streak_grace_day_only_used_once(self, db, ctx, setup_account):
        """A second gap resets to 1; grace day covers only the most recent single gap."""
        from finapp.services.streak import calculate_streak

        today = date.today()

        # Create check-ins: today, today-2 (gap at -1, forgiven), today-4 (gap at -3, NOT forgiven)
        db.add_all([
            CheckIn(account_id=ctx.account_id, date=today),
            # gap at today - 1 (forgiven by grace)
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=2)),
            # gap at today - 3 (NOT forgiven; it's a second gap)
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=4)),
        ])
        db.commit()

        streak = calculate_streak(ctx)
        # When we hit the second gap, the streak stops.
        # Streak is: today (1) + day-2 (1) = 2
        # Then there's a second gap at -3, so we stop.
        assert streak == 2

    def test_calculate_streak_two_day_gap_resets_to_one(self, db, ctx, setup_account):
        """A 2-day gap resets streak to 1 (grace day doesn't cover multi-day gaps)."""
        from finapp.services.streak import calculate_streak

        today = date.today()

        # Create check-ins: today, today-3 (2-day gap at -1 and -2)
        db.add_all([
            CheckIn(account_id=ctx.account_id, date=today),
            # gaps at today - 1 and today - 2 (2-day gap; not forgiven)
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=3)),
        ])
        db.commit()

        streak = calculate_streak(ctx)
        # Only today counts; 2-day gap is too large for grace
        assert streak == 1

    def test_calculate_streak_missing_today_returns_zero(self, db, ctx, setup_account):
        """If there's no checkin for today, streak is 0."""
        from finapp.services.streak import calculate_streak

        today = date.today()

        # Create checkins for yesterday and before, but not today
        db.add_all([
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=1)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=2)),
        ])
        db.commit()

        streak = calculate_streak(ctx)
        assert streak == 0

    def test_calculate_streak_empty_returns_zero(self, db, ctx, setup_account):
        """With no check-ins, streak is 0."""
        from finapp.services.streak import calculate_streak

        streak = calculate_streak(ctx)
        assert streak == 0


class TestGetMilestonesForStreak:
    """Test milestone creation for streak thresholds."""

    def test_get_milestones_at_threshold_7(self, db, ctx, setup_account):
        """Milestone created when streak reaches 7."""
        from finapp.services.streak import get_milestones_for_streak

        today = date.today()
        for i in range(7):
            db.add(CheckIn(account_id=ctx.account_id, date=today - timedelta(days=i)))
        db.commit()

        milestones = get_milestones_for_streak(ctx, streak=7)

        assert len(milestones) == 1
        m = milestones[0]
        assert m.milestone_type == "streak_achieved"
        assert m.threshold == 7
        assert m.account_id == ctx.account_id

    def test_get_milestones_at_all_thresholds(self, db, ctx, setup_account):
        """Milestones created at 7, 30, 90, 180, 365."""
        from finapp.services.streak import get_milestones_for_streak

        today = date.today()
        for i in range(365):
            db.add(CheckIn(account_id=ctx.account_id, date=today - timedelta(days=i)))
        db.commit()

        milestones = get_milestones_for_streak(ctx, streak=365)

        # Should create milestones for 7, 30, 90, 180, 365
        assert len(milestones) == 5
        thresholds = sorted([m.threshold for m in milestones])
        assert thresholds == [7, 30, 90, 180, 365]

    def test_get_milestones_multiple_streaks_idempotent(self, db, ctx, setup_account):
        """Calling get_milestones_for_streak twice at same threshold doesn't duplicate."""
        from finapp.services.streak import get_milestones_for_streak

        today = date.today()
        for i in range(30):
            db.add(CheckIn(account_id=ctx.account_id, date=today - timedelta(days=i)))
        db.commit()

        milestones1 = get_milestones_for_streak(ctx, streak=30)
        db.commit()

        milestones2 = get_milestones_for_streak(ctx, streak=30)
        db.commit()

        # Both calls should create the same milestones for 7 and 30
        # With unique constraint, attempting to create duplicate at 30 should be skipped
        assert len(milestones1) == 2  # 7, 30
        assert len(milestones2) == 0  # Already created, so no new ones

        # Check that the actual DB has exactly 2 milestones
        all_milestones = db.query(Milestone).filter_by(account_id=ctx.account_id).all()
        assert len(all_milestones) == 2

    def test_get_milestones_below_threshold_none(self, db, ctx, setup_account):
        """No milestone if streak hasn't reached threshold."""
        from finapp.services.streak import get_milestones_for_streak

        today = date.today()
        for i in range(5):
            db.add(CheckIn(account_id=ctx.account_id, date=today - timedelta(days=i)))
        db.commit()

        milestones = get_milestones_for_streak(ctx, streak=5)

        assert len(milestones) == 0

    def test_get_milestones_between_thresholds(self, db, ctx, setup_account):
        """Only thresholds <= current streak are created."""
        from finapp.services.streak import get_milestones_for_streak

        today = date.today()
        for i in range(50):
            db.add(CheckIn(account_id=ctx.account_id, date=today - timedelta(days=i)))
        db.commit()

        milestones = get_milestones_for_streak(ctx, streak=50)

        # Should create milestones for 7, 30 (but not 90, 180, 365)
        assert len(milestones) == 2
        thresholds = sorted([m.threshold for m in milestones])
        assert thresholds == [7, 30]

    def test_get_milestones_skips_already_created(self, db, ctx, setup_account):
        """If a milestone already exists for a threshold, it's not recreated."""
        from finapp.services.streak import get_milestones_for_streak

        today = date.today()
        for i in range(30):
            db.add(CheckIn(account_id=ctx.account_id, date=today - timedelta(days=i)))
        db.commit()

        # First call creates milestones for 7 and 30
        milestones1 = get_milestones_for_streak(ctx, streak=30)
        db.commit()
        assert len(milestones1) == 2

        # Second call at same streak should return empty (all already created)
        milestones2 = get_milestones_for_streak(ctx, streak=30)
        assert len(milestones2) == 0

        # Verify DB has exactly 2 milestones
        all_milestones = db.query(Milestone).filter_by(account_id=ctx.account_id).all()
        assert len(all_milestones) == 2


class TestStreakIntegration:
    """Integration tests combining ensure_checkin, calculate_streak, and milestones."""

    def test_full_flow_day_one(self, db, ctx, setup_account):
        """First day: checkin created, streak is 1, no milestones."""
        from finapp.services.streak import ensure_checkin, calculate_streak, get_milestones_for_streak

        checkin = ensure_checkin(ctx)
        assert checkin is not None

        streak = calculate_streak(ctx)
        assert streak == 1

        milestones = get_milestones_for_streak(ctx, streak=streak)
        assert len(milestones) == 0

    def test_full_flow_week_hit(self, db, ctx, setup_account):
        """On day 7: checkin exists, streak is 7, milestone created."""
        from finapp.services.streak import ensure_checkin, calculate_streak, get_milestones_for_streak

        today = date.today()
        for i in range(7):
            db.add(CheckIn(account_id=ctx.account_id, date=today - timedelta(days=i)))
        db.commit()

        checkin = ensure_checkin(ctx)
        assert checkin.date == today

        streak = calculate_streak(ctx)
        assert streak == 7

        milestones = get_milestones_for_streak(ctx, streak=streak)
        assert len(milestones) == 1
        assert milestones[0].threshold == 7

    def test_full_flow_with_grace_day(self, db, ctx, setup_account):
        """Streak of 8 with a grace gap: still counts as 8."""
        from finapp.services.streak import calculate_streak, get_milestones_for_streak

        today = date.today()
        # Create: today, gap, -2, -3, -4, -5, -6, -7, -8
        db.add_all([
            CheckIn(account_id=ctx.account_id, date=today),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=2)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=3)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=4)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=5)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=6)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=7)),
            CheckIn(account_id=ctx.account_id, date=today - timedelta(days=8)),
        ])
        db.commit()

        streak = calculate_streak(ctx)
        assert streak == 8

        milestones = get_milestones_for_streak(ctx, streak=streak)
        assert len(milestones) == 1
        assert milestones[0].threshold == 7
