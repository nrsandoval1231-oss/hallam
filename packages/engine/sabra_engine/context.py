"""Engine context: the wiring a service needs, built once.

Services depend on this rather than constructing engines, sessions or providers
themselves, so tests can swap any collaborator and so there is exactly one place
that decides how the database and the source registry are reached.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from sabra_domain.db import Database
from sabra_domain.settings import Settings, get_settings
from sabra_llm.base import LLMProvider, get_provider
from sabra_providers.registry import SourceRegistry, load_registry
from sqlalchemy.orm import Session


@dataclass
class Repos:
    """All repositories bound to one session."""

    session: Session

    @cached_property
    def events(self) -> Any:
        from sabra_domain.repositories import EventRepository

        return EventRepository(self.session)

    @cached_property
    def candidates(self) -> Any:
        from sabra_domain.repositories import CandidateRepository

        return CandidateRepository(self.session)

    @cached_property
    def resumes(self) -> Any:
        from sabra_domain.repositories import ResumeRepository

        return ResumeRepository(self.session)

    @cached_property
    def search_profiles(self) -> Any:
        from sabra_domain.repositories import SearchProfileRepository

        return SearchProfileRepository(self.session)

    @cached_property
    def companies(self) -> Any:
        from sabra_domain.repositories import CompanyRepository

        return CompanyRepository(self.session)

    @cached_property
    def jobs(self) -> Any:
        from sabra_domain.repositories import JobRepository

        return JobRepository(self.session)

    @cached_property
    def opportunities(self) -> Any:
        from sabra_domain.repositories import OpportunityRepository

        return OpportunityRepository(self.session)

    @cached_property
    def fit(self) -> Any:
        from sabra_domain.repositories import FitAssessmentRepository

        return FitAssessmentRepository(self.session)

    @cached_property
    def research(self) -> Any:
        from sabra_domain.repositories import ResearchRepository

        return ResearchRepository(self.session)

    @cached_property
    def packages(self) -> Any:
        from sabra_domain.repositories import PackageRepository

        return PackageRepository(self.session)

    @cached_property
    def applications(self) -> Any:
        from sabra_domain.repositories import ApplicationRepository

        return ApplicationRepository(self.session)

    @cached_property
    def outcomes(self) -> Any:
        from sabra_domain.repositories import OutcomeRepository

        return OutcomeRepository(self.session)

    @cached_property
    def providers(self) -> Any:
        from sabra_domain.repositories import ProviderRepository

        return ProviderRepository(self.session)

    @cached_property
    def runs(self) -> Any:
        from sabra_domain.repositories import DiscoveryRunRepository

        return DiscoveryRunRepository(self.session)

    @cached_property
    def model_calls(self) -> Any:
        from sabra_domain.repositories import ModelCallRepository

        return ModelCallRepository(self.session)


@dataclass
class EngineContext:
    """Collaborators shared by every service in one engine process."""

    settings: Settings
    db: Database
    registry: SourceRegistry
    llm: LLMProvider

    @classmethod
    def create(
        cls,
        *,
        settings: Settings | None = None,
        database_url: str | None = None,
        registry_path: str | None = None,
        llm: LLMProvider | None = None,
        create_schema: bool = True,
        echo: bool = False,
    ) -> EngineContext:
        settings = settings or get_settings()
        settings.ensure_dirs()
        if database_url:
            settings = settings.model_copy(update={"database_url": database_url})
        db = Database(settings.database_url, echo=echo)
        if create_schema:
            db.create_all()
        from pathlib import Path

        registry = load_registry(Path(registry_path) if registry_path else None, settings)
        return cls(
            settings=settings,
            db=db,
            registry=registry,
            llm=llm if llm is not None else get_provider(settings),
        )

    @contextmanager
    def repos(self) -> Iterator[Repos]:
        """Transactional scope.

        Do not nest one of these inside another on the same context: the inner scope
        opens a second connection, and on SQLite that takes a second write lock which
        the database refuses ("database is locked"). Call a service that manages its
        own session *outside* any scope you are holding, as `tests/conftest_app.py`
        does when it builds a prepared application.
        """

        with self.db.session() as session:
            yield Repos(session)

    @contextmanager
    def read_repos(self) -> Iterator[Repos]:
        with self.db.read_session() as session:
            yield Repos(session)

    def sync_source_registry(self) -> int:
        """Mirror the YAML registry into the database so health is trackable."""

        count = 0
        with self.repos() as repos:
            for source in self.registry:
                repos.providers.upsert(
                    kind=str(source.entry.kind),
                    token=source.entry.token,
                    display_name=source.entry.display_name,
                    industry=source.entry.industry,
                    locator=source.entry.locator,
                    enabled=source.entry.enabled,
                    notes=source.entry.notes,
                )
                count += 1
        return count

    def close(self) -> None:
        closer = getattr(self.llm, "close", None)
        if callable(closer):
            closer()
        self.db.dispose()
