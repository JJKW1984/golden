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


# Phase 5: Budget & Transactions

class TransactionRequest(BaseModel):
    """Request to create or update a transaction."""
    date: date
    amount_cents: int
    direction: str = "out"  # 'in' or 'out'
    category_id: int
    payee: str | None = None
    memo: str | None = None
    mood_tag: str | None = None  # 'planned', 'impulse', 'stress', 'celebration', 'necessity'


class TransactionResponse(BaseModel):
    """Response model for a transaction."""
    id: int
    date: date
    amount_cents: int
    direction: str
    category_id: int
    category_name: str
    payee: str | None
    memo: str | None
    mood_tag: str | None
    link_type: str | None  # 'debt', 'savings', or None
    link_id: int | None
    is_deleted: bool
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class BudgetTableRowResponse(BaseModel):
    """Single row in the budget table."""
    category_id: int
    category_name: str
    target_cents: int
    funded_cents: int
    spent_cents: int
    remaining_cents: int
    is_overspent: bool  # remaining_cents < 0

    class Config:
        from_attributes = True


class BudgetSummaryResponse(BaseModel):
    """Budget data for a single period (Screen 2)."""
    period_id: int
    year: int
    month: int
    income_received_cents: int
    total_target_cents: int
    total_funded_cents: int
    total_spent_cents: int
    total_remaining_cents: int
    unallocated_cents: int
    is_zero_based: bool
    categories: list[BudgetTableRowResponse]

    class Config:
        from_attributes = True


class BudgetHistoryPeriodResponse(BaseModel):
    """Summary of a past period (for history tab)."""
    period_id: int
    year: int
    month: int
    income_received_cents: int
    total_spent_cents: int
    total_target_cents: int

    class Config:
        from_attributes = True


class OverageDetectionResponse(BaseModel):
    """Response when an overage is detected after expense logging."""
    overage_amount_cents: int
    overage_category_id: int
    overage_category_name: str
    available_categories: list[BudgetTableRowResponse]  # Categories with headroom, sorted by headroom descending

    class Config:
        from_attributes = True


class BudgetReallocateRequest(BaseModel):
    """Request to move funds between budget allocations."""
    period_id: int
    from_category_id: int
    to_category_id: int
    amount_cents: int


class BudgetReallocateResponse(BaseModel):
    """Response after reallocation."""
    from_allocation_id: int
    to_allocation_id: int
    from_target_cents: int
    to_target_cents: int

    class Config:
        from_attributes = True


# Phase 7: Savings & Net Worth

class SavingsContributionRequest(BaseModel):
    """Request to contribute to a savings goal."""
    goal_id: int
    amount_cents: int
    memo: str | None = None


class SavingsWithdrawalRequest(BaseModel):
    """Request to withdraw from a savings goal."""
    goal_id: int
    amount_cents: int
    memo: str | None = None


class SavingsGoalResponse(BaseModel):
    """A single savings goal with derived balance."""
    id: int
    name: str
    goal_type: str
    target_cents: int
    balance_cents: int
    percent_complete: int
    is_complete: bool
    target_date: date | None

    class Config:
        from_attributes = True


class EmergencyFundResponse(BaseModel):
    """Emergency Fund card data (Screen 5)."""
    goal_id: int
    balance_cents: int
    target_cents: int
    percent_complete: int
    days_of_coverage: int
    days_of_coverage_estimated: bool
    est_complete_months: int | None
    est_complete_date: str | None

    class Config:
        from_attributes = True


class AssetAccountRequest(BaseModel):
    """Request to add or update an asset account snapshot."""
    id: int | None = None
    name: str
    balance_cents: int


class AssetAccountResponse(BaseModel):
    """Response model for an asset account."""
    id: int
    name: str
    balance_cents: int
    is_active: bool

    class Config:
        from_attributes = True


class NetWorthResponse(BaseModel):
    """Net worth summary (9.8)."""
    net_worth_cents: int
    total_assets_cents: int
    total_debt_cents: int
    trend: list[dict]  # Monthly snapshots; populated starting Phase 8

    class Config:
        from_attributes = True


# Phase 8: Missions, Reviews, Milestones

class MissionProgressResponse(BaseModel):
    """A single mission with derived progress."""
    id: int
    name: str
    mission_type: str
    link_type: str | None
    link_id: int | None
    sort_order: int | None
    start_cents: int
    current_cents: int
    target_cents: int
    percent: int

    class Config:
        from_attributes = True


class MissionReorderRequest(BaseModel):
    """Request to reorder the mission queue."""
    ordered_ids: list[int]


class WeeklyReviewCompleteRequest(BaseModel):
    """Request to complete the weekly review."""
    intention: str | None = None
    quick: bool = False


class MonthlyReviewCloseRequest(BaseModel):
    """Request to close the monthly period (Monthly Reset)."""
    period_id: int
    notes: str | None = None
    sweep_to_mission: bool = False
