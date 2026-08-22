"""Streamlit UI for local LXCell workflows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy.exc import IntegrityError

from lxcell.db.models import Category
from lxcell.db.runtime import DEFAULT_DATABASE_PATH, create_local_session_factory
from lxcell.db.session import session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    Direction,
    OwnershipType,
    PaymentMethod,
    TransactionType,
)
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService

PENDING_DUPLICATE_TRANSACTION_KEY = "lxcell_pending_duplicate_transaction"


def run() -> None:
    st.set_page_config(page_title="LXCell", page_icon="LX", layout="wide")
    st.title("LXCell")

    database_path = Path(
        st.sidebar.text_input("Base de datos", value=str(DEFAULT_DATABASE_PATH))
    )
    session_factory = create_local_session_factory(database_path)

    profiles = load_profiles(session_factory)
    selected_profile_id = profile_selector(profiles)

    setup_tab, transaction_tab, review_tab, categories_tab = st.tabs(
        ["Configuración", "Registrar", "Transacciones", "Categorías"]
    )

    with setup_tab:
        render_setup(session_factory, selected_profile_id)
    with transaction_tab:
        render_transaction_form(session_factory, selected_profile_id)
    with review_tab:
        render_transactions(session_factory, selected_profile_id)
    with categories_tab:
        render_categories(session_factory, selected_profile_id)


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
    accounts, categories = load_accounting_lists(session_factory, selected_profile_id)
    account_labels = {account.id: account.name for account in accounts}
    category_labels = {None: "Sin categoría"} | {
        category.id: category.name for category in categories
    }
    account_ids_by_label = {account.name: account.id for account in accounts}
    category_ids_by_label = {"Sin categoría": None} | {
        category.name: category.id for category in categories
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
                service.soft_delete_transaction(
                    user_profile_id=user_profile_id,
                    transaction_id=transaction_id,
                    decided_by="local_ui",
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
    return (
        name.strip()
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )


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
