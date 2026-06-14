from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Date, UniqueConstraint
from sqlalchemy.orm import relationship

from finapp.db import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    display_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    settings = relationship("Settings", back_populates="account", uselist=False)
    budget_categories = relationship("BudgetCategory", back_populates="account", cascade="all, delete-orphan")
    budget_periods = relationship("BudgetPeriod", back_populates="account", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan")
    debt_accounts = relationship("DebtAccount", back_populates="account", cascade="all, delete-orphan")
    savings_goals = relationship("SavingsGoal", back_populates="account", cascade="all, delete-orphan")
    missions = relationship("Mission", back_populates="account", cascade="all, delete-orphan")
    check_ins = relationship("CheckIn", back_populates="account", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="account", cascade="all, delete-orphan")
    milestones = relationship("Milestone", back_populates="account", cascade="all, delete-orphan")
    asset_accounts = relationship("AssetAccount", back_populates="account", cascade="all, delete-orphan")


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True)
    setup_complete = Column(Boolean, nullable=False, default=False)
    user_name = Column(String(255))
    monthly_income_cents = Column(Integer)  # Expected; informational
    pay_frequency = Column(String(50))  # 'monthly', 'biweekly', 'weekly'
    pay_day = Column(Integer)  # Day of month, or weekday
    debt_method = Column(String(50))  # 'snowball' | 'avalanche'
    hourly_wage_cents = Column(Integer, nullable=True)  # Time-cost display (disabled v1)
    review_day = Column(String(50))  # 'sunday', 'monday', ...
    check_in_anchor = Column(String(255))  # User-described habit anchor
    currency_symbol = Column(String(10), nullable=False, default="$")
    csv_column_map = Column(Text, nullable=True)  # JSON: remembered bank column mapping
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account", back_populates="settings")


class BudgetCategory(Base):
    __tablename__ = "budget_categories"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String(255), nullable=False)
    emoji = Column(String(10), nullable=True)
    sort_order = Column(Integer)
    kind = Column(String(50), nullable=False, default="spending")  # 'spending' | 'income' | 'debt' | 'savings'
    is_system = Column(Boolean, nullable=False, default=False)  # Locked defaults
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account", back_populates="budget_categories")
    budget_allocations = relationship("BudgetAllocation", back_populates="category", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="category")


class BudgetPeriod(Base):
    __tablename__ = "budget_periods"
    __table_args__ = (UniqueConstraint("account_id", "year", "month", name="uq_account_year_month"),)

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)  # 1-12
    income_received_cents = Column(Integer, nullable=False, default=0)  # CACHED derived value
    status = Column(String(50), nullable=False, default="active")  # 'active' | 'closed'
    notes = Column(Text, nullable=True)  # Monthly reflection
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account", back_populates="budget_periods")
    budget_allocations = relationship("BudgetAllocation", back_populates="period", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="period")
    reviews = relationship("Review", back_populates="period")


