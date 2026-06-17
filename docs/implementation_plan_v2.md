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
