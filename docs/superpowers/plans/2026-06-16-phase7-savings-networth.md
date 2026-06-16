# Phase 7: Savings & Net Worth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Savings screen (Emergency Fund card + Other Goals), wire contributions/withdrawals through the ledger, fix days-of-expenses coverage to match spec §4.14 exactly, flip `SavingsGoal.is_complete` on goal completion, and add the Net Worth foundation feature (`AssetAccount` CRUD + `GET /api/networth`).

**Architecture:** Savings contributions/withdrawals are `Transaction` rows with `link_type='savings'` created via the existing `services/ledger.py::create_transaction()` (no special-casing needed there — unlike debt, savings has no principal/interest split). `services/balances.py` already has `get_savings_goal_balance_cents`, `get_days_of_expenses_coverage`, and `get_net_worth_cents` from earlier scaffolding, but `get_days_of_expenses_coverage` does not yet match spec §4.14 (wrong category filter, wrong <30-day fallback) — this plan fixes it. Goal completion (`is_complete`/`completed_at`) is derived and flipped inside `services/reconciliation.py::apply_recomputed_balances()`, the same place `cached_balance_cents` is refreshed after every ledger write, so it never drifts. A new `routers/savings.py` wires HTTP endpoints for the savings screen, asset accounts, and net worth.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy ORM, Jinja2, HTMX/Tailwind, Pydantic v2, pytest

---

## File Structure

**New files:**
- `finapp/routers/savings.py` — `GET /savings`, `POST /savings/contribution`, `POST /savings/withdrawal`, `GET /api/savings/emergency`, `GET /assets`, `POST /assets`, `GET /api/networth`
- `finapp/templates/savings.html` — Emergency Fund card (balance, target, %, days-of-coverage, est. complete) + Other Goals list
- `finapp/templates/assets.html` — Asset account list + add/update form (net worth inputs)
- `tests/test_balances.py` — extend (days-of-coverage fix, completion estimate)
- `tests/test_reconciliation.py` — extend (`is_complete` flip)
- `tests/test_savings_router.py` — new integration tests for all savings/asset/networth routes

**Modified files:**
- `finapp/services/balances.py` — fix `get_days_of_expenses_coverage` (spec-correct category filter + fallback), add `estimate_savings_completion()`, add `get_current_period_spending_target_cents()` helper
- `finapp/services/reconciliation.py` — flip `SavingsGoal.is_complete`/`completed_at` inside `apply_recomputed_balances()`
- `finapp/schemas.py` — add `SavingsContributionRequest`, `SavingsWithdrawalRequest`, `EmergencyFundResponse`, `SavingsGoalResponse`, `AssetAccountRequest`, `AssetAccountResponse`, `NetWorthResponse`
- `finapp/main.py` — register `savings.router`

---

## Task Breakdown

### Task 1: Fix `get_days_of_expenses_coverage` to Match Spec §4.14

**Spec (§4.14):** `avg_daily_expense` = (sum of `kind='spending'` category transactions over the trailing 90 days) / 90. If fewer than 30 days of spending history exist, fall back to (sum of current period spending category targets) / 30, and label the result "estimated".

**Current bug:** `finapp/services/balances.py:114-167` filters by `Transaction.link_type == None` (wrong proxy for "spending category") instead of joining `BudgetCategory.kind == 'spending'`, and its <30-day fallback uses the actual partial average instead of spending targets ÷ 30.

**Files:**
- Modify: `finapp/services/balances.py:114-167`
- Modify: `tests/test_balances.py:327-391` (replace the two existing loose tests with precise ones)

- [ ] **Step 1: Write a failing test for the trailing-90-day case using `kind='spending'` filtering**

```python
# tests/test_balances.py — replace TestDaysOfExpensesCoverage class (lines 327-391)
class TestDaysOfExpensesCoverage:
    """Test emergency fund coverage calculation (spec §4.14)."""

    def test_coverage_uses_spending_category_only(self, db, ctx, setup_account):
        """Only kind='spending' category transactions count toward avg_daily_expense."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()
        debt_cat = BudgetCategory(
            account_id=account.id, name="Debt", kind="debt", is_system=True, is_active=True,
        )
        db.add(debt_cat)
        db.commit()

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=300000,
            opening_balance_cents=300000,
            cached_balance_cents=300000,
        )
        db.add(goal)
        db.commit()

        # 90 days of $100/day spending (kind='spending') -> avg_daily = 9000000/90 = 100000? use smaller numbers
        for i in range(90):
            day = date(2026, 6, 15) - timedelta(days=i)
            create_transaction(ctx, date=day, amount_cents=1000,
                                direction="out", category_id=food_cat.id)
        # A large debt payment that must NOT count as "spending"
        create_transaction(ctx, date=date(2026, 6, 15), amount_cents=500000,
                            direction="out", category_id=debt_cat.id, link_type="debt", link_id=None)
        db.commit()

        days, estimated = get_days_of_expenses_coverage(ctx, goal.id)

        # avg_daily_expense = (90 * 1000) / 90 = 1000 cents/day
        # coverage = 300000 / 1000 = 300 days
        assert days == 300
        assert estimated is False

    def test_coverage_fallback_uses_spending_targets_when_under_30_days(self, db, ctx, setup_account):
        """With < 30 days of spending history, fall back to current-period spending targets / 30, labeled estimated."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=300000,
            opening_balance_cents=300000,
            cached_balance_cents=300000,
        )
        db.add(goal)

        allocation = BudgetAllocation(
            account_id=account.id, period_id=period.id, category_id=food_cat.id,
            target_cents=30000,  # $300 spending target this period
        )
        db.add(allocation)

        # Only 10 days of spending history (< 30)
        for i in range(10):
            day = date(2026, 6, 15) - timedelta(days=i)
            create_transaction(ctx, date=day, amount_cents=500,
                                direction="out", category_id=food_cat.id)
        db.commit()

        days, estimated = get_days_of_expenses_coverage(ctx, goal.id)

        # avg_daily_expense = 30000 / 30 = 1000 cents/day
        # coverage = 300000 / 1000 = 300 days
        assert days == 300
        assert estimated is True

    def test_coverage_no_data_no_targets_returns_zero(self, db, ctx, setup_account):
        """No spending history and no spending targets -> 0 days, estimated."""
        account, period = setup_account
        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=300000,
            opening_balance_cents=300000,
            cached_balance_cents=300000,
        )
        db.add(goal)
        db.commit()

        days, estimated = get_days_of_expenses_coverage(ctx, goal.id)
        assert days == 0
        assert estimated is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd d:\golden
pytest tests/test_balances.py::TestDaysOfExpensesCoverage -v
```

