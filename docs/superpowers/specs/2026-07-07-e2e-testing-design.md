# E2E Testing Design

## Context

The app's current test suite (`tests/`) covers unit logic and HTTP-level integration tests via FastAPI's `TestClient` (in-process ASGI calls through `httpx`). These never render HTML in a real browser or execute client-side JS, so a class of bug can slip through — e.g. the recent CSV import XSS issue, where unescaped CSV-derived values were only caught by inspection, not by any automated test, because `TestClient` never renders or parses the returned HTML as a browser would.

This design adds a browser-driven end-to-end (E2E) test suite as a pre-milestone safety net before continuing further Foundation-tier phases, covering the app's critical user flows against a real running server and real browser.

## Goals

- Catch bugs that only manifest in real HTML rendering, HTMX partial swaps, and browser JS execution (Alpine.js toggles, client-side validation).
- Cover the app's critical flows: onboarding, daily check-in, budget allocation, transactions, debt payoff, savings goals, monthly review, and CSV import.
- Keep the suite fast and reliable enough to run routinely during development (not just in CI, since no CI exists yet).
- Fit the existing `uv` + `pytest` toolchain and follow patterns already established in `tests/conftest.py`.

## Non-Goals

- CI pipeline setup. No `.github/workflows` exists yet; these tests are runnable locally via `uv run pytest tests/e2e/`. CI wiring is a future follow-up if wanted.
- Alembic migration-drift coverage. Like the existing test suite, E2E tests build schema via `Base.metadata.create_all`, not via applying real Alembic migrations. This is a known, pre-existing gap, not one introduced by this design.
- Cross-browser testing. Chromium only.

## Approach

### Tooling

Add `playwright` and `pytest-playwright` as dev dependencies via `uv add --dev`. These integrate directly with `pytest`, matching the existing test runner and avoiding a second test framework.

### Directory structure

New `tests/e2e/` directory, sibling to `tests/integration/` (HTTP-level tests) and the unit tests in `tests/`. It gets its own `conftest.py` for server/browser fixtures, keeping E2E-specific setup out of the existing `tests/conftest.py`.

Run via:

```bash
uv run pytest tests/e2e/
```

Headless Chromium by default; `pytest-playwright` supports `--headed` for local debugging.

### Server & DB fixtures

Playwright drives a real browser making real HTTP requests, so it needs an actual server listening on a port — unlike `TestClient`, which calls the ASGI app in-process without a socket.

A function-scoped fixture:

1. Creates a fresh in-memory SQLite engine with `StaticPool` (mirrors `tests/conftest.py::db_engine`), so all threads — including the background server thread — share the same in-memory database.
2. Overrides `get_db` and `get_account_context` on `finapp.main.app` (same pattern as `test_client_for_budget` in `tests/conftest.py`), seeding a test `Account`.
3. Starts `uvicorn` serving `finapp.main.app` in a background thread, bound to `127.0.0.1` on an ephemeral port.
4. Waits for the server to be ready (poll a health/root request) before yielding.
5. On teardown: stops the uvicorn server, clears `dependency_overrides`, disposes the engine.

Each test function gets a fully isolated DB and server instance — no cross-test pollution, no shared state.

A `seed_account(db, **overrides)`-style helper (living in `tests/e2e/conftest.py` or a shared module) performs direct DB inserts — settings, budget categories, debt accounts, savings goals — for tests that need pre-existing state without walking onboarding in the browser first.

**Known trade-off:** schema comes from `Base.metadata.create_all`, not applied Alembic migrations. This is consistent with the existing test suite's approach and is an accepted, pre-existing gap — not something this design needs to solve.

### Test data setup strategy

- **One comprehensive onboarding walkthrough** (`test_onboarding_e2e.py`) drives the entire onboarding flow through the real browser end-to-end: welcome → income → pay schedule → categories → debts → savings goal → lands on a usable dashboard.
- **All other flow tests** DB-seed their prerequisite state directly (e.g. insert a `DebtAccount` before testing debt payoff), then use the browser only to exercise the specific flow under test. This keeps those tests fast and focused instead of re-walking onboarding every time.

### Test coverage (first pass)

| File | Flow | Notes |
| --- | --- | --- |
| `test_onboarding_e2e.py` | Full onboarding → dashboard | The one full walkthrough test; asserts on rendered HTML/HTMX swaps at each step, not just status codes |
| `test_daily_checkin_e2e.py` | Daily check-in | DB-seeded account/budget state |
| `test_budget_allocation_e2e.py` | Paycheck → category allocation | DB-seeded account/categories |
| `test_transactions_e2e.py` | Add/edit/void transaction | DB-seeded account/categories |
| `test_debt_payoff_e2e.py` | Debt payment flow | DB-seeded debt account |
| `test_savings_goals_e2e.py` | Savings goal contribution | DB-seeded savings goal |
| `test_csv_import_e2e.py` | Upload → preview → edit → confirm | Verifies CSV-derived values render HTML-escaped in the preview (closes the loop on the recent XSS fix); verifies dedup and partial-success summary render correctly |
| `test_monthly_review_e2e.py` | Monthly review / rollover | DB-seeded prior-month state |

### What these tests catch that HTTP-level tests can't

- Real HTML rendering and escaping — e.g. would have caught the CSV import XSS bug, since a real browser parses the response as HTML/DOM rather than treating it as an opaque string.
- Real HTMX swap behavior — partial fragments landing in the correct DOM location, no unintended full-page reloads breaking client-side state.
- Real browser form and JS interactions — Alpine.js toggles, client-side validation, focus/keyboard behavior.

## Testing

- Each E2E test is self-verifying by construction (it drives the browser and asserts on resulting DOM state/rendered content).
- No changes to unit or HTTP-level integration test behavior; this is a purely additive suite.
- `recompute_balances(ctx)` reconciliation isn't directly exercised by these tests (they test UI/rendering, not derived-balance math, which is already covered by existing unit/integration tests) — flows that mutate the ledger should still leave state that existing reconciliation tests would catch if run afterward, but this design doesn't add new reconciliation assertions to the E2E layer itself.
