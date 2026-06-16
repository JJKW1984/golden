"""
Allocation routers: HTTP endpoints for income entry and allocation ritual.
"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from finapp.deps import get_account_context, AccountContext, get_db
from finapp.schemas import (
    IncomeEntryRequest,
    AllocationRitualRequest,
    AllocationRitualResponse,
    AllocationSummaryResponse,
    AllocationTriageResponse,
    BudgetAllocationResponse,
)
from finapp.services.ledger import create_transaction, get_or_create_period
from finapp.services.allocation import (
    set_allocation_targets,
    compute_funded_cents_per_category,
    compute_remaining_cents_per_category,
    compute_unallocated_cents,
    compute_essentials_target_cents,
    is_zero_based_confirmed,
    get_triage_state,
)
from finapp.services.balances import get_budget_spent_cents
from finapp.models import BudgetCategory, BudgetPeriod, BudgetAllocation

router = APIRouter(prefix="/api/allocation", tags=["allocation"])


@router.post("/income")
def log_income(
    request: IncomeEntryRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Log income transaction for the given date.
    Updates period.income_received_cents.
    """
    # Get Income category
    income_cat = ctx.db.query(BudgetCategory).filter_by(
        account_id=ctx.account_id, kind="income"
    ).first()

    if not income_cat:
        raise HTTPException(status_code=400, detail="Income category not found")

    # Create transaction
    txn = create_transaction(
        ctx,
        date=request.date,
        amount_cents=request.amount_cents,
        direction="in",
        category_id=income_cat.id,
        payee=request.payee,
        memo=request.memo,
    )

    # Get the period that was created/updated
    period = get_or_create_period(ctx.db, ctx.account_id, request.date)

    return {
        "status": "income_logged",
        "amount_cents": request.amount_cents,
        "period_id": period.id,
    }


@router.post("/targets")
def set_targets(
    request: AllocationRitualRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> AllocationRitualResponse:
    """
    Set allocation targets for a period (allocation ritual).
    Fires the first_budget milestone the first time this account ever sets any allocation.
    """
    from finapp.services.milestones import create_milestone_if_new

    period = ctx.db.query(BudgetPeriod).filter_by(
        id=request.period_id, account_id=ctx.account_id
    ).first()

    if not period:
        raise HTTPException(status_code=404, detail="Period not found")

    is_first_ever = ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id
    ).count() == 0

    # Convert request format {category_id: target_cents}
    targets = {item["category_id"]: item["target_cents"] for item in request.allocations}

    set_allocation_targets(ctx, period_id=request.period_id, category_targets=targets)

    if is_first_ever:
        create_milestone_if_new(
            ctx,
            milestone_type="first_budget",
            title="Your first budget!",
            description="You set targets for your first month.",
        )

    # Build response
    categories = {
        cat.id: cat
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }

    funded = compute_funded_cents_per_category(ctx, period_id=request.period_id)

    allocations = []
    total_target = 0

    for alloc in ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=request.period_id
    ).all():
        cat = categories.get(alloc.category_id)
        if not cat:
            continue

        spent = get_budget_spent_cents(ctx, request.period_id, alloc.category_id)
        funded_cents = funded.get(alloc.category_id, 0)
        remaining = funded_cents - spent

        allocations.append(
            BudgetAllocationResponse(
                id=alloc.id,
                category_id=alloc.category_id,
                category_name=cat.name,
                target_cents=alloc.target_cents,
                funded_cents=funded_cents,
                spent_cents=spent,
                remaining_cents=remaining,
            )
        )
        total_target += alloc.target_cents

    unallocated = compute_unallocated_cents(ctx, period_id=request.period_id)
    zero_based_block = (unallocated != 0)

    return AllocationRitualResponse(
        period_id=request.period_id,
        allocations=allocations,
        total_target_cents=total_target,
        income_received_cents=period.income_received_cents,
        unallocated_cents=unallocated,
        zero_based_block=zero_based_block,
    )


