# Personal Finance App — Product Design Spec

*Version 2.0 — Foundation Release (Architecture-Hardened)*
*Derived from: Structured Design Brief + v1.0 spec + architectural review*

> **What changed from v1.0.** This revision keeps the product and behavioral design
> intact and reworks the data/architecture layer to remove correctness risks and to
> leave a clean path to a future multi-user, multi-client product. The five decisions
> that drove the rewrite are recorded in the Decisions Log (D10–D14) and summarized
> here:
> 1. **Money is stored as integer cents**, never `DECIMAL`/float.
> 2. **Transactions are the single source of truth**; debt, savings, and budget
>    balances are *derived*, never stored authoritatively.
> 3. **Allocation is a cumulative monthly envelope** — paychecks add up against
>    per-month category targets set once.
> 4. **Stack is FastAPI + Jinja + HTMX** — server-rendered HTML today, a typed JSON
>    API ready for native/mobile clients later.
> 5. **Build single-user/local now, with multi-user seams** — an account context in
>    the service layer, Postgres-portable SQL, and Alembic migrations from day one.

---

## Table of Contents

1. [Decisions Log](#1-decisions-log)
2. [System Architecture](#2-system-architecture)
3. [Ledger Model & Reconciliation](#3-ledger-model--reconciliation)
4. [Data Models](#4-data-models)
5. [Onboarding Flow](#5-onboarding-flow)
6. [Core User Flows](#6-core-user-flows)
7. [Screen Inventory & Specifications](#7-screen-inventory--specifications)
8. [API Surface](#8-api-surface)
9. [Feature Specifications](#9-feature-specifications)
10. [Design Principles & Language Rules](#10-design-principles--language-rules)
11. [Out of Scope for v1](#11-out-of-scope-for-v1)
12. [Open Technical Decisions](#12-open-technical-decisions)
13. [Appendix A: Multi-User Migration Path](#13-appendix-a-multi-user-migration-path)
14. [Appendix B: Money Handling Reference](#14-appendix-b-money-handling-reference)

---

## 1. Decisions Log

These decisions lock the scope of this spec. Any change requires re-evaluating the
affected sections. D1–D9 are carried from v1.0; D10–D14 are new in v2.0.

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Platform | Local web app (localhost) for v1 | No cloud dependency, data stays on device |
| D2 | Backend language | Python 3.11+ | Preferred stack |
| D3 | Data storage (v1) | SQLite single file | Local, zero-maintenance, portable |
| D4 | Transaction entry | Manual primary + CSV import secondary | Intentional friction drives awareness |
| D5 | Income model | Regular/predictable paycheck, budget what has *arrived* | Simplifies allocation; avoids spending future money |
| D6 | Debt payoff method | User chooses during onboarding | Presented with clear tradeoff explanation |
| D7 | Budget granularity | Broad categories to start (5–7) | Lower barrier to entry; granularity earned later |
| D8 | Emotional layer | Mood tags + monthly reflection prompts | Light, optional, forward-oriented |
| D9 | MVP scope | Full Foundation tier | All foundation features in v1 |
| **D10** | **Money representation** | **Integer cents (`INTEGER`)** | SQLite has no true decimal; integers make zero-based sums exact and are portable to Postgres. See Appendix B. |
| **D11** | **Source of truth** | **Transactions are the ledger; balances are derived** | One ledger that never drifts. Debt/savings "balances" and budget "spent" are computed from a starting balance plus transactions. |
| **D12** | **Allocation cadence** | **Cumulative monthly envelope** | Category *targets* are set once per month; each paycheck adds to received income. No re-ritual per paycheck. Supports biweekly/weekly pay. |
| **D13** | **Framework** | **FastAPI + Jinja2 + HTMX** | Server-rendered HTML now; FastAPI gives a typed JSON API for a future native/mobile client (D14). HTMX keeps money logic in Python with no build step. |
| **D14** | **Multi-user posture** | **Single-user/local now, multi-user seams built in** | Account-context in the service layer, Postgres-portable SQL, Alembic from day one. Migration later is additive, not a rewrite. See Appendix A. |

**Note on D1 vs D14.** v1 ships local and single-user, consistent with the brief's
privacy preference. D14 does **not** turn v1 into a hosted product; it only ensures the
code does not paint itself into a single-user corner. Going hosted remains a future
product decision with its own trust-model implications.

---

## 2. System Architecture

### Overview

A single-user local web application for v1. The server binds explicitly to
`127.0.0.1:5000` (loopback only — **not** `0.0.0.0`, which would expose an
unauthenticated app to the local network). The frontend is server-rendered HTML
(Jinja2 templates) with HTMX for partial updates and a small amount of optional
Alpine.js for pure-UI toggles. All data lives in a single SQLite file in v1.

```
[Browser: 127.0.0.1:5000]
        |  (full-page loads + HTMX partial swaps)
        v
[FastAPI app: routers -> services -> repository]
        |  (SQLAlchemy ORM, account-scoped)
        v
[SQLite: finance.db]   (v1)   ->   [Postgres]   (future, see Appendix A)
```

### Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | — |
| Web framework | FastAPI + Jinja2 | Typed JSON endpoints for future client |
| Interactivity | HTMX (CDN) | Server returns HTML fragments; money math stays in Python |
| UI toggles (optional) | Alpine.js (CDN) | Pure client-side state only (modals, mood-tag row) |
| ORM | SQLAlchemy 2.0 | Typed queries; portable SQLite↔Postgres |
| Migrations | Alembic | From day one (D14) for multi-user/Postgres migration history |
| Database (v1) | SQLite | Single `finance.db`; WAL mode |
| Validation | Pydantic v2 | Form/JSON input validation in one place |
| CSS | Tailwind (Play CDN) | Suitable for local single-user; upgrade if hosted |
| Charts | Chart.js (CDN) | Debt/projection charts |
| CSV parsing | Python `csv` stdlib | No external dependency |

**Why FastAPI + HTMX (not Alpine for logic):** The app is form-driven and
server-rendered. HTMX lets the "Unallocated: $0.00" counter and budget-remaining
figures recompute *server-side* and swap in as HTML, so the zero-based math has exactly
one implementation (Python). FastAPI is chosen for the future JSON API surface
(D13/D14), not for `/docs`; Pydantic validation of incoming form data is a real v1
benefit.

### Layering (the multi-user seam)

Code is split into three layers so that the future jump to multi-user is additive:

```
routers/
  HTTP only. Parse request → Pydantic model → call service. No SQL.

services/
  All business logic + money math. Every function takes explicit
  `ctx: AccountContext` first argument.

repository/
  All data access (SQLAlchemy). Every query scoped by ctx.account_id.
```

`AccountContext` is the seam. In v1 it is a hardcoded singleton (`account_id = 1`)
produced by a single dependency. To go multi-user, that one dependency is changed to
read the account from the authenticated session — no service or repository signature
changes. (See Appendix A.)

### File Structure

```
finapp/
├── main.py                  # FastAPI app, router registration, 127.0.0.1 bind
├── deps.py                  # get_account_context() dependency (the seam)
├── db.py                    # Engine, session, WAL pragma, SQLite/Postgres URL switch
├── models.py                # SQLAlchemy ORM models
├── schemas.py               # Pydantic request/response models
├── alembic/                 # Migrations (versioned from day one)
├── routers/
│   ├── dashboard.py         # Dashboard view
│   ├── transactions.py      # Transaction management
│   ├── budget.py            # Budget management
│   ├── debt.py              # Debt management
│   ├── savings.py           # Savings management
│   ├── missions.py          # Mission queue
│   ├── reviews.py           # Weekly/monthly reviews
│   ├── settings.py          # User settings
│   └── import_csv.py        # CSV import handler
├── services/
│   ├── ledger.py            # Single entry point for creating/voiding transactions
│   ├── allocation.py        # Cumulative-envelope allocation logic
│   ├── balances.py          # Derived debt/savings/net-worth balances
│   ├── debt_payoff.py       # Snowball/avalanche projection math
│   ├── projections.py       # Timeline / pace projections
│   ├── csv_import.py        # CSV parsing, mapping, dedup
│   ├── streak.py            # Streak + grace-day logic
│   └── missions.py          # Mission queue + derived progress
├── templates/               # base.html + per-feature dirs + _partials/
├── static/
│   ├── css/                 # Tailwind Play CDN styles
│   └── js/                  # HTMX, Alpine.js (optional)
├── money.py                 # cents<->display helpers (Appendix B)
├── finance.db               # SQLite (auto-created, WAL)
├── backups/                 # Auto-backups (SQLite backup API)
├── requirements.txt
└── run.py                   # Startup: migrate, start server, open browser
```

### Startup

`python run.py` should: (1) run `alembic upgrade head` to bring the schema current,
(2) start Uvicorn bound to `127.0.0.1:5000`, (3) open the browser, (4) redirect to
onboarding if `Settings.setup_complete` is false. A `start.bat` / `start.sh` wraps this.

### Backup

On monthly close (and on demand from Settings), back up using the **SQLite Online
Backup API** (`sqlite3.Connection.backup()`), **not** `shutil.copy`. Copying a live
WAL database with a filesystem copy can capture a torn state; the backup API produces a
consistent snapshot while the app is running. Output: `backups/finance.YYYY-MM.db`.

---

## 3. Ledger Model & Reconciliation

This section did not exist in v1.0 and resolves the largest architectural gap: three
parallel representations of money (transactions, debt balances, savings balances) with
no defined source of truth.

### Principle (D11): the transaction ledger is authoritative

Every movement of money is a `Transaction`. Nothing else stores an authoritative
balance. Specifically:

- **Budget "spent" per category** = sum of expense transactions for that category in
  the period.
- **Debt account balance** = `DebtAccount.opening_balance` − sum of principal applied
  by linked debt-payment transactions (interest is recorded but does not reduce
  principal). See below.
- **Savings goal balance** = sum of contribution transactions (and any withdrawals)
  linked to that goal.
- **Net worth** = sum of asset-account balances − sum of debt balances (see §4.14).

Derived values may be **cached** for display performance (e.g., a `cached_balance`
column refreshed on write), but the cache is always recomputable from transactions and
is never the source of truth. A `recompute_balances(ctx)` routine rebuilds every cache
from the ledger and runs as a consistency check on monthly close.

### How specialized actions map to transactions

| User action | Transactions created | Effect on derived state |
|---|---|---|
| Log income (paycheck) | 1 income transaction (category = Income, `links_to` = none) | Increases `BudgetPeriod.income_received` (derived sum) |
| Log expense | 1 expense transaction (category required) | Reduces that category's remaining |
| Log debt payment | 1 expense transaction, `link_type='debt'`, `link_id=<debt>`, split into `principal_cents` + `interest_cents` | Reduces derived debt balance by `principal_cents`; counts against the Debt category |
| Log savings contribution | 1 transfer/expense transaction, `link_type='savings'`, `link_id=<goal>` | Increases derived goal balance; counts against the Savings/Emergency category |
| Savings withdrawal | 1 negative-direction contribution transaction | Decreases derived goal balance |

There is therefore **no separate `DebtPayment` or `SavingsContribution` table** in v2.
Those v1 tables are collapsed into the transaction ledger via the `link_type`/`link_id`
columns. "Debt payment history" and "contribution history" are *queries*, not tables.

### Reconciliation rules

1. **Single write path.** All transaction creation/voiding goes through
   `services/ledger.py`. No router or service writes a transaction directly. This is
   where the link split (principal/interest), period assignment, and dedup hash live.
2. **Period is derived from date** (resolves a v1 ambiguity). A transaction belongs to
   the `BudgetPeriod` matching its `date`'s year+month, not to "the active period."
   Back-dated and imported transactions land in the correct month automatically. The
   stored `period_id` is set by the ledger service from `date` and re-derived if the
   date is edited.
3. **Edits and deletes are reversible.** Transactions are soft-deleted
   (`is_deleted = TRUE`); a 10-second undo is available (per the UX rules). Deleted
   transactions are excluded from all derived sums.
4. **Balances never set directly.** The UI never lets the user type a new debt balance;
   they log a payment. (Exception: an explicit "correct balance" action creates an
   adjustment transaction with `mood_tag=NULL` and a memo, preserving the audit trail.)

---

## 4. Data Models

All monetary fields are **`INTEGER` cents** (D10). All tables carry an `account_id`
(D14) — in v1 every row has `account_id = 1`; the column exists so multi-user scoping
is a query change, not a migration. `created_at`/`updated_at` are `DATETIME`.

### 4.1 Account (new — tenancy seam)

```
Account
─────────────────────────────────────
id                  INTEGER PK
display_name        TEXT                      # v1: the single local user
created_at          DATETIME
```

In v1 exactly one row exists (`id = 1`). This table is the anchor for `account_id`
foreign keys and the future `User` relationship (Appendix A).

### 4.2 Settings (now per-account)

```
Settings
─────────────────────────────────────────────────────────────
id                      INTEGER PK
account_id              INTEGER FK -> Account
                        # UNIQUE: one settings row per account
setup_complete          BOOLEAN DEFAULT FALSE
user_name               TEXT                   # Greetings only
monthly_income_cents    INTEGER                # Expected; informational
pay_frequency           TEXT                   # 'monthly','biweekly','weekly'
pay_day                 INTEGER                # Day of month, or weekday
debt_method             TEXT                   # 'snowball' | 'avalanche'
hourly_wage_cents       INTEGER NULLABLE       # Time-cost display (disabled v1)
review_day              TEXT                   # 'sunday', 'monday', ...
check_in_anchor         TEXT                   # User-described habit anchor
currency_symbol         TEXT DEFAULT '$'
csv_column_map          TEXT NULLABLE          # JSON: remembered bank column mapping
created_at              DATETIME
updated_at              DATETIME
UNIQUE(account_id)
```

### 4.3 BudgetCategory

```
BudgetCategory
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
name                TEXT NOT NULL
emoji               TEXT NULLABLE
sort_order          INTEGER
kind                TEXT NOT NULL DEFAULT 'spending'
                        # 'spending' | 'income' | 'debt' | 'savings'
is_system           BOOLEAN DEFAULT FALSE       # Locked defaults
is_active           BOOLEAN DEFAULT TRUE
created_at          DATETIME
```

> v2 change: the v1 booleans `is_savings` / `is_debt_minimum` are replaced by a single
> `kind` enum, which also gives us an explicit `income` category for the ledger.

Default categories created at setup:
1. Income (`kind=income`, system)
2. Housing (system)
3. Food
4. Transportation
5. Debt (`kind=debt`, system) — covers minimums and extra payments
6. Emergency Fund (`kind=savings`, system)
7. Personal
8. Everything Else

### 4.4 BudgetPeriod

One row per calendar month per account. `income_received_cents` is **derived** (sum of
income transactions in the month) and may be cached here.

```
BudgetPeriod
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
year                INTEGER NOT NULL
month               INTEGER NOT NULL           # 1-12
income_received_cents   INTEGER DEFAULT 0       # CACHED derived value
status              TEXT DEFAULT 'active'       # 'active' | 'closed'
notes               TEXT NULLABLE               # Monthly reflection
closed_at           DATETIME NULLABLE
created_at          DATETIME
UNIQUE(account_id, year, month)
```

### 4.5 BudgetAllocation

One row per category per period — the **monthly envelope target** (D12). Set once
during the allocation ritual; topped up by later paychecks against the same target.

```
BudgetAllocation
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
period_id           INTEGER FK -> BudgetPeriod
category_id         INTEGER FK -> BudgetCategory
target_cents        INTEGER DEFAULT 0           # Planned amount for the month
created_at          DATETIME
UNIQUE(period_id, category_id)
```

> `funded_cents` (how much of the target the received paychecks cover so far) and
> `spent_cents` (sum of expenses) are **derived**, not stored.

### 4.6 Transaction (the ledger)

```
Transaction
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
period_id           INTEGER FK -> BudgetPeriod  # Derived from date by ledger service
date                DATE NOT NULL
amount_cents        INTEGER NOT NULL            # Magnitude, always >= 0
direction           TEXT NOT NULL               # 'in' | 'out'  (replaces signed amount)
category_id         INTEGER FK -> BudgetCategory NULLABLE
payee               TEXT NULLABLE
memo                TEXT NULLABLE
mood_tag            TEXT NULLABLE               # 'planned','impulse','stress','celebration','necessity'
link_type           TEXT NULLABLE               # NULL | 'debt' | 'savings'
link_id             INTEGER NULLABLE            # FK to DebtAccount or SavingsGoal
principal_cents     INTEGER NULLABLE            # For debt payments: principal portion
interest_cents      INTEGER NULLABLE            # For debt payments: interest portion
is_imported         BOOLEAN DEFAULT FALSE
import_hash         TEXT NULLABLE               # Dedup key
is_deleted          BOOLEAN DEFAULT FALSE       # Soft delete (v1 API referenced this)
created_at          DATETIME
updated_at          DATETIME
```

> v2 changes: (a) **sign convention resolved** — `amount_cents` is always a non-negative
> magnitude and `direction` says in/out; there is no signed-amount/`type` redundancy.
> (b) `link_type`/`link_id`/`principal_cents`/`interest_cents` absorb the former
> `DebtPayment` and `SavingsContribution` tables. (c) `is_deleted` now exists.

### 4.7 DebtAccount

```
DebtAccount
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
name                TEXT NOT NULL
creditor            TEXT NULLABLE
opening_balance_cents   INTEGER NOT NULL        # Balance when added (immutable anchor)
cached_balance_cents    INTEGER NOT NULL        # DERIVED: opening - sum(principal paid)
interest_rate_bps   INTEGER NOT NULL            # Basis points (e.g., 2199 = 21.99%)
minimum_payment_cents   INTEGER NOT NULL
sort_order          INTEGER                     # Auto-set by debt method
is_active           BOOLEAN DEFAULT TRUE
paid_off_at         DATETIME NULLABLE
notes               TEXT NULLABLE
created_at          DATETIME
updated_at          DATETIME
```

> v2 changes: balance is anchored by `opening_balance_cents` and **derived** into
> `cached_balance_cents`. Interest rate stored as integer **basis points** (no decimals).

### 4.8 SavingsGoal

```
SavingsGoal
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
name                TEXT NOT NULL
goal_type           TEXT NOT NULL               # 'emergency_fund' | 'sinking_fund' | 'mission'
target_cents        INTEGER NOT NULL
opening_balance_cents   INTEGER DEFAULT 0       # Balance when added (immutable anchor)
cached_balance_cents    INTEGER DEFAULT 0       # DERIVED: opening + sum(contributions) - withdrawals
target_date         DATE NULLABLE
is_active           BOOLEAN DEFAULT TRUE
is_complete         BOOLEAN DEFAULT FALSE        # DERIVED flag (cached_balance >= target)
completed_at        DATETIME NULLABLE
notes               TEXT NULLABLE
created_at          DATETIME
updated_at          DATETIME
```

> The v1 `SavingsContribution` table is removed; contributions are linked transactions.

### 4.9 Mission

```
Mission
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
name                TEXT NOT NULL
mission_type        TEXT NOT NULL               # 'debt_payoff' | 'emergency_fund' | 'savings_goal'
link_type           TEXT NULLABLE               # 'debt' | 'savings'
link_id             INTEGER NULLABLE            # FK to DebtAccount or SavingsGoal
status              TEXT DEFAULT 'active'        # 'active' | 'completed' | 'paused'
sort_order          INTEGER                      # Queue position
started_at          DATETIME
completed_at        DATETIME NULLABLE
created_at          DATETIME
```

> v2 change: `target_amount`/`start_amount` are **removed**. Mission progress is
> **derived** from the linked debt/savings entity at read time (start = entity opening
> balance, current = entity derived balance, target = debt 0 or goal target). This
> prevents the mission's numbers from drifting away from the entity it tracks.

### 4.10 CheckIn

```
CheckIn
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
date                DATE NOT NULL
duration_seconds    INTEGER NULLABLE
created_at          DATETIME
UNIQUE(account_id, date)
```

### 4.11 Review

```
Review
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
review_type         TEXT NOT NULL               # 'weekly' | 'monthly'
period_id           INTEGER FK -> BudgetPeriod NULLABLE
week_start          DATE NULLABLE
prompt_shown        TEXT NULLABLE               # The reflection prompt presented
completed_at        DATETIME
duration_seconds    INTEGER NULLABLE
notes               TEXT NULLABLE
created_at          DATETIME
```

### 4.12 Milestone

```
Milestone
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
milestone_type      TEXT NOT NULL               # 'debt_paid_off','savings_goal_reached',
                                                 # 'streak_achieved','first_budget', etc.
title               TEXT NOT NULL
description         TEXT NULLABLE
threshold           INTEGER NULLABLE             # e.g., streak day count, for idempotency
link_type           TEXT NULLABLE
link_id             INTEGER NULLABLE
celebrated          BOOLEAN DEFAULT FALSE
created_at          DATETIME
UNIQUE(account_id, milestone_type, threshold, link_id)
```

> v2 change: a `UNIQUE` constraint enforces "a milestone is created only once" at the
> database level rather than relying on an application check alone.

### 4.13 AssetAccount (new — supports Net Worth, a Foundation feature)

The brief rates Net Worth as Foundation, but v1.0 had no model for assets. Minimal
support:

```
AssetAccount
─────────────────────────────────────
id                  INTEGER PK
account_id          INTEGER FK -> Account
name                TEXT NOT NULL               # 'Checking', 'Savings', 'Cash'
balance_cents       INTEGER NOT NULL            # Manually updated snapshot
is_active           BOOLEAN DEFAULT TRUE
updated_at          DATETIME
created_at          DATETIME
```

> v1 keeps this deliberately simple: a manually-updated balance snapshot per account.
> Net worth = sum(asset balances) − sum(derived debt balances). A future version can
> derive asset balances from transactions; v1 does not, to keep entry friction low.

### 4.14 Derived values (not columns — defined here for clarity)

- **Category `funded_cents`** (cumulative envelope, D12): for a period, the running
  income received is allocated to category targets *in priority order* (debt minimums
  and savings first — Pay Yourself First). `funded_cents` per category = the portion of
  received income its target has "claimed" so far. This is what powers "you've funded
  $X of your $Y plan" without forcing a new ritual each paycheck.
- **Category `remaining_cents`** = `funded_cents` − `spent_cents`.
- **Days-of-expenses coverage** (Emergency Fund screen): `emergency_fund_balance /
  avg_daily_expense`, where `avg_daily_expense` = (sum of `kind='spending'` expense
  transactions over the trailing 90 days) / 90. If fewer than 30 days of data exist,
  fall back to (sum of current period spending targets) / 30 and label it "estimated."

---

## 5. Onboarding Flow

Onboarding runs once, gated until `Settings.setup_complete = TRUE`. Six steps with a
visible progress indicator. (Unchanged from v1.0 in intent; money inputs are captured
as cents, interest as basis points.)

### Step 1: Welcome
- Warm intro, no financial questions yet. Ask for a name (greetings only).
- One prompt: "This app will help you rebuild — one day at a time."

### Step 2: Income Setup
- Monthly take-home income (after tax). Pay frequency: monthly / biweekly / weekly.
- Pay day (day of month or weekday). Optional effective hourly wage (feature disabled in v1).

### Step 3: Budget Categories
- Present the 8 default categories. Allow rename, add 1–3 custom, hide unused.
- Cannot delete Income, Housing, Debt, Emergency Fund (system).
- "We'll set dollar amounts next. For now, just confirm these buckets fit your life."

### Step 4: Debt Setup
- "Do you have any debt you want to pay off?" Yes / No / Not yet.
- If Yes: add accounts (name, balance, interest rate, minimum payment). Multiple allowed.
- Debt method choice (no default — user chooses), with a concrete example using their
  real numbers: **Snowball** (smallest balance first; momentum) vs **Avalanche**
  (highest rate first; less interest).

### Step 5: Emergency Fund
- "Do you have emergency savings?" Yes / No. If Yes: current balance.
- Target: $500 / $1,000 / 1 month of expenses (auto) / custom.

### Step 6: Mission Queue
- Auto-generated queue: emergency fund to $500 first if under; else complete fund; else
  first debt in payoff order. User can reorder non-emergency-fund missions.

### Setup Complete
- Dashboard loads. First prompt: "You have [income] coming in. Let's give every dollar a job."

---

## 6. Core User Flows

### 6.1 Daily Check-In Flow
Any page load creates/confirms today's `CheckIn` and updates the streak. Dashboard shows
greeting + date, streak, active-mission progress, weekly spending pulse, one "Next Right
Action," and an always-visible Add Transaction button. Under 60 seconds; no required
action unless income is unallocated.

### 6.2 Income Entry & Allocation Flow (revised for D12)

**Trigger:** "Log Income," or a prompt on expected pay day.

1. Enter income amount + date (payee optional). Ledger service creates an **income
   transaction**; `BudgetPeriod.income_received_cents` (cached) updates.
2. **If category targets for this month are not yet set** (first paycheck of the month):
   open the **Allocation Flow** to set per-category `target_cents` for the whole month.
   - Pre-populate, in priority order (Pay Yourself First): Debt category target = sum of
     active `minimum_payment_cents`; Emergency Fund target = configured monthly amount or
     remaining-to-target, whichever is smaller.
   - User fills remaining categories. A running "Unallocated" counter (HTMX, computed
     server-side) must reach $0 to confirm. "Not sure? Put it in Everything Else."
3. **If targets are already set** (a later paycheck in the same month): **do not** reopen
   the ritual. The new income simply increases received income; the dashboard shows
   funded-vs-target climbing. Optional "Adjust this month's plan" link if they want to.

**Income-under-obligations edge case (new).** If received income for the month is less
than the non-negotiable targets (debt minimums + housing + emergency), the flow does
**not** hard-block at a negative remainder. Instead it surfaces a calm triage screen:
"Your income so far this month is $X, and your essentials plan is $Y. Let's decide what
gets funded first." It funds in priority order, shows what is currently unfunded, and
offers: lower a target, defer emergency-fund contribution this month, or mark that more
income is expected (don't fully allocate yet). The zero-based hard block applies only
when income ≥ essentials.

### 6.3 Expense Logging Flow
Always-visible Add Transaction. Fields: date (default today), amount, category
(required), payee (optional), mood tag (optional 5-icon row), memo (optional). On save,
the category's remaining updates via HTMX. If now over target, the **Overage prompt**
(6.4) appears.

### 6.4 Category Overage Reallocation Flow
Friendly modal (not an alert): "You spent $42 more than planned in Food this month. Want
to move funds from another category?" Lists categories with headroom (most available
first). User pulls from one and confirms, **or** "I'll adjust next month." Moving funds
edits both `BudgetAllocation.target_cents` rows. The transaction is saved regardless.

### 6.5 Weekly Review Flow
Triggered on the designated review day. Five steps (15 min, or quick mode in 2–3):
this-week-at-a-glance (amber for over, teal for on track — never red); reallocate if
needed (6.4 in sequence); mission check (log a payment now if desired); one intention
(optional text); close. Streak acknowledges ("7 reviews in a row").

### 6.6 Monthly Reset Flow
Triggered on the 1st (or manually). Steps: last-month summary (income vs received,
spent vs target, mission progress, milestones); celebrate milestones (6.7);
optional reflection (stored in `BudgetPeriod.notes` + `Review`); **subscription review**
(recurring expenses listed as Keep / Review / Cancel with annual cost); **new-month
allocation** (create new `BudgetPeriod`, set fresh targets; last month shown greyed as
reference; sweep prompt for unspent funds to active mission); month open. The reset also
runs `recompute_balances(ctx)` as a reconciliation check and triggers a backup.

### 6.7 Milestone Celebration Flow
Full-screen moment (not a toast) when: a debt balance hits $0, a savings goal hits
target, a streak threshold (7/30/90/180/365), first budget, or first review. States what
was achieved, offers optional "how does it feel?" text, shows the updated debt queue if
applicable. `celebrated` flips TRUE so it shows once (enforced by the unique constraint
in §4.12).

### 6.8 Re-engagement Recovery Flow
After 3+ days away: no guilt, no "you've been gone X days," no recap. Normal dashboard +
one soft banner: "Welcome back. Quick catch-up, or just move forward from today?"
Quick catch-up: one rough total → assigned to Everything Else (or split). Streak resets
to 1, never negative.

### 6.9 CSV Import Flow
Settings → Import. Upload CSV; map columns (date, amount, direction, payee); the
remembered mapping is stored in `Settings.csv_column_map`. Preview; dedup by
`import_hash` (excluded by default). User unchecks any to exclude. Imported rows get
`is_imported=TRUE`, no mood tag, and route through the **ledger service** like any other
transaction (so period assignment and dedup are consistent). Uncategorized rows queue in
a "Needs Category" inbox; a nav badge shows the count. No auto-categorization in v1.

---

## 7. Screen Inventory & Specifications

### Navigation
Persistent sidebar (desktop) / bottom nav (mobile): Dashboard, Budget, Transactions,
Debt, Savings, Reviews, Settings.

### Screen 1: Dashboard
Daily check-in glance.

```
┌─────────────────────────────────────────┐
│  Good morning, [Name].          Day 24  │  ← Streak
│  ┌─────────────────────────────────┐    │
│  │  CURRENT MISSION                │    │
│  │  Pay off Capital One — $1,847   │    │
│  │  ████████████░░░░░░░░  62%      │    │  ← derived from linked debt
│  │  Est. payoff: March 2027        │    │
│  └─────────────────────────────────┘    │
│  THIS WEEK                              │
│  $843 spent of $1,200 funded            │
│  6 days remaining                       │
│  NEXT RIGHT ACTION                      │
│  → Your weekly review is due today      │
│         [+ Add Transaction]             │
└─────────────────────────────────────────┘
```

**Logic.** Streak: consecutive `CheckIn` days counting back from today, with one grace
day (§9.2). Weekly pulse: sum of week's `kind='spending'` expenses vs prorated funded
targets. Next Right Action priority (first match): unallocated income → review due →
monthly reset available → debt payment not logged this month → no transactions in 5 days
→ "You're on track."

### Screen 2: Budget
Tabs: Current Month | History.

```
┌──────────────────────────────────────────────────┐
│  June 2026                 $2,400 received        │
│  [Set / Adjust Plan]                              │
│  Category        Target   Funded   Spent  Remaining│
│  Housing         $1,000   $1,000   $1,000  $0      │
│  Food            $400     $400     $287    $113  ▓ │
│  Transportation  $250     $250     $189    $61   ▓ │
│  Debt            $350     $350     $350    $0      │
│  Emergency Fund  $100     $100     $100    $0      │
│  Personal        $150     $150     $207   -$57   ⚠ │
│  Everything Else $150     $150     $43     $107  ▓ │
│  ────────────────────────────────────────────────│
│  Total           $2,400   $2,400   $2,176  $224    │
└──────────────────────────────────────────────────┘
```

`Funded` reflects cumulative envelope (D12): when only part of the month's income has
arrived, `Funded < Target` and the gap is shown calmly. ⚠ amber on over; tapping opens
6.4. Unallocated banner if income received exceeds funded targets.

### Screen 3: Transactions
Current month, sorted date-desc. Filters: date range, category, direction, mood tag.
Add via slide-in panel (HTMX) so context is preserved. Debt-payment and
savings-contribution rows show their link inline.

### Screen 4: Debt
Active Target card (balance, % paid off, min, extra this month, projected payoff,
[Log Payment] / [See Projection]). Debt Queue below in payoff order. Projection view: a
Chart.js line chart of current pace plus +$50/+$100 scenarios with interest saved.

### Screen 5: Savings
Emergency Fund card (balance vs target, %, days-of-expenses coverage per §4.14, est.
complete). Other Goals below.

### Screen 6: Reviews
Opens the review flow if due; otherwise shows review history (intentions + notes).

### Screen 7: Settings
Profile; categories (rename/hide/reorder/add); debt method; review day; hourly wage
(disabled); CSV import; **Create Backup** (SQLite backup API); data export (CSV);
about/version.

---

## 8. API Surface

FastAPI. HTML routes return server-rendered pages or HTMX partials; `/api/*` routes
return JSON (the seam for a future native/mobile client, D13/D14). Every handler resolves
`AccountContext` via the `get_account_context` dependency.

### Onboarding
```
GET  /onboarding
POST /onboarding/settings
POST /onboarding/categories
POST /onboarding/debt
POST /onboarding/savings
POST /onboarding/missions          # marks setup_complete
```

### Dashboard
```
GET  /                             # redirects to /onboarding if not set up
GET  /api/dashboard                # JSON: streak, mission progress, pulse, next action
POST /api/checkin                  # record daily check-in (called on load)
```

### Transactions
```
GET  /transactions
POST /transactions                 # -> ledger service
GET  /transactions/{id}
POST /transactions/{id}            # update -> ledger service (re-derives period)
POST /transactions/{id}/delete     # soft delete (is_deleted = TRUE); 10s undo
POST /transactions/{id}/restore    # undo
GET  /api/transactions/recent
```

### Budget
```
GET  /budget
GET  /budget/{year}/{month}
POST /budget/income                # log income -> ledger; opens allocation if needed
GET  /budget/allocate
POST /budget/allocate              # save category targets
POST /budget/reallocate            # move target between categories
GET  /api/budget/pulse
GET  /api/budget/summary/{period_id}   # target/funded/spent/remaining by category
```

### Debt
```
GET  /debt
POST /debt                         # add account
GET  /debt/{id}
POST /debt/{id}                    # edit details (not balance)
POST /debt/{id}/payment            # -> ledger; splits principal/interest
POST /debt/{id}/adjust             # balance correction -> adjustment transaction
GET  /api/debt/projection/{id}
```

### Savings
```
GET  /savings
POST /savings/contribution         # -> ledger (link_type='savings')
POST /savings/withdrawal           # -> ledger (negative direction)
GET  /api/savings/emergency
```

### Missions
```
GET  /api/missions/active          # progress derived from linked entity
POST /missions/{id}/complete
POST /missions/reorder
```

### Reviews
```
GET  /reviews
GET  /reviews/weekly
POST /reviews/weekly
GET  /reviews/monthly
POST /reviews/monthly              # includes period close + recompute + backup
```

### Settings, Import, Net Worth
```
GET  /settings
POST /settings
GET  /settings/categories
POST /settings/categories
POST /settings/import/csv
GET  /settings/import/preview
POST /settings/import/confirm      # -> ledger service
GET  /settings/export
POST /settings/backup              # SQLite backup API
GET  /assets                       # asset accounts (net worth inputs)
POST /assets                       # add/update asset balance snapshot
GET  /api/networth                 # JSON: assets - derived debt balances
```

### Milestones
```
GET  /milestone/{id}
POST /milestone/{id}/celebrate
GET  /api/milestones
```

---

## 9. Feature Specifications

### 9.1 Cumulative-Envelope Allocation Engine
**Service:** `services/allocation.py`

1. Income transaction created via ledger; `BudgetPeriod` resolved from date.
2. First paycheck of a month with no targets → allocation ritual sets `target_cents`
   per category. Pre-fill debt minimums + emergency fund first (Pay Yourself First).
3. Real-time "Unallocated" counter via HTMX, computed server-side; zero-based hard block
   when income ≥ essentials. "Everything Else" absorbs leftovers.
4. Later paychecks in the same month do **not** reopen the ritual; received income rises
   and `funded_cents` per category climbs in priority order.
5. Income-under-obligations triage replaces the hard block when income < essentials (6.2).

### 9.2 Streak Calculation
**Service:** `services/streak.py`

- Page load creates today's `CheckIn` if absent.
- Streak = consecutive days with a CheckIn, counting back from today.
- **Grace day:** the run survives **one** single-day gap; a second gap, or a 2+-day gap,
  resets to 1. Computed by walking back the CheckIn dates and allowing at most one
  one-day hole in the run.
- Thresholds 7/30/90/180/365 create a `Milestone` once each (unique constraint §4.12).

### 9.3 Debt Payoff Projection
**Service:** `services/debt_payoff.py`. Integer-cents amortization, monthly:
```
monthly_rate = interest_rate_bps / 10000 / 12
balance = cached_balance_cents
while balance > 0:
    interest = round(balance * monthly_rate)
    principal = payment_cents - interest
    if principal <= 0: break        # payment doesn't cover interest -> flag
    balance = max(0, balance - principal)
    months += 1
```
Present scenarios: minimum only; current allocation; +$50; +$100. Each returns months,
total interest (cents), payoff date. Flag the "payment ≤ interest" case explicitly.

### 9.4 Next Right Action Logic
Computed in the dashboard service. Priority (first match): setup incomplete → income not
logged → unallocated income → review due today → monthly reset available (1st–3rd, prior
month open) → active-mission debt payment not logged this month → last transaction >5
days ago → "You're on track."

### 9.5 Mood Tag System
Optional 5-icon row on the transaction form: 📅 Planned ⚡ Impulse 😓 Stress 🎉
Celebration 🧾 Necessity. Stored in `Transaction.mood_tag`. Shown inline in lists,
filterable. Monthly review surfaces an informational line ("X impulse purchases totaling
$Y") with no judgment framing.

### 9.6 Monthly Reflection Prompt
Shown in Monthly Reset step 3 and revisitable from Review history. The exact prompt text
shown is stored in `Review.prompt_shown` alongside the response in `Review.notes`. Prompt
list lives in config; rotate by month number.

### 9.7 CSV Import & Deduplication
**Service:** `services/csv_import.py`. Browser column mapping, remembered in
`Settings.csv_column_map`. Dedup hash = SHA-256 of `date + amount_cents +
payee[:20]` (lowercased, stripped), stored in `Transaction.import_hash`; matches excluded
by default. Direction toggle ("which way means you spent money?") per file format.
Uncategorized rows → "Needs Category" inbox with a nav badge. All inserts go through the
ledger service.

### 9.8 Net Worth Snapshot (Foundation; new model support)

`GET /api/networth` returns sum of `AssetAccount.balance_cents` minus sum of derived debt
balances, plus a trend computed from monthly snapshots captured at each monthly close.

---

## 10. Design Principles & Language Rules

Governs every piece of copy. (Unchanged from v1.0 — these are correct as written.)

### Language Rules

| Situation | Don't say | Say instead |
|---|---|---|
| Over budget | "You exceeded your budget" | "You spent $X more than planned in [Category]" |
| Missed check-in | "You haven't opened the app in X days" | [Nothing. Welcome back normally.] |
| Debt | "You owe $X" | "Your current balance is $X" |
| Progress | "Only $X paid off" | "You've paid off $X" |
| Review needed | "You haven't done your review" | "Time for your weekly review" |
| Goal not met | "You fell short of your goal" | "Here's where you are" |

### Color Rules

- **Never red** for over-budget; use amber (#F59E0B). Teal/green for progress; grey for
  closed data. No alarm iconography in routine states. Progress bars fill (never deplete);
  frame as "X% used" with a remaining label.

### UX Rules

- Add Transaction reachable in one tap from anywhere. No confirm dialogs for edits; 10s
  undo on delete. Mobile-friendly required. Local data should feel instant (no spinner at
  v1 volumes). No "Nothing here yet" empty states — use a first-action prompt.

---

## 11. Out of Scope for v1

| Feature | Deferred to |
|---|---|
| Subcategory granularity | v2 — after 60 days of consistent use |
| Sinking funds | v2 |
| Retirement / long-term projection | v2 |
| "What Would Change?" scenario modeler | v2 |
| Automatic bank sync (Plaid, etc.) | Not planned — privacy preference |
| Pause Before Purchase nudge | v2 |
| Time-cost display (hourly wage) | v2 (setting exists, feature disabled) |
| PDF/report export | v2 |
| Multiple users / household view | **Future** — seams built in v1 (D14, Appendix A), not exposed |
| Native mobile app | **Future** — JSON API surface prepared (D13), not built |
| Authentication / hosted deployment | **Future** — see Appendix A |

---

# Appendix A: Multi-User Migration Path

This appendix documents how v1 (single-user, local, SQLite) becomes multi-user without a
rewrite. None of this is built in v1; the seams are.

**Already in place in v1 (the seams):**
- Every table carries `account_id` (all rows = 1 in v1).
- Every service function takes `ctx: AccountContext`; every repository query is scoped by
  `ctx.account_id`.
- `get_account_context()` is the single dependency that produces the context.
- Money is integer cents (portable to Postgres `BIGINT`); SQL is ORM-level and avoids
  SQLite-only constructs.
- Alembic has migration history from commit one.

**Migration steps when going hosted/multi-user:**
1. **Add identity.** New `User` table (email, password hash, etc.) with a relationship to
   `Account` (1:1 to start; 1:many enables households later). Add session/auth middleware.
2. **Flip the seam.** Change `get_account_context()` to read `account_id` from the
   authenticated session instead of returning the hardcoded singleton. No service or
   repository signatures change.
3. **Swap the database.** Point SQLAlchemy at Postgres; run Alembic migrations. Because
   the schema was kept portable and money is integer, this is mechanical. Move from
   SQLite WAL to Postgres connection pooling.
4. **Harden for concurrency & web.** Real session store, CSRF protection on form posts,
   rate limiting, bind to a real interface behind TLS, per-account backups.
5. **Expose the API.** The `/api/*` JSON routes (already typed via Pydantic/FastAPI)
   become the contract for a native/mobile client; add token auth for non-browser clients.

**Trust-model note.** Going hosted reverses the brief's local-first/privacy stance. That
is a product decision, not just an engineering one, and should be made deliberately
(data residency, encryption at rest, breach exposure, regulatory scope). The behavioral
design is unaffected.

---

## 14. Appendix B: Money Handling Reference

- **Storage:** all amounts are `INTEGER` cents. Interest rates are `INTEGER` basis points
  (2199 = 21.99%). No `DECIMAL`, no float, anywhere in the schema.
- **Boundaries:** `money.py` provides `to_cents(str|Decimal) -> int` for parsing user
  input (parse via `Decimal`, then ×100, round half-up) and `to_display(int) -> str` for
  rendering. Conversions happen only at the HTTP boundary; the core never sees floats.
- **Arithmetic:** all budget/allocation/payoff math is integer arithmetic. Amortization
  rounds interest to the nearest cent each month (§9.3).
- **Why:** SQLite's NUMERIC affinity can silently coerce decimals to float, breaking the
  exact zero-based sum the budget depends on. Integers make equality reliable and port
  cleanly to Postgres.

---

*End of Product Design Spec v2.0*
*Next step: technical planning — Alembic baseline migration, ledger-service contract,
and component build order (ledger → allocation → dashboard → debt/savings → reviews).*
