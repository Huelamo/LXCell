from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lxcell.db.models import Base, CategoryMapping, ImportBatch
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    Direction,
    ImportSourceSystem,
    ImportStatus,
    TransactionType,
)
from lxcell.importers import (
    HistoricalExcelPreview,
    HistoricalExcelTrackingComparison,
    HistoricalExcelTrackingValidation,
    HistoricalExcelTransactionCandidate,
    PdfStatementParseIssue,
    PdfStatementPreview,
    PdfStatementTransactionCandidate,
)
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService
from lxcell.ui.streamlit_app import (
    HISTORICAL_EXCEL_PREVIEW_VERSION,
    STATEMENT_PDF_PREVIEW_VERSION,
    account_label_for_transaction_table,
    apply_historical_category_mapping_to_plan,
    canonical_key_from_name,
    category_label_for_transaction_table,
    category_table_rows,
    category_table_success_message,
    confirm_historical_excel_import_from_preview,
    completed_historical_import_batch_fallback,
    completed_statement_pdf_import_batch_fallback,
    edited_category_payload,
    edited_transaction_payload,
    find_duplicate_transactions,
    format_signed_amount_minor,
    friendly_integrity_error_message,
    historical_preview_can_render,
    historical_category_import_plan,
    historical_category_mapping_suggestions,
    historical_import_validation_blockers,
    manual_transaction_payload,
    normalize_category_label_for_import,
    parse_amount_minor,
    preview_candidate_rows,
    statement_pdf_candidate_rows,
    statement_pdf_direction_rows,
    statement_pdf_issue_rows,
    statement_pdf_preview_can_render,
    source_totals_by_category_minor,
    source_totals_by_month_minor,
    stored_historical_preview_matches,
    stored_statement_pdf_preview_matches,
    soft_delete_transaction_for_ui,
    totals_table_rows,
    tracking_comparison_rows,
    tracking_unmatched_category_rows,
    update_category_for_ui,
)


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_parse_amount_minor_accepts_dot_and_comma():
    assert parse_amount_minor("12.34") == 1234
    assert parse_amount_minor("12,34") == 1234


def test_parse_amount_minor_rejects_negative_amount():
    with pytest.raises(ValueError):
        parse_amount_minor("-1.00")


def test_format_signed_amount_minor_preserves_sign():
    assert format_signed_amount_minor(1234) == "12.34"
    assert format_signed_amount_minor(-1234) == "-12.34"


def test_canonical_key_from_name_is_simple_and_stable():
    assert canonical_key_from_name("Category A") == "category_a"
    assert canonical_key_from_name("  Nómina / Salario  ") == "nomina_salario"


def test_friendly_integrity_error_message_handles_duplicate_account_name():
    error = IntegrityError(
        statement=None,
        params=None,
        orig=Exception(
            "UNIQUE constraint failed: accounts.user_profile_id, accounts.name"
        ),
    )

    assert (
        friendly_integrity_error_message(error)
        == "Ya existe una cuenta con ese nombre en este perfil."
    )


def test_edited_transaction_payload_translates_table_labels():
    payload = edited_transaction_payload(
        {
            "fecha": date(2026, 1, 10),
            "cuenta": "Primary account",
            "categoria": "Sin categoría",
            "descripcion": " Merchant A ",
            "importe": "12,34",
            "direccion": Direction.OUTFLOW.value,
            "tipo": TransactionType.EXPENSE.value,
            "metodo_pago": "",
        },
        account_ids_by_label={"Primary account": 7},
        category_ids_by_label={"Sin categoría": None},
    )

    assert payload == {
        "transaction_date": date(2026, 1, 10),
        "account_id": 7,
        "category_id": None,
        "description_clean": "Merchant A",
        "amount_minor": 1234,
        "direction": Direction.OUTFLOW.value,
        "transaction_type": TransactionType.EXPENSE.value,
        "payment_method": None,
    }


