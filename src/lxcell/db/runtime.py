"""Runtime helpers for local LXCell database access."""

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from lxcell.db.models import Base
from lxcell.db.session import create_session_factory, create_sqlite_engine


DEFAULT_DATABASE_PATH = Path("data/lxcell.db")


def sqlite_url_from_path(database_path: Path) -> str:
    """Build a SQLite URL for a local database path."""
    return f"sqlite:///{database_path}"


def initialize_database(database_path: Path = DEFAULT_DATABASE_PATH) -> None:
    """Create the SQLite database schema if it does not exist."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(sqlite_url_from_path(database_path))
    Base.metadata.create_all(engine)
    apply_local_schema_updates(engine)


def apply_local_schema_updates(engine: Engine) -> None:
    """Apply small local SQLite schema updates until migrations are introduced."""
    inspector = inspect(engine)
    if "user_profiles" not in inspector.get_table_names():
        return

    user_profile_columns = {
        column["name"] for column in inspector.get_columns("user_profiles")
    }
    if "transactions_locked_until" not in user_profile_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE user_profiles ADD COLUMN transactions_locked_until DATE")
            )


def create_local_session_factory(
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> sessionmaker:
    """Create a session factory for the local SQLite database."""
    initialize_database(database_path)
    engine = create_sqlite_engine(sqlite_url_from_path(database_path))
    return create_session_factory(engine)


__all__ = [
    "DEFAULT_DATABASE_PATH",
    "apply_local_schema_updates",
    "create_local_session_factory",
    "initialize_database",
    "sqlite_url_from_path",
]
