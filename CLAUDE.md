# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Personal Finance App (v2.0 — Foundation Release)**

A single-user local web application for personal budget management. Runs on `127.0.0.1:5000` (loopback only). Users log income, expenses, and debt payments; the app derives balances from transactions and helps allocate paychecks to budget categories each month using a cumulative-envelope model.

**Key architectural principle:** Transactions are the source of truth; all balances (budget spent, debt balance, savings balance) are derived from transactions and recomputable, never stored authoritatively.

**Scope:** Foundation tier only — onboarding, daily check-in, budget allocation, transactions, debt payoff, savings goals, monthly reviews, CSV import.

## Architecture & Layers

Three-layer architecture (the multi-user seam):

- **Routers** (`finapp/routers/`) — HTTP only. Parse request → Pydantic model → call service.
- **Services** (`finapp/services/`) — All business logic. Every function takes `ctx: AccountContext` as the first argument (the seam for future multi-user support).
- **Repository** (`finapp/repository/`) — All SQL access via SQLAlchemy. Every query scoped by `ctx.account_id`.

Single-user v1 hardcodes `account_id = 1` in `deps.py::get_account_context()`; flipping to multi-user is one-line change there (see Appendix A of the design spec).

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Framework | FastAPI + Jinja2 + HTMX (server-rendered HTML) |
| ORM | SQLAlchemy 2.0 (typed) |
| Migrations | Alembic (from day one for Postgres migration path) |
| Database (v1) | SQLite with WAL mode |
| Validation | Pydantic v2 |
| CSS | Tailwind (Play CDN) |
| UI toggles | Alpine.js (optional, CDN) |
| Charts | Chart.js (CDN) |

**Why HTMX over Alpine for money logic:** The "Unallocated: $0" counter and budget-remaining figures must recompute server-side so the zero-based math has one implementation (Python). HTMX lets the server return HTML fragments to swap in.

## Money Discipline (Non-negotiable)

- **Storage:** All amounts as `INTEGER` cents (not `DECIMAL` or float). Interest rates as `INTEGER` basis points (2199 = 21.99%).
- **Boundaries:** `finapp/money.py` provides `to_cents(str|Decimal) -> int` (parse via `Decimal`, ×100, round half-up) and `to_display(int) -> str`. Conversions happen only at the HTTP boundary.
- **Arithmetic:** All budget/allocation/payoff math uses integer arithmetic. Amortization rounds interest monthly (§9.3 of spec).
- **Why:** SQLite's NUMERIC affinity can silently coerce decimals to float, breaking the exact zero-based sum the budget depends on. Integers are exact and port cleanly to Postgres.

**Every phase's Done gate includes reconciliation:** `recompute_balances(ctx)` rebuilds every cache from the ledger and runs in CI. Cached balance must equal pure-derived value.

## File Structure (at completion)

```
finapp/
├── main.py                      # FastAPI app, router registration
├── deps.py                      # get_account_context() dependency (the seam)
├── db.py                        # Engine, session, WAL pragma, SQLite/Postgres URL
├── models.py                    # SQLAlchemy ORM models (all tables, all account_id)
├── schemas.py                   # Pydantic request/response models
├── money.py                     # to_cents, to_display (most-tested unit)
├── run.py                       # Startup: alembic upgrade → Uvicorn bind → browser
├── requirements.txt
├── alembic/                     # Migrations (versioned from day one)
├── routers/
│   ├── dashboard.py
│   ├── transactions.py
│   ├── budget.py
│   ├── debt.py
│   ├── savings.py
│   ├── missions.py
│   ├── reviews.py
│   ├── settings.py
│   └── import_csv.py
├── services/
│   ├── ledger.py                # SINGLE write path for all transactions
│   ├── allocation.py            # Cumulative-envelope allocation logic
│   ├── balances.py              # Derived values (budget spent, debt, savings, net worth)
│   ├── debt_payoff.py           # Amortization + projection scenarios
│   ├── csv_import.py            # CSV parsing, column mapping, dedup
│   ├── streak.py                # Daily check-in streak + grace day + milestones
│   └── missions.py              # Mission queue + derived progress
├── templates/                   # base.html + per-feature dirs + _partials/
├── static/
│   ├── css/
│   └── js/
├── finance.db                   # SQLite (auto-created, WAL)
├── backups/                     # Auto-backups (SQLite Online Backup API, not shutil.copy)
```

## Key Invariants (Audit Every Phase)

