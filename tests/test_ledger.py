"""
Unit tests for the ledger service (single write path for transactions).
TDD: Tests define the expected behavior before implementation.
"""
import pytest
from datetime import date
from finapp.models import (
    BudgetPeriod,
    BudgetCategory,
    Transaction,
    DebtAccount,
    SavingsGoal,
)
from finapp.services.seeds import seed_default_categories
from finapp.services.ledger import (
    create_transaction,
    edit_transaction,
    void_transaction,
    restore_transaction,
    compute_import_hash,
)


@pytest.fixture
def setup_account(db, account, ctx):
    """Set up account with categories and budget period."""
    seed_default_categories(db, account.id)

    period = BudgetPeriod(
        account_id=account.id,
        year=2026,
        month=6,
        income_received_cents=0,
        status="active",
    )
    db.add(period)
    db.commit()

    return account, period


class TestCreateTransaction:
    """Test transaction creation via the single write path."""

    def test_create_income_transaction(self, db, ctx, setup_account):
        """Create an income transaction and verify period assignment."""
        account, period = setup_account
        income_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, kind="income"
        ).first()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=100000,  # $1000
            direction="in",
            category_id=income_cat.id,
            payee="Employer",
        )

        assert txn.id is not None
        assert txn.amount_cents == 100000
        assert txn.direction == "in"
        assert txn.period_id == period.id  # Auto-assigned from date
        assert txn.is_deleted == False

    def test_create_expense_transaction(self, db, ctx, setup_account):
        """Create an expense transaction."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=5000,  # $50
            direction="out",
            category_id=food_cat.id,
            payee="Grocery Store",
            memo="Weekly groceries",
        )

        assert txn.amount_cents == 5000
        assert txn.direction == "out"
        assert txn.payee == "Grocery Store"
        assert txn.memo == "Weekly groceries"

    def test_create_debt_payment(self, db, ctx, setup_account):
        """Create a debt payment transaction with principal/interest split."""
        account, period = setup_account

        debt = DebtAccount(
            account_id=account.id,
            name="Credit Card",
            opening_balance_cents=50000,
            cached_balance_cents=50000,
            interest_rate_bps=2200,  # 22%
            minimum_payment_cents=5000,
        )
        db.add(debt)
        db.commit()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=10000,  # $100 total payment
            direction="out",
            category_id=None,
            link_type="debt",
            link_id=debt.id,
            principal_cents=9000,  # $90 principal
            interest_cents=1000,   # $10 interest
        )

        assert txn.principal_cents == 9000
        assert txn.interest_cents == 1000
        assert txn.link_type == "debt"
        assert txn.link_id == debt.id

    def test_create_savings_contribution(self, db, ctx, setup_account):
        """Create a savings contribution transaction."""
        account, period = setup_account

        goal = SavingsGoal(
            account_id=account.id,
            name="Emergency Fund",
            goal_type="emergency_fund",
            target_cents=100000,
            opening_balance_cents=0,
            cached_balance_cents=0,
        )
        db.add(goal)
        db.commit()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=20000,  # $200 to savings
            direction="out",
            category_id=None,
            link_type="savings",
            link_id=goal.id,
        )

        assert txn.amount_cents == 20000
        assert txn.direction == "out"
        assert txn.link_type == "savings"

    def test_period_assignment_from_date(self, db, ctx, setup_account):
        """Test that transaction period is derived from date."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        # June transaction
        txn_june = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=1000,
            direction="out",
            category_id=food_cat.id,
        )
        assert txn_june.period_id == period.id

        # Create July period
        period_july = BudgetPeriod(
            account_id=account.id,
            year=2026,
            month=7,
            income_received_cents=0,
            status="active",
        )
        db.add(period_july)
        db.commit()

        # July transaction
        txn_july = create_transaction(
            ctx,
            date=date(2026, 7, 15),
            amount_cents=2000,
            direction="out",
            category_id=food_cat.id,
        )
        assert txn_july.period_id == period_july.id

    def test_dedup_hash_computation(self, db, ctx, setup_account):
        """Test that import_hash is computed correctly."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=5000,
            direction="out",
            category_id=food_cat.id,
            payee="Store",
            is_imported=True,
        )

        # Hash should be computed from date, amount, payee
        expected_hash = compute_import_hash(
            date(2026, 6, 15), 5000, "Store"
        )
        assert txn.import_hash == expected_hash


class TestEditTransaction:
    """Test transaction editing."""

    def test_edit_amount(self, db, ctx, setup_account):
        """Edit transaction amount."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=5000,
            direction="out",
            category_id=food_cat.id,
        )

        txn_updated = edit_transaction(
            ctx,
            txn.id,
            amount_cents=6000,
        )

        assert txn_updated.amount_cents == 6000

    def test_edit_date_updates_period(self, db, ctx, setup_account):
        """Edit transaction date, period should be re-derived."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=5000,
            direction="out",
            category_id=food_cat.id,
        )

        # Create July period
        period_july = BudgetPeriod(
            account_id=account.id,
            year=2026,
            month=7,
            income_received_cents=0,
            status="active",
        )
        db.add(period_july)
        db.commit()

        # Move transaction to July
        txn_updated = edit_transaction(
            ctx,
            txn.id,
            date=date(2026, 7, 20),
        )

        assert txn_updated.date == date(2026, 7, 20)
        assert txn_updated.period_id == period_july.id

    def test_edit_category(self, db, ctx, setup_account):
        """Edit transaction category."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()
        transport_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Transportation"
        ).first()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=5000,
            direction="out",
            category_id=food_cat.id,
        )

        txn_updated = edit_transaction(
            ctx,
            txn.id,
            category_id=transport_cat.id,
        )

        assert txn_updated.category_id == transport_cat.id


