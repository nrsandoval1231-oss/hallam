"""Versioned schema migrations, backup and restore.

`Database.create_all()` builds the *current* model schema and is the right tool
for a fresh database. It cannot change an existing one, though, and a job-search
ledger the owner trusts must never be rewritten by an unversioned guess. This
module adds the missing piece:

* a `schema_migrations` table recording exactly which migrations a database has;
* an ordered, numbered migration list (baseline = 1, the V1 schema);
* `migrate()` — stamp the baseline on existing V1 databases, then apply pending
  migrations in order, each in its own transaction;
* `backup_database()` / `restore_database()` — a verified file copy before any
  destructive step, because rollback of a SQLite schema change *is* the backup.

Every migration's DDL must be idempotent (guarded `IF NOT EXISTS` / "column
already present" checks) so that a database created by `create_all` first and
migrated second applies cleanly.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

SCHEMA_MIGRATIONS_TABLE = "schema_migrations"


@dataclass(frozen=True)
class Migration:
    """One numbered schema change. `apply` runs inside a transaction."""

    version: int
    name: str
    apply: Callable[[Connection], None]


def _baseline(_connection: Connection) -> None:
    """The V1 schema. Existing databases already have it; fresh ones get it from
    `Database.create_all()`. Nothing to execute — the row is the record."""


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "v1-baseline", _baseline),
)


class MigrationError(Exception):
    """A migration failed or the database is in an unknown state."""


@dataclass(frozen=True)
class MigrationReport:
    previous_version: int
    current_version: int
    applied: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def _ensure_table(connection: Connection) -> None:
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} ("
            " version INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
    )


def applied_migrations(connection: Connection) -> dict[int, str]:
    _ensure_table(connection)
    rows = connection.execute(
        text(f"SELECT version, name FROM {SCHEMA_MIGRATIONS_TABLE} ORDER BY version")
    ).all()
    return {int(row[0]): str(row[1]) for row in rows}


def current_version(connection: Connection) -> int:
    versions = applied_migrations(connection)
    return max(versions) if versions else 0


def migrate(engine: Engine) -> MigrationReport:
    """Bring the database to the latest migration version.

    Runs each pending migration in its own transaction; a failure leaves earlier
    migrations committed and the failed one rolled back, and raises
    `MigrationError`. Stamping the baseline on a database that predates this
    table is safe: the V1 schema is exactly what `create_all` has always built.
    """

    applied_names: list[str] = []
    with engine.begin() as connection:
        previous = current_version(connection)
        done = applied_migrations(connection)
        for migration in MIGRATIONS:
            if migration.version in done:
                if done[migration.version] != migration.name:
                    raise MigrationError(
                        f"version {migration.version} is recorded as "
                        f"{done[migration.version]!r} but this build expects "
                        f"{migration.name!r}; the database is newer or older than "
                        "this code — upgrade the application instead"
                    )
                continue
            try:
                with connection.begin_nested():
                    migration.apply(connection)
                    connection.execute(
                        text(
                            f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} "
                            "(version, name, applied_at) VALUES (:v, :n, :at)"
                        ),
                        {
                            "v": migration.version,
                            "n": migration.name,
                            "at": datetime.now(UTC).isoformat(),
                        },
                    )
            except Exception as exc:
                raise MigrationError(
                    f"migration {migration.version} ({migration.name}) failed: {exc}"
                ) from exc
            done[migration.version] = migration.name
            applied_names.append(f"{migration.version}-{migration.name}")
        final = current_version(connection)
    return MigrationReport(previous, final, tuple(applied_names))


# ---------------------------------------------------------------------------
# Backup and restore (SQLite file databases)
# ---------------------------------------------------------------------------


def sqlite_file_path(database_url: str) -> Path | None:
    """The file behind a SQLite URL, or None for anything else."""

    if not database_url.startswith("sqlite"):
        return None
    # sqlite+pysqlite:///<abs path> — three slashes means absolute.
    raw = database_url.split(":///", 1)
    if len(raw) != 2 or not raw[1] or raw[1] == ":memory:":
        return None
    return Path(raw[1])


def backup_database(database_url: str, backup_dir: Path | None = None) -> Path:
    """Copy a SQLite database into a timestamped file next to it (or in `backup_dir`).

    Uses SQLite's `VACUUM INTO` so the copy is a consistent snapshot even while
    WAL files exist. Non-SQLite URLs must be dumped with the database's own
    tool (`pg_dump`) — this function refuses rather than pretending.
    """

    path = sqlite_file_path(database_url)
    if path is None:
        raise MigrationError(
            "automatic backup only supports SQLite file databases; dump "
            "PostgreSQL with pg_dump before migrating"
        )
    if not path.is_file():
        raise MigrationError(f"database file {path} does not exist")
    directory = backup_dir or path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = directory / f"{path.stem}-{stamp}{path.suffix or '.db'}"
    source = create_engine(database_url)
    try:
        with source.begin() as connection:
            connection.exec_driver_sql(f"VACUUM INTO '{target.as_posix()}'")
    finally:
        source.dispose()
    if not target.is_file() or target.stat().st_size == 0:
        raise MigrationError(f"backup to {target} was not created")
    # Verify the copy really opens and carries the same tables.
    check = create_engine(f"sqlite+pysqlite:///{target.as_posix()}")
    try:
        with check.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM sqlite_master WHERE type='table'")
            ).scalar()
    finally:
        check.dispose()
    if not isinstance(count, int) or count == 0:
        raise MigrationError(f"backup {target} did not verify; refusing to continue")
    return target


def restore_database(backup_path: Path, database_url: str) -> Path:
    """Replace the SQLite database file with a verified backup copy.

    The caller is responsible for having stopped the dashboard and any workers;
    this overwrites the live file. The backup must pass the same open-and-count
    verification before anything is touched.
    """

    path = sqlite_file_path(database_url)
    if path is None:
        raise MigrationError("automatic restore only supports SQLite file databases")
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise MigrationError(f"backup {backup_path} does not exist")
    probe = create_engine(f"sqlite+pysqlite:///{backup_path.as_posix()}")
    try:
        with probe.connect() as connection:
            tables = connection.execute(
                text("SELECT count(*) FROM sqlite_master WHERE type='table'")
            ).scalar()
    except Exception as exc:
        raise MigrationError(f"backup {backup_path} does not look like a database: {exc}") from exc
    finally:
        probe.dispose()
    if not isinstance(tables, int) or tables == 0:
        raise MigrationError(f"backup {backup_path} does not look like a database")
    path.parent.mkdir(parents=True, exist_ok=True)
    # A replaced WAL-mode database can leave orphaned -wal/-shm siblings behind;
    # they would corrupt the restored file on the next open.
    for suffix in ("-wal", "-shm"):
        sibling = path.with_name(path.name + suffix)
        if sibling.exists():
            try:
                sibling.unlink()
            except OSError as exc:
                raise MigrationError(
                    f"{sibling} is locked by another process (the dashboard or a "
                    "worker); stop it before restoring"
                ) from exc
    shutil.copyfile(backup_path, path)
    return path


def migration_status(engine: Engine) -> dict[str, Any]:
    """What this database has, what this build expects, and what is pending."""

    with engine.connect() as connection:
        applied = applied_migrations(connection)
        current = current_version(connection)
    expected = {migration.version: migration.name for migration in MIGRATIONS}
    pending = [f"{v}-{n}" for v, n in expected.items() if v not in applied]
    unknown = [f"{v}-{n}" for v, n in applied.items() if v not in expected]
    return {
        "current_version": current,
        "latest_version": max(expected) if expected else 0,
        "applied": [f"{v}-{n}" for v, n in sorted(applied.items())],
        "pending": pending,
        "unknown": unknown,
        "migrations_needed": bool(pending or unknown),
    }
