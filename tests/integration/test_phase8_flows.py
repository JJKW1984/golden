"""
Integration test: a scripted month touching missions, weekly review, and
monthly reset, asserting the reconciliation gate stays green throughout
(per CLAUDE.md's non-negotiable Done-gate requirement starting Phase 2).
"""
import os
import sqlite3
from datetime import date

from finapp.models import (
    DebtAccount, SavingsGoal, Mission, BudgetPeriod, Settings,
    BudgetCategory,
)
from finapp.services.ledger import create_transaction
from finapp.services.missions import get_active_missions, complete_mission
from finapp.services.reviews import (
    ensure_weekly_review_due, complete_weekly_review, close_month,
)
from finapp.services.reconciliation import recompute_balances


def test_full_phase8_month_flow(db, ctx, account, tmp_path, monkeypatch):
    monkeypatch.setattr("finapp.services.reviews.BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("finapp.services.reviews.SOURCE_DB_PATH", str(tmp_path / "finance.db"))
    sqlite3.connect(str(tmp_path / "finance.db")).close()

    settings = Settings(account_id=ctx.account_id, review_day=date.today().strftime("%A").lower())
    db.add(settings)

    debt = DebtAccount(
        account_id=ctx.account_id, name="Capital One", opening_balance_cents=20000,
        cached_balance_cents=20000, interest_rate_bps=0, minimum_payment_cents=2000,
    )
    db.add(debt)
    db.commit()

    mission = Mission(
        account_id=ctx.account_id, name="Pay off Capital One", mission_type="debt_payoff",
        link_type="debt", link_id=debt.id, status="active", sort_order=1,
    )
    db.add(mission)
    db.commit()

    period = BudgetPeriod(account_id=ctx.account_id, year=date.today().year, month=date.today().month)
    db.add(period)
    db.commit()

    # Log a debt payment — reconciliation must stay green
    create_transaction(
        ctx, date=date.today(), amount_cents=5000, direction="out",
        link_type="debt", link_id=debt.id, principal_cents=5000, interest_cents=0,
    )
    report = recompute_balances(db, ctx.account_id)
    assert report["total_drift"] == 0

    # Mission progress reflects the payment
    missions = get_active_missions(ctx)
    assert missions[0]["current_cents"] == 15000

    # Weekly review: due, complete it
    ensure_weekly_review_due(ctx)
    review = complete_weekly_review(ctx, intention="Keep paying down debt.", quick=False)
    assert review.notes == "Keep paying down debt."

    # Monthly close
    result = close_month(ctx, period_id=period.id, notes="Solid month.", sweep_to_mission=False)
    assert result["reconciliation"]["total_drift"] == 0
    assert os.path.exists(result["backup_path"])

    db.refresh(period)
    assert period.status == "closed"

    # Complete the mission and confirm it no longer appears in active list
    complete_mission(ctx, mission.id)
    assert get_active_missions(ctx) == []
