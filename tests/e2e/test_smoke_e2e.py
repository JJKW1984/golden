def test_health_endpoint_via_browser(page, live_server):
    resp = page.goto(f"{live_server.url}/health")
    assert resp is not None
    assert resp.status == 200
    assert "ok" in page.content()
