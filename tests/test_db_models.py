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
    Counterparty,
    ReimbursementMatch,
    SharedExpenseAllocation,
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
    ReimbursementMatchStatus,
    SharedExpenseStatus,
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
        "counterparties",
        "import_batches",
        "imported_transaction_sources",
        "reimbursement_matches",
        "shared_expense_allocations",
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


def test_account_stores_statement_hint_and_personal_reporting_share(session_factory):
    with session_scope(session_factory) as session:
        user_profile = UserProfile(display_name="Sample User")
        account = Account(
            user_profile=user_profile,
            name="Shared account",
            account_type=AccountType.CHECKING,
            ownership_type=OwnershipType.SHARED,
            statement_match_hint="Shared account ending 1234",
            personal_reporting_share_basis_points=5000,
        )
        session.add(account)

    with session_scope(session_factory) as session:
        stored_account = session.scalar(select(Account))

    assert stored_account.statement_match_hint == "Shared account ending 1234"
    assert stored_account.personal_reporting_share_basis_points == 5000


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


def test_shared_expense_allocation_rejects_counterparty_from_different_profile(
    session_factory,
):
    with session_scope(session_factory) as session:
        first_profile = UserProfile(display_name="Sample User A")
        second_profile = UserProfile(display_name="Sample User B")
        account = Account(
            user_profile=first_profile,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        counterparty = Counterparty(
            user_profile=second_profile,
            display_name="Counterparty A",
            normalized_name="counterparty_a",
        )
        transaction = Transaction(
            user_profile=first_profile,
            account=account,
            transaction_date=date(2026, 1, 1),
            description_clean="Merchant A",
            amount_minor=1001,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        session.add_all([first_profile, second_profile, account, counterparty, transaction])
        session.flush()
        first_profile_id = first_profile.id
        transaction_id = transaction.id
        counterparty_id = counterparty.id

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            allocation = SharedExpenseAllocation(
                user_profile_id=first_profile_id,
                transaction_id=transaction_id,
                counterparty_id=counterparty_id,
                personal_share_minor=501,
                recoverable_share_minor=500,
                share_ratio_basis_points=5000,
                status=SharedExpenseStatus.PENDING,
            )
            session.add(allocation)
            session.flush()


def test_reimbursement_match_rejects_transaction_from_different_profile(
    session_factory,
):
    with session_scope(session_factory) as session:
        first_profile = UserProfile(display_name="Sample User A")
        second_profile = UserProfile(display_name="Sample User B")
        first_account = Account(
            user_profile=first_profile,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        second_account = Account(
            user_profile=second_profile,
            name="Other account",
            account_type=AccountType.CHECKING,
        )
        counterparty = Counterparty(
            user_profile=first_profile,
            display_name="Counterparty A",
            normalized_name="counterparty_a",
        )
        expense = Transaction(
            user_profile=first_profile,
            account=first_account,
            transaction_date=date(2026, 1, 1),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        reimbursement = Transaction(
            user_profile=second_profile,
            account=second_account,
            transaction_date=date(2026, 1, 2),
            description_clean="Counterparty A",
            amount_minor=500,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.ADJUSTMENT,
            source_type=TransactionSourceType.MANUAL,
        )
        allocation = SharedExpenseAllocation(
            user_profile=first_profile,
            transaction=expense,
            counterparty=counterparty,
            personal_share_minor=500,
            recoverable_share_minor=500,
            share_ratio_basis_points=5000,
            status=SharedExpenseStatus.PENDING,
        )
        session.add_all(
            [
                first_profile,
                second_profile,
                first_account,
                second_account,
                counterparty,
                expense,
                reimbursement,
                allocation,
            ]
        )
        session.flush()
        first_profile_id = first_profile.id
        allocation_id = allocation.id
        reimbursement_id = reimbursement.id

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            reimbursement_match = ReimbursementMatch(
                user_profile_id=first_profile_id,
                shared_expense_allocation_id=allocation_id,
                reimbursement_transaction_id=reimbursement_id,
                matched_amount_minor=500,
                status=ReimbursementMatchStatus.SUGGESTED,
                confidence=Decimal("0.9500"),
            )
            session.add(reimbursement_match)
            session.flush()
