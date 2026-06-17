"""
Reconciliation harness for verifying that cached balances match pure-derived values.
This is the consistency check that runs on monthly close and in CI.
"""
from datetime import datetime, date, UTC
from decimal import Decimal
from sqlalchemy.orm import Session
from finapp.models import (
    Account,
    BudgetPeriod,
    BudgetCategory,
    Transaction,
    DebtAccount,
    SavingsGoal,
)


def compute_budget_spent_cents(
    db: Session, account_id: int, period_id: int, category_id: int
) -> int:
    """
    Pure-derived: sum of expense transactions (direction='out') for a category in a period.
    Excludes soft-deleted transactions.
    """
    spent = db.query(Transaction).filter(
        Transaction.account_id == account_id,
        Transaction.period_id == period_id,
        Transaction.category_id == category_id,
        Transaction.direction == "out",
        Transaction.is_deleted == False,
    ).all()

    total_cents = sum(t.amount_cents for t in spent)
    return total_cents


def compute_budget_income_received_cents(
    db: Session, account_id: int, period_id: int
) -> int:
    """
    Pure-derived: sum of income transactions (direction='in', category kind='income') for a period.
    Excludes soft-deleted transactions.
    """
    income_txns = db.query(Transaction).filter(
        Transaction.account_id == account_id,
        Transaction.period_id == period_id,
        Transaction.direction == "in",
        Transaction.is_deleted == False,
    ).join(BudgetCategory).filter(
        BudgetCategory.kind == "income"
    ).all()

    total_cents = sum(t.amount_cents for t in income_txns)
    return total_cents


def compute_debt_balance_cents(
    db: Session, account_id: int, debt_id: int
) -> int:
    """
    Pure-derived: opening_balance_cents - sum of principal paid via linked transactions.
    Interest does not reduce principal.
    """
    debt = db.query(DebtAccount).filter_by(
        account_id=account_id, id=debt_id
    ).first()

    if not debt:
        return 0

    # Sum all principal payments (debt-linked transactions with principal_cents set)
    principal_paid = db.query(Transaction).filter(
        Transaction.account_id == account_id,
        Transaction.link_type == "debt",
        Transaction.link_id == debt_id,
        Transaction.is_deleted == False,
    ).all()

    total_principal = sum(t.principal_cents or 0 for t in principal_paid)
    derived_balance = debt.opening_balance_cents - total_principal

    return max(0, derived_balance)  # Balance cannot go negative


def compute_savings_goal_balance_cents(
    db: Session, account_id: int, goal_id: int
) -> int:
    """
    Pure-derived: opening_balance_cents + sum of contributions - withdrawals.
    Contributions are positive (direction='out', link_type='savings', link_id=goal_id).
    Withdrawals are negative (direction='in', link_type='savings', link_id=goal_id).
    """
    goal = db.query(SavingsGoal).filter_by(
        account_id=account_id, id=goal_id
    ).first()

    if not goal:
        return 0

    # Contributions and withdrawals are linked transactions
    txns = db.query(Transaction).filter(
        Transaction.account_id == account_id,
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
    return max(0, derived_balance)  # Balance cannot go negative


def recompute_balances(db: Session, account_id: int) -> dict:
    """
    Rebuild all cached balances from the ledger and return a report of any drift.
    Does NOT update cached values; only detects and reports drift.

    Returns a dict with keys:
    - drifts: list of {table, id, field, cached, derived} showing any mismatches
    - total_drift: count of drifted values
    """
    drifts = []

    # Check BudgetPeriod.income_received_cents
    periods = db.query(BudgetPeriod).filter_by(account_id=account_id).all()
    for period in periods:
        derived = compute_budget_income_received_cents(db, account_id, period.id)
        if period.income_received_cents != derived:
            drifts.append({
                "table": "BudgetPeriod",
                "id": period.id,
                "field": "income_received_cents",
                "cached": period.income_received_cents,
                "derived": derived,
            })

    # Check DebtAccount.cached_balance_cents
    debts = db.query(DebtAccount).filter_by(account_id=account_id).all()
    for debt in debts:
        derived = compute_debt_balance_cents(db, account_id, debt.id)
        if debt.cached_balance_cents != derived:
            drifts.append({
                "table": "DebtAccount",
                "id": debt.id,
                "field": "cached_balance_cents",
                "cached": debt.cached_balance_cents,
                "derived": derived,
            })

    # Check SavingsGoal.cached_balance_cents
    goals = db.query(SavingsGoal).filter_by(account_id=account_id).all()
    for goal in goals:
        derived = compute_savings_goal_balance_cents(db, account_id, goal.id)
        if goal.cached_balance_cents != derived:
            drifts.append({
                "table": "SavingsGoal",
                "id": goal.id,
                "field": "cached_balance_cents",
                "cached": goal.cached_balance_cents,
                "derived": derived,
            })

    return {
        "drifts": drifts,
        "total_drift": len(drifts),
    }


def apply_recomputed_balances(db: Session, account_id: int) -> dict:
    """
    Rebuild all cached balances and UPDATE them in the database.
    Used after transaction writes to keep caches fresh.
    Fires debt_paid_off / savings_goal_reached milestones the moment a
    balance crosses into its completed state (idempotent via
    services/milestones.py).
    Returns the drift report.
    """
    from finapp.deps import AccountContext
    from finapp.services.milestones import create_milestone_if_new

    ctx = AccountContext(account_id=account_id, db=db)

    # Update BudgetPeriod.income_received_cents
    periods = db.query(BudgetPeriod).filter_by(account_id=account_id).all()
    for period in periods:
        derived = compute_budget_income_received_cents(db, account_id, period.id)
        period.income_received_cents = derived

    # Update DebtAccount.cached_balance_cents
    debts = db.query(DebtAccount).filter_by(account_id=account_id).all()
    for debt in debts:
        derived = compute_debt_balance_cents(db, account_id, debt.id)
        debt.cached_balance_cents = derived
        if derived == 0 and debt.paid_off_at is None:
            debt.paid_off_at = datetime.now(UTC)
            create_milestone_if_new(
                ctx,
                milestone_type="debt_paid_off",
                title=f"{debt.name} paid off!",
                description=f"You paid off {debt.name}.",
                link_type="debt",
                link_id=debt.id,
            )

    # Update SavingsGoal.cached_balance_cents
    goals = db.query(SavingsGoal).filter_by(account_id=account_id).all()
    for goal in goals:
        derived = compute_savings_goal_balance_cents(db, account_id, goal.id)
        goal.cached_balance_cents = derived
        if not goal.is_complete and derived >= goal.target_cents and goal.target_cents > 0:
            goal.is_complete = True
            goal.completed_at = datetime.now(UTC)
            create_milestone_if_new(
                ctx,
                milestone_type="savings_goal_reached",
                title=f"{goal.name} goal reached!",
                description=f"You reached your {goal.name} goal.",
                link_type="savings",
                link_id=goal.id,
            )

    db.commit()

    # Run reconciliation check to verify zero drift after update
    return recompute_balances(db, account_id)
