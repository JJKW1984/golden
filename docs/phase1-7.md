## Phase 0 — Foundation & Scaffolding

**Goal.** Stand up the project skeleton, the three-layer architecture seam, and the
toolchain so every later phase drops into a known structure.

**Build.**
- Create the `finapp/` package per the spec's File Structure (§2): `main.py`, `deps.py`,
  `db.py`, `models.py`, `schemas.py`, `routers/`, `services/`, `templates/`, `static/`,
  `money.py`, `run.py`, `requirements.txt`, `alembic/`.
- Pin the stack: Python 3.11+, FastAPI, Jinja2, SQLAlchemy 2.0 (typed), Alembic,
  Pydantic v2, Uvicorn. HTMX + optional Alpine.js + Tailwind (Play CDN) + Chart.js via CDN.
- Implement the **layering seam**: `routers/` (HTTP only) → `services/` (logic, every fn
  takes `ctx: AccountContext` first) → `repository/` (all SQL, scoped by `ctx.account_id`).
- Implement `deps.py::get_account_context()` returning the hardcoded singleton
  (`account_id = 1`) — the one place multi-user later flips.
- Implement `db.py`: engine, session, SQLite WAL pragma, SQLite↔Postgres URL switch.
- Implement `money.py`: `to_cents(str|Decimal) -> int` (parse via `Decimal`, ×100, round
  half-up) and `to_display(int) -> str`. **This is the most-tested unit in the codebase.**
- Implement `run.py`: `alembic upgrade head` → start Uvicorn bound to **`127.0.0.1:5000`**
  (never `0.0.0.0`) → open browser → redirect to onboarding if `setup_complete` is false.
  Add `start.bat` / `start.sh`.
- Initialize Alembic with an **empty baseline migration** (history starts at commit one).
- Stand up CI: lint + test runner. Wire a placeholder reconciliation test slot.

**Done gate.**
- `python run.py` boots, binds to loopback only, serves a health route.
- `money.py` round-trip tests pass (parsing, rounding half-up, display, edge values like
  `$0.00`, large amounts, negative rejection).
- `alembic upgrade head` runs clean from empty DB.
- CI green.

**Out of scope here.** No models, no business logic, no screens.

---

## Phase 1 — Data Model & Migrations

**Goal.** Encode the full v2 schema as SQLAlchemy models with a single Alembic migration,
plus the test harness that future phases reconcile against.

**Build.**
- Implement every model in §4, all monetary fields `INTEGER` cents, all carrying
  `account_id`, with `created_at`/`updated_at`:
  `Account`, `Settings` (UNIQUE account_id), `BudgetCategory` (with `kind` enum),
  `BudgetPeriod` (UNIQUE account_id+year+month), `BudgetAllocation`
  (UNIQUE period+category), `Transaction` (magnitude + `direction`, `link_type`/`link_id`,
  `principal_cents`/`interest_cents`, `is_deleted`, `import_hash`),
  `DebtAccount` (opening + cached balance, `interest_rate_bps`),
  `SavingsGoal`, `Mission` (no stored amounts — progress derived later),
  `CheckIn` (UNIQUE account_id+date), `Review`, `Milestone`
  (UNIQUE account_id+milestone_type+threshold+link_id), `AssetAccount`.
- Generate the single Alembic migration that creates all tables; verify autogenerate
  matches the hand-written models (no diff).
- Seed the 8 default categories (§4.3) as a service/data routine, not a migration:
  Income, Housing, Food, Transportation, Debt, Emergency Fund, Personal, Everything Else,
  with correct `kind` and `is_system` flags.
- Build the **reconciliation test harness**: fixtures that create accounts, periods, and
  transactions, plus helpers that assert "cached == pure-derived." Later phases plug their
  derived values into this harness.

**Done gate.**
- `alembic upgrade head` then `downgrade base` round-trips clean.
- Autogenerate shows no diff against models.
- Default-category seed produces exactly the 8 rows with correct kinds; system categories
  flagged non-deletable.
- Harness can build a populated DB and run the (currently trivial) reconciliation assertion.

---

## Phase 2 — Ledger Service (source of truth)

**Goal.** Implement the authoritative transaction ledger (D11) and all derived balances.
**This is the spine; everything downstream reads through it.**

**Build.**
- `services/ledger.py` as the **single write path** for create/void/edit of transactions.
  No other code writes a `Transaction`. Responsibilities:
  - **Period assignment from date** (resolves v1 ambiguity): a transaction belongs to the
    `BudgetPeriod` matching its date's year+month; re-derived if the date is edited.
  - The **debt-payment split** (`principal_cents` + `interest_cents`) and link handling
    (`link_type`/`link_id`).
  - The **dedup hash** for imports (computed here so CSV import in Phase 10 is consistent).
  - **Soft delete** (`is_deleted = TRUE`) and restore; deleted rows excluded from all sums.
