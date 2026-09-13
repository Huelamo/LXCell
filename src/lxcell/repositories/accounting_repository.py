"""Persistence operations for the accounting core."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload

from lxcell.db.models import (
    Account,
    Budget,
    BudgetLine,
    Category,
    CategoryMapping,
    ClassificationDecision,
    ClassificationRule,
    Counterparty,
    ImportBatch,
    ImportedTransactionSource,
    ReimbursementMatch,
    SharedExpenseAllocation,
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
    ClassificationMatchField,
    ClassificationRuleType,
    Direction,
    ImportAction,
    ImportSourceSystem,
    ImportStatus,
    OwnershipType,
    PaymentMethod,
    ReimbursementMatchStatus,
    RolloverPolicy,
    SharedExpenseStatus,
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

    def get_account(self, *, account_id: int, user_profile_id: int) -> Account | None:
        statement = select(Account).where(
            Account.id == account_id,
            Account.user_profile_id == user_profile_id,
        )
        return self.session.scalar(statement)

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

    def add_counterparty(
        self,
        *,
        user_profile_id: int,
        display_name: str,
        normalized_name: str,
        aliases_raw: str | None = None,
        is_active: bool = True,
    ) -> Counterparty:
        counterparty = Counterparty(
            user_profile_id=user_profile_id,
            display_name=display_name,
            normalized_name=normalized_name,
            aliases_raw=aliases_raw,
            is_active=is_active,
        )
        self.session.add(counterparty)
        return counterparty

    def list_counterparties(
        self, user_profile_id: int, *, include_inactive: bool = False
    ) -> list[Counterparty]:
        statement = select(Counterparty).where(
            Counterparty.user_profile_id == user_profile_id
        )
        if not include_inactive:
            statement = statement.where(Counterparty.is_active.is_(True))
        return list(
            self.session.scalars(
                statement.order_by(Counterparty.display_name, Counterparty.id)
            )
        )

    def get_counterparty(
        self, *, counterparty_id: int, user_profile_id: int
    ) -> Counterparty | None:
        statement = select(Counterparty).where(
            Counterparty.id == counterparty_id,
            Counterparty.user_profile_id == user_profile_id,
        )
        return self.session.scalar(statement)

    def get_counterparty_by_normalized_name(
        self, *, user_profile_id: int, normalized_name: str
    ) -> Counterparty | None:
        statement = select(Counterparty).where(
            Counterparty.user_profile_id == user_profile_id,
            Counterparty.normalized_name == normalized_name,
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

    def get_completed_import_batch_by_file_hash_for_account(
        self,
        *,
        user_profile_id: int,
        account_id: int,
        source_system: ImportSourceSystem,
        source_file_hash: str,
    ) -> ImportBatch | None:
        statement = select(ImportBatch).where(
            ImportBatch.user_profile_id == user_profile_id,
            ImportBatch.account_id == account_id,
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

    def get_imported_source_by_normalized_hash_for_account(
        self,
        *,
        user_profile_id: int,
        account_id: int,
        source_system: ImportSourceSystem,
        normalized_hash: str,
    ) -> ImportedTransactionSource | None:
        statement = (
            select(ImportedTransactionSource)
            .join(ImportBatch)
            .where(
                ImportBatch.user_profile_id == user_profile_id,
                ImportBatch.account_id == account_id,
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

    def add_shared_expense_allocation(
        self,
        *,
        user_profile_id: int,
        transaction_id: int,
        counterparty_id: int,
        personal_share_minor: int,
        recoverable_share_minor: int,
        share_ratio_basis_points: int | None,
        status: SharedExpenseStatus = SharedExpenseStatus.PENDING,
        decided_by: str = "system",
        notes: str | None = None,
    ) -> SharedExpenseAllocation:
        allocation = SharedExpenseAllocation(
            user_profile_id=user_profile_id,
            transaction_id=transaction_id,
            counterparty_id=counterparty_id,
            personal_share_minor=personal_share_minor,
            recoverable_share_minor=recoverable_share_minor,
            share_ratio_basis_points=share_ratio_basis_points,
            status=status,
            decided_by=decided_by,
            notes=notes,
        )
        self.session.add(allocation)
        return allocation

    def get_shared_expense_allocation_for_transaction(
        self, *, transaction_id: int, user_profile_id: int
    ) -> SharedExpenseAllocation | None:
        statement = select(SharedExpenseAllocation).where(
            SharedExpenseAllocation.transaction_id == transaction_id,
            SharedExpenseAllocation.user_profile_id == user_profile_id,
        )
        return self.session.scalar(statement)

    def list_shared_expense_allocations_for_profile(
        self,
        *,
        user_profile_id: int,
        include_waived: bool = False,
    ) -> list[SharedExpenseAllocation]:
        statement = select(SharedExpenseAllocation).where(
            SharedExpenseAllocation.user_profile_id == user_profile_id
        )
        if not include_waived:
            statement = statement.where(
                SharedExpenseAllocation.status != SharedExpenseStatus.WAIVED
            )
        return list(
            self.session.scalars(
                statement.order_by(
                    SharedExpenseAllocation.updated_at.desc(),
                    SharedExpenseAllocation.id.desc(),
                )
            )
        )

    def add_reimbursement_match(
        self,
        *,
        user_profile_id: int,
        shared_expense_allocation_id: int,
        reimbursement_transaction_id: int,
        matched_amount_minor: int,
        status: ReimbursementMatchStatus = ReimbursementMatchStatus.SUGGESTED,
        confidence: Decimal = Decimal("1.0000"),
        decided_by: str = "system",
        notes: str | None = None,
    ) -> ReimbursementMatch:
        reimbursement_match = ReimbursementMatch(
            user_profile_id=user_profile_id,
            shared_expense_allocation_id=shared_expense_allocation_id,
            reimbursement_transaction_id=reimbursement_transaction_id,
            matched_amount_minor=matched_amount_minor,
            status=status,
            confidence=confidence,
            decided_by=decided_by,
            notes=notes,
        )
        self.session.add(reimbursement_match)
        return reimbursement_match

    def get_reimbursement_match(
        self,
        *,
        reimbursement_match_id: int,
        user_profile_id: int,
    ) -> ReimbursementMatch | None:
        statement = select(ReimbursementMatch).where(
            ReimbursementMatch.id == reimbursement_match_id,
            ReimbursementMatch.user_profile_id == user_profile_id,
        )
        return self.session.scalar(statement)

    def get_reimbursement_match_for_pair(
        self,
        *,
        user_profile_id: int,
        shared_expense_allocation_id: int,
        reimbursement_transaction_id: int,
    ) -> ReimbursementMatch | None:
        statement = select(ReimbursementMatch).where(
            ReimbursementMatch.user_profile_id == user_profile_id,
            ReimbursementMatch.shared_expense_allocation_id == shared_expense_allocation_id,
            ReimbursementMatch.reimbursement_transaction_id == reimbursement_transaction_id,
        )
        return self.session.scalar(statement)

    def get_confirmed_reimbursement_match_for_transaction(
        self,
        *,
        user_profile_id: int,
        reimbursement_transaction_id: int,
    ) -> ReimbursementMatch | None:
        statement = select(ReimbursementMatch).where(
            ReimbursementMatch.user_profile_id == user_profile_id,
            ReimbursementMatch.reimbursement_transaction_id == reimbursement_transaction_id,
            ReimbursementMatch.status == ReimbursementMatchStatus.CONFIRMED,
        )
        return self.session.scalar(statement.order_by(ReimbursementMatch.id))

    def list_reimbursement_matches_for_profile(
        self,
        *,
        user_profile_id: int,
        statuses: list[ReimbursementMatchStatus] | None = None,
    ) -> list[ReimbursementMatch]:
        statement = select(ReimbursementMatch).where(
            ReimbursementMatch.user_profile_id == user_profile_id
        )
        if statuses is not None:
            statement = statement.where(ReimbursementMatch.status.in_(statuses))
        return list(
            self.session.scalars(
                statement.order_by(
                    ReimbursementMatch.updated_at.desc(),
                    ReimbursementMatch.id.desc(),
                )
            )
        )

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

    def add_classification_rule(
        self,
        *,
        user_profile_id: int,
        name: str,
        rule_type: ClassificationRuleType,
        match_field: ClassificationMatchField,
        pattern: str,
        category_id: int | None = None,
        transaction_type: TransactionType | None = None,
        payment_method: PaymentMethod | None = None,
        direction: Direction | None = None,
        amount_min_minor: int | None = None,
        amount_max_minor: int | None = None,
        priority: int = 100,
        confidence: Decimal = Decimal("1.0000"),
        auto_apply: bool = False,
        is_active: bool = True,
    ) -> ClassificationRule:
        classification_rule = ClassificationRule(
            user_profile_id=user_profile_id,
            name=name,
            rule_type=rule_type,
            match_field=match_field,
            pattern=pattern,
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            direction=direction,
            amount_min_minor=amount_min_minor,
            amount_max_minor=amount_max_minor,
            priority=priority,
            confidence=confidence,
            auto_apply=auto_apply,
            is_active=is_active,
        )
        self.session.add(classification_rule)
        return classification_rule

    def list_classification_rules(
        self,
        *,
        user_profile_id: int,
        include_inactive: bool = False,
    ) -> list[ClassificationRule]:
        statement = select(ClassificationRule).where(
            ClassificationRule.user_profile_id == user_profile_id
        ).options(joinedload(ClassificationRule.category))
        if not include_inactive:
            statement = statement.where(ClassificationRule.is_active.is_(True))
        return list(
            self.session.scalars(
                statement.order_by(
                    ClassificationRule.priority,
                    ClassificationRule.name,
                    ClassificationRule.id,
                )
            )
        )

    def get_classification_rule(
        self,
        *,
        classification_rule_id: int,
        user_profile_id: int,
    ) -> ClassificationRule | None:
        return self.session.scalar(
            select(ClassificationRule)
            .where(
                ClassificationRule.id == classification_rule_id,
                ClassificationRule.user_profile_id == user_profile_id,
            )
            .options(joinedload(ClassificationRule.category))
        )

    def unlink_classification_decisions_from_rule(
        self,
        *,
        classification_rule_id: int,
        user_profile_id: int,
    ) -> int:
        statement = (
            select(ClassificationDecision)
            .join(Transaction)
            .where(
                ClassificationDecision.classification_rule_id
                == classification_rule_id,
                Transaction.user_profile_id == user_profile_id,
            )
        )
        decisions = list(self.session.scalars(statement))
        for decision in decisions:
            decision.classification_rule_id = None
        return len(decisions)

    def delete_classification_rule(self, classification_rule: ClassificationRule) -> None:
        self.session.delete(classification_rule)

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
