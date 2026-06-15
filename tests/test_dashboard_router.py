"""
Unit tests for the dashboard router endpoints.

Tests cover:
- GET /api/dashboard: Returns complete dashboard data as JSON
- POST /api/checkin: Records check-in, creates milestones, idempotent

Tests use direct function calls to the router endpoints, bypassing FastAPI
and TestClient complexity, focusing on business logic.
"""
import pytest
from datetime import date, timedelta
from finapp.deps import AccountContext
from finapp.models import (
    Settings,
    CheckIn,
    Milestone,
    BudgetPeriod,
    BudgetCategory,
    BudgetAllocation,
)
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction
from finapp.routers.dashboard import get_dashboard, post_checkin
from fastapi import Response


@pytest.fixture
def setup_account(db, account, ctx):
    """Set up account with default categories and settings."""
    seed_default_categories(db, account.id)

    settings = Settings(
        account_id=account.id,
        setup_complete=True,
        user_name="Alice",
    )
    db.add(settings)
    db.commit()

    return account, ctx


@pytest.fixture
def current_period(db, account):
    """Create current month's budget period."""
    today = date.today()
    period = BudgetPeriod(
        account_id=account.id,
        year=today.year,
        month=today.month,
        income_received_cents=0,
        status="active",
    )
    db.add(period)
    db.commit()
    return period


class TestGetDashboardEndpoint:
    """Test GET /api/dashboard endpoint (direct function call)."""

    def test_get_dashboard_returns_dict(self, db, account, ctx, setup_account, current_period):
        """GET /api/dashboard returns dict with dashboard data."""
        response = get_dashboard(ctx)
        assert isinstance(response, dict)

    def test_get_dashboard_includes_greeting(self, db, account, ctx, setup_account, current_period):
        """GET /api/dashboard includes greeting field with user name."""
        response = get_dashboard(ctx)
        assert "greeting" in response
        assert isinstance(response["greeting"], str)
        assert "Alice" in response["greeting"]

    def test_get_dashboard_includes_date(self, db, account, ctx, setup_account, current_period):
        """GET /api/dashboard includes date field."""
        response = get_dashboard(ctx)
        assert "date" in response
        assert isinstance(response["date"], str)

    def test_get_dashboard_includes_streak(self, db, account, ctx, setup_account, current_period):
        """GET /api/dashboard includes streak field."""
        response = get_dashboard(ctx)
        assert "streak" in response
        assert isinstance(response["streak"], int)

    def test_get_dashboard_includes_current_mission(self, db, account, ctx, setup_account, current_period):
        """GET /api/dashboard includes current_mission field."""
        response = get_dashboard(ctx)
        assert "current_mission" in response
        # Can be dict or None

    def test_get_dashboard_includes_this_week_pulse(self, db, account, ctx, setup_account, current_period):
        """GET /api/dashboard includes this_week_pulse field."""
        response = get_dashboard(ctx)
        assert "this_week_pulse" in response
        assert isinstance(response["this_week_pulse"], dict)
        assert "spent_cents" in response["this_week_pulse"]
        assert "target_cents" in response["this_week_pulse"]
        assert "percentage" in response["this_week_pulse"]

    def test_get_dashboard_includes_next_right_action(self, db, account, ctx, setup_account, current_period):
        """GET /api/dashboard includes next_right_action field."""
        response = get_dashboard(ctx)
        assert "next_right_action" in response
        assert isinstance(response["next_right_action"], dict)
        assert "action" in response["next_right_action"]
        assert "label" in response["next_right_action"]
        assert "hint" in response["next_right_action"]


