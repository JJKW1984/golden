"""
Balances service: compute derived values from transactions.
All balances are pure-derived from the ledger; never stored as truth.
"""
from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from finapp.models import (
    Transaction,
    BudgetCategory,
    BudgetPeriod,
    BudgetAllocation,
    DebtAccount,
    SavingsGoal,
    AssetAccount,
)
from finapp.deps import AccountContext


def get_budget_spent_cents(
    ctx: AccountContext, period_id: int, category_id: int
) -> int:
    """
    Derive: sum of expense transactions (direction='out') for a category in a period.
    Excludes soft-deleted transactions.
    """
    total = ctx.db.query(func.sum(Transaction.amount_cents)).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.period_id == period_id,
        Transaction.category_id == category_id,
        Transaction.direction == "out",
        Transaction.is_deleted == False,
    ).scalar()

    return total or 0


def get_budget_income_received_cents(ctx: AccountContext, period_id: int) -> int:
    """
    Derive: sum of income transactions (direction='in', category kind='income') for a period.
    Excludes soft-deleted transactions.
    """
    total = ctx.db.query(func.sum(Transaction.amount_cents)).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.period_id == period_id,
        Transaction.direction == "in",
        Transaction.is_deleted == False,
    ).join(BudgetCategory).filter(
        BudgetCategory.kind == "income"
    ).scalar()

    return total or 0


def get_debt_balance_cents(ctx: AccountContext, debt_id: int) -> int:
    """
    Derive: opening_balance_cents - sum of principal paid via linked transactions.
    Interest does not reduce principal. Balance cannot go negative.
    """
    debt = ctx.db.query(DebtAccount).filter_by(
        account_id=ctx.account_id, id=debt_id
    ).first()

    if not debt:
        return 0

    # Sum all principal payments (debt-linked transactions)
    total_principal = ctx.db.query(func.sum(Transaction.principal_cents)).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.link_type == "debt",
        Transaction.link_id == debt_id,
        Transaction.is_deleted == False,
    ).scalar()

    total_principal = total_principal or 0
    derived_balance = debt.opening_balance_cents - total_principal

    return max(0, derived_balance)


def get_savings_goal_balance_cents(ctx: AccountContext, goal_id: int) -> int:
    """
    Derive: opening_balance_cents + sum of contributions - withdrawals.
    Contributions are direction='out', withdrawals are direction='in'.
    Balance cannot go negative.
    """
    goal = ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, id=goal_id
    ).first()

    if not goal:
        return 0

    # Get all linked transactions (contributions and withdrawals)
    txns = ctx.db.query(Transaction).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.link_type == "savings",
        Transaction.link_id == goal_id,
        Transaction.is_deleted == False,
    ).all()

    total_change = 0
    for t in txns:
        if t.direction == "out":
            # Contribution (money going into savings)
            total_change += t.amount_cents
        elif t.direction == "in":
            # Withdrawal (money coming out of savings)
            total_change -= t.amount_cents

    derived_balance = goal.opening_balance_cents + total_change
    return max(0, derived_balance)


def get_current_period_spending_target_cents(ctx: AccountContext) -> int:
    """
    Sum of target_cents for kind='spending' categories in the current calendar-month
    period. Returns 0 if no period or no allocations exist yet (no side effects —
    does not create a period).
    """
    today = date.today()
    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, year=today.year, month=today.month
    ).first()

    if not period:
        return 0

    total = ctx.db.query(func.sum(BudgetAllocation.target_cents)).filter(
        BudgetAllocation.account_id == ctx.account_id,
        BudgetAllocation.period_id == period.id,
    ).join(BudgetCategory, BudgetAllocation.category_id == BudgetCategory.id).filter(
        BudgetCategory.kind == "spending",
    ).scalar()

    return total or 0