- `services/balances.py` — derived values, all computed from the ledger:
  - Budget "spent" per category per period.
  - Debt balance = `opening_balance_cents` − Σ principal applied (interest never reduces
    principal).
  - Savings goal balance = opening + Σ contributions − withdrawals.
  - Net worth = Σ asset balances − Σ derived debt balances.
  - Days-of-expenses coverage per §4.14 (trailing-90-day avg, with the <30-day fallback).
- **Caching (T3):** maintain `cached_balance_cents` and `income_received_cents`, refreshed
  on every write. Implement `recompute_balances(ctx)` that rebuilds every cache from the
  ledger; this is both the monthly-close consistency check and the CI reconciliation gate.
- Map all specialized actions to transactions per the §3 table (income, expense, debt
  payment, savings contribution, savings withdrawal). No `DebtPayment`/`SavingsContribution`
  tables exist — they are queries.

**Done gate.**
- Unit tests for each derived value, including: interest-only debt payment (principal = 0),
  back-dated transaction landing in the correct period, edited-date re-derivation, soft
  delete excluded from sums, withdrawal reducing goal balance.
- `recompute_balances` produces zero drift across a randomized transaction set
  (property-style test) — **the reconciliation gate is now live in CI.**
- Confirm no code path outside `ledger.py` writes a transaction (architectural test/grep).

---

## Phase 3 — Allocation Engine

**Goal.** Implement the cumulative monthly envelope (D12) and the income/allocation flow,
including the income-under-obligations triage.

**Build.**
- `services/allocation.py`:
  - First paycheck of a month with no targets → allocation ritual sets `target_cents` per
    category (`BudgetAllocation`). Pre-fill in **Pay Yourself First** priority: Debt target
    = Σ active `minimum_payment_cents`; Emergency Fund = configured monthly amount or
    remaining-to-target, whichever is smaller.
  - `funded_cents` per category = the portion of received income its target has claimed so
    far, allocated in priority order (derived, never stored). `remaining_cents` =
    `funded_cents` − `spent_cents`.
  - Later paychecks in the same month **do not** reopen the ritual; received income rises
    and funded climbs.
- Income entry routes through the **ledger** (income transaction), then updates the cached
  `income_received_cents`.
- **Zero-based hard block** when income ≥ essentials: the "Unallocated" total must reach $0
  to confirm (server-side computation; the HTMX wiring lands in Phase 5).
- **Income-under-obligations triage (6.2 edge case):** when received income < essentials,
  do **not** hard-block on a negative remainder. Fund in priority order, show what's
  unfunded, and offer: lower a target, defer emergency-fund this month, or "more income
  expected" (don't fully allocate yet).

**Done gate.**
- Tests: cumulative funding across biweekly/weekly paychecks; priority ordering of funded
  dollars; "Everything Else" absorbing leftovers; zero-based block triggers correctly;
  triage path engages when income < essentials and never produces a hard negative block.
- Reconciliation gate still green (allocation reads derived values; nothing should drift).

---

## Phase 4 — App Shell, Dashboard & Daily Check-In

**Goal.** The visible skeleton: base template, navigation, and the dashboard that makes the
daily check-in feel like a 60-second glance.

**Build.**
- `templates/base.html` with HTMX (and optional Alpine) wired; Tailwind CDN. **Responsive
  per T2:** persistent sidebar at ≥768px, bottom nav at <768px. Nav items: Dashboard,
  Budget, Transactions, Debt, Savings, Reviews, Settings.
- Always-visible **Add Transaction** affordance reachable in one tap from anywhere.
- `services/streak.py`: page load creates today's `CheckIn` if absent; streak = consecutive
  CheckIn days back from today **with one grace day** (one single-day gap survives; a second
  gap or a 2+-day gap resets to 1). Thresholds 7/30/90/180/365 create a `Milestone` once
  each (unique constraint).
