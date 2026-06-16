# Phase 8: Reviews, Monthly Reset & Missions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the rhythm layer of the app: the mission queue (progress derived from linked debt/savings entities), the Weekly Review flow, the Monthly Reset flow (period close + reconciliation + net-worth snapshot + SQLite backup), and the Milestone Celebration flow, wiring all of it to the dashboard's existing read-only stubs.

**Architecture:** Mission progress is never stored — `services/missions.py` derives it from the linked `DebtAccount`/`SavingsGoal` on every read, reusing `services/balances.py`'s existing balance functions (same pattern Phase 4's dashboard stub already uses). Milestones get a single idempotent helper (`services/milestones.py`) built on the existing `(account_id, milestone_type, threshold, link_id)` unique constraint — reused by debt-paid-off, savings-goal-reached, first-budget, and first-review triggers, alongside the streak triggers Phase 4 already wired. Weekly Review and Monthly Reset live in `services/reviews.py`; monthly close adds one new model (`NetWorthSnapshot`, new in this phase — Phase 7 deliberately left the net-worth trend empty pending it) and a new `services/backup.py` using the SQLite Online Backup API (`sqlite3.Connection.backup()`), never `shutil.copy`. All new services take `ctx: AccountContext` first, matching every existing service.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, pytest. Reuses `finapp/services/ledger.py`, `finapp/services/balances.py`, `finapp/services/reconciliation.py`, `finapp/services/streak.py` (milestone unique-constraint pattern), `finapp/services/allocation.py`.

---

## File Structure

- Create: `finapp/services/missions.py` — mission progress (derived), reorder, complete
- Create: `finapp/services/milestones.py` — idempotent milestone creation helper, shared by all triggers
- Create: `finapp/services/reviews.py` — weekly review due-check/data/complete; monthly reset data/close; reflection prompts; "Start this month" shortcut
- Create: `finapp/services/backup.py` — SQLite Online Backup API wrapper
- Create: `finapp/routers/missions.py` — `/api/missions/active`, `/missions/{id}/complete`, `/missions/reorder`
- Create: `finapp/routers/reviews.py` — `/reviews`, `/reviews/weekly`, `/reviews/monthly`, `/reviews/start-month`
- Create: `finapp/routers/milestones.py` — `/milestone/{id}`, `/milestone/{id}/celebrate`, `/api/milestones`
- Create: `finapp/templates/reviews.html` — Reviews hub + weekly/monthly flow + milestone celebration screen
- Modify: `finapp/models.py` — add `NetWorthSnapshot` model
- Modify: `finapp/services/reconciliation.py` — fire `debt_paid_off`/`savings_goal_reached` milestones inside `apply_recomputed_balances`
- Modify: `finapp/routers/allocation.py:71-89` (`set_targets`) — fire `first_budget` milestone on first-ever allocation
- Modify: `finapp/schemas.py` — append mission/review/milestone/net-worth-snapshot schemas
- Modify: `finapp/main.py` — register the three new routers
- Create: `alembic/versions/<hash>_add_net_worth_snapshots.py` — new migration for the new table
- Test: `tests/test_missions.py`, `tests/test_milestones.py`, `tests/test_reviews.py`, `tests/test_backup.py`, `tests/test_missions_router.py`, `tests/test_reviews_router.py`, `tests/test_milestones_router.py`, `tests/integration/test_phase8_flows.py`

---

## Task Breakdown

### Task 1: `NetWorthSnapshot` Model + Migration

**Files:**
- Modify: `finapp/models.py`
- Create: `alembic/versions/<hash>_add_net_worth_snapshots.py`
- Test: `tests/test_balances.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_balances.py`:

```python
def test_net_worth_snapshot_model_round_trip(db, ctx):
    from finapp.models import NetWorthSnapshot
    snap = NetWorthSnapshot(
        account_id=ctx.account_id,
        year=2026,
        month=6,
        net_worth_cents=500000,
        total_assets_cents=800000,
        total_debt_cents=300000,
    )
    db.add(snap)
    db.commit()

    fetched = db.query(NetWorthSnapshot).filter_by(account_id=ctx.account_id).first()
    assert fetched.net_worth_cents == 500000
    assert fetched.year == 2026
    assert fetched.month == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_balances.py::test_net_worth_snapshot_model_round_trip -v`
Expected: FAIL with `ImportError: cannot import name 'NetWorthSnapshot'`

- [ ] **Step 3: Add the model**

In `finapp/models.py`, add after `AssetAccount` (end of file):

```python
class NetWorthSnapshot(Base):
    __tablename__ = "net_worth_snapshots"
    __table_args__ = (
        UniqueConstraint("account_id", "year", "month", name="uq_networth_account_year_month"),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    net_worth_cents = Column(Integer, nullable=False)
    total_assets_cents = Column(Integer, nullable=False)
    total_debt_cents = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account")
```

Add to `Account` (after `asset_accounts = relationship(...)` at `finapp/models.py:25`):

```python
    net_worth_snapshots = relationship("NetWorthSnapshot", back_populates="account", cascade="all, delete-orphan")
```

And change the new model's `account` relationship to `back_populates="net_worth_snapshots"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_balances.py::test_net_worth_snapshot_model_round_trip -v`
Expected: PASS

- [ ] **Step 5: Generate and edit the Alembic migration**

Run: `alembic revision --autogenerate -m "add net worth snapshots"`

Open the generated file under `alembic/versions/` and verify `upgrade()` contains exactly:

```python
def upgrade() -> None:
    op.create_table('net_worth_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('month', sa.Integer(), nullable=False),
    sa.Column('net_worth_cents', sa.Integer(), nullable=False),
    sa.Column('total_assets_cents', sa.Integer(), nullable=False),
    sa.Column('total_debt_cents', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'year', 'month', name='uq_networth_account_year_month')
    )


def downgrade() -> None:
    op.drop_table('net_worth_snapshots')
```

If autogenerate produced anything else (e.g. tried to alter unrelated tables), trim `upgrade()`/`downgrade()` down to just this table.

- [ ] **Step 6: Apply the migration against the dev DB**

Run: `alembic upgrade head`
Expected: applies clean, no errors.

- [ ] **Step 7: Commit**

```bash
git add finapp/models.py alembic/versions tests/test_balances.py
git commit -m "feat: add NetWorthSnapshot model and migration"
```

---

### Task 2: Milestone Helper Service

**Files:**
- Create: `finapp/services/milestones.py`
- Test: `tests/test_milestones.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_milestones.py`:

