"""
Dashboard service: assembles comprehensive dashboard data from ledger, allocation, streak, and next_right_action.

Returns dashboard data structure:
{
    'greeting': str,          # "Good morning/afternoon/evening, {name}"
    'date': str,              # "Monday, June 14"
    'streak': int,
    'current_mission': {      # Read-only stub
        'title': str,
        'progress': int,       # 0-100
        'target': str,
    } or None,
    'this_week_pulse': {
        'spent_cents': int,
        'target_cents': int,
        'percentage': int,     # capped at 100 for the progress bar
        'usage_percentage': int,
        'is_over_budget': bool,
        'remaining_cents': int,
    },
    'next_right_action': {
        'action': str,
        'label': str,
        'hint': str,
    },
}
"""
from datetime import date, datetime, timedelta
from sqlalchemy import func
from finapp.deps import AccountContext
from finapp.models import (
    Settings,
    BudgetPeriod,
    BudgetCategory,
    BudgetAllocation,
    Transaction,
    DebtAccount,
    SavingsGoal,
    Mission,
)
from finapp.services.streak import calculate_streak
from finapp.services.next_right_action import get_next_right_action
from finapp.services.balances import get_debt_balance_cents, get_savings_goal_balance_cents


def get_dashboard_data(ctx: AccountContext) -> dict:
    """
    Assemble comprehensive dashboard data from multiple services.

    Returns:
        dict: Dashboard data with greeting, date, streak, mission, pulse, and next action.
    """
    # 1. Greeting: time-based with user name
    greeting = _get_greeting(ctx)

    # 2. Date: formatted as "Monday, June 14"
    date_str = _format_date(date.today())

    # 3. Streak: from streak service
    streak = calculate_streak(ctx)

    # 4. Current Mission (stub): first active mission with progress
    current_mission = _get_current_mission(ctx)

    # 5. This Week Pulse: spending vs target for current week
    this_week_pulse = _get_this_week_pulse(ctx)

    # 6. Next Right Action: from next_right_action service
    next_right_action = get_next_right_action(ctx)

    return {
        "greeting": greeting,
        "date": date_str,
        "streak": streak,
        "current_mission": current_mission,
        "this_week_pulse": this_week_pulse,
        "next_right_action": next_right_action,
    }


def _get_greeting(ctx: AccountContext) -> str:
    """
    Generate time-based greeting with user name.

    Time periods:
    - 5am-12pm: "Good morning"
    - 12pm-5pm: "Good afternoon"
    - 5pm-5am: "Good evening"

    Returns:
        str: "Good {period}, {name}"
    """
    settings = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).first()
    name = settings.user_name if settings else "there"

    now = datetime.now()
    hour = now.hour

    if 5 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    else:
        period = "evening"

    return f"Good {period}, {name}"


def _format_date(d: date) -> str:
    """
    Format date as "Monday, June 14".

    Args:
        d: date object

    Returns:
        str: Formatted date string
    """
    day_name = d.strftime("%A")
    month_day = d.strftime("%B %d").lstrip("0").replace(" 0", " ")
    return f"{day_name}, {month_day}"


def _get_current_mission(ctx: AccountContext) -> dict | None:
    """
    Get first active mission (excluding emergency_fund type) with progress stub.

    Returns:
        dict with 'title', 'progress', 'target', or None if no active missions.
    """
    # Fetch first active mission (sorted by sort_order, not emergency_fund)
    mission = ctx.db.query(Mission).filter(
        Mission.account_id == ctx.account_id,
        Mission.status == "active",
        Mission.mission_type != "emergency_fund",  # Phase 4 stub: exclude EF
    ).order_by(Mission.sort_order).first()

    if not mission:
        return None

    # Build title from mission and linked entity
    title = mission.name
    progress = 0
    target = ""

    if mission.link_type == "debt" and mission.link_id:
        # Debt mission: get debt account name
        debt = ctx.db.query(DebtAccount).filter_by(
            account_id=ctx.account_id, id=mission.link_id
        ).first()

        if debt:
            title = f"{mission.name} - {debt.name}"
            # Progress: (opening - balance) / opening * 100
            balance = get_debt_balance_cents(ctx, debt.id)
            paid = debt.opening_balance_cents - balance
            if debt.opening_balance_cents > 0:
                progress = min(100, int(paid * 100 / debt.opening_balance_cents))
            target = f"${debt.opening_balance_cents / 100:.2f}"

    elif mission.link_type == "savings" and mission.link_id:
        # Savings mission: get goal name
        goal = ctx.db.query(SavingsGoal).filter_by(
            account_id=ctx.account_id, id=mission.link_id
        ).first()

        if goal:
            title = f"{mission.name} - {goal.name}"
            # Progress: balance / target * 100
            balance = get_savings_goal_balance_cents(ctx, goal.id)
            if goal.target_cents > 0:
                progress = min(100, int(balance * 100 / goal.target_cents))
            target = f"${goal.target_cents / 100:.2f}"

    return {
        "title": title,
        "progress": progress,
        "target": target,
    }


def _get_this_week_pulse(ctx: AccountContext) -> dict:
    """
    Calculate this week's spending vs prorated target.

    Week is Monday (weekday 0) through Sunday (weekday 6).

    Returns:
        dict with 'spent_cents', 'target_cents', 'percentage'
    """
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday of current week

    # Get current period
    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id,
        year=today.year,
        month=today.month,
    ).first()

    if not period:
        # No period yet; return zeros
        return {
            "spent_cents": 0,
            "target_cents": 0,
            "percentage": 0,
            "usage_percentage": 0,
            "is_over_budget": False,
            "remaining_cents": 0,
        }

    # Sum all spending transactions this week
    spending_categories = ctx.db.query(BudgetCategory.id).filter(
        BudgetCategory.account_id == ctx.account_id,
        BudgetCategory.kind == "spending",
    ).all()

    category_ids = [cat[0] for cat in spending_categories]

    spent = ctx.db.query(func.sum(Transaction.amount_cents)).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.period_id == period.id,
        Transaction.direction == "out",
        Transaction.date >= week_start,
        Transaction.date <= (week_start + timedelta(days=6)),
        Transaction.category_id.in_(category_ids) if category_ids else False,
        Transaction.is_deleted == False,
    ).scalar()

    spent_cents = spent or 0

    # Sum targets for spending categories this period
    target = ctx.db.query(func.sum(BudgetAllocation.target_cents)).filter(
        BudgetAllocation.account_id == ctx.account_id,
        BudgetAllocation.period_id == period.id,
        BudgetAllocation.category_id.in_(category_ids) if category_ids else False,
    ).scalar()

    target_cents = target or 0

    # Prorate monthly target to weekly
    # Assume 4.3 weeks per month for prorating
    weekly_target = int((target_cents or 0) / 4.3) if target_cents else 0

    # Calculate usage percentage and display width separately.
    usage_percentage = 0
    if weekly_target > 0:
        usage_percentage = int(spent_cents * 100 / weekly_target)

    return {
        "spent_cents": spent_cents,
        "target_cents": weekly_target,
        "percentage": min(100, usage_percentage),
        "usage_percentage": usage_percentage,
        "is_over_budget": usage_percentage > 100,
        "remaining_cents": weekly_target - spent_cents,
    }
