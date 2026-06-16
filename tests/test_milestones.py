from finapp.models import Milestone
from finapp.services.milestones import create_milestone_if_new


def test_creates_new_milestone(db, ctx):
    m = create_milestone_if_new(
        ctx,
        milestone_type="debt_paid_off",
        title="Capital One paid off!",
        description="You paid off Capital One.",
        link_type="debt",
        link_id=42,
    )
    assert m is not None
    assert m.milestone_type == "debt_paid_off"
    assert m.threshold == 0
    assert m.celebrated is False

    fetched = db.query(Milestone).filter_by(account_id=ctx.account_id).all()
    assert len(fetched) == 1


def test_idempotent_does_not_duplicate(db, ctx):
    create_milestone_if_new(
        ctx, milestone_type="debt_paid_off", title="x", description="x",
        link_type="debt", link_id=42,
    )
    second = create_milestone_if_new(
        ctx, milestone_type="debt_paid_off", title="x", description="x",
        link_type="debt", link_id=42,
    )
    assert second is None
    fetched = db.query(Milestone).filter_by(account_id=ctx.account_id).all()
    assert len(fetched) == 1


def test_different_link_id_creates_separate_milestone(db, ctx):
    create_milestone_if_new(
        ctx, milestone_type="debt_paid_off", title="x", description="x",
        link_type="debt", link_id=1,
    )
    second = create_milestone_if_new(
        ctx, milestone_type="debt_paid_off", title="y", description="y",
        link_type="debt", link_id=2,
    )
    assert second is not None
    fetched = db.query(Milestone).filter_by(account_id=ctx.account_id).all()
    assert len(fetched) == 2


from datetime import date
from finapp.models import DebtAccount, SavingsGoal
from finapp.services.ledger import create_transaction
from finapp.services.reconciliation import apply_recomputed_balances


def test_debt_paid_off_milestone_fires_once(db, ctx):
    debt = DebtAccount(
        account_id=ctx.account_id, name="Capital One", opening_balance_cents=10000,
        cached_balance_cents=10000, interest_rate_bps=0, minimum_payment_cents=1000,
    )
    db.add(debt)
    db.commit()

    create_transaction(
        ctx, date=date.today(), amount_cents=10000, direction="out",
        link_type="debt", link_id=debt.id, principal_cents=10000, interest_cents=0,
    )

    from finapp.models import Milestone
    milestones = db.query(Milestone).filter_by(
        account_id=ctx.account_id, milestone_type="debt_paid_off", link_id=debt.id,
    ).all()
    assert len(milestones) == 1

    # A second recompute must not duplicate it
    apply_recomputed_balances(db, ctx.account_id)
    milestones = db.query(Milestone).filter_by(
        account_id=ctx.account_id, milestone_type="debt_paid_off", link_id=debt.id,
    ).all()
    assert len(milestones) == 1


def test_savings_goal_reached_milestone_fires_once(db, ctx):
    goal = SavingsGoal(
        account_id=ctx.account_id, name="Emergency Fund", goal_type="emergency_fund",
        target_cents=5000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()

    create_transaction(
        ctx, date=date.today(), amount_cents=5000, direction="out",
        link_type="savings", link_id=goal.id,
    )

    from finapp.models import Milestone
    milestones = db.query(Milestone).filter_by(
        account_id=ctx.account_id, milestone_type="savings_goal_reached", link_id=goal.id,
    ).all()
    assert len(milestones) == 1

    apply_recomputed_balances(db, ctx.account_id)
    milestones = db.query(Milestone).filter_by(
        account_id=ctx.account_id, milestone_type="savings_goal_reached", link_id=goal.id,
    ).all()
    assert len(milestones) == 1


def test_first_budget_milestone_fires_on_first_allocation_router_call(
    test_client_for_budget,
):
    from finapp.models import BudgetCategory, BudgetPeriod, Milestone
    from finapp.main import app
    from finapp.deps import get_db

    client = test_client_for_budget
    db = next(app.dependency_overrides[get_db]())

    period = BudgetPeriod(account_id=1, year=2026, month=6, income_received_cents=0)
    db.add(period)
    db.commit()
    cat = BudgetCategory(account_id=1, name="Food", kind="spending")
    db.add(cat)
    db.commit()

    resp = client.post("/api/allocation/targets", json={
        "period_id": period.id,
        "allocations": [{"category_id": cat.id, "target_cents": 1000}],
    })
    assert resp.status_code == 200

    milestones = db.query(Milestone).filter_by(
        account_id=1, milestone_type="first_budget",
    ).all()
    assert len(milestones) == 1

    # A second allocation call must not duplicate it
    client.post("/api/allocation/targets", json={
        "period_id": period.id,
        "allocations": [{"category_id": cat.id, "target_cents": 2000}],
    })
    milestones = db.query(Milestone).filter_by(
        account_id=1, milestone_type="first_budget",
    ).all()
    assert len(milestones) == 1
