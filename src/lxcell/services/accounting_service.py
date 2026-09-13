"""Accounting use cases and business workflows."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from lxcell.db.models import (
    Account,
    Category,
    ClassificationDecision,
    ClassificationRule,
    Counterparty,
    ReimbursementMatch,
    SharedExpenseAllocation,
    Transaction,
    UserProfile,
    utc_now,
)
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    ClassificationMatchField,
    ClassificationRuleType,
    Direction,
    ImportAction,
    ImportSourceSystem,
    OwnershipType,
    PaymentMethod,
    ReimbursementMatchStatus,
    SharedExpenseStatus,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.repositories import AccountingRepository
from lxcell.services.deterministic_classification_service import (
    category_type_is_compatible_with_transaction_type,
)


@dataclass(frozen=True)
class ReimbursementMatchSuggestionResult:
    """Summary of a reimbursement suggestion refresh."""

    created_count: int
    existing_count: int


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
        transactions_locked_until: date | None = None,
    ) -> UserProfile:
        return self.repository.add_user_profile(
            display_name=display_name,
            default_currency=default_currency,
            locale=locale,
            transactions_locked_until=transactions_locked_until,
        )

    def update_user_profile_transaction_lock(
        self,
        *,
        user_profile_id: int,
        transactions_locked_until: date | None,
    ) -> UserProfile:
        user_profile = self.repository.get_user_profile(user_profile_id)
        if user_profile is None:
            raise ValueError("User profile was not found.")
        user_profile.transactions_locked_until = transactions_locked_until
        return user_profile

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

    def update_category(
        self,
        *,
        user_profile_id: int,
        category_id: int,
        name: str,
        category_type: CategoryType,
        canonical_key: str,
        display_order: int,
        is_active: bool,
    ) -> Category:
        if not name:
            raise ValueError("Categories require a name.")
        if not canonical_key:
            raise ValueError("Categories require a canonical_key.")

        category = self.repository.get_category(
            category_id=category_id,
            user_profile_id=user_profile_id,
        )
        if category is None:
            raise ValueError("Category was not found for the user profile.")

        category.name = name
        category.category_type = category_type
        category.canonical_key = canonical_key
        category.display_order = display_order
        category.is_active = is_active
        return category

    def deactivate_category(
        self,
        *,
        user_profile_id: int,
        category_id: int,
    ) -> Category:
        category = self.repository.get_category(
            category_id=category_id,
            user_profile_id=user_profile_id,
        )
        if category is None:
            raise ValueError("Category was not found for the user profile.")
        category.is_active = False
        return category

    def create_classification_rule(
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
    ) -> ClassificationRule:
        if not name.strip():
            raise ValueError("Classification rules require a name.")
        if not pattern.strip():
            raise ValueError("Classification rules require a pattern.")
        if rule_type == ClassificationRuleType.DESCRIPTION_REGEX:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError("Classification rule regex pattern is invalid.") from exc
        if confidence < Decimal("0") or confidence > Decimal("1"):
            raise ValueError("Classification rule confidence must be between 0 and 1.")
        if (
            amount_min_minor is not None
            and amount_max_minor is not None
            and amount_min_minor > amount_max_minor
        ):
            raise ValueError("Classification rule amount range is invalid.")

        if category_id is not None:
            category = self.repository.get_category(
                category_id=category_id,
                user_profile_id=user_profile_id,
            )
            if category is None:
                raise ValueError("Category was not found for the user profile.")
            if not category.is_active:
                raise ValueError("Classification rules require an active category.")
            if transaction_type is not None and not (
                category_type_is_compatible_with_transaction_type(
                    category_type=category.category_type,
                    transaction_type=transaction_type,
                )
            ):
                raise ValueError(
                    "Classification rule category and transaction type are incompatible."
                )

        return self.repository.add_classification_rule(
            user_profile_id=user_profile_id,
            name=name.strip(),
            rule_type=rule_type,
            match_field=match_field,
            pattern=pattern.strip(),
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            direction=direction,
            amount_min_minor=amount_min_minor,
            amount_max_minor=amount_max_minor,
            priority=priority,
            confidence=confidence,
            auto_apply=auto_apply,
        )

    def update_classification_rule(
        self,
        *,
        user_profile_id: int,
        classification_rule_id: int,
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
        self._validate_classification_rule_payload(
            user_profile_id=user_profile_id,
            name=name,
            rule_type=rule_type,
            pattern=pattern,
            category_id=category_id,
            transaction_type=transaction_type,
            amount_min_minor=amount_min_minor,
            amount_max_minor=amount_max_minor,
            confidence=confidence,
            is_active=is_active,
        )
        rule = self.repository.get_classification_rule(
            classification_rule_id=classification_rule_id,
            user_profile_id=user_profile_id,
        )
        if rule is None:
            raise ValueError("Classification rule was not found for the user profile.")

        rule.name = name.strip()
        rule.rule_type = rule_type
        rule.match_field = match_field
        rule.pattern = pattern.strip()
        rule.category_id = category_id
        rule.transaction_type = transaction_type
        rule.payment_method = payment_method
        rule.direction = direction
        rule.amount_min_minor = amount_min_minor
        rule.amount_max_minor = amount_max_minor
        rule.priority = priority
        rule.confidence = confidence
        rule.auto_apply = auto_apply
        rule.is_active = is_active
        return rule

    def _validate_classification_rule_payload(
        self,
        *,
        user_profile_id: int,
        name: str,
        rule_type: ClassificationRuleType,
        pattern: str,
        category_id: int | None,
        transaction_type: TransactionType | None,
        amount_min_minor: int | None,
        amount_max_minor: int | None,
        confidence: Decimal,
        is_active: bool = True,
    ) -> None:
        if not name.strip():
            raise ValueError("Classification rules require a name.")
        if not pattern.strip():
            raise ValueError("Classification rules require a pattern.")
        if rule_type == ClassificationRuleType.DESCRIPTION_REGEX:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError("Classification rule regex pattern is invalid.") from exc
        if confidence < Decimal("0") or confidence > Decimal("1"):
            raise ValueError("Classification rule confidence must be between 0 and 1.")
        if (
            amount_min_minor is not None
            and amount_max_minor is not None
            and amount_min_minor > amount_max_minor
        ):
            raise ValueError("Classification rule amount range is invalid.")

        if category_id is None:
            return

        category = self.repository.get_category(
            category_id=category_id,
            user_profile_id=user_profile_id,
        )
        if category is None:
            raise ValueError("Category was not found for the user profile.")
        if is_active and not category.is_active:
            raise ValueError("Active classification rules require an active category.")
        if transaction_type is not None and not (
            category_type_is_compatible_with_transaction_type(
                category_type=category.category_type,
                transaction_type=transaction_type,
            )
        ):
            raise ValueError(
                "Classification rule category and transaction type are incompatible."
            )

    def hard_delete_classification_rule(
        self,
        *,
        user_profile_id: int,
        classification_rule_id: int,
    ) -> int:
        rule = self.repository.get_classification_rule(
            classification_rule_id=classification_rule_id,
            user_profile_id=user_profile_id,
        )
        if rule is None:
            raise ValueError("Classification rule was not found for the user profile.")
        unlinked_decision_count = (
            self.repository.unlink_classification_decisions_from_rule(
                classification_rule_id=classification_rule_id,
                user_profile_id=user_profile_id,
            )
        )
        self.repository.delete_classification_rule(rule)
        return unlinked_decision_count

    def create_counterparty(
        self,
        *,
        user_profile_id: int,
        display_name: str,
        aliases_raw: str | None = None,
    ) -> Counterparty:
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("Counterparties require a display_name.")
        return self.repository.add_counterparty(
            user_profile_id=user_profile_id,
            display_name=display_name,
            normalized_name=normalized_counterparty_name(display_name),
            aliases_raw=aliases_raw.strip() if aliases_raw else None,
        )

    def mark_transaction_shared_50_50(
        self,
        *,
        user_profile_id: int,
        transaction_id: int,
        counterparty_id: int,
        decided_by: str,
        allow_locked_period_override: bool = False,
    ) -> SharedExpenseAllocation:
        if not decided_by:
            raise ValueError("Shared expense allocation requires decided_by.")

        transaction = self.repository.get_transaction(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        if transaction is None:
            raise ValueError("Transaction was not found for the user profile.")
        if transaction.is_deleted:
            raise ValueError("Deleted transactions cannot be shared.")
        if transaction.direction != Direction.OUTFLOW:
            raise ValueError("Only outflow transactions can be marked as shared.")
        if transaction.amount_minor <= 0:
            raise ValueError("Shared expenses require a positive amount.")

        counterparty = self.repository.get_counterparty(
            counterparty_id=counterparty_id,
            user_profile_id=user_profile_id,
        )
        if counterparty is None:
            raise ValueError("Counterparty was not found for the user profile.")
        if not counterparty.is_active:
            raise ValueError("Inactive counterparties cannot be used for new shares.")

        self._ensure_transaction_period_unlocked(
            user_profile_id=user_profile_id,
            transaction_dates=[transaction.transaction_date],
            allow_locked_period_override=allow_locked_period_override,
        )

        recoverable_share_minor = transaction.amount_minor // 2
        personal_share_minor = transaction.amount_minor - recoverable_share_minor

        allocation = self.repository.get_shared_expense_allocation_for_transaction(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        if allocation is None:
            allocation = self.repository.add_shared_expense_allocation(
                user_profile_id=user_profile_id,
                transaction_id=transaction_id,
                counterparty_id=counterparty_id,
                personal_share_minor=personal_share_minor,
                recoverable_share_minor=recoverable_share_minor,
                share_ratio_basis_points=5000,
                status=SharedExpenseStatus.PENDING,
                decided_by=decided_by,
                notes="Shared expense marked as 50/50.",
            )
            self.repository.session.flush()
            return allocation

        allocation.counterparty_id = counterparty_id
        allocation.personal_share_minor = personal_share_minor
        allocation.recoverable_share_minor = recoverable_share_minor
        allocation.share_ratio_basis_points = 5000
        allocation.status = SharedExpenseStatus.PENDING
        allocation.decided_by = decided_by
        allocation.decided_at = utc_now()
        allocation.notes = "Shared expense marked as 50/50."
        return allocation

    def waive_shared_expense_allocation(
        self,
        *,
        user_profile_id: int,
        transaction_id: int,
        decided_by: str,
        allow_locked_period_override: bool = False,
    ) -> SharedExpenseAllocation:
        if not decided_by:
            raise ValueError("Shared expense allocation waiver requires decided_by.")

        transaction = self.repository.get_transaction(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        if transaction is None:
            raise ValueError("Transaction was not found for the user profile.")

        self._ensure_transaction_period_unlocked(
            user_profile_id=user_profile_id,
            transaction_dates=[transaction.transaction_date],
            allow_locked_period_override=allow_locked_period_override,
        )

        allocation = self.repository.get_shared_expense_allocation_for_transaction(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        if allocation is None:
            raise ValueError("Shared expense allocation was not found.")

        allocation.status = SharedExpenseStatus.WAIVED
        allocation.decided_by = decided_by
        allocation.decided_at = utc_now()
        allocation.notes = "Shared expense allocation waived."
        return allocation

    def refresh_reimbursement_match_suggestions(
        self,
        *,
        user_profile_id: int,
        decided_by: str = "system",
    ) -> ReimbursementMatchSuggestionResult:
        if not decided_by:
            raise ValueError("Reimbursement suggestions require decided_by.")

        allocations = [
            allocation
            for allocation in self.repository.list_shared_expense_allocations_for_profile(
                user_profile_id=user_profile_id
            )
            if allocation.status
            in {
                SharedExpenseStatus.PENDING,
                SharedExpenseStatus.PARTIALLY_REIMBURSED,
            }
        ]
        transactions = self.repository.list_transactions(user_profile_id=user_profile_id)
        inflows = [
            transaction
            for transaction in transactions
            if transaction.direction == Direction.INFLOW
            and transaction.amount_minor > 0
            and transaction.review_status != TransactionReviewStatus.IGNORED
            and self.repository.get_confirmed_reimbursement_match_for_transaction(
                user_profile_id=user_profile_id,
                reimbursement_transaction_id=transaction.id,
            )
            is None
        ]

        created_count = 0
        existing_count = 0
        for allocation in allocations:
            outstanding_minor = self._shared_expense_outstanding_minor(allocation)
            if outstanding_minor <= 0:
                continue
            for transaction in inflows:
                suggestion = self._reimbursement_suggestion_for_pair(
                    allocation=allocation,
                    reimbursement_transaction=transaction,
                    outstanding_minor=outstanding_minor,
                )
                if suggestion is None:
                    continue
                matched_amount_minor, confidence, notes = suggestion
                existing_match = self.repository.get_reimbursement_match_for_pair(
                    user_profile_id=user_profile_id,
                    shared_expense_allocation_id=allocation.id,
                    reimbursement_transaction_id=transaction.id,
                )
                if existing_match is None:
                    self.repository.add_reimbursement_match(
                        user_profile_id=user_profile_id,
                        shared_expense_allocation_id=allocation.id,
                        reimbursement_transaction_id=transaction.id,
                        matched_amount_minor=matched_amount_minor,
                        status=ReimbursementMatchStatus.SUGGESTED,
                        confidence=confidence,
                        decided_by=decided_by,
                        notes=notes,
                    )
                    created_count += 1
                elif existing_match.status == ReimbursementMatchStatus.SUGGESTED:
                    existing_match.matched_amount_minor = matched_amount_minor
                    existing_match.confidence = confidence
                    existing_match.decided_by = decided_by
                    existing_match.decided_at = utc_now()
                    existing_match.notes = notes
                    existing_count += 1
        if created_count or existing_count:
            self.repository.session.flush()

        return ReimbursementMatchSuggestionResult(
            created_count=created_count,
            existing_count=existing_count,
        )

    def confirm_reimbursement_match(
        self,
        *,
        user_profile_id: int,
        reimbursement_match_id: int,
        decided_by: str,
        allow_locked_period_override: bool = False,
    ) -> ReimbursementMatch:
        if not decided_by:
            raise ValueError("Reimbursement confirmation requires decided_by.")
        reimbursement_match = self.repository.get_reimbursement_match(
            reimbursement_match_id=reimbursement_match_id,
            user_profile_id=user_profile_id,
        )
        if reimbursement_match is None:
            raise ValueError("Reimbursement match was not found for the user profile.")
        if reimbursement_match.status == ReimbursementMatchStatus.REJECTED:
            raise ValueError("Rejected reimbursement matches cannot be confirmed.")

        allocation = reimbursement_match.shared_expense_allocation
        self.repository.session.expire(allocation, ["reimbursement_matches"])
        reimbursement_transaction = reimbursement_match.reimbursement_transaction
        self._validate_reimbursement_match(
            reimbursement_match,
            allocation=allocation,
            reimbursement_transaction=reimbursement_transaction,
        )
        existing_confirmed = (
            self.repository.get_confirmed_reimbursement_match_for_transaction(
                user_profile_id=user_profile_id,
                reimbursement_transaction_id=reimbursement_transaction.id,
            )
        )
        if (
            existing_confirmed is not None
            and existing_confirmed.id != reimbursement_match.id
        ):
            raise ValueError(
                "Reimbursement transaction is already linked to another shared expense."
            )

        self._ensure_transaction_period_unlocked(
            user_profile_id=user_profile_id,
            transaction_dates=[
                allocation.transaction.transaction_date,
                reimbursement_transaction.transaction_date,
            ],
            allow_locked_period_override=allow_locked_period_override,
        )

        reimbursement_match.status = ReimbursementMatchStatus.CONFIRMED
        reimbursement_match.decided_by = decided_by
        reimbursement_match.decided_at = utc_now()
        self._update_shared_expense_status_from_confirmed_matches(allocation)
        return reimbursement_match

    def reject_reimbursement_match(
        self,
        *,
        user_profile_id: int,
        reimbursement_match_id: int,
        decided_by: str,
        allow_locked_period_override: bool = False,
    ) -> ReimbursementMatch:
        if not decided_by:
            raise ValueError("Reimbursement rejection requires decided_by.")
        reimbursement_match = self.repository.get_reimbursement_match(
            reimbursement_match_id=reimbursement_match_id,
            user_profile_id=user_profile_id,
        )
        if reimbursement_match is None:
            raise ValueError("Reimbursement match was not found for the user profile.")
        if reimbursement_match.status == ReimbursementMatchStatus.CONFIRMED:
            raise ValueError("Confirmed reimbursement matches cannot be rejected.")

        allocation = reimbursement_match.shared_expense_allocation
        reimbursement_transaction = reimbursement_match.reimbursement_transaction
        self._ensure_transaction_period_unlocked(
            user_profile_id=user_profile_id,
            transaction_dates=[
                allocation.transaction.transaction_date,
                reimbursement_transaction.transaction_date,
            ],
            allow_locked_period_override=allow_locked_period_override,
        )

        reimbursement_match.status = ReimbursementMatchStatus.REJECTED
        reimbursement_match.decided_by = decided_by
        reimbursement_match.decided_at = utc_now()
        return reimbursement_match

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
        allow_locked_period_override: bool = False,
    ) -> Transaction:
        if not description_clean:
            raise ValueError("Manual transactions require description_clean.")
        if not decided_by:
            raise ValueError("Manual transactions require decided_by.")
        self._ensure_transaction_period_unlocked(
            user_profile_id=user_profile_id,
            transaction_dates=[transaction_date],
            allow_locked_period_override=allow_locked_period_override,
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
            review_status=TransactionReviewStatus.USER_CONFIRMED,
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
        category_id: int | None,
        transaction_type: TransactionType,
        decided_by: str,
        payment_method: PaymentMethod | None = None,
        notes: str | None = None,
        allow_locked_period_override: bool = False,
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
        if transaction.is_deleted:
            raise ValueError("Deleted transactions cannot be reviewed.")
        if category_id is not None:
            category = self.repository.get_category(
                category_id=category_id,
                user_profile_id=user_profile_id,
            )
            if category is None:
                raise ValueError("Category was not found for the user profile.")
            if not category.is_active:
                raise ValueError("Transaction review requires an active category.")
            if not category_type_is_compatible_with_transaction_type(
                category_type=category.category_type,
                transaction_type=transaction_type,
            ):
                raise ValueError(
                    "Transaction review category and transaction type are incompatible."
                )
        self._ensure_transaction_period_unlocked(
            user_profile_id=user_profile_id,
            transaction_dates=[transaction.transaction_date],
            allow_locked_period_override=allow_locked_period_override,
        )

        self._supersede_classification_decisions(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )

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

    def update_manual_transaction(
        self,
        *,
        user_profile_id: int,
        transaction_id: int,
        account_id: int,
        transaction_date: date,
        description_clean: str,
        amount_minor: int,
        direction: Direction,
        transaction_type: TransactionType,
        decided_by: str,
        category_id: int | None = None,
        payment_method: PaymentMethod | None = None,
        allow_locked_period_override: bool = False,
    ) -> Transaction:
        if not description_clean:
            raise ValueError("Manual transactions require description_clean.")
        if not decided_by:
            raise ValueError("Manual transaction edits require decided_by.")

        transaction = self.repository.get_transaction(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        if transaction is None:
            raise ValueError("Transaction was not found for the user profile.")
        if transaction.is_deleted:
            raise ValueError("Deleted transactions cannot be edited.")
        self._ensure_transaction_period_unlocked(
            user_profile_id=user_profile_id,
            transaction_dates=[transaction.transaction_date, transaction_date],
            allow_locked_period_override=allow_locked_period_override,
        )

        classification_changed = (
            transaction.category_id != category_id
            or transaction.transaction_type != transaction_type
            or transaction.payment_method != payment_method
        )

        transaction.account_id = account_id
        transaction.transaction_date = transaction_date
        transaction.description_clean = description_clean
        transaction.amount_minor = amount_minor
        transaction.direction = direction
        transaction.transaction_type = transaction_type
        transaction.payment_method = payment_method
        transaction.category_id = category_id
        transaction.review_status = TransactionReviewStatus.USER_CONFIRMED

        if classification_changed:
            self._supersede_classification_decisions(
                transaction_id=transaction_id,
                user_profile_id=user_profile_id,
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
                    notes="Manual transaction edit.",
                )

        return transaction

    def soft_delete_transaction(
        self,
        *,
        user_profile_id: int,
        transaction_id: int,
        decided_by: str,
        allow_locked_period_override: bool = False,
    ) -> Transaction:
        if not decided_by:
            raise ValueError("Manual transaction deletion requires decided_by.")

        transaction = self.repository.get_transaction(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        if transaction is None:
            raise ValueError("Transaction was not found for the user profile.")
        self._ensure_transaction_period_unlocked(
            user_profile_id=user_profile_id,
            transaction_dates=[transaction.transaction_date],
            allow_locked_period_override=allow_locked_period_override,
        )
        transaction.is_deleted = True
        return transaction

    def _ensure_transaction_period_unlocked(
        self,
        *,
        user_profile_id: int,
        transaction_dates: list[date],
        allow_locked_period_override: bool = False,
    ) -> None:
        if allow_locked_period_override:
            return
        user_profile = self.repository.get_user_profile(user_profile_id)
        if user_profile is None:
            raise ValueError("User profile was not found.")
        if protected_transaction_dates(user_profile, transaction_dates):
            raise ValueError(
                "La fecha esta en un periodo protegido. "
                "Confirma el permiso adicional para continuar."
            )

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

    def _supersede_classification_decisions(
        self, *, transaction_id: int, user_profile_id: int
    ) -> None:
        superseded_at = utc_now()
        previous_decisions = self.repository.list_classification_decisions(
            transaction_id=transaction_id,
            user_profile_id=user_profile_id,
        )
        for decision in previous_decisions:
            if decision.decision_status != ClassificationDecisionStatus.SUPERSEDED:
                decision.decision_status = ClassificationDecisionStatus.SUPERSEDED
                decision.superseded_at = superseded_at

    @staticmethod
    def _shared_expense_outstanding_minor(
        allocation: SharedExpenseAllocation,
    ) -> int:
        confirmed_matches = [
            reimbursement_match
            for reimbursement_match in allocation.reimbursement_matches
            if reimbursement_match.status == ReimbursementMatchStatus.CONFIRMED
        ]
        reimbursed_minor = sum(
            reimbursement_match.matched_amount_minor
            for reimbursement_match in confirmed_matches
        )
        return allocation.recoverable_share_minor - reimbursed_minor

    def _reimbursement_suggestion_for_pair(
        self,
        *,
        allocation: SharedExpenseAllocation,
        reimbursement_transaction: Transaction,
        outstanding_minor: int,
    ) -> tuple[int, Decimal, str] | None:
        if reimbursement_transaction.transaction_date < allocation.transaction.transaction_date:
            return None
        if reimbursement_transaction.amount_minor > outstanding_minor:
            return None

        counterparty_tokens = normalized_counterparty_tokens(allocation.counterparty)
        transaction_text = normalized_match_text(
            " ".join(
                part
                for part in [
                    reimbursement_transaction.description_clean,
                    reimbursement_transaction.description_raw,
                ]
                if part
            )
        )
        if not counterparty_tokens or not any(
            token in transaction_text for token in counterparty_tokens
        ):
            return None

        if reimbursement_transaction.amount_minor == outstanding_minor:
            return (
                reimbursement_transaction.amount_minor,
                Decimal("0.9500"),
                "Alias de contraparte e importe recuperable exacto.",
            )
        return (
            reimbursement_transaction.amount_minor,
            Decimal("0.8000"),
            "Alias de contraparte e importe compatible con reembolso parcial.",
        )

    @staticmethod
    def _validate_reimbursement_match(
        reimbursement_match: ReimbursementMatch,
        *,
        allocation: SharedExpenseAllocation,
        reimbursement_transaction: Transaction,
    ) -> None:
        if allocation.status == SharedExpenseStatus.WAIVED:
            raise ValueError("Waived shared expenses cannot receive reimbursements.")
        if reimbursement_transaction.is_deleted:
            raise ValueError("Deleted transactions cannot be used as reimbursements.")
        if reimbursement_transaction.review_status == TransactionReviewStatus.IGNORED:
            raise ValueError("Ignored transactions cannot be used as reimbursements.")
        if reimbursement_transaction.direction != Direction.INFLOW:
            raise ValueError("Only inflow transactions can be used as reimbursements.")
        if reimbursement_match.matched_amount_minor > reimbursement_transaction.amount_minor:
            raise ValueError("Matched amount cannot exceed the reimbursement transaction.")
        confirmed_minor = sum(
            match.matched_amount_minor
            for match in allocation.reimbursement_matches
            if match.status == ReimbursementMatchStatus.CONFIRMED
            and match.id != reimbursement_match.id
        )
        if confirmed_minor + reimbursement_match.matched_amount_minor > (
            allocation.recoverable_share_minor
        ):
            raise ValueError("Matched amount exceeds the recoverable shared expense amount.")

    @staticmethod
    def _update_shared_expense_status_from_confirmed_matches(
        allocation: SharedExpenseAllocation,
    ) -> None:
        confirmed_minor = sum(
            reimbursement_match.matched_amount_minor
            for reimbursement_match in allocation.reimbursement_matches
            if reimbursement_match.status == ReimbursementMatchStatus.CONFIRMED
        )
        if confirmed_minor >= allocation.recoverable_share_minor:
            allocation.status = SharedExpenseStatus.REIMBURSED
        elif confirmed_minor > 0:
            allocation.status = SharedExpenseStatus.PARTIALLY_REIMBURSED
        else:
            allocation.status = SharedExpenseStatus.PENDING


def protected_transaction_dates(
    user_profile: UserProfile,
    transaction_dates: list[date],
) -> list[date]:
    locked_until = user_profile.transactions_locked_until
    if locked_until is None:
        return []
    return [
        transaction_date
        for transaction_date in transaction_dates
        if transaction_date <= locked_until
    ]


def normalized_counterparty_name(display_name: str) -> str:
    normalized = unicodedata.normalize("NFKD", display_name.strip())
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    normalized_name = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name).strip("_").lower()
    if not normalized_name:
        raise ValueError("Counterparty display_name must produce a normalized name.")
    return normalized_name


def normalized_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", " ", ascii_name).strip().lower()


def normalized_counterparty_tokens(counterparty: Counterparty) -> set[str]:
    candidates = [counterparty.display_name]
    if counterparty.aliases_raw:
        candidates.extend(counterparty.aliases_raw.splitlines())
    return {
        normalized
        for candidate in candidates
        if (normalized := normalized_match_text(candidate))
    }


__all__ = [
    "AccountingService",
    "ReimbursementMatchSuggestionResult",
    "normalized_counterparty_name",
    "normalized_counterparty_tokens",
    "normalized_match_text",
    "protected_transaction_dates",
]
