"""Shared test fixtures.

Every test gets a fresh file-backed SQLite database. A file (not `:memory:`) is
deliberate: in-memory SQLite gives each connection its own database, so a test
that opens a second session silently sees nothing. WAL and `foreign_keys=ON` then
match what the pilot runtime enforces.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sabra_domain.db import Database
from sabra_domain.settings import Settings

# Fixtures that build a real prepared application live in their own module so this
# file stays about the database. `pytest_plugins` is the supported way to load it.
pytest_plugins = ["conftest_app"]


@pytest.fixture(autouse=True)
def isolate_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every settings lookup at this test's temporary database.

    API routes call `EngineContext.create()` with no arguments, which reads
    `get_settings()`. Without this override that opens `./var/sabra.db`, the
    operator's real database. Tests must not be able to reach it.
    """

    from sabra_domain.settings import reset_settings_cache

    database = tmp_path / "isolated.db"
    data = tmp_path / "isolated-data"
    for path in (data, data / "artifacts", data / "evidence", data / "automation", data / "candidates"):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SABRA_DATABASE_URL", f"sqlite+pysqlite:///{database}")
    monkeypatch.setenv("SABRA_DATA_DIR", str(data))
    monkeypatch.setenv("SABRA_ARTIFACTS_DIR", str(data / "artifacts"))
    monkeypatch.setenv("SABRA_EVIDENCE_DIR", str(data / "evidence"))
    monkeypatch.setenv("SABRA_AUTOMATION_SCREENSHOT_DIR", str(data / "automation"))
    monkeypatch.setenv("SABRA_CANDIDATE_DIR", str(data / "candidates"))
    monkeypatch.setenv("SABRA_AUTOMATION_DRY_RUN", "true")
    monkeypatch.delenv("SABRA_PRIVATE_DIR", raising=False)
    reset_settings_cache()
    yield database
    reset_settings_cache()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_dir=tmp_path / "evidence",
        automation_screenshot_dir=tmp_path / "automation",
        source_registry_path=tmp_path / "sources.yaml",
        candidate_dir=tmp_path / "candidates",
    )
    # A valid, minimal registry so `EngineContext.create` works without reaching
    # the real `config/sources.yaml`. It is disabled, so no test accidentally
    # performs a live discovery run.
    settings.source_registry_path.write_text(
        "version: 1\nsources:\n"
        "  - id: test-disabled\n"
        "    kind: GREENHOUSE\n"
        "    token: test\n"
        "    display_name: Test Source\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    return settings


@pytest.fixture()
def db(settings: Settings) -> Iterator[Database]:
    settings.ensure_dirs()
    database = Database(settings.database_url)
    database.create_all()
    try:
        yield database
    finally:
        database.dispose()
