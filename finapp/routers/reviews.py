"""
Reviews router: HTTP endpoints for Weekly Review (6.5) and Monthly Reset (6.6).

Endpoints:
- GET  /reviews: Reviews hub — HTML
- GET  /reviews/weekly: Weekly review screen data — JSON
- POST /reviews/weekly: Complete the weekly review
- GET  /reviews/monthly: Monthly reset summary data — JSON
- POST /reviews/monthly: Close the month (period close + recompute + backup)
- POST /reviews/start-month: T1 "Start this month" abbreviated reset shortcut
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from finapp.deps import get_account_context, AccountContext
from finapp.schemas import WeeklyReviewCompleteRequest, MonthlyReviewCloseRequest
from finapp.services.reviews import (
    ensure_weekly_review_due,
    get_weekly_review_data,
    complete_weekly_review,
    get_monthly_reset_data,
    close_month,
    start_month_shortcut,
)

templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["reviews"])


@router.get("/reviews", response_class=HTMLResponse)
def get_reviews_hub(request: Request, ctx: AccountContext = Depends(get_account_context)) -> str:
    return templates.TemplateResponse("reviews.html", {"request": request})


@router.get("/reviews/weekly")
def get_weekly_review(ctx: AccountContext = Depends(get_account_context)) -> dict:
    ensure_weekly_review_due(ctx)
    return get_weekly_review_data(ctx)


@router.post("/reviews/weekly")
def post_weekly_review(
    request: WeeklyReviewCompleteRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    review = complete_weekly_review(ctx, intention=request.intention, quick=request.quick)
    return {
        "id": review.id,
        "week_start": review.week_start.isoformat(),
        "prompt_shown": review.prompt_shown,
        "notes": review.notes,
        "completed_at": review.completed_at.isoformat(),
    }


@router.get("/reviews/monthly")
def get_monthly_review(
    period_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    try:
        return get_monthly_reset_data(ctx, period_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/reviews/monthly")
def post_monthly_review(
    request: MonthlyReviewCloseRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    try:
        result = close_month(
            ctx, period_id=request.period_id, notes=request.notes,
            sweep_to_mission=request.sweep_to_mission,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "period_id": result["period_id"],
        "next_period_id": result["next_period_id"],
        "net_worth_cents": result["net_worth_cents"],
        "backup_path": result["backup_path"],
        "total_drift": result["reconciliation"]["total_drift"],
    }


@router.post("/reviews/start-month")
def post_start_month(ctx: AccountContext = Depends(get_account_context)) -> dict:
    period = start_month_shortcut(ctx)
    return {"period_id": period.id, "year": period.year, "month": period.month}
