from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lxcell.db.models import (
    Base,
    Category,
    CategoryMapping,
    ClassificationDecision,
    ClassificationRule,
    ImportBatch,
    ReimbursementMatch,
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
    ImportSourceSystem,
    ImportStatus,
    PaymentMethod,
    ReimbursementMatchStatus,
    TransactionReviewStatus,
    TransactionSourceType,
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
from lxcell.services import (
    AccountingService,
    CategoryTotal,
    ReportAmountBasis,
    StatementPdfImportResult,
)
from lxcell.ui.streamlit_app import (
    HISTORICAL_EXCEL_PREVIEW_VERSION,
    STATEMENT_PDF_PROTECTED_IMPORT_CONFIRMATION_TEXT,
    STATEMENT_PDF_PREVIEW_VERSION,
    account_label_for_transaction_table,
    apply_historical_category_mapping_to_plan,
    canonical_key_from_name,
    category_label_for_transaction_table,
    category_table_rows,
    category_table_success_message,
    category_total_table_rows,
    classification_decision_summary,
    classification_rule_editor_rows,
    classification_rule_table_rows,
    classification_rule_table_success_message,
    confirm_historical_excel_import_from_preview,
    confirm_imported_transaction_review_from_ui,
    confirm_statement_pdf_import_from_preview,
    completed_historical_import_batch_fallback,
    completed_statement_pdf_import_batch_fallback,
    create_category_from_ui,
    create_classification_rule_from_ui,
    apply_classification_rule_table_changes,
    edited_category_payload,
    edited_classification_rule_payload,
    edited_transaction_payload,
    find_duplicate_transactions,
    format_signed_amount_minor,
    friendly_integrity_error_message,
    historical_preview_can_render,
    historical_category_import_plan,
    historical_category_mapping_suggestions,
    historical_import_validation_blockers,
    hard_delete_classification_rule_for_ui,
    load_latest_classification_decisions,
    manual_transaction_payload,
    matching_classification_rule_for_transaction_review,
    matching_classification_rule_for_review,
    normalize_category_label_for_import,
    parse_amount_minor,
    parse_optional_amount_minor,
    preview_candidate_rows,
    report_amount_basis_from_label,
    report_amount_basis_labels,
    reimbursement_match_label,
    reimbursement_match_table_rows,
    repeated_merchant_base_pattern,
    refresh_pending_classifications_after_rule_update,
    refresh_pending_classifications_from_ui,
    statement_pdf_candidate_rows,
    statement_pdf_direction_rows,
    statement_pdf_issue_rows,
    statement_pdf_import_success_message,
    statement_pdf_preview_can_render,
    statement_pdf_protected_import_confirmation_matches,
    statement_pdf_protected_candidate_count,
    source_totals_by_category_minor,
    source_totals_by_month_minor,
    stored_historical_preview_matches,
    stored_statement_pdf_preview_matches,
    soft_delete_transaction_for_ui,
    totals_table_rows,
    transaction_table_rows,
    transaction_review_queue_rows,
    tracking_comparison_rows,
    tracking_unmatched_category_rows,
    transaction_table_has_locked_period_changes,
    update_category_for_ui,
    update_classification_rule_for_ui,
    suggested_classification_rule_pattern,
    suggested_payment_method_for_review,
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


def test_parse_optional_amount_minor_accepts_blank_and_amount():
    assert parse_optional_amount_minor("") is None
    assert parse_optional_amount_minor("  ") is None
    assert parse_optional_amount_minor("12,34") == 1234


def test_format_signed_amount_minor_preserves_sign():
    assert format_signed_amount_minor(1234) == "12.34"
    assert format_signed_amount_minor(-1234) == "-12.34"


def test_report_amount_basis_labels_map_to_service_values():
    assert report_amount_basis_labels() == ["Personal", "Bruto"]
    assert report_amount_basis_from_label("Personal") == ReportAmountBasis.PERSONAL
    assert report_amount_basis_from_label("Bruto") == ReportAmountBasis.GROSS


def test_category_total_table_rows_format_report_totals():
    rows = category_total_table_rows(
        [
            CategoryTotal(
                category_id=1,
                category_name="Category A",
                category_type=CategoryType.EXPENSE,
                amount_minor=-1234,
            ),
            CategoryTotal(
                category_id=None,
                category_name=None,
                category_type=None,
                amount_minor=500,
            ),
        ]
    )

    assert rows == [
        {
            "categoría": "Category A",
            "tipo": CategoryType.EXPENSE.value,
            "importe": "-12.34",
        },
        {
            "categoría": "Sin categoría",
            "tipo": "",
            "importe": "5.00",
        },
    ]


def test_create_classification_rule_from_ui_persists_rule(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        category = service.create_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()

    create_classification_rule_from_ui(
        session_factory,
        user_profile_id=profile.id,
        name="Merchant A",
        pattern="merchant a",
        category_id=category.id,
        rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
        match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
        direction=Direction.OUTFLOW,
        transaction_type=TransactionType.EXPENSE,
        payment_method=PaymentMethod.CARD,
        amount_min_minor=100,
        amount_max_minor=5000,
        priority=10,
        confidence=Decimal("0.9500"),
        auto_apply=True,
    )

    with session_scope(session_factory) as session:
        rule = session.scalar(select(ClassificationRule))
        rows = classification_rule_table_rows([rule])

    assert rule is not None
    assert rule.name == "Merchant A"
    assert rule.category_id == category.id
    assert rule.direction == Direction.OUTFLOW
    assert rule.transaction_type == TransactionType.EXPENSE
    assert rule.payment_method == PaymentMethod.CARD
    assert rule.amount_min_minor == 100
    assert rule.amount_max_minor == 5000
    assert rule.priority == 10
    assert rule.auto_apply is True
    assert rows == [
        {
            "nombre": "Merchant A",
            "patrón": "merchant a",
            "categoría": "Category A",
            "dirección": Direction.OUTFLOW.value,
            "tipo": TransactionType.EXPENSE.value,
            "confianza": "95%",
            "auto": "sí",
        }
    ]


def test_classification_rule_editor_rows_include_editable_fields(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        category = service.create_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A",
            pattern="merchant a",
            category_id=category.id,
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            amount_min_minor=100,
            amount_max_minor=5000,
            priority=10,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        session.flush()

    rows = classification_rule_editor_rows(
        [rule],
        category_labels={category.id: category.name},
    )

    assert rows == [
        {
            "id": rule.id,
            "nombre": "Merchant A",
            "patrón": "merchant a",
            "categoría": "Category A",
            "tipo_regla": ClassificationRuleType.DESCRIPTION_CONTAINS.value,
            "campo": ClassificationMatchField.DESCRIPTION_CLEAN.value,
            "dirección": Direction.OUTFLOW.value,
            "tipo": TransactionType.EXPENSE.value,
            "método": PaymentMethod.CARD.value,
            "importe_mínimo": "1.00",
            "importe_máximo": "50.00",
            "prioridad": 10,
            "confianza": 95,
            "autoaplicar": True,
            "activa": True,
            "acción": "",
        }
    ]


def test_edited_classification_rule_payload_normalizes_values():
    payload = edited_classification_rule_payload(
        {
            "nombre": " Merchant B ",
            "patrón": " merchant b ",
            "categoría": "Sin categoría",
            "tipo_regla": ClassificationRuleType.DESCRIPTION_CONTAINS.value,
            "campo": ClassificationMatchField.DESCRIPTION_CLEAN.value,
            "dirección": "",
            "tipo": TransactionType.EXPENSE.value,
            "método": "",
            "importe_mínimo": "1.00",
            "importe_máximo": "",
            "prioridad": 3.0,
            "confianza": 90.0,
            "autoaplicar": False,
            "activa": True,
        },
        category_ids_by_label={"Sin categoría": None},
    )

    assert payload == {
        "name": "Merchant B",
        "pattern": "merchant b",
        "category_id": None,
        "rule_type": ClassificationRuleType.DESCRIPTION_CONTAINS,
        "match_field": ClassificationMatchField.DESCRIPTION_CLEAN,
        "direction": None,
        "transaction_type": TransactionType.EXPENSE,
        "payment_method": None,
        "amount_min_minor": 100,
        "amount_max_minor": None,
        "priority": 3,
        "confidence": Decimal("0.9000"),
        "auto_apply": False,
        "is_active": True,
    }


def test_apply_classification_rule_table_changes_updates_rule(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        category = service.create_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A",
            pattern="merchant a",
            category_id=category.id,
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            priority=10,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        session.flush()

    updated_count, hard_deleted_count, unlinked_decision_count = (
        apply_classification_rule_table_changes(
            session_factory,
            user_profile_id=profile.id,
            original_rules=[rule],
            edited_rows=[
                {
                    "id": rule.id,
                    "nombre": "Merchant B",
                    "patrón": "merchant b",
                    "categoría": "Category A",
                    "tipo_regla": ClassificationRuleType.DESCRIPTION_REGEX.value,
                    "campo": ClassificationMatchField.DESCRIPTION_RAW.value,
                    "dirección": Direction.OUTFLOW.value,
                    "tipo": TransactionType.EXPENSE.value,
                    "método": "",
                    "importe_mínimo": "",
                    "importe_máximo": "60.00",
                    "prioridad": 5,
                    "confianza": 90,
                    "autoaplicar": False,
                    "activa": False,
                    "acción": "",
                }
            ],
            category_ids_by_label={"Sin categoría": None, "Category A": category.id},
        )
    )

    with session_scope(session_factory) as session:
        stored_rule = session.get(ClassificationRule, rule.id)

    assert updated_count == 1
    assert stored_rule.name == "Merchant B"
    assert stored_rule.pattern == "merchant b"
    assert stored_rule.rule_type == ClassificationRuleType.DESCRIPTION_REGEX
    assert stored_rule.match_field == ClassificationMatchField.DESCRIPTION_RAW
    assert stored_rule.payment_method is None
    assert stored_rule.amount_max_minor == 6000
    assert stored_rule.priority == 5
    assert stored_rule.confidence == Decimal("0.9000")
    assert stored_rule.auto_apply is False
    assert stored_rule.is_active is False
    assert hard_deleted_count == 0
    assert unlinked_decision_count == 0
    assert classification_rule_table_success_message(
        updated_count=updated_count,
        hard_deleted_count=hard_deleted_count,
        unlinked_decision_count=unlinked_decision_count,
    ) == (
        "1 regla actualizada."
    )


def test_update_classification_rule_for_ui_falls_back_for_legacy_service(
    session_factory,
):
    class LegacyService:
        def __init__(self, repository):
            self.repository = repository

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        category = service.create_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A",
            pattern="merchant a",
            category_id=category.id,
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            priority=10,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        session.flush()

        update_classification_rule_for_ui(
            LegacyService(repository),
            user_profile_id=profile.id,
            classification_rule_id=rule.id,
            name="Merchant B",
            pattern="merchant b",
            category_id=category.id,
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=None,
            amount_min_minor=None,
            amount_max_minor=None,
            priority=5,
            confidence=Decimal("0.9000"),
            auto_apply=False,
            is_active=True,
        )

    with session_scope(session_factory) as session:
        stored_rule = session.get(ClassificationRule, rule.id)

    assert stored_rule.name == "Merchant B"
    assert stored_rule.pattern == "merchant b"
    assert stored_rule.payment_method is None
    assert stored_rule.priority == 5
    assert stored_rule.confidence == Decimal("0.9000")
    assert stored_rule.auto_apply is False
    assert stored_rule.is_active is True


def test_apply_classification_rule_table_changes_hard_deletes_rule(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A",
            pattern="merchant a",
            category_id=category.id,
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            transaction_type=TransactionType.EXPENSE,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        session.flush()
        repository.add_classification_decision(
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

    updated_count, hard_deleted_count, unlinked_decision_count = (
        apply_classification_rule_table_changes(
            session_factory,
            user_profile_id=profile.id,
            original_rules=[rule],
            edited_rows=[
                {
                    "id": rule.id,
                    "nombre": "Merchant A",
                    "patrón": "merchant a",
                    "categoría": "Category A",
                    "tipo_regla": ClassificationRuleType.DESCRIPTION_CONTAINS.value,
                    "campo": ClassificationMatchField.DESCRIPTION_CLEAN.value,
                    "dirección": "",
                    "tipo": TransactionType.EXPENSE.value,
                    "método": "",
                    "importe_mínimo": "",
                    "importe_máximo": "",
                    "prioridad": 100,
                    "confianza": 95,
                    "autoaplicar": True,
                    "activa": True,
                    "acción": "Borrar definitivamente",
                }
            ],
            category_ids_by_label={"Sin categoría": None, "Category A": category.id},
        )
    )

    with session_scope(session_factory) as session:
        stored_rule = session.get(ClassificationRule, rule.id)
        decisions = list(session.scalars(select(ClassificationDecision)))

    assert updated_count == 0
    assert hard_deleted_count == 1
    assert unlinked_decision_count == 1
    assert stored_rule is None
    assert decisions[0].classification_rule_id is None
    assert classification_rule_table_success_message(
        updated_count=updated_count,
        hard_deleted_count=hard_deleted_count,
        unlinked_decision_count=unlinked_decision_count,
    ) == (
        "1 regla borrada definitivamente. "
        "1 decisión conserva la auditoría sin enlace a la regla."
    )


def test_hard_delete_classification_rule_for_ui_falls_back_for_legacy_service(
    session_factory,
):
    class LegacyService:
        def __init__(self, repository):
            self.repository = repository

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A",
            pattern="merchant a",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            confidence=Decimal("0.9500"),
        )
        session.flush()

        unlinked_count = hard_delete_classification_rule_for_ui(
            LegacyService(repository),
            user_profile_id=profile.id,
            classification_rule_id=rule.id,
        )

    with session_scope(session_factory) as session:
        stored_rule = session.get(ClassificationRule, rule.id)

    assert unlinked_count == 0
    assert stored_rule is None


def test_canonical_key_from_name_is_simple_and_stable():
    assert canonical_key_from_name("Category A") == "category_a"
    assert canonical_key_from_name("  Categoría / Especial  ") == "categoria_especial"


def test_create_category_from_ui_uses_generated_canonical_key(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()

    category = create_category_from_ui(
        session_factory,
        user_profile_id=profile.id,
        name="Category A",
        category_type=CategoryType.EXPENSE,
        canonical_key=None,
        display_order=3,
    )

    with session_scope(session_factory) as session:
        stored_category = session.get(Category, category.id)

    assert stored_category.name == "Category A"
    assert stored_category.category_type == CategoryType.EXPENSE
    assert stored_category.canonical_key == "category_a"
    assert stored_category.display_order == 3


def test_create_category_from_ui_accepts_explicit_canonical_key(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()

    category = create_category_from_ui(
        session_factory,
        user_profile_id=profile.id,
        name="Category A",
        category_type=CategoryType.EXPENSE,
        canonical_key="custom_key",
        display_order=0,
    )

    with session_scope(session_factory) as session:
        stored_category = session.get(Category, category.id)

    assert stored_category.canonical_key == "custom_key"


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


def test_friendly_integrity_error_message_handles_duplicate_counterparty_name():
    error = IntegrityError(
        statement=None,
        params=None,
        orig=Exception(
            "UNIQUE constraint failed: "
            "counterparties.user_profile_id, counterparties.normalized_name"
        ),
    )

    assert (
        friendly_integrity_error_message(error)
        == "Ya existe una contraparte con ese nombre en este perfil."
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


def test_transaction_table_rows_mark_shared_transactions(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        transaction = service.record_manual_transaction(
            user_profile_id=profile.id,
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

    rows = transaction_table_rows(
        [transaction],
        account_labels={account.id: account.name},
        category_labels={category.id: category.name},
        shared_transaction_ids={transaction.id},
    )

    assert rows[0]["compartida"] == "sí"


def test_transaction_review_queue_rows_show_latest_classification_suggestion(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
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
        decision = repository.add_classification_decision(
            transaction_id=transaction.id,
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            decision_source=ClassificationDecisionSource.DETERMINISTIC_RULE,
            decision_status=ClassificationDecisionStatus.SUGGESTED,
            decided_by="system",
        )
        session.flush()

    decision_by_transaction_id = {transaction.id: decision}
    rows = transaction_review_queue_rows(
        [transaction],
        account_labels={account.id: account.name},
        category_labels={None: "Sin categoría", category.id: category.name},
        decision_by_transaction_id=decision_by_transaction_id,
    )

    assert rows == [
        {
            "id": transaction.id,
            "fecha": date(2026, 1, 10),
            "descripcion": "Merchant A",
            "importe": "12.34",
            "direccion": Direction.OUTFLOW.value,
            "cuenta": "Primary account",
            "categoria actual": "Sin categoría",
            "sugerencia": (
                "Category A · expense · deterministic_rule · suggested"
            ),
        }
    ]
    assert (
        classification_decision_summary(
            decision,
            {None: "Sin categoría", category.id: category.name},
        )
        == "Category A · expense · deterministic_rule · suggested"
    )


def test_review_suggests_card_payment_method_from_transaction_text(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        account = service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A Tarjeta: 123456******7890",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        transaction_with_method = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 11),
            description_clean="Merchant B Tarjeta: 123456******7890",
            amount_minor=500,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.DIRECT_DEBIT,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        peer_to_peer_transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 12),
            description_clean="Transfer via Tikkie",
            amount_minor=2500,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        peer_to_peer_transaction_with_existing_method = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 13),
            description_clean="Pago Bizum",
            amount_minor=2500,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()

    assert suggested_payment_method_for_review(transaction) == PaymentMethod.CARD
    assert (
        suggested_payment_method_for_review(transaction_with_method)
        == PaymentMethod.DIRECT_DEBIT
    )
    assert (
        suggested_payment_method_for_review(peer_to_peer_transaction)
        == PaymentMethod.PEER_TO_PEER
    )
    assert (
        suggested_payment_method_for_review(peer_to_peer_transaction_with_existing_method)
        == PaymentMethod.PEER_TO_PEER
    )


def test_suggested_classification_rule_pattern_removes_card_number(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        account = service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A Tarjeta: 123456******7890",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()

    assert suggested_classification_rule_pattern(transaction) == "Merchant A"


def test_suggested_classification_rule_pattern_uses_reusable_merchant_base(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        account = service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        session.flush()
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant Market A Merchant Market 1497, City",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()

    assert suggested_classification_rule_pattern(transaction) == "Merchant Market"
    assert repeated_merchant_base_pattern("Merchant Shop Merchant Shop") == "Merchant Shop"
    assert (
        repeated_merchant_base_pattern("Merchant Transit A abc123def456")
        == "Merchant Transit"
    )


def test_confirm_imported_transaction_review_from_ui_accepts_classification(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
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

    decision_id, learned_rule_id, reclassified_count = (
        confirm_imported_transaction_review_from_ui(
            session_factory,
            user_profile_id=profile.id,
            transaction_id=transaction.id,
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            decided_by="Sample User",
        )
    )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        stored_transaction = repository.get_transaction(
            transaction_id=transaction.id,
            user_profile_id=profile.id,
        )
        decisions = repository.list_classification_decisions(
            transaction_id=transaction.id,
            user_profile_id=profile.id,
        )
    latest_decisions = load_latest_classification_decisions(
        session_factory,
        user_profile_id=profile.id,
        transaction_ids=[transaction.id],
    )

    assert stored_transaction.category_id == category.id
    assert stored_transaction.payment_method == PaymentMethod.CARD
    assert stored_transaction.review_status == TransactionReviewStatus.USER_CONFIRMED
    assert [decision.decision_status for decision in decisions] == [
        ClassificationDecisionStatus.SUPERSEDED,
        ClassificationDecisionStatus.ACCEPTED,
    ]
    assert decisions[1].id == decision_id
    assert learned_rule_id is None
    assert reclassified_count == 0
    assert latest_decisions[transaction.id].id == decision_id


def test_confirm_imported_transaction_review_from_ui_can_create_learned_rule(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A Tarjeta: 123456******7890",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()

    decision_id, learned_rule_id, reclassified_count = (
        confirm_imported_transaction_review_from_ui(
            session_factory,
            user_profile_id=profile.id,
            transaction_id=transaction.id,
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            decided_by="Sample User",
            create_learned_rule=True,
            learned_rule_pattern=suggested_classification_rule_pattern(transaction),
        )
    )

    with session_scope(session_factory) as session:
        rule = session.get(ClassificationRule, learned_rule_id)
        decision = session.get(ClassificationDecision, decision_id)

    assert decision is not None
    assert reclassified_count == 0
    assert rule is not None
    assert rule.pattern == "Merchant A"
    assert rule.category_id == category.id
    assert rule.transaction_type == TransactionType.EXPENSE
    assert rule.payment_method == PaymentMethod.CARD
    assert rule.direction == Direction.OUTFLOW
    assert rule.confidence == Decimal("0.9500")
    assert rule.auto_apply is True


def test_matching_classification_rule_for_review_reuses_existing_rule(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        category = service.create_category(
            user_profile_id=profile.id,
            name="Category A",
            category_type=CategoryType.EXPENSE,
            canonical_key="category_a",
        )
        session.flush()
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Merchant A",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="Merchant A",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            direction=Direction.OUTFLOW,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        session.flush()
        rules = repository.list_classification_rules(user_profile_id=profile.id)

    assert (
        matching_classification_rule_for_review(
            rules,
            pattern="merchant a",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.CARD,
            direction=Direction.OUTFLOW,
        ).id
        == rule.id
    )


def test_matching_classification_rule_for_transaction_review_reuses_broad_rule(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Payment App",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="Payment App",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=None,
            direction=Direction.OUTFLOW,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Payment App transfer to person",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.PEER_TO_PEER,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()
        rules = repository.list_classification_rules(user_profile_id=profile.id)

    match = matching_classification_rule_for_transaction_review(
        rules,
        transaction=transaction,
        category_id=category.id,
        transaction_type=TransactionType.EXPENSE,
        payment_method=PaymentMethod.PEER_TO_PEER,
    )

    assert match is not None
    assert match.id == rule.id


def test_confirm_imported_transaction_review_reuses_broad_learned_rule(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        existing_rule = service.create_classification_rule(
            user_profile_id=profile.id,
            name="Payment App",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="Payment App",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=None,
            direction=Direction.OUTFLOW,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Payment App transfer to person",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.PEER_TO_PEER,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()

    decision_id, learned_rule_id, reclassified_count = (
        confirm_imported_transaction_review_from_ui(
            session_factory,
            user_profile_id=profile.id,
            transaction_id=transaction.id,
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.PEER_TO_PEER,
            decided_by="Sample User",
            create_learned_rule=True,
            learned_rule_pattern="Payment App transfer to person",
        )
    )

    with session_scope(session_factory) as session:
        rules = session.scalars(select(ClassificationRule)).all()
        decision = session.get(ClassificationDecision, decision_id)

    assert decision is not None
    assert learned_rule_id == existing_rule.id
    assert reclassified_count == 0
    assert len(rules) == 1
    assert rules[0].id == existing_rule.id


def test_refresh_pending_classifications_from_ui_applies_existing_rules(
    session_factory,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        service.create_classification_rule(
            user_profile_id=profile.id,
            name="Payment App",
            rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
            match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
            pattern="Payment App",
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.PEER_TO_PEER,
            direction=Direction.OUTFLOW,
            confidence=Decimal("0.9500"),
            auto_apply=True,
        )
        transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Payment App transfer to person",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.PEER_TO_PEER,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()

    reclassified_count = refresh_pending_classifications_from_ui(
        session_factory,
        user_profile_id=profile.id,
    )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        refreshed = repository.get_transaction(
            transaction_id=transaction.id,
            user_profile_id=profile.id,
        )
        decisions = repository.list_classification_decisions(
            transaction_id=transaction.id,
            user_profile_id=profile.id,
        )

    assert reclassified_count == 1
    assert refreshed.category_id == category.id
    assert refreshed.payment_method == PaymentMethod.PEER_TO_PEER
    assert refreshed.review_status == TransactionReviewStatus.PENDING_REVIEW
    assert decisions[-1].decision_source == (
        ClassificationDecisionSource.DETERMINISTIC_RULE
    )
    assert decisions[-1].decision_status == ClassificationDecisionStatus.ACCEPTED


def test_learned_rule_refreshes_other_pending_transactions(session_factory):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        service = AccountingService(repository)
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
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
        session.flush()
        reviewed_transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Payment App A transfer to person",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.PEER_TO_PEER,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        pending_transaction = repository.add_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 11),
            description_clean="Payment App A transfer to another person",
            amount_minor=500,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.PEER_TO_PEER,
            source_type=TransactionSourceType.BANK_IMPORT,
            review_status=TransactionReviewStatus.PENDING_REVIEW,
        )
        session.flush()

    decision_id, learned_rule_id, reclassified_count = (
        confirm_imported_transaction_review_from_ui(
            session_factory,
            user_profile_id=profile.id,
            transaction_id=reviewed_transaction.id,
            category_id=category.id,
            transaction_type=TransactionType.EXPENSE,
            payment_method=PaymentMethod.PEER_TO_PEER,
            decided_by="Sample User",
            create_learned_rule=True,
            learned_rule_pattern="Payment App A",
        )
    )

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        reviewed = repository.get_transaction(
            transaction_id=reviewed_transaction.id,
            user_profile_id=profile.id,
        )
        pending = repository.get_transaction(
            transaction_id=pending_transaction.id,
            user_profile_id=profile.id,
        )
        pending_decisions = repository.list_classification_decisions(
            transaction_id=pending_transaction.id,
            user_profile_id=profile.id,
        )

    assert decision_id is not None
    assert learned_rule_id is not None
    assert reclassified_count == 1
    assert reviewed.review_status == TransactionReviewStatus.USER_CONFIRMED
    assert pending.review_status == TransactionReviewStatus.PENDING_REVIEW
    assert pending.category_id == category.id
    assert pending.payment_method == PaymentMethod.PEER_TO_PEER
    assert pending_decisions[-1].decision_source == (
        ClassificationDecisionSource.DETERMINISTIC_RULE
    )
    assert pending_decisions[-1].decision_status == ClassificationDecisionStatus.ACCEPTED


def test_reimbursement_match_table_rows_format_suggestions(session_factory):
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(display_name="Sample User")
        session.flush()
        account = service.create_account(
            user_profile_id=profile.id,
            name="Primary account",
            account_type=AccountType.CHECKING,
        )
        counterparty = service.create_counterparty(
            user_profile_id=profile.id,
            display_name="Counterparty A",
        )
        session.flush()
        expense = service.record_manual_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 1, 10),
            description_clean="Merchant A",
            amount_minor=1000,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        reimbursement = service.record_manual_transaction(
            user_profile_id=profile.id,
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
            user_profile_id=profile.id,
            transaction_id=expense.id,
            counterparty_id=counterparty.id,
            decided_by="Sample User",
        )
        reimbursement_match = service.repository.add_reimbursement_match(
            user_profile_id=profile.id,
            shared_expense_allocation_id=allocation.id,
            reimbursement_transaction_id=reimbursement.id,
            matched_amount_minor=500,
            status=ReimbursementMatchStatus.SUGGESTED,
            confidence=Decimal("0.9500"),
            notes="Alias de contraparte e importe recuperable exacto.",
        )
        session.flush()

        rows = reimbursement_match_table_rows([reimbursement_match])
        label = reimbursement_match_label(reimbursement_match)

    assert rows == [
        {
            "id": reimbursement_match.id,
            "contraparte": "Counterparty A",
            "gasto": (
                f"{expense.id} · 2026-01-10 · Merchant A · 10.00"
            ),
            "reembolso": (
                f"{reimbursement.id} · 2026-01-12 · "
                "Transfer from Counterparty A · 5.00"
            ),
            "importe": "5.00",
            "confianza": "95%",
            "motivo": "Alias de contraparte e importe recuperable exacto.",
        }
    ]
    assert label == f"{reimbursement_match.id} · Counterparty A · 5.00"


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
            "periodo": "abierto",
            "saldo": "987.66",
            "hash": "abcdef123456",
        }
    ]
    assert statement_pdf_candidate_rows(
        preview,
        locked_until=date(2026, 9, 30),
    )[0]["periodo"] == "protegido"
    assert statement_pdf_protected_candidate_count(
        preview,
        locked_until=date(2026, 9, 30),
    ) == 1


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


def test_confirm_statement_pdf_import_from_preview_writes_import(session_factory):
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

    preview = PdfStatementPreview(
        source_file_name="sample.pdf",
        source_file_hash="abc123",
        page_count=1,
        candidates=(
            PdfStatementTransactionCandidate(
                row_number_source=1,
                page_number=1,
                transaction_date=date(2026, 2, 1),
                posted_date=date(2026, 2, 1),
                description_raw="Merchant A",
                description_clean="Merchant A",
                amount_minor=1234,
                direction=Direction.OUTFLOW,
                amount_raw="12,34",
                currency="EUR",
                balance_raw="987,66",
                balance_minor=98766,
                payload_raw={},
                content_hash="abc",
            ),
        ),
        issues=(),
    )

    result = confirm_statement_pdf_import_from_preview(
        session_factory,
        user_profile_id=profile.id,
        account_id=account.id,
        source_system=ImportSourceSystem.BANK_PDF,
        preview=preview,
        confirmed_by="Sample User",
        user_confirmed=True,
    )

    with session_scope(session_factory) as session:
        transaction = session.scalar(select(Transaction))

    assert result.transaction_count == 1
    assert transaction.description_clean == "Merchant A"


def test_statement_pdf_import_success_message_includes_skipped_rows():
    message = statement_pdf_import_success_message(
        StatementPdfImportResult(
            import_batch_id=1,
            account_id=2,
            transaction_count=3,
            ignored_protected_count=4,
            matched_existing_count=5,
            marked_duplicate_count=6,
        )
    )

    assert message == (
        "Extracto guardado: 3 transacción(es) creada(s), "
        "4 protegida(s) ignorada(s), "
        "5 duplicada(s) existente(s), "
        "6 duplicada(s) en el archivo"
    )


def test_statement_pdf_protected_import_confirmation_requires_exact_text():
    assert statement_pdf_protected_import_confirmation_matches(
        STATEMENT_PDF_PROTECTED_IMPORT_CONFIRMATION_TEXT
    )
    assert statement_pdf_protected_import_confirmation_matches(
        f"  {STATEMENT_PDF_PROTECTED_IMPORT_CONFIRMATION_TEXT}  "
    )
    assert not statement_pdf_protected_import_confirmation_matches("")
    assert not statement_pdf_protected_import_confirmation_matches(
        "IMPORTAR PROTEGIDAS"
    )
    assert not statement_pdf_protected_import_confirmation_matches(
        STATEMENT_PDF_PROTECTED_IMPORT_CONFIRMATION_TEXT.lower()
    )


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


def test_transaction_table_has_locked_period_changes_detects_protected_edits(
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
        service.update_user_profile_transaction_lock(
            user_profile_id=profile.id,
            transactions_locked_until=date(2026, 1, 31),
        )
        session.flush()
        transaction = service.record_manual_transaction(
            user_profile_id=profile.id,
            account_id=account.id,
            transaction_date=date(2026, 2, 1),
            description_clean="Merchant A",
            amount_minor=1234,
            direction=Direction.OUTFLOW,
            transaction_type=TransactionType.EXPENSE,
            decided_by="Sample User",
        )
        session.flush()

    assert transaction_table_has_locked_period_changes(
        session_factory,
        user_profile_id=profile.id,
        original_transactions=[transaction],
        edited_rows=[
            {
                "id": transaction.id,
                "fecha": date(2026, 1, 30),
                "cuenta": "Primary account",
                "categoria": "Sin categoría",
                "descripcion": "Merchant A",
                "importe": "12.34",
                "direccion": Direction.OUTFLOW.value,
                "tipo": TransactionType.EXPENSE.value,
                "metodo_pago": "",
                "estado": "user_confirmed",
                "eliminar": False,
            }
        ],
        account_ids_by_label={"Primary account": account.id},
        category_ids_by_label={"Sin categoría": None},
    )


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