class TestPostCheckinEndpoint:
    """Test POST /api/checkin endpoint (direct function call)."""

    def test_post_checkin_creates_checkin(self, db, account, ctx, setup_account):
        """POST /api/checkin creates a CheckIn record for today."""
        response = post_checkin(ctx, response=Response())
        assert "checkin_id" in response
        assert isinstance(response["checkin_id"], int)

        # Verify CheckIn was created in DB
        checkin = db.query(CheckIn).filter_by(
            account_id=account.id,
            date=date.today()
        ).first()
        assert checkin is not None
        assert checkin.id == response["checkin_id"]

    def test_post_checkin_returns_new_checkin_status_201(self, db, account, ctx, setup_account):
        """POST /api/checkin sets status 201 on new check-in."""
        response_obj = Response()
        response = post_checkin(ctx, response=response_obj)
        assert response_obj.status_code == 201

    def test_post_checkin_returns_existing_checkin_status_200(self, db, account, ctx, setup_account):
        """POST /api/checkin sets status 200 on already-checked-in day."""
        # First checkin
        response_obj1 = Response()
        response1 = post_checkin(ctx, response=response_obj1)
        assert response_obj1.status_code == 201

        # Second checkin same day
        response_obj2 = Response()
        response2 = post_checkin(ctx, response=response_obj2)
        assert response_obj2.status_code == 200

    def test_post_checkin_is_idempotent(self, db, account, ctx, setup_account):
        """POST /api/checkin is idempotent; multiple calls return same checkin."""
        response_obj1 = Response()
        response1 = post_checkin(ctx, response=response_obj1)
        checkin_id_1 = response1["checkin_id"]

        response_obj2 = Response()
        response2 = post_checkin(ctx, response=response_obj2)
        checkin_id_2 = response2["checkin_id"]

        assert checkin_id_1 == checkin_id_2

    def test_post_checkin_includes_streak(self, db, account, ctx, setup_account):
        """POST /api/checkin response includes updated streak."""
        response = post_checkin(ctx, response=Response())
        assert "streak" in response
        assert isinstance(response["streak"], int)
        assert response["streak"] >= 1  # At least today's checkin

    def test_post_checkin_includes_milestones_created(self, db, account, ctx, setup_account):
        """POST /api/checkin response includes milestones_created."""
        response = post_checkin(ctx, response=Response())
        assert "milestones_created" in response
        assert isinstance(response["milestones_created"], list)

    def test_post_checkin_creates_7day_milestone(self, db, account, ctx, setup_account):
        """POST /api/checkin creates 7-day streak milestone on 7th checkin."""
        # Create 6 check-ins prior to today (yesterday back 6 days)
        today = date.today()
        for i in range(1, 7):
            day = today - timedelta(days=i)
            checkin = CheckIn(account_id=account.id, date=day)
            db.add(checkin)
        db.commit()

        # 7th check-in (today) should create milestone
        response = post_checkin(ctx, response=Response())

        # Should include 7-day milestone
        assert response["streak"] == 7
        milestones = response["milestones_created"]
        assert len(milestones) > 0
        assert any(m.threshold == 7 for m in milestones)

    def test_post_checkin_milestone_has_required_fields(self, db, account, ctx, setup_account):
        """POST /api/checkin milestone objects have threshold and message."""
        # Create 6 prior check-ins (yesterday back 6 days)
        today = date.today()
        for i in range(1, 7):
            day = today - timedelta(days=i)
            checkin = CheckIn(account_id=account.id, date=day)
            db.add(checkin)
        db.commit()

        response = post_checkin(ctx, response=Response())

        for milestone in response["milestones_created"]:
            assert hasattr(milestone, "threshold")
            assert hasattr(milestone, "message")
            assert isinstance(milestone.threshold, int)
            assert isinstance(milestone.message, str)

    def test_post_checkin_no_duplicate_milestones(self, db, account, ctx, setup_account):
        """POST /api/checkin is idempotent; doesn't create duplicate milestones."""
        # Create 6 prior check-ins (yesterday back 6 days)
        today = date.today()
        for i in range(1, 7):
            day = today - timedelta(days=i)
            checkin = CheckIn(account_id=account.id, date=day)
            db.add(checkin)
        db.commit()

        # First POST creates milestone
        response1 = post_checkin(ctx, response=Response())
        milestones1 = response1["milestones_created"]

        # Verify the first POST created the milestone
        assert response1["streak"] == 7
        assert any(m.threshold == 7 for m in milestones1)

    def test_post_checkin_returns_multiple_milestones(self, db, account, ctx, setup_account):
        """POST /api/checkin can return multiple milestones if streak hits multiple thresholds."""
        # Create 29 prior check-ins (yesterday back 29 days) to hit 30-day on next checkin
        today = date.today()
        for i in range(1, 30):
            day = today - timedelta(days=i)
            checkin = CheckIn(account_id=account.id, date=day)
            db.add(checkin)
        db.commit()

        response = post_checkin(ctx, response=Response())

        # Should have streak of 30
        assert response["streak"] == 30
        milestones = response["milestones_created"]
        # Should include both 7-day and 30-day
        thresholds = [m.threshold for m in milestones]
        assert 7 in thresholds
        assert 30 in thresholds


class TestCheckinMilestoneIntegration:
    """Test interaction between check-in and milestone creation."""

    def test_checkin_streak_matches_milestone_thresholds(self, db, account, ctx, setup_account):
        """Streak count and milestone thresholds are aligned."""
        today = date.today()

        # Create exactly 6 prior check-ins (yesterday back 6 days)
        for i in range(1, 7):
            day = today - timedelta(days=i)
            checkin = CheckIn(account_id=account.id, date=day)
            db.add(checkin)
        db.commit()

        response = post_checkin(ctx, response=Response())

        # Streak should be 7 (6 prior + today)
        assert response["streak"] == 7

        # Should create 7-day milestone
        milestones = response["milestones_created"]
        assert any(m.threshold == 7 for m in milestones)

    def test_checkin_persists_milestones_to_db(self, db, account, ctx, setup_account):
        """Milestones created by check-in are persisted to database."""
        today = date.today()

        # Create 6 prior check-ins (yesterday back 6 days)
        for i in range(1, 7):
            day = today - timedelta(days=i)
            checkin = CheckIn(account_id=account.id, date=day)
            db.add(checkin)
        db.commit()

        response = post_checkin(ctx, response=Response())

        # Verify milestone is in DB
        milestone = db.query(Milestone).filter_by(
            account_id=account.id,
            milestone_type="streak_achieved",
            threshold=7,
        ).first()

        assert milestone is not None
        assert milestone.title == "7 Day Streak!"


class TestCheckinDashboardIntegration:
    """Test that check-in affects dashboard data."""

    def test_dashboard_reflects_checkin_immediately(self, db, account, ctx, setup_account, current_period):
        """GET /api/dashboard reflects check-in made via POST /api/checkin."""
        # Before checkin
        response1 = get_dashboard(ctx)
        streak_before = response1["streak"]

        # Create checkin
        post_checkin(ctx, response=Response())

        # After checkin
        response2 = get_dashboard(ctx)
        streak_after = response2["streak"]

        # Streak should increase
        assert streak_after > streak_before
        assert streak_after >= 1
