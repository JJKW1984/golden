from tests.e2e.conftest import seed_account


def test_health_endpoint_via_browser(page, live_server):
    resp = page.goto(f"{live_server.url}/health")
    assert resp is not None
    assert resp.status == 200
    assert "ok" in page.content()


def test_seeded_settings_render_in_browser(page, live_server):
    seed_account(live_server.db, user_name="Seeded Sam")

    page.goto(f"{live_server.url}/settings")

    name_input = page.locator('input[name="user_name"]')
    assert name_input.input_value() == "Seeded Sam"
