"""
Router tests for the onboarding flow (Phase 9): the six POST steps, the
GET /onboarding page, and the GET / redirect guard.
"""
from finapp.models import Settings, BudgetCategory, DebtAccount, SavingsGoal, Mission


def _db():
    from finapp.main import app
    from finapp.deps import get_db
    return next(app.dependency_overrides[get_db]())


def test_get_onboarding_page_renders(test_client_for_budget):
    resp = test_client_for_budget.get("/onboarding")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_root_redirects_to_onboarding_when_incomplete(test_client_for_budget):
    resp = test_client_for_budget.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].endswith("/onboarding")


def test_root_redirects_to_dashboard_when_complete(test_client_for_budget):
    db = _db()
    db.add(Settings(account_id=1, setup_complete=True))
    db.commit()
    resp = test_client_for_budget.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].endswith("/dashboard")


def test_post_settings_converts_money_to_cents(test_client_for_budget):
    resp = test_client_for_budget.post("/onboarding/settings", json={
        "user_name": "Sam",
        "monthly_income": "4000.00",
        "pay_frequency": "biweekly",
        "pay_day": 15,
    })
    assert resp.status_code == 200
    db = _db()
    settings = db.query(Settings).filter_by(account_id=1).first()
    assert settings.monthly_income_cents == 400000
    assert settings.user_name == "Sam"


def test_post_categories_applies_edits(test_client_for_budget):
    test_client_for_budget.get("/onboarding")  # seed via page load
    db = _db()
    food = db.query(BudgetCategory).filter_by(account_id=1, name="Food").first()
    resp = test_client_for_budget.post("/onboarding/categories", json={
        "renames": {str(food.id): "Groceries"},
        "additions": ["Pets"],
        "hidden_ids": [],
    })
    assert resp.status_code == 200
    db.refresh(food)
    assert food.name == "Groceries"


def test_post_debt_creates_accounts_and_records_method(test_client_for_budget):
    resp = test_client_for_budget.post("/onboarding/debt", json={
        "has_debt": "yes",
        "method": "avalanche",
        "debts": [
            {"name": "Card A", "balance": "500.00", "interest_rate": "21.99", "minimum_payment": "25.00"},
        ],
    })
    assert resp.status_code == 200
    db = _db()
    debt = db.query(DebtAccount).filter_by(account_id=1).first()
    assert debt.opening_balance_cents == 50000
    assert debt.interest_rate_bps == 2199
    assert debt.minimum_payment_cents == 2500
    settings = db.query(Settings).filter_by(account_id=1).first()
    assert settings.debt_method == "avalanche"


def test_post_savings_creates_emergency_fund(test_client_for_budget):
    resp = test_client_for_budget.post("/onboarding/savings", json={
        "has_savings": True,
        "current_balance": "200.00",
        "target": "1000.00",
    })
    assert resp.status_code == 200
    db = _db()
    goal = db.query(SavingsGoal).filter_by(account_id=1, goal_type="emergency_fund").first()
    assert goal.opening_balance_cents == 20000
    assert goal.target_cents == 100000


def test_post_missions_generates_queue_and_completes_setup(test_client_for_budget):
    # set up an emergency fund under target so a mission is generated
    test_client_for_budget.post("/onboarding/savings", json={
        "has_savings": False, "current_balance": "0", "target": "1000.00",
    })
    resp = test_client_for_budget.post("/onboarding/missions", json={})
    assert resp.status_code == 200
    db = _db()
    assert db.query(Mission).filter_by(account_id=1, status="active").count() >= 1
    settings = db.query(Settings).filter_by(account_id=1).first()
    assert settings.setup_complete is True
