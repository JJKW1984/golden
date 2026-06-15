from datetime import date
from pydantic import BaseModel


class BudgetAllocationResponse(BaseModel):
    """Response model for a single budget allocation."""
    id: int
    category_id: int
    category_name: str
    target_cents: int
    funded_cents: int  # Derived, not stored
    spent_cents: int  # Derived
    remaining_cents: int  # Derived: funded - spent

    class Config:
        from_attributes = True


class AllocationRitualRequest(BaseModel):
    """Request to set initial targets for a period (allocation ritual)."""
    period_id: int
    allocations: list[dict]  # [{category_id, target_cents}, ...]


class AllocationRitualResponse(BaseModel):
    """Response after setting targets."""
    period_id: int
    allocations: list[BudgetAllocationResponse]
    total_target_cents: int
    income_received_cents: int
    unallocated_cents: int  # income_received - sum(target)
    zero_based_block: bool  # True if unallocated != 0


class IncomeEntryRequest(BaseModel):
    """Request to log income."""
    date: date
    amount_cents: int
    payee: str = None
    memo: str = None


class AllocationSummaryResponse(BaseModel):
    """Summary of allocation state for a period."""
    period_id: int
    income_received_cents: int
    total_target_cents: int
    total_funded_cents: int
    total_spent_cents: int
    unallocated_cents: int
    is_zero_based: bool
    allocations: list[BudgetAllocationResponse]
    has_insufficient_income: bool  # True when income < essentials


class AllocationTriageResponse(BaseModel):
    """Response when income < essentials (triage path)."""
    message: str
    income_received_cents: int
    essentials_target_cents: int
    shortfall_cents: int
    funded_allocations: list[BudgetAllocationResponse]
    unfunded_allocations: list[BudgetAllocationResponse]
    options: list[str]  # ["lower_target", "defer_emergency_fund", "expect_more_income"]


class MilestoneResponse(BaseModel):
    """Response model for a created milestone."""
    threshold: int
    message: str

    class Config:
        from_attributes = True


class CheckinResponse(BaseModel):
    """Response after recording a check-in."""
    checkin_id: int
    streak: int
    milestones_created: list[MilestoneResponse]

    class Config:
        from_attributes = True


class DashboardPulseResponse(BaseModel):
    """Weekly spending pulse data."""
    spent_cents: int
    target_cents: int
    percentage: int

    class Config:
        from_attributes = True


class DashboardMissionResponse(BaseModel):
    """Current active mission with progress."""
    title: str
    progress: int  # 0-100
    target: str

    class Config:
        from_attributes = True


class NextRightActionResponse(BaseModel):
    """Next right action prompt."""
    action: str
    label: str
    hint: str

    class Config:
        from_attributes = True


class DashboardResponse(BaseModel):
    """Complete dashboard data structure."""
    greeting: str
    date: str
    streak: int
    current_mission: DashboardMissionResponse | None
    this_week_pulse: DashboardPulseResponse
    next_right_action: NextRightActionResponse

    class Config:
        from_attributes = True
