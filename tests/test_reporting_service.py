from datetime import date

import pytest

from lxcell.db.models import Base
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import (
    AccountType,
    BudgetPeriodType,
    CategoryType,
    Direction,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService, ReportingService


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def _create_reporting_context(session):
    repository = AccountingRepository(session)
    accounting_service = AccountingService(repository)
    user_profile = accounting_service.create_user_profile(display_name="Sample User")
    other_profile = accounting_service.create_user_profile(display_name="Other User")
    session.flush()

    account = accounting_service.create_account(
        user_profile_id=user_profile.id,
        name="Primary account",
        account_type=AccountType.CHECKING,
    )
    other_account = accounting_service.create_account(
        user_profile_id=other_profile.id,
        name="Other account",
        account_type=AccountType.CHECKING,
    )
    expense_category = accounting_service.create_category(
        user_profile_id=user_profile.id,
        name="Category A",
        category_type=CategoryType.EXPENSE,
        canonical_key="category_a",
    )
    income_category = accounting_service.create_category(
        user_profile_id=user_profile.id,
        name="Category B",
        category_type=CategoryType.INCOME,
        canonical_key="category_b",
    )
    session.flush()
    return (
        repository,
        user_profile,
        other_profile,
        account,
        other_account,
        expense_category,
        income_category,
    )


def test_cashflow_summary_respects_direction_and_net_amount(session_factory):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            account,
            _other_account,
            expense_category,
            income_category,
        ) = _create_reporting_context(session)

        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 5),
            amount_minor=2500,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=income_category.id,
            transaction_date=date(2026, 1, 6),
            amount_minor=5000,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.INCOME,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 7),
            amount_minor=100,
            direction=Direction.NEUTRAL,
            transaction_type=TransactionType.ADJUSTMENT,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        summary = ReportingService(repository).summarize_cashflow(
            user_profile_id=user_profile.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )

    assert summary.inflow_minor == 5000
    assert summary.outflow_minor == 2500
    assert summary.neutral_minor == 100
    assert summary.net_minor == 2500


def test_reporting_excludes_deleted_ignored_transfers_and_other_profiles_by_default(
    session_factory,
):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            other_profile,
            account,
            other_account,
            expense_category,
            _income_category,
        ) = _create_reporting_context(session)

        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 5),
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 6),
            amount_minor=2000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.TRANSFER,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 7),
            amount_minor=3000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            review_status=TransactionReviewStatus.IGNORED,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 8),
            amount_minor=4000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
            is_deleted=True,
        )
        repository.add_transaction(
            user_profile_id=other_profile.id,
            account_id=other_account.id,
            transaction_date=date(2026, 1, 9),
            amount_minor=5000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        summary = ReportingService(repository).summarize_cashflow(
            user_profile_id=user_profile.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        summary_with_transfers = ReportingService(repository).summarize_cashflow(
            user_profile_id=user_profile.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            include_transfers=True,
        )

    assert summary.outflow_minor == 1000
    assert summary_with_transfers.outflow_minor == 3000


def test_category_summary_groups_signed_amounts_and_uncategorized(
    session_factory,
):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            account,
            _other_account,
            expense_category,
            income_category,
        ) = _create_reporting_context(session)

        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 5),
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 6),
            amount_minor=250,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.FEE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=income_category.id,
            transaction_date=date(2026, 1, 7),
            amount_minor=5000,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.INCOME,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 8),
            amount_minor=100,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        totals = ReportingService(repository).summarize_by_category(
            user_profile_id=user_profile.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )

    totals_by_id = {total.category_id: total for total in totals}
    assert totals_by_id[expense_category.id].amount_minor == -1250
    assert totals_by_id[income_category.id].amount_minor == 5000
    assert totals_by_id[None].amount_minor == -100
    assert totals_by_id[None].category_name is None