1. **One write path** — Every transaction is created/voided/edited through `services/ledger.py`. No other code writes a `Transaction`.
2. **Transactions are truth; balances derive** — No authoritative balance lives outside the ledger. Caches are always recomputable via `recompute_balances(ctx)` and reconciled.
3. **Integer cents / basis points only** — Floats and `DECIMAL` never enter schema or core logic.
4. **Account-scoped everything** — Every query filters by `ctx.account_id`; every service takes `ctx` first.
5. **Loopback only** — `127.0.0.1:5000`, never `0.0.0.0` (unauthenticated app must not expose to local network).
6. **Calm by design** — Never red for over-budget (use amber #F59E0B); no guilt copy; reversible actions; first-action prompts instead of empty states.

## Execution Model

**Phases are sequential.** Each phase is a self-contained dispatch unit:

1. Agent receives phase brief.
2. Agent decomposes into tasks internally.
3. Agent builds phase.
4. Agent must pass the phase's **Done gate** before the next phase dispatches.

**Done gate for every phase (starting Phase 2):** 
- Unit tests for the phase's logic pass.
- Integration tests for the phase's user-facing flow pass (where applicable).
- **`recompute_balances(ctx)` reconciliation check passes** — every cached balance equals its pure-derived value computed from the ledger.

See `docs/implementation_plan_v2.md` for phase map (0–11) and detailed Done gate for each.

## Common Commands (Will Be)

Once Phase 0 is complete:

```bash
# Run the app (alembic migrate + start server + open browser)
python run.py

# Or on Windows
start.bat

# Run tests (unit + integration)
pytest

# Run a single test
pytest tests/test_money.py -v

# Lint
flake8 finapp/ --max-line-length=100

# Alembic: create migration
alembic revision --autogenerate -m "description"

# Alembic: apply migrations
alembic upgrade head

# Alembic: rollback
alembic downgrade -1
```

## Critical External References

- **Product Design Spec v2.0:** `docs/product_design_spec.md` — The complete system spec. Phase briefs derive from this.
  - Decisions D1–D14 lock the scope; any deviation must re-evaluate affected sections.
  - Out of Scope (§11): subcategories, sinking funds, retirement projection, bank sync, native mobile (JSON API surface prepared), multi-user (seams built, not exposed).
  - Appendix A: Multi-user migration path (the seam in action).
  - Appendix B: Money handling reference (integer cents, Decimal parsing, why not float).

- **Implementation Plan v2.0:** `docs/implementation_plan_v2.md` — Phase-by-phase briefs, order, and Done gates. Dispatch agents sequentially through this, one phase at a time.

## Data Correctness & Testing

**Reconciliation is the release gate.** A phase is not done until:
1. Unit tests pass.
2. Integration tests pass.
3. `recompute_balances(ctx)` produces zero drift.

The reconciliation check runs in CI starting Phase 2. It rebuilds every cached balance from the ledger and asserts they match. This is the single non-negotiable correctness gate.

**Property-style tests are encouraged.** For example, Phase 2's Done gate includes a randomized-transaction property test that `recompute_balances` produces zero drift across hundreds of generated transaction sets.

## Onboarding a New Agent

1. Read the **Product Design Spec v2.0** (`docs/product_design_spec.md`) — at least the Decisions (§1) and your phase's subsection of §6–§9.
2. Read the **Implementation Plan v2.0** (`docs/implementation_plan_v2.md`) — the phase map and your phase's brief.
3. Understand the architecture seam: `AccountContext` in every service, scoped queries in repository, HTTP-only routers.
4. Know the three money rules: integer cents, `money.py` at boundaries only, `recompute_balances(ctx)` as the reconciliation gate.
5. Dispatch your phase by the brief. Build unit tests, integration tests, and a passing reconciliation check before signaling Done.

## Bootstrap (Phase 0)

Phase 0 sets up the skeleton and the toolchain so all later phases drop into a known structure. Key steps:
- Create the `finapp/` package with the file structure above.
- Pin the stack: Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Uvicorn, HTMX, Tailwind.
- Implement the three-layer seam: routers → services (taking `ctx` first) → repository (scoped by `ctx.account_id`).
- Implement `money.py` with round-trip tests (parsing, rounding half-up, display, edge cases).
- Implement `run.py`: alembic migrate → Uvicorn loopback bind → browser open.
- Stand up CI: lint + test runner (placeholder reconciliation slot for Phase 2+).

See Phase 0 brief in `docs/implementation_plan_v2.md` for the full Done gate.
