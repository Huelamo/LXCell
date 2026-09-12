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

from lxcell.db.models import Category, CategoryMapping, ImportBatch, Transaction
from lxcell.db.runtime import DEFAULT_DATABASE_PATH, create_local_session_factory
from lxcell.db.session import session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    Direction,
    ImportSourceSystem,
    ImportStatus,
    OwnershipType,
    PaymentMethod,
    TransactionType,
)
from lxcell.importers import HistoricalExcelPreview, PdfStatementPreview
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService
from lxcell.services.historical_excel_import_service import HistoricalExcelImportService

PENDING_DUPLICATE_TRANSACTION_KEY = "lxcell_pending_duplicate_transaction"
HISTORICAL_EXCEL_PREVIEW_KEY = "lxcell_historical_excel_preview"
HISTORICAL_EXCEL_PREVIEW_VERSION = 5
HISTORICAL_EXCEL_ACCOUNT_NAME = "Excel histórico"
STATEMENT_PDF_PREVIEW_KEY = "lxcell_statement_pdf_preview"
STATEMENT_PDF_PREVIEW_VERSION = 1


def run() -> None:
    st.set_page_config(page_title="LXCell", page_icon="LX", layout="wide")
    st.title("LXCell")

    database_path = Path(
        st.sidebar.text_input("Base de datos", value=str(DEFAULT_DATABASE_PATH))
    )
    session_factory = create_local_session_factory(database_path)

    profiles = load_profiles(session_factory)
    selected_profile_id = profile_selector(profiles)

    setup_tab, transaction_tab, review_tab, categories_tab, import_tab = st.tabs(
        ["Configuración", "Registrar", "Transacciones", "Categorías", "Importar"]
    )

    with setup_tab:
        render_setup(session_factory, selected_profile_id)
    with transaction_tab:
        render_transaction_form(session_factory, selected_profile_id)
    with review_tab:
        render_transactions(session_factory, selected_profile_id)
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

    account_column, category_column = st.columns(2)
    with account_column:
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

    with category_column:
        st.subheader("Categoría")
        show_category_advanced = st.checkbox(
            "Opciones avanzadas",
            key="create_category_advanced",
        )
        with st.form("create_category", clear_on_submit=True):
            category_name = st.text_input("Nombre de la categoría")
            category_type = st.selectbox("Tipo de categoría", enum_values(CategoryType))
            canonical_key = ""
            if show_category_advanced:
                canonical_key = st.text_input("Clave canónica")
            display_order = st.number_input("Orden", min_value=0, step=1)
            submitted = st.form_submit_button("Crear categoría")
            if submitted:
                try:
                    resolved_canonical_key = (
                        canonical_key.strip()
                        if canonical_key.strip()
                        else canonical_key_from_name(category_name)
                    )
                    with session_scope(session_factory) as session:
                        service = AccountingService(AccountingRepository(session))
                        category = service.create_category(
                            user_profile_id=selected_profile_id,
                            name=category_name,
                            category_type=CategoryType(category_type),
                            canonical_key=resolved_canonical_key,
                            display_order=int(display_order),
                        )
                        session.flush()
                        flash_success("setup", f"Categoría creada: {category.name}")
                    st.rerun()
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

    st.subheader("Últimas transacciones")
    if not transactions:
        st.info("Todavía no hay transacciones.")
        return

    recent_transactions = transactions[-100:][::-1]
    editor_rows = transaction_table_rows(
        recent_transactions,
        account_labels=account_labels,
        category_labels=category_labels,
    )
    edited_table = st.data_editor(
        pd.DataFrame(editor_rows),
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=["id", "estado"],
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

    if st.button("Guardar cambios", type="primary"):
        if marked_for_deletion and not confirm_delete:
            st.warning("Marca la confirmación antes de eliminar transacciones.")
            return
        try:
            updated_count, deleted_count = apply_transaction_table_changes(
                session_factory,
                user_profile_id=selected_profile_id,
                original_transactions=recent_transactions,
                edited_rows=edited_rows,
                account_ids_by_label=account_ids_by_label,
                category_ids_by_label=category_ids_by_label,
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


def render_categories(session_factory, selected_profile_id: int | None) -> None:
    if selected_profile_id is None:
        st.info("Selecciona un perfil para ver categorías.")
        return

    render_flash_success("categories")
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

    candidate_column, page_column, issue_column = st.columns(3)
    candidate_column.metric("Movimientos", preview.transaction_count)
    page_column.metric("Páginas", preview.page_count)
    issue_column.metric("Incidencias", len(preview.issues))

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

    candidate_rows = statement_pdf_candidate_rows(preview, limit=100)
    if candidate_rows:
        st.subheader("Primeros movimientos")
        st.dataframe(pd.DataFrame(candidate_rows), use_container_width=True)


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
            "saldo": (
                format_signed_amount_minor(candidate.balance_minor)
                if candidate.balance_minor is not None
                else ""
            ),
            "hash": candidate.content_hash[:12],
        }
        for candidate in preview.candidates[:limit]
    ]


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


def transaction_table_rows(
    transactions,
    *,
    account_labels: dict[int, str],
    category_labels: dict[int | None, str],
) -> list[dict]:
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
            )
            updated_count += 1

    return updated_count, deleted_count


def soft_delete_transaction_for_ui(
    service: AccountingService,
    *,
    user_profile_id: int,
    transaction_id: int,
) -> None:
    if hasattr(service, "soft_delete_transaction"):
        service.soft_delete_transaction(
            user_profile_id=user_profile_id,
            transaction_id=transaction_id,
            decided_by="local_ui",
        )
        return

    transaction = service.repository.session.get(Transaction, transaction_id)
    if transaction is None or transaction.user_profile_id != user_profile_id:
        raise ValueError("Transaction was not found for the user profile.")
    transaction.is_deleted = True


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


def load_categories(session_factory, user_profile_id: int, *, include_inactive: bool = False):
    with session_scope(session_factory) as session:
        return AccountingRepository(session).list_categories(
            user_profile_id,
            include_inactive=include_inactive,
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


def parse_amount_minor(value: str) -> int:
    normalized_value = value.replace(",", ".")
    try:
        amount = Decimal(normalized_value)
    except InvalidOperation as exc:
        raise ValueError("Introduce un importe válido.") from exc
    if amount < 0:
        raise ValueError("Introduce un importe positivo y usa Dirección.")
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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
    return "No se pudo guardar porque ya existe un registro equivalente."


if __name__ == "__main__":
    run()