def test_reporting_uses_inclusive_date_range(session_factory):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            account,
            _other_account,
            expense_category,
            _income_category,
        ) = _create_reporting_context(session)

        for transaction_date, amount_minor in [
            (date(2025, 12, 31), 1000),
            (date(2026, 1, 1), 2000),
            (date(2026, 1, 31), 3000),
            (date(2026, 2, 1), 4000),
        ]:
            repository.add_transaction(
                user_profile_id=user_profile.id,
                account_id=account.id,
                category_id=expense_category.id,
                transaction_date=transaction_date,
                amount_minor=amount_minor,
                direction=Direction.OUTFLOW,
                transaction_type=TransactionType.EXPENSE,
                source_type=TransactionSourceType.MANUAL,
            )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        summary = ReportingService(repository).summarize_cashflow(
            user_profile_id=user_profile.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )

    assert summary.outflow_minor == 5000


def test_budget_actuals_compare_planned_and_actual_by_budget_line(
    session_factory,
):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            account,
            _other_account,
            expense_category,
            income_category,
        ) = _create_reporting_context(session)
        budget = repository.add_budget(
            user_profile_id=user_profile.id,
            name="Monthly budget",
            period_type=BudgetPeriodType.MONTHLY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        session.flush()
        repository.add_budget_line(
            budget_id=budget.id,
            category_id=expense_category.id,
            amount_minor=3000,
        )
        repository.add_budget_line(
            budget_id=budget.id,
            category_id=income_category.id,
            amount_minor=5000,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 5),
            amount_minor=1200,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=income_category.id,
            transaction_date=date(2026, 1, 6),
            amount_minor=5000,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.INCOME,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        summary = ReportingService(
            AccountingRepository(session)
        ).summarize_budget_actuals(
            user_profile_id=user_profile.id,
            budget_id=budget.id,
        )

    lines_by_category_id = {line.category_id: line for line in summary.lines}
    assert summary.planned_amount_minor == 8000
    assert summary.actual_amount_minor == 6200
    assert summary.remaining_minor == 1800
    assert lines_by_category_id[expense_category.id].planned_amount_minor == 3000
    assert lines_by_category_id[expense_category.id].actual_amount_minor == 1200
    assert lines_by_category_id[expense_category.id].remaining_minor == 1800
    assert lines_by_category_id[income_category.id].actual_amount_minor == 5000


def test_budget_actuals_use_custom_date_range_and_transfer_default(
    session_factory,
):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            account,
            _other_account,
            expense_category,
            _income_category,
        ) = _create_reporting_context(session)
        budget = repository.add_budget(
            user_profile_id=user_profile.id,
            name="Monthly budget",
            period_type=BudgetPeriodType.MONTHLY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        session.flush()
        repository.add_budget_line(
            budget_id=budget.id,
            category_id=expense_category.id,
            amount_minor=10000,
        )
        for transaction_date, amount_minor, transaction_type in [
            (date(2026, 1, 5), 1000, TransactionType.EXPENSE),
            (date(2026, 1, 6), 2000, TransactionType.TRANSFER),
            (date(2026, 2, 5), 3000, TransactionType.EXPENSE),
        ]:
            repository.add_transaction(
                user_profile_id=user_profile.id,
                account_id=account.id,
                category_id=expense_category.id,
                transaction_date=transaction_date,
                amount_minor=amount_minor,
                direction=Direction.OUTFLOW,
                transaction_type=transaction_type,
                source_type=TransactionSourceType.MANUAL,
            )

    with session_scope(session_factory) as session:
        reporting_service = ReportingService(AccountingRepository(session))
        default_summary = reporting_service.summarize_budget_actuals(
            user_profile_id=user_profile.id,
            budget_id=budget.id,
        )
        custom_summary = reporting_service.summarize_budget_actuals(
            user_profile_id=user_profile.id,
            budget_id=budget.id,
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 28),
        )
        with_transfers_summary = reporting_service.summarize_budget_actuals(
            user_profile_id=user_profile.id,
            budget_id=budget.id,
            include_transfers=True,
        )

    assert default_summary.actual_amount_minor == 1000
    assert custom_summary.actual_amount_minor == 3000
    assert with_transfers_summary.actual_amount_minor == 3000


