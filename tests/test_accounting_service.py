from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from lxcell.db.models import (
    Base,
    ClassificationDecision,
    ImportBatch,
    ImportedTransactionSource,
    ReimbursementMatch,
    SharedExpenseAllocation,
)
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    ClassificationMatchField,
    ClassificationRuleType,
    Direction,
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
from lxcell.services import AccountingService, normalized_counterparty_name


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


def test_create_shared_account_can_store_statement_hint_and_personal_share(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile = service.create_user_profile(display_name="Sample User")
        session.flush()

        account = service.create_account(
            user_profile_id=user_profile.id,
            name="Shared account",
            account_type=AccountType.CHECKING,
            ownership_type=OwnershipType.SHARED,
            statement_match_hint="Shared account ending 1234",
            personal_reporting_share_basis_points=5000,
        )

    assert account.statement_match_hint == "Shared account ending 1234"
    assert account.personal_reporting_share_basis_points == 5000


def test_personal_reporting_share_requires_shared_account(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile = service.create_user_profile(display_name="Sample User")
        session.flush()

        with pytest.raises(
            ValueError,
            match="only be configured for shared accounts",
        ):
            service.create_account(
                user_profile_id=user_profile.id,
                name="Primary account",
                account_type=AccountType.CHECKING,
                ownership_type=OwnershipType.PERSONAL,
                personal_reporting_share_basis_points=5000,
            )


def test_update_account_can_change_statement_hint_and_personal_share(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        account = service.create_account(
            user_profile_id=user_profile.id,
            name="Shared account",
            account_type=AccountType.CHECKING,
            ownership_type=OwnershipType.SHARED,
        )
        session.flush()

        updated = service.update_account(
            user_profile_id=user_profile.id,
            account_id=account.id,
            name="Updated shared account",
            account_type=AccountType.SAVINGS,
            ownership_type=OwnershipType.SHARED,
            currency="EUR",
            statement_match_hint="Updated hint",
            personal_reporting_share_basis_points=2500,
            is_active=True,
        )

    assert updated.name == "Updated shared account"
    assert updated.account_type == AccountType.SAVINGS
    assert updated.statement_match_hint == "Updated hint"
    assert updated.personal_reporting_share_basis_points == 2500


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


def test_manual_transaction_without_category_is_confirmed_but_not_classified(
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

    assert stored_transaction.review_status == TransactionReviewStatus.USER_CONFIRMED
    assert stored_transaction.category_id is None
    assert decision_count == 0
    assert imported_source.created_transaction_id == transaction.id


def test_update_category_updates_editable_fields(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, _account, category = _create_profile_account_category(service)
        session.flush()

        service.update_category(
            user_profile_id=user_profile.id,
            category_id=category.id,
            name="Category B",
            category_type=CategoryType.INCOME,
            canonical_key="category_b",
            display_order=3,
            is_active=False,
        )

    with session_scope(session_factory) as session:
        stored_category = AccountingRepository(session).get_category(
            category_id=category.id,
            user_profile_id=user_profile.id,
        )

    assert stored_category.name == "Category B"
    assert stored_category.category_type == CategoryType.INCOME
    assert stored_category.canonical_key == "category_b"
    assert stored_category.display_order == 3
    assert stored_category.is_active is False


def test_deactivate_category_hides_it_from_default_lists(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, _account, category = _create_profile_account_category(service)
        session.flush()

        service.deactivate_category(
            user_profile_id=user_profile.id,
            category_id=category.id,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        active_categories = repository.list_categories(user_profile.id)
        all_categories = repository.list_categories(
            user_profile.id,
            include_inactive=True,
        )

    assert active_categories == []
    assert len(all_categories) == 1
    assert all_categories[0].is_active is False


def test_update_classification_rule_updates_existing_rule(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, _account, category = _create_profile_account_category(service)
        rule = service.create_classification_rule(
            user_profile_id=user_profile.id,
            name="Merchant A",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            direction=Direction.OUTFLOW,
            amount_min_minor=100,
            amount_max_minor=5000,
            priority=10,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        session.flush()

        service.update_classification_rule(
            user_profile_id=user_profile.id,
            classification_rule_id=rule.id,
            name="Merchant B",
            rule_type=ClassificationRuleType.DESCRIPTION_REGEX,
            match_field=ClassificationMatchField.DESCRIPTION_RAW,
            pattern="merchant b.*",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=None,
            direction=Direction.OUTFLOW,
            amount_min_minor=None,
            amount_max_minor=6000,
            priority=5,
            confidence=Decimal("0.9000"),
            auto_apply=False,
            is_active=False,
        )

    with session_scope(session_factory) as session:
        stored_rule = AccountingRepository(session).get_classification_rule(
            classification_rule_id=rule.id,
            user_profile_id=user_profile.id,
        )

    assert stored_rule.name == "Merchant B"
    assert stored_rule.rule_type == ClassificationRuleType.DESCRIPTION_REGEX
    assert stored_rule.match_field == ClassificationMatchField.DESCRIPTION_RAW
    assert stored_rule.pattern == "merchant b.*"
    assert stored_rule.category_id == category.id
    assert stored_rule.transaction_type == TransactionType.EXPENSE
    assert stored_rule.payment_method is None
    assert stored_rule.direction == Direction.OUTFLOW
    assert stored_rule.amount_min_minor is None
    assert stored_rule.amount_max_minor == 6000
    assert stored_rule.priority == 5
    assert stored_rule.confidence == Decimal("0.9000")
    assert stored_rule.auto_apply is False
    assert stored_rule.is_active is False


def test_active_classification_rule_requires_active_category(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, _account, category = _create_profile_account_category(service)
        service.deactivate_category(
            user_profile_id=user_profile.id,
            category_id=category.id,
        )
        rule = service.create_classification_rule(
            user_profile_id=user_profile.id,
            name="Merchant A",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=None,
            transaction_type=TransactionType.EXPENSE,
            confidence=Decimal("0.9500"),
        )
        rule.is_active = False
        session.flush()

        with pytest.raises(ValueError, match="active category"):
            service.update_classification_rule(
                user_profile_id=user_profile.id,
                classification_rule_id=rule.id,
                name="Merchant A",
                rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
                match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
                pattern="merchant a",
                category_id=category.id,
                transaction_type=TransactionType.EXPENSE,
                confidence=Decimal("0.9500"),
                is_active=True,
            )


def test_hard_delete_classification_rule_removes_rule_and_unlinks_decisions(
    session_factory,
):
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
            decided_by="Sample User",
        )
        rule = service.create_classification_rule(
            user_profile_id=user_profile.id,
            name="Merchant A",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        session.flush()
        service.repository.add_classification_decision(
            transaction_id=transaction.id,
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=None,
            decision_source=ClassificationDecisionSource.DETERMINISTIC_RULE,
            decision_status=ClassificationDecisionStatus.ACCEPTED,
            classification_rule_id=rule.id,
            confidence=Decimal("0.9500"),
            decided_by="system",
        )
        session.flush()

        unlinked_count = service.hard_delete_classification_rule(
            user_profile_id=user_profile.id,
            classification_rule_id=rule.id,
        )

    with session_scope(session_factory) as session:
        stored_rule = AccountingRepository(session).get_classification_rule(
            classification_rule_id=rule.id,
            user_profile_id=user_profile.id,
        )
        decisions = list(session.scalars(select(ClassificationDecision)))

    assert unlinked_count == 1
    assert stored_rule is None
    assert len(decisions) == 2
    assert decisions[-1].classification_rule_id is None


def test_create_counterparty_normalizes_display_name(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile = service.create_user_profile(display_name="Sample User")
        session.flush()

        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="  Counterparty Á  ",
            aliases_raw="Alias A",
        )

    assert counterparty.display_name == "Counterparty Á"
    assert counterparty.normalized_name == "counterparty_a"
    assert counterparty.aliases_raw == "Alias A"
    assert normalized_counterparty_name("Counterparty Á") == "counterparty_a"


def test_mark_transaction_shared_50_50_persists_exact_minor_unit_split(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, category = _create_profile_account_category(service)
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
        )
        session.flush()
        transaction = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            category_id=category.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1001,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        session.flush()

        allocation = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )

    with session_scope(session_factory) as session:
        stored_allocation = session.scalar(select(SharedExpenseAllocation))

    assert stored_allocation.id == allocation.id
    assert stored_allocation.transaction_id == transaction.id
    assert stored_allocation.counterparty_id == counterparty.id
    assert stored_allocation.personal_share_minor == 501
    assert stored_allocation.recoverable_share_minor == 500
    assert stored_allocation.share_ratio_basis_points == 5000
    assert stored_allocation.status == SharedExpenseStatus.PENDING
    assert stored_allocation.decided_by == "Sample User"


def test_mark_transaction_shared_50_50_requires_outflow(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, _category = _create_profile_account_category(service)
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
        )
        session.flush()
        transaction = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Transfer A",
            amount_minor=1000,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.ADJUSTMENT,
            decided_by="Sample User",
        )
        session.flush()

        with pytest.raises(ValueError, match="Only outflow transactions"):
            service.mark_transaction_shared_50_50(
                user_profile_id=user_profile.id,
                transaction_id=transaction.id,
                counterparty_id=counterparty.id,
                decided_by="Sample User",
            )


def test_shared_expense_allocation_respects_profile_lock(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile = service.create_user_profile(
            display_name="Sample User",
            transactions_locked_until=date(2026, 1, 31),
        )
        session.flush()
        account = service.create_account(
            user_profile_id=user_profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
        )
        session.flush()
        transaction = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
            allow_locked_period_override=True,
        )
        session.flush()

        with pytest.raises(ValueError, match="periodo protegido"):
            service.mark_transaction_shared_50_50(
                user_profile_id=user_profile.id,
                transaction_id=transaction.id,
                counterparty_id=counterparty.id,
                decided_by="Sample User",
            )

        allocation = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
            allow_locked_period_override=True,
        )

    assert allocation.status == SharedExpenseStatus.PENDING


def test_waived_shared_expense_can_be_reactivated_without_deleting_audit(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, _category = _create_profile_account_category(service)
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
        )
        transaction = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        session.flush()

        allocation = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )
        service.waive_shared_expense_allocation(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            decided_by="Sample User",
        )
        reactivated = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )

    with session_scope(session_factory) as session:
        allocation_count = session.scalar(select(func.count(SharedExpenseAllocation.id)))

    assert reactivated.id == allocation.id
    assert reactivated.status == SharedExpenseStatus.PENDING
    assert allocation_count == 1


def test_reimbursement_suggestions_match_counterparty_alias_and_exact_amount(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, _category = _create_profile_account_category(service)
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
            aliases_raw="Alias A",
        )
        session.flush()
        expense = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        reimbursement = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 12),
            description_clean="Transfer from Alias A",
            amount_minor=500,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.ADJUSTMENT,
            decided_by="Sample User",
        )
        session.flush()
        allocation = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=expense.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )

        result = service.refresh_reimbursement_match_suggestions(
            user_profile_id=user_profile.id,
            decided_by="Sample User",
        )

    with session_scope(session_factory) as session:
        reimbursement_match = session.scalar(select(ReimbursementMatch))

    assert result.created_count == 1
    assert result.existing_count == 0
    assert reimbursement_match.shared_expense_allocation_id == allocation.id
    assert reimbursement_match.reimbursement_transaction_id == reimbursement.id
    assert reimbursement_match.matched_amount_minor == 500
    assert reimbursement_match.status == ReimbursementMatchStatus.SUGGESTED
    assert reimbursement_match.confidence == Decimal("0.9500")


def test_confirm_reimbursement_match_updates_shared_expense_status(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, _category = _create_profile_account_category(service)
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
        )
        session.flush()
        expense = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 12),
            description_clean="Transfer from Counterparty A",
            amount_minor=500,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.ADJUSTMENT,
            decided_by="Sample User",
        )
        session.flush()
        allocation = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=expense.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )
        service.refresh_reimbursement_match_suggestions(
            user_profile_id=user_profile.id,
            decided_by="Sample User",
        )
        reimbursement_match = session.scalar(select(ReimbursementMatch))

        confirmed_match = service.confirm_reimbursement_match(
            user_profile_id=user_profile.id,
            reimbursement_match_id=reimbursement_match.id,
            decided_by="Sample User",
        )

    assert confirmed_match.status == ReimbursementMatchStatus.CONFIRMED
    assert allocation.status == SharedExpenseStatus.REIMBURSED