class BudgetAllocation(Base):
    __tablename__ = "budget_allocations"
    __table_args__ = (UniqueConstraint("period_id", "category_id", name="uq_period_category"),)

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    period_id = Column(Integer, ForeignKey("budget_periods.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("budget_categories.id"), nullable=False)
    target_cents = Column(Integer, nullable=False, default=0)  # Planned amount for the month
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account")
    period = relationship("BudgetPeriod", back_populates="budget_allocations")
    category = relationship("BudgetCategory", back_populates="budget_allocations")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    period_id = Column(Integer, ForeignKey("budget_periods.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount_cents = Column(Integer, nullable=False)  # Magnitude, always >= 0
    direction = Column(String(50), nullable=False)  # 'in' | 'out'
    category_id = Column(Integer, ForeignKey("budget_categories.id"), nullable=True)
    payee = Column(String(255), nullable=True)
    memo = Column(Text, nullable=True)
    mood_tag = Column(String(50), nullable=True)  # 'planned','impulse','stress','celebration','necessity'
    link_type = Column(String(50), nullable=True)  # NULL | 'debt' | 'savings'
    link_id = Column(Integer, nullable=True)  # FK to DebtAccount or SavingsGoal
    principal_cents = Column(Integer, nullable=True)  # For debt payments: principal portion
    interest_cents = Column(Integer, nullable=True)  # For debt payments: interest portion
    is_imported = Column(Boolean, nullable=False, default=False)
    import_hash = Column(String(255), nullable=True)  # Dedup key
    is_deleted = Column(Boolean, nullable=False, default=False)  # Soft delete
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account", back_populates="transactions")
    period = relationship("BudgetPeriod", back_populates="transactions")
    category = relationship("BudgetCategory", back_populates="transactions")


class DebtAccount(Base):
    __tablename__ = "debt_accounts"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String(255), nullable=False)
    creditor = Column(String(255), nullable=True)
    opening_balance_cents = Column(Integer, nullable=False)  # Balance when added (immutable anchor)
    cached_balance_cents = Column(Integer, nullable=False)  # DERIVED: opening - sum(principal paid)
    interest_rate_bps = Column(Integer, nullable=False)  # Basis points (e.g., 2199 = 21.99%)
    minimum_payment_cents = Column(Integer, nullable=False)
    sort_order = Column(Integer)  # Auto-set by debt method
    is_active = Column(Boolean, nullable=False, default=True)
    paid_off_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account", back_populates="debt_accounts")


class SavingsGoal(Base):
    __tablename__ = "savings_goals"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String(255), nullable=False)
    goal_type = Column(String(50), nullable=False)  # 'emergency_fund' | 'sinking_fund' | 'mission'
    target_cents = Column(Integer, nullable=False)
    opening_balance_cents = Column(Integer, nullable=False, default=0)  # Balance when added (immutable anchor)
    cached_balance_cents = Column(Integer, nullable=False, default=0)  # DERIVED: opening + sum(contributions) - withdrawals
    target_date = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_complete = Column(Boolean, nullable=False, default=False)  # DERIVED flag (cached_balance >= target)
    completed_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account", back_populates="savings_goals")


class Mission(Base):
    __tablename__ = "missions"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String(255), nullable=False)
    mission_type = Column(String(50), nullable=False)  # 'debt_payoff' | 'emergency_fund' | 'savings_goal'
    link_type = Column(String(50), nullable=True)  # 'debt' | 'savings'
    link_id = Column(Integer, nullable=True)  # FK to DebtAccount or SavingsGoal
    status = Column(String(50), nullable=False, default="active")  # 'active' | 'completed' | 'paused'
    sort_order = Column(Integer)  # Queue position
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account", back_populates="missions")


class CheckIn(Base):
    __tablename__ = "check_ins"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_account_date"),)

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    date = Column(Date, nullable=False)
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account", back_populates="check_ins")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    review_type = Column(String(50), nullable=False)  # 'weekly' | 'monthly'
    period_id = Column(Integer, ForeignKey("budget_periods.id"), nullable=True)
    week_start = Column(Date, nullable=True)
    prompt_shown = Column(Text, nullable=True)  # The reflection prompt presented
    completed_at = Column(DateTime, nullable=False)
    duration_seconds = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account", back_populates="reviews")
    period = relationship("BudgetPeriod", back_populates="reviews")


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (UniqueConstraint("account_id", "milestone_type", "threshold", "link_id", name="uq_milestone"),)

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    milestone_type = Column(String(50), nullable=False)  # 'debt_paid_off','savings_goal_reached', 'streak_achieved','first_budget', etc.
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    threshold = Column(Integer, nullable=True)  # e.g., streak day count, for idempotency
    link_type = Column(String(50), nullable=True)
    link_id = Column(Integer, nullable=True)
    celebrated = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account", back_populates="milestones")


class AssetAccount(Base):
    __tablename__ = "asset_accounts"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String(255), nullable=False)  # 'Checking', 'Savings', 'Cash'
    balance_cents = Column(Integer, nullable=False)  # Manually updated snapshot
    is_active = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("Account", back_populates="asset_accounts")
