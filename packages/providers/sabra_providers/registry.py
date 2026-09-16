"""Source registry (requirement 7).

The set of discovery sources is configuration, not code: adding a board must not
require a release. Each entry records what was verified live and when, so a board
that stops publishing is visible as a stale verification rather than as an
unexplained drop in results.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from sabra_domain.enums import SourceKind
from sabra_domain.errors import ConfigurationError
from sabra_domain.schemas import SourceRegistryEntry
from sabra_domain.settings import Settings, get_settings

from .base import Provider
from .direct_careers import DirectCareersProvider
from .greenhouse import GreenhouseProvider
from .http import HttpClient
from .lever import LeverProvider

PROVIDER_TYPES: dict[SourceKind, type[Provider]] = {
    SourceKind.GREENHOUSE: GreenhouseProvider,
    SourceKind.LEVER: LeverProvider,
}


@dataclass(slots=True)
class SourceDefinition:
    """One configured source, with its verification provenance."""

    entry: SourceRegistryEntry
    verified_jobs: int | None = None
    verified_at: date | None = None
    careers_url: str | None = None
    priority: int = 100

    @property
    def id(self) -> str:
        return self.entry.id


class SourceRegistry:
    """Loads, validates and instantiates discovery sources."""

    def __init__(
        self,
        sources: list[SourceDefinition],
        *,
        version: int = 1,
        notes: str | None = None,
    ) -> None:
        self.sources = sources
        self.version = version
        self.notes = notes

    def __len__(self) -> int:
        return len(self.sources)

    def __iter__(self):
        return iter(self.sources)

    @property
    def enabled(self) -> list[SourceDefinition]:
        return [s for s in self.sources if s.entry.enabled]

    def by_id(self, source_id: str) -> SourceDefinition:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise ConfigurationError(f"unknown source id: {source_id!r}")

    def kinds(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for source in self.sources:
            counts[str(source.entry.kind)] = counts.get(str(source.entry.kind), 0) + 1
        return counts

    def build_provider(
        self,
        source_id: str,
        *,
        http: HttpClient | None = None,
        settings: Settings | None = None,
        max_items: int | None = None,
    ) -> Provider:
        """Instantiate the provider for one source."""

        definition = self.by_id(source_id)
        entry = definition.entry
        settings = settings or get_settings()
        common: dict[str, Any] = {
            "display_name": entry.display_name,
            "company_domain": _domain_from_locator(entry.locator),
            "http": http,
            "settings": settings,
            "max_items": max_items,
        }
        if entry.kind is SourceKind.GREENHOUSE:
            return GreenhouseProvider(entry.token, **common)
        if entry.kind is SourceKind.LEVER:
            return LeverProvider(entry.token, **common)
        if entry.kind is SourceKind.DIRECT_CAREERS:
            if not definition.careers_url:
                raise ConfigurationError(
                    f"source {source_id!r} is DIRECT_CAREERS but declares no careers_url"
                )
            return DirectCareersProvider(entry.token, careers_url=definition.careers_url, **common)
        raise ConfigurationError(f"no provider implementation for kind {entry.kind!r}")

    def build_providers(
        self,
        *,
        http: HttpClient | None = None,
        settings: Settings | None = None,
        max_items: int | None = None,
        only: list[str] | None = None,
    ) -> list[Provider]:
        providers = []
        for source in self.enabled:
            if only and source.id not in only:
                continue
            providers.append(
                self.build_provider(
                    source.id, http=http, settings=settings, max_items=max_items
                )
            )
        return providers


def _domain_from_locator(locator: str | None) -> str | None:
    """A registry entry may name the employer's own domain, e.g. `revance.com`."""

    if not locator:
        return None
    cleaned = locator.strip()
    if "://" in cleaned:
        return None
    if "." in cleaned and " " not in cleaned:
        return cleaned
    return None


def load_registry(path: Path | None = None, settings: Settings | None = None) -> SourceRegistry:
    settings = settings or get_settings()
    path = path or settings.source_registry_path
    if not Path(path).exists():
        raise ConfigurationError(f"source registry not found: {path}")

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = raw.get("sources")
    if not isinstance(entries, list):
        raise ConfigurationError(f"{path} has no `sources` list")

    definitions: list[SourceDefinition] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ConfigurationError(f"{path}: sources[{index}] is not a mapping")
        missing = {"id", "kind", "token", "display_name"} - set(item)
        if missing:
            raise ConfigurationError(
                f"{path}: sources[{index}] missing required keys {sorted(missing)}"
            )
        source_id = str(item["id"])
        if source_id in seen:
            raise ConfigurationError(f"{path}: duplicate source id {source_id!r}")
        seen.add(source_id)
        try:
            kind = SourceKind(str(item["kind"]).upper())
        except ValueError as exc:
            raise ConfigurationError(
                f"{path}: source {source_id!r} has unknown kind {item['kind']!r}"
            ) from exc

        entry = SourceRegistryEntry(
            id=source_id,
            kind=kind,
            token=str(item["token"]),
            display_name=str(item["display_name"]),
            industry=item.get("industry"),
            enabled=bool(item.get("enabled", True)),
            locator=item.get("domain") or item.get("locator"),
            notes=item.get("notes"),
            discovered_job_count=item.get("verified_jobs"),
        )
        verified_at = item.get("verified_at")
        if isinstance(verified_at, str):
            try:
                verified_at = date.fromisoformat(verified_at)
            except ValueError:
                verified_at = None
        elif not isinstance(verified_at, date):
            verified_at = None

        definitions.append(
            SourceDefinition(
                entry=entry,
                verified_jobs=item.get("verified_jobs"),
                verified_at=verified_at,
                careers_url=item.get("careers_url") or item.get("url"),
                priority=int(item.get("priority", 100)),
            )
        )

    return SourceRegistry(
        definitions,
        version=int(raw.get("version", 1)),
        notes=raw.get("notes"),
    )
