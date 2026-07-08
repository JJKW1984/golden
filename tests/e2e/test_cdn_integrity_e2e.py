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
