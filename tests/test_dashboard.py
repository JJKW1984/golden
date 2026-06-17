"""
Unit tests for the Dashboard service.
TDD: Tests define the dashboard data structure and component logic before implementation.

Dashboard data includes:
- greeting: time-based greeting with user name
- date: formatted current date
- streak: check-in streak count
- current_mission: active mission stub with progress
- this_week_pulse: spending vs target for current week
- next_right_action: primary action prompt
"""
import pytest
from datetime import date, timedelta
from finapp.models import (
    Settings,
    CheckIn,
    BudgetPeriod,
    BudgetCategory,
    BudgetAllocation,
    Transaction,
    DebtAccount,
    SavingsGoal,
    Mission,
)
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import create_transaction


@pytest.fixture
def setup_account(db, account, ctx):
    """Set up account with default categories and settings."""
    seed_default_categories(db, account.id)

    # Initialize settings with user name
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


class TestGreeting:
    """Test the greeting component."""

    def test_greeting_morning(self, db, ctx, setup_account, monkeypatch):
        """Greeting is 'Good morning, Alice' between 5am-12pm."""
        from finapp.services.dashboard import get_dashboard_data
        from datetime import datetime

        # Mock current time to 8am
        class MockDatetime:
            @staticmethod
            def now():
                return datetime(2026, 6, 14, 8, 0, 0)

        monkeypatch.setattr("finapp.services.dashboard.datetime", MockDatetime)

        data = get_dashboard_data(ctx)
        assert data["greeting"] == "Good morning, Alice"

    def test_greeting_afternoon(self, db, ctx, setup_account, monkeypatch):
        """Greeting is 'Good afternoon, Alice' between 12pm-5pm."""
        from finapp.services.dashboard import get_dashboard_data
        from datetime import datetime

        class MockDatetime:
            @staticmethod
            def now():
                return datetime(2026, 6, 14, 14, 0, 0)

        monkeypatch.setattr("finapp.services.dashboard.datetime", MockDatetime)

        data = get_dashboard_data(ctx)
        assert data["greeting"] == "Good afternoon, Alice"

    def test_greeting_evening(self, db, ctx, setup_account, monkeypatch):
        """Greeting is 'Good evening, Alice' after 5pm or before 5am."""
        from finapp.services.dashboard import get_dashboard_data
        from datetime import datetime

        class MockDatetime:
            @staticmethod
            def now():
                return datetime(2026, 6, 14, 21, 0, 0)

        monkeypatch.setattr("finapp.services.dashboard.datetime", MockDatetime)

        data = get_dashboard_data(ctx)
        assert data["greeting"] == "Good evening, Alice"

    def test_greeting_midnight(self, db, ctx, setup_account, monkeypatch):
        """Greeting is 'Good evening, Alice' at midnight."""
        from finapp.services.dashboard import get_dashboard_data
        from datetime import datetime

        class MockDatetime:
            @staticmethod
            def now():
                return datetime(2026, 6, 14, 0, 0, 0)

        monkeypatch.setattr("finapp.services.dashboard.datetime", MockDatetime)

        data = get_dashboard_data(ctx)
        assert data["greeting"] == "Good evening, Alice"


class TestDateFormatting:
    """Test the date formatting component."""

    def test_date_format_includes_day_of_week(self, db, ctx, setup_account, monkeypatch):
        """Date is formatted as 'Sunday, June 14'."""
        from finapp.services.dashboard import get_dashboard_data
        from datetime import date

        class MockDate(date):
            @classmethod
            def today(cls):
                return date(2026, 6, 14)

        monkeypatch.setattr("finapp.services.dashboard.date", MockDate)
        data = get_dashboard_data(ctx)
        assert "June 14" in data["date"]
        assert data["date"][0].isalpha()  # Starts with day name

    def test_date_includes_month_and_day(self, db, ctx, setup_account, monkeypatch):
        """Date includes full month name and numeric day."""
        from finapp.services.dashboard import get_dashboard_data
        from datetime import date

        class MockDate(date):
            @classmethod
            def today(cls):
                return date(2026, 6, 14)

        monkeypatch.setattr("finapp.services.dashboard.date", MockDate)
        data = get_dashboard_data(ctx)
        assert any(month in data["date"] for month in [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ])
        assert "14" in data["date"]


