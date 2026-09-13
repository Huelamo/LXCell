"""Deterministic transaction classification workflows."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from lxcell.db.models import ClassificationRule, Transaction
from lxcell.enums.core_enums import (
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    ClassificationMatchField,
    ClassificationRuleType,
    Direction,
    PaymentMethod,
    TransactionType,
)
from lxcell.repositories import AccountingRepository

AUTO_APPLY_CONFIDENCE_THRESHOLD = Decimal("0.9500")
SUGGESTION_CONFIDENCE_THRESHOLD = Decimal("0.7000")


@dataclass(frozen=True)
class ClassificationResult:
    """Classification outcome for one transaction."""

    category_id: int | None
    transaction_type: TransactionType | None
    payment_method: PaymentMethod | None
    decision_source: ClassificationDecisionSource
    decision_status: ClassificationDecisionStatus
    classification_rule_id: int | None
    confidence: Decimal
    notes: str

    @property
    def should_apply_to_transaction(self) -> bool:
        return self.decision_status == ClassificationDecisionStatus.ACCEPTED


class DeterministicClassificationService:
    """Apply conservative deterministic classification rules."""

    def __init__(self, repository: AccountingRepository) -> None:
        self.repository = repository

    def classify_transaction(
        self,
        *,
        user_profile_id: int,
        transaction: Transaction,
    ) -> ClassificationResult | None:
        rule_matches = [
            rule
            for rule in self.repository.list_classification_rules(
                user_profile_id=user_profile_id
            )
            if self._rule_matches_transaction(rule, transaction)
        ]
        if not rule_matches:
            return None

        non_conflicting_matches = self._non_conflicting_matches(rule_matches)
        if not non_conflicting_matches:
            return None

        auto_apply_matches = [
            rule
            for rule in non_conflicting_matches
            if rule.auto_apply and rule.confidence >= AUTO_APPLY_CONFIDENCE_THRESHOLD
        ]
        if auto_apply_matches:
            return self._result_from_rule(
                auto_apply_matches[0],
                transaction=transaction,
                decision_status=ClassificationDecisionStatus.ACCEPTED,
                notes="Deterministic auto classification.",
            )

        suggested_matches = [
            rule
            for rule in non_conflicting_matches
            if rule.confidence >= SUGGESTION_CONFIDENCE_THRESHOLD
        ]
        if suggested_matches:
            return self._result_from_rule(
                suggested_matches[0],
                transaction=transaction,
                decision_status=ClassificationDecisionStatus.SUGGESTED,
                notes="Deterministic classification suggestion.",
            )
        return None

    def classify_and_record_transaction(
        self,
        *,
        user_profile_id: int,
        transaction: Transaction,
        decided_by: str = "system",
    ) -> ClassificationResult | None:
        result = self.classify_transaction(
            user_profile_id=user_profile_id,
            transaction=transaction,
        )
        if result is None:
            return None

        if result.should_apply_to_transaction:
            transaction.category_id = result.category_id
            if result.transaction_type is not None:
                transaction.transaction_type = result.transaction_type
            if result.payment_method is not None:
                transaction.payment_method = result.payment_method

        self.repository.add_classification_decision(
            transaction_id=transaction.id,
            category_id=result.category_id,
            transaction_type=result.transaction_type,
            payment_method=result.payment_method,
            classification_rule_id=result.classification_rule_id,
            confidence=result.confidence,
            decision_source=result.decision_source,
            decision_status=result.decision_status,
            decided_by=decided_by,
            notes=result.notes,
        )
        return result

    def _rule_matches_transaction(
        self,
        rule: ClassificationRule,
        transaction: Transaction,
    ) -> bool:
        if rule.category is not None:
            if not rule.category.is_active:
                return False
            if not category_type_is_compatible_with_transaction_type(
                category_type=rule.category.category_type,
                transaction_type=rule.transaction_type or transaction.transaction_type,
            ):
                return False
        if not self._rule_direction_matches(rule, transaction):
            return False
        if rule.amount_min_minor is not None and transaction.amount_minor < rule.amount_min_minor:
            return False
        if rule.amount_max_minor is not None and transaction.amount_minor > rule.amount_max_minor:
            return False

        value = self._transaction_match_value(rule, transaction)
        if not value:
            return False

        if rule.rule_type == ClassificationRuleType.DESCRIPTION_REGEX:
            try:
                return re.search(rule.pattern, value, flags=re.IGNORECASE) is not None
            except re.error:
                return False
        if rule.rule_type in {
            ClassificationRuleType.DESCRIPTION_CONTAINS,
            ClassificationRuleType.AMOUNT_AND_DESCRIPTION,
            ClassificationRuleType.RECURRING_TRANSACTION,
        }:
            return normalize_classification_text(rule.pattern) in value
        return False

    @staticmethod
    def _rule_direction_matches(
        rule: ClassificationRule,
        transaction: Transaction,
    ) -> bool:
        if rule.direction is not None and rule.direction != transaction.direction:
            return False
        if rule.transaction_type is None:
            return True
        if transaction.direction == Direction.OUTFLOW:
            return rule.transaction_type in {
                TransactionType.EXPENSE,
                TransactionType.FEE,
                TransactionType.TAX,
                TransactionType.SAVING,
                TransactionType.INVESTMENT,
                TransactionType.DEBT_PAYMENT,
                TransactionType.TRANSFER,
            }
        if transaction.direction == Direction.INFLOW:
            return rule.transaction_type in {
                TransactionType.INCOME,
                TransactionType.REFUND,
                TransactionType.TRANSFER,
                TransactionType.ADJUSTMENT,
            }
        return True

    @staticmethod
    def _transaction_match_value(
        rule: ClassificationRule,
        transaction: Transaction,
    ) -> str:
        if rule.match_field == ClassificationMatchField.DESCRIPTION_RAW:
            return normalize_classification_text(transaction.description_raw or "")
        return normalize_classification_text(transaction.description_clean or "")

    @staticmethod
    def _non_conflicting_matches(
        matches: list[ClassificationRule],
    ) -> list[ClassificationRule]:
        targets = {
            (
                rule.category_id,
                rule.transaction_type,
                rule.payment_method,
            )
            for rule in matches
        }
        if len(targets) != 1:
            return []
        return sorted(
            matches,
            key=lambda rule: (
                rule.priority,
                -rule.confidence,
                rule.id,
            ),
        )

    @staticmethod
    def _result_from_rule(
        rule: ClassificationRule,
        *,
        transaction: Transaction,
        decision_status: ClassificationDecisionStatus,
        notes: str,
    ) -> ClassificationResult:
        transaction_type = rule.transaction_type or transaction.transaction_type
        payment_method = rule.payment_method or transaction.payment_method
        return ClassificationResult(
            category_id=rule.category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            decision_source=ClassificationDecisionSource.DETERMINISTIC_RULE,
            decision_status=decision_status,
            classification_rule_id=rule.id,
            confidence=rule.confidence,
            notes=notes,
        )


def normalize_classification_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    alphanumeric_value = re.sub(r"[^a-zA-Z0-9]+", " ", ascii_value)
    return re.sub(r"\s+", " ", alphanumeric_value).strip().lower()


def category_type_is_compatible_with_transaction_type(
    *,
    category_type: CategoryType,
    transaction_type: TransactionType,
) -> bool:
    if category_type == CategoryType.INCOME:
        return transaction_type == TransactionType.INCOME
    if category_type == CategoryType.EXPENSE:
        return transaction_type in {
            TransactionType.EXPENSE,
            TransactionType.FEE,
            TransactionType.TAX,
            TransactionType.REFUND,
        }
    if category_type == CategoryType.TRANSFER:
        return transaction_type == TransactionType.TRANSFER
    if category_type == CategoryType.ADJUSTMENT:
        return transaction_type == TransactionType.ADJUSTMENT
    if category_type == CategoryType.SAVING:
        return transaction_type == TransactionType.SAVING
    if category_type == CategoryType.INVESTMENT:
        return transaction_type == TransactionType.INVESTMENT
    if category_type == CategoryType.DEBT:
        return transaction_type == TransactionType.DEBT_PAYMENT
    return False


__all__ = [
    "AUTO_APPLY_CONFIDENCE_THRESHOLD",
    "ClassificationResult",
    "DeterministicClassificationService",
    "SUGGESTION_CONFIDENCE_THRESHOLD",
    "category_type_is_compatible_with_transaction_type",
    "normalize_classification_text",
]