def test_edited_category_payload_normalizes_table_values():
    payload = edited_category_payload(
        {
            "nombre": " Category B ",
            "tipo": "income",
            "clave": " category_b ",
            "orden": 2.0,
        },
        existing_is_active=False,
    )

    assert payload == {
        "name": "Category B",
        "category_type": "income",
        "canonical_key": "category_b",
        "display_order": 2,
        "is_active": False,
    }


def test_edited_category_payload_preserves_key_outside_advanced_view():
    payload = edited_category_payload(
        {
            "nombre": "New visible name",
            "tipo": "expense",
            "orden": 2.0,
        },
        existing_canonical_key="stable_key",
        show_advanced=False,
    )

    assert payload["name"] == "New visible name"
    assert payload["canonical_key"] == "stable_key"


def test_category_table_rows_hide_technical_columns_outside_advanced_view(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        category = repository.add_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )

    rows = category_table_rows([category], show_advanced=False)

    assert rows == [
        {
            "id": category.id,
            "nombre": "Category A",
            "tipo": CategoryType.EXPENSE.value,
            "orden": 0,
            "accion": "",
        }
    ]


def test_category_table_rows_show_deleted_status_in_advanced_view(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        category = repository.add_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
            is_active=False,
        )

    rows = category_table_rows([category], show_advanced=True)

    assert rows == [
        {
            "id": category.id,
            "nombre": "Category A",
            "tipo": CategoryType.EXPENSE.value,
            "orden": 0,
            "accion": "",
            "estado": "eliminada",
            "clave": "category_a",
        }
    ]


def test_transaction_table_labels_show_inactive_records(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        account = repository.add_account(
            user_profile_id=profile.id,
            name="Historical account",
            account_type=AccountType.OTHER,
            is_active=False,
        )
        category = repository.add_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
            is_active=False,
        )

    assert account_label_for_transaction_table(account) == "Historical account (eliminada)"
    assert category_label_for_transaction_table(category) == "Category A (eliminada)"


def test_update_category_for_ui_falls_back_for_loaded_legacy_service(session_factory):
    class LegacyService:
        def __init__(self, repository):
            self.repository = repository

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        category = repository.add_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()

        update_category_for_ui(
            LegacyService(repository),
            user_profile_id=profile.id,
            category_id=category.id,
            name="Category B",
            category_type=CategoryType.INCOME,
            canonical_key="category_b",
            display_order=4,
            is_active=False,
        )

    with session_scope(session_factory) as session:
        category = AccountingRepository(session).get_category(
            category_id=category.id,
            user_profile_id=profile.id,
        )

    assert category.name == "Category B"
    assert category.category_type == CategoryType.INCOME
    assert category.canonical_key == "category_b"
    assert category.display_order == 4
    assert category.is_active is False


def test_soft_delete_transaction_for_ui_falls_back_for_loaded_legacy_service(
    session_factory,
):
    class LegacyService:
        def __init__(self, repository):
            self.repository = repository

    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        account = service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()
        transaction = service.record_manual_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        session.flush()

        soft_delete_transaction_for_ui(
            LegacyService(service.repository),
            user_profile_id=profile.id,
            transaction_id=transaction.id,
        )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        visible_transactions = repository.list_transactions(user_profile_id=profile.id)
        all_transactions = repository.list_transactions(
            user_profile_id=profile.id,
            include_deleted=True,
        )

    assert visible_transactions == []
    assert len(all_transactions) == 1
    assert all_transactions[0].is_deleted is True


def test_category_table_success_message_includes_deleted_categories():
    assert (
        category_table_success_message(
            updated_count=1,
            deleted_count=2,
            reactivated_count=3,
        )
        == "Categorías guardadas: 1 actualizada(s), 2 eliminada(s), 3 reactivada(s)"
    )


