from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from lxcell.db.models import (
    Account,
    Base,
    Category,
    CategoryMapping,
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
    Direction,
    ImportSourceSystem,
    ImportStatus,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.importers import (
    HistoricalExcelPreview,
    HistoricalExcelTrackingComparison,
    HistoricalExcelTrackingValidation,
    HistoricalExcelTransactionCandidate,
)
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService, HistoricalExcelImportService
from lxcell.services.historical_excel_import_service import (
    HISTORICAL_EXCEL_ACCOUNT_NAME,
)


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_confirmed_historical_excel_import_writes_auditable_records(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        existing_category = service.create_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()

        result = HistoricalExcelImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            preview=sample_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        session.flush()

    with session_scope(session_factory) as session:
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))
        import_batch = session.scalar(select(ImportBatch))
        account = session.scalar(select(Account))
        categories = list(session.scalars(select(Category).order_by(Category.name)))
        imported_sources = list(
            session.scalars(select(ImportedTransactionSource).order_by(ImportedTransactionSource.id))
        )
        decisions = list(
            session.scalars(select(ClassificationDecision).order_by(ClassificationDecision.id))
        )

    assert result.transaction_count == 4
    assert result.created_category_count == 1
    assert result.reused_category_count == 1
    assert account.name == HISTORICAL_EXCEL_ACCOUNT_NAME
    assert account.account_type == AccountType.OTHER
    assert import_batch.account_id is None
    assert import_batch.source_system == ImportSourceSystem.EXCEL_HISTORICAL
    assert import_batch.source_file_hash == "abc123"
    assert import_batch.import_status == ImportStatus.COMPLETED
    assert [category.name for category in categories] == ["Category A", "Income A"]
    assert categories[0].id == existing_category.id
    assert categories[1].category_type == CategoryType.INCOME
    assert len(imported_sources) == 4
    assert all(source.normalized_hash for source in imported_sources)
    assert [transaction.review_status for transaction in transactions] == [
        TransactionReviewStatus.USER_CONFIRMED,
    ] * 4
    assert [transaction.source_type for transaction in transactions] == [
        TransactionSourceType.EXCEL_IMPORT,
    ] * 4
    assert [transaction.transaction_type for transaction in transactions] == [
        TransactionType.EXPENSE,
        TransactionType.REFUND,
        TransactionType.INCOME,
        TransactionType.ADJUSTMENT,
    ]
    assert [transaction.direction for transaction in transactions] == [
        Direction.OUTFLOW,
        Direction.INFLOW,
        Direction.INFLOW,
        Direction.OUTFLOW,
    ]
    assert [decision.decision_source for decision in decisions] == [
        ClassificationDecisionSource.HISTORICAL_MATCH,
    ] * 4
    assert [decision.decision_status for decision in decisions] == [
        ClassificationDecisionStatus.ACCEPTED,
    ] * 4
    assert {decision.decided_by for decision in decisions} == {"Sample User"}


def test_confirmed_historical_excel_import_requires_explicit_confirmation(
    session_factory,
):
    with session_scope(session_factory) as session:
        profile = AccountingRepository(session).add_user_profile(display_name="Sample User")
        session.flush()

        with pytest.raises(ValueError, match="explicit confirmation"):
            HistoricalExcelImportService(AccountingRepository(session)).confirm_import(
                user_profile_id=profile.id,
                preview=sample_preview(),
                confirmed_by="Sample User",
                user_confirmed=False,
            )

    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(ImportBatch.id))) == 0


def test_confirmed_historical_excel_import_respects_profile_lock(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        service.update_user_profile_transaction_lock(
            user_profile_id=profile.id,
            transactions_locked_until=date(2026, 1, 31),
        )

        with pytest.raises(ValueError, match="periodo protegido"):
            HistoricalExcelImportService(AccountingRepository(session)).confirm_import(
                user_profile_id=profile.id,
                preview=sample_preview(),
                confirmed_by="Sample User",
                user_confirmed=True,
            )

        result = HistoricalExcelImportService(AccountingRepository(session)).confirm_import(
            user_profile_id=profile.id,
            preview=sample_preview(source_file_hash="override123"),
            confirmed_by="Sample User",
            user_confirmed=True,
            allow_locked_period_override=True,
        )

    assert result.transaction_count == 4


def test_confirmed_historical_excel_import_maps_source_categories_to_existing_category(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        combined_category = service.create_category(
            user_profile_id=profile.id,
            name="Combined Category",
            category_type=CategoryType.EXPENSE,
            canonical_key="combined_category",
        )
        session.flush()

        result = HistoricalExcelImportService(
            AccountingRepository(session)
        ).confirm_import(
            user_profile_id=profile.id,
            preview=mapping_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
            category_id_overrides_by_source_name={
                "Legacy Category A": combined_category.id,
                "Legacy Category B": combined_category.id,
            },
        )

    with session_scope(session_factory) as session:
        categories = list(session.scalars(select(Category)))
        mappings = list(
            session.scalars(select(CategoryMapping).order_by(CategoryMapping.source_category_name))
        )
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))

    assert result.created_category_count == 0
    assert result.mapped_category_count == 2
    assert len(categories) == 1
    assert [
        (mapping.source_file_hash, mapping.source_category_name, mapping.target_category_id)
        for mapping in mappings
    ] == [
        ("legacy123", "Legacy Category A", combined_category.id),
        ("legacy123", "Legacy Category B", combined_category.id),
    ]
    assert {transaction.category_id for transaction in transactions} == {
        combined_category.id
    }