def test_budget_actuals_report_categories_without_budget_lines(session_factory):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            account,
            _other_account,
            expense_category,
            income_category,
        ) = _create_reporting_context(session)
        budget = repository.add_budget(
            user_profile_id=user_profile.id,
            name="Monthly budget",
            period_type=BudgetPeriodType.MONTHLY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        session.flush()
        repository.add_budget_line(
            budget_id=budget.id,
            category_id=expense_category.id,
            amount_minor=1000,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=expense_category.id,
            transaction_date=date(2026, 1, 5),
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=income_category.id,
            transaction_date=date(2026, 1, 6),
            amount_minor=5000,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.INCOME,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        summary = ReportingService(
            AccountingRepository(session)
        ).summarize_budget_actuals(
            user_profile_id=user_profile.id,
            budget_id=budget.id,
        )

    unbudgeted_lines_by_category_id = {
        line.category_id: line for line in summary.unbudgeted_lines
    }
    assert len(summary.lines) == 1
    assert summary.actual_amount_minor == 1000
    assert summary.unbudgeted_actual_amount_minor == 5000
    assert unbudgeted_lines_by_category_id[income_category.id].planned_amount_minor == 0
    assert unbudgeted_lines_by_category_id[income_category.id].actual_amount_minor == 5000
    assert unbudgeted_lines_by_category_id[income_category.id].remaining_minor == -5000


def test_budget_actuals_report_uncategorized_activity_for_review(session_factory):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            account,
            _other_account,
            expense_category,
            _income_category,
        ) = _create_reporting_context(session)
        budget = repository.add_budget(
            user_profile_id=user_profile.id,
            name="Monthly budget",
            period_type=BudgetPeriodType.MONTHLY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        session.flush()
        repository.add_budget_line(
            budget_id=budget.id,
            category_id=expense_category.id,
            amount_minor=1000,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 5),
            amount_minor=700,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 6),
            amount_minor=300,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.REFUND,
            source_type=TransactionSourceType.MANUAL,
        )

    with session_scope(session_factory) as session:
        summary = ReportingService(
            AccountingRepository(session)
        ).summarize_budget_actuals(
            user_profile_id=user_profile.id,
            budget_id=budget.id,
        )

    assert summary.uncategorized_actual_amount_minor == 1000
    assert summary.uncategorized_transaction_count == 2


def test_budget_uncategorized_activity_uses_report_filters(session_factory):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            account,
            _other_account,
            expense_category,
            _income_category,
        ) = _create_reporting_context(session)
        budget = repository.add_budget(
            user_profile_id=user_profile.id,
            name="Monthly budget",
            period_type=BudgetPeriodType.MONTHLY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        session.flush()
        repository.add_budget_line(
            budget_id=budget.id,
            category_id=expense_category.id,
            amount_minor=1000,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 5),
            amount_minor=100,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 6),
            amount_minor=200,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.TRANSFER,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 7),
            amount_minor=300,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            review_status=TransactionReviewStatus.IGNORED,
            source_type=TransactionSourceType.MANUAL,
        )
        repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 8),
            amount_minor=400,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.MANUAL,
            is_deleted=True,
        )

    with session_scope(session_factory) as session:
        reporting_service = ReportingService(AccountingRepository(session))
        default_summary = reporting_service.summarize_budget_actuals(
            user_profile_id=user_profile.id,
            budget_id=budget.id,
        )
        with_transfers_summary = reporting_service.summarize_budget_actuals(
            user_profile_id=user_profile.id,
            budget_id=budget.id,
            include_transfers=True,
        )

    assert default_summary.uncategorized_actual_amount_minor == 100
    assert default_summary.uncategorized_transaction_count == 1
    assert with_transfers_summary.uncategorized_actual_amount_minor == 300
    assert with_transfers_summary.uncategorized_transaction_count == 2


def test_budget_actuals_reject_inactive_budget(session_factory):
    with session_scope(session_factory) as session:
        (
            repository,
            user_profile,
            _other_profile,
            _account,
            _other_account,
            _expense_category,
            _income_category,
        ) = _create_reporting_context(session)
        budget = repository.add_budget(
            user_profile_id=user_profile.id,
            name="Inactive budget",
            period_type=BudgetPeriodType.MONTHLY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            is_active=False,
        )

    with session_scope(session_factory) as session:
        with pytest.raises(ValueError):
            ReportingService(AccountingRepository(session)).summarize_budget_actuals(
                user_profile_id=user_profile.id,
                budget_id=budget.id,
            )