Expected: `FAILED` — current function returns a plain `int`, not a `(days, estimated)` tuple, and the old import/test signature mismatches will raise `TypeError: cannot unpack non-iterable int object`.

- [ ] **Step 3: Add `BudgetAllocation` import and rewrite `get_days_of_expenses_coverage`**

```python
# finapp/services/balances.py
# Update the import block at the top of the file (line 8-15):
from finapp.models import (
    Transaction,
    BudgetCategory,
    BudgetPeriod,
    BudgetAllocation,
    DebtAccount,
    SavingsGoal,
    AssetAccount,
)
```

```python
# finapp/services/balances.py — replace get_days_of_expenses_coverage (lines 114-167)
def get_current_period_spending_target_cents(ctx: AccountContext) -> int:
    """
    Sum of target_cents for kind='spending' categories in the current calendar-month
    period. Returns 0 if no period or no allocations exist yet (no side effects —
    does not create a period).
    """
    today = date.today()
    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, year=today.year, month=today.month
    ).first()

    if not period:
        return 0

    total = ctx.db.query(func.sum(BudgetAllocation.target_cents)).filter(
        BudgetAllocation.account_id == ctx.account_id,
        BudgetAllocation.period_id == period.id,
    ).join(BudgetCategory, BudgetAllocation.category_id == BudgetCategory.id).filter(
        BudgetCategory.kind == "spending",
    ).scalar()

    return total or 0


def get_days_of_expenses_coverage(ctx: AccountContext, goal_id: int) -> tuple[int, bool]:
    """
    Derive days-of-expenses coverage for an emergency fund goal (spec §4.14).

    avg_daily_expense = (sum of kind='spending' category transactions over the
    trailing 90 days) / 90.

    If fewer than 30 days of spending history exist, fall back to
    (sum of current-period spending category targets) / 30, labeled "estimated".

    Returns:
        (days_of_coverage, estimated) tuple. estimated=True when the fallback was used.
    """
    balance = get_savings_goal_balance_cents(ctx, goal_id)

    today = date.today()
    cutoff_date = today - timedelta(days=90)

    spending_txns = ctx.db.query(Transaction).join(
        BudgetCategory, Transaction.category_id == BudgetCategory.id
    ).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.date >= cutoff_date,
        Transaction.direction == "out",
        Transaction.is_deleted == False,
        BudgetCategory.kind == "spending",
    ).all()

    earliest_date = min((t.date for t in spending_txns), default=None)
    days_with_data = (today - earliest_date).days + 1 if earliest_date else 0

    if days_with_data >= 30:
        total_spent = sum(t.amount_cents for t in spending_txns)
        daily_avg = total_spent // 90
        estimated = False
    else:
        target_total = get_current_period_spending_target_cents(ctx)
        daily_avg = target_total // 30
        estimated = True

    if daily_avg == 0:
        return (0, estimated)

    return (balance // daily_avg, estimated)
```

- [ ] **Step 4: Update the test file's existing imports to include `BudgetAllocation`**

Check the top of `tests/test_balances.py` and add `BudgetAllocation` to the `finapp.models` import line if not already present.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
pytest tests/test_balances.py::TestDaysOfExpensesCoverage -v
```

Expected: `PASSED (3 tests)`

- [ ] **Step 6: Run the full balances test file to check for regressions**

```bash
pytest tests/test_balances.py -v
```

Expected: All tests pass (no other test calls `get_days_of_expenses_coverage` with the old signature).

- [ ] **Step 7: Commit**

```bash
git add finapp/services/balances.py tests/test_balances.py
git commit -m "fix: days-of-expenses coverage matches spec 4.14 (spending-kind filter, target-based fallback)"
```

---

### Task 2: Flip `SavingsGoal.is_complete` on Goal Completion

**Files:**
- Modify: `finapp/services/reconciliation.py:158-187` (`apply_recomputed_balances`)
- Modify: `tests/test_reconciliation.py`

- [ ] **Step 1: Write a failing test for completion flip**

```python
# tests/test_reconciliation.py — add near the other savings goal tests
def test_apply_recomputed_balances_flips_is_complete(db, populated_account):
    """When cached_balance_cents reaches target_cents, is_complete flips and completed_at is set."""
    from finapp.services.reconciliation import apply_recomputed_balances
    from finapp.services.ledger import create_transaction
    from finapp.deps import AccountContext
    from datetime import date as date_cls

    account, period = populated_account
    ctx = AccountContext(account_id=account.id, db=db)

    goal = SavingsGoal(
        account_id=account.id,
        name="Vacation Fund",
        goal_type="sinking_fund",
        target_cents=10000,
        opening_balance_cents=0,
        cached_balance_cents=0,
        is_complete=False,
    )
    db.add(goal)
    db.commit()

    create_transaction(
        ctx, date=date_cls(2026, 6, 15), amount_cents=10000,
        direction="out", link_type="savings", link_id=goal.id,
    )

    apply_recomputed_balances(db, account.id)
    db.refresh(goal)

    assert goal.cached_balance_cents == 10000
    assert goal.is_complete is True
    assert goal.completed_at is not None