def test_confirmed_historical_excel_import_rejects_incompatible_category_mapping(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        income_category = service.create_category(
            user_profile_id=profile.id,
            name="Income Category",
            category_type=CategoryType.INCOME,
            canonical_key="income_category",
        )
        session.flush()

        with pytest.raises(ValueError, match="incompatible category types"):
            HistoricalExcelImportService(AccountingRepository(session)).confirm_import(
                user_profile_id=profile.id,
                preview=mapping_preview(),
                confirmed_by="Sample User",
                user_confirmed=True,
                category_id_overrides_by_source_name={
                    "Legacy Category A": income_category.id,
                },
            )


def test_confirmed_historical_excel_import_does_not_silently_reuse_inactive_category(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        service.create_category(
            user_profile_id=profile.id,
            name="Legacy Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="legacy_category_a",
        )
        session.flush()
        inactive_category = AccountingRepository(session).list_categories(
            profile.id,
            include_inactive=True,
        )[0]
        inactive_category.is_active = False

        with pytest.raises(ValueError, match="category conflicts"):
            HistoricalExcelImportService(AccountingRepository(session)).confirm_import(
                user_profile_id=profile.id,
                preview=mapping_preview(),
                confirmed_by="Sample User",
                user_confirmed=True,
            )


def test_confirmed_historical_excel_import_blocks_dirty_validation(session_factory):
    with session_scope(session_factory) as session:
        profile = AccountingRepository(session).add_user_profile(display_name="Sample User")
        session.flush()

        with pytest.raises(ValueError, match="differences"):
            HistoricalExcelImportService(AccountingRepository(session)).confirm_import(
                user_profile_id=profile.id,
                preview=sample_preview(
                    tracking_validation=HistoricalExcelTrackingValidation(
                        sheet_name="Seguimiento",
                        comparisons=(
                            HistoricalExcelTrackingComparison(
                                month_key="2026-01",
                                source_category_name="Category A",
                                registro_amount_minor=750,
                                seguimiento_amount_minor=748,
                            ),
                        ),
                        registro_only_categories=(),
                        seguimiento_only_categories=(),
                    )
                ),
                confirmed_by="Sample User",
                user_confirmed=True,
            )

    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(ImportBatch.id))) == 0