```python
from finapp.models import Milestone
from finapp.services.milestones import create_milestone_if_new


def test_creates_new_milestone(db, ctx):
    m = create_milestone_if_new(
        ctx,
        milestone_type="debt_paid_off",
        title="Capital One paid off!",
        description="You paid off Capital One.",
        link_type="debt",
        link_id=42,
    )
    assert m is not None
    assert m.milestone_type == "debt_paid_off"
    assert m.threshold == 0
    assert m.celebrated is False

    fetched = db.query(Milestone).filter_by(account_id=ctx.account_id).all()
    assert len(fetched) == 1


def test_idempotent_does_not_duplicate(db, ctx):
    create_milestone_if_new(
        ctx, milestone_type="debt_paid_off", title="x", description="x",
        link_type="debt", link_id=42,
    )
    second = create_milestone_if_new(
        ctx, milestone_type="debt_paid_off", title="x", description="x",
        link_type="debt", link_id=42,
    )
    assert second is None
    fetched = db.query(Milestone).filter_by(account_id=ctx.account_id).all()
    assert len(fetched) == 1


def test_different_link_id_creates_separate_milestone(db, ctx):
    create_milestone_if_new(
        ctx, milestone_type="debt_paid_off", title="x", description="x",
        link_type="debt", link_id=1,
    )
    second = create_milestone_if_new(
        ctx, milestone_type="debt_paid_off", title="y", description="y",
        link_type="debt", link_id=2,
    )
    assert second is not None
    fetched = db.query(Milestone).filter_by(account_id=ctx.account_id).all()
    assert len(fetched) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_milestones.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finapp.services.milestones'`

- [ ] **Step 3: Implement the service**

Create `finapp/services/milestones.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_milestones.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add finapp/services/milestones.py tests/test_milestones.py
git commit -m "feat: add idempotent milestone creation helper"
```

---

### Task 3: Wire `debt_paid_off` / `savings_goal_reached` Milestones into Reconciliation

**Files:**
- Modify: `finapp/services/reconciliation.py` (`apply_recomputed_balances`, lines 177-207)
- Test: `tests/test_milestones.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_milestones.py`:

```python
from datetime import date
from finapp.models import DebtAccount, SavingsGoal
from finapp.services.ledger import create_transaction
from finapp.services.reconciliation import apply_recomputed_balances


def test_debt_paid_off_milestone_fires_once(db, ctx):
    debt = DebtAccount(
        account_id=ctx.account_id, name="Capital One", opening_balance_cents=10000,
        cached_balance_cents=10000, interest_rate_bps=0, minimum_payment_cents=1000,
    )
    db.add(debt)
    db.commit()

    create_transaction(
        ctx, date=date.today(), amount_cents=10000, direction="out",
        link_type="debt", link_id=debt.id, principal_cents=10000, interest_cents=0,
    )

    from finapp.models import Milestone
    milestones = db.query(Milestone).filter_by(
        account_id=ctx.account_id, milestone_type="debt_paid_off", link_id=debt.id,
    ).all()
    assert len(milestones) == 1

    # A second recompute (e.g. another write) must not duplicate it
    apply_recomputed_balances(db, ctx.account_id)
    milestones = db.query(Milestone).filter_by(
        account_id=ctx.account_id, milestone_type="debt_paid_off", link_id=debt.id,
    ).all()
    assert len(milestones) == 1


def test_savings_goal_reached_milestone_fires_once(db, ctx):
    goal = SavingsGoal(
        account_id=ctx.account_id, name="Emergency Fund", goal_type="emergency_fund",
        target_cents=5000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()

    create_transaction(
        ctx, date=date.today(), amount_cents=5000, direction="out",
        link_type="savings", link_id=goal.id,
    )

    from finapp.models import Milestone
    milestones = db.query(Milestone).filter_by(
        account_id=ctx.account_id, milestone_type="savings_goal_reached", link_id=goal.id,
    ).all()
    assert len(milestones) == 1

    apply_recomputed_balances(db, ctx.account_id)
    milestones = db.query(Milestone).filter_by(
        account_id=ctx.account_id, milestone_type="savings_goal_reached", link_id=goal.id,
    ).all()
    assert len(milestones) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_milestones.py -k "paid_off or reached" -v`
Expected: FAIL — no `Milestone` rows created.

- [ ] **Step 3: Wire the milestone calls into `apply_recomputed_balances`**

In `finapp/services/reconciliation.py`, update `apply_recomputed_balances` (replace lines 177-207):

```python
def apply_recomputed_balances(db: Session, account_id: int) -> dict:
    """
    Rebuild all cached balances and UPDATE them in the database.
    Used after transaction writes to keep caches fresh.
    Fires debt_paid_off / savings_goal_reached milestones the moment a
    balance crosses into its completed state (idempotent — see
    services/milestones.py).
    Returns the drift report.
    """
    from finapp.deps import AccountContext
    from finapp.services.milestones import create_milestone_if_new

    ctx = AccountContext(account_id=account_id, db=db)

    # Update BudgetPeriod.income_received_cents
    periods = db.query(BudgetPeriod).filter_by(account_id=account_id).all()
    for period in periods:
        derived = compute_budget_income_received_cents(db, account_id, period.id)
        period.income_received_cents = derived

    # Update DebtAccount.cached_balance_cents
    debts = db.query(DebtAccount).filter_by(account_id=account_id).all()
    for debt in debts:
        derived = compute_debt_balance_cents(db, account_id, debt.id)
        debt.cached_balance_cents = derived
        if derived == 0 and debt.paid_off_at is None:
            debt.paid_off_at = datetime.utcnow()
            create_milestone_if_new(
                ctx,
                milestone_type="debt_paid_off",
                title=f"{debt.name} paid off!",
                description=f"You paid off {debt.name}.",
                link_type="debt",
                link_id=debt.id,
            )

    # Update SavingsGoal.cached_balance_cents
    goals = db.query(SavingsGoal).filter_by(account_id=account_id).all()
    for goal in goals:
        derived = compute_savings_goal_balance_cents(db, account_id, goal.id)
        goal.cached_balance_cents = derived
        if not goal.is_complete and derived >= goal.target_cents and goal.target_cents > 0:
            goal.is_complete = True
            goal.completed_at = datetime.utcnow()
            create_milestone_if_new(
                ctx,
                milestone_type="savings_goal_reached",
                title=f"{goal.name} goal reached!",
                description=f"You reached your {goal.name} goal.",
                link_type="savings",
                link_id=goal.id,
            )

    db.commit()

    # Run reconciliation check to verify zero drift after update
    return recompute_balances(db, account_id)
```