def test_apply_recomputed_balances_does_not_flip_is_complete_below_target(db, populated_account):
    """is_complete stays False while balance is below target."""
    from finapp.services.reconciliation import apply_recomputed_balances
    from finapp.deps import AccountContext

    account, period = populated_account
    ctx = AccountContext(account_id=account.id, db=db)

    goal = SavingsGoal(
        account_id=account.id,
        name="Vacation Fund",
        goal_type="sinking_fund",
        target_cents=10000,
        opening_balance_cents=5000,
        cached_balance_cents=5000,
        is_complete=False,
    )
    db.add(goal)
    db.commit()

    apply_recomputed_balances(db, account.id)
    db.refresh(goal)

    assert goal.is_complete is False
    assert goal.completed_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_reconciliation.py::test_apply_recomputed_balances_flips_is_complete -v
```

Expected: `FAILED` — `goal.is_complete` stays `False` because nothing sets it today.

- [ ] **Step 3: Update `apply_recomputed_balances` to flip `is_complete`**

```python
# finapp/services/reconciliation.py
# Add datetime import at top if not present: "from datetime import datetime, date"

# Replace the SavingsGoal loop inside apply_recomputed_balances() (around line 177-181):
    # Update SavingsGoal.cached_balance_cents and flip is_complete when target is reached
    goals = db.query(SavingsGoal).filter_by(account_id=account_id).all()
    for goal in goals:
        derived = compute_savings_goal_balance_cents(db, account_id, goal.id)
        goal.cached_balance_cents = derived
        if not goal.is_complete and derived >= goal.target_cents:
            goal.is_complete = True
            goal.completed_at = datetime.utcnow()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_reconciliation.py::test_apply_recomputed_balances_flips_is_complete tests/test_reconciliation.py::test_apply_recomputed_balances_does_not_flip_is_complete_below_target -v
```

Expected: `PASSED (2 tests)`

- [ ] **Step 5: Run the full reconciliation suite to check for regressions**

```bash
pytest tests/test_reconciliation.py -v
```

Expected: All tests pass, including the existing randomized property test.

- [ ] **Step 6: Commit**

```bash
git add finapp/services/reconciliation.py tests/test_reconciliation.py
git commit -m "feat: flip SavingsGoal.is_complete and completed_at when cached balance reaches target"
```

---

### Task 3: Add `estimate_savings_completion()` for "Est. Complete" Date

**Files:**
- Modify: `finapp/services/balances.py`
- Modify: `tests/test_balances.py`

- [ ] **Step 1: Write failing tests for completion estimate**

```python
# tests/test_balances.py — add a new test class
class TestEstimateSavingsCompletion:
    """Test estimated completion date for an in-progress savings goal (calm-by-design: no guilt, just an estimate)."""

    def test_no_contribution_history_returns_none(self, db, ctx, setup_account):
        account, period = setup_account
        goal = SavingsGoal(
            account_id=account.id, name="Vacation", goal_type="sinking_fund",
            target_cents=100000, opening_balance_cents=0, cached_balance_cents=0,
        )
        db.add(goal)
        db.commit()

        months, est_date = estimate_savings_completion(ctx, goal.id)
        assert months is None
        assert est_date is None

    def test_already_complete_returns_none(self, db, ctx, setup_account):
        account, period = setup_account
        goal = SavingsGoal(
            account_id=account.id, name="Vacation", goal_type="sinking_fund",
            target_cents=10000, opening_balance_cents=10000, cached_balance_cents=10000,
            is_complete=True,
        )
        db.add(goal)
        db.commit()

        months, est_date = estimate_savings_completion(ctx, goal.id)
        assert months is None
        assert est_date is None

    def test_estimates_from_trailing_90_day_contribution_rate(self, db, ctx, setup_account):
        account, period = setup_account
        goal = SavingsGoal(
            account_id=account.id, name="Vacation", goal_type="sinking_fund",
            target_cents=100000, opening_balance_cents=0, cached_balance_cents=0,
        )
        db.add(goal)
        db.commit()

        # 3 contributions of $100 each over the trailing 90 days -> $300 / 3 months = $100/month avg
        for i, day in enumerate([date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15)]):
            create_transaction(ctx, date=day, amount_cents=10000,
                                direction="out", link_type="savings", link_id=goal.id)
        db.commit()

        months, est_date = estimate_savings_completion(ctx, goal.id)

        # Remaining = 100000 - 30000 = 70000 cents; avg monthly = 10000 cents -> 7 months
        assert months == 7
        assert est_date is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_balances.py::TestEstimateSavingsCompletion -v
