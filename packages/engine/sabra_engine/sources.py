"""Job boards: list, probe, add, enable and disable (`sabra sources`, Settings page).

The registry file stays the source of truth. Adding a board appends one entry to the
configured file (comments and layout untouched), re-reads it, and mirrors it into the
providers table, so the same board is visible to the next discovery run, the CLI and
the dashboard at once.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sabra_domain.errors import NotFound, ValidationFailure
from sabra_domain.schemas import SearchProfile
from sabra_providers.http import HttpClient
from sabra_providers.probe import (
    BoardLocator,
    ProbeResult,
    build_probe_http,
    fetch_board_name,
    parse_locator,
    probe_board,
)
from sabra_providers.registry_file import append_source, set_source_enabled

from .context import EngineContext


def build_probe_client(ctx: EngineContext) -> HttpClient:
    """The client a probe reads with. A seam for tests, which replace it with a canned one."""

    return build_probe_http(ctx.settings)


def source_id_for(locator: BoardLocator) -> str:
    return f"{str(locator.kind).lower()}-{locator.token}"


class SourcesService:
    def __init__(self, ctx: EngineContext, *, http: Any | None = None) -> None:
        self.ctx = ctx
        self._http = http

    # -- reading ---------------------------------------------------------

    def list_sources(self) -> list[dict[str, Any]]:
        with self.ctx.read_repos() as repos:
            rows = {(row.kind, row.token): row for row in repos.providers.list_all()}
        out: list[dict[str, Any]] = []
        for source in self.ctx.registry:
            entry = source.entry
            row = rows.get((str(entry.kind), entry.token))
            out.append(
                {
                    "id": source.id,
                    "kind": str(entry.kind),
                    "token": entry.token,
                    "display_name": entry.display_name,
                    "industry": entry.industry,
                    "enabled": entry.enabled,
                    "verified_jobs": source.verified_jobs,
                    "verified_at": source.verified_at.isoformat() if source.verified_at else None,
                    "max_items": source.max_items,
                    "notes": entry.notes,
                    "health": row.health if row else "UNKNOWN",
                    "total_discovered": row.total_discovered if row else 0,
                    "last_success_at": row.last_success_at.isoformat()
                    if row and row.last_success_at
                    else None,
                    "last_attempt_at": row.last_attempt_at.isoformat()
                    if row and row.last_attempt_at
                    else None,
                    "last_error": row.last_error if row else None,
                    "consecutive_failures": row.consecutive_failures if row else 0,
                }
            )
        return out

    def _active_profile(self) -> SearchProfile | None:
        with self.ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return None
            return repos.search_profiles.active_one(candidate.id)

    # -- probing ---------------------------------------------------------

    def _probe(
        self, locator: BoardLocator, http: Any, profile: SearchProfile | None
    ) -> ProbeResult:
        return probe_board(locator, http=http, settings=self.ctx.settings, profile=profile)

    def _client(self) -> tuple[Any, bool]:
        if self._http is not None:
            return self._http, False
        return build_probe_client(self.ctx), True

    def probe(self, url_or_token: str, kind: str | None = None) -> dict[str, Any]:
        """Detect the board and read it once. Nothing is written."""

        locator = parse_locator(url_or_token, kind)
        profile = self._active_profile()
        http, owned = self._client()
        try:
            payload = self._probe(locator, http, profile).as_dict()
        finally:
            if owned:
                http.close()
        existing = self._existing(locator)
        payload["already_in_registry"] = existing
        return payload

    def _existing(self, locator: BoardLocator) -> str | None:
        for source in self.ctx.registry:
            if (
                source.entry.kind is locator.kind
                and source.entry.token.lower() == locator.token.lower()
            ):
                return source.id
        return None

    # -- changing the registry -------------------------------------------

    def add(
        self,
        url_or_token: str,
        kind: str | None = None,
        *,
        display_name: str | None = None,
        industry: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Probe a board and, if it is live and new, append it to the registry file."""

        locator = parse_locator(url_or_token, kind)
        existing = self._existing(locator)
        if existing is not None:
            raise ValidationFailure(
                f"{locator.kind} board {locator.token!r} is already in the registry as "
                f"{existing!r}. Nothing was added."
            )
        source_id = source_id_for(locator)
        if any(source.id == source_id for source in self.ctx.registry):
            raise ValidationFailure(
                f"a source with id {source_id!r} is already in the registry. Nothing was added."
            )

        profile = self._active_profile()
        http, owned = self._client()
        try:
            result = self._probe(locator, http, profile)
            if not result.live:
                raise ValidationFailure(
                    f"the {locator.kind} board {locator.token!r} did not respond as a live job "
                    f"board ({result.error or 'no answer'}). Nothing was added."
                )
            name = (display_name or "").strip() or fetch_board_name(locator, http=http)
        finally:
            if owned:
                http.close()
        name = name or locator.token

        entry: dict[str, Any] = {
            "id": source_id,
            "kind": str(locator.kind),
            "token": locator.token,
            "display_name": name,
            "industry": (industry or "").strip() or None,
            "enabled": True,
            "verified_jobs": result.posting_count,
            "verified_at": date.today(),
            "notes": (notes or "").strip() or None,
        }
        path = self.ctx.registry_path or self.ctx.settings.source_registry_path
        append_source(path, entry, settings=self.ctx.settings)
        self.ctx.reload_registry()
        synced = self.ctx.sync_source_registry()
        return {
            "source": next(row for row in self.list_sources() if row["id"] == source_id),
            "probe": result.as_dict(),
            "registry_file": str(path),
            "sources_synced": synced,
            "warnings": (
                ["the board is live but lists no postings right now"]
                if result.posting_count == 0
                else []
            ),
        }

    def set_enabled(self, source_id: str, enabled: bool) -> dict[str, Any]:
        if not any(source.id == source_id for source in self.ctx.registry):
            raise NotFound("source", source_id)
        path = self.ctx.registry_path or self.ctx.settings.source_registry_path
        changed = set_source_enabled(path, source_id, enabled, settings=self.ctx.settings)
        self.ctx.reload_registry()
        self.ctx.sync_source_registry()
        row = next(row for row in self.list_sources() if row["id"] == source_id)
        return {"source": row, "changed": changed}
