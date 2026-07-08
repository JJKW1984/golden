from finapp.models import DebtAccount
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