```

Expected: `FAILED - NameError: name 'estimate_savings_completion' is not defined`

- [ ] **Step 3: Implement `estimate_savings_completion`**

```python
# finapp/services/balances.py — append at end of file
def estimate_savings_completion(ctx: AccountContext, goal_id: int) -> tuple[int | None, str | None]:
    """
    Estimate months remaining and a target completion date for a savings goal,
    based on the trailing-90-day average monthly contribution rate.

    Returns (None, None) if the goal is already complete or has no contribution
    history in the trailing 90 days (nothing to estimate from — calm by design,
    no guilt copy for "not enough data").
    """
    goal = ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, id=goal_id
    ).first()

    if not goal or goal.is_complete:
        return (None, None)

    balance = get_savings_goal_balance_cents(ctx, goal_id)
    remaining = goal.target_cents - balance
    if remaining <= 0:
        return (None, None)

    today = date.today()
    cutoff_date = today - timedelta(days=90)

    contributions = ctx.db.query(Transaction).filter(
        Transaction.account_id == ctx.account_id,
        Transaction.link_type == "savings",
        Transaction.link_id == goal_id,
        Transaction.direction == "out",
        Transaction.date >= cutoff_date,
        Transaction.is_deleted == False,
    ).all()

    total_contributed = sum(t.amount_cents for t in contributions)
    if total_contributed <= 0:
        return (None, None)

    avg_monthly_cents = total_contributed // 3  # trailing-90-day window ~= 3 months
    if avg_monthly_cents <= 0:
        return (None, None)

    months = -(-remaining // avg_monthly_cents)  # ceiling division
    est_date = (today + timedelta(days=30 * months)).isoformat()

    return (months, est_date)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_balances.py::TestEstimateSavingsCompletion -v
```

Expected: `PASSED (3 tests)`

- [ ] **Step 5: Commit**

```bash
git add finapp/services/balances.py tests/test_balances.py
git commit -m "feat: estimate savings goal completion date from trailing-90-day contribution rate"
```

---

### Task 4: Add Pydantic Schemas

**Files:**
- Modify: `finapp/schemas.py`

- [ ] **Step 1: Append savings/asset/net-worth schemas**

```python
# finapp/schemas.py — append at end of file

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
```

- [ ] **Step 2: Verify the module still imports cleanly**

```bash
python -c "import finapp.schemas"
```

Expected: No output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add finapp/schemas.py
git commit -m "feat: add savings, asset account, and net worth schemas"
```

---

### Task 5: Savings Router — Contribution & Withdrawal

**Files:**
- Create: `finapp/routers/savings.py`
- Create: `tests/test_savings_router.py`

- [ ] **Step 1: Write failing tests for POST /savings/contribution and /savings/withdrawal**

```python
# tests/test_savings_router.py
import pytest
from datetime import date
from sqlalchemy.orm import sessionmaker
from finapp.models import Account, SavingsGoal, AssetAccount
from finapp.deps import AccountContext


@pytest.fixture
def test_client(db_engine):
    """TestClient with overridden db/account dependencies (mirrors test_client_for_budget pattern)."""
    from fastapi.testclient import TestClient
    from finapp.main import app
    from finapp.deps import get_db, get_account_context

    Session = sessionmaker(bind=db_engine)
    test_db = Session()

    account = Account(id=1, display_name="Test User")
    test_db.add(account)
    test_db.commit()
    cached_account_id = account.id

    def override_get_db():
        yield test_db

    def override_get_account_context():
        return AccountContext(account_id=cached_account_id, db=test_db)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_account_context] = override_get_account_context

    yield TestClient(app), test_db, cached_account_id

    app.dependency_overrides.clear()
    test_db.close()


@pytest.fixture
def savings_goal(test_client):
    client, db, account_id = test_client
    goal = SavingsGoal(
        account_id=account_id,
        name="Emergency Fund",
        goal_type="emergency_fund",
        target_cents=300000,
        opening_balance_cents=100000,
        cached_balance_cents=100000,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def test_post_savings_contribution(test_client, savings_goal):
    client, db, account_id = test_client

    response = client.post("/savings/contribution", json={
        "goal_id": savings_goal.id,
        "amount_cents": 5000,
        "memo": "Paycheck sweep",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["new_balance_cents"] == 105000

    db.refresh(savings_goal)
    assert savings_goal.cached_balance_cents == 105000


def test_post_savings_withdrawal(test_client, savings_goal):
    client, db, account_id = test_client

    response = client.post("/savings/withdrawal", json={
        "goal_id": savings_goal.id,
        "amount_cents": 20000,
        "memo": "Car repair",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["new_balance_cents"] == 80000

    db.refresh(savings_goal)
    assert savings_goal.cached_balance_cents == 80000


def test_post_savings_contribution_unknown_goal_404s(test_client):
    client, db, account_id = test_client

    response = client.post("/savings/contribution", json={
        "goal_id": 9999,
        "amount_cents": 5000,
    })

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_savings_router.py::test_post_savings_contribution -v
```

Expected: `FAILED - 404 Not Found` (route doesn't exist yet)

- [ ] **Step 3: Create `finapp/routers/savings.py` with contribution/withdrawal endpoints**

```python
# finapp/routers/savings.py
"""
Savings router: HTTP endpoints for savings goals, asset accounts, and net worth.

Endpoints:
- GET  /savings: Savings screen (Emergency Fund card + Other Goals) — HTML
- POST /savings/contribution: -> ledger (link_type='savings', direction='out')
- POST /savings/withdrawal: -> ledger (link_type='savings', direction='in')
- GET  /api/savings/emergency: Emergency Fund card data — JSON
- GET  /assets: Asset account list — HTML
- POST /assets: Add/update an asset account balance snapshot
- GET  /api/networth: Net worth summary — JSON
"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from finapp.deps import get_account_context, AccountContext
from finapp.models import SavingsGoal, AssetAccount
from finapp.services.ledger import create_transaction
from finapp.services.balances import (
    get_savings_goal_balance_cents,
    get_days_of_expenses_coverage,
    estimate_savings_completion,
    get_net_worth_cents,
)
from finapp.schemas import (
    SavingsContributionRequest,
    SavingsWithdrawalRequest,
    AssetAccountRequest,
)

templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["savings"])


def _get_goal_or_404(ctx: AccountContext, goal_id: int) -> SavingsGoal:
    goal = ctx.db.query(SavingsGoal).filter_by(
        id=goal_id, account_id=ctx.account_id
    ).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Savings goal not found")
    return goal


@router.post("/savings/contribution")
def post_savings_contribution(
    request: SavingsContributionRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """Record a contribution to a savings goal."""
    goal = _get_goal_or_404(ctx, request.goal_id)

    create_transaction(
        ctx=ctx,
        date=date.today(),
        amount_cents=request.amount_cents,
        direction="out",
        category_id=None,
        payee="",
        memo=request.memo or f"Contribution to {goal.name}",
        link_type="savings",
        link_id=goal.id,
    )

    ctx.db.refresh(goal)
    return {"goal_id": goal.id, "new_balance_cents": goal.cached_balance_cents}


@router.post("/savings/withdrawal")
def post_savings_withdrawal(
    request: SavingsWithdrawalRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """Record a withdrawal from a savings goal."""
    goal = _get_goal_or_404(ctx, request.goal_id)

    create_transaction(
        ctx=ctx,
        date=date.today(),
        amount_cents=request.amount_cents,
        direction="in",
        category_id=None,
        payee="",
        memo=request.memo or f"Withdrawal from {goal.name}",
        link_type="savings",
        link_id=goal.id,
    )

    ctx.db.refresh(goal)
    return {"goal_id": goal.id, "new_balance_cents": goal.cached_balance_cents}
```

- [ ] **Step 4: Register the router in `finapp/main.py`**

```python
# finapp/main.py
from finapp.routers import allocation, dashboard, budget, transactions, debt, savings

app = FastAPI(title="Personal Finance App")

templates = Jinja2Templates(directory="finapp/templates")

# Register routers
app.include_router(allocation.router)
app.include_router(dashboard.router)
app.include_router(budget.router)
app.include_router(transactions.router)
app.include_router(debt.router)
app.include_router(savings.router)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_savings_router.py -v
```

Expected: `PASSED (3 tests)`

- [ ] **Step 6: Commit**

```bash
git add finapp/routers/savings.py finapp/main.py tests/test_savings_router.py
git commit -m "feat: add savings contribution/withdrawal endpoints"
```

---

### Task 6: Savings Router — Emergency Fund API & Savings Screen

**Files:**
- Modify: `finapp/routers/savings.py`
- Create: `finapp/templates/savings.html`
- Modify: `tests/test_savings_router.py`

- [ ] **Step 1: Write failing tests for GET /api/savings/emergency and GET /savings**

```python
# tests/test_savings_router.py — append

def test_get_emergency_fund_api(test_client, savings_goal):
    client, db, account_id = test_client

    response = client.get("/api/savings/emergency")

    assert response.status_code == 200
    data = response.json()
    assert data["goal_id"] == savings_goal.id
    assert data["balance_cents"] == 100000
    assert data["target_cents"] == 300000
    assert data["percent_complete"] == 33  # 100000/300000 rounded down


def test_get_emergency_fund_api_no_goal_404s(test_client):
    client, db, account_id = test_client

    response = client.get("/api/savings/emergency")

    assert response.status_code == 404


def test_get_savings_screen(test_client, savings_goal):
    client, db, account_id = test_client

    other_goal = SavingsGoal(
        account_id=account_id, name="Vacation", goal_type="sinking_fund",
        target_cents=50000, opening_balance_cents=10000, cached_balance_cents=10000,
    )
    db.add(other_goal)
    db.commit()

    response = client.get("/savings")

    assert response.status_code == 200
    assert "Emergency Fund" in response.text
    assert "Vacation" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_savings_router.py::test_get_emergency_fund_api tests/test_savings_router.py::test_get_savings_screen -v
```

Expected: `FAILED - 404 Not Found` (routes don't exist yet)

- [ ] **Step 3: Implement GET /api/savings/emergency and GET /savings**

```python
# finapp/routers/savings.py — append below the withdrawal endpoint

def _emergency_fund_goal(ctx: AccountContext) -> SavingsGoal | None:
    return ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, goal_type="emergency_fund", is_active=True
    ).first()


@router.get("/api/savings/emergency")
def get_emergency_fund(ctx: AccountContext = Depends(get_account_context)) -> dict:
    """Get Emergency Fund card data (Screen 5)."""
    goal = _emergency_fund_goal(ctx)
    if not goal:
        raise HTTPException(status_code=404, detail="No emergency fund goal found")

    balance = get_savings_goal_balance_cents(ctx, goal.id)
    days, estimated = get_days_of_expenses_coverage(ctx, goal.id)
    months, est_date = estimate_savings_completion(ctx, goal.id)

    percent_complete = 0
    if goal.target_cents > 0:
        percent_complete = int(100 * balance / goal.target_cents)

    return {
        "goal_id": goal.id,
        "balance_cents": balance,
        "target_cents": goal.target_cents,
        "percent_complete": percent_complete,
        "days_of_coverage": days,
        "days_of_coverage_estimated": estimated,
        "est_complete_months": months,
        "est_complete_date": est_date,
    }


@router.get("/savings", response_class=HTMLResponse)
def get_savings_screen(
    request: Request,
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """Savings screen: Emergency Fund card + Other Goals."""
    goals = ctx.db.query(SavingsGoal).filter_by(
        account_id=ctx.account_id, is_active=True
    ).all()

    emergency = None
    other_goals = []

    for goal in goals:
        balance = get_savings_goal_balance_cents(ctx, goal.id)
        percent_complete = int(100 * balance / goal.target_cents) if goal.target_cents > 0 else 0

        if goal.goal_type == "emergency_fund":
            days, estimated = get_days_of_expenses_coverage(ctx, goal.id)
            months, est_date = estimate_savings_completion(ctx, goal.id)
            emergency = {
                "id": goal.id,
                "name": goal.name,
                "balance": balance / 100,
                "target": goal.target_cents / 100,
                "percent_complete": percent_complete,
                "days_of_coverage": days,
                "days_estimated": estimated,
                "est_complete_date": est_date,
                "is_complete": goal.is_complete,
            }
        else:
            other_goals.append({
                "id": goal.id,
                "name": goal.name,
                "balance": balance / 100,
                "target": goal.target_cents / 100,
                "percent_complete": percent_complete,
                "is_complete": goal.is_complete,
            })

    return templates.TemplateResponse(
        "savings.html",
        {
            "request": request,
            "emergency": emergency,
            "other_goals": other_goals,
        },
    )
```

- [ ] **Step 4: Create the savings screen template**

```html
<!-- finapp/templates/savings.html -->
{% extends "base.html" %}

{% block title %}Savings{% endblock %}

{% block content %}
<div class="container mx-auto px-4 py-8 space-y-6">
    <h1 class="text-3xl font-bold">Savings</h1>

    {% if emergency %}
    <div class="border-2 border-teal-500 rounded-lg p-6 bg-teal-50">
        <h2 class="text-xl font-bold mb-4">{{ emergency.name }}</h2>

        <div class="grid grid-cols-2 gap-4 mb-6">
            <div>
                <p class="text-gray-600 text-sm">Balance</p>
                <p class="text-2xl font-bold">${{ "%.2f"|format(emergency.balance) }}</p>
            </div>
            <div>
                <p class="text-gray-600 text-sm">Target</p>
                <p class="text-2xl font-bold">${{ "%.2f"|format(emergency.target) }}</p>
            </div>
            <div>
                <p class="text-gray-600 text-sm">Progress</p>
                <p class="text-xl">{{ emergency.percent_complete }}%</p>
            </div>
            <div>
                <p class="text-gray-600 text-sm">
                    Days of expenses covered{% if emergency.days_estimated %} (estimated){% endif %}
                </p>
                <p class="text-xl">{{ emergency.days_of_coverage }} days</p>
            </div>
        </div>

        {% if emergency.is_complete %}
        <p class="text-teal-800 font-semibold">You've reached your emergency fund target.</p>
        {% elif emergency.est_complete_date %}
        <p class="text-gray-700">Estimated complete: {{ emergency.est_complete_date }}</p>
        {% endif %}

        <div class="flex gap-2 flex-wrap mt-4">
            <button class="bg-teal-500 hover:bg-teal-600 text-white px-6 py-2 rounded font-medium"
                    onclick="openContributionModal({{ emergency.id }})">
                Add Contribution
            </button>
            <button class="bg-gray-200 hover:bg-gray-300 px-6 py-2 rounded font-medium text-gray-800"
                    onclick="openWithdrawalModal({{ emergency.id }})">
                Withdraw
            </button>
        </div>
    </div>
    {% endif %}

    {% if other_goals %}
    <div class="space-y-3">
        <h3 class="text-lg font-bold">Other Goals</h3>
        {% for goal in other_goals %}
        <div class="border rounded-lg p-4 bg-white">
            <div class="flex justify-between items-start">
                <div>
                    <h4 class="font-semibold">{{ goal.name }}</h4>
                    <p class="text-gray-600 text-sm">${{ "%.2f"|format(goal.balance) }} of ${{ "%.2f"|format(goal.target) }}</p>
                </div>
                <div class="text-right">
                    <p class="font-semibold">{{ goal.percent_complete }}%</p>
                    {% if goal.is_complete %}<p class="text-teal-700 text-xs">Complete</p>{% endif %}
                </div>
            </div>
            <div class="flex gap-2 mt-3">
                <button class="bg-teal-500 hover:bg-teal-600 text-white px-4 py-1.5 rounded text-sm font-medium"
                        onclick="openContributionModal({{ goal.id }})">
                    Add Contribution
                </button>
                <button class="bg-gray-200 hover:bg-gray-300 px-4 py-1.5 rounded text-sm font-medium text-gray-800"
                        onclick="openWithdrawalModal({{ goal.id }})">
                    Withdraw
                </button>
            </div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {% if not emergency and not other_goals %}
    <div class="bg-gray-50 rounded-lg p-8 text-center">
        <p class="text-gray-600">No savings goals yet</p>
    </div>
    {% endif %}
</div>

<script>
function openContributionModal(goalId) {
    const amount = prompt("Enter contribution amount in dollars:");
    if (amount) {
        fetch('/savings/contribution', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ goal_id: goalId, amount_cents: Math.round(parseFloat(amount) * 100) })
        })
        .then(r => r.json())
        .then(() => location.reload())
        .catch(err => alert('Error: ' + err.message));
    }
}

function openWithdrawalModal(goalId) {
    const amount = prompt("Enter withdrawal amount in dollars:");
    if (amount) {
        fetch('/savings/withdrawal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ goal_id: goalId, amount_cents: Math.round(parseFloat(amount) * 100) })
        })
        .then(r => r.json())
        .then(() => location.reload())
        .catch(err => alert('Error: ' + err.message));
    }
}
</script>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_savings_router.py -v
```

Expected: `PASSED (8 tests)`

- [ ] **Step 6: Commit**

```bash
git add finapp/routers/savings.py finapp/templates/savings.html tests/test_savings_router.py
git commit -m "feat: add emergency fund API and savings screen"
```

---

### Task 7: Asset Accounts & Net Worth

**Files:**
- Modify: `finapp/routers/savings.py`
- Create: `finapp/templates/assets.html`
- Modify: `tests/test_savings_router.py`

- [ ] **Step 1: Write failing tests for GET/POST /assets and GET /api/networth**

```python
# tests/test_savings_router.py — append

def test_post_assets_creates_new_asset(test_client):
    client, db, account_id = test_client

    response = client.post("/assets", json={"name": "Checking", "balance_cents": 250000})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Checking"
    assert data["balance_cents"] == 250000

    asset = db.query(AssetAccount).filter_by(account_id=account_id, name="Checking").first()
    assert asset is not None
    assert asset.balance_cents == 250000


def test_post_assets_updates_existing_asset(test_client):
    client, db, account_id = test_client

    asset = AssetAccount(account_id=account_id, name="Cash", balance_cents=10000)
    db.add(asset)
    db.commit()
    db.refresh(asset)

    response = client.post("/assets", json={"id": asset.id, "name": "Cash", "balance_cents": 15000})

    assert response.status_code == 200
    data = response.json()
    assert data["balance_cents"] == 15000

    db.refresh(asset)
    assert asset.balance_cents == 15000


def test_get_assets_screen(test_client):
    client, db, account_id = test_client

    db.add(AssetAccount(account_id=account_id, name="Checking", balance_cents=250000))
    db.commit()

    response = client.get("/assets")

    assert response.status_code == 200
    assert "Checking" in response.text


def test_get_networth(test_client, savings_goal):
    client, db, account_id = test_client

    db.add(AssetAccount(account_id=account_id, name="Checking", balance_cents=500000))
    db.commit()

    response = client.get("/api/networth")

    assert response.status_code == 200
    data = response.json()
    assert data["total_assets_cents"] == 500000
    assert data["total_debt_cents"] == 0
    assert data["net_worth_cents"] == 500000
    assert data["trend"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_savings_router.py::test_post_assets_creates_new_asset tests/test_savings_router.py::test_get_networth -v
```

Expected: `FAILED - 404 Not Found`

- [ ] **Step 3: Implement asset account and net worth endpoints**

```python
# finapp/routers/savings.py — append at the end of the file

@router.get("/assets", response_class=HTMLResponse)
def get_assets_screen(
    request: Request,
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """Asset accounts screen (net worth inputs)."""
    assets = ctx.db.query(AssetAccount).filter_by(
        account_id=ctx.account_id, is_active=True
    ).all()

    return templates.TemplateResponse(
        "assets.html",
        {
            "request": request,
            "assets": [
                {"id": a.id, "name": a.name, "balance": a.balance_cents / 100}
                for a in assets
            ],
        },
    )


@router.post("/assets")
def post_assets(
    request: AssetAccountRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """Add a new asset account, or update an existing one's balance snapshot."""
    if request.id is not None:
        asset = ctx.db.query(AssetAccount).filter_by(
            id=request.id, account_id=ctx.account_id
        ).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset account not found")
        asset.name = request.name
        asset.balance_cents = request.balance_cents
    else:
        asset = AssetAccount(
            account_id=ctx.account_id,
            name=request.name,
            balance_cents=request.balance_cents,
        )
        ctx.db.add(asset)

    ctx.db.commit()
    ctx.db.refresh(asset)

    return {"id": asset.id, "name": asset.name, "balance_cents": asset.balance_cents}


@router.get("/api/networth")
def get_networth(ctx: AccountContext = Depends(get_account_context)) -> dict:
    """Net worth summary (spec 9.8). Trend is populated starting Phase 8 monthly snapshots."""
    from sqlalchemy import func as sa_func
    from finapp.models import DebtAccount
    from finapp.services.balances import get_debt_balance_cents

    total_assets = ctx.db.query(sa_func.sum(AssetAccount.balance_cents)).filter(
        AssetAccount.account_id == ctx.account_id,
        AssetAccount.is_active == True,
    ).scalar() or 0

    debts = ctx.db.query(DebtAccount).filter_by(
        account_id=ctx.account_id, is_active=True
    ).all()
    total_debt = sum(get_debt_balance_cents(ctx, d.id) for d in debts)

    return {
        "net_worth_cents": total_assets - total_debt,
        "total_assets_cents": total_assets,
        "total_debt_cents": total_debt,
        "trend": [],
    }
```

- [ ] **Step 4: Create the assets screen template**

```html
<!-- finapp/templates/assets.html -->
{% extends "base.html" %}

{% block title %}Assets{% endblock %}

{% block content %}
<div class="container mx-auto px-4 py-8 space-y-6">
    <h1 class="text-3xl font-bold">Assets</h1>
    <p class="text-gray-600">Manually updated balance snapshots, used for your net worth.</p>

    {% if assets %}
    <div class="space-y-3">
        {% for asset in assets %}
        <div class="border rounded-lg p-4 bg-white flex justify-between items-center">
            <div>
                <h4 class="font-semibold">{{ asset.name }}</h4>
                <p class="text-gray-600 text-sm">${{ "%.2f"|format(asset.balance) }}</p>
            </div>
            <button class="bg-gray-200 hover:bg-gray-300 px-4 py-1.5 rounded text-sm font-medium text-gray-800"
                    onclick="openUpdateAssetModal({{ asset.id }}, '{{ asset.name }}', {{ asset.balance }})">
                Update Balance
            </button>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <div class="bg-gray-50 rounded-lg p-8 text-center">
        <p class="text-gray-600">No asset accounts yet</p>
    </div>
    {% endif %}

    <button class="bg-blue-500 hover:bg-blue-600 text-white px-6 py-2 rounded font-medium"
            onclick="openAddAssetModal()">
        Add Asset Account
    </button>
</div>

<script>
function openAddAssetModal() {
    const name = prompt("Account name (e.g. Checking, Savings, Cash):");
    if (!name) return;
    const balance = prompt("Current balance in dollars:");
    if (!balance) return;
    fetch('/assets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, balance_cents: Math.round(parseFloat(balance) * 100) })
    })
    .then(r => r.json())
    .then(() => location.reload())
    .catch(err => alert('Error: ' + err.message));
}

function openUpdateAssetModal(id, name, currentBalance) {
    const balance = prompt(`Update balance for ${name} (current: $${currentBalance.toFixed(2)}):`);
    if (!balance) return;
    fetch('/assets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id, name: name, balance_cents: Math.round(parseFloat(balance) * 100) })
    })
    .then(r => r.json())
    .then(() => location.reload())
    .catch(err => alert('Error: ' + err.message));
}
</script>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_savings_router.py -v
```

Expected: `PASSED (12 tests)`

- [ ] **Step 6: Commit**

```bash
git add finapp/routers/savings.py finapp/templates/assets.html tests/test_savings_router.py
git commit -m "feat: add asset account CRUD and net worth endpoint"
```

---

### Task 8: Reconciliation & Full Suite Verification

**Files:**
- No new files

- [ ] **Step 1: Run the full Phase 7 test surface**

```bash
cd d:\golden
pytest tests/test_balances.py tests/test_reconciliation.py tests/test_savings_router.py -v
```

Expected: All tests passing.

- [ ] **Step 2: Run the existing randomized reconciliation property test to confirm no regression**

```bash
pytest tests/test_reconciliation.py -k "property or random" -v
```

Expected: Passes — zero drift across generated transaction sets, including the new `is_complete` flip logic.

- [ ] **Step 3: Run the full project test suite**

```bash
pytest
```

Expected: All tests passing, no regressions in debt, budget, transactions, or dashboard suites.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: verify Phase 7 reconciliation and full suite green"
```

---

## Self-Review Against Spec

**Spec Section Coverage:**

✅ **§4.13 AssetAccount:** Model already existed (Phase 6 scaffolding); CRUD routes added in Task 7.

✅ **§4.14 Days-of-expenses coverage:** Fixed to use `kind='spending'` category filter and target-based `<30`-day fallback labeled "estimated" — Task 1.

✅ **§4.8 SavingsGoal `is_complete`/`completed_at`:** Flipped inside `apply_recomputed_balances` so it never drifts from the ledger — Task 2.

✅ **Screen 5 (Savings):** Emergency Fund card (balance vs target, %, days-of-coverage, est. complete) + Other Goals — Task 6.

✅ **Ledger integration:** Contributions/withdrawals route through `create_transaction(..., link_type='savings')`; withdrawal uses `direction='in'` (negative-direction contribution) — Task 5.

✅ **§9.8 Net Worth:** `GET /api/networth` = Σ asset balances − Σ derived debt balances, with `trend: []` placeholder (snapshot capture wired in Phase 8 per implementation plan) — Task 7.

✅ **Routes:** `GET /savings`, `POST /savings/contribution`, `POST /savings/withdrawal`, `GET /api/savings/emergency`, `GET/POST /assets`, `GET /api/networth` — Tasks 5–7.

✅ **Reconciliation gate:** `apply_recomputed_balances` covers savings balance + completion flip; full suite + randomized property test verified green — Task 8.

**Gaps:** None identified. Net worth trend data is intentionally empty (`[]`) — the implementation plan explicitly defers monthly snapshot capture to Phase 8's monthly close.

**Placeholder Scan:** No TBD/TODO/vague steps; all code blocks are complete and exact.

**Type Consistency:** `get_days_of_expenses_coverage` now returns `tuple[int, bool]` everywhere it's called (balances.py, savings.py, test files) — consistent across all tasks. `estimate_savings_completion` returns `tuple[int | None, str | None]` consistently.

---

## Plan complete and saved

Plan saved to `docs/superpowers/plans/2026-06-16-phase7-savings-networth.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec compliance, then code quality) between tasks, fast iteration with atomic commits

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach would you prefer?
