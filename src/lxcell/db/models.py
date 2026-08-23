"""SQLAlchemy ORM models for the Phase 1 schema."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lxcell.enums.core_enums import (
    AccountType,
    BudgetPeriodType,
    CategoryMappingStatus,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    ClassificationMatchField,
    ClassificationRuleType,
    Direction,
    ImportAction,
    ImportSourceSystem,
    ImportStatus,
    OwnershipType,
    PaymentMethod,
    RolloverPolicy,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.db.base import Base


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def enum_type(enum_class: type[StrEnum]) -> SqlEnum:
    """Create a SQLAlchemy enum that persists StrEnum values, not member names."""
    return SqlEnum(
        enum_class,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        validate_strings=True,
    )


class IdMixin:
    """Integer primary key shared by Phase 1 tables."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class TimestampMixin:
    """Creation and update timestamps shared by main Phase 1 entities."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class UserProfile(IdMixin, TimestampMixin, Base):
    """An isolated financial workspace."""

    __tablename__ = "user_profiles"

    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    locale: Mapped[str] = mapped_column(String(20), default="es_ES", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    accounts: Mapped[list["Account"]] = relationship(back_populates="user_profile")
    categories: Mapped[list["Category"]] = relationship(back_populates="user_profile")
    category_mappings: Mapped[list["CategoryMapping"]] = relationship(
        back_populates="user_profile", overlaps="category"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="user_profile", overlaps="account,category"
    )
    import_batches: Mapped[list["ImportBatch"]] = relationship(
        back_populates="user_profile", overlaps="account"
    )
    classification_rules: Mapped[list["ClassificationRule"]] = relationship(
        back_populates="user_profile", overlaps="category"
    )
    budgets: Mapped[list["Budget"]] = relationship(back_populates="user_profile")

    __table_args__ = (CheckConstraint("length(default_currency) = 3"),)


class Account(IdMixin, TimestampMixin, Base):
    """A financial account, card, wallet, liability, or investment account."""

    __tablename__ = "accounts"

    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    institution_name: Mapped[str | None] = mapped_column(String(200))
    account_type: Mapped[AccountType] = mapped_column(enum_type(AccountType), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    ownership_type: Mapped[OwnershipType] = mapped_column(
        enum_type(OwnershipType), default=OwnershipType.PERSONAL, nullable=False
    )
    external_account_ref: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user_profile: Mapped[UserProfile] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account", overlaps="transactions,user_profile"
    )
    import_batches: Mapped[list["ImportBatch"]] = relationship(
        back_populates="account", overlaps="import_batches,user_profile"
    )

    __table_args__ = (
        UniqueConstraint("id", "user_profile_id"),
        UniqueConstraint("user_profile_id", "name"),
        CheckConstraint("length(currency) = 3"),
    )


class Category(IdMixin, TimestampMixin, Base):
    """A user-defined classification bucket for transactions and budgets."""

    __tablename__ = "categories"

    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_category_id: Mapped[int | None] = mapped_column(Integer)
    category_type: Mapped[CategoryType] = mapped_column(enum_type(CategoryType), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user_profile: Mapped[UserProfile] = relationship(back_populates="categories")
    parent_category: Mapped["Category | None"] = relationship(
        remote_side="Category.id",
        back_populates="child_categories",
        overlaps="categories,user_profile",
    )
    child_categories: Mapped[list["Category"]] = relationship(
        back_populates="parent_category", overlaps="categories,user_profile"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="category", overlaps="account,transactions,user_profile"
    )
    classification_rules: Mapped[list["ClassificationRule"]] = relationship(
        back_populates="category", overlaps="classification_rules,user_profile"
    )
    classification_decisions: Mapped[list["ClassificationDecision"]] = relationship(
        back_populates="category"
    )
    budget_lines: Mapped[list["BudgetLine"]] = relationship(back_populates="category")
    source_category_mappings: Mapped[list["CategoryMapping"]] = relationship(
        back_populates="target_category", overlaps="category_mappings,user_profile"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_category_id", "user_profile_id"],
            ["categories.id", "categories.user_profile_id"],
        ),
        UniqueConstraint("id", "user_profile_id"),
        UniqueConstraint("user_profile_id", "name"),
        UniqueConstraint("user_profile_id", "canonical_key"),
    )


class CategoryMapping(IdMixin, TimestampMixin, Base):
    """Mapping from a historical source category to an LXCell category."""

    __tablename__ = "category_mappings"

    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    source_system: Mapped[ImportSourceSystem] = mapped_column(
        enum_type(ImportSourceSystem), nullable=False
    )
    source_file_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_file_name: Mapped[str | None] = mapped_column(String(500))
    source_category_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_category_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_column_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    target_category_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_from_import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id")
    )
    mapping_status: Mapped[CategoryMappingStatus] = mapped_column(
        enum_type(CategoryMappingStatus),
        default=CategoryMappingStatus.CONFIRMED,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text)

    user_profile: Mapped[UserProfile] = relationship(
        back_populates="category_mappings", overlaps="target_category"
    )
    target_category: Mapped[Category] = relationship(
        back_populates="source_category_mappings",
        overlaps="category_mappings,user_profile",
    )
    created_from_import_batch: Mapped["ImportBatch | None"] = relationship(
        back_populates="category_mappings"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["target_category_id", "user_profile_id"],
            ["categories.id", "categories.user_profile_id"],
        ),
        UniqueConstraint(
            "user_profile_id",
            "source_system",
            "source_file_hash",
            "source_category_key",
        ),
    )


class Transaction(IdMixin, TimestampMixin, Base):
    """One normalized financial movement."""

    __tablename__ = "transactions"

    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    posted_date: Mapped[date | None] = mapped_column(Date)
    description_clean: Mapped[str | None] = mapped_column(String(500))
    description_raw: Mapped[str | None] = mapped_column(String(1000))
    category_id: Mapped[int | None] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    direction: Mapped[Direction] = mapped_column(enum_type(Direction), nullable=False)
    transaction_type: Mapped[TransactionType] = mapped_column(
        enum_type(TransactionType), nullable=False
    )
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod))
    review_status: Mapped[TransactionReviewStatus] = mapped_column(
        enum_type(TransactionReviewStatus),
        default=TransactionReviewStatus.PENDING_REVIEW,
        nullable=False,
    )
    source_type: Mapped[TransactionSourceType] = mapped_column(
        enum_type(TransactionSourceType), nullable=False
    )
    source_id: Mapped[str | None] = mapped_column(String(200))
    is_duplicate_candidate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user_profile: Mapped[UserProfile] = relationship(
        back_populates="transactions", overlaps="account,category,transactions"
    )
    account: Mapped[Account] = relationship(
        back_populates="transactions", overlaps="category,transactions,user_profile"
    )
    category: Mapped[Category | None] = relationship(
        back_populates="transactions", overlaps="account,transactions,user_profile"
    )
    imported_source: Mapped["ImportedTransactionSource | None"] = relationship(
        back_populates="created_transaction"
    )
    classification_decisions: Mapped[list["ClassificationDecision"]] = relationship(
        back_populates="transaction"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_profile_id"],
            ["accounts.id", "accounts.user_profile_id"],
        ),
        ForeignKeyConstraint(
            ["category_id", "user_profile_id"],
            ["categories.id", "categories.user_profile_id"],
        ),
        UniqueConstraint("id", "user_profile_id"),
        CheckConstraint("amount_minor >= 0"),
        CheckConstraint("length(currency) = 3"),
        CheckConstraint(
            "review_status != 'user_confirmed' OR description_clean IS NOT NULL"
        ),
    )


class ImportBatch(IdMixin, TimestampMixin, Base):
    """One import operation."""

    __tablename__ = "import_batches"

    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    account_id: Mapped[int | None] = mapped_column(Integer)
    source_system: Mapped[ImportSourceSystem] = mapped_column(
        enum_type(ImportSourceSystem), nullable=False
    )
    source_file_name: Mapped[str | None] = mapped_column(String(500))
    source_file_hash: Mapped[str | None] = mapped_column(String(128))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    import_status: Mapped[ImportStatus] = mapped_column(
        enum_type(ImportStatus), default=ImportStatus.PENDING, nullable=False
    )
    imported_by: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)

    user_profile: Mapped[UserProfile] = relationship(
        back_populates="import_batches", overlaps="account,import_batches"
    )
    account: Mapped[Account | None] = relationship(
        back_populates="import_batches", overlaps="import_batches,user_profile"
    )
    imported_transaction_sources: Mapped[list["ImportedTransactionSource"]] = relationship(
        back_populates="import_batch"
    )
    category_mappings: Mapped[list[CategoryMapping]] = relationship(
        back_populates="created_from_import_batch"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_profile_id"],
            ["accounts.id", "accounts.user_profile_id"],
        ),
    )


class ImportedTransactionSource(IdMixin, Base):
    """Source-level representation of an imported row or record."""

    __tablename__ = "imported_transaction_sources"

    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id"), nullable=False
    )
    row_number_source: Mapped[int | None] = mapped_column(Integer)
    record_id_source: Mapped[str | None] = mapped_column(String(200))
    date_raw: Mapped[str | None] = mapped_column(String(100))
    description_raw: Mapped[str | None] = mapped_column(String(1000))
    amount_raw: Mapped[str | None] = mapped_column(String(100))
    currency_raw: Mapped[str | None] = mapped_column(String(20))
    payload_raw_json: Mapped[str | None] = mapped_column(Text)
    normalized_hash: Mapped[str | None] = mapped_column(String(128))
    created_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    import_action: Mapped[ImportAction] = mapped_column(enum_type(ImportAction), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    import_batch: Mapped[ImportBatch] = relationship(
        back_populates="imported_transaction_sources"
    )
    created_transaction: Mapped[Transaction | None] = relationship(
        back_populates="imported_source"
    )

    __table_args__ = (UniqueConstraint("created_transaction_id"),)


class ClassificationRule(IdMixin, TimestampMixin, Base):
    """A deterministic rule that can suggest transaction classifications."""

    __tablename__ = "classification_rules"

    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_type: Mapped[ClassificationRuleType] = mapped_column(
        enum_type(ClassificationRuleType), nullable=False
    )
    match_field: Mapped[ClassificationMatchField] = mapped_column(
        enum_type(ClassificationMatchField), nullable=False
    )
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    category_id: Mapped[int | None] = mapped_column(Integer)
    transaction_type: Mapped[TransactionType | None] = mapped_column(enum_type(TransactionType))
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod))
    direction: Mapped[Direction | None] = mapped_column(enum_type(Direction))
    amount_min_minor: Mapped[int | None] = mapped_column(Integer)
    amount_max_minor: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=Decimal("1.0000"), nullable=False
    )
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user_profile: Mapped[UserProfile] = relationship(
        back_populates="classification_rules", overlaps="category,classification_rules"
    )
    category: Mapped[Category | None] = relationship(
        back_populates="classification_rules", overlaps="classification_rules,user_profile"
    )
    classification_decisions: Mapped[list["ClassificationDecision"]] = relationship(
        back_populates="classification_rule"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "user_profile_id"],
            ["categories.id", "categories.user_profile_id"],
        ),
        CheckConstraint("amount_min_minor IS NULL OR amount_min_minor >= 0"),
        CheckConstraint("amount_max_minor IS NULL OR amount_max_minor >= 0"),
        CheckConstraint(
            "amount_min_minor IS NULL OR amount_max_minor IS NULL "
            "OR amount_min_minor <= amount_max_minor"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1"),
    )


class ClassificationDecision(IdMixin, Base):
    """Append-only record of a classification suggestion or accepted decision."""

    __tablename__ = "classification_decisions"

    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    transaction_type: Mapped[TransactionType | None] = mapped_column(enum_type(TransactionType))
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod))
    decision_source: Mapped[ClassificationDecisionSource] = mapped_column(
        enum_type(ClassificationDecisionSource), nullable=False
    )
    classification_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("classification_rules.id")
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=Decimal("1.0000"), nullable=False
    )
    decision_status: Mapped[ClassificationDecisionStatus] = mapped_column(
        enum_type(ClassificationDecisionStatus), nullable=False
    )
    decided_by: Mapped[str] = mapped_column(String(200), default="system", nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    transaction: Mapped[Transaction] = relationship(back_populates="classification_decisions")
    category: Mapped[Category | None] = relationship(back_populates="classification_decisions")
    classification_rule: Mapped[ClassificationRule | None] = relationship(
        back_populates="classification_decisions"
    )

    __table_args__ = (CheckConstraint("confidence >= 0 AND confidence <= 1"),)


class Budget(IdMixin, TimestampMixin, Base):
    """A budget plan for one user profile and period."""

    __tablename__ = "budgets"

    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    period_type: Mapped[BudgetPeriodType] = mapped_column(
        enum_type(BudgetPeriodType), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user_profile: Mapped[UserProfile] = relationship(back_populates="budgets")
    budget_lines: Mapped[list["BudgetLine"]] = relationship(back_populates="budget")

    __table_args__ = (
        UniqueConstraint("user_profile_id", "name"),
        CheckConstraint("length(currency) = 3"),
        CheckConstraint("start_date <= end_date"),
    )


class BudgetLine(IdMixin, TimestampMixin, Base):
    """A planned amount for a category within a budget."""

    __tablename__ = "budget_lines"

    budget_id: Mapped[int] = mapped_column(ForeignKey("budgets.id"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    rollover_policy: Mapped[RolloverPolicy] = mapped_column(
        enum_type(RolloverPolicy), default=RolloverPolicy.NONE, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    budget: Mapped[Budget] = relationship(back_populates="budget_lines")
    category: Mapped[Category] = relationship(back_populates="budget_lines")

    __table_args__ = (
        UniqueConstraint("budget_id", "category_id"),
        CheckConstraint("amount_minor >= 0"),
    )


__all__ = [
    "Account",
    "Base",
    "Budget",
    "BudgetLine",
    "Category",
    "CategoryMapping",
    "ClassificationDecision",
    "ClassificationRule",
    "IdMixin",
    "ImportBatch",
    "ImportedTransactionSource",
    "TimestampMixin",
    "Transaction",
    "UserProfile",
    "enum_type",
    "utc_now",
]
