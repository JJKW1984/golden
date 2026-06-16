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
