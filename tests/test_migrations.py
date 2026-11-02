"""Versioned migrations, backup and restore — the database lifecycle contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from sabra_domain import migrations
from sabra_domain.db import Database
from sabra_domain.migrations import (
    Migration,
    MigrationError,
    backup_database,
    migrate,
    migration_status,
    restore_database,
    sqlite_file_path,
)


def _fresh(tmp_path: Path, name: str = "mig.db") -> tuple[Database, str]:
    url = f"sqlite+pysqlite:///{tmp_path / name}"
    database = Database(url)
    database.create_all()
    return database, url


def test_a_fresh_database_stamps_the_baseline(tmp_path: Path) -> None:
    database, _url = _fresh(tmp_path)
    try:
        report = migrate(database.engine)
        assert report.previous_version == 0
        assert report.current_version == 1
        assert report.applied == ("1-v1-baseline",)
        status = migration_status(database.engine)
        assert status["pending"] == []
        assert status["unknown"] == []
        assert not status["migrations_needed"]
    finally:
        database.dispose()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    database, _url = _fresh(tmp_path)
    try:
        migrate(database.engine)
        again = migrate(database.engine)
        assert not again.changed
        assert again.previous_version == 1 and again.current_version == 1
        assert migration_status(database.engine)["applied"] == ["1-v1-baseline"]
    finally:
        database.dispose()


def test_an_existing_v1_database_without_a_version_table_is_stamped(tmp_path: Path) -> None:
    # A pilot database created before migrations existed: create_all only, no
    # schema_migrations table. Migrating it records the baseline and nothing else.
    database, _url = _fresh(tmp_path)
    try:
        report = migrate(database.engine)
        assert report.applied == ("1-v1-baseline",)
        with database.engine.connect() as connection:
            rows = connection.exec_driver_sql("SELECT version, name FROM schema_migrations").all()
        assert rows == [(1, "v1-baseline")]
    finally:
        database.dispose()


def test_pending_migrations_apply_in_order(tmp_path: Path, monkeypatch) -> None:
    database, _url = _fresh(tmp_path)
    try:
        migrate(database.engine)

        def _probe(connection) -> None:
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS migration_probe (id INTEGER PRIMARY KEY)"
            )

        extra = migrations.MIGRATIONS + (
            Migration(2, "v2-probe", _probe),
            Migration(3, "v2-probe-second", _probe),
        )
        monkeypatch.setattr(migrations, "MIGRATIONS", extra)
        report = migrate(database.engine)
        assert report.previous_version == 1
        assert report.applied == ("2-v2-probe", "3-v2-probe-second")
        with database.engine.connect() as connection:
            names = connection.exec_driver_sql(
                "SELECT name FROM schema_migrations ORDER BY version"
            ).all()
            tables = connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='migration_probe'"
            ).all()
        assert [row[0] for row in names] == [
            "v1-baseline",
            "v2-probe",
            "v2-probe-second",
        ]
        assert tables, "the migration's DDL actually ran"
        # Idempotent against the new schema too.
        assert not migrate(database.engine).changed
    finally:
        database.dispose()


def test_a_failing_migration_rolls_back_and_is_reported(tmp_path: Path, monkeypatch) -> None:
    database, _url = _fresh(tmp_path)
    try:
        migrate(database.engine)

        def _boom(connection) -> None:  # noqa: ARG001 - the failure is the point
            raise RuntimeError("simulated DDL failure")

        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            migrations.MIGRATIONS + (Migration(2, "v2-broken", _boom),),
        )
        with pytest.raises(MigrationError, match="v2-broken"):
            migrate(database.engine)
        status = migration_status(database.engine)
        assert status["current_version"] == 1
        assert status["pending"] == ["2-v2-broken"]
    finally:
        database.dispose()


def test_a_recorded_name_that_differs_from_the_build_is_refused(tmp_path: Path) -> None:
    database, _url = _fresh(tmp_path)
    try:
        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations VALUES (1, 'some-future-name', 'now')"
            )
        with pytest.raises(MigrationError, match="version 1"):
            migrate(database.engine)
    finally:
        database.dispose()


def test_sqlite_file_path_recognition() -> None:
    assert sqlite_file_path("sqlite+pysqlite:////tmp/x/y.db") == Path("/tmp/x/y.db")
    assert sqlite_file_path("postgresql+psycopg://u:p@h/db") is None
    assert sqlite_file_path("sqlite+pysqlite:///:memory:") is None


def test_backup_writes_a_verified_copy(tmp_path: Path) -> None:
    database, url = _fresh(tmp_path)
    try:
        migrate(database.engine)
        backup = backup_database(url)
        assert backup.parent == tmp_path / "backups"
        assert backup.is_file() and backup.stat().st_size > 0
        # The copy opens and carries the schema, including the migration record.
        check = Database(f"sqlite+pysqlite:///{backup}")
        try:
            status = migration_status(check.engine)
            assert status["current_version"] == 1
        finally:
            check.dispose()
    finally:
        database.dispose()


def test_backup_refuses_non_sqlite_urls() -> None:
    with pytest.raises(MigrationError, match="pg_dump"):
        backup_database("postgresql+psycopg://u:p@h/db")


def test_restore_replaces_the_database_from_a_backup(tmp_path: Path) -> None:
    database, url = _fresh(tmp_path, "live.db")
    try:
        migrate(database.engine)
        backup = backup_database(url)
    finally:
        database.dispose()
    # A new, empty "live" file is replaced by the backup. schema_migrations is
    # not in the ORM metadata, so simulate a pre-migration database by dropping
    # it explicitly.
    replaced = Database(url)
    try:
        with replaced.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS schema_migrations")
    finally:
        replaced.dispose()
    restore_database(backup, url)
    check = Database(url)
    try:
        assert migration_status(check.engine)["current_version"] == 1
    finally:
        check.dispose()


def test_restore_refuses_a_missing_or_empty_backup(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="does not exist"):
        restore_database(tmp_path / "nope.db", "sqlite+pysqlite:///" + str(tmp_path / "x.db"))
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database")
    with pytest.raises(MigrationError):
        restore_database(junk, "sqlite+pysqlite:///" + str(tmp_path / "x.db"))
