# Personal Finance App — Phased Implementation Plan

*Dispatch plan for an agent-driven development team*
*Source spec: `product_design_spec_v2.md` (Version 2.0 — Foundation Release)*
*Prepared: 2026-06-14*

---

## How to use this document

This plan turns the v2.0 design spec into an ordered set of **phase-level briefs** that
a development agent works through **sequentially, one phase at a time**. Each phase is a
self-contained dispatch unit: the agent receives the brief, decomposes it internally,
builds it, and must pass the phase's **Done gate** before the next phase is dispatched.

### Execution model

One agent, strictly sequential. Each phase assumes everything in the prior phases is
merged, tested, and green. Do not begin a phase until its predecessor's Done gate is satisfied.

### Verification bar (every phase)

A phase is "done" only when:

1. Unit tests for the phase's logic pass.
2. Integration tests for the phase's user-facing flow pass (where applicable).
3. The **`recompute_balances(ctx)` reconciliation check passes** — every cached balance
   equals its pure-derived value computed from the ledger. This is the single
   non-negotiable correctness gate and runs in CI from Phase 2 onward.

### Money discipline (applies to all phases)

All amounts are `INTEGER` cents; interest is `INTEGER` basis points. Conversions happen
only at the HTTP boundary via `money.py`. No `DECIMAL`, no float, anywhere in the schema
or core logic (Appendix B).

---

## Resolved open decisions (T1–T4)

These were open in the spec (§12) and are now **locked**. Agents treat them as binding.

| # | Decision | Resolution | Build implication |
|---|---|---|---|
| **T1** | New-month `BudgetPeriod` creation | **Ritual + shortcut.** The Monthly Reset ritual opens the period. If the user logs a transaction in a month with no period, offer a one-tap "Start this month" abbreviated reset. The ledger derives `period_id` from the transaction date regardless. | Phase 2 (ledger derives period); Phase 8 (reset ritual + shortcut) |
| **T2** | Responsive breakpoint | **768px split.** Desktop ≥768px → persistent sidebar. Mobile <768px → bottom nav. | Phase 4 (base layout/nav) |
| **T3** | Balance storage | **Cache + reconcile.** Cache `cached_balance_cents` and `income_received_cents`; recompute on write and on monthly close. `recompute_balances` is the safety net and the CI gate. Fall back to pure-derive only if drift is ever observed. | Phase 2 (cache + recompute routine) |
| **T4** | "Correct balance" adjustment UX | **"Update balance."** Neutral copy: "Update balance to match your statement." Creates an adjustment transaction with a memo; no failure connotation. | Phase 6 (debt adjust action) |

---

---

## Phase map at a glance

The build order follows the spec's recommended dependency chain
(ledger → allocation → dashboard → debt/savings → reviews), bracketed by a foundation
phase up front and onboarding/import/polish at the end.

| Phase | Name | Depends on | Core deliverable |
|---|---|---|---|
| 0 | Foundation & Scaffolding | — | Repo, layering, Alembic baseline, `money.py`, CI |
| 1 | Data Model & Migrations | 0 | All ORM models + first migration + reconciliation test harness |
| 2 | Ledger Service (source of truth) | 1 | Single write path, derived balances, cache + `recompute_balances` |
| 3 | Allocation Engine | 2 | Cumulative-envelope allocation, income flow, triage edge case |
| 4 | App Shell, Dashboard & Daily Check-In | 2 | Base layout, nav, streak, Next Right Action, Add Transaction |
| 5 | Budget & Transactions Screens | 3, 4 | Budget table, transactions list, expense + overage flows |
| 6 | Debt | 2, 4 | Debt accounts, payments, projection engine, "Update balance" |
| 7 | Savings & Net Worth | 2, 4 | Goals, emergency-fund coverage, asset accounts, net worth |
| 8 | Reviews, Monthly Reset & Missions | 5, 6, 7 | Weekly/monthly flows, period close, mission queue, milestones |
| 9 | Onboarding | 1–8 | Six-step setup gating `setup_complete` |
| 10 | CSV Import & Settings | 2, 9 | Import/dedup, settings, backup, export |
| 11 | Polish, Copy & Hardening | all | Language/color rules, undo, a11y, full-suite verification |


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

