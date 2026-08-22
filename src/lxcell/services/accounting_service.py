"""Accounting use cases and business workflows."""

from datetime import date
from decimal import Decimal

from lxcell.db.models import (
    Account,
    Category,
    ClassificationDecision,
    Transaction,
    UserProfile,
    utc_now,
)
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    Direction,
    ImportAction,
    ImportSourceSystem,
    OwnershipType,
    PaymentMethod,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.repositories import AccountingRepository


class AccountingService:
    """Service layer for Phase 1 accounting workflows."""

    def __init__(self, repository: AccountingRepository) -> None:
        self.repository = repository

    def create_user_profile(
        self,
        *,
        display_name: str,
        default_currency: str = "EUR",
        locale: str = "es_ES",
    ) -> UserProfile:
        return self.repository.add_user_profile(
            display_name=display_name,
            default_currency=default_currency,
            locale=locale,
        )

    def create_account(
        self,
        *,
        user_profile_id: int,
        name: str,
        account_type: AccountType,
        institution_name: str | None = None,
        currency: str = "EUR",
        ownership_type: OwnershipType = OwnershipType.PERSONAL,
        external_account_ref: str | None = None,
    ) -> Account:
        return self.repository.add_account(
            user_profile_id=user_profile_id,
            name=name,
            account_type=account_type,
            institution_name=institution_name,
            currency=currency,
            ownership_type=ownership_type,
            external_account_ref=external_account_ref,
        )

    def create_category(
        self,
        *,
        user_profile_id: int,
        name: str,
        category_type: CategoryType,
        canonical_key: str,
        parent_category_id: int | None = None,
        display_order: int = 0,
    ) -> Category:
        return self.repository.add_category(
            user_profile_id=user_profile_id,
            name=name,
            category_type=category_type,
            canonical_key=canonical_key,
            parent_category_id=parent_category_id,
            display_order=display_order,
        )

    def record_manual_transaction(
        self,
        *,
        user_profile_id: int,
        account_id: int,
        transaction_date: date,
        description_clean: str,
        amount_minor: int,
        direction: Direction,
        transaction_type: TransactionType,
        decided_by: str,
        category_id: int | None = None,
        posted_date: date | None = None,
        description_raw: str | None = None,
        currency: str = "EUR",
        payment_method: PaymentMethod | None = None,
    ) -> Transaction:
        if not description_clean:
            raise ValueError("Manual transactions require description_clean.")
        if not decided_by:
            raise ValueError("Manual transactions require decided_by.")

        review_status = (
            TransactionReviewStatus.USER_CONFIRMED
            if category_id is not None
            else TransactionReviewStatus.PENDING_REVIEW
        )
        transaction = self.repository.add_transaction(
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
            source_type=TransactionSourceType.MANUAL,
        )
        self.repository.session.flush()

        self._record_manual_audit_source(
            user_profile_id=user_profile_id,
            account_id=account_id,
            transaction=transaction,
            decided_by=decided_by,
        )

        if category_id is not None:
            self.repository.add_classification_decision(
                transaction_id=transaction.id,
                category_id=category_id,
                transaction_type=transaction_type,
                payment_method=payment_method,
                decision_source=ClassificationDecisionSource.MANUAL_USER,
                decision_status=ClassificationDecisionStatus.ACCEPTED,
                confidence=Decimal("1.0000"),
                decided_by=decided_by,
            )

        return transaction

    def confirm_transaction_classification(
        self,
        *,
        user_profile_id: int,
        transaction_id: int,
        category_id: int,
        transaction_type: TransactionType,
        decided_by: str,
        payment_method: PaymentMethod | None = None,
        notes: str | None = None,
    ) -> ClassificationDecision:
        if not decided_by:
            raise ValueError("Classification confirmation requires decided_by.")

        transaction = self.repository.get_transaction(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        if transaction is None:
            raise ValueError("Transaction was not found for the user profile.")
        if not transaction.description_clean:
            raise ValueError("Confirmed transactions require description_clean.")

        superseded_at = utc_now()
        previous_decisions = self.repository.list_classification_decisions(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        for decision in previous_decisions:
            if decision.decision_status != ClassificationDecisionStatus.SUPERSEDED:
                decision.decision_status = ClassificationDecisionStatus.SUPERSEDED
                decision.superseded_at = superseded_at

        decision = self.repository.add_classification_decision(
            transaction_id=transaction_id,
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            decision_source=ClassificationDecisionSource.MANUAL_USER,
            decision_status=ClassificationDecisionStatus.ACCEPTED,
            confidence=Decimal("1.0000"),
            decided_by=decided_by,
            notes=notes,
        )

        transaction.category_id = category_id
        transaction.transaction_type = transaction_type
        if payment_method is not None:
            transaction.payment_method = payment_method
        transaction.review_status = TransactionReviewStatus.USER_CONFIRMED

        return decision

    def _record_manual_audit_source(
        self,
        *,
        user_profile_id: int,
        account_id: int,
        transaction: Transaction,
        decided_by: str,
    ) -> None:
        import_batch = self.repository.add_import_batch(
            user_profile_id=user_profile_id,
            account_id=account_id,
            source_system=ImportSourceSystem.MANUAL_ENTRY,
            imported_by=decided_by,
            notes="Manual transaction entry.",
        )
        self.repository.session.flush()
        self.repository.add_imported_transaction_source(
            import_batch_id=import_batch.id,
            import_action=ImportAction.CREATED_TRANSACTION,
            date_raw=transaction.transaction_date.isoformat(),
            description_raw=transaction.description_raw or transaction.description_clean,
            amount_raw=str(transaction.amount_minor),
            currency_raw=transaction.currency,
            created_transaction_id=transaction.id,
        )


__all__ = ["AccountingService"]
