"""Database engine / session setup."""

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


# Unbound factory; configure_database() binds it to an engine. Keeping a single
# factory object means code that imported SessionLocal always sees the current engine.
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
engine: Engine | None = None


def configure_database(url: str) -> Engine:
    """(Re)bind the session factory to a new engine. Used at import time and by tests."""
    global engine
    if engine is not None:
        engine.dispose()

    connect_args = {}
    if url.startswith("sqlite"):
        # Requests and background workers run in different threads.
        connect_args = {"check_same_thread": False, "timeout": 30}

    engine = create_engine(url, connect_args=connect_args)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL lets status reads proceed while the worker is writing.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    SessionLocal.configure(bind=engine)
    return engine


def init_db() -> None:
    from app import models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


configure_database(settings.database_url)
