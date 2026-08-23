from datetime import date
from decimal import Decimal

import pytest

from lxcell.db.models import Base
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import (
    AccountType,
    BudgetPeriodType,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    Direction,
    ImportSourceSystem,
    ImportStatus,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.repositories import AccountingRepository


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_repository_creates_core_records_inside_session_scope(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        user_profile = repository.add_user_profile(display_name="Sample User")
        session.flush()

        account = repository.add_account(
            user_profile_id=user_profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        category = repository.add_category(
            user_profile_id=user_profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()

        transaction = repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=category.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        stored_transaction = repository.get_transaction(
            transaction_id=transaction.id,
            user_profile_id=user_profile.id,
        )

    assert stored_transaction is not None
    assert stored_transaction.description_clean == "Merchant A"
    assert stored_transaction.amount_minor == 1234


def test_repository_lists_are_scoped_to_user_profile(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        first_profile = repository.add_user_profile(display_name="Sample User A")
        second_profile = repository.add_user_profile(display_name="Sample User B")
        session.flush()

        first_account = repository.add_account(
            user_profile_id=first_profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        repository.add_account(
            user_profile_id=second_profile.id,
            name="Other account",
            account_type=AccountType.CHECKING,
        )
        first_category = repository.add_category(
            user_profile_id=first_profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        repository.add_category(
            user_profile_id=second_profile.id,
            name="Category B",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_b",
        )
        session.flush()

        repository.add_transaction(
            user_profile_id=first_profile.id,
            account_id=first_account.id,
            category_id=first_category.id,
            transaction_date=date(2026, 1, 10),
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        accounts = repository.list_accounts(first_profile.id)
        categories = repository.list_categories(first_profile.id)
        transactions = repository.list_transactions(user_profile_id=first_profile.id)

    assert [account.name for account in accounts] == ["Primary account"]
    assert [category.name for category in categories] == ["Category A"]
    assert len(transactions) == 1


def test_repository_list_filters_default_to_active_and_not_deleted(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        user_profile = repository.add_user_profile(display_name="Sample User")
        session.flush()

        active_account = repository.add_account(
            user_profile_id=user_profile.id,
            name="Active account",
            account_type=AccountType.CHECKING,
        )
        repository.add_account(
            user_profile_id=user_profile.id,
            name="Inactive account",
            account_type=AccountType.CHECKING,
            is_active=False,
        )
        category = repository.add_category(
            user_profile_id=user_profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()

        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=active_account.id,
            category_id=category.id,
            transaction_date=date(2026, 1, 10),
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=active_account.id,
            category_id=category.id,
            transaction_date=date(2026, 1, 11),
            amount_minor=2000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
            is_deleted=True,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        active_accounts = repository.list_accounts(user_profile.id)
        all_accounts = repository.list_accounts(
            user_profile.id, include_inactive=True
        )
        visible_transactions = repository.list_transactions(
            user_profile_id=user_profile.id
        )
        all_transactions = repository.list_transactions(
            user_profile_id=user_profile.id, include_deleted=True
        )

    assert [account.name for account in active_accounts] == ["Active account"]
    assert [account.name for account in all_accounts] == [
        "Active account",
        "Inactive account",
    ]
    assert [transaction.amount_minor for transaction in visible_transactions] == [1000]
    assert [transaction.amount_minor for transaction in all_transactions] == [1000, 2000]


def test_repository_filters_transactions_by_date_and_review_status(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        user_profile = repository.add_user_profile(display_name="Sample User")
        session.flush()

        account = repository.add_account(
            user_profile_id=user_profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 2, 10),
            description_clean="Merchant A",
            amount_minor=2000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            review_status=TransactionReviewStatus.USER_CONFIRMED,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        transactions = repository.list_transactions(
            user_profile_id=user_profile.id,
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 28),
            review_status=TransactionReviewStatus.USER_CONFIRMED,
        )

    assert [transaction.amount_minor for transaction in transactions] == [2000]


def test_repository_finds_completed_import_batch_by_hash(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        user_profile = repository.add_user_profile(display_name="Sample User")
        other_profile = repository.add_user_profile(display_name="Other User")
        session.flush()

        completed_batch = repository.add_import_batch(
            user_profile_id=user_profile.id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_hash="abc123",
            import_status=ImportStatus.COMPLETED,
        )
        repository.add_import_batch(
            user_profile_id=user_profile.id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_hash="pending123",
            import_status=ImportStatus.PENDING,
        )
        repository.add_import_batch(
            user_profile_id=other_profile.id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_hash="abc123",
            import_status=ImportStatus.COMPLETED,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        found_batch = repository.get_completed_import_batch_by_file_hash(
            user_profile_id=user_profile.id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_hash="abc123",
        )
        pending_batch = repository.get_completed_import_batch_by_file_hash(
            user_profile_id=user_profile.id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_hash="pending123",
        )

    assert found_batch.id == completed_batch.id
    assert pending_batch is None


def test_repository_lists_classification_decisions_through_profile_scope(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        user_profile = repository.add_user_profile(display_name="Sample User")
        other_profile = repository.add_user_profile(display_name="Other User")
        session.flush()

        account = repository.add_account(
            user_profile_id=user_profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        transaction = repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        session.flush()

        decision = repository.add_classification_decision(
            transaction_id=transaction.id,
            decision_source=ClassificationDecisionSource.DETERMINISTIC_RULE,
            decision_status=ClassificationDecisionStatus.SUGGESTED,
            confidence=Decimal("0.7500"),
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        decisions = repository.list_classification_decisions(
            transaction_id=transaction.id,
            user_profile_id=user_profile.id,
        )
        hidden_decisions = repository.list_classification_decisions(
            transaction_id=transaction.id,
            user_profile_id=other_profile.id,
        )

    assert [stored_decision.id for stored_decision in decisions] == [decision.id]
    assert decisions[0].confidence == Decimal("0.7500")
    assert hidden_decisions == []


def test_repository_creates_budget_and_budget_line(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        user_profile = repository.add_user_profile(display_name="Sample User")
        session.flush()

        category = repository.add_category(
            user_profile_id=user_profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        budget = repository.add_budget(
            user_profile_id=user_profile.id,
            name="Monthly budget",
            period_type=BudgetPeriodType.MONTHLY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        session.flush()

        budget_line = repository.add_budget_line(
            budget_id=budget.id,
            category_id=category.id,
            amount_minor=50000,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        stored_budget = repository.get_budget(
            budget_id=budget.id,
            user_profile_id=user_profile.id,
        )

    assert stored_budget is not None
    assert stored_budget.name == "Monthly budget"
    assert budget_line.amount_minor == 50000
