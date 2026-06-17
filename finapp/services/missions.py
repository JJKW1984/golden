"""
Mission service: the mission queue. Progress is always DERIVED from the
linked DebtAccount/SavingsGoal — never stored — so it can never drift from
the ledger (same invariant as services/balances.py).
"""
from datetime import datetime, UTC
from finapp.deps import AccountContext
from finapp.models import Mission, DebtAccount, SavingsGoal
from finapp.services.balances import get_debt_balance_cents, get_savings_goal_balance_cents


def get_mission_progress(ctx: AccountContext, mission: Mission) -> dict:
    """
    Derive {start_cents, current_cents, target_cents, percent} from the
    mission's linked entity.

    Debt missions: start = opening balance, current = derived remaining
    balance, target = 0 (debt-free), percent = % of opening balance paid off.

    Savings/emergency-fund missions: start = opening balance, current =
    derived balance, target = goal target, percent = % of (target - start)
    progress made.
    """
    if mission.link_type == "debt" and mission.link_id:
        debt = ctx.db.query(DebtAccount).filter_by(
            account_id=ctx.account_id, id=mission.link_id
        ).first()
        if not debt:
            return {"start_cents": 0, "current_cents": 0, "target_cents": 0, "percent": 0}

        start = debt.opening_balance_cents
        current = get_debt_balance_cents(ctx, debt.id)
        paid = start - current
        percent = int(100 * paid / start) if start > 0 else 0
        return {
            "start_cents": start,
            "current_cents": current,
            "target_cents": 0,
            "percent": max(0, min(100, percent)),
        }

    if mission.link_type == "savings" and mission.link_id:
        goal = ctx.db.query(SavingsGoal).filter_by(
            account_id=ctx.account_id, id=mission.link_id
        ).first()
        if not goal:
            return {"start_cents": 0, "current_cents": 0, "target_cents": 0, "percent": 0}

        start = goal.opening_balance_cents
        current = get_savings_goal_balance_cents(ctx, goal.id)
        span = goal.target_cents - start
        percent = int(100 * (current - start) / span) if span > 0 else 0
        return {
            "start_cents": start,
            "current_cents": current,
            "target_cents": goal.target_cents,
            "percent": max(0, min(100, percent)),
        }

    return {"start_cents": 0, "current_cents": 0, "target_cents": 0, "percent": 0}


def get_active_missions(ctx: AccountContext) -> list[dict]:
    """Active missions in queue order, each with derived progress."""
    missions = ctx.db.query(Mission).filter_by(
        account_id=ctx.account_id, status="active",
    ).order_by(Mission.sort_order).all()

    result = []
    for m in missions:
        progress = get_mission_progress(ctx, m)
        result.append({
            "id": m.id,
            "name": m.name,
            "mission_type": m.mission_type,
            "link_type": m.link_type,
            "link_id": m.link_id,
            "sort_order": m.sort_order,
            **progress,
        })
    return result


def complete_mission(ctx: AccountContext, mission_id: int) -> Mission:
    """Mark a mission completed. Idempotent on status, raises if not found."""
    mission = ctx.db.query(Mission).filter_by(
        account_id=ctx.account_id, id=mission_id,
    ).first()
    if not mission:
        raise ValueError(f"Mission {mission_id} not found")

    mission.status = "completed"
    mission.completed_at = datetime.now(UTC)
    ctx.db.commit()
    return mission


def reorder_missions(ctx: AccountContext, ordered_ids: list[int]) -> list[Mission]:
    """
    Set sort_order for the given mission ids, in the order given.
    Emergency-fund missions are pinned and cannot be reordered — raises
    ValueError if one is included in ordered_ids.
    """
    missions = ctx.db.query(Mission).filter(
        Mission.account_id == ctx.account_id,
        Mission.id.in_(ordered_ids),
    ).all()
    missions_by_id = {m.id: m for m in missions}

    for mission_id in ordered_ids:
        mission = missions_by_id.get(mission_id)
        if mission and mission.mission_type == "emergency_fund":
            raise ValueError("Emergency fund mission cannot be reordered")

    for i, mission_id in enumerate(ordered_ids, start=1):
        mission = missions_by_id.get(mission_id)
        if mission:
            mission.sort_order = i

    ctx.db.commit()
    return list(missions_by_id.values())
