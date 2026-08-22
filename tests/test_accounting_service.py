from datetime import date

import pytest
from sqlalchemy import func, select

from lxcell.db.models import (
    Base,
    ClassificationDecision,
    ImportBatch,
    ImportedTransactionSource,
)
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    ClassificationDecisionStatus,
    Direction,
    ImportSourceSystem,
    PaymentMethod,
    TransactionReviewStatus,
    TransactionType,
)
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def _create_profile_account_category(service: AccountingService):
    user_profile = service.create_user_profile(display_name="Sample User")
    service.repository.session.flush()
    account = service.create_account(
        user_profile_id=user_profile.id,
        name="Primary account",
        account_type=AccountType.CHECKING,
    )
    category = service.create_category(
        user_profile_id=user_profile.id,
        name="Category A",
        category_type=CategoryType.EXPENSE,
        canonical_key="category_a",
    )
    service.repository.session.flush()
    return user_profile, account, category


def test_manual_transaction_with_category_is_confirmed_and_audited(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, category = _create_profile_account_category(service)

        transaction = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=category.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            decided_by="Sample User",
        )

    with session_scope(session_factory) as session:
        stored_transaction = AccountingRepository(session).get_transaction(
            transaction_id=transaction.id,
            user_profile_id=user_profile.id,
        )
        import_batch = session.scalar(select(ImportBatch))
        imported_source = session.scalar(select(ImportedTransactionSource))
        decision = session.scalar(select(ClassificationDecision))

    assert stored_transaction is not None
    assert stored_transaction.review_status == TransactionReviewStatus.USER_CONFIRMED
    assert stored_transaction.category_id == category.id
    assert import_batch.source_system == ImportSourceSystem.MANUAL_ENTRY
    assert import_batch.imported_by == "Sample User"
    assert imported_source.created_transaction_id == transaction.id
    assert decision.decision_status == ClassificationDecisionStatus.ACCEPTED
    assert decision.decided_by == "Sample User"


def test_manual_transaction_without_category_remains_pending_but_audited(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, _category = _create_profile_account_category(service)

        transaction = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )

    with session_scope(session_factory) as session:
        stored_transaction = AccountingRepository(session).get_transaction(
            transaction_id=transaction.id,
            user_profile_id=user_profile.id,
        )
        decision_count = session.scalar(select(func.count(ClassificationDecision.id)))
        imported_source = session.scalar(select(ImportedTransactionSource))

    assert stored_transaction.review_status == TransactionReviewStatus.PENDING_REVIEW
    assert stored_transaction.category_id is None
    assert decision_count == 0
    assert imported_source.created_transaction_id == transaction.id


def test_confirm_transaction_classification_updates_transaction_and_supersedes_previous(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, category = _create_profile_account_category(service)
        second_category = service.create_category(
            user_profile_id=user_profile.id,
            name="Category B",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_b",
        )
        session.flush()

        transaction = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=category.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        session.flush()

        accepted_decision = service.confirm_transaction_classification(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            category_id=second_category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            decided_by="Sample User",
            notes="Manual correction.",
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        stored_transaction = repository.get_transaction(
            transaction_id=transaction.id,
            user_profile_id=user_profile.id,
        )
        decisions = repository.list_classification_decisions(
            transaction_id=transaction.id,
            user_profile_id=user_profile.id,
        )

    assert stored_transaction.category_id == second_category.id
    assert stored_transaction.payment_method == PaymentMethod.CARD
    assert stored_transaction.review_status == TransactionReviewStatus.USER_CONFIRMED
    assert len(decisions) == 2
    assert decisions[0].decision_status == ClassificationDecisionStatus.SUPERSEDED
    assert decisions[0].superseded_at is not None
    assert decisions[1].id == accepted_decision.id
    assert decisions[1].decision_status == ClassificationDecisionStatus.ACCEPTED


def test_service_rejects_manual_transaction_without_description(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, category = _create_profile_account_category(service)

        with pytest.raises(ValueError):
            service.record_manual_transaction(
                user_profile_id=user_profile.id,
                account_id=account.id,
                category_id=category.id,
                transaction_date=date(2026, 1, 10),
                description_clean="",
                amount_minor=1234,
                direction=Direction.OUTFLOW,
                transaction_type=TransactionType.EXPENSE,
                decided_by="Sample User",
            )
