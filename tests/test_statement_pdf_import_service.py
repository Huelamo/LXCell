from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from lxcell.db.models import (
    Base,
    ClassificationDecision,
    ImportBatch,
    ImportedTransactionSource,
    Transaction,
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
    ImportAction,
    ImportSourceSystem,
    ImportStatus,
    PaymentMethod,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.importers import PdfStatementPreview, PdfStatementTransactionCandidate
from lxcell.importers.pdf_statement import PdfStatementParseIssue
from lxcell.repositories import AccountingRepository
from lxcell.services import (
    AccountingService,
    StatementPdfImportService,
    suggested_statement_account,
)


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_confirmed_statement_pdf_import_writes_auditable_pending_transactions(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        result = StatementPdfImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_PDF,
            preview=sample_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        import_batch = session.scalar(select(ImportBatch))
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))
        imported_sources = list(
            session.scalars(
                select(ImportedTransactionSource).order_by(ImportedTransactionSource.id)
            )
        )
        decisions = list(
            session.scalars(
                select(ClassificationDecision).order_by(ClassificationDecision.id)
            )
        )

    assert result.transaction_count == 2
    assert result.source_row_count == 2
    assert result.ignored_protected_count == 0
    assert import_batch.account_id == account.id
    assert import_batch.source_system == ImportSourceSystem.BANK_PDF
    assert import_batch.source_file_hash == "abc123"
    assert import_batch.import_status == ImportStatus.COMPLETED
    assert [transaction.review_status for transaction in transactions] == [
        TransactionReviewStatus.PENDING_REVIEW,
        TransactionReviewStatus.PENDING_REVIEW,
    ]
    assert [transaction.source_type for transaction in transactions] == [
        TransactionSourceType.BANK_IMPORT,
        TransactionSourceType.BANK_IMPORT,
    ]
    assert [transaction.category_id for transaction in transactions] == [None, None]
    assert [transaction.transaction_type for transaction in transactions] == [
        TransactionType.EXPENSE,
        TransactionType.ADJUSTMENT,
    ]
    assert [source.import_action for source in imported_sources] == [
        ImportAction.CREATED_TRANSACTION,
        ImportAction.CREATED_TRANSACTION,
    ]
    assert all(source.normalized_hash for source in imported_sources)
    assert [decision.decision_source for decision in decisions] == [
        ClassificationDecisionSource.IMPORT_DEFAULT,
        ClassificationDecisionSource.IMPORT_DEFAULT,
    ]
    assert [decision.decision_status for decision in decisions] == [
        ClassificationDecisionStatus.SUGGESTED,
        ClassificationDecisionStatus.SUGGESTED,
    ]


def test_statement_pdf_account_suggestion_uses_unique_configured_hint(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        shared_account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Shared account",
            account_type=AccountType.CHECKING,
            statement_match_hint="Shared account ending 1234",
        )
        other_account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Other account",
            account_type=AccountType.CHECKING,
            statement_match_hint="Other account",
        )
        session.flush()

        preview = sample_preview(
            account_hint_text="Bank A Shared account ending 1234"
        )

        assert (
            suggested_statement_account(
                preview=preview,
                accounts=[shared_account, other_account],
            )
            == shared_account
        )

        other_account.statement_match_hint = "account ending 1234"
        session.flush()

        assert (
            suggested_statement_account(
                preview=preview,
                accounts=[shared_account, other_account],
            )
            is None
        )


def test_confirmed_bank_csv_import_writes_statement_transactions(session_factory):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        result = StatementPdfImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_CSV,
            preview=sample_preview(
                source_file_hash="csv123",
                candidates=(
                    sample_candidate(
                        row_number_source=1,
                        description="Merchant A",
                        amount_minor=1234,
                        direction=Direction.OUTFLOW,
                        amount_raw="12,34",
                        payload_extra={
                            "transaction_type_raw": "SEPA direct debit",
                        },
                    ),
                ),
            ),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        import_batch = session.scalar(select(ImportBatch))
        transaction = session.scalar(select(Transaction))

    assert result.transaction_count == 1
    assert import_batch.source_system == ImportSourceSystem.BANK_CSV
    assert transaction.payment_method == PaymentMethod.DIRECT_DEBIT