def test_preview_candidate_rows_use_import_preview_labels():
    preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(
            HistoricalExcelTransactionCandidate(
                row_number_source=3,
                column_name_source="Category A",
                transaction_date=date(2026, 1, 10),
                source_category_name="Category A",
                amount_minor=1234,
                direction=Direction.OUTFLOW,
                amount_raw="12.34",
                description_raw="Merchant A",
                payload_raw={},
                source_amount_minor=1234,
                source_column_kind="expense",
            ),
        ),
        ignored_row_numbers=(),
    )

    assert preview_candidate_rows(preview) == [
        {
            "fila": 3,
            "fecha": date(2026, 1, 10),
            "categoría origen": "Category A",
            "tipo columna": "gasto",
            "importe Excel": "12.34",
            "dirección": "outflow",
            "comentario": "Merchant A",
        }
    ]


def test_preview_candidate_rows_fall_back_for_legacy_candidates():
    preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(
            HistoricalExcelTransactionCandidate(
                row_number_source=3,
                column_name_source="Category A",
                transaction_date=date(2026, 1, 10),
                source_category_name="Category A",
                amount_minor=1234,
                direction=Direction.OUTFLOW,
                amount_raw="12.34",
                description_raw=None,
                payload_raw={},
            ),
        ),
        ignored_row_numbers=(),
    )

    assert preview_candidate_rows(preview)[0]["importe Excel"] == "12.34"


def test_source_totals_for_preview_use_excel_amounts():
    preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(
            HistoricalExcelTransactionCandidate(
                row_number_source=3,
                column_name_source="Category A",
                transaction_date=date(2026, 1, 10),
                source_category_name="Category A",
                amount_minor=1234,
                direction=Direction.OUTFLOW,
                amount_raw="12.34",
                description_raw=None,
                payload_raw={},
                source_amount_minor=1234,
            ),
            HistoricalExcelTransactionCandidate(
                row_number_source=4,
                column_name_source="Category A",
                transaction_date=date(2026, 1, 11),
                source_category_name="Category A",
                amount_minor=250,
                direction=Direction.INFLOW,
                amount_raw="-2.5",
                description_raw=None,
                payload_raw={},
                source_amount_minor=-250,
            ),
        ),
        ignored_row_numbers=(),
    )

    assert source_totals_by_category_minor(preview) == {"Category A": 984}
    assert source_totals_by_month_minor(preview) == {"2026-01": 984}


def test_source_totals_for_preview_round_after_aggregation():
    preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=tuple(
            HistoricalExcelTransactionCandidate(
                row_number_source=row_number,
                column_name_source="Category A",
                transaction_date=date(2026, 1, row_number),
                source_category_name="Category A",
                amount_minor=2,
                direction=Direction.OUTFLOW,
                amount_raw="0.015",
                description_raw=None,
                payload_raw={},
                source_amount_minor=2,
                source_amount_decimal=Decimal("0.015"),
            )
            for row_number in range(1, 7)
        ),
        ignored_row_numbers=(),
    )

    assert source_totals_by_category_minor(preview) == {"Category A": 9}
    assert source_totals_by_month_minor(preview) == {"2026-01": 9}


def test_stored_historical_preview_matches_rejects_stale_preview():
    upload_signature = ("sample.xlsx", "Registro", "abc123")
    preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(),
        ignored_row_numbers=(),
    )

    assert not stored_historical_preview_matches(
        {
            "preview": object(),
            "signature": upload_signature,
        },
        upload_signature,
    )
    assert stored_historical_preview_matches(
            {
                "preview": preview,
                "signature": upload_signature,
                "version": HISTORICAL_EXCEL_PREVIEW_VERSION,
            },
            upload_signature,
        )


def test_historical_preview_can_render_rejects_legacy_object():
    assert not historical_preview_can_render(object())


