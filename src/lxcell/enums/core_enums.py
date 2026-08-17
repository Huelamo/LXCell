"""Core enums for the Phase 1 accounting model."""

from enum import StrEnum


class AccountType(StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    INVESTMENT = "investment"
    LOAN = "loan"
    MORTGAGE = "mortgage"
    OTHER = "other"


class OwnershipType(StrEnum):
    PERSONAL = "personal"
    SHARED = "shared"
    MANAGED_FOR_SOMEONE_ELSE = "managed_for_someone_else"


class CategoryType(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    SAVING = "saving"
    INVESTMENT = "investment"
    DEBT = "debt"
    ADJUSTMENT = "adjustment"


class Direction(StrEnum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    NEUTRAL = "neutral"


class TransactionType(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    REFUND = "refund"
    FEE = "fee"
    TAX = "tax"
    SAVING = "saving"
    INVESTMENT = "investment"
    DEBT_PAYMENT = "debt_payment"
    ADJUSTMENT = "adjustment"


class PaymentMethod(StrEnum):
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    PEER_TO_PEER = "peer_to_peer"
    DIRECT_DEBIT = "direct_debit"
    CASH = "cash"
    STANDING_ORDER = "standing_order"
    OTHER = "other"


class TransactionReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    USER_CONFIRMED = "user_confirmed"
    IGNORED = "ignored"


class TransactionSourceType(StrEnum):
    MANUAL = "manual"
    BANK_IMPORT = "bank_import"
    EXCEL_IMPORT = "excel_import"
    API = "api"
    OTHER = "other"


class ImportSourceSystem(StrEnum):
    MANUAL_ENTRY = "manual_entry"
    BANK_CSV = "bank_csv"
    CARD_CSV = "card_csv"
    EXCEL_HISTORICAL = "excel_historical"
    API = "api"
    OTHER = "other"


class ImportStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ImportAction(StrEnum):
    CREATED_TRANSACTION = "created_transaction"
    MATCHED_EXISTING = "matched_existing"
    MARKED_DUPLICATE = "marked_duplicate"
    IGNORED = "ignored"
    FAILED_VALIDATION = "failed_validation"


class ClassificationRuleType(StrEnum):
    DESCRIPTION_CONTAINS = "description_contains"
    DESCRIPTION_REGEX = "description_regex"
    AMOUNT_AND_DESCRIPTION = "amount_and_description"
    RECURRING_TRANSACTION = "recurring_transaction"
    HISTORICAL_MATCH = "historical_match"


class ClassificationMatchField(StrEnum):
    DESCRIPTION_RAW = "description_raw"
    DESCRIPTION_CLEAN = "description_clean"


class ClassificationDecisionSource(StrEnum):
    MANUAL_USER = "manual_user"
    DETERMINISTIC_RULE = "deterministic_rule"
    HISTORICAL_MATCH = "historical_match"
    AI_SUGGESTION = "ai_suggestion"
    IMPORT_DEFAULT = "import_default"


class ClassificationDecisionStatus(StrEnum):
    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class BudgetPeriodType(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class RolloverPolicy(StrEnum):
    NONE = "none"
