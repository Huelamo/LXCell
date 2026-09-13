"""Streamlit UI for local LXCell workflows."""

from __future__ import annotations

import hashlib
import importlib
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lxcell.db.models import (
    Category,
    CategoryMapping,
    ClassificationDecision,
    ClassificationRule,
    Counterparty,
    ImportBatch,
    ReimbursementMatch,
    Transaction,
)
from lxcell.db.runtime import DEFAULT_DATABASE_PATH, create_local_session_factory
from lxcell.db.session import session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    ClassificationDecisionStatus,
    ClassificationMatchField,
    ClassificationRuleType,
    Direction,
    ImportSourceSystem,
    ImportStatus,
    OwnershipType,
    PaymentMethod,
    ReimbursementMatchStatus,
    TransactionReviewStatus,
    TransactionType,
)
from lxcell.importers import HistoricalExcelPreview, PdfStatementPreview
from lxcell.repositories import AccountingRepository
from lxcell.services import (
    AccountingService,
    DeterministicClassificationService,
    ReportAmountBasis,
    ReportingService,
    normalize_classification_text,
)
from lxcell.services.accounting_service import protected_transaction_dates
from lxcell.services.historical_excel_import_service import HistoricalExcelImportService
from lxcell.services.statement_pdf_import_service import StatementPdfImportService

PENDING_DUPLICATE_TRANSACTION_KEY = "lxcell_pending_duplicate_transaction"
HISTORICAL_EXCEL_PREVIEW_KEY = "lxcell_historical_excel_preview"
HISTORICAL_EXCEL_PREVIEW_VERSION = 5
HISTORICAL_EXCEL_ACCOUNT_NAME = "Excel histórico"
STATEMENT_PDF_PREVIEW_KEY = "lxcell_statement_pdf_preview"
STATEMENT_PDF_PREVIEW_VERSION = 1
STATEMENT_PDF_PROTECTED_IMPORT_CONFIRMATION_TEXT = "IMPORTAR PERIODO PROTEGIDO"
CLASSIFICATION_RULE_HARD_DELETE_CONFIRMATION_TEXT = "BORRAR REGLAS"


def run() -> None:
    st.set_page_config(page_title="LXCell", page_icon="LX", layout="wide")
    st.title("LXCell")

    database_path = Path(
        st.sidebar.text_input("Base de datos", value=str(DEFAULT_DATABASE_PATH))
    )
    session_factory = create_local_session_factory(database_path)

    profiles = load_profiles(session_factory)
    selected_profile_id = profile_selector(profiles)

    setup_tab, transaction_tab, review_tab, reports_tab, categories_tab, import_tab = st.tabs(
        [
            "Configuración",
            "Registrar",
            "Transacciones",
            "Informes",
            "Categorías",
            "Importar",
        ]
    )

    with setup_tab:
        render_setup(session_factory, selected_profile_id)
    with transaction_tab:
        render_transaction_form(session_factory, selected_profile_id)
    with review_tab:
        render_transactions(session_factory, selected_profile_id)
    with reports_tab:
        render_reports(session_factory, selected_profile_id)
    with categories_tab:
        render_categories(session_factory, selected_profile_id)
    with import_tab:
        render_import_preview(session_factory, selected_profile_id)


def render_setup(session_factory, selected_profile_id: int | None) -> None:
    st.subheader("Perfil")
    render_flash_success("setup")

    with st.form("create_profile", clear_on_submit=True):
        display_name = st.text_input("Nombre del perfil")
        default_currency = st.text_input("Moneda", value="EUR", max_chars=3)
        locale = st.text_input("Locale", value="es_ES")
        submitted = st.form_submit_button("Crear perfil")
        if submitted:
            try:
                with session_scope(session_factory) as session:
                    service = AccountingService(AccountingRepository(session))
                    profile = service.create_user_profile(
                        display_name=display_name,
                        default_currency=default_currency.upper(),
                        locale=locale,
                    )
                    session.flush()
                    flash_success("setup", f"Perfil creado: {profile.display_name}")
                st.rerun()
            except IntegrityError as exc:
                st.warning(friendly_integrity_error_message(exc))

    if selected_profile_id is None:
        st.info("Crea o selecciona un perfil para añadir cuentas y categorías.")
        return

    profile = load_user_profile(session_factory, selected_profile_id)
    if profile is not None:
        st.subheader("Protección histórica")
        current_lock = profile.transactions_locked_until
        lock_enabled = st.checkbox(
            "Proteger transacciones antiguas",
            value=current_lock is not None,
            key="transactions_locked_until_enabled",
        )
        lock_date = st.date_input(
            "Bloquear hasta",
            value=current_lock or date.today(),
            disabled=not lock_enabled,
            key="transactions_locked_until",
        )
        if st.button("Guardar protección histórica"):
            update_profile_transaction_lock_from_ui(
                session_factory,
                user_profile_id=selected_profile_id,
                transactions_locked_until=lock_date if lock_enabled else None,
            )
            flash_success("setup", "Protección histórica guardada.")
            st.rerun()

    st.subheader("Cuenta")
    with st.form("create_account", clear_on_submit=True):
        account_name = st.text_input("Nombre de la cuenta")
        account_type = st.selectbox("Tipo", enum_values(AccountType))
        ownership_type = st.selectbox("Titularidad", enum_values(OwnershipType))
        currency = st.text_input("Moneda de la cuenta", value="EUR", max_chars=3)
        institution_name = st.text_input("Entidad")
        submitted = st.form_submit_button("Crear cuenta")
        if submitted:
            try:
                with session_scope(session_factory) as session:
                    service = AccountingService(AccountingRepository(session))
                    account = service.create_account(
                        user_profile_id=selected_profile_id,
                        name=account_name,
                        account_type=AccountType(account_type),
                        institution_name=institution_name or None,
                        currency=currency.upper(),
                        ownership_type=OwnershipType(ownership_type),
                    )
                    session.flush()
                    flash_success("setup", f"Cuenta creada: {account.name}")
                st.rerun()
            except IntegrityError as exc:
                st.warning(friendly_integrity_error_message(exc))

    st.subheader("Contrapartes")
    counterparties = load_counterparties(session_factory, selected_profile_id)
    if counterparties:
        st.dataframe(
            pd.DataFrame(counterparty_table_rows(counterparties)),
            use_container_width=True,
            hide_index=True,
        )
    with st.form("create_counterparty", clear_on_submit=True):
        counterparty_name = st.text_input("Nombre de la contraparte")
        aliases_raw = st.text_area("Alias en conceptos", height=80)
        submitted = st.form_submit_button("Crear contraparte")
        if submitted:
            try:
                with session_scope(session_factory) as session:
                    service = AccountingService(AccountingRepository(session))
                    counterparty = service.create_counterparty(
                        user_profile_id=selected_profile_id,
                        display_name=counterparty_name,
                        aliases_raw=aliases_raw,
                    )
                    session.flush()
                    flash_success(
                        "setup",
                        f"Contraparte creada: {counterparty.display_name}",
                    )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except IntegrityError as exc:
                st.warning(friendly_integrity_error_message(exc))

    render_classification_rule_setup(
        session_factory,
        selected_profile_id=selected_profile_id,
    )