def test_confirm_partial_reimbursement_match_updates_shared_expense_status(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, _category = _create_profile_account_category(service)
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
        )
        session.flush()
        expense = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 12),
            description_clean="Transfer from Counterparty A",
            amount_minor=200,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.ADJUSTMENT,
            decided_by="Sample User",
        )
        session.flush()
        allocation = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=expense.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )
        service.refresh_reimbursement_match_suggestions(
            user_profile_id=user_profile.id,
            decided_by="Sample User",
        )
        reimbursement_match = session.scalar(select(ReimbursementMatch))

        service.confirm_reimbursement_match(
            user_profile_id=user_profile.id,
            reimbursement_match_id=reimbursement_match.id,
            decided_by="Sample User",
        )

    assert allocation.status == SharedExpenseStatus.PARTIALLY_REIMBURSED


def test_reimbursement_suggestions_skip_missing_alias_and_excess_amount(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, _category = _create_profile_account_category(service)
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
        )
        session.flush()
        expense = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 12),
            description_clean="Transfer from Someone Else",
            amount_minor=500,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.ADJUSTMENT,
            decided_by="Sample User",
        )
        service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 13),
            description_clean="Transfer from Counterparty A",
            amount_minor=600,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.ADJUSTMENT,
            decided_by="Sample User",
        )
        session.flush()
        service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=expense.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )

        result = service.refresh_reimbursement_match_suggestions(
            user_profile_id=user_profile.id,
            decided_by="Sample User",
        )

    with session_scope(session_factory) as session:
        match_count = session.scalar(select(func.count(ReimbursementMatch.id)))

    assert result.created_count == 0
    assert match_count == 0


