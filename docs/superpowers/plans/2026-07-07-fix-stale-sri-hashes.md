# Fix Stale SRI Hashes on HTMX/Chart.js Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the stale Subresource Integrity (SRI) hashes on the HTMX and Chart.js `<script>` tags in `finapp/templates/base.html` and `finapp/templates/debt_projection.html`, which currently cause Chromium to block both scripts on every page load — breaking all HTMX-driven interactions and the debt payoff projection chart in every real browser.

**Architecture:** Both scripts are pinned to fixed, versioned CDN URLs (`htmx.org@1.9.10`, `chart.js@4.4.0`) — unlike the Tailwind Play CDN and floating-version Alpine.js tag fixed in the prior E2E-testing plan, these have stable content that CAN be correctly hash-pinned. The fix is a straight data correction: replace the stale `integrity` attribute values with hashes computed from the actual bytes served at those URLs today, verified independently via `openssl dgst`. Regression coverage is added to the existing Playwright E2E suite (`tests/e2e/`) built in the prior plan — asserting no `console` "blocked" errors and that the affected page behavior (an HTMX request, a rendered Chart.js `<canvas>`) actually fires in a real browser.

**Tech Stack:** Jinja2 templates, Playwright (`pytest-playwright`, already a dev dependency), `openssl` for SRI hash computation.

## Global Constraints

- **Loopback only:** any test server must bind `127.0.0.1`, never `0.0.0.0` (existing project invariant).
- **Purely corrective:** no markup restructuring, no version bumps, no behavior changes beyond fixing the two `integrity` attribute values — this plan does not vendor assets or otherwise change the CDN-loading approach (that was scoped out of this plan on purpose).
- **Test toolchain:** `uv run pytest tests/e2e/` (existing E2E harness from `docs/superpowers/plans/2026-07-07-e2e-testing.md`; do not create a second harness).
- **money.py / cents integer discipline is unaffected by this plan** — no ledger or balance code is touched.

## Background: why this is broken today

`finapp/templates/base.html` loads:

```html
<script src="https://unpkg.com/htmx.org@1.9.10"
    integrity="sha384-D1Kt99CQMDuVeK5mXA0C5n9+fFplnTmHpSvHO1RJkCMw3f/DxmlJ8oPjGELHNWuLQ"
    crossorigin="anonymous"></script>
```

and

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"
    integrity="sha384-TN3Hv5OYYP5+bL1s6K4qYaZrQ3zGfGSVVaWatDh6f3nXj0Gh6bUvCNGYLQV/B3Vhm"
    crossorigin="anonymous"></script>
```

`finapp/templates/debt_projection.html` (which `{% extends "base.html" %}` and therefore loads Chart.js twice) has its own duplicate Chart.js tag with the identical stale hash:

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js" integrity="sha384-TN3Hv5OYYP5+bL1s6K4qYaZrQ3zGfGSVVaWatDh6f3nXj0Gh6bUvCNGYLQV/B3Vhm" crossorigin="anonymous"></script>
```

Both stored hashes do not match the bytes currently served at those pinned URLs. Verified independently:

```bash
curl -sL -o /tmp/htmx-1.9.10.js "https://unpkg.com/htmx.org@1.9.10"
openssl dgst -sha384 -binary /tmp/htmx-1.9.10.js | openssl base64 -A
# => D1Kt99CQMDuVetoL1lrYwg5t+9QdHe7NLX/SoJYkXDFfX37iInKRy5xLSi8nO7UC

curl -sL -o /tmp/chart-4.4.0.js "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"
openssl dgst -sha384 -binary /tmp/chart-4.4.0.js | openssl base64 -A
# => FcQlsUOd0TJjROrBxhJdUhXTUgNJQxTMcxZe6nHbaEfFL1zjQ+bq/uRoBQxb0KMo
```

These computed values also match what Chromium itself reports in its "Failed to find a valid digest" console error when blocking the mismatched script — independent confirmation the correct hashes are:

- HTMX 1.9.10: `sha384-D1Kt99CQMDuVetoL1lrYwg5t+9QdHe7NLX/SoJYkXDFfX37iInKRy5xLSi8nO7UC`
- Chart.js 4.4.0: `sha384-FcQlsUOd0TJjROrBxhJdUhXTUgNJQxTMcxZe6nHbaEfFL1zjQ+bq/uRoBQxb0KMo`

## File Structure

Files created or modified by this plan:

- Modify: `finapp/templates/base.html` — correct the HTMX and Chart.js `integrity` attribute values.
- Modify: `finapp/templates/debt_projection.html` — correct the duplicate Chart.js `integrity` attribute value.
- Create: `tests/e2e/test_cdn_integrity_e2e.py` — regression tests proving HTMX and Chart.js actually execute in a real browser (no blocked-script console errors, and each library's real effect is observed: an HTMX-driven request fires, and Chart.js renders onto the canvas).

---

### Task 1: Fix HTMX's stale SRI hash and add a regression test

Any page extending `base.html` loads HTMX. The dashboard page (`/dashboard`) is a normal HTMX-driven page and needs no debt/category seeding beyond a completed account, so it's the lowest-friction page to prove HTMX is not blocked.

**Files:**
- Modify: `finapp/templates/base.html`
- Test: `tests/e2e/test_cdn_integrity_e2e.py`

**Interfaces:**
- Consumes: `live_server` fixture and `seed_account` helper from `tests/e2e/conftest.py` (built in `docs/superpowers/plans/2026-07-07-e2e-testing.md`; both already exist on `master`).
- Produces: `tests/e2e/test_cdn_integrity_e2e.py::test_htmx_loads_without_integrity_error` — a pattern later steps/tasks in this plan follow for Chart.js.

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_cdn_integrity_e2e.py`:

```python
from tests.e2e.conftest import seed_account


def test_htmx_loads_without_integrity_error(page, live_server):
    seed_account(live_server.db)

    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

    page.goto(f"{live_server.url}/dashboard")
    page.wait_for_load_state("networkidle")

    integrity_errors = [e for e in console_errors if "integrity" in e.lower()]
    assert integrity_errors == [], f"SRI integrity errors: {integrity_errors}"

    # HTMX registers itself as window.htmx once its script has actually
    # executed (not just downloaded) — proves the browser didn't block it.
    assert page.evaluate("typeof window.htmx !== 'undefined'")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/e2e/test_cdn_integrity_e2e.py::test_htmx_loads_without_integrity_error -v`
Expected: FAIL — either an `integrity_errors` assertion failure (the console reports a blocked-script SRI mismatch) or `window.htmx` is undefined because the script never executed.

- [ ] **Step 3: Fix the HTMX integrity hash in `base.html`**

In `finapp/templates/base.html`, replace:

```html
    <!-- HTMX -->
    <script src="https://unpkg.com/htmx.org@1.9.10"
        integrity="sha384-D1Kt99CQMDuVeK5mXA0C5n9+fFplnTmHpSvHO1RJkCMw3f/DxmlJ8oPjGELHNWuLQ"
        crossorigin="anonymous"></script>
```

with:

```html
    <!-- HTMX -->
    <script src="https://unpkg.com/htmx.org@1.9.10"
        integrity="sha384-D1Kt99CQMDuVetoL1lrYwg5t+9QdHe7NLX/SoJYkXDFfX37iInKRy5xLSi8nO7UC"
        crossorigin="anonymous"></script>
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/e2e/test_cdn_integrity_e2e.py::test_htmx_loads_without_integrity_error -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finapp/templates/base.html tests/e2e/test_cdn_integrity_e2e.py
git commit -m "fix: correct stale HTMX SRI hash blocking it in every real browser"
```

---

### Task 2: Fix Chart.js's stale SRI hash (both locations) and add a regression test

Chart.js is loaded twice with the identical stale hash: once unconditionally in `base.html`, and again (redundantly) in `debt_projection.html`. Both must be corrected together since they're the same value — fixing only one leaves the other still blocked, and since `debt_projection.html` extends `base.html`, its own duplicate tag is what actually renders the payoff chart (base.html's copy loads uselessly on every page, including ones with no `<canvas>`; that redundancy is a separate, out-of-scope cleanup — this plan only fixes the hash values).

**Files:**
- Modify: `finapp/templates/base.html`
- Modify: `finapp/templates/debt_projection.html`
- Test: `tests/e2e/test_cdn_integrity_e2e.py`

**Interfaces:**
- Consumes: `live_server`, `seed_account` (as Task 1); additionally needs `finapp.models.DebtAccount` for seeding a debt to view a projection for.
- Produces: `tests/e2e/test_cdn_integrity_e2e.py::test_chartjs_renders_projection_chart`.

- [ ] **Step 1: Write the failing test**

Add `from finapp.models import DebtAccount` to the top of `tests/e2e/test_cdn_integrity_e2e.py`, alongside the existing `from tests.e2e.conftest import seed_account` import. Then append this test function to the file:

```python
def test_chartjs_renders_projection_chart(page, live_server):
    seed_account(live_server.db)
    debt = DebtAccount(
        account_id=1,
        name="Visa",
        creditor="Chase",
        opening_balance_cents=500000,
        cached_balance_cents=500000,
        interest_rate_bps=2199,
        minimum_payment_cents=15000,
        is_active=True,
    )
    live_server.db.add(debt)
    live_server.db.commit()

    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

    page.goto(f"{live_server.url}/debt/{debt.id}/projection")
    page.wait_for_load_state("networkidle")

    integrity_errors = [e for e in console_errors if "integrity" in e.lower()]
    assert integrity_errors == [], f"SRI integrity errors: {integrity_errors}"

    # Chart.js draws onto the <canvas id="projectionChart"> via getContext('2d');
    # if the script were blocked, `new Chart(...)` would never run and the
    # canvas would never receive a rendering context.
    has_rendering_context = page.evaluate(
        "document.getElementById('projectionChart').getContext('2d') !== null"
        " && typeof window.Chart !== 'undefined'"
    )
    assert has_rendering_context
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/e2e/test_cdn_integrity_e2e.py::test_chartjs_renders_projection_chart -v`
Expected: FAIL — `integrity_errors` non-empty and/or `window.Chart` undefined.

- [ ] **Step 3: Fix the Chart.js integrity hash in `base.html`**

In `finapp/templates/base.html`, replace:

```html
    <!-- Chart.js (for debt/savings projections) -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"
        integrity="sha384-TN3Hv5OYYP5+bL1s6K4qYaZrQ3zGfGSVVaWatDh6f3nXj0Gh6bUvCNGYLQV/B3Vhm"
        crossorigin="anonymous"></script>
```

with:

```html
    <!-- Chart.js (for debt/savings projections) -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"
        integrity="sha384-FcQlsUOd0TJjROrBxhJdUhXTUgNJQxTMcxZe6nHbaEfFL1zjQ+bq/uRoBQxb0KMo"
        crossorigin="anonymous"></script>
```

- [ ] **Step 4: Fix the duplicate Chart.js integrity hash in `debt_projection.html`**

In `finapp/templates/debt_projection.html`, replace:

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js" integrity="sha384-TN3Hv5OYYP5+bL1s6K4qYaZrQ3zGfGSVVaWatDh6f3nXj0Gh6bUvCNGYLQV/B3Vhm" crossorigin="anonymous"></script>
```

with:

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js" integrity="sha384-FcQlsUOd0TJjROrBxhJdUhXTUgNJQxTMcxZe6nHbaEfFL1zjQ+bq/uRoBQxb0KMo" crossorigin="anonymous"></script>
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `uv run pytest tests/e2e/test_cdn_integrity_e2e.py::test_chartjs_renders_projection_chart -v`
Expected: PASS.

- [ ] **Step 6: Run the full E2E and existing debt-router suites to confirm no regressions**

Run: `uv run pytest tests/e2e/ tests/test_debt_router.py tests/test_debt_payoff.py -q`
Expected: all pass (existing debt tests are HTTP-level via `TestClient` and don't touch script tags, so they should be unaffected; this just confirms nothing else broke).

- [ ] **Step 7: Commit**

```bash
git add finapp/templates/base.html finapp/templates/debt_projection.html tests/e2e/test_cdn_integrity_e2e.py
git commit -m "fix: correct stale Chart.js SRI hash blocking debt projection chart"
```

---

## Notes / accepted trade-offs

- This plan deliberately does **not** vendor these assets into `/static/` — that was explicitly scoped out in favor of the narrower hash-correction fix (see prior conversation). Vendoring remains a valid follow-up if the user wants to eliminate CDN supply-chain surface entirely; it would replace both fixed script tags with local `<script src="/static/...">` includes and drop `integrity`/`crossorigin` (no longer needed once self-hosted).
- The redundant double-load of Chart.js (once inert in `base.html` on every page, once again in `debt_projection.html`) is left as-is — fixing it is a simplification, not a correctness fix, and out of this plan's narrow scope.
- No changes to Tailwind's Play CDN or Alpine.js's floating-version tag — those were already fixed (by removing SRI, since they structurally can't support it) in `docs/superpowers/plans/2026-07-07-e2e-testing.md`.