def test_confirmed_bank_csv_import_uses_transfer_transaction_type(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        result = StatementPdfImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_CSV,
            preview=sample_preview(
                source_file_hash="csv-transfer",
                candidates=(
                    sample_candidate(
                        row_number_source=1,
                        description="Internal transfer",
                        amount_minor=100000,
                        direction=Direction.INFLOW,
                        amount_raw="1000,00",
                        payload_extra={
                            "transaction_type_raw": "Transfer",
                        },
                    ),
                ),
            ),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        transaction = session.scalar(select(Transaction))

    assert result.transaction_count == 1
    assert transaction.transaction_type == TransactionType.TRANSFER
    assert transaction.payment_method == PaymentMethod.BANK_TRANSFER


def test_confirmed_statement_pdf_import_applies_deterministic_rules(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        category = accounting_service.create_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()
        rule = accounting_service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="merchant a",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            direction=Direction.OUTFLOW,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        session.flush()

        result = StatementPdfImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_PDF,
            preview=sample_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))
        decisions = list(
            session.scalars(
                select(ClassificationDecision).order_by(ClassificationDecision.id)
            )
        )

    assert result.transaction_count == 2
    assert [transaction.category_id for transaction in transactions] == [
        category.id,
        None,
    ]
    assert [decision.decision_source for decision in decisions] == [
        ClassificationDecisionSource.DETERMINISTIC_RULE,
        ClassificationDecisionSource.IMPORT_DEFAULT,
    ]
    assert decisions[0].decision_status == ClassificationDecisionStatus.ACCEPTED
    assert decisions[0].classification_rule_id == rule.id
    assert decisions[0].category_id == category.id
    assert decisions[1].decision_status == ClassificationDecisionStatus.SUGGESTED


def test_confirmed_statement_pdf_import_blocks_repeated_completed_file(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()
        service = StatementPdfImportService(AccountingRepository(session))
        service.confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_PDF,
            preview=sample_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
        )

        with pytest.raises(ValueError, match="already imported"):
            service.confirm_import(
                user_profile_id=profile.id,
                account_id=account.id,
                source_system=ImportSourceSystem.BANK_PDF,
                preview=sample_preview(),
                confirmed_by="Sample User",
                user_confirmed=True,
            )


def test_confirmed_statement_pdf_import_requires_explicit_confirmation(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        with pytest.raises(ValueError, match="explicit confirmation"):
            StatementPdfImportService(AccountingRepository(session)).confirm_import(
                user_profile_id=profile.id,
                account_id=account.id,
                source_system=ImportSourceSystem.BANK_PDF,
                preview=sample_preview(),
                confirmed_by="Sample User",
                user_confirmed=False,
            )

    with session_scope(session_factory) as session:
        assert session.scalar(select(ImportBatch)) is None


def test_confirmed_statement_pdf_import_rejects_parse_issues(session_factory):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        with pytest.raises(ValueError, match="parse issues"):
            StatementPdfImportService(AccountingRepository(session)).confirm_import(
                user_profile_id=profile.id,
                account_id=account.id,
                source_system=ImportSourceSystem.BANK_PDF,
                preview=sample_preview(
                    issues=(
                        PdfStatementParseIssue(
                            page_number=1,
                            row_number_source=1,
                            message="Sample issue",
                        ),
                    ),
                ),
                confirmed_by="Sample User",
                user_confirmed=True,
            )


def test_confirmed_statement_pdf_import_ignores_protected_rows_by_default(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(
            display_name="Sample User",
            transactions_locked_until=date(2026, 1, 31),
        )
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        result = StatementPdfImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_PDF,
            preview=sample_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))
        imported_sources = list(
            session.scalars(
                select(ImportedTransactionSource).order_by(ImportedTransactionSource.id)
            )
        )
        import_batch = session.scalar(select(ImportBatch))

    assert result.transaction_count == 1
    assert result.ignored_protected_count == 1
    assert import_batch.import_status == ImportStatus.COMPLETED_WITH_WARNINGS
    assert [transaction.transaction_date for transaction in transactions] == [
        date(2026, 2, 1)
    ]
    assert [source.import_action for source in imported_sources] == [
        ImportAction.IGNORED,
        ImportAction.CREATED_TRANSACTION,
    ]
    assert imported_sources[0].created_transaction_id is None
    assert "protected_period" in imported_sources[0].payload_raw_json


def test_confirmed_statement_pdf_import_can_override_protected_rows(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(
            display_name="Sample User",
            transactions_locked_until=date(2026, 1, 31),
        )
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary card",
            account_type=AccountType.CREDIT_CARD,
        )
        session.flush()

        result = StatementPdfImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.CARD_PDF,
            preview=sample_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
            allow_locked_period_override=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))
        import_batch = session.scalar(select(ImportBatch))

    assert result.transaction_count == 2
    assert result.ignored_protected_count == 0
    assert import_batch.import_status == ImportStatus.COMPLETED
    assert [transaction.payment_method for transaction in transactions] == [
        PaymentMethod.CARD,
        PaymentMethod.CARD,
    ]


