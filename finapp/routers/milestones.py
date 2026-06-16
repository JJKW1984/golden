"""
Milestones router: HTTP endpoints for the Milestone Celebration flow (6.7).

Endpoints:
- GET  /milestone/{id}: Milestone detail for the celebration screen — JSON
- POST /milestone/{id}/celebrate: Flip celebrated=True (idempotent, fires once)
- GET  /api/milestones: Uncelebrated milestones — JSON
"""
from fastapi import APIRouter, Depends, HTTPException

from finapp.deps import get_account_context, AccountContext
from finapp.models import Milestone
from finapp.schemas import MilestoneDetailResponse, MilestoneCelebrateRequest

router = APIRouter(tags=["milestones"])


def _get_milestone_or_404(ctx: AccountContext, milestone_id: int) -> Milestone:
    milestone = ctx.db.query(Milestone).filter_by(
        id=milestone_id, account_id=ctx.account_id,
    ).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return milestone


@router.get("/milestone/{milestone_id}", response_model=MilestoneDetailResponse)
def get_milestone(milestone_id: int, ctx: AccountContext = Depends(get_account_context)) -> Milestone:
    return _get_milestone_or_404(ctx, milestone_id)


@router.post("/milestone/{milestone_id}/celebrate")
def post_milestone_celebrate(
    milestone_id: int,
    request: MilestoneCelebrateRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    milestone = _get_milestone_or_404(ctx, milestone_id)
    if not milestone.celebrated:
        milestone.celebrated = True
        if request.feeling:
            milestone.description = (milestone.description or "") + f"\n\nHow it felt: {request.feeling}"
        ctx.db.commit()
    return {"id": milestone.id, "celebrated": milestone.celebrated}


@router.get("/api/milestones", response_model=list[MilestoneDetailResponse])
def get_uncelebrated_milestones(ctx: AccountContext = Depends(get_account_context)) -> list[Milestone]:
    return ctx.db.query(Milestone).filter_by(
        account_id=ctx.account_id, celebrated=False,
    ).all()