class TestStreak:
    """Test the streak component."""

    def test_streak_with_checkins(self, db, ctx, setup_account):
        """Streak fetches current streak from streak service."""
        from finapp.services.dashboard import get_dashboard_data

        today = date.today()

        # Create 3 consecutive check-ins
        for i in range(3):
            day = today - timedelta(days=i)
            checkin = CheckIn(account_id=ctx.account_id, date=day)
            db.add(checkin)
        db.commit()

        data = get_dashboard_data(ctx)
        assert data["streak"] == 3

    def test_streak_zero_without_checkin(self, db, ctx, setup_account):
        """Streak is 0 when no check-in for today."""
        from finapp.services.dashboard import get_dashboard_data

        today = date.today()

        # Create a check-in for yesterday only
        checkin = CheckIn(account_id=ctx.account_id, date=today - timedelta(days=1))
        db.add(checkin)
        db.commit()

        data = get_dashboard_data(ctx)
        assert data["streak"] == 0


class TestCurrentMission:
    """Test the current mission stub component."""

    def test_current_mission_returns_first_active(self, db, ctx, setup_account, current_period):
        """current_mission returns first active mission with progress stub."""
        from finapp.services.dashboard import get_dashboard_data

        # Create a debt account
        debt = DebtAccount(
            account_id=ctx.account_id,
            name="Credit Card",
            opening_balance_cents=10000,  # $100
            cached_balance_cents=10000,
            interest_rate_bps=1999,
            minimum_payment_cents=2500,
        )
        db.add(debt)
        db.commit()

        # Create a debt payoff mission
        mission = Mission(
            account_id=ctx.account_id,
            name="Pay off Credit Card",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt.id,
            status="active",
            sort_order=1,
        )
        db.add(mission)
        db.commit()

        data = get_dashboard_data(ctx)
        assert data["current_mission"] is not None
        assert "title" in data["current_mission"]
        assert "progress" in data["current_mission"]
        assert "target" in data["current_mission"]
        # Progress should be 0-100
        assert 0 <= data["current_mission"]["progress"] <= 100

    def test_current_mission_none_when_no_active(self, db, ctx, setup_account):
        """current_mission is None when no active missions."""
        from finapp.services.dashboard import get_dashboard_data

        data = get_dashboard_data(ctx)
        assert data["current_mission"] is None

    def test_current_mission_includes_debt_account_name(self, db, ctx, setup_account):
        """current_mission title includes debt account name."""
        from finapp.services.dashboard import get_dashboard_data

        debt = DebtAccount(
            account_id=ctx.account_id,
            name="Auto Loan",
            opening_balance_cents=50000,
            cached_balance_cents=50000,
            interest_rate_bps=599,
            minimum_payment_cents=5000,
        )
        db.add(debt)
        db.commit()

        mission = Mission(
            account_id=ctx.account_id,
            name="Auto Loan Payoff",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt.id,
            status="active",
            sort_order=1,
        )
        db.add(mission)
        db.commit()

        data = get_dashboard_data(ctx)
        # Title should reference the debt name
        assert "Auto Loan" in data["current_mission"]["title"]

    def test_current_mission_ignores_completed(self, db, ctx, setup_account):
        """current_mission skips completed missions."""
        from finapp.services.dashboard import get_dashboard_data

        debt = DebtAccount(
            account_id=ctx.account_id,
            name="Old Debt",
            opening_balance_cents=5000,
            cached_balance_cents=5000,
            interest_rate_bps=1999,
            minimum_payment_cents=500,
        )
        db.add(debt)
        db.commit()

        # Create a completed mission (should be ignored)
        mission1 = Mission(
            account_id=ctx.account_id,
            name="Old Debt Payoff",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt.id,
            status="completed",
            sort_order=1,
        )
        db.add(mission1)
        db.commit()

        data = get_dashboard_data(ctx)
        assert data["current_mission"] is None

    def test_current_mission_ignores_emergency_fund(self, db, ctx, setup_account):
        """current_mission ignores emergency_fund goal missions in Phase 4 stub."""
        from finapp.services.dashboard import get_dashboard_data

        ef = SavingsGoal(
            account_id=ctx.account_id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=100000,
            opening_balance_cents=0,
            cached_balance_cents=0,
        )
        db.add(ef)
        db.commit()

        mission = Mission(
            account_id=ctx.account_id,
            name="Emergency Fund Builder",
            mission_type="emergency_fund",
            link_type="savings",
            link_id=ef.id,
            status="active",
            sort_order=1,
        )
        db.add(mission)
        db.commit()

        data = get_dashboard_data(ctx)
        # Emergency fund missions should not appear (stub)
        assert data["current_mission"] is None


