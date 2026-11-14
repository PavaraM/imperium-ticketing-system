from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

from app.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    """Build the SQLite engine and apply pragmas to every new connection."""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def get_session(request: Request) -> Iterator[Session]:
    """Request-scoped session. Never share this between requests or background tasks."""
    with Session(request.app.state.engine) as session:
        yield session