class TestSoftDelete:
    """Test soft delete and restore."""

    def test_void_transaction(self, db, ctx, setup_account):
        """Soft-delete a transaction."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=5000,
            direction="out",
            category_id=food_cat.id,
        )

        void_transaction(ctx, txn.id)

        deleted_txn = db.query(Transaction).filter_by(id=txn.id).first()
        assert deleted_txn.is_deleted == True

    def test_restore_transaction(self, db, ctx, setup_account):
        """Restore a soft-deleted transaction."""
        account, period = setup_account
        food_cat = db.query(BudgetCategory).filter_by(
            account_id=account.id, name="Food"
        ).first()

        txn = create_transaction(
            ctx,
            date=date(2026, 6, 15),
            amount_cents=5000,
            direction="out",
            category_id=food_cat.id,
        )

        void_transaction(ctx, txn.id)
        assert db.query(Transaction).filter_by(id=txn.id).first().is_deleted == True

        restore_transaction(ctx, txn.id)
        restored = db.query(Transaction).filter_by(id=txn.id).first()
        assert restored.is_deleted == False


class TestImportHashDedup:
    """Test dedup hash for import detection."""

    def test_import_hash_consistency(self):
        """Test that import_hash is computed consistently."""
        hash1 = compute_import_hash(date(2026, 6, 15), 5000, "Store")
        hash2 = compute_import_hash(date(2026, 6, 15), 5000, "Store")

        assert hash1 == hash2

    def test_import_hash_different_for_different_values(self):
        """Different values produce different hashes."""
        hash1 = compute_import_hash(date(2026, 6, 15), 5000, "Store A")
        hash2 = compute_import_hash(date(2026, 6, 15), 5000, "Store B")
        hash3 = compute_import_hash(date(2026, 6, 15), 6000, "Store A")

        assert hash1 != hash2
        assert hash1 != hash3

    def test_import_hash_case_insensitive(self):
        """Import hash should be case-insensitive."""
        hash1 = compute_import_hash(date(2026, 6, 15), 5000, "STORE")
        hash2 = compute_import_hash(date(2026, 6, 15), 5000, "store")

        assert hash1 == hash2
