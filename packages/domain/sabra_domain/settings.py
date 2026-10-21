"""Environment-driven configuration. One source of truth for runtime knobs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime settings, overridable by `SABRA_*` environment variables.

    `database_url` defaults to `<data_dir>/sabra.db`, a file-backed SQLite database,
    because the pilot must run on the owner's machine without a database service.
    PostgreSQL is the documented production target: set `SABRA_DATABASE_URL` to a
    `postgresql+psycopg://...` URL and the same schema and queries apply.
    """

    model_config = SettingsConfigDict(env_prefix="SABRA_", env_file=".env", extra="ignore")

    # Empty means "derive from data_dir" (see `model_post_init`), so the default is
    # anchored on REPO_ROOT like every other path rather than on the working
    # directory. A relative default opened a different file from each directory.
    database_url: str = ""
    # When set, résumé, answers, the database and artifacts live here instead of
    # inside the repository. Personal data must not be committed.
    private_dir: Path | None = None
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
    # How long a headed handoff waits for the person before the application is
    # moved to BLOCKED (retryable). Default 45 minutes.
    handoff_timeout_seconds: int = 2700
    automation_screenshot_dir: Path = Field(default=REPO_ROOT / "var" / "automation")

    # API.
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    api_token: str | None = None
    web_origin: str = "http://localhost:3000"

    def model_post_init(self, __context: object) -> None:
        """Root personal paths at `private_dir` when the operator sets one.

        Individual `SABRA_*` paths still win when they were changed from the
        repository defaults. The default database URL is retargeted so a private
        directory does not leave the SQLite file inside the checkout.
        """

        if self.private_dir is not None:
            root = Path(self.private_dir)
            # The pilot keeps the database and runtime files under <private>/var,
            # matching an existing checkout layout such as hallam-private\var\sabra.db.
            private_var = root / "var"
            if self.data_dir == REPO_ROOT / "var":
                self.data_dir = private_var
            if self.artifacts_dir == REPO_ROOT / "var" / "artifacts":
                self.artifacts_dir = private_var / "artifacts"
            if self.evidence_dir == REPO_ROOT / "var" / "evidence":
                self.evidence_dir = private_var / "evidence"
            if self.candidate_dir == REPO_ROOT / "config" / "candidates":
                self.candidate_dir = root / "candidates"
            if self.automation_screenshot_dir == REPO_ROOT / "var" / "automation":
                self.automation_screenshot_dir = private_var / "automation"
        if not self.database_url:
            # Absolute before it goes into a URL: a relative path would again depend on
            # the working directory. Relative values are anchored on the checkout, like
            # every other default path.
            data_dir = Path(self.data_dir)
            if not data_dir.is_absolute():
                data_dir = REPO_ROOT / data_dir
            self.data_dir = data_dir
            self.database_url = f"sqlite+pysqlite:///{(data_dir / 'sabra.db').as_posix()}"

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
