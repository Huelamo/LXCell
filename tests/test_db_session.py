import pytest
from sqlalchemy import text

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