@router.get("/summary/{period_id}")
def get_allocation_summary(
    period_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> AllocationSummaryResponse:
    """
    Get current allocation state for a period (all categories, funded/spent/remaining).
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()

    if not period:
        raise HTTPException(status_code=404, detail="Period not found")

    categories = {
        cat.id: cat
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }

    funded = compute_funded_cents_per_category(ctx, period_id=period_id)

    allocations = []
    total_target = 0
    total_funded = 0
    total_spent = 0

    for alloc in ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all():
        cat = categories.get(alloc.category_id)
        if not cat:
            continue

        spent = get_budget_spent_cents(ctx, period_id, alloc.category_id)
        funded_cents = funded.get(alloc.category_id, 0)
        remaining = funded_cents - spent

        allocations.append(
            BudgetAllocationResponse(
                id=alloc.id,
                category_id=alloc.category_id,
                category_name=cat.name,
                target_cents=alloc.target_cents,
                funded_cents=funded_cents,
                spent_cents=spent,
                remaining_cents=remaining,
            )
        )
        total_target += alloc.target_cents
        total_funded += funded_cents
        total_spent += spent

    unallocated = compute_unallocated_cents(ctx, period_id=period_id)
    is_zero_based = (unallocated == 0)

    triage = get_triage_state(ctx, period_id=period_id)
    has_insufficient_income = triage["has_insufficient_income"]

    return AllocationSummaryResponse(
        period_id=period_id,
        income_received_cents=period.income_received_cents,
        total_target_cents=total_target,
        total_funded_cents=total_funded,
        total_spent_cents=total_spent,
        unallocated_cents=unallocated,
        is_zero_based=is_zero_based,
        allocations=allocations,
        has_insufficient_income=has_insufficient_income,
    )


@router.get("/triage/{period_id}")
def get_allocation_triage(
    period_id: int,
    ctx: AccountContext = Depends(get_account_context),
) -> AllocationTriageResponse:
    """
    Get triage state when income < essentials (soft allocation path).
    """
    period = ctx.db.query(BudgetPeriod).filter_by(
        id=period_id, account_id=ctx.account_id
    ).first()

    if not period:
        raise HTTPException(status_code=404, detail="Period not found")

    triage = get_triage_state(ctx, period_id=period_id)

    if not triage["has_insufficient_income"]:
        raise HTTPException(status_code=400, detail="Income is sufficient; triage not applicable")

    categories = {
        cat.id: cat
        for cat in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id).all()
    }

    funded = compute_funded_cents_per_category(ctx, period_id=period_id)

    funded_allocs = []
    unfunded_allocs = []

    for alloc in ctx.db.query(BudgetAllocation).filter_by(
        account_id=ctx.account_id, period_id=period_id
    ).all():
        cat = categories.get(alloc.category_id)
        if not cat:
            continue

        spent = get_budget_spent_cents(ctx, period_id, alloc.category_id)
        funded_cents = funded.get(alloc.category_id, 0)
        remaining = funded_cents - spent

        alloc_response = BudgetAllocationResponse(
            id=alloc.id,
            category_id=alloc.category_id,
            category_name=cat.name,
            target_cents=alloc.target_cents,
            funded_cents=funded_cents,
            spent_cents=spent,
            remaining_cents=remaining,
        )

        if funded_cents >= alloc.target_cents:
            funded_allocs.append(alloc_response)
        else:
            unfunded_allocs.append(alloc_response)

    message = (
        f"Income ${triage['income_received_cents']/100:.2f} is short by "
        f"${triage['shortfall_cents']/100:.2f} of essentials. "
        f"You can: lower targets, defer Emergency Fund this month, or wait for more income."
    )

    return AllocationTriageResponse(
        message=message,
        income_received_cents=triage["income_received_cents"],
        essentials_target_cents=triage["essentials_target_cents"],
        shortfall_cents=triage["shortfall_cents"],
        funded_allocations=funded_allocs,
        unfunded_allocations=unfunded_allocs,
        options=triage["options"],
    )