class TestThisWeekPulse:
    """Test the weekly spending pulse component."""

    def test_this_week_pulse_calculates_weekly_spending(self, db, ctx, setup_account, current_period):
        """this_week_pulse sums spending transactions in current week (Mon-Sun)."""
        from finapp.services.dashboard import get_dashboard_data

        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday of current week

        # Get spending category
        spending_cat = db.query(BudgetCategory).filter(
            BudgetCategory.account_id == ctx.account_id,
            BudgetCategory.kind == "spending"
        ).first()

        # Create allocation for this period
        alloc = BudgetAllocation(
            account_id=ctx.account_id,
            period_id=current_period.id,
            category_id=spending_cat.id,
            target_cents=50000,  # $500
        )
        db.add(alloc)
        db.commit()

        # Create transactions this week
        for i in range(3):
            txn_date = week_start + timedelta(days=i)
            create_transaction(
                ctx,
                date=txn_date,
                amount_cents=10000,  # $100
                direction="out",
                category_id=spending_cat.id,
                payee="Test",
            )

        data = get_dashboard_data(ctx)
        pulse = data["this_week_pulse"]

        assert pulse["spent_cents"] == 30000  # $300 total
        assert pulse["target_cents"] > 0  # Prorated target
        assert 0 <= pulse["percentage"] <= 100

    def test_this_week_pulse_percentage(self, db, ctx, setup_account, current_period):
        """this_week_pulse percentage is (spent / target * 100)."""
        from finapp.services.dashboard import get_dashboard_data

        today = date.today()
        week_start = today - timedelta(days=today.weekday())

        spending_cat = db.query(BudgetCategory).filter(
            BudgetCategory.account_id == ctx.account_id,
            BudgetCategory.kind == "spending"
        ).first()

        # Allocate $500 for the month (prorated to ~$115 per week if 4.3 weeks)
        alloc = BudgetAllocation(
            account_id=ctx.account_id,
            period_id=current_period.id,
            category_id=spending_cat.id,
            target_cents=50000,
        )
        db.add(alloc)
        db.commit()

        # Spend $100 this week
        create_transaction(
            ctx,
            date=week_start,
            amount_cents=10000,
            direction="out",
            category_id=spending_cat.id,
            payee="Test",
        )

        data = get_dashboard_data(ctx)
        pulse = data["this_week_pulse"]

        # Percentage should be reasonable (spent < target, so < 100)
        assert pulse["percentage"] < 100
        assert pulse["usage_percentage"] == pulse["percentage"]
        assert pulse["is_over_budget"] is False

    def test_this_week_pulse_flags_over_budget(self, db, ctx, setup_account, current_period):
        """this_week_pulse exposes over-budget state when spending exceeds the weekly target."""
        from finapp.services.dashboard import get_dashboard_data

        today = date.today()
        week_start = today - timedelta(days=today.weekday())

        spending_cat = db.query(BudgetCategory).filter(
            BudgetCategory.account_id == ctx.account_id,
            BudgetCategory.kind == "spending"
        ).first()

        alloc = BudgetAllocation(
            account_id=ctx.account_id,
            period_id=current_period.id,
            category_id=spending_cat.id,
            target_cents=4300,
        )
        db.add(alloc)
        db.commit()

        create_transaction(
            ctx,
            date=week_start,
            amount_cents=5000,
            direction="out",
            category_id=spending_cat.id,
            payee="Test",
        )

        data = get_dashboard_data(ctx)
        pulse = data["this_week_pulse"]

        assert pulse["usage_percentage"] > 100
        assert pulse["percentage"] == 100
        assert pulse["is_over_budget"] is True
        assert pulse["remaining_cents"] < 0

    def test_this_week_pulse_excludes_future_transactions(self, db, ctx, setup_account, current_period):
        """this_week_pulse excludes transactions outside current week."""
        from finapp.services.dashboard import get_dashboard_data

        today = date.today()
        week_start = today - timedelta(days=today.weekday())

        spending_cat = db.query(BudgetCategory).filter(
            BudgetCategory.account_id == ctx.account_id,
            BudgetCategory.kind == "spending"
        ).first()

        alloc = BudgetAllocation(
            account_id=ctx.account_id,
            period_id=current_period.id,
            category_id=spending_cat.id,
            target_cents=50000,
        )
        db.add(alloc)
        db.commit()

        # Create transaction this week
        create_transaction(
            ctx,
            date=week_start,
            amount_cents=10000,
            direction="out",
            category_id=spending_cat.id,
            payee="This week",
        )

        # Create transaction next week (should be excluded)
        next_week = week_start + timedelta(days=7)
        create_transaction(
            ctx,
            date=next_week,
            amount_cents=20000,
            direction="out",
            category_id=spending_cat.id,
            payee="Next week",
        )

        data = get_dashboard_data(ctx)
        pulse = data["this_week_pulse"]

        # Should only include this week's $100
        assert pulse["spent_cents"] == 10000


