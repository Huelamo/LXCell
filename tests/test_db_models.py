from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers

from lxcell.db.models import (
    Account,
    Base,
    Category,
    ClassificationDecision,
    Transaction,
    UserProfile,
)
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    Direction,
    OwnershipType,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_phase_1_models_configure_and_create_expected_tables(session_factory):
    configure_mappers()

    table_names = set(inspect(session_factory.kw["bind"]).get_table_names())

    assert table_names == {
        "accounts",
        "budget_lines",
        "budgets",
        "categories",
        "category_mappings",
        "classification_decisions",
        "classification_rules",
        "import_batches",
        "imported_transaction_sources",
        "transactions",
        "user_profiles",
    }


def test_user_profile_stores_transaction_lock_date(session_factory):
    with session_scope(session_factory) as session:
        user_profile = UserProfile(
            display_name="Sample User",
            transactions_locked_until=date(2026, 1, 31),
        )
        session.add(user_profile)

    with session_scope(session_factory) as session:
        stored_profile = session.scalar(select(UserProfile))

    assert stored_profile.transactions_locked_until == date(2026, 1, 31)


def test_enums_are_persisted_as_snake_case_values(session_factory):
    with session_scope(session_factory) as session:
        user_profile = UserProfile(display_name="Sample User")
        account = Account(
            user_profile=user_profile,
            name="Primary account",
            account_type=AccountType.CHECKING,
            ownership_type=OwnershipType.PERSONAL,
        )
        session.add(account)

    with session_scope(session_factory) as session:
        row = session.execute(
            text("SELECT account_type, ownership_type FROM accounts")
        ).one()

    assert row.account_type == "checking"
    assert row.ownership_type == "personal"


def test_transaction_rejects_account_from_different_profile(session_factory):
    with session_scope(session_factory) as session:
        first_profile = UserProfile(display_name="Sample User A")
        second_profile = UserProfile(display_name="Sample User B")
        account = Account(
            user_profile=first_profile,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.add_all([first_profile, second_profile, account])
        session.flush()
        first_account_id = account.id
        second_profile_id = second_profile.id

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            transaction = Transaction(
                user_profile_id=second_profile_id,
                account_id=first_account_id,
                transaction_date=date(2026, 1, 1),
                amount_minor=1000,
                direction=Direction.OUTFLOW,
                transaction_type=TransactionType.EXPENSE,
                source_type=TransactionSourceType.MANUAL,
            )
            session.add(transaction)
            session.flush()


def test_user_confirmed_transaction_requires_clean_description(session_factory):
    with session_scope(session_factory) as session:
        user_profile = UserProfile(display_name="Sample User")
        account = Account(
            user_profile=user_profile,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.add(account)
        session.flush()
        user_profile_id = user_profile.id
        account_id = account.id

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            transaction = Transaction(
                user_profile_id=user_profile_id,
                account_id=account_id,
                transaction_date=date(2026, 1, 1),
                amount_minor=1000,
                direction=Direction.OUTFLOW,
                transaction_type=TransactionType.EXPENSE,
                review_status=TransactionReviewStatus.USER_CONFIRMED,
                source_type=TransactionSourceType.MANUAL,
            )
            session.add(transaction)
            session.flush()


def test_classification_decision_defaults_to_system_and_decimal_confidence(
    session_factory,
):
    with session_scope(session_factory) as session:
        user_profile = UserProfile(display_name="Sample User")
        account = Account(
            user_profile=user_profile,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        category = Category(
            user_profile=user_profile,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        transaction = Transaction(
            user_profile=user_profile,
            account=account,
            category=category,
            transaction_date=date(2026, 1, 1),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        decision = ClassificationDecision(
            transaction=transaction,
            category=category,
            decision_source=ClassificationDecisionSource.DETERMINISTIC_RULE,
            decision_status=ClassificationDecisionStatus.SUGGESTED,
        )
        session.add(decision)
        session.flush()

        assert decision.decided_by == "system"
        assert decision.confidence == Decimal("1.0000")
