"""Command line interface for local LXCell workflows."""

from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Sequence

from lxcell.db.runtime import (
    DEFAULT_DATABASE_PATH,
    create_local_session_factory,
    initialize_database,
)
from lxcell.db.session import session_scope
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    Direction,
    OwnershipType,
    PaymentMethod,
    TransactionReviewStatus,
    TransactionType,
)
from lxcell.repositories import AccountingRepository
from lxcell.services import AccountingService


def main(argv: Sequence[str] | None = None) -> int:
    """Run the LXCell command line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m lxcell.cli",
        description="Herramientas locales para registrar datos en LXCell.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help="Ruta de la base SQLite local. Por defecto: data/lxcell.db",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Inicializa la base de datos.")
    init_parser.set_defaults(handler=handle_init_db)

    profile_parser = subparsers.add_parser("add-profile", help="Crea un perfil.")
    profile_parser.add_argument("--display-name", required=True)
    profile_parser.add_argument("--currency", default="EUR")
    profile_parser.add_argument("--locale", default="es_ES")
    profile_parser.set_defaults(handler=handle_add_profile)

    account_parser = subparsers.add_parser("add-account", help="Crea una cuenta.")
    account_parser.add_argument("--profile-id", type=int, required=True)
    account_parser.add_argument("--name", required=True)
    account_parser.add_argument("--type", choices=enum_values(AccountType), required=True)
    account_parser.add_argument("--currency", default="EUR")
    account_parser.add_argument("--institution-name")
    account_parser.add_argument(
        "--ownership",
        choices=enum_values(OwnershipType),
        default=OwnershipType.PERSONAL.value,
    )
    account_parser.add_argument("--external-ref")
    account_parser.set_defaults(handler=handle_add_account)

    category_parser = subparsers.add_parser("add-category", help="Crea una categoria.")
    category_parser.add_argument("--profile-id", type=int, required=True)
    category_parser.add_argument("--name", required=True)
    category_parser.add_argument("--type", choices=enum_values(CategoryType), required=True)
    category_parser.add_argument("--canonical-key")
    category_parser.add_argument("--parent-id", type=int)
    category_parser.add_argument("--display-order", type=int, default=0)
    category_parser.set_defaults(handler=handle_add_category)

    transaction_parser = subparsers.add_parser(
        "add-transaction", help="Registra una transaccion manual."
    )
    transaction_parser.add_argument("--profile-id", type=int, required=True)
    transaction_parser.add_argument("--account-id", type=int, required=True)
    transaction_parser.add_argument("--category-id", type=int)
    transaction_parser.add_argument("--date", type=parse_date, required=True)
    transaction_parser.add_argument("--posted-date", type=parse_date)
    transaction_parser.add_argument("--description", required=True)
    transaction_parser.add_argument("--raw-description")
    transaction_parser.add_argument("--amount", type=parse_amount_minor, required=True)
    transaction_parser.add_argument("--currency", default="EUR")
    transaction_parser.add_argument(
        "--direction", choices=enum_values(Direction), required=True
    )
    transaction_parser.add_argument(
        "--type", choices=enum_values(TransactionType), required=True
    )
    transaction_parser.add_argument("--payment-method", choices=enum_values(PaymentMethod))
    transaction_parser.add_argument("--decided-by", default="local_cli")
    transaction_parser.set_defaults(handler=handle_add_transaction)

    list_parser = subparsers.add_parser(
        "list-transactions", help="Lista transacciones de un perfil."
    )
    list_parser.add_argument("--profile-id", type=int, required=True)
    list_parser.add_argument("--start-date", type=parse_date)
    list_parser.add_argument("--end-date", type=parse_date)
    list_parser.add_argument("--include-deleted", action="store_true")
    list_parser.set_defaults(handler=handle_list_transactions)

    return parser


def handle_init_db(args: argparse.Namespace) -> int:
    initialize_database(args.database)
    print(f"Base de datos inicializada: {args.database}")
    return 0


def handle_add_profile(args: argparse.Namespace) -> int:
    session_factory = create_local_session_factory(args.database)
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        profile = service.create_user_profile(
            display_name=args.display_name,
            default_currency=args.currency,
            locale=args.locale,
        )
        session.flush()
        print(f"Perfil creado: id={profile.id} nombre={profile.display_name}")
    return 0


def handle_add_account(args: argparse.Namespace) -> int:
    session_factory = create_local_session_factory(args.database)
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        account = service.create_account(
            user_profile_id=args.profile_id,
            name=args.name,
            account_type=AccountType(args.type),
            institution_name=args.institution_name,
            currency=args.currency,
            ownership_type=OwnershipType(args.ownership),
            external_account_ref=args.external_ref,
        )
        session.flush()
        print(f"Cuenta creada: id={account.id} nombre={account.name}")
    return 0


def handle_add_category(args: argparse.Namespace) -> int:
    session_factory = create_local_session_factory(args.database)
    canonical_key = args.canonical_key or canonical_key_from_name(args.name)
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        category = service.create_category(
            user_profile_id=args.profile_id,
            name=args.name,
            category_type=CategoryType(args.type),
            canonical_key=canonical_key,
            parent_category_id=args.parent_id,
            display_order=args.display_order,
        )
        session.flush()
        print(
            "Categoria creada: "
            f"id={category.id} nombre={category.name} clave={category.canonical_key}"
        )
    return 0


def handle_add_transaction(args: argparse.Namespace) -> int:
    session_factory = create_local_session_factory(args.database)
    with session_scope(session_factory) as session:
        service = AccountingService(AccountingRepository(session))
        transaction = service.record_manual_transaction(
            user_profile_id=args.profile_id,
            account_id=args.account_id,
            category_id=args.category_id,
            transaction_date=args.date,
            posted_date=args.posted_date,
            description_clean=args.description,
            description_raw=args.raw_description,
            amount_minor=args.amount,
            currency=args.currency,
            direction=Direction(args.direction),
            transaction_type=TransactionType(args.type),
            payment_method=(
                PaymentMethod(args.payment_method)
                if args.payment_method is not None
                else None
            ),
            decided_by=args.decided_by,
        )
        session.flush()
        print(
            "Transaccion registrada: "
            f"id={transaction.id} estado={transaction.review_status.value}"
        )
    return 0


def handle_list_transactions(args: argparse.Namespace) -> int:
    session_factory = create_local_session_factory(args.database)
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        transactions = repository.list_transactions(
            user_profile_id=args.profile_id,
            start_date=args.start_date,
            end_date=args.end_date,
            include_deleted=args.include_deleted,
        )
        for transaction in transactions:
            print(format_transaction(transaction))
    return 0


def format_transaction(transaction) -> str:
    category_id = transaction.category_id if transaction.category_id is not None else "-"
    return (
        f"{transaction.id}\t{transaction.transaction_date.isoformat()}\t"
        f"{transaction.direction.value}\t{transaction.amount_minor}\t"
        f"category={category_id}\t"
        f"status={transaction.review_status.value}\t"
        f"{transaction.description_clean or ''}"
    )


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Usa fecha ISO YYYY-MM-DD.") from exc


def parse_amount_minor(value: str) -> int:
    normalized_value = value.replace(",", ".")
    try:
        amount = Decimal(normalized_value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("Usa un importe decimal valido.") from exc
    if amount < 0:
        raise argparse.ArgumentTypeError("Usa importe no negativo y direction aparte.")
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def canonical_key_from_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    canonical_key = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name).strip("_").lower()
    if not canonical_key:
        raise ValueError("Category name must produce a canonical key.")
    return canonical_key


def enum_values(enum_class) -> list[str]:
    return [member.value for member in enum_class]


if __name__ == "__main__":
    raise SystemExit(main())