def test_statement_pdf_candidate_rows_use_preview_labels():
    preview = PdfStatementPreview(
        source_file_name="statement.pdf",
        source_file_hash="abc123",
        page_count=2,
        candidates=(
            PdfStatementTransactionCandidate(
                row_number_source=1,
                page_number=2,
                transaction_date=date(2026, 9, 11),
                posted_date=date(2026, 9, 12),
                description_raw="Merchant A raw",
                description_clean="Merchant A",
                amount_minor=1234,
                direction=Direction.OUTFLOW,
                amount_raw="12,34",
                currency="EUR",
                balance_raw="987,66",
                balance_minor=98766,
                payload_raw={},
                content_hash="abcdef1234567890",
            ),
        ),
        issues=(),
    )

    assert statement_pdf_candidate_rows(preview) == [
        {
            "fila": 1,
            "página": 2,
            "fecha": date(2026, 9, 11),
            "fecha valor": date(2026, 9, 12),
            "descripción": "Merchant A",
            "importe": "-12.34",
            "dirección": Direction.OUTFLOW.value,
            "saldo": "987.66",
            "hash": "abcdef123456",
        }
    ]


def test_statement_pdf_summary_rows_format_direction_and_issues():
    preview = PdfStatementPreview(
        source_file_name="statement.pdf",
        source_file_hash="abc123",
        page_count=1,
        candidates=(
            PdfStatementTransactionCandidate(
                row_number_source=1,
                page_number=1,
                transaction_date=date(2026, 9, 11),
                posted_date=None,
                description_raw="Merchant A",
                description_clean="Merchant A",
                amount_minor=1000,
                direction=Direction.OUTFLOW,
                amount_raw="10,00",
                currency="EUR",
                balance_raw=None,
                balance_minor=None,
                payload_raw={},
                content_hash="abc123",
            ),
            PdfStatementTransactionCandidate(
                row_number_source=2,
                page_number=1,
                transaction_date=date(2026, 9, 12),
                posted_date=None,
                description_raw="Merchant B",
                description_clean="Merchant B",
                amount_minor=250,
                direction=Direction.INFLOW,
                amount_raw="2,50",
                currency="EUR",
                balance_raw=None,
                balance_minor=None,
                payload_raw={},
                content_hash="def456",
            ),
        ),
        issues=(
            PdfStatementParseIssue(
                page_number=1,
                row_number_source=3,
                message="Row has no outgoing or incoming amount.",
            ),
        ),
    )

    assert statement_pdf_direction_rows(preview) == [
        {"dirección": "Entrante", "movimientos": 1, "importe": "2.50"},
        {"dirección": "Saliente", "movimientos": 1, "importe": "-10.00"},
    ]
    assert statement_pdf_issue_rows(preview) == [
        {
            "página": 1,
            "fila": 3,
            "incidencia": "Row has no outgoing or incoming amount.",
        }
    ]


def test_stored_statement_pdf_preview_matches_rejects_stale_preview():
    upload_signature = ("statement.pdf", 7, ImportSourceSystem.BANK_PDF.value, "abc123")
    preview = PdfStatementPreview(
        source_file_name="statement.pdf",
        source_file_hash="abc123",
        page_count=1,
        candidates=(),
        issues=(),
    )

    assert not stored_statement_pdf_preview_matches(
        {
            "preview": object(),
            "signature": upload_signature,
        },
        upload_signature,
    )
    assert stored_statement_pdf_preview_matches(
        {
            "preview": preview,
            "signature": upload_signature,
            "version": STATEMENT_PDF_PREVIEW_VERSION,
        },
        upload_signature,
    )


def test_statement_pdf_preview_can_render_rejects_legacy_object():
    assert not statement_pdf_preview_can_render(object())


