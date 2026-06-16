from finapp.models import Milestone


def _seed_milestone(db):
    m = Milestone(
        account_id=1, milestone_type="first_budget", title="Your first budget!",
        threshold=0, celebrated=False,
    )
    db.add(m)
    db.commit()
    return m


def test_get_milestone_detail(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    m = _seed_milestone(db)

    resp = test_client_for_budget.get(f"/milestone/{m.id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Your first budget!"


def test_celebrate_milestone_flips_once(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    m = _seed_milestone(db)

    resp = test_client_for_budget.post(f"/milestone/{m.id}/celebrate", json={"feeling": "Relieved!"})
    assert resp.status_code == 200
    db.refresh(m)
    assert m.celebrated is True


def test_get_uncelebrated_milestones(test_client_for_budget):
    from finapp.main import app
    from finapp.deps import get_db
    db = next(app.dependency_overrides[get_db]())
    _seed_milestone(db)

    resp = test_client_for_budget.get("/api/milestones")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
