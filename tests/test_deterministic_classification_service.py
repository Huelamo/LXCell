from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from lxcell.db.models import Base, ClassificationDecision, Transaction
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    ClassificationMatchField,
    ClassificationRuleType,
    Direction,
    PaymentMethod,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService, DeterministicClassificationService


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_deterministic_rule_auto_applies_high_confidence_match(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile, account, category = create_profile_account_category(service)
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A groceries",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            direction=Direction.OUTFLOW,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        transaction = add_imported_transaction(
            repository,
            user_profile_id=profile.id,
            account_id=account.id,
            description="Merchant-A Shop",
        )
        session.flush()

        result = DeterministicClassificationService(
            repository
        ).classify_and_record_transaction(
            user_profile_id=profile.id,
            transaction=transaction,
        )
        session.flush()

    with session_scope(session_factory) as session:
        stored_transaction = session.get(Transaction, transaction.id)
        decision = session.scalar(select(ClassificationDecision))

    assert result is not None
    assert result.decision_status == ClassificationDecisionStatus.ACCEPTED
    assert stored_transaction.category_id == category.id
    assert stored_transaction.transaction_type == TransactionType.EXPENSE
    assert stored_transaction.payment_method == PaymentMethod.CARD
    assert stored_transaction.review_status == TransactionReviewStatus.PENDING_REVIEW
    assert decision.decision_source == ClassificationDecisionSource.DETERMINISTIC_RULE
    assert decision.decision_status == ClassificationDecisionStatus.ACCEPTED
    assert decision.classification_rule_id == rule.id
    assert decision.category_id == category.id


def test_deterministic_rule_suggests_without_mutating_transaction(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile, account, category = create_profile_account_category(service)
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A suggestion",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            confidence=Decimal("0.9000"),
            auto_apply=False,
        )
        transaction = add_imported_transaction(
            repository,
            user_profile_id=profile.id,
            account_id=account.id,
            description="Merchant A",
        )
        session.flush()

        result = DeterministicClassificationService(
            repository
        ).classify_and_record_transaction(
            user_profile_id=profile.id,
            transaction=transaction,
        )
        session.flush()

    with session_scope(session_factory) as session:
        stored_transaction = session.get(Transaction, transaction.id)
        decision = session.scalar(select(ClassificationDecision))

    assert result is not None
    assert result.decision_status == ClassificationDecisionStatus.SUGGESTED
    assert stored_transaction.category_id is None
    assert decision.classification_rule_id == rule.id
    assert decision.category_id == category.id
    assert decision.decision_status == ClassificationDecisionStatus.SUGGESTED


def test_conflicting_deterministic_rules_do_not_classify(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile, account, category = create_profile_account_category(service)
        other_category = service.create_category(
            user_profile_id=profile.id,
            name="Category B",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_b",
        )
        service.create_classification_rule(
            user_profile_id=profile.id,
            name="First rule",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            confidence=Decimal("1.0000"),
            auto_apply=True,
        )
        service.create_classification_rule(
            user_profile_id=profile.id,
            name="Second rule",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=other_category.id,
            transaction_type=TransactionType.EXPENSE,
            confidence=Decimal("1.0000"),
            auto_apply=True,
        )
        transaction = add_imported_transaction(
            repository,
            user_profile_id=profile.id,
            account_id=account.id,
            description="Merchant A",
        )
        session.flush()

        result = DeterministicClassificationService(
            repository
        ).classify_and_record_transaction(
            user_profile_id=profile.id,
            transaction=transaction,
        )
        session.flush()

    with session_scope(session_factory) as session:
        stored_transaction = session.get(Transaction, transaction.id)
        decisions = list(session.scalars(select(ClassificationDecision)))

    assert result is None
    assert stored_transaction.category_id is None
    assert decisions == []


def test_inactive_categories_are_ignored_by_classifier(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile, account, category = create_profile_account_category(service)
        service.create_classification_rule(
            user_profile_id=profile.id,
            name="Inactive category rule",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            confidence=Decimal("1.0000"),
            auto_apply=True,
        )
        category.is_active = False
        transaction = add_imported_transaction(
            repository,
            user_profile_id=profile.id,
            account_id=account.id,
            description="Merchant A",
        )
        session.flush()

        result = DeterministicClassificationService(
            repository
        ).classify_and_record_transaction(
            user_profile_id=profile.id,
            transaction=transaction,
        )
        session.flush()

    with session_scope(session_factory) as session:
        stored_transaction = session.get(Transaction, transaction.id)

    assert result is None
    assert stored_transaction.category_id is None


def test_classification_rule_validation_blocks_invalid_regex(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile, _, category = create_profile_account_category(service)

        with pytest.raises(ValueError, match="regex pattern is invalid"):
            service.create_classification_rule(
                user_profile_id=profile.id,
                name="Invalid regex",
                rule_type=ClassificationRuleType.DESCRIPTION_REGEX,
                match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
                pattern="[",
                category_id=category.id,
            )


def test_classification_rule_validation_blocks_income_category_for_refund(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        income_category = service.create_category(
            user_profile_id=profile.id,
            name="Income Category",
            category_type=CategoryType.INCOME,
            canonical_key="income_category",
        )
        session.flush()

        with pytest.raises(ValueError, match="incompatible"):
            service.create_classification_rule(
                user_profile_id=profile.id,
                name="Refund rule",
                rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
                match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
                pattern="merchant a",
                category_id=income_category.id,
                transaction_type=TransactionType.REFUND,
            )


def create_profile_account_category(service: AccountingService):
    profile = service.create_user_profile(display_name="Sample User")
    service.repository.session.flush()
    account = service.create_account(
        user_profile_id=profile.id,
        name="Primary account",
        account_type=AccountType.CHECKING,
    )
    category = service.create_category(
        user_profile_id=profile.id,
        name="Category A",
        category_type=CategoryType.EXPENSE,
        canonical_key="category_a",
    )
    service.repository.session.flush()
    return profile, account, category


def add_imported_transaction(
    repository: AccountingRepository,
    *,
    user_profile_id: int,
    account_id: int,
    description: str,
) -> Transaction:
    return repository.add_transaction(
        user_profile_id=user_profile_id,
        account_id=account_id,
        transaction_date=date(2026, 1, 10),
        description_clean=description,
        description_raw=description,
        amount_minor=1234,
        direction=Direction.OUTFLOW,
        transaction_type=TransactionType.EXPENSE,
        source_type=TransactionSourceType.BANK_IMPORT,
        review_status=TransactionReviewStatus.PENDING_REVIEW,
    )
