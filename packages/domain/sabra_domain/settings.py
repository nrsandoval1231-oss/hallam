"""Environment-driven configuration. One source of truth for runtime knobs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime settings, overridable by `SABRA_*` environment variables.

    `database_url` defaults to a file-backed SQLite database because the pilot
    must run on the owner's machine without a database service. PostgreSQL is the
    documented production target: set `SABRA_DATABASE_URL` to a
    `postgresql+psycopg://...` URL and the same schema and queries apply.
    """

    model_config = SettingsConfigDict(env_prefix="SABRA_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+pysqlite:///./var/sabra.db"
    data_dir: Path = Field(default=REPO_ROOT / "var")
    artifacts_dir: Path = Field(default=REPO_ROOT / "var" / "artifacts")
    evidence_dir: Path = Field(default=REPO_ROOT / "var" / "evidence")
    source_registry_path: Path = Field(default=REPO_ROOT / "config" / "sources.yaml")
    candidate_dir: Path = Field(default=REPO_ROOT / "config" / "candidates")

    # Reasoning layer. Unset by default: every feature must degrade to a
    # deterministic path rather than fail when no model is configured.
    llm_provider: str = "none"
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 60.0

    # Discovery behaviour.
    discovery_timeout_seconds: float = 20.0
    discovery_max_pages: int = 5
    discovery_user_agent: str = "SabraJobEngine/0.1 (+candidate-owned job search)"

    # Automation behaviour.
    playwright_headless: bool = True
    playwright_timeout_ms: int = 30_000
    automation_dry_run: bool = True
    automation_screenshot_dir: Path = Field(default=REPO_ROOT / "var" / "automation")

    # API.
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    api_token: str | None = None
    web_origin: str = "http://localhost:3000"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.artifacts_dir,
            self.evidence_dir,
            self.automation_screenshot_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
