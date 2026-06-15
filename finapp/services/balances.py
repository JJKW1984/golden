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


def get_days_of_expenses_coverage(ctx: AccountContext, goal_id: int) -> int:
    """
    Derive: days of expenses coverage from emergency fund balance.
    Uses trailing 90-day average daily spending.
    If less than 30 days of data, estimates from available data.
    Returns estimated number of days covered.
    """
    goal = ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, id=goal_id
    ).first()

    if not goal:
        return 0

    balance = get_savings_goal_balance_cents(ctx, goal_id)

    # Get all spending transactions (non-income, non-linked) from last 90 days
    today = date.today()
    days_back = 90
    cutoff_date = today - timedelta(days=days_back)

    spending_txns = ctx.db.query(Transaction).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.date >= cutoff_date,
        Transaction.direction == "out",
        Transaction.link_type == None,  # Regular expenses, not debt/savings
        Transaction.is_deleted == False,
    ).all()

    if not spending_txns:
        # No spending data, estimate can't be calculated
        return 0

    total_spent = sum(t.amount_cents for t in spending_txns)

    # Get the date range of available data
    min_date = min(t.date for t in spending_txns)
    max_date = max(t.date for t in spending_txns)
    days_with_data = (max_date - min_date).days + 1

    if days_with_data < 30:
        # Less than 30 days of data: estimate from what we have
        if days_with_data > 0:
            daily_avg = total_spent // days_with_data if days_with_data > 0 else 1
        else:
            return 0
    else:
        # At least 30 days: use trailing 90-day average
        daily_avg = total_spent // days_back if days_back > 0 else 1

    if daily_avg == 0:
        return 0

    return balance // daily_avg


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