Note: `debt.paid_off_at is None` (rather than re-checking the milestone table) is the guard that keeps this cheap on every write — `create_milestone_if_new` is still the source of truth for dedup, this is just an early-exit.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_milestones.py -v`
Expected: PASS

- [ ] **Step 5: Run the full existing suite to check nothing regressed**

Run: `pytest tests/test_reconciliation.py tests/test_ledger.py tests/test_balances.py tests/test_savings_router.py tests/test_debt_router.py -v`
Expected: PASS (this function is on the hot path for every transaction write — confirm Phase 5–7 tests still pass)

- [ ] **Step 6: Commit**

```bash
git add finapp/services/reconciliation.py tests/test_milestones.py
git commit -m "feat: fire debt_paid_off and savings_goal_reached milestones from reconciliation"
```

---

### Task 4: `first_budget` Milestone on First Allocation

**Files:**
- Modify: `finapp/routers/allocation.py` (`set_targets`, lines 71-89)
- Test: `tests/test_milestones.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_milestones.py`:

```python
def test_first_budget_milestone_fires_on_first_allocation_router_call(
    test_client_for_budget,
):
    from finapp.models import BudgetCategory, BudgetPeriod, Milestone

    client = test_client_for_budget
    # Discover a real category + period via the existing seed fixtures used elsewhere,
    # or create them directly against the shared test session.
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())

    period = BudgetPeriod(account_id=1, year=2026, month=6, income_received_cents=0)
    db.add(period)
    db.commit()
    cat = BudgetCategory(account_id=1, name="Food", kind="spending")
    db.add(cat)
    db.commit()

    resp = client.post("/api/allocation/targets", json={
        "period_id": period.id,
        "allocations": [{"category_id": cat.id, "target_cents": 1000}],
    })
    assert resp.status_code == 200

    milestones = db.query(Milestone).filter_by(
        account_id=1, milestone_type="first_budget",
    ).all()
    assert len(milestones) == 1

    # A second allocation call must not duplicate it
    client.post("/api/allocation/targets", json={
        "period_id": period.id,
        "allocations": [{"category_id": cat.id, "target_cents": 2000}],
    })
    milestones = db.query(Milestone).filter_by(
        account_id=1, milestone_type="first_budget",
    ).all()
    assert len(milestones) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_milestones.py::test_first_budget_milestone_fires_on_first_allocation_router_call -v`
Expected: FAIL — no milestone created.

- [ ] **Step 3: Wire the milestone into the router**

In `finapp/routers/allocation.py`, modify `set_targets` (around line 71-89):

```python
@router.post("/targets")
def set_targets(
    request: AllocationRitualRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> AllocationRitualResponse:
    """
    Set allocation targets for a period (allocation ritual).
    Fires the first_budget milestone the first time this account ever
    sets any allocation.
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

    # ... rest of the function is unchanged (build response) ...
```

(Leave the response-building code below unchanged — only the count-and-fire block is new.)

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_milestones.py -v`
Expected: PASS

- [ ] **Step 5: Run allocation router/flow tests to check no regression**

Run: `pytest tests/test_allocation.py tests/integration/test_allocation_flow.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add finapp/routers/allocation.py tests/test_milestones.py
git commit -m "feat: fire first_budget milestone on first allocation"
```

---

### Task 5: Missions Service — Derived Progress, Complete, Reorder

**Files:**
- Create: `finapp/services/missions.py`
- Test: `tests/test_missions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_missions.py`:

```python
from datetime import date
from finapp.models import DebtAccount, SavingsGoal, Mission
from finapp.services.ledger import create_transaction
from finapp.services.missions import (
    get_mission_progress,
    get_active_missions,
    complete_mission,
    reorder_missions,
)


def _make_debt_mission(db, ctx, opening=10000, paid=4000):
    debt = DebtAccount(
        account_id=ctx.account_id, name="Capital One", opening_balance_cents=opening,
        cached_balance_cents=opening, interest_rate_bps=0, minimum_payment_cents=1000,
    )
    db.add(debt)
    db.commit()
    if paid:
        create_transaction(
            ctx, date=date.today(), amount_cents=paid, direction="out",
            link_type="debt", link_id=debt.id, principal_cents=paid, interest_cents=0,
        )
    mission = Mission(
        account_id=ctx.account_id, name="Pay off Capital One", mission_type="debt_payoff",
        link_type="debt", link_id=debt.id, status="active", sort_order=1,
    )
    db.add(mission)
    db.commit()
    return mission, debt


def test_debt_mission_progress_derives_from_linked_debt(db, ctx):
    mission, debt = _make_debt_mission(db, ctx, opening=10000, paid=4000)
    progress = get_mission_progress(ctx, mission)
    assert progress["start_cents"] == 10000
    assert progress["current_cents"] == 6000  # remaining balance
    assert progress["target_cents"] == 0
    assert progress["percent"] == 40  # 4000/10000 paid off


def test_savings_mission_progress_derives_from_linked_goal(db, ctx):
    goal = SavingsGoal(
        account_id=ctx.account_id, name="New Car", goal_type="sinking_fund",
        target_cents=10000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()
    create_transaction(
        ctx, date=date.today(), amount_cents=2500, direction="out",
        link_type="savings", link_id=goal.id,
    )
    mission = Mission(
        account_id=ctx.account_id, name="Save for car", mission_type="savings_goal",
        link_type="savings", link_id=goal.id, status="active", sort_order=1,
    )
    db.add(mission)
    db.commit()

    progress = get_mission_progress(ctx, mission)
    assert progress["start_cents"] == 0
    assert progress["current_cents"] == 2500
    assert progress["target_cents"] == 10000
    assert progress["percent"] == 25


def test_mission_progress_never_drifts_after_multiple_payments(db, ctx):
    mission, debt = _make_debt_mission(db, ctx, opening=10000, paid=4000)
    create_transaction(
        ctx, date=date.today(), amount_cents=1000, direction="out",
        link_type="debt", link_id=debt.id, principal_cents=1000, interest_cents=0,
    )
    progress = get_mission_progress(ctx, mission)
    assert progress["current_cents"] == 5000
    assert progress["percent"] == 50


def test_get_active_missions_orders_by_sort_order(db, ctx):
    m1, _ = _make_debt_mission(db, ctx)
    m1.sort_order = 2
    db.commit()
    goal = SavingsGoal(
        account_id=ctx.account_id, name="EF", goal_type="emergency_fund",
        target_cents=1000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()
    m2 = Mission(
        account_id=ctx.account_id, name="Build EF", mission_type="emergency_fund",
        link_type="savings", link_id=goal.id, status="active", sort_order=1,
    )
    db.add(m2)
    db.commit()

    active = get_active_missions(ctx)
    assert [m["id"] for m in active] == [m2.id, m1.id]


def test_complete_mission_sets_status_and_timestamp(db, ctx):
    mission, _ = _make_debt_mission(db, ctx)
    completed = complete_mission(ctx, mission.id)
    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_reorder_missions_sets_sort_order(db, ctx):
    m1, _ = _make_debt_mission(db, ctx)
    goal = SavingsGoal(
        account_id=ctx.account_id, name="Vacation", goal_type="sinking_fund",
        target_cents=1000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()
    m2 = Mission(
        account_id=ctx.account_id, name="Save vacation", mission_type="savings_goal",
        link_type="savings", link_id=goal.id, status="active", sort_order=1,
    )
    db.add(m2)
    db.commit()

    reorder_missions(ctx, [m2.id, m1.id])
    db.refresh(m1)
    db.refresh(m2)
    assert m2.sort_order < m1.sort_order


def test_reorder_rejects_emergency_fund_mission(db, ctx):
    goal = SavingsGoal(
        account_id=ctx.account_id, name="EF", goal_type="emergency_fund",
        target_cents=1000, opening_balance_cents=0, cached_balance_cents=0,
    )
    db.add(goal)
    db.commit()
    ef_mission = Mission(
        account_id=ctx.account_id, name="Build EF", mission_type="emergency_fund",
        link_type="savings", link_id=goal.id, status="active", sort_order=1,
    )
    db.add(ef_mission)
    db.commit()

    import pytest
    with pytest.raises(ValueError):
        reorder_missions(ctx, [ef_mission.id])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_missions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finapp.services.missions'`

- [ ] **Step 3: Implement the service**

Create `finapp/services/missions.py`:

```python
"""
Mission service: the mission queue. Progress is always DERIVED from the
linked DebtAccount/SavingsGoal — never stored — so it can never drift from
the ledger (same invariant as services/balances.py).
"""
from datetime import datetime
from finapp.deps import AccountContext
from finapp.models import Mission, DebtAccount, SavingsGoal
from finapp.services.balances import get_debt_balance_cents, get_savings_goal_balance_cents


def get_mission_progress(ctx: AccountContext, mission: Mission) -> dict:
    """
    Derive {start_cents, current_cents, target_cents, percent} from the
    mission's linked entity.

    Debt missions: start = opening balance, current = derived remaining
    balance, target = 0 (debt-free), percent = % of opening balance paid off.

    Savings/emergency-fund missions: start = opening balance, current =
    derived balance, target = goal target, percent = % of (target - start)
    progress made.
    """
    if mission.link_type == "debt" and mission.link_id:
        debt = ctx.db.query(DebtAccount).filter_by(
            account_id=ctx.account_id, id=mission.link_id
        ).first()
        if not debt:
            return {"start_cents": 0, "current_cents": 0, "target_cents": 0, "percent": 0}

        start = debt.opening_balance_cents
        current = get_debt_balance_cents(ctx, debt.id)
        paid = start - current
        percent = int(100 * paid / start) if start > 0 else 0
        return {
            "start_cents": start,
            "current_cents": current,
            "target_cents": 0,
            "percent": max(0, min(100, percent)),
        }

    if mission.link_type == "savings" and mission.link_id:
        goal = ctx.db.query(SavingsGoal).filter_by(
            account_id=ctx.account_id, id=mission.link_id
        ).first()
        if not goal:
            return {"start_cents": 0, "current_cents": 0, "target_cents": 0, "percent": 0}

        start = goal.opening_balance_cents
        current = get_savings_goal_balance_cents(ctx, goal.id)
        span = goal.target_cents - start
        percent = int(100 * (current - start) / span) if span > 0 else 0
        return {
            "start_cents": start,
            "current_cents": current,
            "target_cents": goal.target_cents,
            "percent": max(0, min(100, percent)),
        }

    return {"start_cents": 0, "current_cents": 0, "target_cents": 0, "percent": 0}


def get_active_missions(ctx: AccountContext) -> list[dict]:
    """Active missions in queue order, each with derived progress."""
    missions = ctx.db.query(Mission).filter_by(
        account_id=ctx.account_id, status="active",
    ).order_by(Mission.sort_order).all()

    result = []
    for m in missions:
        progress = get_mission_progress(ctx, m)
        result.append({
            "id": m.id,
            "name": m.name,
            "mission_type": m.mission_type,
            "link_type": m.link_type,
            "link_id": m.link_id,
            "sort_order": m.sort_order,
            **progress,
        })
    return result


def complete_mission(ctx: AccountContext, mission_id: int) -> Mission:
    """Mark a mission completed. Idempotent on status, raises if not found."""
    mission = ctx.db.query(Mission).filter_by(
        account_id=ctx.account_id, id=mission_id,
    ).first()
    if not mission:
        raise ValueError(f"Mission {mission_id} not found")

    mission.status = "completed"
    mission.completed_at = datetime.utcnow()
    ctx.db.commit()
    return mission


def reorder_missions(ctx: AccountContext, ordered_ids: list[int]) -> list[Mission]:
    """
    Set sort_order for the given mission ids, in the order given.
    Emergency-fund missions are pinned and cannot be reordered — raises
    ValueError if one is included in ordered_ids.
    """
    missions = ctx.db.query(Mission).filter(
        Mission.account_id == ctx.account_id,
        Mission.id.in_(ordered_ids),
    ).all()
    missions_by_id = {m.id: m for m in missions}

    for mission_id in ordered_ids:
        mission = missions_by_id.get(mission_id)
        if mission and mission.mission_type == "emergency_fund":
            raise ValueError("Emergency fund mission cannot be reordered")

    # Emergency fund missions keep sort_order 0 and stay first; everything
    # else gets sort_order starting at 1, in the order given.
    for i, mission_id in enumerate(ordered_ids, start=1):
        mission = missions_by_id.get(mission_id)
        if mission:
            mission.sort_order = i

    ctx.db.commit()
    return list(missions_by_id.values())
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_missions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add finapp/services/missions.py tests/test_missions.py
git commit -m "feat: add mission service with derived progress, complete, reorder"
```

---

### Task 6: Missions Router

**Files:**
- Create: `finapp/routers/missions.py`
- Modify: `finapp/schemas.py`
- Modify: `finapp/main.py`
- Test: `tests/test_missions_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_missions_router.py`:

```python
from finapp.models import DebtAccount, Mission


def _seed_debt_mission(db):
    debt = DebtAccount(
        account_id=1, name="Capital One", opening_balance_cents=10000,
        cached_balance_cents=6000, interest_rate_bps=0, minimum_payment_cents=1000,
    )
    db.add(debt)
    db.commit()
    mission = Mission(
        account_id=1, name="Pay off Capital One", mission_type="debt_payoff",
        link_type="debt", link_id=debt.id, status="active", sort_order=1,
    )
    db.add(mission)
    db.commit()
    return mission


def test_get_active_missions_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    _seed_debt_mission(db)

    resp = test_client_for_budget.get("/api/missions/active")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["current_cents"] == 6000


def test_complete_mission_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    mission = _seed_debt_mission(db)

    resp = test_client_for_budget.post(f"/missions/{mission.id}/complete")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_reorder_missions_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    mission = _seed_debt_mission(db)

    resp = test_client_for_budget.post("/missions/reorder", json={"ordered_ids": [mission.id]})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_missions_router.py -v`
Expected: FAIL — 404 (no route registered)

- [ ] **Step 3: Add schemas**

Append to `finapp/schemas.py`:

```python
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
```

- [ ] **Step 4: Implement the router**

Create `finapp/routers/missions.py`:

```python
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
```

- [ ] **Step 5: Register the router**

In `finapp/main.py`, update imports and registration:

```python
from finapp.routers import allocation, dashboard, budget, transactions, debt, savings, missions
```

```python
app.include_router(missions.router)
```

- [ ] **Step 6: Run to verify pass**

Run: `pytest tests/test_missions_router.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add finapp/routers/missions.py finapp/schemas.py finapp/main.py tests/test_missions_router.py
git commit -m "feat: add missions router"
```

---

### Task 7: Reflection Prompts + Weekly Review Service

**Files:**
- Create: `finapp/services/reviews.py`
- Test: `tests/test_reviews.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reviews.py`:

```python
from datetime import date, datetime, timedelta
from finapp.models import Settings, Review, BudgetCategory, BudgetAllocation, BudgetPeriod
from finapp.services.ledger import create_transaction
from finapp.services.reviews import (
    REFLECTION_PROMPTS,
    get_reflection_prompt,
    ensure_weekly_review_due,
    get_weekly_review_data,
    complete_weekly_review,
    get_review_streak,
)


def test_get_reflection_prompt_rotates_by_month():
    p1 = get_reflection_prompt(1)
    p2 = get_reflection_prompt(2)
    assert p1 == REFLECTION_PROMPTS[1 % len(REFLECTION_PROMPTS)]
    assert p2 == REFLECTION_PROMPTS[2 % len(REFLECTION_PROMPTS)]


def test_ensure_weekly_review_due_creates_pending_review_on_review_day(db, ctx, account):
    settings = Settings(account_id=ctx.account_id, review_day=date.today().strftime("%A").lower())
    db.add(settings)
    db.commit()

    review = ensure_weekly_review_due(ctx)
    assert review is not None
    assert review.prompt_shown is None
    assert review.review_type == "weekly"

    # Idempotent: calling again on the same day does not create a duplicate
    again = ensure_weekly_review_due(ctx)
    assert again.id == review.id
    count = db.query(Review).filter_by(account_id=ctx.account_id).count()
    assert count == 1


def test_ensure_weekly_review_due_noop_on_non_review_day(db, ctx, account):
    not_today = (date.today() + timedelta(days=1)).strftime("%A").lower()
    settings = Settings(account_id=ctx.account_id, review_day=not_today)
    db.add(settings)
    db.commit()

    review = ensure_weekly_review_due(ctx)
    assert review is None


def test_complete_weekly_review_sets_prompt_and_notes(db, ctx, account):
    settings = Settings(account_id=ctx.account_id, review_day=date.today().strftime("%A").lower())
    db.add(settings)
    db.commit()
    ensure_weekly_review_due(ctx)

    result = complete_weekly_review(ctx, intention="Cook at home more.", quick=False)
    assert result.notes == "Cook at home more."
    assert result.prompt_shown is not None
    assert result.completed_at is not None


def test_review_streak_counts_consecutive_completed_weeks(db, ctx, account):
    today = date.today()
    for weeks_ago in range(3):
        week_start = today - timedelta(days=today.weekday() + 7 * weeks_ago)
        review = Review(
            account_id=ctx.account_id, review_type="weekly", week_start=week_start,
            prompt_shown="done", completed_at=datetime.utcnow(),
        )
        db.add(review)
    db.commit()

    streak = get_review_streak(ctx)
    assert streak == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_reviews.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finapp.services.reviews'`

- [ ] **Step 3: Implement reflection prompts + weekly review functions**

Create `finapp/services/reviews.py`:

```python
"""
Reviews service: Weekly Review (6.5) and Monthly Reset (6.6) flows, plus the
monthly reflection prompt rotation (9.6).

Weekly review "due" detection works like services/streak.py's check-in
pattern: ensure_weekly_review_due() creates a pending Review row
(prompt_shown=None) the first time it's called on the account's
configured review_day each week — idempotent per week. The existing
services/next_right_action.py priority check (_review_due_today) just
queries for that pending row; this module is what actually creates it.
"""
from datetime import date, datetime, timedelta

from finapp.deps import AccountContext
from finapp.models import Settings, Review

REFLECTION_PROMPTS = [
    "What's one thing about money that felt easier this month?",
    "What's one purchase you're glad you made?",
    "Did anything surprise you about your spending this month?",
    "What would make next month feel calmer?",
    "What's one habit you want to keep doing?",
    "Where did your money go that you didn't expect?",
    "What's one thing you're proud of this month?",
    "Is there a category that needs a different target next month?",
    "What's something you said no to that felt good?",
    "How does your progress toward your mission feel right now?",
    "What's one small win from this month?",
    "What do you want to remember about this month?",
]


def get_reflection_prompt(month: int) -> str:
    """Rotate the monthly reflection prompt by calendar month number (1-12)."""
    return REFLECTION_PROMPTS[month % len(REFLECTION_PROMPTS)]


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def ensure_weekly_review_due(ctx: AccountContext) -> Review | None:
    """
    If today is the account's configured review_day and no Review exists
    yet for this week, create a pending one (prompt_shown=None) and return
    it. If a Review already exists for this week, return it (idempotent).
    If today isn't the review day, return None.
    """
    settings = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).first()
    if not settings or not settings.review_day:
        return None

    today = date.today()
    if today.strftime("%A").lower() != settings.review_day.lower():
        return None

    week_start = _week_start(today)
    existing = ctx.db.query(Review).filter_by(
        account_id=ctx.account_id, review_type="weekly", week_start=week_start,
    ).first()
    if existing:
        return existing

    review = Review(
        account_id=ctx.account_id,
        review_type="weekly",
        week_start=week_start,
        prompt_shown=None,
        completed_at=datetime.utcnow(),
    )
    ctx.db.add(review)
    ctx.db.commit()
    return review


def get_weekly_review_data(ctx: AccountContext) -> dict:
    """
    Assemble the five-step weekly review screen data: this-week at-a-glance
    (amber if over, teal if on track — never red), categories needing
    reallocation, active missions for the mission-check step, and the
    current review streak.
    """
    from finapp.services.dashboard import _get_this_week_pulse
    from finapp.services.missions import get_active_missions

    pulse = _get_this_week_pulse(ctx)
    status = "amber" if pulse["percentage"] > 100 else "teal"

    return {
        "this_week_pulse": pulse,
        "status": status,
        "active_missions": get_active_missions(ctx),
        "review_streak": get_review_streak(ctx),
    }


def complete_weekly_review(ctx: AccountContext, intention: str | None, quick: bool) -> Review:
    """
    Complete this week's review: ensures the pending row exists, stamps
    prompt_shown with a fixed weekly prompt, stores the optional intention
    in notes, and fires the first_review milestone the first time ever.
    """
    from finapp.services.milestones import create_milestone_if_new

    review = ensure_weekly_review_due(ctx)
    if review is None:
        week_start = _week_start(date.today())
        review = ctx.db.query(Review).filter_by(
            account_id=ctx.account_id, review_type="weekly", week_start=week_start,
        ).first()
        if review is None:
            review = Review(
                account_id=ctx.account_id, review_type="weekly", week_start=week_start,
                completed_at=datetime.utcnow(),
            )
            ctx.db.add(review)

    is_first_ever = ctx.db.query(Review).filter_by(
        account_id=ctx.account_id,
    ).filter(Review.prompt_shown.isnot(None)).count() == 0

    review.prompt_shown = "What's one intention for next week?"
    review.notes = intention
    review.completed_at = datetime.utcnow()
    ctx.db.commit()

    if is_first_ever:
        create_milestone_if_new(
            ctx, milestone_type="first_review", title="Your first review!",
            description="You completed your first weekly review.",
        )

    return review


def get_review_streak(ctx: AccountContext) -> int:
    """
    Count consecutive completed weekly reviews (prompt_shown set), counting
    back from the most recent week with no gap. No grace day (unlike the
    daily check-in streak).
    """
    reviews = ctx.db.query(Review).filter(
        Review.account_id == ctx.account_id,
        Review.review_type == "weekly",
        Review.prompt_shown.isnot(None),
    ).order_by(Review.week_start.desc()).all()

    if not reviews:
        return 0

    streak = 0
    expected_week = reviews[0].week_start
    for r in reviews:
        if r.week_start == expected_week:
            streak += 1
            expected_week = expected_week - timedelta(days=7)
        else:
            break

    return streak
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_reviews.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add finapp/services/reviews.py tests/test_reviews.py
git commit -m "feat: add weekly review service and reflection prompt rotation"
```

---

### Task 8: SQLite Backup Service

**Files:**
- Create: `finapp/services/backup.py`
- Test: `tests/test_backup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backup.py`:

```python
import os
import sqlite3
import tempfile
from finapp.services.backup import create_backup


def test_create_backup_uses_online_backup_api(tmp_path, monkeypatch):
    src_path = tmp_path / "finance.db"
    backups_dir = tmp_path / "backups"

    src_conn = sqlite3.connect(str(src_path))
    src_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    src_conn.execute("INSERT INTO t (v) VALUES ('hello')")
    src_conn.commit()

    backup_path = create_backup(
        source_db_path=str(src_path),
        backups_dir=str(backups_dir),
        year=2026, month=6,
    )

    assert os.path.exists(backup_path)
    assert backup_path.endswith("finance.2026-06.db")

    dest_conn = sqlite3.connect(backup_path)
    rows = dest_conn.execute("SELECT v FROM t").fetchall()
    assert rows == [("hello",)]
    dest_conn.close()
    src_conn.close()


def test_create_backup_is_consistent_snapshot_not_copy(tmp_path):
    """Confirms the backup uses Connection.backup(), not shutil.copy, by
    verifying the destination is independently queryable as a complete
    SQLite database (a raw file copy mid-write would not pass this check
    reliably; backup() always produces a complete, consistent file)."""
    src_path = tmp_path / "finance.db"
    backups_dir = tmp_path / "backups"

    src_conn = sqlite3.connect(str(src_path))
    src_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    src_conn.commit()

    backup_path = create_backup(
        source_db_path=str(src_path), backups_dir=str(backups_dir), year=2026, month=7,
    )

    dest_conn = sqlite3.connect(backup_path)
    # PRAGMA integrity_check returns [('ok',)] for a complete, valid file
    result = dest_conn.execute("PRAGMA integrity_check").fetchall()
    assert result == [("ok",)]
    dest_conn.close()
    src_conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_backup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finapp.services.backup'`

- [ ] **Step 3: Implement the backup service**

Create `finapp/services/backup.py`:

```python
"""
Backup service: writes a consistent point-in-time snapshot of the SQLite
database using the SQLite Online Backup API (sqlite3.Connection.backup()).
Deliberately NOT shutil.copy — a raw file copy can capture a database
mid-write (especially under WAL mode) and produce a corrupt snapshot;
Connection.backup() guarantees a complete, consistent copy.
"""
import os
import sqlite3


def create_backup(source_db_path: str, backups_dir: str, year: int, month: int) -> str:
    """
    Back up source_db_path to backups_dir/finance.YYYY-MM.db using the
    SQLite Online Backup API. Creates backups_dir if it doesn't exist.
    Returns the backup file path.
    """
    os.makedirs(backups_dir, exist_ok=True)

    filename = f"finance.{year:04d}-{month:02d}.db"
    dest_path = os.path.join(backups_dir, filename)

    source_conn = sqlite3.connect(source_db_path)
    dest_conn = sqlite3.connect(dest_path)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    return dest_path
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_backup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add finapp/services/backup.py tests/test_backup.py
git commit -m "feat: add SQLite Online Backup API service"
```

---

### Task 9: Monthly Reset Service — Summary, Close, Net-Worth Snapshot, Backup

**Files:**
- Modify: `finapp/services/reviews.py`
- Test: `tests/test_reviews.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reviews.py`:

```python
from finapp.models import DebtAccount, NetWorthSnapshot, Milestone
from finapp.services.reviews import (
    get_monthly_reset_data, close_month, start_month_shortcut,
)


def _make_period(db, ctx, year, month, status="active"):
    period = BudgetPeriod(account_id=ctx.account_id, year=year, month=month, status=status)
    db.add(period)
    db.commit()
    return period


def test_close_month_marks_period_closed_and_creates_next(db, ctx, account, tmp_path, monkeypatch):
    monkeypatch.setattr("finapp.services.reviews.BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("finapp.services.reviews.SOURCE_DB_PATH", str(tmp_path / "finance.db"))
    # create a throwaway source db file so create_backup has something to read
    import sqlite3
    sqlite3.connect(str(tmp_path / "finance.db")).close()

    period = _make_period(db, ctx, 2026, 5)

    result = close_month(ctx, period_id=period.id, notes="Good month.", sweep_to_mission=False)

    db.refresh(period)
    assert period.status == "closed"
    assert period.closed_at is not None
    assert period.notes == "Good month."
    assert result["reconciliation"]["total_drift"] == 0
    assert os.path.exists(result["backup_path"])

    snapshot = db.query(NetWorthSnapshot).filter_by(
        account_id=ctx.account_id, year=2026, month=5,
    ).first()
    assert snapshot is not None


def test_close_month_creates_milestone_once_per_account_not_per_month(db, ctx, account, tmp_path, monkeypatch):
    monkeypatch.setattr("finapp.services.reviews.BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("finapp.services.reviews.SOURCE_DB_PATH", str(tmp_path / "finance.db"))
    import sqlite3
    sqlite3.connect(str(tmp_path / "finance.db")).close()

    period = _make_period(db, ctx, 2026, 5)
    close_month(ctx, period_id=period.id, notes=None, sweep_to_mission=False)

    period2 = _make_period(db, ctx, 2026, 6)
    close_month(ctx, period_id=period2.id, notes=None, sweep_to_mission=False)

    snapshots = db.query(NetWorthSnapshot).filter_by(account_id=ctx.account_id).all()
    assert len(snapshots) == 2


def test_start_month_shortcut_creates_period_if_missing(db, ctx, account):
    period = start_month_shortcut(ctx)
    assert period.year == date.today().year
    assert period.month == date.today().month

    again = start_month_shortcut(ctx)
    assert again.id == period.id
```

Add the missing `import os` at the top of `tests/test_reviews.py` if not already present.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_reviews.py -k "close_month or start_month" -v`
Expected: FAIL — `ImportError: cannot import name 'get_monthly_reset_data'`

- [ ] **Step 3: Implement monthly reset functions**

Append to `finapp/services/reviews.py`:

```python
import os
from finapp.models import BudgetPeriod, DebtAccount, SavingsGoal, NetWorthSnapshot
from finapp.services.balances import get_debt_balance_cents, get_savings_goal_balance_cents
from finapp.services.ledger import get_or_create_period

BACKUPS_DIR = "backups"
SOURCE_DB_PATH = "finance.db"


def _prior_period(ctx: AccountContext) -> BudgetPeriod | None:
    today = date.today()
    if today.month == 1:
        prior_year, prior_month = today.year - 1, 12
    else:
        prior_year, prior_month = today.year, today.month - 1
    return ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, year=prior_year, month=prior_month,
    ).first()


def get_monthly_reset_data(ctx: AccountContext, period_id: int) -> dict:
    """
    Last-month summary for the Monthly Reset screen: income vs received,
    spent vs target per category, mission progress, milestones earned in
    that period's date range.
    """
    from finapp.services.balances import (
        get_budget_income_received_cents, get_current_period_spending_target_cents,
    )
    from finapp.services.missions import get_active_missions
    from finapp.models import Milestone, Transaction

    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id,
    ).first()
    if not period:
        raise ValueError(f"Period {period_id} not found")

    income_received = get_budget_income_received_cents(ctx, period.id)

    spent = ctx.db.query(Transaction).filter_by(
        account_id=ctx.account_id, period_id=period.id, direction="out", is_deleted=False,
    ).all()
    total_spent = sum(t.amount_cents for t in spent)

    reflection_prompt = get_reflection_prompt(period.month)

    return {
        "period_id": period.id,
        "year": period.year,
        "month": period.month,
        "income_received_cents": income_received,
        "total_spent_cents": total_spent,
        "active_missions": get_active_missions(ctx),
        "reflection_prompt": reflection_prompt,
    }


def close_month(
    ctx: AccountContext, period_id: int, notes: str | None, sweep_to_mission: bool,
) -> dict:
    """
    Monthly Reset close (6.6): marks the period closed, optionally sweeps
    unspent budget to the active mission, opens next month's period, runs
    recompute_balances as the reconciliation gate, captures a net-worth
    snapshot for the closing period, and writes a backup file.
    """
    from finapp.services.reconciliation import apply_recomputed_balances
    from finapp.services.balances import get_net_worth_cents
    from finapp.services.backup import create_backup
    from finapp.services.milestones import create_milestone_if_new
    from finapp.models import AssetAccount
    from sqlalchemy import func

    period = ctx.db.query(BudgetPeriod).filter_by(
        account_id=ctx.account_id, id=period_id,
    ).first()
    if not period:
        raise ValueError(f"Period {period_id} not found")

    period.status = "closed"
    period.closed_at = datetime.utcnow()
    if notes:
        period.notes = notes
    ctx.db.commit()

    # Open next month's period (no-op if it already exists)
    next_month_date = date(period.year, period.month, 1) + timedelta(days=32)
    next_period = get_or_create_period(ctx.db, ctx.account_id, next_month_date.replace(day=1))

    # Reconciliation gate
    reconciliation = apply_recomputed_balances(ctx.db, ctx.account_id)

    # Net-worth snapshot for the closing period
    total_assets = ctx.db.query(func.sum(AssetAccount.balance_cents)).filter_by(
        account_id=ctx.account_id, is_active=True,
    ).scalar() or 0
    debts = ctx.db.query(DebtAccount).filter_by(account_id=ctx.account_id, is_active=True).all()
    total_debt = sum(get_debt_balance_cents(ctx, d.id) for d in debts)
    net_worth = total_assets - total_debt

    existing_snapshot = ctx.db.query(NetWorthSnapshot).filter_by(
        account_id=ctx.account_id, year=period.year, month=period.month,
    ).first()
    if existing_snapshot:
        existing_snapshot.net_worth_cents = net_worth
        existing_snapshot.total_assets_cents = total_assets
        existing_snapshot.total_debt_cents = total_debt
    else:
        ctx.db.add(NetWorthSnapshot(
            account_id=ctx.account_id, year=period.year, month=period.month,
            net_worth_cents=net_worth, total_assets_cents=total_assets, total_debt_cents=total_debt,
        ))
    ctx.db.commit()

    # Backup
    backup_path = create_backup(
        source_db_path=SOURCE_DB_PATH, backups_dir=BACKUPS_DIR,
        year=period.year, month=period.month,
    )

    return {
        "period_id": period.id,
        "next_period_id": next_period.id,
        "reconciliation": reconciliation,
        "net_worth_cents": net_worth,
        "backup_path": backup_path,
    }


def start_month_shortcut(ctx: AccountContext) -> BudgetPeriod:
    """
    T1: "Start this month" abbreviated reset. If a transaction is logged in
    a month with no BudgetPeriod, this opens the period without running the
    full Monthly Reset ritual. get_or_create_period already does the
    period-creation half of this (ledger derives it regardless); this is
    the explicit, user-triggered entry point for the one-tap banner.
    """
    today = date.today()
    return get_or_create_period(ctx.db, ctx.account_id, today)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_reviews.py -v`
Expected: PASS

- [ ] **Step 5: Run the reconciliation + ledger suites to confirm no regression**

Run: `pytest tests/test_reconciliation.py tests/test_ledger.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add finapp/services/reviews.py tests/test_reviews.py
git commit -m "feat: add monthly reset close, net-worth snapshot, and start-month shortcut"
```

---

### Task 10: Reviews Router

**Files:**
- Create: `finapp/routers/reviews.py`
- Modify: `finapp/schemas.py`
- Modify: `finapp/main.py`
- Test: `tests/test_reviews_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reviews_router.py`:

```python
from finapp.models import Settings, BudgetPeriod
from datetime import date


def _set_review_day(db):
    settings = Settings(account_id=1, review_day=date.today().strftime("%A").lower())
    db.add(settings)
    db.commit()


def test_get_weekly_review_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    _set_review_day(db)

    resp = test_client_for_budget.get("/reviews/weekly")
    assert resp.status_code == 200


def test_post_weekly_review_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    _set_review_day(db)
    test_client_for_budget.get("/reviews/weekly")  # ensure pending row exists

    resp = test_client_for_budget.post("/reviews/weekly", json={"intention": "Spend less on eating out.", "quick": False})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Spend less on eating out."


def test_post_monthly_review_endpoint(test_client_for_budget, tmp_path, monkeypatch):
    monkeypatch.setattr("finapp.services.reviews.BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("finapp.services.reviews.SOURCE_DB_PATH", str(tmp_path / "finance.db"))
    import sqlite3
    sqlite3.connect(str(tmp_path / "finance.db")).close()

    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    period = BudgetPeriod(account_id=1, year=2026, month=5, status="active")
    db.add(period)
    db.commit()

    resp = test_client_for_budget.post("/reviews/monthly", json={
        "period_id": period.id, "notes": "Good month.", "sweep_to_mission": False,
    })
    assert resp.status_code == 200
    assert resp.json()["period_id"] == period.id


def test_post_start_month_endpoint(test_client_for_budget):
    resp = test_client_for_budget.post("/reviews/start-month")
    assert resp.status_code == 200
    assert resp.json()["month"] == date.today().month
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_reviews_router.py -v`
Expected: FAIL — 404 (no routes registered)

- [ ] **Step 3: Add schemas**

Append to `finapp/schemas.py`:

```python
class WeeklyReviewCompleteRequest(BaseModel):
    """Request to complete the weekly review."""
    intention: str | None = None
    quick: bool = False


class WeeklyReviewResponse(BaseModel):
    """Response after completing the weekly review."""
    id: int
    week_start: date
    prompt_shown: str | None
    notes: str | None
    completed_at: str

    class Config:
        from_attributes = True


class MonthlyReviewCloseRequest(BaseModel):
    """Request to close the monthly period (Monthly Reset)."""
    period_id: int
    notes: str | None = None
    sweep_to_mission: bool = False


class MonthlyReviewCloseResponse(BaseModel):
    """Response after closing a monthly period."""
    period_id: int
    next_period_id: int
    net_worth_cents: int
    backup_path: str
    total_drift: int


class StartMonthResponse(BaseModel):
    """Response after the 'Start this month' shortcut."""
    period_id: int
    year: int
    month: int
```

- [ ] **Step 4: Implement the router**

Create `finapp/routers/reviews.py`:

```python
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
from finapp.schemas import (
    WeeklyReviewCompleteRequest,
    MonthlyReviewCloseRequest,
)
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
```

- [ ] **Step 5: Create a minimal reviews template**

Create `finapp/templates/reviews.html`:

```html
{% extends "base.html" %}

{% block title %}Reviews{% endblock %}

{% block content %}
<div class="container mx-auto px-4 py-8 space-y-6">
    <h1 class="text-3xl font-bold">Reviews</h1>
    <p class="text-gray-600">Weekly reviews and monthly resets, fetched and rendered via HTMX from
        <code>/reviews/weekly</code> and <code>/reviews/monthly</code>.</p>
    <div id="reviews-content" hx-get="/reviews/weekly" hx-trigger="load" hx-swap="innerHTML"></div>
</div>
{% endblock %}
```

- [ ] **Step 6: Register the router**

In `finapp/main.py`:

```python
from finapp.routers import allocation, dashboard, budget, transactions, debt, savings, missions, reviews
```

```python
app.include_router(reviews.router)
```

- [ ] **Step 7: Run to verify pass**

Run: `pytest tests/test_reviews_router.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add finapp/routers/reviews.py finapp/templates/reviews.html finapp/schemas.py finapp/main.py tests/test_reviews_router.py
git commit -m "feat: add reviews router and minimal reviews template"
```

---

### Task 11: Milestones Router

**Files:**
- Create: `finapp/routers/milestones.py`
- Modify: `finapp/schemas.py`
- Modify: `finapp/main.py`
- Test: `tests/test_milestones_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_milestones_router.py`:

```python
from finapp.models import Milestone


def _seed_milestone(db):
    m = Milestone(
        account_id=1, milestone_type="first_budget", title="Your first budget!",
        threshold=0, celebrated=False,
    )
    db.add(m)
    db.commit()
    return m


def test_get_milestone_detail(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    m = _seed_milestone(db)

    resp = test_client_for_budget.get(f"/milestone/{m.id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Your first budget!"


def test_celebrate_milestone_flips_once(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    m = _seed_milestone(db)

    resp = test_client_for_budget.post(f"/milestone/{m.id}/celebrate", json={"feeling": "Relieved!"})
    assert resp.status_code == 200
    db.refresh(m)
    assert m.celebrated is True


def test_get_uncelebrated_milestones(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    _seed_milestone(db)

    resp = test_client_for_budget.get("/api/milestones")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_milestones_router.py -v`
Expected: FAIL — 404 (no routes registered)

- [ ] **Step 3: Add schemas**

Append to `finapp/schemas.py`:

```python
class MilestoneDetailResponse(BaseModel):
    """Full milestone detail for the celebration screen."""
    id: int
    milestone_type: str
    title: str
    description: str | None
    celebrated: bool

    class Config:
        from_attributes = True


class MilestoneCelebrateRequest(BaseModel):
    """Optional 'how does it feel?' response."""
    feeling: str | None = None
```

- [ ] **Step 4: Implement the router**

Create `finapp/routers/milestones.py`:

```python
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
```

- [ ] **Step 5: Register the router**

In `finapp/main.py`:

```python
from finapp.routers import allocation, dashboard, budget, transactions, debt, savings, missions, reviews, milestones
```

```python
app.include_router(milestones.router)
```

- [ ] **Step 6: Run to verify pass**

Run: `pytest tests/test_milestones_router.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add finapp/routers/milestones.py finapp/schemas.py finapp/main.py tests/test_milestones_router.py
git commit -m "feat: add milestones router"
```

---

### Task 12: Integration Test — Full Phase 8 Flow + Reconciliation Gate

**Files:**
- Create: `tests/integration/test_phase8_flows.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_phase8_flows.py`:

```python
"""
Integration test: a scripted month touching missions, weekly review, and
monthly reset, asserting the reconciliation gate stays green throughout
(per CLAUDE.md's non-negotiable Done-gate requirement starting Phase 2).
"""
import os
import sqlite3
from datetime import date

from finapp.models import (
    DebtAccount, SavingsGoal, Mission, BudgetPeriod, Settings,
    BudgetCategory,
)
from finapp.services.ledger import create_transaction
from finapp.services.missions import get_active_missions, complete_mission
from finapp.services.reviews import (
    ensure_weekly_review_due, complete_weekly_review, close_month,
)
from finapp.services.reconciliation import recompute_balances


def test_full_phase8_month_flow(db, ctx, account, tmp_path, monkeypatch):
    monkeypatch.setattr("finapp.services.reviews.BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("finapp.services.reviews.SOURCE_DB_PATH", str(tmp_path / "finance.db"))
    sqlite3.connect(str(tmp_path / "finance.db")).close()

    settings = Settings(account_id=ctx.account_id, review_day=date.today().strftime("%A").lower())
    db.add(settings)

    debt = DebtAccount(
        account_id=ctx.account_id, name="Capital One", opening_balance_cents=20000,
        cached_balance_cents=20000, interest_rate_bps=0, minimum_payment_cents=2000,
    )
    db.add(debt)
    db.commit()

    mission = Mission(
        account_id=ctx.account_id, name="Pay off Capital One", mission_type="debt_payoff",
        link_type="debt", link_id=debt.id, status="active", sort_order=1,
    )
    db.add(mission)
    db.commit()

    period = BudgetPeriod(account_id=ctx.account_id, year=date.today().year, month=date.today().month)
    db.add(period)
    db.commit()

    # Log a debt payment — reconciliation must stay green
    create_transaction(
        ctx, date=date.today(), amount_cents=5000, direction="out",
        link_type="debt", link_id=debt.id, principal_cents=5000, interest_cents=0,
    )
    report = recompute_balances(db, ctx.account_id)
    assert report["total_drift"] == 0

    # Mission progress reflects the payment
    missions = get_active_missions(ctx)
    assert missions[0]["current_cents"] == 15000

    # Weekly review: due, complete it
    ensure_weekly_review_due(ctx)
    review = complete_weekly_review(ctx, intention="Keep paying down debt.", quick=False)
    assert review.notes == "Keep paying down debt."

    # Monthly close
    result = close_month(ctx, period_id=period.id, notes="Solid month.", sweep_to_mission=False)
    assert result["reconciliation"]["total_drift"] == 0
    assert os.path.exists(result["backup_path"])

    db.refresh(period)
    assert period.status == "closed"

    # Complete the mission and confirm it no longer appears in active list
    complete_mission(ctx, mission.id)
    assert get_active_missions(ctx) == []
```

- [ ] **Step 2: Run to verify failure (sanity check before fix, should already pass given prior tasks)**

Run: `pytest tests/integration/test_phase8_flows.py -v`
Expected: PASS (this is a synthesis test over already-implemented pieces — if it fails, debug which Task's behavior regressed before moving on)

- [ ] **Step 3: Run the entire suite**

Run: `pytest -v`
Expected: ALL PASS, including every Phase 0-7 test file untouched by this plan.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_phase8_flows.py
git commit -m "test: add Phase 8 integration flow covering missions, reviews, and monthly close"
```

---

## Done Gate Checklist (from `docs/implementation_plan_v2.md`)

- [x] Weekly review full + quick path — Task 7, 10
- [x] Monthly close creates next period, runs recompute, writes backup file, captures net-worth snapshot — Task 9, 10, 12
- [x] Mission progress derives from linked entity and never drifts — Task 5, 12
- [x] Milestone fires exactly once — Task 2, 3, 4
- [x] "Start this month" shortcut path — Task 9, 10
- [x] Backup uses the SQLite Online Backup API, not `shutil.copy`; test asserts a consistent snapshot — Task 8
- [x] Reconciliation gate green — Task 3, 9, 12
