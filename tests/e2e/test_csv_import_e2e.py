import csv

from playwright.sync_api import expect

from tests.e2e.conftest import seed_account

XSS_PAYEE = '<img src=x onerror="window.__xss_fired=true">'


def test_csv_preview_escapes_payee(page, tmp_path, live_server):
    seed_account(live_server.db)

    csv_path = tmp_path / "bank.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Amount", "Description"])
        writer.writerow(["2026-06-01", "-12.50", XSS_PAYEE])

    page.goto(f"{live_server.url}/settings")

    page.get_by_test_id("csv-file").set_input_files(str(csv_path))
    page.get_by_test_id("csv-map-date").fill("Date")
    page.get_by_test_id("csv-map-amount").fill("Amount")
    page.get_by_test_id("csv-map-payee").fill("Description")
    page.get_by_test_id("csv-preview-submit").click()

    # Preview panel appears after the async upload resolves.
    expect(page.locator("#import-preview")).to_be_visible()

    # The payee renders as the LITERAL string inside an input value —
    # proving it was HTML-escaped, not parsed as markup.
    payee_input = page.locator("#import-rows .row-payee").first
    assert payee_input.input_value() == XSS_PAYEE

    # No injected <img> element leaked into the rows DOM.
    assert page.locator("#import-rows img").count() == 0

    # The onerror handler never executed.
    assert page.evaluate("window.__xss_fired") in (None, False)