def test_confirmed_statement_pdf_import_detects_peer_to_peer_payment_method(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        result = StatementPdfImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_PDF,
            preview=sample_preview(
                candidates=(
                    sample_candidate(
                        row_number_source=1,
                        description="Transfer via Tikkie",
                    ),
                    sample_candidate(
                        row_number_source=2,
                        transaction_date=date(2026, 1, 11),
                        description="Pago Bizum",
                        content_hash="content456",
                    ),
                ),
            ),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))

    assert result.transaction_count == 2
    assert [transaction.payment_method for transaction in transactions] == [
        PaymentMethod.PEER_TO_PEER,
        PaymentMethod.PEER_TO_PEER,
    ]


def test_confirmed_statement_pdf_import_matches_existing_source_rows(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()
        service = StatementPdfImportService(AccountingRepository(session))
        service.confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_PDF,
            preview=sample_preview(source_file_hash="first123"),
            confirmed_by="Sample User",
            user_confirmed=True,
        )

        result = service.confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_PDF,
            preview=sample_preview(source_file_hash="second123"),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))
        latest_batch = session.scalar(
            select(ImportBatch).where(ImportBatch.source_file_hash == "second123")
        )
        latest_sources = list(
            session.scalars(
                select(ImportedTransactionSource)
                .where(ImportedTransactionSource.import_batch_id == latest_batch.id)
                .order_by(ImportedTransactionSource.id)
            )
        )

    assert result.transaction_count == 0
    assert result.matched_existing_count == 2
    assert len(transactions) == 2
    assert latest_batch.import_status == ImportStatus.COMPLETED_WITH_WARNINGS
    assert [source.import_action for source in latest_sources] == [
        ImportAction.MATCHED_EXISTING,
        ImportAction.MATCHED_EXISTING,
    ]
    assert all("matched_source_id" in source.payload_raw_json for source in latest_sources)


def test_confirmed_statement_pdf_import_marks_duplicate_rows_in_same_preview(
    session_factory,
):
    with session_scope(session_factory) as session:
        accounting_service = AccountingService(AccountingRepository(session))
        profile = accounting_service.create_user_profile(display_name="Sample User")
        session.flush()
        account = accounting_service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()

        result = StatementPdfImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            account_id=account.id,
            source_system=ImportSourceSystem.BANK_PDF,
            preview=sample_preview(candidates=(sample_candidate(), sample_candidate())),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        transactions = list(session.scalars(select(Transaction)))
        imported_sources = list(
            session.scalars(
                select(ImportedTransactionSource).order_by(ImportedTransactionSource.id)
            )
        )

    assert result.transaction_count == 1
    assert result.marked_duplicate_count == 1
    assert len(transactions) == 1
    assert [source.import_action for source in imported_sources] == [
        ImportAction.CREATED_TRANSACTION,
        ImportAction.MARKED_DUPLICATE,
    ]


def sample_preview(
    *,
    source_file_hash: str = "abc123",
    candidates: tuple[PdfStatementTransactionCandidate, ...] | None = None,
    issues: tuple[PdfStatementParseIssue, ...] = (),
    account_hint_text: str | None = None,
) -> PdfStatementPreview:
    return PdfStatementPreview(
        source_file_name="sample.pdf",
        source_file_hash=source_file_hash,
        page_count=1,
        candidates=candidates
        or (
            sample_candidate(
                row_number_source=1,
                transaction_date=date(2026, 1, 10),
                description="Merchant A",
                amount_minor=1234,
                direction=Direction.OUTFLOW,
                amount_raw="12,34",
            ),
            sample_candidate(
                row_number_source=2,
                transaction_date=date(2026, 2, 1),
                description="Merchant B",
                amount_minor=500,
                direction=Direction.INFLOW,
                amount_raw="5,00",
            ),
        ),
        issues=issues,
        account_hint_text=account_hint_text,
    )


def sample_candidate(
    *,
    row_number_source: int = 1,
    transaction_date: date = date(2026, 1, 10),
    description: str = "Merchant A",
    amount_minor: int = 1234,
    direction: Direction = Direction.OUTFLOW,
    amount_raw: str = "12,34",
    content_hash: str = "content123",
    payload_extra: dict | None = None,
) -> PdfStatementTransactionCandidate:
    payload_raw = {
        "transaction_date_raw": transaction_date.isoformat(),
        "posted_date_raw": transaction_date.isoformat(),
        "description_raw": description,
        "money_out_raw": amount_raw if direction == Direction.OUTFLOW else None,
        "money_in_raw": amount_raw if direction == Direction.INFLOW else None,
        "balance_raw": "987,66",
    }
    if payload_extra:
        payload_raw.update(payload_extra)
    return PdfStatementTransactionCandidate(
        row_number_source=row_number_source,
        page_number=1,
        transaction_date=transaction_date,
        posted_date=transaction_date,
        description_raw=description,
        description_clean=description,
        amount_minor=amount_minor,
        direction=direction,
        amount_raw=amount_raw,
        currency="EUR",
        balance_raw="987,66",
        balance_minor=98766,
        payload_raw=payload_raw,
        content_hash=content_hash,
    )
