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

SQLITE_BUSY_TIMEOUT_MS = 30_000


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """Enable foreign keys and WAL.

    SQLite ignores foreign keys unless asked, and a silently unenforced FK on the
    ledger is exactly the kind of quiet inconsistency this product cannot afford.
    """

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # Another Sabra process (the dashboard, a CLI run) may hold the write lock. Wait
    # for it rather than failing instantly with "database is locked".
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    kwargs: dict[str, Any] = {"future": True, "echo": echo, "pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_MS // 1000,
        }
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
        # `create_all` skips a table that already exists, indexes included. A database
        # that got the `runs` table before its one-active-run index existed gets it here.
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_one_active ON runs ((1)) "
                "WHERE status IN ('QUEUED', 'RUNNING')"
            )

    def drop_all(self) -> None:
        from .models import Base

        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self, *, immediate: bool = True) -> Iterator[Session]:
        """Transactional scope. Rolls back on any exception.

        On SQLite the scope opens with `BEGIN IMMEDIATE`, taking the write lock up front.
        Without it a scope that reads and then writes takes the lock at its first write,
        so two such scopes (two browser tabs, the dashboard and a CLI run) can both read
        the same row and the second write silently overwrites the first. With it they
        queue: the second waits (up to the busy timeout) and then reads the committed row.
        The cost is that the lock is held for the whole scope, so keep a scope short and do
        slow work (a model call, a network fetch) outside it. `immediate=False` opts out.
        """

        session = self._session_factory()
        try:
            if immediate and self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
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
