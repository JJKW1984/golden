from playwright.sync_api import expect


def test_full_onboarding_lands_on_dashboard(page, live_server):
    page.goto(f"{live_server.url}/onboarding")

    # Step 1: Welcome — name, then advance (client-side step change).
    expect(page.get_by_test_id("onboarding-step-1")).to_be_visible()
    page.get_by_test_id("onboarding-name").fill("Alex")
    page.get_by_test_id("onboarding-get-started").click()

    # Step 2: Income — fill, choose frequency + pay day, POST /onboarding/settings.
    expect(page.get_by_test_id("onboarding-step-2")).to_be_visible()
    page.get_by_test_id("onboarding-income").fill("4000.00")
    page.get_by_test_id("onboarding-pay-frequency").select_option("monthly")
    page.get_by_test_id("onboarding-pay-day").fill("1")
    page.get_by_test_id("onboarding-income-continue").click()

    # Step 3: Categories — accept defaults, POST /onboarding/categories.
    expect(page.get_by_test_id("onboarding-step-3")).to_be_visible()
    page.get_by_test_id("onboarding-categories-continue").click()

    # Step 4: Debt — default "no", POST /onboarding/debt.
    expect(page.get_by_test_id("onboarding-step-4")).to_be_visible()
    page.get_by_test_id("onboarding-debt-continue").click()

    # Step 5: Savings — accept default target, POST /onboarding/savings.
    expect(page.get_by_test_id("onboarding-step-5")).to_be_visible()
    page.get_by_test_id("onboarding-savings-continue").click()

    # Step 6: Mission queue — finish, POST /onboarding/missions, redirect to dashboard.
    expect(page.get_by_test_id("onboarding-step-6")).to_be_visible()
    page.get_by_test_id("onboarding-finish").click()

    expect(page).to_have_url(f"{live_server.url}/dashboard")