def render_classification_rule_setup(
    session_factory,
    *,
    selected_profile_id: int,
) -> None:
    st.subheader("Reglas de clasificación")
    rules = load_classification_rules(
        session_factory,
        selected_profile_id,
        include_inactive=True,
    )
    categories = load_categories(
        session_factory,
        selected_profile_id,
        include_inactive=True,
    )
    category_labels = {
        category.id: category_label_for_transaction_table(category)
        for category in categories
    }
    category_ids_by_label = {"Sin categoría": None} | {
        label: category_id for category_id, label in category_labels.items()
    }

    if rules:
        edited_table = st.data_editor(
            pd.DataFrame(
                classification_rule_editor_rows(
                    rules,
                    category_labels=category_labels,
                )
            ),
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            disabled=["id"],
            column_config=classification_rule_editor_column_config(
                category_options=list(category_ids_by_label),
            ),
            key="classification_rules_editor",
        )
        hard_delete_requested = any(
            row.get("acción") == "Borrar definitivamente"
            for row in edited_table.to_dict("records")
        )
        hard_delete_confirmation = ""
        if hard_delete_requested:
            hard_delete_confirmation = st.text_input(
                "Para borrar reglas definitivamente, escribe BORRAR REGLAS",
                key="classification_rules_hard_delete_confirmation",
            )
        if st.button("Guardar reglas", type="primary"):
            if (
                hard_delete_requested
                and hard_delete_confirmation
                != CLASSIFICATION_RULE_HARD_DELETE_CONFIRMATION_TEXT
            ):
                st.warning(
                    "Escribe BORRAR REGLAS para borrar definitivamente las reglas marcadas."
                )
                return
            try:
                (
                    updated_count,
                    hard_deleted_count,
                    unlinked_decision_count,
                ) = apply_classification_rule_table_changes(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    original_rules=rules,
                    edited_rows=edited_table.to_dict("records"),
                    category_ids_by_label=category_ids_by_label,
                )
            except IntegrityError as exc:
                st.warning(friendly_integrity_error_message(exc))
                return
            except ValueError as exc:
                st.error(str(exc))
                return

            if updated_count == 0 and hard_deleted_count == 0:
                st.info("No hay cambios que guardar.")
                return
            flash_success(
                "setup",
                classification_rule_table_success_message(
                    updated_count=updated_count,
                    hard_deleted_count=hard_deleted_count,
                    unlinked_decision_count=unlinked_decision_count,
                ),
            )
            st.rerun()
    else:
        st.info("Todavía no hay reglas de clasificación.")

    active_categories = [category for category in categories if category.is_active]
    if not active_categories:
        st.info("Crea una categoría activa para añadir reglas de clasificación.")
        return

    active_category_labels = {
        category.id: category.name for category in active_categories
    }
    with st.form("create_classification_rule", clear_on_submit=True):
        name = st.text_input("Nombre de la regla")
        pattern = st.text_input("Texto o patrón")
        category_id = st.selectbox(
            "Categoría",
            options=[category.id for category in active_categories],
            format_func=active_category_labels.get,
        )
        rule_type = st.selectbox(
            "Tipo de regla",
            options=[
                ClassificationRuleType.DESCRIPTION_CONTAINS.value,
                ClassificationRuleType.DESCRIPTION_REGEX.value,
            ],
        )
        match_field = st.selectbox(
            "Campo",
            options=[
                ClassificationMatchField.DESCRIPTION_CLEAN.value,
                ClassificationMatchField.DESCRIPTION_RAW.value,
            ],
        )
        direction = st.selectbox("Dirección", [""] + enum_values(Direction))
        transaction_type = st.selectbox(
            "Tipo de transacción",
            [""] + enum_values(TransactionType),
        )
        payment_method = st.selectbox(
            "Método de pago",
            [""] + enum_values(PaymentMethod),
        )
        min_amount_text = st.text_input("Importe mínimo", placeholder="opcional")
        max_amount_text = st.text_input("Importe máximo", placeholder="opcional")
        priority = st.number_input("Prioridad", min_value=0, value=100, step=1)
        confidence_percent = st.slider("Confianza", min_value=70, max_value=100, value=95)
        auto_apply = st.checkbox("Autoaplicar si no hay conflicto", value=True)
        submitted = st.form_submit_button("Crear regla")
        if submitted:
            try:
                create_classification_rule_from_ui(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    name=name,
                    pattern=pattern,
                    category_id=category_id,
                    rule_type=ClassificationRuleType(rule_type),
                    match_field=ClassificationMatchField(match_field),
                    direction=Direction(direction) if direction else None,
                    transaction_type=(
                        TransactionType(transaction_type)
                        if transaction_type
                        else None
                    ),
                    payment_method=(
                        PaymentMethod(payment_method)
                        if payment_method
                        else None
                    ),
                    amount_min_minor=parse_optional_amount_minor(min_amount_text),
                    amount_max_minor=parse_optional_amount_minor(max_amount_text),
                    priority=int(priority),
                    confidence=(
                        Decimal(confidence_percent) / Decimal("100")
                    ).quantize(Decimal("0.0001")),
                    auto_apply=auto_apply,
                )
                flash_success("setup", "Regla de clasificación creada.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except IntegrityError as exc:
                st.warning(friendly_integrity_error_message(exc))


def render_transaction_form(session_factory, selected_profile_id: int | None) -> None:
    if selected_profile_id is None:
        st.info("Selecciona un perfil para registrar transacciones.")
        return

    accounts, categories = load_accounting_lists(
        session_factory,
        selected_profile_id,
        include_inactive=True,
    )
    if not accounts:
        st.warning("Añade una cuenta antes de registrar transacciones.")
        return

    render_flash_success("transaction")
    render_pending_duplicate_confirmation(session_factory, selected_profile_id)

    with st.form("manual_transaction", clear_on_submit=True):
        transaction_date = st.date_input("Fecha", value=date.today())
        account_id = st.selectbox(
            "Cuenta",
            options=[account.id for account in accounts],
            format_func={account.id: account.name for account in accounts}.get,
        )
        category_options = [None] + [category.id for category in categories]
        category_labels = {None: "Sin categoría"} | {
            category.id: category.name for category in categories
        }
        category_id = st.selectbox(
            "Categoría",
            options=category_options,
            format_func=category_labels.get,
        )
        description = st.text_input("Descripción")
        amount_text = st.text_input("Importe", placeholder="12.34")
        direction = st.segmented_control(
            "Dirección",
            enum_values(Direction),
            default=Direction.OUTFLOW.value,
        )
        transaction_type = st.selectbox("Tipo", enum_values(TransactionType))
        payment_method = st.selectbox(
            "Método de pago", [""] + enum_values(PaymentMethod)
        )
        decided_by = st.text_input("Registrado por", value="local_ui")
        locked_period_override = render_locked_period_override_for_dates(
            session_factory,
            user_profile_id=selected_profile_id,
            transaction_dates=[transaction_date],
            key="manual_transaction_locked_period_override",
        )

        submitted = st.form_submit_button("Registrar transacción")
        if submitted:
            try:
                amount_minor = parse_amount_minor(amount_text)
                payload = manual_transaction_payload(
                    transaction_date=transaction_date,
                    account_id=account_id,
                    category_id=category_id,
                    description=description,
                    amount_minor=amount_minor,
                    direction=direction,
                    transaction_type=transaction_type,
                    payment_method=payment_method,
                    decided_by=decided_by,
                    allow_locked_period_override=locked_period_override,
                )
                duplicates = find_duplicate_transactions(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    payload=payload,
                )
                if duplicates:
                    st.session_state[PENDING_DUPLICATE_TRANSACTION_KEY] = {
                        "user_profile_id": selected_profile_id,
                        "payload": payload,
                        "duplicate_ids": [transaction.id for transaction in duplicates],
                    }
                    st.rerun()
                transaction_id, review_status = record_manual_transaction_from_payload(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    payload=payload,
                )
                flash_success(
                    "transaction",
                    "Transacción registrada: "
                    f"{transaction_id} · {format_review_status(review_status)}",
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def render_transactions(session_factory, selected_profile_id: int | None) -> None:
    if selected_profile_id is None:
        st.info("Selecciona un perfil para ver transacciones.")
        return

    render_flash_success("transactions")
    accounts, categories = load_accounting_lists(
        session_factory,
        selected_profile_id,
        include_inactive=True,
    )
    account_labels = {
        account.id: account_label_for_transaction_table(account)
        for account in accounts
    }
    category_labels = {None: "Sin categoría"} | {
        category.id: category_label_for_transaction_table(category)
        for category in categories
    }
    account_ids_by_label = {
        account_label_for_transaction_table(account): account.id
        for account in accounts
    }
    category_ids_by_label = {"Sin categoría": None} | {
        category_label_for_transaction_table(category): category.id
        for category in categories
    }

    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        transactions = repository.list_transactions(user_profile_id=selected_profile_id)
        shared_transaction_ids = {
            allocation.transaction_id
            for allocation in repository.list_shared_expense_allocations_for_profile(
                user_profile_id=selected_profile_id
            )
        }

    if not transactions:
        st.info("Todavía no hay transacciones.")
        return

    pending_reclassified_count = refresh_pending_classifications_from_ui(
        session_factory,
        user_profile_id=selected_profile_id,
    )
    if pending_reclassified_count:
        flash_success(
            "transactions",
            "Reglas aplicadas a pendientes: "
            f"{pending_reclassified_count} actualizada(s).",
        )
        st.rerun()

    render_imported_transaction_review_queue(
        session_factory,
        selected_profile_id=selected_profile_id,
        transactions=transactions,
        categories=categories,
        account_labels=account_labels,
        category_labels=category_labels,
    )

    st.subheader("Últimas transacciones")
    recent_transactions = transactions[-100:][::-1]
    editor_rows = transaction_table_rows(
        recent_transactions,
        account_labels=account_labels,
        category_labels=category_labels,
        shared_transaction_ids=shared_transaction_ids,
    )
    edited_table = st.data_editor(
        pd.DataFrame(editor_rows),
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=["id", "compartida", "estado"],
        column_config={
            "id": st.column_config.NumberColumn("id"),
            "fecha": st.column_config.DateColumn("fecha", format="YYYY-MM-DD"),
            "descripcion": st.column_config.TextColumn("descripción"),
            "importe": st.column_config.TextColumn("importe"),
            "direccion": st.column_config.SelectboxColumn(
                "dirección",
                options=enum_values(Direction),
                required=True,
            ),
            "tipo": st.column_config.SelectboxColumn(
                "tipo",
                options=enum_values(TransactionType),
                required=True,
            ),
            "cuenta": st.column_config.SelectboxColumn(
                "cuenta",
                options=list(account_ids_by_label),
                required=True,
            ),
            "categoria": st.column_config.SelectboxColumn(
                "categoría",
                options=list(category_ids_by_label),
                required=True,
            ),
            "metodo_pago": st.column_config.SelectboxColumn(
                "método",
                options=[""] + enum_values(PaymentMethod),
                required=False,
            ),
            "compartida": st.column_config.TextColumn("compartida"),
            "estado": st.column_config.TextColumn("estado"),
            "eliminar": st.column_config.CheckboxColumn("eliminar"),
        },
        key="transactions_editor",
    )

    edited_rows = edited_table.to_dict("records")
    marked_for_deletion = [row for row in edited_rows if row["eliminar"]]
    confirm_delete = False
    if marked_for_deletion:
        confirm_delete = st.checkbox(
            "Confirmo que quiero eliminar las transacciones marcadas del libro activo"
        )
    locked_period_override = False
    if transaction_table_has_locked_period_changes(
        session_factory,
        user_profile_id=selected_profile_id,
        original_transactions=recent_transactions,
        edited_rows=edited_rows,
        account_ids_by_label=account_ids_by_label,
        category_ids_by_label=category_ids_by_label,
    ):
        locked_period_override = st.checkbox(
            "Confirmo que quiero modificar transacciones en un periodo protegido",
            key="transaction_table_locked_period_override",
        )

    if st.button("Guardar cambios", type="primary"):
        if marked_for_deletion and not confirm_delete:
            st.warning("Marca la confirmación antes de eliminar transacciones.")
            return
        if (
            transaction_table_has_locked_period_changes(
                session_factory,
                user_profile_id=selected_profile_id,
                original_transactions=recent_transactions,
                edited_rows=edited_rows,
                account_ids_by_label=account_ids_by_label,
                category_ids_by_label=category_ids_by_label,
            )
            and not locked_period_override
        ):
            st.warning("Confirma el permiso adicional para modificar el periodo protegido.")
            return
        try:
            updated_count, deleted_count = apply_transaction_table_changes(
                session_factory,
                user_profile_id=selected_profile_id,
                original_transactions=recent_transactions,
                edited_rows=edited_rows,
                account_ids_by_label=account_ids_by_label,
                category_ids_by_label=category_ids_by_label,
                allow_locked_period_override=locked_period_override,
            )
        except ValueError as exc:
            st.error(str(exc))
            return

        if updated_count == 0 and deleted_count == 0:
            st.info("No hay cambios que guardar.")
            return
        flash_success(
            "transactions",
            transaction_table_success_message(
                updated_count=updated_count,
                deleted_count=deleted_count,
            ),
        )
        st.rerun()

    render_shared_expense_actions(
        session_factory,
        selected_profile_id=selected_profile_id,
        transactions=recent_transactions,
        active_shared_transaction_ids=shared_transaction_ids,
    )


def render_imported_transaction_review_queue(
    session_factory,
    *,
    selected_profile_id: int,
    transactions,
    categories,
    account_labels: dict[int, str],
    category_labels: dict[int | None, str],
) -> None:
    st.subheader("Revisión de importaciones")
    review_transactions = [
        transaction
        for transaction in transactions
        if transaction.review_status == TransactionReviewStatus.PENDING_REVIEW
        and not transaction.is_deleted
    ]
    if not review_transactions:
        st.info("No hay transacciones importadas pendientes de revisión.")
        return

    decision_by_transaction_id = load_latest_classification_decisions(
        session_factory,
        user_profile_id=selected_profile_id,
        transaction_ids=[transaction.id for transaction in review_transactions],
    )
    st.dataframe(
        pd.DataFrame(
            transaction_review_queue_rows(
                review_transactions,
                account_labels=account_labels,
                category_labels=category_labels,
                decision_by_transaction_id=decision_by_transaction_id,
            )
        ),
        use_container_width=True,
        hide_index=True,
    )

    review_transactions = sorted(
        review_transactions,
        key=lambda transaction: (transaction.transaction_date, transaction.id),
    )
    selected_transaction_id = st.selectbox(
        "Transacción pendiente",
        options=[transaction.id for transaction in review_transactions],
        format_func={
            transaction.id: review_transaction_label(transaction)
            for transaction in review_transactions
        }.get,
        key="classification_review_transaction_id",
    )
    selected_transaction = next(
        transaction
        for transaction in review_transactions
        if transaction.id == selected_transaction_id
    )
    latest_decision = decision_by_transaction_id.get(selected_transaction_id)
    st.caption(
        "Sugerencia: "
        + classification_decision_summary(latest_decision, category_labels)
    )

    active_categories = [category for category in categories if category.is_active]
    category_options = [None] + [category.id for category in active_categories]
    suggested_category_id = (
        selected_transaction.category_id
        if selected_transaction.category_id in category_options
        else latest_decision.category_id
        if latest_decision is not None
        and latest_decision.category_id in category_options
        else None
    )
    locked_period_override = render_locked_period_override_for_dates(
        session_factory,
        user_profile_id=selected_profile_id,
        transaction_dates=[selected_transaction.transaction_date],
        key=f"classification_review_locked_period_override_{selected_transaction_id}",
    )

    with st.form(f"classification_review_{selected_transaction_id}"):
        category_id = st.selectbox(
            "Categoría revisada",
            options=category_options,
            index=category_options.index(suggested_category_id),
            format_func=category_labels.get,
        )
        transaction_type = st.selectbox(
            "Tipo revisado",
            options=enum_values(TransactionType),
            index=enum_values(TransactionType).index(
                selected_transaction.transaction_type.value
            ),
        )
        payment_method_options = [None] + list(PaymentMethod)
        suggested_payment_method = suggested_payment_method_for_review(
            selected_transaction
        )
        payment_method = st.selectbox(
            "Método revisado",
            options=payment_method_options,
            index=payment_method_options.index(suggested_payment_method),
            format_func=lambda method: "Sin método"
            if method is None
            else method.value,
        )
        suggested_rule_pattern = suggested_classification_rule_pattern(
            selected_transaction
        )
        create_and_auto_apply_learned_rule = st.checkbox(
            "Crear regla y autoaplicarla si no hay conflicto",
            value=False,
        )
        learned_rule_pattern = st.text_input(
            "Texto para la regla",
            value=suggested_rule_pattern,
        )
        decided_by = st.text_input("Revisado por", value="local_ui")
        submitted = st.form_submit_button("Confirmar revisión", type="primary")
        if submitted:
            try:
                (
                    decision_id,
                    learned_rule_id,
                    reclassified_count,
                ) = confirm_imported_transaction_review_from_ui(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    transaction_id=selected_transaction_id,
                    category_id=category_id,
                    transaction_type=TransactionType(transaction_type),
                    payment_method=payment_method,
                    decided_by=decided_by,
                    allow_locked_period_override=locked_period_override,
                    create_learned_rule=create_and_auto_apply_learned_rule,
                    learned_rule_pattern=learned_rule_pattern,
                    learned_rule_auto_apply=True,
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            message = f"Revisión confirmada: decisión {decision_id}"
            if learned_rule_id is not None:
                message += f" · regla {learned_rule_id}"
            if reclassified_count:
                message += f" · {reclassified_count} pendiente(s) actualizada(s)"
            flash_success(
                "transactions",
                message,
            )
            st.rerun()


def render_shared_expense_actions(
    session_factory,
    *,
    selected_profile_id: int,
    transactions,
    active_shared_transaction_ids: set[int],
) -> None:
    st.subheader("Gastos compartidos")
    counterparties = load_counterparties(session_factory, selected_profile_id)
    if not counterparties:
        st.info("Crea una contraparte en Configuración para marcar gastos compartidos.")
        return

    candidates = [
        transaction
        for transaction in transactions
        if transaction.direction == Direction.OUTFLOW and transaction.amount_minor > 0
    ]
    if not candidates:
        st.info("No hay gastos recientes que puedan marcarse como compartidos.")
        return

    selected_transaction_id = st.selectbox(
        "Transacción",
        options=[transaction.id for transaction in candidates],
        format_func={
            transaction.id: shared_expense_transaction_label(transaction)
            for transaction in candidates
        }.get,
        key="shared_expense_transaction_id",
    )
    selected_transaction = next(
        transaction
        for transaction in candidates
        if transaction.id == selected_transaction_id
    )
    selected_counterparty_id = st.selectbox(
        "Contraparte",
        options=[counterparty.id for counterparty in counterparties],
        format_func={
            counterparty.id: counterparty.display_name
            for counterparty in counterparties
        }.get,
        key="shared_expense_counterparty_id",
    )
    locked_period_override = render_locked_period_override_for_dates(
        session_factory,
        user_profile_id=selected_profile_id,
        transaction_dates=[selected_transaction.transaction_date],
        key=f"shared_expense_locked_period_override_{selected_transaction_id}",
    )

    mark_column, waive_column = st.columns(2)
    with mark_column:
        if st.button("Marcar 50/50", type="primary"):
            try:
                allocation_id, personal_share_minor, recoverable_share_minor = (
                    mark_transaction_shared_50_50_from_ui(
                        session_factory,
                        user_profile_id=selected_profile_id,
                        transaction_id=selected_transaction_id,
                        counterparty_id=selected_counterparty_id,
                        allow_locked_period_override=locked_period_override,
                    )
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            flash_success(
                "transactions",
                "Gasto compartido guardado: "
                f"{allocation_id} · parte propia {format_amount_minor(personal_share_minor)} "
                f"· recuperable {format_amount_minor(recoverable_share_minor)}",
            )
            st.rerun()
    with waive_column:
        disable_waive = selected_transaction_id not in active_shared_transaction_ids
        if st.button("Quitar marca compartida", disabled=disable_waive):
            try:
                waive_shared_expense_from_ui(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    transaction_id=selected_transaction_id,
                    allow_locked_period_override=locked_period_override,
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            flash_success("transactions", "Marca de gasto compartido retirada.")
            st.rerun()

    render_reimbursement_match_review(session_factory, selected_profile_id)


def render_reimbursement_match_review(session_factory, selected_profile_id: int) -> None:
    st.subheader("Reembolsos")
    if st.button("Buscar sugerencias de reembolso"):
        with session_scope(session_factory) as session:
            service = AccountingService(AccountingRepository(session))
            result = service.refresh_reimbursement_match_suggestions(
                user_profile_id=selected_profile_id,
                decided_by="local_ui",
            )
        flash_success(
            "transactions",
            "Sugerencias de reembolso actualizadas: "
            f"{result.created_count} nueva(s), {result.existing_count} revisada(s).",
        )
        st.rerun()

    matches = load_reimbursement_matches(
        session_factory,
        user_profile_id=selected_profile_id,
        statuses=[ReimbursementMatchStatus.SUGGESTED],
    )
    if not matches:
        st.info("No hay sugerencias de reembolso pendientes.")
        return

    st.dataframe(
        pd.DataFrame(reimbursement_match_table_rows(matches)),
        use_container_width=True,
        hide_index=True,
    )

    selected_match_id = st.selectbox(
        "Sugerencia",
        options=[match.id for match in matches],
        format_func={match.id: reimbursement_match_label(match) for match in matches}.get,
        key="reimbursement_match_id",
    )
    selected_match = next(match for match in matches if match.id == selected_match_id)
    locked_period_override = render_locked_period_override_for_dates(
        session_factory,
        user_profile_id=selected_profile_id,
        transaction_dates=[
            selected_match.shared_expense_allocation.transaction.transaction_date,
            selected_match.reimbursement_transaction.transaction_date,
        ],
        key=f"reimbursement_match_locked_period_override_{selected_match_id}",
    )

    confirm_column, reject_column = st.columns(2)
    with confirm_column:
        if st.button("Confirmar reembolso", type="primary"):
            try:
                confirm_reimbursement_match_from_ui(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    reimbursement_match_id=selected_match_id,
                    allow_locked_period_override=locked_period_override,
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            flash_success("transactions", "Reembolso confirmado.")
            st.rerun()
    with reject_column:
        if st.button("Rechazar sugerencia"):
            try:
                reject_reimbursement_match_from_ui(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    reimbursement_match_id=selected_match_id,
                    allow_locked_period_override=locked_period_override,
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            flash_success("transactions", "Sugerencia de reembolso rechazada.")
            st.rerun()


def render_reports(session_factory, selected_profile_id: int | None) -> None:
    if selected_profile_id is None:
        st.info("Selecciona un perfil para ver informes.")
        return

    today = date.today()
    start_column, end_column, basis_column, transfer_column = st.columns(4)
    with start_column:
        start_date = st.date_input(
            "Desde",
            value=date(today.year, today.month, 1),
            key="report_start_date",
        )
    with end_column:
        end_date = st.date_input("Hasta", value=today, key="report_end_date")
    with basis_column:
        amount_basis_label = st.segmented_control(
            "Base",
            report_amount_basis_labels(),
            default="Personal",
            key="report_amount_basis",
        )
    with transfer_column:
        include_transfers = st.checkbox(
            "Incluir transferencias",
            key="report_include_transfers",
        )

    amount_basis = report_amount_basis_from_label(amount_basis_label or "Personal")
    if start_date > end_date:
        st.warning("La fecha inicial no puede ser posterior a la fecha final.")
        return

    with session_scope(session_factory) as session:
        reporting_service = ReportingService(AccountingRepository(session))
        cashflow_summary = reporting_service.summarize_cashflow(
            user_profile_id=selected_profile_id,
            start_date=start_date,
            end_date=end_date,
            include_transfers=include_transfers,
            amount_basis=amount_basis,
        )
        category_totals = reporting_service.summarize_by_category(
            user_profile_id=selected_profile_id,
            start_date=start_date,
            end_date=end_date,
            include_transfers=include_transfers,
            amount_basis=amount_basis,
        )

    st.subheader("Resumen")
    inflow_column, outflow_column, net_column, neutral_column = st.columns(4)
    inflow_column.metric("Ingresos", format_amount_minor(cashflow_summary.inflow_minor))
    outflow_column.metric("Gastos", format_amount_minor(cashflow_summary.outflow_minor))
    net_column.metric("Neto", format_signed_amount_minor(cashflow_summary.net_minor))
    neutral_column.metric("Neutral", format_amount_minor(cashflow_summary.neutral_minor))

    st.subheader("Categorías")
    rows = category_total_table_rows(category_totals)
    if not rows:
        st.info("No hay movimientos en el periodo seleccionado.")
        return
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


def render_create_category_form(session_factory, *, selected_profile_id: int) -> None:
    st.subheader("Crear categoría")
    show_category_advanced = st.checkbox(
        "Opciones avanzadas",
        key="create_category_advanced",
    )
    with st.form("create_category", clear_on_submit=True):
        category_name = st.text_input("Nombre de la categoría")
        category_type = st.selectbox("Tipo de categoría", enum_values(CategoryType))
        canonical_key = ""
        display_order = 0
        if show_category_advanced:
            canonical_key = st.text_input("Clave canónica")
            display_order = st.number_input("Orden", min_value=0, step=1)
        submitted = st.form_submit_button("Crear categoría")
        if submitted:
            try:
                category = create_category_from_ui(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    name=category_name,
                    category_type=CategoryType(category_type),
                    canonical_key=canonical_key or None,
                    display_order=int(display_order),
                )
                flash_success("categories", f"Categoría creada: {category.name}")
                st.rerun()
            except IntegrityError as exc:
                st.warning(friendly_integrity_error_message(exc))


def render_categories(session_factory, selected_profile_id: int | None) -> None:
    if selected_profile_id is None:
        st.info("Selecciona un perfil para ver categorías.")
        return

    render_flash_success("categories")
    render_create_category_form(
        session_factory,
        selected_profile_id=selected_profile_id,
    )
    st.divider()

    show_advanced = st.checkbox("Vista avanzada")
    categories = load_categories(
        session_factory,
        selected_profile_id,
        include_inactive=show_advanced,
    )

    st.subheader("Categorías")
    if not categories:
        st.info("Todavía no hay categorías.")
        return

    category_column_config = {
        "id": st.column_config.NumberColumn("id"),
        "nombre": st.column_config.TextColumn("nombre", required=True),
        "tipo": st.column_config.SelectboxColumn(
            "tipo",
            options=enum_values(CategoryType),
            required=True,
        ),
        "orden": st.column_config.NumberColumn("orden", min_value=0, step=1),
        "accion": st.column_config.SelectboxColumn(
            "acción",
            options=["", "Eliminar"],
            required=False,
        ),
    }
    if show_advanced:
        category_column_config["estado"] = st.column_config.TextColumn("estado")
        category_column_config["accion"] = st.column_config.SelectboxColumn(
            "acción",
            options=["", "Eliminar", "Reactivar"],
            required=False,
        )
        category_column_config["clave"] = st.column_config.TextColumn(
            "clave",
            required=True,
        )

    edited_table = st.data_editor(
        pd.DataFrame(category_table_rows(categories, show_advanced=show_advanced)),
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=["id", "estado"],
        column_config=category_column_config,
        key="categories_editor",
    )

    edited_rows = edited_table.to_dict("records")
    marked_for_deletion = [row for row in edited_rows if row["accion"] == "Eliminar"]
    confirm_delete = False
    if marked_for_deletion:
        confirm_delete = st.checkbox(
            "Confirmo que quiero eliminar las categorías marcadas del uso activo"
        )

    if st.button("Guardar categorías", type="primary"):
        if marked_for_deletion and not confirm_delete:
            st.warning("Marca la confirmación antes de eliminar categorías.")
            return
        try:
            updated_count, deleted_count, reactivated_count = apply_category_table_changes(
                session_factory,
                user_profile_id=selected_profile_id,
                original_categories=categories,
                edited_rows=edited_rows,
                show_advanced=show_advanced,
            )
        except IntegrityError as exc:
            st.warning(friendly_integrity_error_message(exc))
            return
        except ValueError as exc:
            st.error(str(exc))
            return

        if updated_count == 0 and deleted_count == 0 and reactivated_count == 0:
            st.info("No hay cambios que guardar.")
            return
        flash_success(
            "categories",
            category_table_success_message(
                updated_count=updated_count,
                deleted_count=deleted_count,
                reactivated_count=reactivated_count,
            ),
        )
        st.rerun()


def render_import_preview(session_factory, selected_profile_id: int | None) -> None:
    if selected_profile_id is None:
        st.info("Selecciona un perfil para previsualizar importaciones.")
        return

    render_statement_pdf_preview(session_factory, selected_profile_id)
    st.divider()

    st.subheader("Importar Excel histórico")
    render_flash_success("historical_import")
    uploaded_file = st.file_uploader("Archivo .xlsx", type=["xlsx"])
    sheet_name = st.text_input("Hoja", value="Registro")

    if uploaded_file is None:
        st.session_state.pop(HISTORICAL_EXCEL_PREVIEW_KEY, None)
        return

    resolved_sheet_name = resolve_historical_sheet_name(sheet_name)
    upload_signature = uploaded_file_signature(uploaded_file, resolved_sheet_name)
    if st.button("Previsualizar Excel", type="primary"):
        try:
            preview = preview_uploaded_historical_excel(
                uploaded_file,
                sheet_name=resolved_sheet_name,
            )
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state[HISTORICAL_EXCEL_PREVIEW_KEY] = {
            "preview": preview,
            "signature": upload_signature,
            "version": HISTORICAL_EXCEL_PREVIEW_VERSION,
        }

    stored_preview = st.session_state.get(HISTORICAL_EXCEL_PREVIEW_KEY)
    if stored_historical_preview_matches(stored_preview, upload_signature):
        render_historical_excel_preview(stored_preview["preview"])
        render_historical_import_preparation(
            session_factory,
            selected_profile_id,
            stored_preview["preview"],
        )
    elif stored_preview is not None:
        st.session_state.pop(HISTORICAL_EXCEL_PREVIEW_KEY, None)
        st.info("La previsualización anterior ha caducado. Pulsa de nuevo Previsualizar Excel.")


def render_statement_pdf_preview(session_factory, selected_profile_id: int) -> None:
    st.subheader("Importar extracto PDF")
    render_flash_success("statement_pdf_import")

    accounts, _ = load_accounting_lists(
        session_factory,
        selected_profile_id,
        include_inactive=False,
    )
    if not accounts:
        st.info("Añade una cuenta activa antes de previsualizar extractos.")
        st.session_state.pop(STATEMENT_PDF_PREVIEW_KEY, None)
        return

    account_labels = {account.id: account.name for account in accounts}
    account_id = st.selectbox(
        "Cuenta del extracto",
        options=[account.id for account in accounts],
        format_func=account_labels.get,
        key="statement_pdf_account_id",
    )
    source_system = st.selectbox(
        "Tipo de extracto",
        options=[ImportSourceSystem.BANK_PDF.value, ImportSourceSystem.CARD_PDF.value],
        format_func={
            ImportSourceSystem.BANK_PDF.value: "Cuenta bancaria PDF",
            ImportSourceSystem.CARD_PDF.value: "Tarjeta PDF",
        }.get,
        key="statement_pdf_source_system",
    )
    uploaded_file = st.file_uploader(
        "Archivo PDF",
        type=["pdf"],
        key="statement_pdf_file",
    )

    if uploaded_file is None:
        st.session_state.pop(STATEMENT_PDF_PREVIEW_KEY, None)
        return

    upload_signature = uploaded_statement_pdf_signature(
        uploaded_file,
        account_id=account_id,
        source_system=source_system,
    )
    if st.button("Previsualizar extracto", type="primary"):
        try:
            preview = preview_uploaded_statement_pdf(uploaded_file)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state[STATEMENT_PDF_PREVIEW_KEY] = {
            "preview": preview,
            "signature": upload_signature,
            "version": STATEMENT_PDF_PREVIEW_VERSION,
        }

    stored_preview = st.session_state.get(STATEMENT_PDF_PREVIEW_KEY)
    if stored_statement_pdf_preview_matches(stored_preview, upload_signature):
        preview = stored_preview["preview"]
        render_statement_pdf_preview_results(
            session_factory,
            user_profile_id=selected_profile_id,
            account_id=account_id,
            account_label=account_labels[account_id],
            source_system=ImportSourceSystem(source_system),
            preview=preview,
        )
    elif stored_preview is not None:
        st.session_state.pop(STATEMENT_PDF_PREVIEW_KEY, None)
        st.info(
            "La previsualización anterior ha caducado. "
            "Pulsa de nuevo Previsualizar extracto."
        )


def preview_uploaded_statement_pdf(uploaded_file):
    with NamedTemporaryFile(delete=False, suffix=".pdf") as temporary_file:
        temporary_path = Path(temporary_file.name)
        temporary_file.write(uploaded_file.getbuffer())

    try:
        importer_module = importlib.import_module("lxcell.importers.pdf_statement")
        importer_module = importlib.reload(importer_module)
        return importer_module.PdfStatementDryRunImporter().preview(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def uploaded_statement_pdf_signature(
    uploaded_file,
    *,
    account_id: int,
    source_system: str,
) -> tuple[str, int, str, str]:
    digest = hashlib.sha256(uploaded_file.getbuffer()).hexdigest()
    return uploaded_file.name, account_id, source_system, digest


def stored_statement_pdf_preview_matches(
    stored_preview,
    upload_signature: tuple[str, int, str, str],
) -> bool:
    preview = stored_preview.get("preview") if isinstance(stored_preview, dict) else None
    return (
        isinstance(stored_preview, dict)
        and stored_preview.get("version") == STATEMENT_PDF_PREVIEW_VERSION
        and stored_preview.get("signature") == upload_signature
        and statement_pdf_preview_can_render(preview)
    )


def statement_pdf_preview_can_render(preview) -> bool:
    return (
        preview is not None
        and hasattr(preview, "candidates")
        and hasattr(preview, "issues")
        and hasattr(preview, "page_count")
        and hasattr(preview, "source_file_hash")
    )


def render_statement_pdf_preview_results(
    session_factory,
    *,
    user_profile_id: int,
    account_id: int,
    account_label: str,
    source_system: ImportSourceSystem,
    preview: PdfStatementPreview,
) -> None:
    st.success("Extracto leído en modo previsualización. No se ha guardado nada.")

    duplicate_batch = completed_statement_pdf_import_batch(
        session_factory,
        user_profile_id=user_profile_id,
        account_id=account_id,
        source_system=source_system,
        source_file_hash=preview.source_file_hash,
    )
    if duplicate_batch is not None:
        st.warning("Este archivo ya fue importado correctamente para esta cuenta.")

    locked_until = statement_pdf_locked_until(
        session_factory,
        user_profile_id=user_profile_id,
    )
    protected_count = statement_pdf_protected_candidate_count(
        preview,
        locked_until=locked_until,
    )
    candidate_column, page_column, issue_column, protected_column = st.columns(4)
    candidate_column.metric("Movimientos", preview.transaction_count)
    page_column.metric("Páginas", preview.page_count)
    issue_column.metric("Incidencias", len(preview.issues))
    protected_column.metric("Protegidos", protected_count)
    if protected_count:
        st.info(
            "Los movimientos en periodo protegido requerirán permiso adicional "
            "o se ignorarán en la importación confirmada."
        )

    st.caption(
        f"Cuenta: {account_label} · "
        f"tipo: {source_system.value} · "
        f"hash: {preview.source_file_hash[:12]}"
    )

    direction_rows = statement_pdf_direction_rows(preview)
    if direction_rows:
        st.subheader("Resumen por dirección")
        st.dataframe(pd.DataFrame(direction_rows), use_container_width=True)

    issue_rows = statement_pdf_issue_rows(preview)
    if issue_rows:
        st.subheader("Incidencias de lectura")
        st.dataframe(pd.DataFrame(issue_rows), use_container_width=True)

    candidate_rows = statement_pdf_candidate_rows(
        preview,
        limit=100,
        locked_until=locked_until,
    )
    if candidate_rows:
        st.subheader("Primeros movimientos")
        st.dataframe(pd.DataFrame(candidate_rows), use_container_width=True)

    render_statement_pdf_import_confirmation(
        session_factory,
        user_profile_id=user_profile_id,
        account_id=account_id,
        source_system=source_system,
        preview=preview,
        duplicate_batch=duplicate_batch,
        protected_count=protected_count,
    )


def render_statement_pdf_import_confirmation(
    session_factory,
    *,
    user_profile_id: int,
    account_id: int,
    source_system: ImportSourceSystem,
    preview: PdfStatementPreview,
    duplicate_batch,
    protected_count: int,
) -> None:
    st.subheader("Guardar extracto")
    if duplicate_batch is not None:
        st.info("No se puede guardar porque este archivo ya consta como importado.")
        return
    if preview.issues:
        st.info("Corrige las incidencias de lectura antes de guardar el extracto.")
        return
    if preview.transaction_count == 0:
        st.info("No hay movimientos que guardar.")
        return

    open_count = preview.transaction_count - protected_count
    if protected_count:
        st.caption(
            f"Por defecto se guardarán {open_count} movimiento(s) y se ignorarán "
            f"{protected_count} movimiento(s) del periodo protegido con traza de auditoría."
        )
    else:
        st.caption(f"Se guardarán hasta {preview.transaction_count} movimiento(s).")

    with st.form("confirm_statement_pdf_import"):
        confirmed_by = st.text_input(
            "Confirmado por",
            value="local_ui",
            key="statement_pdf_confirmed_by",
        )
        import_protected = False
        protected_confirmation_text = ""
        if protected_count:
            st.warning(
                "Importar movimientos del periodo protegido puede duplicar o modificar "
                "historial ya revisado."
            )
            st.caption(
                "Para importarlos también, escribe exactamente: "
                f"{STATEMENT_PDF_PROTECTED_IMPORT_CONFIRMATION_TEXT}"
            )
            protected_confirmation_text = st.text_input(
                "Texto de confirmación para periodo protegido",
                key="statement_pdf_locked_period_override_text",
            )
        user_confirmed = st.checkbox(
            "Confirmo que quiero guardar este extracto en la base de datos",
            key="confirm_statement_pdf_import",
        )
        submitted = st.form_submit_button("Guardar extracto")

    if not submitted:
        return
    if (
        protected_count
        and protected_confirmation_text.strip()
        and not statement_pdf_protected_import_confirmation_matches(
            protected_confirmation_text
        )
    ):
        st.warning("El texto de confirmación del periodo protegido no coincide.")
        return
    import_protected = statement_pdf_protected_import_confirmation_matches(
        protected_confirmation_text
    )
    try:
        result = confirm_statement_pdf_import_from_preview(
            session_factory,
            user_profile_id=user_profile_id,
            account_id=account_id,
            source_system=source_system,
            preview=preview,
            confirmed_by=confirmed_by.strip(),
            user_confirmed=user_confirmed,
            allow_locked_period_override=import_protected,
        )
    except (IntegrityError, ValueError) as exc:
        st.error(str(exc))
        return

    st.session_state.pop(STATEMENT_PDF_PREVIEW_KEY, None)
    flash_success(
        "statement_pdf_import",
        statement_pdf_import_success_message(result),
    )
    st.rerun()


def confirm_statement_pdf_import_from_preview(
    session_factory,
    *,
    user_profile_id: int,
    account_id: int,
    source_system: ImportSourceSystem,
    preview: PdfStatementPreview,
    confirmed_by: str,
    user_confirmed: bool,
    allow_locked_period_override: bool = False,
):
    with session_scope(session_factory) as session:
        service = StatementPdfImportService(AccountingRepository(session))
        result = service.confirm_import(
            user_profile_id=user_profile_id,
            account_id=account_id,
            source_system=source_system,
            preview=preview,
            confirmed_by=confirmed_by,
            user_confirmed=user_confirmed,
            allow_locked_period_override=allow_locked_period_override,
        )
        session.flush()
        return result


def statement_pdf_import_success_message(result) -> str:
    parts = [f"{result.transaction_count} transacción(es) creada(s)"]
    if result.ignored_protected_count:
        parts.append(f"{result.ignored_protected_count} protegida(s) ignorada(s)")
    if result.matched_existing_count:
        parts.append(f"{result.matched_existing_count} duplicada(s) existente(s)")
    if result.marked_duplicate_count:
        parts.append(f"{result.marked_duplicate_count} duplicada(s) en el archivo")
    return "Extracto guardado: " + ", ".join(parts)


def statement_pdf_protected_import_confirmation_matches(value: str) -> bool:
    return value.strip() == STATEMENT_PDF_PROTECTED_IMPORT_CONFIRMATION_TEXT


def completed_statement_pdf_import_batch(
    session_factory,
    *,
    user_profile_id: int,
    account_id: int,
    source_system: ImportSourceSystem,
    source_file_hash: str,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        return completed_statement_pdf_import_batch_fallback(
            repository,
            user_profile_id=user_profile_id,
            account_id=account_id,
            source_system=source_system,
            source_file_hash=source_file_hash,
        )


def completed_statement_pdf_import_batch_fallback(
    repository: AccountingRepository,
    *,
    user_profile_id: int,
    account_id: int,
    source_system: ImportSourceSystem,
    source_file_hash: str,
):
    statement = select(ImportBatch).where(
        ImportBatch.user_profile_id == user_profile_id,
        ImportBatch.account_id == account_id,
        ImportBatch.source_system == source_system,
        ImportBatch.source_file_hash == source_file_hash,
        ImportBatch.import_status.in_(
            [ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_WARNINGS]
        ),
    )
    return repository.session.scalar(statement.order_by(ImportBatch.imported_at.desc()))


def preview_uploaded_historical_excel(uploaded_file, *, sheet_name: str):
    with NamedTemporaryFile(delete=False, suffix=".xlsx") as temporary_file:
        temporary_path = Path(temporary_file.name)
        temporary_file.write(uploaded_file.getbuffer())

    try:
        importer_module = importlib.import_module("lxcell.importers.excel_historical")
        importer_module = importlib.reload(importer_module)
        return importer_module.HistoricalExcelDryRunImporter(
            sheet_name=sheet_name
        ).preview(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def resolve_historical_sheet_name(sheet_name: str) -> str:
    return sheet_name.strip() or "Registro"


def uploaded_file_signature(uploaded_file, sheet_name: str) -> tuple[str, str, str]:
    digest = hashlib.sha256(uploaded_file.getbuffer()).hexdigest()
    return uploaded_file.name, sheet_name, digest


def stored_historical_preview_matches(
    stored_preview,
    upload_signature: tuple[str, str, str],
) -> bool:
    preview = stored_preview.get("preview") if isinstance(stored_preview, dict) else None
    return (
        isinstance(stored_preview, dict)
        and stored_preview.get("version") == HISTORICAL_EXCEL_PREVIEW_VERSION
        and stored_preview.get("signature") == upload_signature
        and historical_preview_can_render(preview)
    )


def historical_preview_can_render(preview) -> bool:
    return (
        preview is not None
        and hasattr(preview, "candidates")
        and hasattr(preview, "ignored_row_numbers")
        and hasattr(preview, "source_categories")
    )


def render_historical_excel_preview(preview: HistoricalExcelPreview) -> None:
    st.success("Excel leído en modo previsualización. No se ha guardado nada.")

    candidate_column, category_column, ignored_column = st.columns(3)
    candidate_column.metric("Candidatos", preview.transaction_count)
    category_column.metric("Categorías origen", len(preview.source_categories))
    ignored_column.metric("Filas ignoradas", len(preview.ignored_row_numbers))

    st.caption(
        f"Hoja: {preview.sheet_name} · "
        f"cabecera: fila {preview.header_row_number} · "
        f"hash: {preview.source_file_hash[:12]}"
    )

    totals_by_month = totals_table_rows(source_totals_by_month_minor(preview), "mes")
    if totals_by_month:
        st.subheader("Totales Excel por mes")
        st.dataframe(pd.DataFrame(totals_by_month), use_container_width=True)

    totals_by_category = totals_table_rows(
        source_totals_by_category_minor(preview),
        "categoría origen",
    )
    if totals_by_category:
        st.subheader("Totales Excel por categoría origen")
        st.dataframe(pd.DataFrame(totals_by_category), use_container_width=True)

    candidate_rows = preview_candidate_rows(preview, limit=100)
    if candidate_rows:
        st.subheader("Primeros candidatos")
        st.dataframe(pd.DataFrame(candidate_rows), use_container_width=True)

    render_tracking_validation(preview)

    if preview.ignored_row_numbers:
        st.caption(
            "Filas ignoradas: "
            + ", ".join(str(row_number) for row_number in preview.ignored_row_numbers)
        )


def render_tracking_validation(preview: HistoricalExcelPreview) -> None:
    validation = getattr(preview, "tracking_validation", None)
    st.subheader("Validación contra Seguimiento")
    if validation is None:
        st.info("No se ha podido leer una validación comparable en Seguimiento.")
        return

    ok_column, difference_column, unmatched_column = st.columns(3)
    ok_column.metric("Coincidencias", validation.ok_count)
    difference_column.metric("Diferencias", validation.difference_count)
    unmatched_column.metric(
        "Categorías sin emparejar",
        len(validation.registro_only_categories)
        + len(validation.seguimiento_only_categories),
    )

    comparison_rows = tracking_comparison_rows(validation)
    if comparison_rows:
        st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True)

    unmatched_rows = tracking_unmatched_category_rows(validation)
    if unmatched_rows:
        st.subheader("Categorías sin emparejar")
        st.dataframe(pd.DataFrame(unmatched_rows), use_container_width=True)


def render_historical_import_preparation(
    session_factory,
    selected_profile_id: int,
    preview: HistoricalExcelPreview,
) -> None:
    st.subheader("Preparar importación")

    validation_blockers = historical_import_validation_blockers(preview)
    duplicate_batch = completed_historical_import_batch(
        session_factory,
        user_profile_id=selected_profile_id,
        source_file_hash=preview.source_file_hash,
    )
    categories = load_categories(
        session_factory,
        selected_profile_id,
        include_inactive=True,
    )
    category_plan = historical_category_import_plan(categories, preview)
    category_mapping_suggestions = historical_category_mapping_suggestions(
        session_factory,
        user_profile_id=selected_profile_id,
        category_plan=category_plan,
    )
    category_mapping_overrides = render_historical_category_mapping_controls(
        categories,
        category_plan,
        category_mapping_suggestions,
    )
    resolved_category_plan = apply_historical_category_mapping_to_plan(
        category_plan,
        categories,
        category_mapping_overrides,
    )
    category_conflicts = [
        row for row in resolved_category_plan if row["acción"] == "conflicto"
    ]

    transaction_column, category_column, account_column = st.columns(3)
    transaction_column.metric("Transacciones", preview.transaction_count)
    category_column.metric(
        "Categorías nuevas",
        count_rows_by_action(resolved_category_plan, "crear"),
    )
    account_column.metric("Cuenta destino", HISTORICAL_EXCEL_ACCOUNT_NAME)

    if validation_blockers:
        for blocker in validation_blockers:
            st.warning(blocker)
    if duplicate_batch is not None:
        st.error("Este archivo ya fue importado correctamente para este perfil.")
    if category_conflicts:
        st.warning("Hay conflictos de categorías que requieren revisión.")
    locked_candidate_dates = locked_import_candidate_dates(
        session_factory,
        user_profile_id=selected_profile_id,
        transaction_dates=[
            candidate.transaction_date for candidate in preview.candidates
        ],
    )
    if locked_candidate_dates:
        st.warning(
            "El Excel contiene transacciones en un periodo protegido. "
            "Necesitas permiso adicional para importarlas."
        )

    if resolved_category_plan:
        st.dataframe(pd.DataFrame(resolved_category_plan), use_container_width=True)

    can_prepare_import = (
        not validation_blockers
        and duplicate_batch is None
        and not category_conflicts
        and preview.transaction_count > 0
    )
    if can_prepare_import:
        confirmed_by = st.text_input("Confirmado por", value="local_ui")
        locked_period_override = True
        if locked_candidate_dates:
            locked_period_override = st.checkbox(
                "Confirmo que quiero importar transacciones en un periodo protegido",
                key="historical_import_locked_period_override",
            )
        user_confirmed = st.checkbox(
            "Confirmo que quiero importar este Excel histórico en la base de datos",
            key="confirm_historical_excel_import",
        )
        if st.button(
            "Importar Excel histórico",
            type="primary",
            disabled=not user_confirmed,
        ):
            try:
                result = confirm_historical_excel_import_from_preview(
                    session_factory,
                    user_profile_id=selected_profile_id,
                    preview=preview,
                    confirmed_by=confirmed_by.strip(),
                    user_confirmed=user_confirmed,
                    category_id_overrides_by_source_name=category_mapping_overrides,
                    allow_locked_period_override=locked_period_override,
                )
            except (IntegrityError, ValueError) as exc:
                st.error(str(exc))
                return
            st.session_state.pop(HISTORICAL_EXCEL_PREVIEW_KEY, None)
            flash_success(
                "historical_import",
                "Importación completada: "
                f"{result.transaction_count} transacciones.",
            )
            st.rerun()


def historical_import_validation_blockers(preview: HistoricalExcelPreview) -> list[str]:
    validation = getattr(preview, "tracking_validation", None)
    if validation is None:
        return ["La importación requiere validación comparable contra Seguimiento."]

    blockers = []
    if validation.difference_count:
        blockers.append("Hay diferencias entre Registro y Seguimiento.")
    if validation.registro_only_categories:
        blockers.append("Hay categorías presentes solo en Registro.")
    if validation.seguimiento_only_categories:
        blockers.append("Hay categorías presentes solo en Seguimiento.")
    return blockers


def completed_historical_import_batch(
    session_factory,
    *,
    user_profile_id: int,
    source_file_hash: str,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        return completed_historical_import_batch_fallback(
            repository,
            user_profile_id=user_profile_id,
            source_file_hash=source_file_hash,
        )


def completed_historical_import_batch_fallback(
    repository: AccountingRepository,
    *,
    user_profile_id: int,
    source_file_hash: str,
):
    statement = select(ImportBatch).where(
        ImportBatch.user_profile_id == user_profile_id,
        ImportBatch.source_system == ImportSourceSystem.EXCEL_HISTORICAL,
        ImportBatch.source_file_hash == source_file_hash,
        ImportBatch.import_status.in_(
            [ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_WARNINGS]
        ),
    )
    return repository.session.scalar(statement.order_by(ImportBatch.imported_at.desc()))


def confirm_historical_excel_import_from_preview(
    session_factory,
    *,
    user_profile_id: int,
    preview: HistoricalExcelPreview,
    confirmed_by: str,
    user_confirmed: bool,
    category_id_overrides_by_source_name: dict[str, int] | None = None,
    allow_locked_period_override: bool = False,
):
    with session_scope(session_factory) as session:
        service = HistoricalExcelImportService(AccountingRepository(session))
        result = service.confirm_import(
            user_profile_id=user_profile_id,
            preview=preview,
            confirmed_by=confirmed_by,
            user_confirmed=user_confirmed,
            category_id_overrides_by_source_name=(
                category_id_overrides_by_source_name
            ),
            allow_locked_period_override=allow_locked_period_override,
        )
        session.flush()
        return result


def render_historical_category_mapping_controls(
    categories,
    category_plan: list[dict],
    category_mapping_suggestions: dict[str, int] | None = None,
) -> dict[str, int]:
    rows_to_resolve = [
        row for row in category_plan if row["acción"] in {"crear", "conflicto"}
    ]
    if not rows_to_resolve:
        return {}

    st.subheader("Resolver categorías")
    category_id_overrides = {}
    active_categories = [category for category in categories if category.is_active]
    for row in rows_to_resolve:
        compatible_categories = [
            category
            for category in active_categories
            if category.category_type.value == row["tipo"]
        ]
        suggested_category_id = (category_mapping_suggestions or {}).get(
            row["categoría Excel"]
        )
        selected_category_id = historical_category_mapping_selectbox(
            row,
            compatible_categories,
            suggested_category_id=suggested_category_id,
        )
        if selected_category_id is not None:
            category_id_overrides[row["categoría Excel"]] = selected_category_id
    return category_id_overrides


def historical_category_mapping_selectbox(
    row: dict,
    compatible_categories,
    *,
    suggested_category_id: int | None = None,
) -> int | None:
    create_option = "__create__"
    select_option = "__select__"
    options = [category.id for category in compatible_categories]
    if row["acción"] == "crear":
        options = [create_option] + options
    else:
        options = [select_option] + options

    labels = {
        create_option: f"Crear nueva categoría '{row['categoría Excel']}'",
        select_option: "Selecciona una categoría existente",
    } | {category.id: category.name for category in compatible_categories}
    if suggested_category_id in {category.id for category in compatible_categories}:
        st.caption(f"Sugerencia: {labels[suggested_category_id]}")
    index = (
        options.index(suggested_category_id)
        if suggested_category_id in options
        else 0
    )
    selected_value = st.selectbox(
        f"Categoría destino para {row['categoría Excel']}",
        options=options,
        format_func=labels.get,
        index=index,
        key=f"historical_category_mapping_{row['categoría Excel']}",
    )
    return selected_value if isinstance(selected_value, int) else None


def historical_category_mapping_suggestions(
    session_factory,
    *,
    user_profile_id: int,
    category_plan: list[dict],
) -> dict[str, int]:
    rows_to_resolve = [
        row for row in category_plan if row["acción"] in {"crear", "conflicto"}
    ]
    if not rows_to_resolve:
        return {}

    suggestions = {}
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        for row in rows_to_resolve:
            source_category_key = normalize_category_label_for_import(
                row["categoría Excel"]
            )
            if hasattr(repository, "list_category_mapping_suggestions"):
                mapping_suggestions = repository.list_category_mapping_suggestions(
                    user_profile_id=user_profile_id,
                    source_system=ImportSourceSystem.EXCEL_HISTORICAL,
                    source_category_key=source_category_key,
                )
            else:
                mapping_suggestions = historical_category_mapping_suggestions_fallback(
                    repository,
                    user_profile_id=user_profile_id,
                    source_category_key=source_category_key,
                )
            compatible_mapping = next(
                (
                    mapping
                    for mapping in mapping_suggestions
                    if mapping.target_category.is_active
                    and mapping.target_category.category_type.value == row["tipo"]
                ),
                None,
            )
            if compatible_mapping is not None:
                suggestions[row["categoría Excel"]] = compatible_mapping.target_category_id
    return suggestions


def historical_category_mapping_suggestions_fallback(
    repository: AccountingRepository,
    *,
    user_profile_id: int,
    source_category_key: str,
):
    statement = (
        select(CategoryMapping)
        .join(Category)
        .where(
            CategoryMapping.user_profile_id == user_profile_id,
            CategoryMapping.source_system == ImportSourceSystem.EXCEL_HISTORICAL,
            CategoryMapping.source_category_key == source_category_key,
            Category.is_active.is_(True),
        )
        .order_by(CategoryMapping.updated_at.desc(), CategoryMapping.id.desc())
    )
    return list(repository.session.scalars(statement))


def apply_historical_category_mapping_to_plan(
    category_plan: list[dict],
    categories,
    category_id_overrides_by_source_name: dict[str, int],
) -> list[dict]:
    categories_by_id = {category.id: category for category in categories}
    resolved_rows = []
    for row in category_plan:
        category_id = category_id_overrides_by_source_name.get(row["categoría Excel"])
        if category_id is None:
            resolved_rows.append(row)
            continue
        category = categories_by_id[category_id]
        resolved_rows.append(
            {
                "categoría Excel": row["categoría Excel"],
                "acción": "mapear",
                "categoría LXCell": category.name,
                "tipo": category.category_type.value,
                "clave": category.canonical_key,
            }
        )
    return resolved_rows


def historical_category_import_plan(categories, preview: HistoricalExcelPreview) -> list[dict]:
    categories_by_normalized_name = {
        normalize_category_label_for_import(category.name): category
        for category in categories
    }
    categories_by_canonical_key = {
        category.canonical_key: category
        for category in categories
    }
    source_category_kinds = source_category_kinds_by_name(preview)

    rows = []
    for source_category_name in preview.source_categories:
        normalized_name = normalize_category_label_for_import(source_category_name)
        canonical_key = canonical_key_from_name(source_category_name)
        category_kind = source_category_kinds.get(source_category_name, "expense")
        category_type = (
            CategoryType.INCOME.value
            if category_kind == "income"
            else CategoryType.EXPENSE.value
        )

        matched_category = categories_by_normalized_name.get(normalized_name)
        if matched_category is not None and matched_category.is_active:
            rows.append(
                {
                    "categoría Excel": source_category_name,
                    "acción": "reutilizar",
                    "categoría LXCell": matched_category.name,
                    "tipo": matched_category.category_type.value,
                    "clave": matched_category.canonical_key,
                }
            )
            continue
        if matched_category is not None and not matched_category.is_active:
            rows.append(
                {
                    "categoría Excel": source_category_name,
                    "acción": "conflicto",
                    "categoría LXCell": category_label_for_transaction_table(
                        matched_category
                    ),
                    "tipo": matched_category.category_type.value,
                    "clave": matched_category.canonical_key,
                }
            )
            continue

        conflicting_category = categories_by_canonical_key.get(canonical_key)
        if conflicting_category is not None:
            rows.append(
                {
                    "categoría Excel": source_category_name,
                    "acción": "conflicto",
                    "categoría LXCell": conflicting_category.name,
                    "tipo": conflicting_category.category_type.value,
                    "clave": canonical_key,
                }
            )
            continue

        rows.append(
            {
                "categoría Excel": source_category_name,
                "acción": "crear",
                "categoría LXCell": source_category_name,
                "tipo": category_type,
                "clave": canonical_key,
            }
        )

    return rows


def source_category_kinds_by_name(preview: HistoricalExcelPreview) -> dict[str, str]:
    kinds_by_name = {}
    for candidate in preview.candidates:
        category_name = candidate.source_category_name
        category_kind = candidate_source_column_kind(candidate)
        if category_kind == "income":
            kinds_by_name[category_name] = "income"
        else:
            kinds_by_name.setdefault(category_name, "expense")
    return kinds_by_name


def normalize_category_label_for_import(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    without_accents = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())


def count_rows_by_action(rows: list[dict], action: str) -> int:
    return sum(1 for row in rows if row["acción"] == action)


def preview_candidate_rows(
    preview: HistoricalExcelPreview,
    *,
    limit: int = 100,
) -> list[dict]:
    return [
        {
            "fila": candidate.row_number_source,
            "fecha": candidate.transaction_date,
            "categoría origen": candidate.source_category_name,
            "tipo columna": (
                "ingreso"
                if candidate_source_column_kind(candidate) == "income"
                else "gasto"
            ),
            "importe Excel": format_signed_amount_minor(
                candidate_source_signed_amount_minor(candidate)
            ),
            "dirección": candidate.direction.value,
            "comentario": candidate.description_raw or "",
        }
        for candidate in preview.candidates[:limit]
    ]


def statement_pdf_candidate_rows(
    preview: PdfStatementPreview,
    *,
    limit: int = 100,
    locked_until: date | None = None,
) -> list[dict]:
    return [
        {
            "fila": candidate.row_number_source,
            "página": candidate.page_number,
            "fecha": candidate.transaction_date,
            "fecha valor": candidate.posted_date,
            "descripción": candidate.description_clean,
            "importe": format_signed_amount_minor(
                statement_pdf_signed_amount_minor(candidate)
            ),
            "dirección": candidate.direction.value,
            "periodo": (
                "protegido"
                if statement_pdf_candidate_is_locked(
                    candidate,
                    locked_until=locked_until,
                )
                else "abierto"
            ),
            "saldo": (
                format_signed_amount_minor(candidate.balance_minor)
                if candidate.balance_minor is not None
                else ""
            ),
            "hash": candidate.content_hash[:12],
        }
        for candidate in preview.candidates[:limit]
    ]


def statement_pdf_locked_until(session_factory, *, user_profile_id: int) -> date | None:
    user_profile = load_user_profile(session_factory, user_profile_id)
    if user_profile is None:
        return None
    return user_profile.transactions_locked_until


def statement_pdf_protected_candidate_count(
    preview: PdfStatementPreview,
    *,
    locked_until: date | None,
) -> int:
    return sum(
        1
        for candidate in preview.candidates
        if statement_pdf_candidate_is_locked(candidate, locked_until=locked_until)
    )


def statement_pdf_candidate_is_locked(candidate, *, locked_until: date | None) -> bool:
    return locked_until is not None and candidate.transaction_date <= locked_until


def statement_pdf_issue_rows(preview: PdfStatementPreview) -> list[dict]:
    return [
        {
            "página": issue.page_number,
            "fila": issue.row_number_source or "",
            "incidencia": issue.message,
        }
        for issue in preview.issues
    ]


def statement_pdf_direction_rows(preview: PdfStatementPreview) -> list[dict]:
    totals: dict[Direction, dict[str, int]] = {}
    for candidate in preview.candidates:
        current = totals.setdefault(
            candidate.direction,
            {"movimientos": 0, "importe_minor": 0},
        )
        current["movimientos"] += 1
        current["importe_minor"] += statement_pdf_signed_amount_minor(candidate)

    labels = {
        Direction.INFLOW: "Entrante",
        Direction.OUTFLOW: "Saliente",
        Direction.NEUTRAL: "Neutral",
    }
    return [
        {
            "dirección": labels[direction],
            "movimientos": values["movimientos"],
            "importe": format_signed_amount_minor(values["importe_minor"]),
        }
        for direction, values in sorted(
            totals.items(),
            key=lambda item: item[0].value,
        )
    ]


def statement_pdf_signed_amount_minor(candidate) -> int:
    if candidate.direction == Direction.INFLOW:
        return candidate.amount_minor
    if candidate.direction == Direction.OUTFLOW:
        return -candidate.amount_minor
    return 0


def source_totals_by_month_minor(preview: HistoricalExcelPreview) -> dict[str, int]:
    totals: dict[str, Decimal] = {}
    for candidate in preview.candidates:
        month_key = candidate.transaction_date.strftime("%Y-%m")
        totals[month_key] = totals.get(month_key, Decimal("0")) + (
            candidate_source_amount_decimal(candidate)
        )
    return {
        month_key: minor_units_from_decimal(amount)
        for month_key, amount in sorted(totals.items())
    }


def source_totals_by_category_minor(preview: HistoricalExcelPreview) -> dict[str, int]:
    totals: dict[str, Decimal] = {}
    for candidate in preview.candidates:
        category_key = candidate.source_category_name
        totals[category_key] = totals.get(
            category_key,
            Decimal("0"),
        ) + candidate_source_amount_decimal(candidate)
    return {
        category_key: minor_units_from_decimal(amount)
        for category_key, amount in sorted(totals.items())
    }


def tracking_comparison_rows(validation) -> list[dict]:
    return [
        {
            "mes": comparison.month_key,
            "categoría origen": comparison.source_category_name,
            "Registro": format_signed_amount_minor(comparison.registro_amount_minor),
            "Seguimiento": format_signed_amount_minor(
                comparison.seguimiento_amount_minor
            ),
            "diferencia": format_signed_amount_minor(comparison.difference_minor),
            "estado": "ok" if comparison.status == "ok" else "diferencia",
        }
        for comparison in validation.comparisons
    ]


def tracking_unmatched_category_rows(validation) -> list[dict]:
    rows = [
        {
            "categoría": category_name,
            "aparece en": "Registro",
        }
        for category_name in validation.registro_only_categories
    ]
    rows.extend(
        {
            "categoría": category_name,
            "aparece en": "Seguimiento",
        }
        for category_name in validation.seguimiento_only_categories
    )
    return sorted(rows, key=lambda row: (row["categoría"], row["aparece en"]))


def candidate_source_column_kind(candidate) -> str:
    return getattr(candidate, "source_column_kind", "expense")


def candidate_source_signed_amount_minor(candidate) -> int:
    source_amount_minor = getattr(candidate, "source_amount_minor", None)
    if source_amount_minor is not None:
        return source_amount_minor
    return abs(candidate.amount_minor)


def candidate_source_amount_decimal(candidate) -> Decimal:
    source_amount_decimal = getattr(candidate, "source_amount_decimal", None)
    if source_amount_decimal is not None:
        return source_amount_decimal
    source_amount_minor = getattr(candidate, "source_amount_minor", None)
    if source_amount_minor is not None:
        return Decimal(source_amount_minor) / Decimal("100")
    return Decimal(abs(candidate.amount_minor)) / Decimal("100")


def minor_units_from_decimal(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def totals_table_rows(totals_minor: dict[str, int], label: str) -> list[dict]:
    return [
        {
            label: key,
            "importe": format_signed_amount_minor(amount_minor),
        }
        for key, amount_minor in totals_minor.items()
    ]


def category_table_rows(categories, *, show_advanced: bool = False) -> list[dict]:
    rows = []
    for category in categories:
        row = {
            "id": category.id,
            "nombre": category.name,
            "tipo": category.category_type.value,
            "orden": category.display_order,
            "accion": "",
        }
        if show_advanced:
            row["estado"] = category_status_label(category)
            row["clave"] = category.canonical_key
        rows.append(row)
    return rows


def category_status_label(category) -> str:
    return "activa" if category.is_active else "eliminada"


def apply_category_table_changes(
    session_factory,
    *,
    user_profile_id: int,
    original_categories,
    edited_rows: list[dict],
    show_advanced: bool = False,
) -> tuple[int, int, int]:
    original_by_id = {category.id: category for category in original_categories}
    updated_count = 0
    deleted_count = 0
    reactivated_count = 0

    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        for row in edited_rows:
            category_id = int(row["id"])
            category = original_by_id[category_id]
            action = row["accion"]
            if action == "Eliminar":
                if not category.is_active:
                    raise ValueError(
                        f"La categoría '{category.name}' ya está eliminada."
                    )
                deactivate_category_for_ui(service, user_profile_id, category)
                deleted_count += 1
                continue
            payload = edited_category_payload(
                row,
                existing_canonical_key=category.canonical_key,
                existing_is_active=category.is_active,
                show_advanced=show_advanced,
            )
            if action == "Reactivar":
                if category.is_active:
                    raise ValueError(
                        f"La categoría '{category.name}' ya está activa."
                    )
                payload["is_active"] = True
                reactivated_count += 1

            if not category_row_changed(category, payload):
                continue
            update_category_for_ui(
                service,
                user_profile_id=user_profile_id,
                category_id=category_id,
                name=payload["name"],
                category_type=CategoryType(payload["category_type"]),
                canonical_key=payload["canonical_key"],
                display_order=payload["display_order"],
                is_active=payload["is_active"],
            )
            if action != "Reactivar":
                updated_count += 1

    return updated_count, deleted_count, reactivated_count


def update_category_for_ui(
    service: AccountingService,
    *,
    user_profile_id: int,
    category_id: int,
    name: str,
    category_type: CategoryType,
    canonical_key: str,
    display_order: int,
    is_active: bool,
) -> None:
    if hasattr(service, "update_category"):
        service.update_category(
            user_profile_id=user_profile_id,
            category_id=category_id,
            name=name,
            category_type=category_type,
            canonical_key=canonical_key,
            display_order=display_order,
            is_active=is_active,
        )
        return

    category = service.repository.session.get(Category, category_id)
    if category is None or category.user_profile_id != user_profile_id:
        raise ValueError("Category was not found for the user profile.")

    category.name = name
    category.category_type = category_type
    category.canonical_key = canonical_key
    category.display_order = display_order
    category.is_active = is_active


def create_category_from_ui(
    session_factory,
    *,
    user_profile_id: int,
    name: str,
    category_type: CategoryType,
    canonical_key: str | None,
    display_order: int,
) -> Category:
    resolved_canonical_key = (
        canonical_key.strip()
        if canonical_key and canonical_key.strip()
        else canonical_key_from_name(name)
    )
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        category = service.create_category(
            user_profile_id=user_profile_id,
            name=name,
            category_type=category_type,
            canonical_key=resolved_canonical_key,
            display_order=display_order,
        )
        session.flush()
        return category


def deactivate_category_for_ui(
    service: AccountingService,
    user_profile_id: int,
    category,
) -> None:
    if hasattr(service, "deactivate_category"):
        service.deactivate_category(
            user_profile_id=user_profile_id,
            category_id=category.id,
        )
        return

    update_category_for_ui(
        service,
        user_profile_id=user_profile_id,
        category_id=category.id,
        name=category.name,
        category_type=category.category_type,
        canonical_key=category.canonical_key,
        display_order=category.display_order,
        is_active=False,
    )


def edited_category_payload(
    row: dict,
    *,
    existing_canonical_key: str | None = None,
    existing_is_active: bool = True,
    show_advanced: bool = True,
) -> dict:
    canonical_key = (
        str(row["clave"]).strip()
        if show_advanced
        else existing_canonical_key or canonical_key_from_name(str(row["nombre"]))
    )
    return {
        "name": str(row["nombre"]).strip(),
        "category_type": row["tipo"],
        "canonical_key": canonical_key,
        "display_order": int(row["orden"]),
        "is_active": existing_is_active,
    }


def category_row_changed(category, payload: dict) -> bool:
    return (
        category.name != payload["name"]
        or category.category_type != CategoryType(payload["category_type"])
        or category.canonical_key != payload["canonical_key"]
        or category.display_order != payload["display_order"]
        or category.is_active != payload["is_active"]
    )


def category_table_success_message(
    *, updated_count: int, deleted_count: int, reactivated_count: int = 0
) -> str:
    parts = []
    if updated_count:
        parts.append(f"{updated_count} actualizada(s)")
    if deleted_count:
        parts.append(f"{deleted_count} eliminada(s)")
    if reactivated_count:
        parts.append(f"{reactivated_count} reactivada(s)")
    return "Categorías guardadas: " + ", ".join(parts)


def transaction_review_queue_rows(
    transactions,
    *,
    account_labels: dict[int, str],
    category_labels: dict[int | None, str],
    decision_by_transaction_id: dict[int, ClassificationDecision],
) -> list[dict]:
    return [
        {
            "id": transaction.id,
            "fecha": transaction.transaction_date,
            "descripcion": transaction.description_clean or "",
            "importe": format_amount_minor(transaction.amount_minor),
            "direccion": transaction.direction.value,
            "cuenta": account_labels.get(
                transaction.account_id,
                f"Cuenta {transaction.account_id}",
            ),
            "categoria actual": category_labels.get(
                transaction.category_id,
                "Sin categoría",
            ),
            "sugerencia": classification_decision_summary(
                decision_by_transaction_id.get(transaction.id),
                category_labels,
            ),
        }
        for transaction in sorted(
            transactions,
            key=lambda transaction: (transaction.transaction_date, transaction.id),
        )
    ]


def review_transaction_label(transaction: Transaction) -> str:
    return (
        f"{transaction.id} · {transaction.transaction_date.isoformat()} · "
        f"{transaction.description_clean or ''} · "
        f"{format_amount_minor(transaction.amount_minor)}"
    )


def classification_decision_summary(
    decision: ClassificationDecision | None,
    category_labels: dict[int | None, str],
) -> str:
    if decision is None:
        return "Sin sugerencia"
    category_label = category_labels.get(decision.category_id, "Sin categoría")
    parts = [category_label]
    if decision.transaction_type is not None:
        parts.append(decision.transaction_type.value)
    if decision.payment_method is not None:
        parts.append(decision.payment_method.value)
    parts.append(decision.decision_source.value)
    parts.append(decision.decision_status.value)
    return " · ".join(parts)


def suggested_payment_method_for_review(transaction: Transaction) -> PaymentMethod | None:
    description = f"{transaction.description_clean or ''} {transaction.description_raw or ''}"
    if re.search(r"\b(?:tikkie|bizum)\b", description, flags=re.IGNORECASE):
        return PaymentMethod.PEER_TO_PEER
    if transaction.payment_method is not None:
        return transaction.payment_method
    if re.search(r"\b(?:tarjeta|card)\b", description, flags=re.IGNORECASE):
        return PaymentMethod.CARD
    return None


def suggested_classification_rule_pattern(transaction: Transaction) -> str:
    description = transaction.description_clean or transaction.description_raw or ""
    description = re.sub(
        r"\b(?:tarjeta|card)\s*:?\s*[0-9* ]{4,}\b",
        " ",
        description,
        flags=re.IGNORECASE,
    )
    description = description.split(",", maxsplit=1)[0]
    description = re.sub(r"\b\d+\b\s*$", " ", description)
    description = re.sub(r"\s+", " ", description).strip(" ,.-")
    return repeated_merchant_base_pattern(description)[:120]


def repeated_merchant_base_pattern(description: str) -> str:
    tokens = description.split()
    if len(tokens) < 3:
        return description

    if len(tokens) % 2 == 0:
        midpoint = len(tokens) // 2
        if normalized_token_sequence(tokens[:midpoint]) == normalized_token_sequence(
            tokens[midpoint:]
        ):
            return " ".join(tokens[:midpoint])

    for index in range(1, len(tokens) - 1):
        if tokens[index].lower() != "a":
            continue
        before = tokens[:index]
        after = tokens[index + 1 :]
        if len(after) == 1 and token_looks_like_opaque_reference(after[0]):
            return " ".join(before)
        if normalized_token_sequence(before) == normalized_token_sequence(after):
            return " ".join(before)
    return description


def normalized_token_sequence(tokens: list[str]) -> list[str]:
    return [re.sub(r"[^a-zA-Z0-9]+", "", token).lower() for token in tokens]


def token_looks_like_opaque_reference(token: str) -> bool:
    normalized_token = re.sub(r"[^a-zA-Z0-9]+", "", token)
    return (
        len(normalized_token) >= 8
        and any(character.isalpha() for character in normalized_token)
        and any(character.isdigit() for character in normalized_token)
    )


def learned_classification_rule_name(pattern: str) -> str:
    if not pattern:
        return "Regla aprendida"
    return f"Aprendida: {pattern[:80]}"


def matching_classification_rule_for_review(
    rules: list[ClassificationRule],
    *,
    pattern: str,
    category_id: int,
    transaction_type: TransactionType,
    payment_method: PaymentMethod | None,
    direction: Direction,
) -> ClassificationRule | None:
    normalized_pattern = pattern.strip().lower()
    for rule in rules:
        if (
            rule.is_active
            and rule.rule_type == ClassificationRuleType.DESCRIPTION_CONTAINS
            and rule.match_field == ClassificationMatchField.DESCRIPTION_CLEAN
            and rule.pattern.strip().lower() == normalized_pattern
            and rule.category_id == category_id
            and rule.transaction_type == transaction_type
            and rule.payment_method == payment_method
            and rule.direction == direction
        ):
            return rule
    return None


def conflicting_classification_rule_for_review(
    rules: list[ClassificationRule],
    *,
    pattern: str,
    category_id: int,
    transaction_type: TransactionType,
    payment_method: PaymentMethod | None,
    direction: Direction,
) -> ClassificationRule | None:
    normalized_pattern = pattern.strip().lower()
    for rule in rules:
        if not (
            rule.is_active
            and rule.rule_type == ClassificationRuleType.DESCRIPTION_CONTAINS
            and rule.match_field == ClassificationMatchField.DESCRIPTION_CLEAN
            and rule.pattern.strip().lower() == normalized_pattern
            and rule.direction == direction
        ):
            continue
        if (
            rule.category_id != category_id
            or rule.transaction_type != transaction_type
            or rule.payment_method != payment_method
        ):
            return rule
    return None


def matching_classification_rule_for_transaction_review(
    rules: list[ClassificationRule],
    *,
    transaction: Transaction,
    category_id: int,
    transaction_type: TransactionType,
    payment_method: PaymentMethod | None,
) -> ClassificationRule | None:
    for rule in rules:
        if not classification_rule_target_is_compatible_with_review(
            rule,
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            direction=transaction.direction,
        ):
            continue
        if classification_rule_matches_review_transaction(rule, transaction):
            return rule
    return None


def conflicting_classification_rule_for_transaction_review(
    rules: list[ClassificationRule],
    *,
    transaction: Transaction,
    category_id: int,
    transaction_type: TransactionType,
    payment_method: PaymentMethod | None,
) -> ClassificationRule | None:
    for rule in rules:
        if not classification_rule_matches_review_transaction(rule, transaction):
            continue
        if not classification_rule_target_is_compatible_with_review(
            rule,
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            direction=transaction.direction,
        ):
            return rule
    return None


def classification_rule_target_is_compatible_with_review(
    rule: ClassificationRule,
    *,
    category_id: int,
    transaction_type: TransactionType,
    payment_method: PaymentMethod | None,
    direction: Direction,
) -> bool:
    return (
        rule.is_active
        and rule.category_id == category_id
        and rule.direction in {None, direction}
        and rule.transaction_type in {None, transaction_type}
        and rule.payment_method in {None, payment_method}
    )


def classification_rule_matches_review_transaction(
    rule: ClassificationRule,
    transaction: Transaction,
) -> bool:
    if not rule.is_active:
        return False
    if rule.direction is not None and rule.direction != transaction.direction:
        return False
    if (
        rule.amount_min_minor is not None
        and transaction.amount_minor < rule.amount_min_minor
    ):
        return False
    if (
        rule.amount_max_minor is not None
        and transaction.amount_minor > rule.amount_max_minor
    ):
        return False
    if rule.match_field == ClassificationMatchField.DESCRIPTION_RAW:
        value = normalize_classification_text(transaction.description_raw or "")
    else:
        value = normalize_classification_text(transaction.description_clean or "")
    if not value:
        return False
    if rule.rule_type == ClassificationRuleType.DESCRIPTION_CONTAINS:
        return normalize_classification_text(rule.pattern) in value
    if rule.rule_type == ClassificationRuleType.DESCRIPTION_REGEX:
        try:
            return re.search(rule.pattern, value, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def refresh_pending_classifications_after_rule_update(
    repository: AccountingRepository,
    *,
    user_profile_id: int,
) -> int:
    classifier = DeterministicClassificationService(repository)
    updated_count = 0
    for transaction in repository.list_transactions(
        user_profile_id=user_profile_id,
        review_status=TransactionReviewStatus.PENDING_REVIEW,
    ):
        result = classifier.classify_transaction(
            user_profile_id=user_profile_id,
            transaction=transaction,
        )
        if result is None:
            continue
        decisions = repository.list_classification_decisions(
            transaction_id=transaction.id,
            user_profile_id=user_profile_id,
        )
        active_decisions = [
            decision
            for decision in decisions
            if decision.decision_status != ClassificationDecisionStatus.SUPERSEDED
        ]
        if active_decisions and classification_result_matches_decision(
            result,
            active_decisions[-1],
            transaction,
        ):
            continue
        classifier.classify_and_record_transaction(
            user_profile_id=user_profile_id,
            transaction=transaction,
            decided_by="system",
        )
        updated_count += 1
    return updated_count


def refresh_pending_classifications_from_ui(
    session_factory,
    *,
    user_profile_id: int,
) -> int:
    with session_scope(session_factory) as session:
        updated_count = refresh_pending_classifications_after_rule_update(
            AccountingRepository(session),
            user_profile_id=user_profile_id,
        )
        session.flush()
        return updated_count


def classification_result_matches_decision(
    result,
    decision: ClassificationDecision,
    transaction: Transaction,
) -> bool:
    if (
        decision.decision_source != result.decision_source
        or decision.decision_status != result.decision_status
        or decision.classification_rule_id != result.classification_rule_id
        or decision.category_id != result.category_id
        or decision.transaction_type != result.transaction_type
        or decision.payment_method != result.payment_method
    ):
        return False
    if result.should_apply_to_transaction:
        return (
            transaction.category_id == result.category_id
            and transaction.transaction_type == result.transaction_type
            and transaction.payment_method == result.payment_method
        )
    return True


def transaction_table_rows(
    transactions,
    *,
    account_labels: dict[int, str],
    category_labels: dict[int | None, str],
    shared_transaction_ids: set[int] | None = None,
) -> list[dict]:
    shared_transaction_ids = shared_transaction_ids or set()
    return [
        {
            "id": transaction.id,
            "fecha": transaction.transaction_date,
            "descripcion": transaction.description_clean or "",
            "importe": format_amount_minor(transaction.amount_minor),
            "direccion": transaction.direction.value,
            "tipo": transaction.transaction_type.value,
            "cuenta": account_labels.get(
                transaction.account_id,
                f"Cuenta {transaction.account_id}",
            ),
            "categoria": category_labels.get(
                transaction.category_id,
                f"Categoría {transaction.category_id}",
            ),
            "metodo_pago": (
                transaction.payment_method.value
                if transaction.payment_method is not None
                else ""
            ),
            "compartida": "sí" if transaction.id in shared_transaction_ids else "",
            "estado": transaction.review_status.value,
            "eliminar": False,
        }
        for transaction in transactions
    ]


def account_label_for_transaction_table(account) -> str:
    if account.is_active:
        return account.name
    return f"{account.name} (eliminada)"


def category_label_for_transaction_table(category) -> str:
    if category.is_active:
        return category.name
    return f"{category.name} (eliminada)"


def apply_transaction_table_changes(
    session_factory,
    *,
    user_profile_id: int,
    original_transactions,
    edited_rows: list[dict],
    account_ids_by_label: dict[str, int],
    category_ids_by_label: dict[str, int | None],
    allow_locked_period_override: bool = False,
) -> tuple[int, int]:
    original_by_id = {transaction.id: transaction for transaction in original_transactions}
    updated_count = 0
    deleted_count = 0

    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        for row in edited_rows:
            transaction_id = int(row["id"])
            transaction = original_by_id[transaction_id]
            if row["eliminar"]:
                soft_delete_transaction_for_ui(
                    service,
                    user_profile_id=user_profile_id,
                    transaction_id=transaction_id,
                    allow_locked_period_override=allow_locked_period_override,
                )
                deleted_count += 1
                continue

            payload = edited_transaction_payload(
                row,
                account_ids_by_label=account_ids_by_label,
                category_ids_by_label=category_ids_by_label,
            )
            if not transaction_row_changed(transaction, payload):
                continue
            service.update_manual_transaction(
                user_profile_id=user_profile_id,
                transaction_id=transaction_id,
                account_id=payload["account_id"],
                category_id=payload["category_id"],
                transaction_date=payload["transaction_date"],
                description_clean=payload["description_clean"],
                amount_minor=payload["amount_minor"],
                direction=Direction(payload["direction"]),
                transaction_type=TransactionType(payload["transaction_type"]),
                payment_method=(
                    PaymentMethod(payload["payment_method"])
                    if payload["payment_method"] is not None
                    else None
                ),
                decided_by="local_ui",
                allow_locked_period_override=allow_locked_period_override,
            )
            updated_count += 1

    return updated_count, deleted_count


def soft_delete_transaction_for_ui(
    service: AccountingService,
    *,
    user_profile_id: int,
    transaction_id: int,
    allow_locked_period_override: bool = False,
) -> None:
    if hasattr(service, "soft_delete_transaction"):
        service.soft_delete_transaction(
            user_profile_id=user_profile_id,
            transaction_id=transaction_id,
            decided_by="local_ui",
            allow_locked_period_override=allow_locked_period_override,
        )
        return

    transaction = service.repository.session.get(Transaction, transaction_id)
    if transaction is None or transaction.user_profile_id != user_profile_id:
        raise ValueError("Transaction was not found for the user profile.")
    transaction.is_deleted = True


def transaction_table_has_locked_period_changes(
    session_factory,
    *,
    user_profile_id: int,
    original_transactions,
    edited_rows: list[dict],
    account_ids_by_label: dict[str, int],
    category_ids_by_label: dict[str, int | None],
) -> bool:
    user_profile = load_user_profile(session_factory, user_profile_id)
    if user_profile is None:
        return False
    original_by_id = {transaction.id: transaction for transaction in original_transactions}
    dates_to_check: list[date] = []
    for row in edited_rows:
        transaction_id = int(row["id"])
        transaction = original_by_id[transaction_id]
        if row["eliminar"]:
            dates_to_check.append(transaction.transaction_date)
            continue
        payload = edited_transaction_payload(
            row,
            account_ids_by_label=account_ids_by_label,
            category_ids_by_label=category_ids_by_label,
        )
        if transaction_row_changed(transaction, payload):
            dates_to_check.extend([transaction.transaction_date, payload["transaction_date"]])
    return bool(protected_transaction_dates(user_profile, dates_to_check))


def edited_transaction_payload(
    row: dict,
    *,
    account_ids_by_label: dict[str, int],
    category_ids_by_label: dict[str, int | None],
) -> dict:
    account_label = row["cuenta"]
    category_label = row["categoria"]
    if account_label not in account_ids_by_label:
        raise ValueError("Selecciona una cuenta válida en la tabla.")
    if category_label not in category_ids_by_label:
        raise ValueError("Selecciona una categoría válida en la tabla.")

    return {
        "transaction_date": normalize_editor_date(row["fecha"]),
        "account_id": account_ids_by_label[account_label],
        "category_id": category_ids_by_label[category_label],
        "description_clean": str(row["descripcion"]).strip(),
        "amount_minor": parse_amount_minor(str(row["importe"])),
        "direction": row["direccion"],
        "transaction_type": row["tipo"],
        "payment_method": row["metodo_pago"] or None,
    }


def normalize_editor_date(value) -> date:
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


def transaction_row_changed(transaction, payload: dict) -> bool:
    payment_method = (
        PaymentMethod(payload["payment_method"])
        if payload["payment_method"] is not None
        else None
    )
    return (
        transaction.transaction_date != payload["transaction_date"]
        or transaction.account_id != payload["account_id"]
        or transaction.category_id != payload["category_id"]
        or (transaction.description_clean or "") != payload["description_clean"]
        or transaction.amount_minor != payload["amount_minor"]
        or transaction.direction != Direction(payload["direction"])
        or transaction.transaction_type != TransactionType(payload["transaction_type"])
        or transaction.payment_method != payment_method
    )


def transaction_table_success_message(*, updated_count: int, deleted_count: int) -> str:
    parts = []
    if updated_count:
        parts.append(f"{updated_count} actualizada(s)")
    if deleted_count:
        parts.append(f"{deleted_count} eliminada(s)")
    return "Cambios guardados: " + ", ".join(parts)


def profile_selector(profiles) -> int | None:
    if not profiles:
        st.sidebar.info("No hay perfiles.")
        return None
    profile_ids = [profile.id for profile in profiles]
    labels = {profile.id: profile.display_name for profile in profiles}
    return st.sidebar.selectbox(
        "Perfil",
        options=profile_ids,
        format_func=labels.get,
    )


def load_profiles(session_factory):
    with session_scope(session_factory) as session:
        return AccountingRepository(session).list_user_profiles()


def load_user_profile(session_factory, user_profile_id: int):
    with session_scope(session_factory) as session:
        return AccountingRepository(session).get_user_profile(user_profile_id)


def update_profile_transaction_lock_from_ui(
    session_factory,
    *,
    user_profile_id: int,
    transactions_locked_until: date | None,
) -> None:
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        service.update_user_profile_transaction_lock(
            user_profile_id=user_profile_id,
            transactions_locked_until=transactions_locked_until,
        )


def render_locked_period_override_for_dates(
    session_factory,
    *,
    user_profile_id: int,
    transaction_dates: list[date],
    key: str,
) -> bool:
    user_profile = load_user_profile(session_factory, user_profile_id)
    if user_profile is None:
        return False
    if not protected_transaction_dates(user_profile, transaction_dates):
        return False

    st.warning(
        "La fecha esta en un periodo protegido "
        f"(hasta {user_profile.transactions_locked_until.isoformat()})."
    )
    return st.checkbox(
        "Confirmo el permiso adicional para operar en el periodo protegido",
        key=key,
    )


def locked_import_candidate_dates(
    session_factory,
    *,
    user_profile_id: int,
    transaction_dates: list[date],
) -> list[date]:
    user_profile = load_user_profile(session_factory, user_profile_id)
    if user_profile is None:
        return []
    return protected_transaction_dates(user_profile, transaction_dates)


def load_categories(session_factory, user_profile_id: int, *, include_inactive: bool = False):
    with session_scope(session_factory) as session:
        return AccountingRepository(session).list_categories(
            user_profile_id,
            include_inactive=include_inactive,
        )


def load_counterparties(
    session_factory,
    user_profile_id: int,
    *,
    include_inactive: bool = False,
):
    with session_scope(session_factory) as session:
        return AccountingRepository(session).list_counterparties(
            user_profile_id,
            include_inactive=include_inactive,
        )


def load_classification_rules(
    session_factory,
    user_profile_id: int,
    *,
    include_inactive: bool = False,
):
    with session_scope(session_factory) as session:
        return AccountingRepository(session).list_classification_rules(
            user_profile_id=user_profile_id,
            include_inactive=include_inactive,
        )


def load_latest_classification_decisions(
    session_factory,
    *,
    user_profile_id: int,
    transaction_ids: list[int],
) -> dict[int, ClassificationDecision]:
    latest_by_transaction_id = {}
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        for transaction_id in transaction_ids:
            decisions = repository.list_classification_decisions(
                transaction_id=transaction_id,
                user_profile_id=user_profile_id,
            )
            active_decisions = [
                decision
                for decision in decisions
                if decision.decision_status
                != ClassificationDecisionStatus.SUPERSEDED
            ]
            if active_decisions:
                latest_by_transaction_id[transaction_id] = active_decisions[-1]
    return latest_by_transaction_id


def load_reimbursement_matches(
    session_factory,
    *,
    user_profile_id: int,
    statuses: list[ReimbursementMatchStatus] | None = None,
):
    with session_scope(session_factory) as session:
        return AccountingRepository(session).list_reimbursement_matches_for_profile(
            user_profile_id=user_profile_id,
            statuses=statuses,
        )


def load_accounting_lists(
    session_factory,
    user_profile_id: int,
    *,
    include_inactive: bool = False,
):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        return (
            repository.list_accounts(user_profile_id, include_inactive=include_inactive),
            repository.list_categories(user_profile_id, include_inactive=include_inactive),
        )


def enum_values(enum_class) -> list[str]:
    return [member.value for member in enum_class]


def format_amount_minor(amount_minor: int) -> str:
    return f"{Decimal(amount_minor) / Decimal('100'):.2f}"


def format_signed_amount_minor(amount_minor: int) -> str:
    sign = "-" if amount_minor < 0 else ""
    return f"{sign}{format_amount_minor(abs(amount_minor))}"


def report_amount_basis_labels() -> list[str]:
    return ["Personal", "Bruto"]


def report_amount_basis_from_label(label: str) -> ReportAmountBasis:
    if label == "Bruto":
        return ReportAmountBasis.GROSS
    return ReportAmountBasis.PERSONAL


def category_total_table_rows(category_totals) -> list[dict]:
    return [
        {
            "categoría": total.category_name or "Sin categoría",
            "tipo": total.category_type.value if total.category_type is not None else "",
            "importe": format_signed_amount_minor(total.amount_minor),
        }
        for total in category_totals
    ]


def classification_rule_table_rows(rules: list[ClassificationRule]) -> list[dict]:
    return [
        {
            "nombre": rule.name,
            "patrón": rule.pattern,
            "categoría": rule.category.name if rule.category is not None else "",
            "dirección": rule.direction.value if rule.direction is not None else "",
            "tipo": (
                rule.transaction_type.value
                if rule.transaction_type is not None
                else ""
            ),
            "confianza": f"{int(rule.confidence * Decimal('100'))}%",
            "auto": "sí" if rule.auto_apply else "",
        }
        for rule in rules
    ]


def classification_rule_editor_rows(
    rules: list[ClassificationRule],
    *,
    category_labels: dict[int, str],
) -> list[dict]:
    return [
        {
            "id": rule.id,
            "nombre": rule.name,
            "patrón": rule.pattern,
            "categoría": (
                category_labels.get(rule.category_id, "Sin categoría")
                if rule.category_id is not None
                else "Sin categoría"
            ),
            "tipo_regla": rule.rule_type.value,
            "campo": rule.match_field.value,
            "dirección": rule.direction.value if rule.direction is not None else "",
            "tipo": (
                rule.transaction_type.value
                if rule.transaction_type is not None
                else ""
            ),
            "método": rule.payment_method.value
            if rule.payment_method is not None
            else "",
            "importe_mínimo": format_amount_minor(rule.amount_min_minor)
            if rule.amount_min_minor is not None
            else "",
            "importe_máximo": format_amount_minor(rule.amount_max_minor)
            if rule.amount_max_minor is not None
            else "",
            "prioridad": rule.priority,
            "confianza": int(rule.confidence * Decimal("100")),
            "autoaplicar": rule.auto_apply,
            "activa": rule.is_active,
            "acción": "",
        }
        for rule in rules
    ]


def classification_rule_editor_column_config(
    *,
    category_options: list[str],
) -> dict:
    return {
        "id": st.column_config.NumberColumn("id"),
        "nombre": st.column_config.TextColumn("nombre", required=True),
        "patrón": st.column_config.TextColumn("patrón", required=True),
        "categoría": st.column_config.SelectboxColumn(
            "categoría",
            options=category_options,
            required=True,
        ),
        "tipo_regla": st.column_config.SelectboxColumn(
            "tipo regla",
            options=enum_values(ClassificationRuleType),
            required=True,
        ),
        "campo": st.column_config.SelectboxColumn(
            "campo",
            options=enum_values(ClassificationMatchField),
            required=True,
        ),
        "dirección": st.column_config.SelectboxColumn(
            "dirección",
            options=[""] + enum_values(Direction),
            required=False,
        ),
        "tipo": st.column_config.SelectboxColumn(
            "tipo",
            options=[""] + enum_values(TransactionType),
            required=False,
        ),
        "método": st.column_config.SelectboxColumn(
            "método",
            options=[""] + enum_values(PaymentMethod),
            required=False,
        ),
        "importe_mínimo": st.column_config.TextColumn("importe mínimo"),
        "importe_máximo": st.column_config.TextColumn("importe máximo"),
        "prioridad": st.column_config.NumberColumn("prioridad", min_value=0, step=1),
        "confianza": st.column_config.NumberColumn(
            "confianza %",
            min_value=0,
            max_value=100,
            step=1,
        ),
        "autoaplicar": st.column_config.CheckboxColumn("autoaplicar"),
        "activa": st.column_config.CheckboxColumn("activa"),
        "acción": st.column_config.SelectboxColumn(
            "acción",
            options=["", "Borrar definitivamente"],
            required=False,
        ),
    }


def apply_classification_rule_table_changes(
    session_factory,
    *,
    user_profile_id: int,
    original_rules: list[ClassificationRule],
    edited_rows: list[dict],
    category_ids_by_label: dict[str, int | None],
) -> tuple[int, int, int]:
    original_by_id = {rule.id: rule for rule in original_rules}
    updated_count = 0
    hard_deleted_count = 0
    unlinked_decision_count = 0
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        for row in edited_rows:
            rule_id = int(row["id"])
            rule = original_by_id[rule_id]
            if row.get("acción") == "Borrar definitivamente":
                unlinked_decision_count += hard_delete_classification_rule_for_ui(
                    service,
                    user_profile_id=user_profile_id,
                    classification_rule_id=rule_id,
                )
                hard_deleted_count += 1
                continue
            payload = edited_classification_rule_payload(
                row,
                category_ids_by_label=category_ids_by_label,
            )
            if not classification_rule_row_changed(rule, payload):
                continue
            update_classification_rule_for_ui(
                service,
                user_profile_id=user_profile_id,
                classification_rule_id=rule_id,
                name=payload["name"],
                rule_type=payload["rule_type"],
                match_field=payload["match_field"],
                pattern=payload["pattern"],
                category_id=payload["category_id"],
                transaction_type=payload["transaction_type"],
                payment_method=payload["payment_method"],
                direction=payload["direction"],
                amount_min_minor=payload["amount_min_minor"],
                amount_max_minor=payload["amount_max_minor"],
                priority=payload["priority"],
                confidence=payload["confidence"],
                auto_apply=payload["auto_apply"],
                is_active=payload["is_active"],
            )
            updated_count += 1
    return updated_count, hard_deleted_count, unlinked_decision_count


def edited_classification_rule_payload(
    row: dict,
    *,
    category_ids_by_label: dict[str, int | None],
) -> dict:
    category_label = normalized_optional_text(row.get("categoría")) or "Sin categoría"
    if category_label not in category_ids_by_label:
        raise ValueError(f"La categoría '{category_label}' no existe.")
    return {
        "name": required_text(row.get("nombre"), "La regla necesita nombre."),
        "pattern": required_text(row.get("patrón"), "La regla necesita patrón."),
        "category_id": category_ids_by_label[category_label],
        "rule_type": ClassificationRuleType(
            required_text(row.get("tipo_regla"), "La regla necesita tipo.")
        ),
        "match_field": ClassificationMatchField(
            required_text(row.get("campo"), "La regla necesita campo.")
        ),
        "direction": optional_enum_value(row.get("dirección"), Direction),
        "transaction_type": optional_enum_value(row.get("tipo"), TransactionType),
        "payment_method": optional_enum_value(row.get("método"), PaymentMethod),
        "amount_min_minor": parse_optional_amount_minor(
            normalized_optional_text(row.get("importe_mínimo")) or ""
        ),
        "amount_max_minor": parse_optional_amount_minor(
            normalized_optional_text(row.get("importe_máximo")) or ""
        ),
        "priority": int(row.get("prioridad") or 0),
        "confidence": (
            Decimal(str(int(row.get("confianza") or 0))) / Decimal("100")
        ).quantize(Decimal("0.0001")),
        "auto_apply": bool(row.get("autoaplicar")),
        "is_active": bool(row.get("activa")),
    }


def classification_rule_row_changed(rule: ClassificationRule, payload: dict) -> bool:
    return (
        rule.name != payload["name"]
        or rule.pattern != payload["pattern"]
        or rule.category_id != payload["category_id"]
        or rule.rule_type != payload["rule_type"]
        or rule.match_field != payload["match_field"]
        or rule.direction != payload["direction"]
        or rule.transaction_type != payload["transaction_type"]
        or rule.payment_method != payload["payment_method"]
        or rule.amount_min_minor != payload["amount_min_minor"]
        or rule.amount_max_minor != payload["amount_max_minor"]
        or rule.priority != payload["priority"]
        or rule.confidence != payload["confidence"]
        or rule.auto_apply != payload["auto_apply"]
        or rule.is_active != payload["is_active"]
    )


def classification_rule_table_success_message(
    *,
    updated_count: int,
    hard_deleted_count: int,
    unlinked_decision_count: int = 0,
) -> str:
    messages = []
    if updated_count == 1:
        messages.append("1 regla actualizada")
    elif updated_count:
        messages.append(f"{updated_count} reglas actualizadas")
    if hard_deleted_count == 1:
        messages.append("1 regla borrada definitivamente")
    elif hard_deleted_count:
        messages.append(f"{hard_deleted_count} reglas borradas definitivamente")
    if unlinked_decision_count == 1:
        messages.append("1 decisión conserva la auditoría sin enlace a la regla")
    elif unlinked_decision_count:
        messages.append(
            f"{unlinked_decision_count} decisiones conservan la auditoría "
            "sin enlace a la regla"
        )
    return ". ".join(messages) + "."


def hard_delete_classification_rule_for_ui(
    service: AccountingService,
    *,
    user_profile_id: int,
    classification_rule_id: int,
) -> int:
    if hasattr(service, "hard_delete_classification_rule"):
        return service.hard_delete_classification_rule(
            user_profile_id=user_profile_id,
            classification_rule_id=classification_rule_id,
        )

    rule = service.repository.session.get(ClassificationRule, classification_rule_id)
    if rule is None or rule.user_profile_id != user_profile_id:
        raise ValueError("La regla no existe para este perfil.")
    decisions = list(
        service.repository.session.scalars(
            select(ClassificationDecision)
            .join(Transaction)
            .where(
                ClassificationDecision.classification_rule_id
                == classification_rule_id,
                Transaction.user_profile_id == user_profile_id,
            )
        )
    )
    for decision in decisions:
        decision.classification_rule_id = None
    service.repository.session.delete(rule)
    return len(decisions)


def update_classification_rule_for_ui(
    service: AccountingService,
    *,
    user_profile_id: int,
    classification_rule_id: int,
    name: str,
    rule_type: ClassificationRuleType,
    match_field: ClassificationMatchField,
    pattern: str,
    category_id: int | None,
    transaction_type: TransactionType | None,
    payment_method: PaymentMethod | None,
    direction: Direction | None,
    amount_min_minor: int | None,
    amount_max_minor: int | None,
    priority: int,
    confidence: Decimal,
    auto_apply: bool,
    is_active: bool,
) -> None:
    if hasattr(service, "update_classification_rule"):
        service.update_classification_rule(
            user_profile_id=user_profile_id,
            classification_rule_id=classification_rule_id,
            name=name,
            rule_type=rule_type,
            match_field=match_field,
            pattern=pattern,
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            direction=direction,
            amount_min_minor=amount_min_minor,
            amount_max_minor=amount_max_minor,
            priority=priority,
            confidence=confidence,
            auto_apply=auto_apply,
            is_active=is_active,
        )
        return

    validate_classification_rule_payload_for_ui(
        service,
        user_profile_id=user_profile_id,
        name=name,
        rule_type=rule_type,
        pattern=pattern,
        category_id=category_id,
        transaction_type=transaction_type,
        amount_min_minor=amount_min_minor,
        amount_max_minor=amount_max_minor,
        confidence=confidence,
        is_active=is_active,
    )
    rule = service.repository.session.get(ClassificationRule, classification_rule_id)
    if rule is None or rule.user_profile_id != user_profile_id:
        raise ValueError("La regla no existe para este perfil.")

    rule.name = name.strip()
    rule.rule_type = rule_type
    rule.match_field = match_field
    rule.pattern = pattern.strip()
    rule.category_id = category_id
    rule.transaction_type = transaction_type
    rule.payment_method = payment_method
    rule.direction = direction
    rule.amount_min_minor = amount_min_minor
    rule.amount_max_minor = amount_max_minor
    rule.priority = priority
    rule.confidence = confidence
    rule.auto_apply = auto_apply
    rule.is_active = is_active


def validate_classification_rule_payload_for_ui(
    service: AccountingService,
    *,
    user_profile_id: int,
    name: str,
    rule_type: ClassificationRuleType,
    pattern: str,
    category_id: int | None,
    transaction_type: TransactionType | None,
    amount_min_minor: int | None,
    amount_max_minor: int | None,
    confidence: Decimal,
    is_active: bool,
) -> None:
    if not name.strip():
        raise ValueError("La regla necesita nombre.")
    if not pattern.strip():
        raise ValueError("La regla necesita patrón.")
    if rule_type == ClassificationRuleType.DESCRIPTION_REGEX:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("El patrón regex de la regla no es válido.") from exc
    if confidence < Decimal("0") or confidence > Decimal("1"):
        raise ValueError("La confianza de la regla debe estar entre 0 y 100%.")
    if (
        amount_min_minor is not None
        and amount_max_minor is not None
        and amount_min_minor > amount_max_minor
    ):
        raise ValueError("El rango de importes de la regla no es válido.")
    if category_id is None:
        return

    category = service.repository.get_category(
        category_id=category_id,
        user_profile_id=user_profile_id,
    )
    if category is None:
        raise ValueError("La categoría de la regla no existe para este perfil.")
    if is_active and not category.is_active:
        raise ValueError("Una regla activa necesita una categoría activa.")
    if transaction_type is not None and not ui_category_type_matches_transaction_type(
        category.category_type,
        transaction_type,
    ):
        raise ValueError("La categoría y el tipo de transacción no son compatibles.")


def ui_category_type_matches_transaction_type(
    category_type: CategoryType,
    transaction_type: TransactionType,
) -> bool:
    if category_type == CategoryType.INCOME:
        return transaction_type == TransactionType.INCOME
    if category_type == CategoryType.EXPENSE:
        return transaction_type in {
            TransactionType.EXPENSE,
            TransactionType.FEE,
            TransactionType.TAX,
            TransactionType.REFUND,
        }
    if category_type == CategoryType.TRANSFER:
        return transaction_type == TransactionType.TRANSFER
    if category_type == CategoryType.ADJUSTMENT:
        return transaction_type == TransactionType.ADJUSTMENT
    if category_type == CategoryType.SAVING:
        return transaction_type == TransactionType.SAVING
    if category_type == CategoryType.INVESTMENT:
        return transaction_type == TransactionType.INVESTMENT
    if category_type == CategoryType.DEBT:
        return transaction_type == TransactionType.DEBT_PAYMENT
    return False


def normalized_optional_text(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def required_text(value, message: str) -> str:
    text = normalized_optional_text(value)
    if text is None:
        raise ValueError(message)
    return text


def optional_enum_value(value, enum_class):
    text = normalized_optional_text(value)
    if text is None:
        return None
    return enum_class(text)


def counterparty_table_rows(counterparties: list[Counterparty]) -> list[dict]:
    return [
        {
            "nombre": counterparty.display_name,
            "alias": counterparty.aliases_raw or "",
        }
        for counterparty in counterparties
    ]


def reimbursement_match_table_rows(matches: list[ReimbursementMatch]) -> list[dict]:
    return [
        {
            "id": match.id,
            "contraparte": match.shared_expense_allocation.counterparty.display_name,
            "gasto": shared_expense_transaction_label(
                match.shared_expense_allocation.transaction
            ),
            "reembolso": shared_expense_transaction_label(
                match.reimbursement_transaction
            ),
            "importe": format_amount_minor(match.matched_amount_minor),
            "confianza": f"{int(match.confidence * Decimal('100'))}%",
            "motivo": match.notes or "",
        }
        for match in matches
    ]


def reimbursement_match_label(match: ReimbursementMatch) -> str:
    return (
        f"{match.id} · "
        f"{match.shared_expense_allocation.counterparty.display_name} · "
        f"{format_amount_minor(match.matched_amount_minor)}"
    )


def shared_expense_transaction_label(transaction: Transaction) -> str:
    return (
        f"{transaction.id} · {transaction.transaction_date.isoformat()} · "
        f"{transaction.description_clean or ''} · "
        f"{format_amount_minor(transaction.amount_minor)}"
    )


def mark_transaction_shared_50_50_from_ui(
    session_factory,
    *,
    user_profile_id: int,
    transaction_id: int,
    counterparty_id: int,
    allow_locked_period_override: bool = False,
) -> tuple[int, int, int]:
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        allocation = service.mark_transaction_shared_50_50(
            user_profile_id=user_profile_id,
            transaction_id=transaction_id,
            counterparty_id=counterparty_id,
            decided_by="local_ui",
            allow_locked_period_override=allow_locked_period_override,
        )
        session.flush()
        return (
            allocation.id,
            allocation.personal_share_minor,
            allocation.recoverable_share_minor,
        )


def create_classification_rule_from_ui(
    session_factory,
    *,
    user_profile_id: int,
    name: str,
    pattern: str,
    category_id: int,
    rule_type: ClassificationRuleType,
    match_field: ClassificationMatchField,
    direction: Direction | None,
    transaction_type: TransactionType | None,
    payment_method: PaymentMethod | None,
    amount_min_minor: int | None,
    amount_max_minor: int | None,
    priority: int,
    confidence: Decimal,
    auto_apply: bool,
) -> None:
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        service.create_classification_rule(
            user_profile_id=user_profile_id,
            name=name,
            rule_type=rule_type,
            match_field=match_field,
            pattern=pattern,
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            direction=direction,
            amount_min_minor=amount_min_minor,
            amount_max_minor=amount_max_minor,
            priority=priority,
            confidence=confidence,
            auto_apply=auto_apply,
        )


def confirm_imported_transaction_review_from_ui(
    session_factory,
    *,
    user_profile_id: int,
    transaction_id: int,
    category_id: int | None,
    transaction_type: TransactionType,
    payment_method: PaymentMethod | None,
    decided_by: str,
    allow_locked_period_override: bool = False,
    create_learned_rule: bool = False,
    learned_rule_pattern: str | None = None,
    learned_rule_auto_apply: bool = True,
) -> tuple[int, int | None, int]:
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        decision = service.confirm_transaction_classification(
            user_profile_id=user_profile_id,
            transaction_id=transaction_id,
            category_id=category_id,
            transaction_type=transaction_type,
            payment_method=payment_method,
            decided_by=decided_by,
            notes="Imported transaction review.",
            allow_locked_period_override=allow_locked_period_override,
        )
        session.flush()

        learned_rule_id = None
        reclassified_count = 0
        if create_learned_rule:
            if category_id is None:
                raise ValueError("Selecciona una categoría para crear una regla.")
            pattern = (learned_rule_pattern or "").strip()
            if not pattern:
                raise ValueError("Introduce un texto para crear la regla.")
            transaction = service.repository.get_transaction(
                transaction_id=transaction_id,
                user_profile_id=user_profile_id,
            )
            if transaction is None:
                raise ValueError("Transaction was not found for the user profile.")
            active_rules = service.repository.list_classification_rules(
                user_profile_id=user_profile_id
            )
            existing_rule = matching_classification_rule_for_transaction_review(
                active_rules,
                transaction=transaction,
                category_id=category_id,
                transaction_type=transaction_type,
                payment_method=payment_method,
            )
            if existing_rule is None:
                conflicting_rule = conflicting_classification_rule_for_transaction_review(
                    active_rules,
                    transaction=transaction,
                    category_id=category_id,
                    transaction_type=transaction_type,
                    payment_method=payment_method,
                )
                if conflicting_rule is not None:
                    raise ValueError(
                        "Ya existe una regla activa que coincide con esta "
                        "transacción y apunta a otra clasificación. Revisa las "
                        "reglas en Configuración antes de crear otra."
                    )
            if existing_rule is None:
                existing_rule = matching_classification_rule_for_review(
                    active_rules,
                    pattern=pattern,
                    category_id=category_id,
                    transaction_type=transaction_type,
                    payment_method=payment_method,
                    direction=transaction.direction,
                )
            if existing_rule is None:
                conflicting_rule = conflicting_classification_rule_for_review(
                    active_rules,
                    pattern=pattern,
                    category_id=category_id,
                    transaction_type=transaction_type,
                    payment_method=payment_method,
                    direction=transaction.direction,
                )
                if conflicting_rule is not None:
                    raise ValueError(
                        "Ya existe una regla activa con ese texto y otra "
                        "clasificación. Revisa las reglas en Configuración "
                        "antes de crear otra."
                    )
            if existing_rule is None:
                rule = service.create_classification_rule(
                    user_profile_id=user_profile_id,
                    name=learned_classification_rule_name(pattern),
                    rule_type=ClassificationRuleType.DESCRIPTION_CONTAINS,
                    match_field=ClassificationMatchField.DESCRIPTION_CLEAN,
                    pattern=pattern,
                    category_id=category_id,
                    transaction_type=transaction_type,
                    payment_method=payment_method,
                    direction=transaction.direction,
                    confidence=Decimal("0.9500"),
                    auto_apply=learned_rule_auto_apply,
                )
                session.flush()
                learned_rule_id = rule.id
            else:
                learned_rule_id = existing_rule.id
            reclassified_count = refresh_pending_classifications_after_rule_update(
                service.repository,
                user_profile_id=user_profile_id,
            )
            session.flush()
        return decision.id, learned_rule_id, reclassified_count


def confirm_reimbursement_match_from_ui(
    session_factory,
    *,
    user_profile_id: int,
    reimbursement_match_id: int,
    allow_locked_period_override: bool = False,
) -> None:
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        service.confirm_reimbursement_match(
            user_profile_id=user_profile_id,
            reimbursement_match_id=reimbursement_match_id,
            decided_by="local_ui",
            allow_locked_period_override=allow_locked_period_override,
        )


def reject_reimbursement_match_from_ui(
    session_factory,
    *,
    user_profile_id: int,
    reimbursement_match_id: int,
    allow_locked_period_override: bool = False,
) -> None:
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        service.reject_reimbursement_match(
            user_profile_id=user_profile_id,
            reimbursement_match_id=reimbursement_match_id,
            decided_by="local_ui",
            allow_locked_period_override=allow_locked_period_override,
        )


def waive_shared_expense_from_ui(
    session_factory,
    *,
    user_profile_id: int,
    transaction_id: int,
    allow_locked_period_override: bool = False,
) -> None:
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        service.waive_shared_expense_allocation(
            user_profile_id=user_profile_id,
            transaction_id=transaction_id,
            decided_by="local_ui",
            allow_locked_period_override=allow_locked_period_override,
        )


def parse_amount_minor(value: str) -> int:
    normalized_value = value.replace(",", ".")
    try:
        amount = Decimal(normalized_value)
    except InvalidOperation as exc:
        raise ValueError("Introduce un importe válido.") from exc
    if amount < 0:
        raise ValueError("Introduce un importe positivo y usa Dirección.")
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_optional_amount_minor(value: str) -> int | None:
    if not value.strip():
        return None
    return parse_amount_minor(value)


def canonical_key_from_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    canonical_key = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name).strip("_").lower()
    if not canonical_key:
        raise ValueError("Category name must produce a canonical key.")
    return canonical_key


def flash_success(scope: str, message: str) -> None:
    st.session_state[f"lxcell_{scope}_success"] = message


def render_flash_success(scope: str) -> None:
    message = st.session_state.pop(f"lxcell_{scope}_success", None)
    if message:
        st.success(message)


def render_pending_duplicate_confirmation(session_factory, selected_profile_id: int) -> None:
    pending = st.session_state.get(PENDING_DUPLICATE_TRANSACTION_KEY)
    if not pending or pending["user_profile_id"] != selected_profile_id:
        return

    duplicate_ids = ", ".join(str(value) for value in pending["duplicate_ids"])
    st.warning(
        "Ya existe una transacción con los mismos datos "
        f"(id: {duplicate_ids}). Confirma si realmente quieres registrarla otra vez."
    )
    confirm_column, cancel_column = st.columns(2)
    with confirm_column:
        if st.button("Registrar duplicado", type="primary"):
            transaction_id, review_status = record_manual_transaction_from_payload(
                session_factory,
                user_profile_id=selected_profile_id,
                payload=pending["payload"],
            )
            st.session_state.pop(PENDING_DUPLICATE_TRANSACTION_KEY, None)
            flash_success(
                "transaction",
                "Transacción duplicada registrada: "
                f"{transaction_id} · {format_review_status(review_status)}",
            )
            st.rerun()
    with cancel_column:
        if st.button("Cancelar"):
            st.session_state.pop(PENDING_DUPLICATE_TRANSACTION_KEY, None)
            st.rerun()


def manual_transaction_payload(
    *,
    transaction_date: date,
    account_id: int,
    category_id: int | None,
    description: str,
    amount_minor: int,
    direction: str,
    transaction_type: str,
    payment_method: str,
    decided_by: str,
    allow_locked_period_override: bool = False,
) -> dict:
    return {
        "transaction_date": transaction_date,
        "account_id": account_id,
        "category_id": category_id,
        "description_clean": description.strip(),
        "amount_minor": amount_minor,
        "currency": "EUR",
        "direction": direction,
        "transaction_type": transaction_type,
        "payment_method": payment_method or None,
        "decided_by": decided_by.strip(),
        "allow_locked_period_override": allow_locked_period_override,
    }


def find_duplicate_transactions(session_factory, *, user_profile_id: int, payload: dict):
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        transactions = repository.list_transactions(
            user_profile_id=user_profile_id,
            account_id=payload["account_id"],
            start_date=payload["transaction_date"],
            end_date=payload["transaction_date"],
        )
        return [
            transaction
            for transaction in transactions
            if is_duplicate_transaction(transaction, payload)
        ]


def is_duplicate_transaction(transaction, payload: dict) -> bool:
    payment_method = (
        PaymentMethod(payload["payment_method"])
        if payload["payment_method"] is not None
        else None
    )
    return (
        transaction.category_id == payload["category_id"]
        and transaction.description_clean == payload["description_clean"]
        and transaction.amount_minor == payload["amount_minor"]
        and transaction.currency == payload["currency"]
        and transaction.direction == Direction(payload["direction"])
        and transaction.transaction_type == TransactionType(payload["transaction_type"])
        and transaction.payment_method == payment_method
    )


def record_manual_transaction_from_payload(
    session_factory, *, user_profile_id: int, payload: dict
) -> tuple[int, object]:
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        transaction = service.record_manual_transaction(
            user_profile_id=user_profile_id,
            account_id=payload["account_id"],
            category_id=payload["category_id"],
            transaction_date=payload["transaction_date"],
            description_clean=payload["description_clean"],
            amount_minor=payload["amount_minor"],
            direction=Direction(payload["direction"]),
            transaction_type=TransactionType(payload["transaction_type"]),
            payment_method=(
                PaymentMethod(payload["payment_method"])
                if payload["payment_method"] is not None
                else None
            ),
            decided_by=payload["decided_by"],
            allow_locked_period_override=payload.get(
                "allow_locked_period_override",
                False,
            ),
        )
        session.flush()
        return transaction.id, transaction.review_status


def format_review_status(review_status) -> str:
    if review_status.value == "user_confirmed":
        return "confirmada"
    if review_status.value == "pending_review":
        return "pendiente de revisión"
    if review_status.value == "ignored":
        return "ignorada"
    return review_status.value


def friendly_integrity_error_message(error: IntegrityError) -> str:
    message = str(error.orig)
    if "accounts.user_profile_id, accounts.name" in message:
        return "Ya existe una cuenta con ese nombre en este perfil."
    if "categories.user_profile_id, categories.name" in message:
        return "Ya existe una categoría con ese nombre en este perfil."
    if "categories.user_profile_id, categories.canonical_key" in message:
        return "Ya existe una categoría con esa clave canónica en este perfil."
    if "counterparties.user_profile_id, counterparties.normalized_name" in message:
        return "Ya existe una contraparte con ese nombre en este perfil."
    return "No se pudo guardar porque ya existe un registro equivalente."


if __name__ == "__main__":
    run()