def test_completed_statement_pdf_import_batch_fallback_is_account_scoped(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        account_a = repository.add_account(
            user_profile_id=profile.id,
            name="Account A",
            account_type=AccountType.CHECKING,
        )
        account_b = repository.add_account(
            user_profile_id=profile.id,
            name="Account B",
            account_type=AccountType.CHECKING,
        )
        session.flush()
        matching_batch = repository.add_import_batch(
            user_profile_id=profile.id,
            account_id=account_a.id,
            source_system=ImportSourceSystem.BANK_PDF,
            source_file_hash="abc123",
            import_status=ImportStatus.COMPLETED,
        )
        repository.add_import_batch(
            user_profile_id=profile.id,
            account_id=account_b.id,
            source_system=ImportSourceSystem.BANK_PDF,
            source_file_hash="abc123",
            import_status=ImportStatus.COMPLETED,
        )
        session.flush()

        found_batch = completed_statement_pdf_import_batch_fallback(
            repository,
            user_profile_id=profile.id,
            account_id=account_a.id,
            source_system=ImportSourceSystem.BANK_PDF,
            source_file_hash="abc123",
        )
        missing_batch = completed_statement_pdf_import_batch_fallback(
            repository,
            user_profile_id=profile.id,
            account_id=account_a.id,
            source_system=ImportSourceSystem.CARD_PDF,
            source_file_hash="abc123",
        )

    assert found_batch == matching_batch
    assert missing_batch is None


def test_totals_table_rows_format_signed_amounts():
    assert totals_table_rows({"2026-01": -1234}, "mes") == [
        {"mes": "2026-01", "importe": "-12.34"}
    ]


def test_tracking_comparison_rows_format_validation_results():
    validation = HistoricalExcelTrackingValidation(
        sheet_name="Seguimiento",
        comparisons=(
            HistoricalExcelTrackingComparison(
                month_key="2026-01",
                source_category_name="Category A",
                registro_amount_minor=1000,
                seguimiento_amount_minor=999,
            ),
        ),
        registro_only_categories=(),
        seguimiento_only_categories=(),
    )

    assert tracking_comparison_rows(validation) == [
        {
            "mes": "2026-01",
            "categoría origen": "Category A",
            "Registro": "10.00",
            "Seguimiento": "9.99",
            "diferencia": "0.01",
            "estado": "ok",
        }
    ]


def test_tracking_unmatched_category_rows_identify_where_category_appears():
    validation = HistoricalExcelTrackingValidation(
        sheet_name="Seguimiento",
        comparisons=(),
        registro_only_categories=("Category A",),
        seguimiento_only_categories=("Category B",),
    )

    assert tracking_unmatched_category_rows(validation) == [
        {
            "categoría": "Category A",
            "aparece en": "Registro",
        },
        {
            "categoría": "Category B",
            "aparece en": "Seguimiento",
        },
    ]


def test_historical_category_import_plan_reuses_creates_and_flags_conflicts(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        existing_category = repository.add_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        conflicting_category = repository.add_category(
            user_profile_id=profile.id,
            name="Legacy Category",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_b",
        )

    preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(
            HistoricalExcelTransactionCandidate(
                row_number_source=3,
                column_name_source="Category A",
                transaction_date=date(2026, 1, 10),
                source_category_name="Category A",
                amount_minor=100,
                direction=Direction.OUTFLOW,
                amount_raw="1",
                description_raw=None,
                payload_raw={},
                source_column_kind="expense",
            ),
            HistoricalExcelTransactionCandidate(
                row_number_source=4,
                column_name_source="Category B",
                transaction_date=date(2026, 1, 10),
                source_category_name="Category B",
                amount_minor=100,
                direction=Direction.OUTFLOW,
                amount_raw="1",
                description_raw=None,
                payload_raw={},
                source_column_kind="expense",
            ),
            HistoricalExcelTransactionCandidate(
                row_number_source=5,
                column_name_source="Salary",
                transaction_date=date(2026, 1, 10),
                source_category_name="Salary",
                amount_minor=100,
                direction=Direction.INFLOW,
                amount_raw="1",
                description_raw=None,
                payload_raw={},
                source_column_kind="income",
            ),
        ),
        ignored_row_numbers=(),
    )

    rows = historical_category_import_plan(
        [existing_category, conflicting_category],
        preview,
    )

    assert rows == [
        {
            "categoría Excel": "Category A",
            "acción": "reutilizar",
            "categoría LXCell": "Category A",
            "tipo": "expense",
            "clave": "category_a",
        },
        {
            "categoría Excel": "Category B",
            "acción": "conflicto",
            "categoría LXCell": "Legacy Category",
            "tipo": "expense",
            "clave": "category_b",
        },
        {
            "categoría Excel": "Salary",
            "acción": "crear",
            "categoría LXCell": "Salary",
            "tipo": "income",
            "clave": "salary",
        },
    ]


def test_historical_category_import_plan_flags_inactive_name_match_as_conflict(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        inactive_category = repository.add_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
            is_active=False,
        )

    preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(
            HistoricalExcelTransactionCandidate(
                row_number_source=3,
                column_name_source="Category A",
                transaction_date=date(2026, 1, 10),
                source_category_name="Category A",
                amount_minor=100,
                direction=Direction.OUTFLOW,
                amount_raw="1",
                description_raw=None,
                payload_raw={},
                source_column_kind="expense",
            ),
        ),
        ignored_row_numbers=(),
    )

    rows = historical_category_import_plan([inactive_category], preview)

    assert rows == [
        {
            "categoría Excel": "Category A",
            "acción": "conflicto",
            "categoría LXCell": "Category A (eliminada)",
            "tipo": "expense",
            "clave": "category_a",
        }
    ]