## Phase 8 — Reviews, Monthly Reset & Missions

**Goal.** The rhythm layer: weekly review, monthly reset (incl. period close), the mission
queue, and milestone celebrations.

**Build.**
- `services/missions.py`: mission queue with progress **derived from the linked entity**
  (start = entity opening balance, current = derived balance, target = debt 0 or goal
  target). Reorder (non-emergency-fund) and complete. Wire the dashboard mission card.
- **Weekly Review (6.5):** five steps (with a 2–3 min quick mode): at-a-glance (amber/teal,
  never red), reallocate if needed, mission check, one optional intention, close. Streak
  acknowledgement. Store `prompt_shown` + `notes` on `Review`.
- **Monthly Reset (6.6):** last-month summary; milestone celebration; optional reflection
  (`BudgetPeriod.notes` + `Review`); subscription review (Keep/Review/Cancel with annual
  cost); new-month allocation (new `BudgetPeriod`, fresh targets, prior month greyed,
  sweep-unspent-to-mission prompt). **On close: run `recompute_balances(ctx)`, capture the
  net-worth snapshot, and trigger a backup** (SQLite backup API → `backups/finance.YYYY-MM.db`).
- **T1 wiring:** Monthly Reset opens the period; if a transaction is logged in a month with
  no period, surface the one-tap **"Start this month"** abbreviated reset.
- **Milestone Celebration (6.7):** full-screen moment (not a toast) on debt $0, goal hit,
  streak threshold, first budget, first review; optional "how does it feel?"; `celebrated`
  flips once (unique constraint).
- **Monthly Reflection (§9.6):** prompt text from config, rotated by month number, stored
  in `Review.prompt_shown`.
- Routes: `GET /reviews`, `GET/POST /reviews/weekly`, `GET/POST /reviews/monthly`
  (close + recompute + backup), `GET /api/missions/active`, `POST /missions/{id}/complete`,
  `POST /missions/reorder`, `GET /milestone/{id}`, `POST /milestone/{id}/celebrate`,
  `GET /api/milestones`.

**Done gate.**
- Tests: weekly review full + quick path; monthly close creates next period, runs recompute,
  writes backup file, captures net-worth snapshot; mission progress derives from linked
  entity and never drifts; milestone fires exactly once; "Start this month" shortcut path.
- Backup uses the **SQLite Online Backup API** (`Connection.backup()`), not `shutil.copy`;
  test asserts a consistent snapshot.
- Reconciliation gate green.

---

## Phase 9 — Onboarding

**Goal.** The six-step setup that gates the app until `setup_complete = TRUE`.

**Build.**
- Six steps with a visible progress indicator (§5): Welcome (name only) → Income Setup
  (take-home, frequency, pay day, optional hourly wage [feature disabled]) → Budget
  Categories (8 defaults, rename/add 1–3/hide; can't delete Income/Housing/Debt/Emergency
  Fund) → Debt Setup (Yes/No/Not yet; accounts; method choice with a concrete example using
  their real numbers, no default) → Emergency Fund (current balance + target options) →
  Mission Queue (auto-generated, reorderable except emergency fund).
- Capture money as cents, interest as basis points at the boundary.
- `POST /onboarding/missions` flips `setup_complete`; `GET /` redirects to `/onboarding`
  until then. Setup-complete lands on the dashboard with "Let's give every dollar a job."
- Routes: `GET /onboarding`, `POST /onboarding/{settings,categories,debt,savings,missions}`.

**Done gate.**
- Integration test walks all six steps and lands on a usable dashboard with seeded
  categories, debts, emergency fund, and a mission queue.
- System categories non-deletable; debt method has no preselected default.
- Reconciliation gate green.

---

## Phase 10 — CSV Import & Settings

**Goal.** Secondary transaction entry and the settings surface (incl. backup/export).