def test_reject_reimbursement_match_keeps_shared_expense_pending(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, _category = _create_profile_account_category(service)
        counterparty = service.create_counterparty(
            user_profile_id=user_profile.id,
            display_name="Counterparty A",
        )
        session.flush()
        expense = service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        service.record_manual_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 12),
            description_clean="Transfer from Counterparty A",
            amount_minor=500,
            direction=Direction.INFLOW,
            transaction_type=TransactionType.ADJUSTMENT,
            decided_by="Sample User",
        )
        session.flush()
        allocation = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile.id,
            transaction_id=expense.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )
        service.refresh_reimbursement_match_suggestions(
            user_profile_id=user_profile.id,
            decided_by="Sample User",
        )
        reimbursement_match = session.scalar(select(ReimbursementMatch))

        rejected_match = service.reject_reimbursement_match(
            user_profile_id=user_profile.id,
            reimbursement_match_id=reimbursement_match.id,
            decided_by="Sample User",
        )

    assert rejected_match.status == ReimbursementMatchStatus.REJECTED
    assert allocation.status == SharedExpenseStatus.PENDING


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


def test_confirm_transaction_classification_can_confirm_without_category(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        user_profile, account, category = _create_profile_account_category(service)
        transaction = repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()
        repository.add_classification_decision(
            transaction_id=transaction.id,
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            decision_source=ClassificationDecisionSource.DETERMINISTIC_RULE,
            decision_status=ClassificationDecisionStatus.SUGGESTED,
            decided_by="system",
        )
        session.flush()

        accepted_decision = service.confirm_transaction_classification(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            category_id=None,
            transaction_type=TransactionType.EXPENSE,
            payment_method=None,
            decided_by="Sample User",
            notes="Reviewed without category.",
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

    assert stored_transaction.category_id is None
    assert stored_transaction.review_status == TransactionReviewStatus.USER_CONFIRMED
    assert len(decisions) == 2
    assert decisions[0].decision_status == ClassificationDecisionStatus.SUPERSEDED
    assert decisions[1].id == accepted_decision.id
    assert decisions[1].category_id is None
    assert decisions[1].decision_status == ClassificationDecisionStatus.ACCEPTED
    assert decisions[1].decision_source == ClassificationDecisionSource.MANUAL_USER


def test_confirm_transaction_classification_rejects_incompatible_category(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        user_profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        account = service.create_account(
            user_profile_id=user_profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        income_category = service.create_category(
            user_profile_id=user_profile.id,
            name="Category A",
            category_type=CategoryType.INCOME,
            canonical_key="category_a",
        )
        session.flush()
        transaction = repository.add_transaction(
            user_profile_id=user_profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()

        with pytest.raises(ValueError, match="incompatible"):
            service.confirm_transaction_classification(
                user_profile_id=user_profile.id,
                transaction_id=transaction.id,
                category_id=income_category.id,
                transaction_type=TransactionType.EXPENSE,
                decided_by="Sample User",
            )


def test_update_manual_transaction_updates_fields_and_classification_audit(
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
            payment_method=PaymentMethod.CARD,
            decided_by="Sample User",
        )
        session.flush()

        service.update_manual_transaction(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            account_id=account.id,
            category_id=second_category.id,
            transaction_date=date(2026, 1, 11),
            description_clean="Merchant B",
            amount_minor=2000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.FEE,
            payment_method=None,
            decided_by="Sample User",
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

    assert stored_transaction.transaction_date == date(2026, 1, 11)
    assert stored_transaction.description_clean == "Merchant B"
    assert stored_transaction.amount_minor == 2000
    assert stored_transaction.category_id == second_category.id
    assert stored_transaction.transaction_type == TransactionType.FEE
    assert stored_transaction.payment_method is None
    assert stored_transaction.review_status == TransactionReviewStatus.USER_CONFIRMED
    assert len(decisions) == 2
    assert decisions[0].decision_status == ClassificationDecisionStatus.SUPERSEDED
    assert decisions[1].decision_status == ClassificationDecisionStatus.ACCEPTED
    assert decisions[1].notes == "Manual transaction edit."


def test_soft_delete_transaction_hides_it_from_default_lists(session_factory):
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
            decided_by="Sample User",
        )
        session.flush()

        service.soft_delete_transaction(
            user_profile_id=user_profile.id,
            transaction_id=transaction.id,
            decided_by="Sample User",
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        visible_transactions = repository.list_transactions(
            user_profile_id=user_profile.id
        )
        all_transactions = repository.list_transactions(
            user_profile_id=user_profile.id,
            include_deleted=True,
        )

    assert visible_transactions == []
    assert len(all_transactions) == 1
    assert all_transactions[0].is_deleted is True


def test_locked_profile_rejects_manual_transaction_without_override(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, category = _create_profile_account_category(service)
        service.update_user_profile_transaction_lock(
            user_profile_id=user_profile.id,
            transactions_locked_until=date(2026, 1, 31),
        )

        with pytest.raises(ValueError, match="periodo protegido"):
            service.record_manual_transaction(
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


def test_locked_profile_allows_manual_transaction_with_override(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        user_profile, account, category = _create_profile_account_category(service)
        service.update_user_profile_transaction_lock(
            user_profile_id=user_profile.id,
            transactions_locked_until=date(2026, 1, 31),
        )

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
            allow_locked_period_override=True,
        )

    assert transaction.transaction_date == date(2026, 1, 10)


def test_locked_profile_rejects_edit_and_delete_without_override(session_factory):
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
            decided_by="Sample User",
        )
        service.update_user_profile_transaction_lock(
            user_profile_id=user_profile.id,
            transactions_locked_until=date(2026, 1, 31),
        )
        session.flush()

        with pytest.raises(ValueError, match="periodo protegido"):
            service.update_manual_transaction(
                user_profile_id=user_profile.id,
                transaction_id=transaction.id,
                account_id=account.id,
                category_id=category.id,
                transaction_date=date(2026, 1, 11),
                description_clean="Merchant B",
                amount_minor=2000,
                direction=Direction.OUTFLOW,
                transaction_type=TransactionType.EXPENSE,
                decided_by="Sample User",
            )

        with pytest.raises(ValueError, match="periodo protegido"):
            service.soft_delete_transaction(
                user_profile_id=user_profile.id,
                transaction_id=transaction.id,
                decided_by="Sample User",
            )


def test_locked_profile_rejects_classification_confirmation_without_override(
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
        service.update_user_profile_transaction_lock(
            user_profile_id=user_profile.id,
            transactions_locked_until=date(2026, 1, 31),
        )
        session.flush()

        with pytest.raises(ValueError, match="periodo protegido"):
            service.confirm_transaction_classification(
                user_profile_id=user_profile.id,
                transaction_id=transaction.id,
                category_id=second_category.id,
                transaction_type=TransactionType.EXPENSE,
                decided_by="Sample User",
            )


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
