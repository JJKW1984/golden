"""
Integration test (Phase 9): walk all six onboarding steps through the HTTP
API and confirm the app lands on a usable dashboard with seeded categories,
debts, an emergency fund, and a mission queue — with the reconciliation gate
green throughout (CLAUDE.md non-negotiable Done gate).
"""
from finapp.models import BudgetCategory, DebtAccount, SavingsGoal, Mission, Settings
from finapp.deps import AccountContext
from finapp.services.reconciliation import recompute_balances


def _db():
    from finapp.main import app
    from finapp.deps import get_db
    return next(app.dependency_overrides[get_db]())


def test_full_onboarding_walkthrough(test_client_for_budget):
    client = test_client_for_budget

    # Before setup, root redirects into onboarding.
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"].endswith("/onboarding")

    # Step 1 & 2: welcome + income
    assert client.post("/onboarding/settings", json={
        "user_name": "Sam",
        "monthly_income": "4000.00",
        "pay_frequency": "biweekly",
        "pay_day": 15,
    }).status_code == 200

    # Step 3: categories — seed (via page), rename one, add one, hide a non-system one
    client.get("/onboarding")
    db = _db()
    food = db.query(BudgetCategory).filter_by(account_id=1, name="Food").first()
    personal = db.query(BudgetCategory).filter_by(account_id=1, name="Personal").first()
    assert client.post("/onboarding/categories", json={
        "renames": {str(food.id): "Groceries"},
        "additions": ["Pets"],
        "hidden_ids": [personal.id],
    }).status_code == 200

    # Step 4: debt — two accounts, snowball
    assert client.post("/onboarding/debt", json={
        "has_debt": "yes",
        "method": "snowball",
        "debts": [
            {"name": "Card A", "balance": "500.00", "interest_rate": "21.99", "minimum_payment": "25.00"},
            {"name": "Card B", "balance": "100.00", "interest_rate": "9.99", "minimum_payment": "10.00"},
        ],
    }).status_code == 200

    # Step 5: emergency fund under target
    assert client.post("/onboarding/savings", json={
        "has_savings": True,
        "current_balance": "100.00",
        "target": "1000.00",
    }).status_code == 200

    # Step 6: generate mission queue + complete setup
    assert client.post("/onboarding/missions", json={}).status_code == 200

    # --- Assertions on the resulting state ---
    db = _db()
    settings = db.query(Settings).filter_by(account_id=1).first()
    assert settings.setup_complete is True
    assert settings.debt_method == "snowball"

    # Seeded categories present, rename + hide applied
    assert db.query(BudgetCategory).filter_by(account_id=1, name="Groceries").first() is not None
    db.refresh(personal)
    assert personal.is_active is False

    # Debts seeded; snowball order (smaller balance first)
    debts = db.query(DebtAccount).filter_by(account_id=1).order_by(DebtAccount.sort_order).all()
    assert [d.name for d in debts] == ["Card B", "Card A"]

    # Emergency fund created
    ef = db.query(SavingsGoal).filter_by(account_id=1, goal_type="emergency_fund").first()
    assert ef.target_cents == 100000

    # Mission queue: emergency fund first, then debts
    missions = db.query(Mission).filter_by(account_id=1, status="active").order_by(Mission.sort_order).all()
    assert missions[0].mission_type == "emergency_fund"
    assert {m.mission_type for m in missions} == {"emergency_fund", "debt_payoff"}

    # Root now lands on the dashboard, which renders.
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200

    # Reconciliation gate green.
    report = recompute_balances(db, 1)
    assert report["total_drift"] == 0
