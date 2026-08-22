"""Runtime helpers for local LXCell database access."""

from pathlib import Path

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


def create_local_session_factory(
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> sessionmaker:
    """Create a session factory for the local SQLite database."""
    initialize_database(database_path)
    engine = create_sqlite_engine(sqlite_url_from_path(database_path))
    return create_session_factory(engine)


__all__ = [
    "DEFAULT_DATABASE_PATH",
    "create_local_session_factory",
    "initialize_database",
    "sqlite_url_from_path",
]