def test_confirmed_historical_excel_import_blocks_same_file_hash_per_profile(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        first_profile = repository.add_user_profile(display_name="Sample User")
        second_profile = repository.add_user_profile(display_name="Other Sample User")
        session.flush()

        service = HistoricalExcelImportService(repository)
        service.confirm_import(
            user_profile_id=first_profile.id,
            preview=sample_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        with pytest.raises(ValueError, match="already imported"):
            service.confirm_import(
                user_profile_id=first_profile.id,
                preview=sample_preview(),
                confirmed_by="Sample User",
                user_confirmed=True,
            )
        second_result = service.confirm_import(
            user_profile_id=second_profile.id,
            preview=sample_preview(),
            confirmed_by="Other Sample User",
            user_confirmed=True,
        )

    assert second_result.transaction_count == 4


def test_confirmed_historical_excel_import_blocks_normalized_row_overlap(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()

        service = HistoricalExcelImportService(repository)
        service.confirm_import(
            user_profile_id=profile.id,
            preview=sample_preview(source_file_hash="abc123"),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        with pytest.raises(ValueError, match="overlaps"):
            service.confirm_import(
                user_profile_id=profile.id,
                preview=sample_preview(source_file_hash="def456"),
                confirmed_by="Sample User",
                user_confirmed=True,
            )


def test_historical_excel_import_rollback_soft_deletes_created_transactions(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        service = HistoricalExcelImportService(repository)
        result = service.confirm_import(
            user_profile_id=profile.id,
            preview=sample_preview(),
            confirmed_by="Sample User",
            user_confirmed=True,
        )
        service.rollback_import_batch(
            user_profile_id=profile.id,
            import_batch_id=result.import_batch_id,
            decided_by="Sample User",
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))
        import_batch = repository.get_import_batch(
            import_batch_id=result.import_batch_id,
            user_profile_id=profile.id,
        )
        duplicate_batch = repository.get_completed_import_batch_by_file_hash(
            user_profile_id=profile.id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_hash="abc123",
        )

    assert import_batch.import_status == ImportStatus.ROLLED_BACK
    assert all(transaction.is_deleted for transaction in transactions)
    assert duplicate_batch is None


def sample_preview(
    *,
    source_file_hash: str = "abc123",
    tracking_validation: HistoricalExcelTrackingValidation | None = None,
) -> HistoricalExcelPreview:
    return HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash=source_file_hash,
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(
            candidate(
                row_number_source=3,
                source_category_name="Category A",
                amount=Decimal("10.00"),
                direction=Direction.OUTFLOW,
                source_column_kind="expense",
                description_raw="Merchant A",
            ),
            candidate(
                row_number_source=4,
                source_category_name="Category A",
                amount=Decimal("-2.50"),
                direction=Direction.INFLOW,
                source_column_kind="expense",
                description_raw="Merchant A refund",
            ),
            candidate(
                row_number_source=5,
                source_category_name="Income A",
                amount=Decimal("20.00"),
                direction=Direction.INFLOW,
                source_column_kind="income",
                description_raw="Income Source A",
            ),
            candidate(
                row_number_source=6,
                source_category_name="Income A",
                amount=Decimal("-1.00"),
                direction=Direction.OUTFLOW,
                source_column_kind="income",
                description_raw="Income adjustment A",
            ),
        ),
        ignored_row_numbers=(),
        tracking_validation=tracking_validation
        or HistoricalExcelTrackingValidation(
            sheet_name="Seguimiento",
            comparisons=(
                HistoricalExcelTrackingComparison(
                    month_key="2026-01",
                    source_category_name="Category A",
                    registro_amount_minor=750,
                    seguimiento_amount_minor=750,
                ),
            ),
            registro_only_categories=(),
            seguimiento_only_categories=(),
        ),
    )


def mapping_preview() -> HistoricalExcelPreview:
    return HistoricalExcelPreview(
        source_file_name="legacy_sample.xlsx",
        source_file_hash="legacy123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(
            candidate(
                row_number_source=3,
                source_category_name="Legacy Category A",
                amount=Decimal("10.00"),
                direction=Direction.OUTFLOW,
                source_column_kind="expense",
                description_raw="Merchant A",
            ),
            candidate(
                row_number_source=4,
                source_category_name="Legacy Category B",
                amount=Decimal("5.00"),
                direction=Direction.OUTFLOW,
                source_column_kind="expense",
                description_raw="Merchant B",
            ),
        ),
        ignored_row_numbers=(),
        tracking_validation=HistoricalExcelTrackingValidation(
            sheet_name="Seguimiento",
            comparisons=(
                HistoricalExcelTrackingComparison(
                    month_key="2026-01",
                    source_category_name="Legacy Category A",
                    registro_amount_minor=1000,
                    seguimiento_amount_minor=1000,
                ),
                HistoricalExcelTrackingComparison(
                    month_key="2026-01",
                    source_category_name="Legacy Category B",
                    registro_amount_minor=500,
                    seguimiento_amount_minor=500,
                ),
            ),
            registro_only_categories=(),
            seguimiento_only_categories=(),
        ),
    )


def candidate(
    *,
    row_number_source: int,
    source_category_name: str,
    amount: Decimal,
    direction: Direction,
    source_column_kind: str,
    description_raw: str,
) -> HistoricalExcelTransactionCandidate:
    return HistoricalExcelTransactionCandidate(
        row_number_source=row_number_source,
        column_name_source=source_category_name,
        transaction_date=date(2026, 1, 10),
        source_category_name=source_category_name,
        amount_minor=abs(int(amount * Decimal("100"))),
        direction=direction,
        amount_raw=str(amount),
        description_raw=description_raw,
        payload_raw={
            "Fecha": "2026-01-10",
            source_category_name: str(amount),
            "Comentarios": description_raw,
        },
        source_amount_minor=int(amount * Decimal("100")),
        source_amount_decimal=amount,
        source_column_kind=source_column_kind,
    )
