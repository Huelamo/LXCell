import pytest
from sqlalchemy import inspect, text

from lxcell.db.runtime import apply_local_schema_updates
from lxcell.db.session import create_session_factory, create_sqlite_engine, session_scope


def test_session_scope_commits_successful_work(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    session_factory = create_session_factory(engine)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample_records (name TEXT NOT NULL)"))

    with session_scope(session_factory) as session:
        session.execute(
            text("INSERT INTO sample_records (name) VALUES (:name)"),
            {"name": "Sample record"},
        )

    with engine.connect() as connection:
        result = connection.execute(text("SELECT name FROM sample_records"))
        assert result.scalar_one() == "Sample record"


def test_session_scope_rolls_back_failed_work(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    session_factory = create_session_factory(engine)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample_records (name TEXT NOT NULL)"))

    with pytest.raises(RuntimeError):
        with session_scope(session_factory) as session:
            session.execute(
                text("INSERT INTO sample_records (name) VALUES (:name)"),
                {"name": "Sample record"},
            )
            raise RuntimeError("Force rollback")

    with engine.connect() as connection:
        result = connection.execute(text("SELECT COUNT(*) FROM sample_records"))
        assert result.scalar_one() == 0


def test_apply_local_schema_updates_adds_profile_transaction_lock(tmp_path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'lxcell.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE user_profiles ("
                "id INTEGER PRIMARY KEY, "
                "display_name TEXT NOT NULL"
                ")"
            )
        )

    apply_local_schema_updates(engine)

    column_names = {
        column["name"] for column in inspect(engine).get_columns("user_profiles")
    }
    assert "transactions_locked_until" in column_names
