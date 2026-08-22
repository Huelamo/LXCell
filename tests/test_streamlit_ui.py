from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from lxcell.db.models import Base
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import AccountType, CategoryType, Direction, TransactionType
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService
from lxcell.ui.streamlit_app import (
    canonical_key_from_name,
    category_table_rows,
    category_table_success_message,
    edited_category_payload,
    edited_transaction_payload,
    find_duplicate_transactions,
    friendly_integrity_error_message,
    manual_transaction_payload,
    parse_amount_minor,
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


def test_canonical_key_from_name_is_simple_and_stable():
    assert canonical_key_from_name("Category A") == "category_a"


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


def test_category_table_success_message_includes_deleted_categories():
    assert (
        category_table_success_message(
            updated_count=1,
            deleted_count=2,
            reactivated_count=3,
        )
        == "Categorías guardadas: 1 actualizada(s), 2 eliminada(s), 3 reactivada(s)"
    )


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
