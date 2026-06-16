"""
Missions router: HTTP endpoints for the mission queue.

Endpoints:
- GET  /api/missions/active: Active missions with derived progress — JSON
- POST /missions/{id}/complete: Mark a mission completed
- POST /missions/reorder: Reorder the mission queue (non-emergency-fund only)
"""
from fastapi import APIRouter, Depends, HTTPException

from finapp.deps import get_account_context, AccountContext
from finapp.schemas import MissionProgressResponse, MissionReorderRequest
from finapp.services.missions import get_active_missions, complete_mission, reorder_missions

router = APIRouter(tags=["missions"])


@router.get("/api/missions/active", response_model=list[MissionProgressResponse])
def get_missions_active(ctx: AccountContext = Depends(get_account_context)) -> list[dict]:
    return get_active_missions(ctx)


@router.post("/missions/{mission_id}/complete")
def post_mission_complete(
    mission_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    try:
        mission = complete_mission(ctx, mission_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": mission.id, "status": mission.status}


@router.post("/missions/reorder")
def post_missions_reorder(
    request: MissionReorderRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    try:
        missions = reorder_missions(ctx, request.ordered_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"reordered": [m.id for m in missions]}
