from datetime import date
from finapp.models import DebtAccount, Mission
from finapp.deps import AccountContext
from finapp.services.ledger import create_transaction


def _seed_debt_mission(db):
    debt = DebtAccount(
        account_id=1, name="Capital One", opening_balance_cents=10000,
        cached_balance_cents=10000, interest_rate_bps=0, minimum_payment_cents=1000,
    )
    db.add(debt)
    db.commit()
    # Pay 4000 principal so derived balance = 6000
    ctx = AccountContext(account_id=1, db=db)
    create_transaction(
        ctx, date=date.today(), amount_cents=4000, direction="out",
        link_type="debt", link_id=debt.id, principal_cents=4000, interest_cents=0,
    )
    mission = Mission(
        account_id=1, name="Pay off Capital One", mission_type="debt_payoff",
        link_type="debt", link_id=debt.id, status="active", sort_order=1,
    )
    db.add(mission)
    db.commit()
    return mission


def test_get_active_missions_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    _seed_debt_mission(db)

    resp = test_client_for_budget.get("/api/missions/active")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["current_cents"] == 6000


def test_complete_mission_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    mission = _seed_debt_mission(db)

    resp = test_client_for_budget.post(f"/missions/{mission.id}/complete")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_reorder_missions_endpoint(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    mission = _seed_debt_mission(db)

    resp = test_client_for_budget.post("/missions/reorder", json={"ordered_ids": [mission.id]})
    assert resp.status_code == 200
