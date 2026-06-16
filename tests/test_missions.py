from datetime import date
from finapp.models import DebtAccount, SavingsGoal, Mission
from finapp.services.ledger import create_transaction
from finapp.services.missions import (
    get_mission_progress,
    get_active_missions,
    complete_mission,
    reorder_missions,
)


def _make_debt_mission(db, ctx, opening=10000, paid=4000):
    debt = DebtAccount(
        account_id=ctx.account_id, name="Capital One", opening_balance_cents=opening,
        cached_balance_cents=opening, interest_rate_bps=0, minimum_payment_cents=1000,
    )
    db.add(debt)
    db.commit()
    if paid:
        create_transaction(
            ctx, date=date.today(), amount_cents=paid, direction="out",
            link_type="debt", link_id=debt.id, principal_cents=paid, interest_cents=0,
        )
    mission = Mission(
        account_id=ctx.account_id, name="Pay off Capital One", mission_type="debt_payoff",
        link_type="debt", link_id=debt.id, status="active", sort_order=1,
    )
    db.add(mission)
    db.commit()
    return mission, debt


def test_debt_mission_progress_derives_from_linked_debt(db, ctx):
    mission, debt = _make_debt_mission(db, ctx, opening=10000, paid=4000)
    progress = get_mission_progress(ctx, mission)
    assert progress["start_cents"] == 10000
    assert progress["current_cents"] == 6000  # remaining balance
    assert progress["target_cents"] == 0
    assert progress["percent"] == 40  # 4000/10000 paid off


def test_savings_mission_progress_derives_from_linked_goal(db, ctx):
    goal = SavingsGoal(
        account_id=ctx.account_id, name="New Car", goal_type="sinking_fund",
        target_cents=10000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()
    create_transaction(
        ctx, date=date.today(), amount_cents=2500, direction="out",
        link_type="savings", link_id=goal.id,
    )
    mission = Mission(
        account_id=ctx.account_id, name="Save for car", mission_type="savings_goal",
        link_type="savings", link_id=goal.id, status="active", sort_order=1,
    )
    db.add(mission)
    db.commit()

    progress = get_mission_progress(ctx, mission)
    assert progress["start_cents"] == 0
    assert progress["current_cents"] == 2500
    assert progress["target_cents"] == 10000
    assert progress["percent"] == 25


def test_mission_progress_never_drifts_after_multiple_payments(db, ctx):
    mission, debt = _make_debt_mission(db, ctx, opening=10000, paid=4000)
    create_transaction(
        ctx, date=date.today(), amount_cents=1000, direction="out",
        link_type="debt", link_id=debt.id, principal_cents=1000, interest_cents=0,
    )
    progress = get_mission_progress(ctx, mission)
    assert progress["current_cents"] == 5000
    assert progress["percent"] == 50


def test_get_active_missions_orders_by_sort_order(db, ctx):
    m1, _ = _make_debt_mission(db, ctx)
    m1.sort_order = 2
    db.commit()
    goal = SavingsGoal(
        account_id=ctx.account_id, name="EF", goal_type="emergency_fund",
        target_cents=1000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()
    m2 = Mission(
        account_id=ctx.account_id, name="Build EF", mission_type="emergency_fund",
        link_type="savings", link_id=goal.id, status="active", sort_order=1,
    )
    db.add(m2)
    db.commit()

    active = get_active_missions(ctx)
    assert [m["id"] for m in active] == [m2.id, m1.id]


def test_complete_mission_sets_status_and_timestamp(db, ctx):
    mission, _ = _make_debt_mission(db, ctx)
    completed = complete_mission(ctx, mission.id)
    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_reorder_missions_sets_sort_order(db, ctx):
    m1, _ = _make_debt_mission(db, ctx)
    goal = SavingsGoal(
        account_id=ctx.account_id, name="Vacation", goal_type="sinking_fund",
        target_cents=1000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()
    m2 = Mission(
        account_id=ctx.account_id, name="Save vacation", mission_type="savings_goal",
        link_type="savings", link_id=goal.id, status="active", sort_order=1,
    )
    db.add(m2)
    db.commit()

    reorder_missions(ctx, [m2.id, m1.id])
    db.refresh(m1)
    db.refresh(m2)
    assert m2.sort_order < m1.sort_order


def test_reorder_rejects_emergency_fund_mission(db, ctx):
    goal = SavingsGoal(
        account_id=ctx.account_id, name="EF", goal_type="emergency_fund",
        target_cents=1000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()
    ef_mission = Mission(
        account_id=ctx.account_id, name="Build EF", mission_type="emergency_fund",
        link_type="savings", link_id=goal.id, status="active", sort_order=1,
    )
    db.add(ef_mission)
    db.commit()

    import pytest
    with pytest.raises(ValueError):
        reorder_missions(ctx, [ef_mission.id])
