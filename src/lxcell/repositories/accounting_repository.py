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
    CategoryMapping,
    ClassificationDecision,
    ImportBatch,
    ImportedTransactionSource,
    Transaction,
    UserProfile,
)
from lxcell.enums.core_enums import (
    AccountType,
    BudgetPeriodType,
    CategoryMappingStatus,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
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
        transactions_locked_until: date | None = None,
    ) -> UserProfile:
        user_profile = UserProfile(
            display_name=display_name,
            default_currency=default_currency,
            locale=locale,
            is_active=is_active,
            transactions_locked_until=transactions_locked_until,
        )
        self.session.add(user_profile)
        return user_profile

    def get_user_profile(self, user_profile_id: int) -> UserProfile | None:
        return self.session.get(UserProfile, user_profile_id)

    def list_user_profiles(self, *, include_inactive: bool = False) -> list[UserProfile]:
        statement = select(UserProfile)
        if not include_inactive:
            statement = statement.where(UserProfile.is_active.is_(True))
        return list(
            self.session.scalars(statement.order_by(UserProfile.display_name, UserProfile.id))
        )

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

    def add_category_mapping(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        source_file_hash: str,
        source_category_name: str,
        source_category_key: str,
        source_column_kind: str,
        target_category_id: int,
        source_file_name: str | None = None,
        created_from_import_batch_id: int | None = None,
        mapping_status: CategoryMappingStatus = CategoryMappingStatus.CONFIRMED,
        notes: str | None = None,
    ) -> CategoryMapping:
        category_mapping = CategoryMapping(
            user_profile_id=user_profile_id,
            source_system=source_system,
            source_file_hash=source_file_hash,
            source_file_name=source_file_name,
            source_category_name=source_category_name,
            source_category_key=source_category_key,
            source_column_kind=source_column_kind,
            target_category_id=target_category_id,
            created_from_import_batch_id=created_from_import_batch_id,
            mapping_status=mapping_status,
            notes=notes,
        )
        self.session.add(category_mapping)
        return category_mapping

    def get_category_mapping_for_source(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        source_file_hash: str,
        source_category_key: str,
    ) -> CategoryMapping | None:
        statement = select(CategoryMapping).where(
            CategoryMapping.user_profile_id == user_profile_id,
            CategoryMapping.source_system == source_system,
            CategoryMapping.source_file_hash == source_file_hash,
            CategoryMapping.source_category_key == source_category_key,
        )
        return self.session.scalar(statement)

    def list_category_mapping_suggestions(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        source_category_key: str,
    ) -> list[CategoryMapping]:
        statement = (
            select(CategoryMapping)
            .join(Category)
            .where(
                CategoryMapping.user_profile_id == user_profile_id,
                CategoryMapping.source_system == source_system,
                CategoryMapping.source_category_key == source_category_key,
                CategoryMapping.mapping_status == CategoryMappingStatus.CONFIRMED,
                Category.is_active.is_(True),
            )
            .order_by(CategoryMapping.updated_at.desc(), CategoryMapping.id.desc())
        )
        return list(self.session.scalars(statement))

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

    def get_category(self, *, category_id: int, user_profile_id: int) -> Category | None:
        statement = select(Category).where(
            Category.id == category_id,
            Category.user_profile_id == user_profile_id,
        )
        return self.session.scalar(statement)

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

    def add_import_batch(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        account_id: int | None = None,
        source_file_name: str | None = None,
        source_file_hash: str | None = None,
        import_status: ImportStatus = ImportStatus.COMPLETED,
        imported_by: str | None = None,
        notes: str | None = None,
    ) -> ImportBatch:
        import_batch = ImportBatch(
            user_profile_id=user_profile_id,
            account_id=account_id,
            source_system=source_system,
            source_file_name=source_file_name,
            source_file_hash=source_file_hash,
            import_status=import_status,
            imported_by=imported_by,
            notes=notes,
        )
        self.session.add(import_batch)
        return import_batch

    def get_completed_import_batch_by_file_hash(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        source_file_hash: str,
    ) -> ImportBatch | None:
        statement = select(ImportBatch).where(
            ImportBatch.user_profile_id == user_profile_id,
            ImportBatch.source_system == source_system,
            ImportBatch.source_file_hash == source_file_hash,
            ImportBatch.import_status.in_(
                [
                    ImportStatus.COMPLETED,
                    ImportStatus.COMPLETED_WITH_WARNINGS,
                ]
            ),
        )
        return self.session.scalar(statement.order_by(ImportBatch.imported_at.desc()))

    def get_import_batch(
        self, *, import_batch_id: int, user_profile_id: int
    ) -> ImportBatch | None:
        statement = select(ImportBatch).where(
            ImportBatch.id == import_batch_id,
            ImportBatch.user_profile_id == user_profile_id,
        )
        return self.session.scalar(statement)

    def get_imported_source_by_normalized_hash(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        normalized_hash: str,
    ) -> ImportedTransactionSource | None:
        statement = (
            select(ImportedTransactionSource)
            .join(ImportBatch)
            .where(
                ImportBatch.user_profile_id == user_profile_id,
                ImportBatch.source_system == source_system,
                ImportBatch.import_status.in_(
                    [
                        ImportStatus.COMPLETED,
                        ImportStatus.COMPLETED_WITH_WARNINGS,
                    ]
                ),
                ImportedTransactionSource.normalized_hash == normalized_hash,
            )
        )
        return self.session.scalar(statement.order_by(ImportedTransactionSource.id))

    def add_imported_transaction_source(
        self,
        *,
        import_batch_id: int,
        import_action: ImportAction,
        row_number_source: int | None = None,
        record_id_source: str | None = None,
        date_raw: str | None = None,
        description_raw: str | None = None,
        amount_raw: str | None = None,
        currency_raw: str | None = None,
        payload_raw_json: str | None = None,
        normalized_hash: str | None = None,
        created_transaction_id: int | None = None,
    ) -> ImportedTransactionSource:
        imported_source = ImportedTransactionSource(
            import_batch_id=import_batch_id,
            row_number_source=row_number_source,
            record_id_source=record_id_source,
            date_raw=date_raw,
            description_raw=description_raw,
            amount_raw=amount_raw,
            currency_raw=currency_raw,
            payload_raw_json=payload_raw_json,
            normalized_hash=normalized_hash,
            created_transaction_id=created_transaction_id,
            import_action=import_action,
        )
        self.session.add(imported_source)
        return imported_source

    def list_imported_transaction_sources(
        self, *, import_batch_id: int
    ) -> list[ImportedTransactionSource]:
        statement = select(ImportedTransactionSource).where(
            ImportedTransactionSource.import_batch_id == import_batch_id
        )
        return list(self.session.scalars(statement.order_by(ImportedTransactionSource.id)))

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
