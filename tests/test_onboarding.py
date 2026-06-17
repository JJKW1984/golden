"""
Unit tests for the onboarding service (Phase 9).

Covers the six-step setup: welcome+income, category confirmation (with the
system-category non-deletion rule), debt setup (no default method),
emergency-fund creation, mission-queue auto-generation, and the
setup_complete flip.
"""
import pytest

from finapp.models import BudgetCategory, DebtAccount, SavingsGoal, Mission, Settings
from finapp.services import onboarding


# --- Step 1 & 2: welcome + income -------------------------------------------

def test_save_settings_creates_settings_with_income(ctx):
    settings = onboarding.save_settings(
        ctx,
        user_name="Sam",
        monthly_income_cents=400000,
        pay_frequency="biweekly",
        pay_day=15,
    )
    assert settings.user_name == "Sam"
    assert settings.monthly_income_cents == 400000
    assert settings.pay_frequency == "biweekly"
    assert settings.pay_day == 15
    assert settings.setup_complete is False


def test_save_settings_is_idempotent_updates_existing(ctx):
    onboarding.save_settings(ctx, user_name="Sam", monthly_income_cents=1, pay_frequency="weekly", pay_day=1)
    onboarding.save_settings(ctx, user_name="Sam2", monthly_income_cents=2, pay_frequency="monthly", pay_day=2)
    rows = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).all()
    assert len(rows) == 1
    assert rows[0].user_name == "Sam2"


# --- Step 3: categories ------------------------------------------------------

def test_get_categories_seeds_defaults_when_empty(ctx):
    cats = onboarding.get_categories(ctx)
    assert len(cats) == 8
    names = {c.name for c in cats}
    assert {"Income", "Housing", "Debt", "Emergency Fund"} <= names


def test_confirm_categories_renames_adds_and_hides(ctx):
    onboarding.get_categories(ctx)  # seed
    food = ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id, name="Food").first()
    personal = ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id, name="Personal").first()

    onboarding.confirm_categories(
        ctx,
        renames={food.id: "Groceries"},
        additions=["Pets", "Hobbies"],
        hidden_ids=[personal.id],
    )

    ctx.db.refresh(food)
    ctx.db.refresh(personal)
    assert food.name == "Groceries"
    assert personal.is_active is False
    new_names = {c.name for c in ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id, is_active=True)}
    assert {"Pets", "Hobbies"} <= new_names


def test_confirm_categories_cannot_hide_system_category(ctx):
    onboarding.get_categories(ctx)
    housing = ctx.db.query(BudgetCategory).filter_by(account_id=ctx.account_id, name="Housing").first()
    with pytest.raises(ValueError):
        onboarding.confirm_categories(ctx, renames={}, additions=[], hidden_ids=[housing.id])
    ctx.db.refresh(housing)
    assert housing.is_active is True


# --- Step 4: debt ------------------------------------------------------------

def test_add_debts_creates_accounts_and_sets_method(ctx):
    debts = onboarding.add_debts(
        ctx,
        debts=[
            {"name": "Card A", "balance_cents": 50000, "interest_rate_bps": 2199, "minimum_payment_cents": 2500},
            {"name": "Card B", "balance_cents": 10000, "interest_rate_bps": 999, "minimum_payment_cents": 1000},
        ],
        method="snowball",
    )
    assert len(debts) == 2
    # snowball: smallest balance first -> Card B sorts before Card A
    by_name = {d.name: d for d in debts}
    assert by_name["Card B"].sort_order < by_name["Card A"].sort_order
    assert by_name["Card A"].cached_balance_cents == 50000
    settings = ctx.db.query(Settings).filter_by(account_id=ctx.account_id).first()
    assert settings.debt_method == "snowball"


def test_add_debts_avalanche_orders_by_rate(ctx):
    debts = onboarding.add_debts(
        ctx,
        debts=[
            {"name": "Low", "balance_cents": 10000, "interest_rate_bps": 500, "minimum_payment_cents": 1000},
            {"name": "High", "balance_cents": 90000, "interest_rate_bps": 2500, "minimum_payment_cents": 2000},
        ],
        method="avalanche",
    )
    by_name = {d.name: d for d in debts}
    assert by_name["High"].sort_order < by_name["Low"].sort_order


def test_add_debts_none_path_creates_nothing(ctx):
    debts = onboarding.add_debts(ctx, debts=[], method=None)
    assert debts == []
    assert ctx.db.query(DebtAccount).count() == 0


# --- Step 5: emergency fund --------------------------------------------------

def test_set_emergency_fund_creates_goal(ctx):
    goal = onboarding.set_emergency_fund(ctx, current_balance_cents=20000, target_cents=100000)
    assert goal.goal_type == "emergency_fund"
    assert goal.opening_balance_cents == 20000
    assert goal.cached_balance_cents == 20000
    assert goal.target_cents == 100000


# --- Step 6: mission queue ---------------------------------------------------

def test_generate_mission_queue_emergency_fund_first_then_debts(ctx):
    onboarding.set_emergency_fund(ctx, current_balance_cents=0, target_cents=50000)
    onboarding.add_debts(
        ctx,
        debts=[
            {"name": "Card A", "balance_cents": 50000, "interest_rate_bps": 2199, "minimum_payment_cents": 2500},
            {"name": "Card B", "balance_cents": 10000, "interest_rate_bps": 999, "minimum_payment_cents": 1000},
        ],
        method="snowball",
    )
    missions = onboarding.generate_mission_queue(ctx)
    assert missions[0].mission_type == "emergency_fund"
    assert missions[0].sort_order == 1
    # Then debts in payoff order: Card B (smaller) before Card A
    debt_missions = [m for m in missions if m.mission_type == "debt_payoff"]
    assert [m.name for m in debt_missions][0].endswith("Card B")


def test_generate_mission_queue_skips_funded_emergency_fund(ctx):
    onboarding.set_emergency_fund(ctx, current_balance_cents=100000, target_cents=50000)
    missions = onboarding.generate_mission_queue(ctx)
    assert all(m.mission_type != "emergency_fund" for m in missions)


# --- Setup complete ----------------------------------------------------------

def test_complete_setup_flips_flag(ctx):
    onboarding.save_settings(ctx, user_name="Sam", monthly_income_cents=1, pay_frequency="weekly", pay_day=1)
    settings = onboarding.complete_setup(ctx)
    assert settings.setup_complete is True


def test_is_setup_complete_reflects_state(ctx):
    assert onboarding.is_setup_complete(ctx) is False
    onboarding.save_settings(ctx, user_name="Sam", monthly_income_cents=1, pay_frequency="weekly", pay_day=1)
    onboarding.complete_setup(ctx)
    assert onboarding.is_setup_complete(ctx) is True
