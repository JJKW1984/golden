"""
Next Right Action service: determines the primary action to prompt users with on the dashboard.

Priority order (first match wins):
1. setup_complete = FALSE → "Complete Setup"
2. No income logged this period → "Log Income"
3. Unallocated income > 0 → "Allocate Paychecks"
4. Review due today → "Do Weekly Review"
5. Monthly reset available (1st–3rd, prior month open) → "Close Previous Month"
6. Active-mission debt payment not logged this month → "Log Debt Payment"
7. Last transaction >5 days ago → "Log Recent Activity"
8. (default) → "You're on track"
"""
from datetime import date, datetime, timedelta, UTC
from sqlalchemy import func
from sqlalchemy.orm import Session

from finapp.deps import AccountContext
from finapp.models import (
    Settings,
    BudgetPeriod,
    BudgetCategory,
    BudgetAllocation,
    Transaction,
    DebtAccount,
    Mission,
    Review,
)
from finapp.services.balances import get_budget_income_received_cents


def _get_current_period(ctx: AccountContext) -> BudgetPeriod:
    """Get or create the current month's budget period."""
    today = date.today()
    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id,
        year=today.year,
        month=today.month,
    ).first()
    return period


def _get_prior_period(ctx: AccountContext) -> BudgetPeriod:
    """Get the prior month's budget period if it exists."""
    today = date.today()
    if today.month == 1:
        prior_year = today.year - 1
        prior_month = 12
    else:
        prior_year = today.year
        prior_month = today.month - 1

    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id,
        year=prior_year,
        month=prior_month,
    ).first()
    return period


def _has_income_this_period(ctx: AccountContext, period: BudgetPeriod) -> bool:
    """Check if any income has been logged in this period."""
    if not period:
        return False

    income_received = get_budget_income_received_cents(ctx, period.id)
    return income_received > 0


def _get_unallocated_cents(ctx: AccountContext, period: BudgetPeriod) -> int:
    """
    Calculate unallocated income: total received - sum of targets.
    """
    if not period:
        return 0

    income_received = get_budget_income_received_cents(ctx, period.id)

    # Sum all targets for this period
    total_target = ctx.db.query(func.sum(BudgetAllocation.target_cents)).filter(
        BudgetAllocation.account_id == ctx.account_id,
        BudgetAllocation.period_id == period.id,
    ).scalar()

    total_target = total_target or 0
    unallocated = income_received - total_target

    return max(0, unallocated)  # Never negative


def _review_due_today(ctx: AccountContext) -> bool:
    """
    Check if there's a review record with no prompt_shown for this week.
    A review with prompt_shown=NULL means it hasn't been shown yet.
    """
    today = date.today()
    week_start = today - timedelta(days=today.weekday())

    review = ctx.db.query(Review).filter(
        Review.account_id == ctx.account_id,
        Review.review_type == "weekly",
        Review.week_start == week_start,
        Review.prompt_shown.is_(None),
    ).first()

    return review is not None


def _is_reset_window_open(ctx: AccountContext) -> bool:
    """
    Check if we're in the 1st–3rd of the month AND the prior month is still open.
    """
    today = date.today()

    # Check if we're in 1st–3rd
    if today.day < 1 or today.day > 3:
        return False

    # Check if prior month is open
    prior_period = _get_prior_period(ctx)
    if not prior_period or prior_period.status != "active":
        return False

    return True


def _active_mission_debt_payment_due(ctx: AccountContext, period: BudgetPeriod) -> bool:
    """
    Check if there's an active debt mission AND no debt payment has been logged this month.
    """
    if not period:
        return False

    # Find active debt missions for this account
    active_debt_mission = ctx.db.query(Mission).filter(
        Mission.account_id == ctx.account_id,
        Mission.mission_type == "debt_payoff",
        Mission.status == "active",
        Mission.link_type == "debt",
    ).first()

    if not active_debt_mission or not active_debt_mission.link_id:
        return False

    # Check if a debt payment exists this month for this debt
    debt_payment = ctx.db.query(Transaction).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.period_id == period.id,
        Transaction.link_type == "debt",
        Transaction.link_id == active_debt_mission.link_id,
        Transaction.is_deleted == False,
    ).first()

    return debt_payment is None


def _days_since_last_transaction(ctx: AccountContext) -> int:
    """
    Calculate days since the last transaction (most recent created_at).
    Returns 0 if no transactions exist, or a very large number if the last txn is old.
    """
    last_txn = ctx.db.query(Transaction).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.is_deleted == False,
    ).order_by(Transaction.created_at.desc()).first()

    if not last_txn:
        return 0

    days_ago = (datetime.now(UTC).date() - last_txn.date).days
    return days_ago


def get_next_right_action(ctx: AccountContext) -> dict:
    """
    Determine the primary action to prompt the user with on the dashboard.

    Returns a dict with:
    - action: str (one of: complete_setup, log_income, allocate_paychecks,
                   do_review, close_previous_month, log_debt_payment,
                   log_recent_activity, on_track)
    - label: str (user-facing label)
    - hint: str (optional hint text)

    Priority order (first match wins):
    1. setup_complete = FALSE → "Complete Setup"
    2. No income logged this period → "Log Income"
    3. Unallocated income > 0 → "Allocate Paychecks"
    4. Review due today → "Do Weekly Review"
    5. Monthly reset available (1st–3rd, prior month open) → "Close Previous Month"
    6. Active-mission debt payment not logged this month → "Log Debt Payment"
    7. Last transaction >5 days ago → "Log Recent Activity"
    8. (default) → "You're on track"
    """

    # Priority 1: Setup incomplete
    settings = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).first()
    if not settings or not settings.setup_complete:
        return {
            'action': 'complete_setup',
            'label': 'Complete Setup',
            'hint': 'Let\'s set up your account to get started.',
        }

    current_period = _get_current_period(ctx)

    # Priority 2: No income logged
    if not _has_income_this_period(ctx, current_period):
        return {
            'action': 'log_income',
            'label': 'Log Income',
            'hint': 'Record your paycheck to start allocating.',
        }

    # Priority 3: Unallocated income
    unallocated = _get_unallocated_cents(ctx, current_period)
    if unallocated > 0:
        return {
            'action': 'allocate_paychecks',
            'label': 'Allocate Paychecks',
            'hint': f'You have ${unallocated / 100:.2f} unallocated.',
        }

    # Priority 4: Review due today
    if _review_due_today(ctx):
        return {
            'action': 'do_review',
            'label': 'Do Weekly Review',
            'hint': 'Take 2 minutes to reflect on your week.',
        }

    # Priority 5: Monthly reset available
    if _is_reset_window_open(ctx):
        return {
            'action': 'close_previous_month',
            'label': 'Close Previous Month',
            'hint': 'Wrap up last month to start fresh.',
        }

    # Priority 6: Active debt mission payment not logged
    if current_period and _active_mission_debt_payment_due(ctx, current_period):
        return {
            'action': 'log_debt_payment',
            'label': 'Log Debt Payment',
            'hint': 'Keep up with your debt payoff mission.',
        }

    # Priority 7: Last transaction >5 days ago
    days_since = _days_since_last_transaction(ctx)
    if days_since > 5:
        return {
            'action': 'log_recent_activity',
            'label': 'Log Recent Activity',
            'hint': f'It\'s been {days_since} days. Any recent transactions?',
        }

    # Priority 8: Default (you're on track)
    return {
        'action': 'on_track',
        'label': 'You\'re on track',
        'hint': 'Keep up the good work!',
    }