def test_apply_historical_category_mapping_to_plan_marks_selected_existing_category(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        target_category = repository.add_category(
            user_profile_id=profile.id,
            name="Combined Category",
            category_type=CategoryType.EXPENSE,
            canonical_key="combined_category",
        )

    rows = apply_historical_category_mapping_to_plan(
        [
            {
                "categoría Excel": "Legacy Category A",
                "acción": "crear",
                "categoría LXCell": "Legacy Category A",
                "tipo": "expense",
                "clave": "legacy_category_a",
            },
        ],
        [target_category],
        {"Legacy Category A": target_category.id},
    )

    assert rows == [
        {
            "categoría Excel": "Legacy Category A",
            "acción": "mapear",
            "categoría LXCell": "Combined Category",
            "tipo": "expense",
            "clave": "combined_category",
        }
    ]


def test_historical_category_mapping_suggestions_use_prior_confirmed_mapping(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        target_category = repository.add_category(
            user_profile_id=profile.id,
            name="Combined Category",
            category_type=CategoryType.EXPENSE,
            canonical_key="combined_category",
        )
        session.flush()
        session.add(
            CategoryMapping(
                user_profile_id=profile.id,
                source_system=ImportSourceSystem.EXCEL_HISTORICAL,
                source_file_hash="older_hash",
                source_file_name="older.xlsx",
                source_category_name="Legacy Category A",
                source_category_key="legacy category a",
                source_column_kind="expense",
                target_category_id=target_category.id,
            )
        )

    suggestions = historical_category_mapping_suggestions(
        session_factory,
        user_profile_id=profile.id,
        category_plan=[
            {
                "categoría Excel": "Legacy Category A",
                "acción": "crear",
                "categoría LXCell": "Legacy Category A",
                "tipo": "expense",
                "clave": "legacy_category_a",
            }
        ],
    )

    assert suggestions == {"Legacy Category A": target_category.id}


def test_historical_import_validation_blockers_require_clean_tracking_validation():
    invalid_preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(),
        ignored_row_numbers=(),
        tracking_validation=HistoricalExcelTrackingValidation(
            sheet_name="Seguimiento",
            comparisons=(
                HistoricalExcelTrackingComparison(
                    month_key="2026-01",
                    source_category_name="Category A",
                    registro_amount_minor=100,
                    seguimiento_amount_minor=102,
                ),
            ),
            registro_only_categories=("Category B",),
            seguimiento_only_categories=(),
        ),
    )

    assert historical_import_validation_blockers(invalid_preview) == [
        "Hay diferencias entre Registro y Seguimiento.",
        "Hay categorías presentes solo en Registro.",
    ]


def test_completed_historical_import_batch_fallback_finds_completed_hash(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        profile = repository.add_user_profile(display_name="Sample User")
        session.flush()
        batch = repository.add_import_batch(
            user_profile_id=profile.id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_hash="abc123",
            import_status=ImportStatus.COMPLETED,
        )
        session.flush()

        found_batch = completed_historical_import_batch_fallback(
            repository,
            user_profile_id=profile.id,
            source_file_hash="abc123",
        )

    assert found_batch.id == batch.id


def test_confirm_historical_excel_import_from_preview_writes_import(session_factory):
    with session_scope(session_factory) as session:
        profile = AccountingRepository(session).add_user_profile(display_name="Sample User")
        session.flush()

    preview = HistoricalExcelPreview(
        source_file_name="sample.xlsx",
        source_file_hash="abc123",
        sheet_name="Registro",
        header_row_number=2,
        date_column_name="Fecha",
        candidates=(
            HistoricalExcelTransactionCandidate(
                row_number_source=3,
                column_name_source="Category A",
                transaction_date=date(2026, 1, 10),
                source_category_name="Category A",
                amount_minor=100,
                direction=Direction.OUTFLOW,
                amount_raw="1",
                description_raw=None,
                payload_raw={},
                source_amount_minor=100,
                source_amount_decimal=Decimal("1.00"),
                source_column_kind="expense",
            ),
        ),
        ignored_row_numbers=(),
        tracking_validation=HistoricalExcelTrackingValidation(
            sheet_name="Seguimiento",
            comparisons=(
                HistoricalExcelTrackingComparison(
                    month_key="2026-01",
                    source_category_name="Category A",
                    registro_amount_minor=100,
                    seguimiento_amount_minor=100,
                ),
            ),
            registro_only_categories=(),
            seguimiento_only_categories=(),
        ),
    )

    result = confirm_historical_excel_import_from_preview(
        session_factory,
        user_profile_id=profile.id,
        preview=preview,
        confirmed_by="Sample User",
        user_confirmed=True,
    )

    with session_scope(session_factory) as session:
        import_batch = session.scalar(select(ImportBatch))

    assert result.transaction_count == 1
    assert import_batch.source_file_hash == "abc123"


def test_normalize_category_label_for_import_is_accent_insensitive():
    assert normalize_category_label_for_import("  Nómina  ") == "nomina"


def test_find_duplicate_transactions_detects_exact_existing_transaction(
    session_factory,
):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        account = service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()
        service.record_manual_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )

    payload = manual_transaction_payload(
        transaction_date=date(2026, 1, 10),
        account_id=account.id,
        category_id=None,
        description="Merchant A",
        amount_minor=1234,
        direction=Direction.OUTFLOW.value,
        transaction_type=TransactionType.EXPENSE.value,
        payment_method="",
        decided_by="Sample User",
    )
    different_amount_payload = payload | {"amount_minor": 1235}

    assert len(
        find_duplicate_transactions(
            session_factory,
            user_profile_id=profile.id,
            payload=payload,
        )
    ) == 1
    assert (
        find_duplicate_transactions(
            session_factory,
            user_profile_id=profile.id,
            payload=different_amount_payload,
        )
        == []
    )


def test_repository_lists_active_user_profiles(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        active_profile = repository.add_user_profile(display_name="Sample User")
        repository.add_user_profile(display_name="Inactive User", is_active=False)

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        active_profiles = repository.list_user_profiles()
        all_profiles = repository.list_user_profiles(include_inactive=True)

    assert [profile.id for profile in active_profiles] == [active_profile.id]
    assert [profile.display_name for profile in all_profiles] == [
        "Inactive User",
        "Sample User",
    ]
