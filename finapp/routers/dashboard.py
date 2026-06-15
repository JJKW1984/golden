"""
Dashboard routers: HTTP endpoints for dashboard data and check-in recording.

Endpoints:
- GET /api/dashboard: Returns complete dashboard data (JSON)
- POST /api/checkin: Records check-in, returns status with streak and milestones (JSON)
- GET /dashboard: Dashboard HTML page
"""
from datetime import datetime
from fastapi import APIRouter, Depends, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from finapp.deps import get_account_context, AccountContext
from finapp.schemas import (
    DashboardResponse,
    CheckinResponse,
    MilestoneResponse,
)
from finapp.services.dashboard import get_dashboard_data
from finapp.services.streak import ensure_checkin, calculate_streak, get_milestones_for_streak

# Initialize templates
templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard", response_model=DashboardResponse)
def get_dashboard(
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Get complete dashboard data.

    Returns the output of dashboard.get_dashboard_data(ctx) including:
    - greeting: time-based greeting with user name
    - date: formatted current date
    - streak: check-in streak count
    - current_mission: first active mission with progress, or None
    - this_week_pulse: spending vs target for current week
    - next_right_action: primary action to prompt user with

    Status: 200 OK
    """
    data = get_dashboard_data(ctx)
    return data


@router.post("/api/checkin", response_model=CheckinResponse)
def post_checkin(
    ctx: AccountContext = Depends(get_account_context),
    response: Response = None,
) -> dict:
    """
    Record a check-in for today and return updated streak with milestones.

    Creates a CheckIn record for today (idempotent). Calculates the current streak
    and generates Milestone objects for any threshold reached (7, 30, 90, 180, 365).

    Returns:
        {
            'checkin_id': int,
            'streak': int,
            'milestones_created': list[{threshold: int, message: str}],
        }

    Status: 201 Created on new check-in, 200 OK if already checked in today
    """
    from datetime import date
    from sqlalchemy import func
    from finapp.models import CheckIn

    # Check if checkin already exists for today
    today = date.today()
    existing_checkin = ctx.db.query(CheckIn).filter_by(
        account_id=ctx.account_id, date=today
    ).first()

    is_new_checkin = existing_checkin is None

    # Ensure check-in exists for today (idempotent)
    checkin = ensure_checkin(ctx)

    # Calculate current streak
    streak = calculate_streak(ctx)

    # Get milestones for this streak
    milestones = get_milestones_for_streak(ctx, streak)

    # Convert milestones to response format
    milestones_created = [
        MilestoneResponse(
            threshold=m.threshold,
            message=m.title,
        )
        for m in milestones
    ]

    # Set status code based on whether checkin was newly created
    if response is not None:
        response.status_code = 201 if is_new_checkin else 200

    return {
        "checkin_id": checkin.id,
        "streak": streak,
        "milestones_created": milestones_created,
    }


@router.get("/dashboard", response_class=HTMLResponse)
def get_dashboard_page(
    request: Request,
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """
    Dashboard HTML page (Screen 1).

    Fetches dashboard data from the service and renders the dashboard.html template.
    The template displays:
    - Greeting and current date
    - Streak card with emoji
    - Current mission (read-only stub)
    - This week pulse (spending vs target)
    - Next right action (primary CTA)

    Status: 200 OK
    """
    # Fetch dashboard data
    dashboard = get_dashboard_data(ctx)

    # Render template with dashboard data
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "dashboard": dashboard,
        }
    )