**Build.**
- **CSV Import (6.9 / §9.7):** upload → column mapping (date, amount, direction, payee),
  remembered in `Settings.csv_column_map`; direction toggle ("which way means you spent
  money?"); preview; **dedup** by `import_hash` = SHA-256 of `date + amount_cents +
  payee[:20]` (lowercased, stripped), matches excluded by default; user can uncheck rows.
  Imported rows: `is_imported=TRUE`, no mood tag, **routed through the ledger service**.
  Uncategorized rows → "Needs Category" inbox with a nav badge count. No auto-categorization.
- **Settings (Screen 7):** profile; categories (rename/hide/reorder/add); debt method;
  review day; hourly wage (disabled); CSV import; **Create Backup** (SQLite backup API);
  data export (CSV); about/version.
- Routes: `GET/POST /settings`, `GET/POST /settings/categories`, `POST /settings/import/csv`,
  `GET /settings/import/preview`, `POST /settings/import/confirm`, `GET /settings/export`,
  `POST /settings/backup`.

**Done gate.**
- Tests: mapping remembered; dedup excludes known hashes; imported rows land in correct
  periods via the ledger; uncategorized rows surface in the inbox with an accurate badge;
  on-demand backup produces a consistent snapshot; CSV export round-trips.
- Reconciliation gate green after an import of mixed/duplicate rows.

---

## Phase 11 — Polish, Copy & Hardening

**Goal.** Enforce the design language globally and run the final full-suite verification.

**Build.**
- **Language Rules (§10):** audit every user-facing string against the Don't/Say table
  (over-budget, missed check-in, debt, progress, review, goal-not-met). No guilt copy.
- **Color Rules:** never red for over-budget (amber #F59E0B); teal/green for progress; grey
  for closed data; progress bars fill, framed "X% used" with a remaining label; no alarm
  iconography in routine states.
- **UX Rules:** Add Transaction one tap from anywhere; no confirm dialogs for edits; 10s
  undo on delete; mobile-friendly; no spinners at v1 volumes; **no "Nothing here yet" empty
  states** — first-action prompts instead.
- **Re-engagement Recovery (6.8):** after 3+ days away, no guilt/recap; soft "Welcome back"
  banner + optional quick catch-up (rough total → Everything Else); streak resets to 1.
- Confirm loopback-only bind, WAL mode, and that `/api/*` JSON routes resolve
  `AccountContext` via the single dependency (the multi-user seam is intact and untested-by-
  exposure but present).

**Done gate (release gate).**
- Full unit + integration suite green; **`recompute_balances` reconciliation passes on a
  full simulated multi-month lifecycle** (onboarding → several months of income, expenses,
  debt payments, savings, reviews, resets, an import).
- Copy/color audit checklist signed off against §10.
- Server binds to `127.0.0.1` only; backup files verified consistent.
- A `Plan` or `general-purpose` verification agent re-checks the running app against the
  spec's Foundation scope (see Out-of-Scope note below) and reports no gaps.

---

## Explicitly out of scope (do not build)

Per spec §11, these are **not** in this build and agents must not pull them forward:

- Subcategory granularity
- Sinking funds
- Retirement/long-term projection
- "What Would Change?" modeler
- Automatic bank sync
- Pause Before Purchase nudge
- Time-cost display (setting exists, feature disabled)
- PDF/report export
- Multiple users / household view (seams only, not exposed)
- Native mobile app (JSON API prepared, not built)
- Authentication / hosted deployment

### Multi-user seams

The multi-user seams (`account_id` everywhere, `AccountContext` in every service,
Alembic from day one, integer-cents/portable SQL) **are** built — but never exposed. The
hosted/multi-user migration (Appendix A) is future work.

---

## Cross-cutting invariants (audit every phase against these)

1. **One write path** — Every transaction is created/voided/edited through `services/ledger.py`.
2. **Transactions are truth; balances derive** — No authoritative balance lives outside the
   ledger; caches are always recomputable and reconciled.
3. **Integer cents / basis points only** — Floats and `DECIMAL` never enter schema or core.
4. **Account-scoped everything** — Every query filters by `ctx.account_id`; every service
   takes `ctx` first.
5. **Loopback only** — `127.0.0.1:5000`, never `0.0.0.0`.
6. **Calm by design** — Never red; no guilt copy; reversible actions; first-action prompts.

---

*End of phased implementation plan.*