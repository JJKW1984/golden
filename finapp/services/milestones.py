"""
Milestone helper: idempotent creation shared by every milestone trigger
(debt paid off, savings goal reached, first budget, first review, streak
thresholds in services/streak.py).

Idempotency relies on the (account_id, milestone_type, threshold, link_id)
unique constraint (models.Milestone.__table_args__). SQLite treats NULL as
distinct from NULL in unique constraints, so triggers that have no natural
threshold (debt paid off, savings goal reached, first budget, first review)
must pass threshold=0 rather than None to make the constraint actually
enforce uniqueness.
"""
from finapp.deps import AccountContext
from finapp.models import Milestone


def create_milestone_if_new(
    ctx: AccountContext,
    milestone_type: str,
    title: str,
    description: str = None,
    threshold: int = 0,
    link_type: str = None,
    link_id: int = None,
) -> Milestone | None:
    """
    Create a Milestone if one matching (milestone_type, threshold, link_id)
    doesn't already exist for this account. Returns the new Milestone, or
    None if it already existed (no-op).
    """
    existing = ctx.db.query(Milestone).filter_by(
        account_id=ctx.account_id,
        milestone_type=milestone_type,
        threshold=threshold,
        link_id=link_id,
    ).first()

    if existing:
        return None

    milestone = Milestone(
        account_id=ctx.account_id,
        milestone_type=milestone_type,
        title=title,
        description=description,
        threshold=threshold,
        link_type=link_type,
        link_id=link_id,
        celebrated=False,
    )
    ctx.db.add(milestone)
    ctx.db.commit()

    return milestone