class TestNextRightAction:
    """Test integration with next_right_action service."""

    def test_next_right_action_included_in_data(self, db, ctx, setup_account, current_period):
        """next_right_action is included in dashboard data."""
        from finapp.services.dashboard import get_dashboard_data

        data = get_dashboard_data(ctx)
        assert "next_right_action" in data
        nra = data["next_right_action"]

        assert "action" in nra
        assert "label" in nra
        assert "hint" in nra

    def test_next_right_action_reflects_setup_complete(self, db, ctx, setup_account, current_period):
        """next_right_action reflects current setup state."""
        from finapp.services.dashboard import get_dashboard_data

        # Mark setup as incomplete
        settings = db.query(Settings).filter_by(account_id=ctx.account_id).first()
        settings.setup_complete = False
        db.commit()

        data = get_dashboard_data(ctx)
        assert data["next_right_action"]["action"] == "complete_setup"


class TestDashboardDataStructure:
    """Test the overall dashboard data structure."""

    def test_dashboard_returns_complete_dict(self, db, ctx, setup_account, current_period):
        """get_dashboard_data returns dict with all required keys."""
        from finapp.services.dashboard import get_dashboard_data

        data = get_dashboard_data(ctx)

        required_keys = [
            "greeting",
            "date",
            "streak",
            "current_mission",
            "this_week_pulse",
            "next_right_action",
        ]

        for key in required_keys:
            assert key in data, f"Missing key: {key}"

    def test_dashboard_pulse_substructure(self, db, ctx, setup_account, current_period):
        """this_week_pulse has correct substructure."""
        from finapp.services.dashboard import get_dashboard_data

        data = get_dashboard_data(ctx)
        pulse = data["this_week_pulse"]

        required_pulse_keys = ["spent_cents", "target_cents", "percentage"]
        for key in required_pulse_keys:
            assert key in pulse, f"Missing pulse key: {key}"

    def test_dashboard_mission_substructure_when_present(self, db, ctx, setup_account, current_period):
        """current_mission has correct substructure when not None."""
        from finapp.services.dashboard import get_dashboard_data

        # Create a mission
        debt = DebtAccount(
            account_id=ctx.account_id,
            name="Test Debt",
            opening_balance_cents=5000,
            cached_balance_cents=5000,
            interest_rate_bps=1999,
            minimum_payment_cents=500,
        )
        db.add(debt)
        db.commit()

        mission = Mission(
            account_id=ctx.account_id,
            name="Test Mission",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt.id,
            status="active",
            sort_order=1,
        )
        db.add(mission)
        db.commit()

        data = get_dashboard_data(ctx)
        mission_data = data["current_mission"]

        assert mission_data is not None
        required_mission_keys = ["title", "progress", "target"]
        for key in required_mission_keys:
            assert key in mission_data, f"Missing mission key: {key}"


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_dashboard_without_settings(self, db, ctx, account):
        """Dashboard handles account without settings gracefully."""
        from finapp.services.dashboard import get_dashboard_data

        # Don't set up settings; use raw ctx
        data = get_dashboard_data(ctx)

        # Should still return valid structure
        assert "greeting" in data
        assert "there" in data["greeting"]  # Default fallback

    def test_dashboard_with_savings_mission(self, db, ctx, setup_account, current_period):
        """Dashboard shows savings goal missions correctly."""
        from finapp.services.dashboard import get_dashboard_data

        goal = SavingsGoal(
            account_id=ctx.account_id,
            name="Vacation Fund",
            goal_type="sinking_fund",
            target_cents=100000,
            opening_balance_cents=0,
            cached_balance_cents=0,
        )
        db.add(goal)
        db.commit()

        mission = Mission(
            account_id=ctx.account_id,
            name="Vacation Savings",
            mission_type="savings_goal",
            link_type="savings",
            link_id=goal.id,
            status="active",
            sort_order=1,
        )
        db.add(mission)
        db.commit()

        data = get_dashboard_data(ctx)
        assert data["current_mission"] is not None
        assert "Vacation Fund" in data["current_mission"]["title"]

    def test_dashboard_zero_debt_progress(self, db, ctx, setup_account, current_period):
        """Dashboard shows 0% progress for unpaid debt."""
        from finapp.services.dashboard import get_dashboard_data

        debt = DebtAccount(
            account_id=ctx.account_id,
            name="New Debt",
            opening_balance_cents=10000,
            cached_balance_cents=10000,
            interest_rate_bps=1999,
            minimum_payment_cents=500,
        )
        db.add(debt)
        db.commit()

        mission = Mission(
            account_id=ctx.account_id,
            name="Pay off New Debt",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt.id,
            status="active",
            sort_order=1,
        )
        db.add(mission)
        db.commit()

        data = get_dashboard_data(ctx)
        assert data["current_mission"]["progress"] == 0

    def test_dashboard_full_debt_progress(self, db, ctx, setup_account, current_period):
        """Dashboard shows 100% progress for paid-off debt."""
        from finapp.services.dashboard import get_dashboard_data

        debt = DebtAccount(
            account_id=ctx.account_id,
            name="Paid Off Debt",
            opening_balance_cents=5000,
            cached_balance_cents=0,  # All paid
            interest_rate_bps=1999,
            minimum_payment_cents=500,
        )
        db.add(debt)
        db.commit()

        # Create a principal payment transaction to simulate payoff
        create_transaction(
            ctx,
            date=date.today(),
            amount_cents=5000,
            direction="in",
            link_type="debt",
            link_id=debt.id,
            principal_cents=5000,
        )

        mission = Mission(
            account_id=ctx.account_id,
            name="Pay off Paid Off Debt",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt.id,
            status="active",
            sort_order=1,
        )
        db.add(mission)
        db.commit()

        data = get_dashboard_data(ctx)
        assert data["current_mission"]["progress"] == 100

    def test_dashboard_without_current_period(self, db, ctx, setup_account):
        """Dashboard handles missing current period gracefully."""
        from finapp.services.dashboard import get_dashboard_data

        # Don't create current period
        data = get_dashboard_data(ctx)

        # Pulse should show zeros
        pulse = data["this_week_pulse"]
        assert pulse["spent_cents"] == 0
        assert pulse["target_cents"] == 0
        assert pulse["percentage"] == 0

    def test_dashboard_mission_sort_order(self, db, ctx, setup_account, current_period):
        """Dashboard returns first mission by sort_order, not by creation order."""
        from finapp.services.dashboard import get_dashboard_data

        # Create two missions with sort_order
        debt1 = DebtAccount(
            account_id=ctx.account_id,
            name="First Debt",
            opening_balance_cents=10000,
            cached_balance_cents=10000,
            interest_rate_bps=1999,
            minimum_payment_cents=500,
        )
        db.add(debt1)
        db.commit()

        debt2 = DebtAccount(
            account_id=ctx.account_id,
            name="Second Debt",
            opening_balance_cents=5000,
            cached_balance_cents=5000,
            interest_rate_bps=1999,
            minimum_payment_cents=250,
        )
        db.add(debt2)
        db.commit()

        # Create mission 2 first (but with higher sort order)
        mission2 = Mission(
            account_id=ctx.account_id,
            name="Second Mission",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt2.id,
            status="active",
            sort_order=2,
        )
        db.add(mission2)
        db.commit()

        # Create mission 1 second (but with lower sort order)
        mission1 = Mission(
            account_id=ctx.account_id,
            name="First Mission",
            mission_type="debt_payoff",
            link_type="debt",
            link_id=debt1.id,
            status="active",
            sort_order=1,
        )
        db.add(mission1)
        db.commit()

        data = get_dashboard_data(ctx)
        # Should return mission 1 due to sort_order
        assert "First Debt" in data["current_mission"]["title"]