def get_days_of_expenses_coverage(ctx: AccountContext, goal_id: int) -> tuple[int, bool]:
    """
    Derive: days of expenses coverage from emergency fund balance (spec §4.14).

    avg_daily_expense = (sum of kind='spending' category transactions over the
    trailing 90 days) / 90.

    If fewer than 30 days of spending history exist, fall back to
    (sum of current-period spending category targets) / 30, labeled "estimated".

    Returns:
        (days_of_coverage, estimated) tuple. estimated=True when the fallback was used.
    """
    balance = get_savings_goal_balance_cents(ctx, goal_id)

    today = date.today()
    days_back = 90
    cutoff_date = today - timedelta(days=days_back)

    spending_txns = ctx.db.query(Transaction).join(
        BudgetCategory, Transaction.category_id == BudgetCategory.id
    ).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.date >= cutoff_date,
        Transaction.direction == "out",
        Transaction.is_deleted == False,
        BudgetCategory.kind == "spending",
    ).all()

    earliest_date = min((t.date for t in spending_txns), default=None)
    latest_date = max((t.date for t in spending_txns), default=None)
    days_with_data = (latest_date - earliest_date).days + 1 if earliest_date else 0

    if days_with_data >= 30:
        total_spent = sum(t.amount_cents for t in spending_txns)
        daily_avg = total_spent // days_with_data
        estimated = False
    else:
        target_total = get_current_period_spending_target_cents(ctx)
        daily_avg = target_total // 30
        estimated = True

    if daily_avg == 0:
        return (0, estimated)

    return (balance // daily_avg, estimated)


def get_net_worth_cents(ctx: AccountContext) -> int:
    """
    Derive: sum of asset balances - sum of derived debt balances.
    Assets are manually updated snapshots; debts are derived from transactions.
    """
    # Sum active asset accounts
    assets = ctx.db.query(func.sum(AssetAccount.balance_cents)).filter(
        AssetAccount.account_id == ctx.account_id,
        AssetAccount.is_active == True,
    ).scalar()

    assets = assets or 0

    # Sum derived debt balances
    debts = ctx.db.query(DebtAccount).filter_by(
        account_id=ctx.account_id, is_active=True
    ).all()

    total_debt = sum(get_debt_balance_cents(ctx, d.id) for d in debts)

    return assets - total_debt


def get_essentials_target_cents(ctx: AccountContext, period_id: int) -> int:
    """
    Helper: sum of essential category targets (Debt, Emergency Fund, Housing, Food, Transportation).
    Used for triage logic.
    """
    from finapp.services.allocation import compute_essentials_target_cents
    return compute_essentials_target_cents(ctx, period_id)


def estimate_savings_completion(ctx: AccountContext, goal_id: int) -> tuple[int | None, str | None]:
    """
    Estimate months remaining and a target completion date for a savings goal,
    based on the trailing-90-day average monthly contribution rate.

    Returns (None, None) if the goal is already complete or has no contribution
    history in the trailing 90 days (nothing to estimate from — calm by design,
    no guilt copy for "not enough data").
    """
    goal = ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, id=goal_id
    ).first()

    if not goal or goal.is_complete:
        return (None, None)

    balance = get_savings_goal_balance_cents(ctx, goal_id)
    remaining = goal.target_cents - balance
    if remaining <= 0:
        return (None, None)

    today = date.today()
    cutoff_date = today - timedelta(days=90)

    contributions = ctx.db.query(Transaction).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.link_type == "savings",
        Transaction.link_id == goal_id,
        Transaction.direction == "out",
        Transaction.date >= cutoff_date,
        Transaction.is_deleted == False,
    ).all()

    total_contributed = sum(t.amount_cents for t in contributions)
    if total_contributed <= 0:
        return (None, None)

    avg_monthly_cents = total_contributed // 3  # trailing-90-day window ~= 3 months
    if avg_monthly_cents <= 0:
        return (None, None)

    months = -(-remaining // avg_monthly_cents)  # ceiling division
    est_date = (today + timedelta(days=30 * months)).isoformat()

    return (months, est_date)
