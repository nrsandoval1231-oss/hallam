"""Engine and session management.

One place builds engines so tests, workers and the API all get the same
configured behaviour (SQLite pragmas, SQLite/PG portability, disposal).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .settings import get_settings


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """Enable foreign keys and WAL.

    SQLite ignores foreign keys unless asked, and a silently unenforced FK on the
    ledger is exactly the kind of quiet inconsistency this product cannot afford.
    """

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    kwargs: dict[str, Any] = {"future": True, "echo": echo, "pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(database_url, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _configure_sqlite)
    return engine


class Database:
    """Owns one engine and hands out transactional sessions."""

    def __init__(self, database_url: str | None = None, *, echo: bool = False) -> None:
        self.url = database_url or get_settings().database_url
        self.engine = build_engine(self.url, echo=echo)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    def create_all(self) -> None:
        from .models import Base

        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        from .models import Base

        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional scope. Rolls back on any exception."""

        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def read_session(self) -> Iterator[Session]:
        """Read-only scope. Same session semantics, no commit."""

        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
