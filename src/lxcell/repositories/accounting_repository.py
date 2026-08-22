"""Persistence operations for the accounting core."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from lxcell.db.models import (
    Account,
    Budget,
    BudgetLine,
    Category,
    ClassificationDecision,
    Transaction,
    UserProfile,
)
from lxcell.enums.core_enums import (
    AccountType,
    BudgetPeriodType,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    Direction,
    OwnershipType,
    PaymentMethod,
    RolloverPolicy,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)


class AccountingRepository:
    """Repository for Phase 1 accounting persistence operations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_user_profile(
        self,
        *,
        display_name: str,
        default_currency: str = "EUR",
        locale: str = "es_ES",
        is_active: bool = True,
    ) -> UserProfile:
        user_profile = UserProfile(
            display_name=display_name,
            default_currency=default_currency,
            locale=locale,
            is_active=is_active,
        )
        self.session.add(user_profile)
        return user_profile

    def get_user_profile(self, user_profile_id: int) -> UserProfile | None:
        return self.session.get(UserProfile, user_profile_id)

    def add_account(
        self,
        *,
        user_profile_id: int,
        name: str,
        account_type: AccountType,
        institution_name: str | None = None,
        currency: str = "EUR",
        ownership_type: OwnershipType = OwnershipType.PERSONAL,
        external_account_ref: str | None = None,
        is_active: bool = True,
    ) -> Account:
        account = Account(
            user_profile_id=user_profile_id,
            name=name,
            institution_name=institution_name,
            account_type=account_type,
            currency=currency,
            ownership_type=ownership_type,
            external_account_ref=external_account_ref,
            is_active=is_active,
        )
        self.session.add(account)
        return account

    def list_accounts(
        self, user_profile_id: int, *, include_inactive: bool = False
    ) -> list[Account]:
        statement = select(Account).where(Account.user_profile_id == user_profile_id)
        if not include_inactive:
            statement = statement.where(Account.is_active.is_(True))
        return list(self.session.scalars(statement.order_by(Account.name, Account.id)))

    def add_category(
        self,
        *,
        user_profile_id: int,
        name: str,
        category_type: CategoryType,
        canonical_key: str,
        parent_category_id: int | None = None,
        display_order: int = 0,
        is_active: bool = True,
    ) -> Category:
        category = Category(
            user_profile_id=user_profile_id,
            name=name,
            parent_category_id=parent_category_id,
            category_type=category_type,
            canonical_key=canonical_key,
            display_order=display_order,
            is_active=is_active,
        )
        self.session.add(category)
        return category

    def list_categories(
        self, user_profile_id: int, *, include_inactive: bool = False
    ) -> list[Category]:
        statement = select(Category).where(Category.user_profile_id == user_profile_id)
        if not include_inactive:
            statement = statement.where(Category.is_active.is_(True))
        return list(
            self.session.scalars(
                statement.order_by(Category.display_order, Category.name, Category.id)
            )
        )

    def add_transaction(
        self,
        *,
        user_profile_id: int,
        account_id: int,
        transaction_date: date,
        amount_minor: int,
        direction: Direction,
        transaction_type: TransactionType,
        source_type: TransactionSourceType,
        posted_date: date | None = None,
        description_clean: str | None = None,
        description_raw: str | None = None,
        category_id: int | None = None,
        currency: str = "EUR",
        payment_method: PaymentMethod | None = None,
        review_status: TransactionReviewStatus = (
            TransactionReviewStatus.PENDING_REVIEW
        ),
        source_id: str | None = None,
        is_duplicate_candidate: bool = False,
        is_deleted: bool = False,
    ) -> Transaction:
        transaction = Transaction(
            user_profile_id=user_profile_id,
            account_id=account_id,
            transaction_date=transaction_date,
            posted_date=posted_date,
            description_clean=description_clean,
            description_raw=description_raw,
            category_id=category_id,
            amount_minor=amount_minor,
            currency=currency,
            direction=direction,
            transaction_type=transaction_type,
            payment_method=payment_method,
            review_status=review_status,
            source_type=source_type,
            source_id=source_id,
            is_duplicate_candidate=is_duplicate_candidate,
            is_deleted=is_deleted,
        )
        self.session.add(transaction)
        return transaction

    def get_transaction(
        self, *, transaction_id: int, user_profile_id: int
    ) -> Transaction | None:
        statement = select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_profile_id == user_profile_id,
        )
        return self.session.scalar(statement)

    def list_transactions(
        self,
        *,
        user_profile_id: int,
        account_id: int | None = None,
        category_id: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        review_status: TransactionReviewStatus | None = None,
        include_deleted: bool = False,
    ) -> list[Transaction]:
        statement = select(Transaction).where(
            Transaction.user_profile_id == user_profile_id
        )
        if account_id is not None:
            statement = statement.where(Transaction.account_id == account_id)
        if category_id is not None:
            statement = statement.where(Transaction.category_id == category_id)
        if start_date is not None:
            statement = statement.where(Transaction.transaction_date >= start_date)
        if end_date is not None:
            statement = statement.where(Transaction.transaction_date <= end_date)
        if review_status is not None:
            statement = statement.where(Transaction.review_status == review_status)
        if not include_deleted:
            statement = statement.where(Transaction.is_deleted.is_(False))
        return list(self.session.scalars(self._order_transactions(statement)))

    def add_classification_decision(
        self,
        *,
        transaction_id: int,
        decision_source: ClassificationDecisionSource,
        decision_status: ClassificationDecisionStatus,
        category_id: int | None = None,
        transaction_type: TransactionType | None = None,
        payment_method: PaymentMethod | None = None,
        classification_rule_id: int | None = None,
        confidence: Decimal = Decimal("1.0000"),
        decided_by: str = "system",
        notes: str | None = None,
    ) -> ClassificationDecision:
        decision = ClassificationDecision(
            transaction_id=transaction_id,
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            decision_source=decision_source,
            classification_rule_id=classification_rule_id,
            confidence=confidence,
            decision_status=decision_status,
            decided_by=decided_by,
            notes=notes,
        )
        self.session.add(decision)
        return decision

    def list_classification_decisions(
        self, *, transaction_id: int, user_profile_id: int
    ) -> list[ClassificationDecision]:
        statement = (
            select(ClassificationDecision)
            .join(Transaction)
            .where(
                ClassificationDecision.transaction_id == transaction_id,
                Transaction.user_profile_id == user_profile_id,
            )
            .order_by(ClassificationDecision.decided_at, ClassificationDecision.id)
        )
        return list(self.session.scalars(statement))

    def add_budget(
        self,
        *,
        user_profile_id: int,
        name: str,
        period_type: BudgetPeriodType,
        start_date: date,
        end_date: date,
        currency: str = "EUR",
        is_active: bool = True,
    ) -> Budget:
        budget = Budget(
            user_profile_id=user_profile_id,
            name=name,
            period_type=period_type,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            is_active=is_active,
        )
        self.session.add(budget)
        return budget

    def get_budget(self, *, budget_id: int, user_profile_id: int) -> Budget | None:
        statement = select(Budget).where(
            Budget.id == budget_id,
            Budget.user_profile_id == user_profile_id,
        )
        return self.session.scalar(statement)

    def add_budget_line(
        self,
        *,
        budget_id: int,
        category_id: int,
        amount_minor: int,
        rollover_policy: RolloverPolicy = RolloverPolicy.NONE,
        notes: str | None = None,
    ) -> BudgetLine:
        budget_line = BudgetLine(
            budget_id=budget_id,
            category_id=category_id,
            amount_minor=amount_minor,
            rollover_policy=rollover_policy,
            notes=notes,
        )
        self.session.add(budget_line)
        return budget_line

    @staticmethod
    def _order_transactions(statement: Select[tuple[Transaction]]) -> Select[tuple[Transaction]]:
        return statement.order_by(Transaction.transaction_date, Transaction.id)


__all__ = ["AccountingRepository"]
