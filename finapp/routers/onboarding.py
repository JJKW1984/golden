"""
Onboarding router (Phase 9): HTTP-only. Parses requests, converts money to
cents / basis points at the boundary, and delegates to the onboarding service.

Routes:
- GET  /                       redirects to /onboarding (incomplete) or /dashboard
- GET  /onboarding             the six-step setup page
- POST /onboarding/settings    step 1 & 2: name + income
- POST /onboarding/categories  step 3: rename / add / hide
- POST /onboarding/debt        step 4: debt accounts + method (no default)
- POST /onboarding/savings     step 5: emergency fund
- POST /onboarding/missions    step 6: generate queue, flip setup_complete
"""
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from finapp.deps import get_account_context, AccountContext
from finapp.money import to_cents
from finapp.schemas import (
    OnboardingSettingsRequest,
    OnboardingCategoriesRequest,
    OnboardingDebtRequest,
    OnboardingSavingsRequest,
)
from finapp.services import onboarding

templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["onboarding"])


def _percent_to_bps(value: str) -> int:
    """Convert a percent string ("21.99") to integer basis points (2199)."""
    return int((Decimal(value) * 100).to_integral_value(rounding="ROUND_HALF_UP"))


@router.get("/", response_class=HTMLResponse)
def get_root(ctx: AccountContext = Depends(get_account_context)):
    """Gate: redirect to onboarding until setup_complete, else to the dashboard."""
    if onboarding.is_setup_complete(ctx):
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/onboarding", status_code=302)


@router.get("/onboarding", response_class=HTMLResponse)
def get_onboarding(request: Request, ctx: AccountContext = Depends(get_account_context)):
    """The six-step setup page with a visible progress indicator."""
    categories = onboarding.get_categories(ctx)
    settings = onboarding.get_or_create_settings(ctx)
    return templates.TemplateResponse(
        "onboarding.html",
        {"request": request, "categories": categories, "settings": settings},
    )


@router.post("/onboarding/settings")
def post_settings(
    body: OnboardingSettingsRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    settings = onboarding.save_settings(
        ctx,
        user_name=body.user_name,
        monthly_income_cents=to_cents(body.monthly_income),
        pay_frequency=body.pay_frequency,
        pay_day=body.pay_day,
        hourly_wage_cents=to_cents(body.hourly_wage) if body.hourly_wage else None,
    )
    return {"ok": True, "setup_complete": settings.setup_complete}


@router.post("/onboarding/categories")
def post_categories(
    body: OnboardingCategoriesRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    cats = onboarding.confirm_categories(
        ctx,
        renames=body.renames,
        additions=body.additions,
        hidden_ids=body.hidden_ids,
    )
    return {"ok": True, "active_categories": [c.id for c in cats if c.is_active]}


@router.post("/onboarding/debt")
def post_debt(
    body: OnboardingDebtRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    debts = []
    if body.has_debt == "yes":
        debts = [
            {
                "name": d.name,
                "creditor": d.creditor,
                "balance_cents": to_cents(d.balance),
                "interest_rate_bps": _percent_to_bps(d.interest_rate),
                "minimum_payment_cents": to_cents(d.minimum_payment),
            }
            for d in body.debts
        ]
    created = onboarding.add_debts(ctx, debts=debts, method=body.method)
    return {"ok": True, "debt_ids": [d.id for d in created]}


@router.post("/onboarding/savings")
def post_savings(
    body: OnboardingSavingsRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    current = to_cents(body.current_balance) if body.has_savings else 0
    goal = onboarding.set_emergency_fund(
        ctx,
        current_balance_cents=current,
        target_cents=to_cents(body.target),
    )
    return {"ok": True, "goal_id": goal.id}


@router.post("/onboarding/missions")
def post_missions(ctx: AccountContext = Depends(get_account_context)) -> dict:
    missions = onboarding.generate_mission_queue(ctx)
    onboarding.complete_setup(ctx)
    return {"ok": True, "mission_ids": [m.id for m in missions], "setup_complete": True}
