from sqlalchemy import select

from lxcell.cli import main
from lxcell.db.models import Base, ImportBatch, ImportedTransactionSource
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope
from lxcell.enums.core_enums import ImportSourceSystem, TransactionReviewStatus
from lxcell.repositories import AccountingRepository


def test_cli_initializes_database_and_records_confirmed_transaction(tmp_path, capsys):
    database_path = tmp_path / "lxcell.db"

    assert main(["--database", str(database_path), "init-db"]) == 0
    assert database_path.exists()

    assert (
        main(
            [
                "--database",
                str(database_path),
                "add-profile",
                "--display-name",
                "Sample User",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--database",
                str(database_path),
                "add-account",
                "--profile-id",
                "1",
                "--name",
                "Primary account",
                "--type",
                "checking",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--database",
                str(database_path),
                "add-category",
                "--profile-id",
                "1",
                "--name",
                "Category A",
                "--type",
                "expense",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--database",
                str(database_path),
                "add-transaction",
                "--profile-id",
                "1",
                "--account-id",
                "1",
                "--category-id",
                "1",
                "--date",
                "2026-01-10",
                "--description",
                "Merchant A",
                "--amount",
                "12,34",
                "--direction",
                "outflow",
                "--type",
                "expense",
                "--payment-method",
                "card",
                "--decided-by",
                "Sample User",
            ]
        )
        == 0
    )

    session_factory = _session_factory(database_path)
    with session_scope(session_factory) as session:
        repository = AccountingRepository(session)
        transaction = repository.get_transaction(transaction_id=1, user_profile_id=1)
        import_batch = session.scalar(select(ImportBatch))
        imported_source = session.scalar(select(ImportedTransactionSource))

    assert transaction is not None
    assert transaction.amount_minor == 1234
    assert transaction.review_status == TransactionReviewStatus.USER_CONFIRMED
    assert import_batch.source_system == ImportSourceSystem.MANUAL_ENTRY
    assert imported_source.created_transaction_id == transaction.id

    output = capsys.readouterr().out
    assert "Transaccion registrada: id=1 estado=user_confirmed" in output


def test_cli_records_uncategorized_transaction_as_confirmed_and_lists_it(
    tmp_path, capsys
):
    database_path = tmp_path / "lxcell.db"

    main(["--database", str(database_path), "init-db"])
    main(
        [
            "--database",
            str(database_path),
            "add-profile",
            "--display-name",
            "Sample User",
        ]
    )
    main(
        [
            "--database",
            str(database_path),
            "add-account",
            "--profile-id",
            "1",
            "--name",
            "Primary account",
            "--type",
            "checking",
        ]
    )
    main(
        [
            "--database",
            str(database_path),
            "add-transaction",
            "--profile-id",
            "1",
            "--account-id",
            "1",
            "--date",
            "2026-01-10",
            "--description",
            "Merchant A",
            "--amount",
            "10.00",
            "--direction",
            "outflow",
            "--type",
            "expense",
        ]
    )
    main(
        [
            "--database",
            str(database_path),
            "list-transactions",
            "--profile-id",
            "1",
        ]
    )

    session_factory = _session_factory(database_path)
    with session_scope(session_factory) as session:
        transaction = AccountingRepository(session).get_transaction(
            transaction_id=1,
            user_profile_id=1,
        )

    assert transaction.review_status == TransactionReviewStatus.USER_CONFIRMED
    output = capsys.readouterr().out
    assert "status=user_confirmed" in output
    assert "Merchant A" in output


def _session_factory(database_path):
    engine = create_sqlite_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)