- Dashboard (Screen 1): greeting + date + streak; current-mission card (progress derived
  from the linked entity — read-only stub until Phase 8 wires missions); this-week pulse
  (week's `kind='spending'` expenses vs prorated funded targets); **Next Right Action**.
- `services/` Next Right Action priority (§9.4), first match: setup incomplete → income not
  logged → unallocated income → review due today → monthly reset available (1st–3rd, prior
  month open) → active-mission debt payment not logged this month → last transaction >5
  days ago → "You're on track."
- `POST /api/checkin` and `GET /api/dashboard` JSON routes.

**Done gate.**
- Streak tests: clean run, single grace gap survives, double gap resets, milestone fires
  once per threshold.
- Next Right Action returns the correct branch for each priority scenario.
- Dashboard renders at both breakpoints; Add Transaction reachable from every screen.

---

## Phase 5 — Budget & Transactions Screens

**Goal.** The two highest-traffic screens, wiring the allocation engine and ledger to live
HTMX UI.

**Build.**
- **Budget screen (Screen 2):** Current Month | History tabs. Table of
  target / funded / spent / remaining per category, totals row. Funded reflects the
  cumulative envelope (funded < target shown calmly when only part of income has arrived).
  Amber ⚠ on over (never red); tapping opens the overage flow. Unallocated banner when
  received income exceeds funded targets. "Set / Adjust Plan" entry to the allocation ritual.
- Allocation ritual UI with the **server-side "Unallocated" counter via HTMX** reaching $0
  (the Phase 3 logic, now wired). "Not sure? Put it in Everything Else."
- **Transactions screen (Screen 3):** current month, date-desc; filters (date range,
  category, direction, mood tag); add via slide-in HTMX panel preserving context.
  Debt-payment and savings-contribution rows show their link inline.
- **Expense logging flow (6.3):** date (default today), amount, category (required), payee,
  mood tag (optional 5-icon row §9.5), memo. On save, category remaining updates via HTMX;
  if over target, the **Overage Reallocation flow (6.4)** modal appears.
- **Overage flow (6.4):** friendly modal (not an alert) listing categories with headroom
  (most available first); move funds (edits both `BudgetAllocation.target_cents` rows) or
  "I'll adjust next month." Transaction saves regardless.
- Routes: `GET /budget`, `GET /budget/{year}/{month}`, `POST /budget/income`,
  `GET/POST /budget/allocate`, `POST /budget/reallocate`, `GET /api/budget/pulse`,
  `GET /api/budget/summary/{period_id}`; transactions CRUD incl.
  `POST /transactions/{id}/delete` + `/restore` (10s undo).

**Done gate.**
- Integration tests: log income → ritual → zero-based confirm; log expense → remaining
  updates; overage triggers modal and reallocation edits both targets; mood-tag persists
  and filters.
- Reconciliation gate green after a scripted month of mixed transactions.

---

## Phase 6 — Debt

**Goal.** Debt accounts, payments through the ledger, and the projection engine.

**Build.**
- **Debt screen (Screen 4):** Active Target card (balance, % paid off, minimum, extra this
  month, projected payoff, [Log Payment] / [See Projection]); Debt Queue below in payoff
  order; projection view as a Chart.js line chart (current pace + $50 / +$100 scenarios
  with interest saved).
- Debt payment routes through the **ledger** (`link_type='debt'`, principal/interest split).
- `services/debt_payoff.py`: integer-cents monthly amortization (§9.3). Scenarios: minimum
  only, current allocation, +$50, +$100 — each returns months, total interest (cents),
  payoff date. **Explicitly flag the "payment ≤ interest" case.**
- **"Update balance" adjustment (T4):** copy "Update balance to match your statement";
  creates an adjustment transaction with a memo (`mood_tag=NULL`), preserving the audit
  trail. The UI never lets the user type a debt balance directly otherwise.
- Debt method (snowball/avalanche) sets `sort_order` of the queue.
- Routes: `GET/POST /debt`, `GET/POST /debt/{id}`, `POST /debt/{id}/payment`,
  `POST /debt/{id}/adjust`, `GET /api/debt/projection/{id}`.

**Done gate.**
- Amortization tests against hand-computed fixtures incl. the payment-≤-interest flag and
  exact integer-cents interest rounding each month.
- Payment reduces derived balance by principal only; adjustment creates an audit transaction.
- Snowball vs avalanche ordering correct.
- Reconciliation gate green.

---

## Phase 7 — Savings & Net Worth

**Goal.** Savings goals, emergency-fund coverage math, and the Net Worth foundation feature.

**Build.**
- **Savings screen (Screen 5):** Emergency Fund card (balance vs target, %,
  days-of-expenses coverage per §4.14, est. complete date); Other Goals below.
- Contributions and withdrawals route through the **ledger** (`link_type='savings'`;
  withdrawal = negative-direction contribution).
- **Net Worth (§9.8):** `AssetAccount` CRUD (manually-updated balance snapshots);
  `GET /api/networth` = Σ asset balances − Σ derived debt balances, plus a trend from
  monthly snapshots captured at monthly close (snapshot capture wired in Phase 8).
- Routes: `GET /savings`, `POST /savings/contribution`, `POST /savings/withdrawal`,
  `GET /api/savings/emergency`, `GET/POST /assets`, `GET /api/networth`.

**Done gate.**
- Tests: contribution/withdrawal update derived goal balance; goal completion flips derived
  `is_complete`; days-of-coverage uses trailing-90 avg and the <30-day "estimated" fallback;
  net worth subtracts derived debt correctly.
- Reconciliation gate green.