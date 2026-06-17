# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

### Personal Finance App (v2.0 — Foundation Release)

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
| --- | --- |
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

## Common Commands

```bash
# Install all deps (creates .venv automatically)
uv sync

# Run the app (alembic migrate + start server + open browser)
uv run python finapp/run.py

# Or on Windows
start.bat

# Run tests (unit + integration)
uv run pytest tests/

# Run a single test
uv run pytest tests/test_money.py -v

# Lint
uv run flake8 finapp/ --max-line-length=100

# Add a production dependency
uv add <package>

# Add a dev/test dependency
uv add --dev <package>

# Alembic: create migration
uv run alembic revision --autogenerate -m "description"

# Alembic: apply migrations
uv run alembic upgrade head

# Alembic: rollback
uv run alembic downgrade -1
```

## Data Correctness & Testing

**Reconciliation is the release gate.** A phase is not done until:

1. Unit tests pass.
2. Integration tests pass.
3. `recompute_balances(ctx)` produces zero drift.

**Property-style tests are encouraged.**
