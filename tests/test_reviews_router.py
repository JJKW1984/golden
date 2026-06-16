import sqlite3
from datetime import date
from finapp.models import Settings, BudgetPeriod


def _set_review_day(db):
    settings = Settings(account_id=1, review_day=date.today().strftime("%A").lower())
    db.add(settings)
    db.commit()


def test_get_weekly_review_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    _set_review_day(db)

    resp = test_client_for_budget.get("/reviews/weekly")
    assert resp.status_code == 200


def test_post_weekly_review_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    _set_review_day(db)
    test_client_for_budget.get("/reviews/weekly")  # ensure pending row exists

    resp = test_client_for_budget.post(
        "/reviews/weekly",
        json={"intention": "Spend less on eating out.", "quick": False},
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Spend less on eating out."


def test_post_monthly_review_endpoint(test_client_for_budget, tmp_path, monkeypatch):
    monkeypatch.setattr("finapp.services.reviews.BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("finapp.services.reviews.SOURCE_DB_PATH", str(tmp_path / "finance.db"))
    sqlite3.connect(str(tmp_path / "finance.db")).close()

    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    period = BudgetPeriod(account_id=1, year=2026, month=5, status="active")
    db.add(period)
    db.commit()

    resp = test_client_for_budget.post("/reviews/monthly", json={
        "period_id": period.id, "notes": "Good month.", "sweep_to_mission": False,
    })
    assert resp.status_code == 200
    assert resp.json()["period_id"] == period.id


def test_post_start_month_endpoint(test_client_for_budget):
    resp = test_client_for_budget.post("/reviews/start-month")
    assert resp.status_code == 200
    assert resp.json()["month"] == date.today().month
