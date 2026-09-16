"""Discovery service (requirements 7, 9, 38).

Runs every enabled source, normalizes what comes back, persists it idempotently
and collapses duplicate postings into canonical opportunities.

Failure isolation is the core property: a source that times out, 500s or returns
garbage is recorded against that source and the run continues with the rest. A
failed discovery source must never corrupt the existing opportunity queue, so
persistence happens per source and nothing is deleted on failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from sabra_domain.enums import (
    DiscoveryRunStatus,
    EntityType,
    EventType,
    JobStatus,
    SourceHealth,
)
from sabra_domain.errors import ProviderError
from sabra_domain.schemas import SearchProfile
from sabra_providers.base import Provider
from sabra_providers.dedupe import JobKey, group_records
from sabra_providers.http import HttpClient
from sabra_providers.registry import SourceDefinition

from .context import EngineContext, Repos

# Job states eligible for opportunity (re)grouping. A job already rejected on
# merit is deliberately excluded: re-running discovery must not resurrect a
# decision the candidate's search profile already made.
GROUPABLE_STATUSES = (
    JobStatus.DISCOVERED,
    JobStatus.NORMALIZED,
    JobStatus.QUALIFIED,
    JobStatus.SHORTLISTED,
    JobStatus.RESEARCHED,
    JobStatus.PREPARED,
    JobStatus.AWAITING_APPROVAL,
    JobStatus.APPROVED,
)


@dataclass(slots=True)
class SourceOutcome:
    source_id: str
    kind: str
    token: str
    display_name: str
    ok: bool
    jobs_seen: int = 0
    jobs_new: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    http_requests: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    total_reported: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "token": self.token,
            "display_name": self.display_name,
            "ok": self.ok,
            "jobs_seen": self.jobs_seen,
            "jobs_new": self.jobs_new,
            "jobs_updated": self.jobs_updated,
            "jobs_unchanged": self.jobs_unchanged,
            "http_requests": self.http_requests,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "warnings": self.warnings[:20],
            "truncated": self.truncated,
            "total_reported": self.total_reported,
        }


@dataclass(slots=True)
class DiscoveryRunResult:
    run_id: str
    status: DiscoveryRunStatus
    started_at: datetime
    finished_at: datetime
    sources: list[SourceOutcome] = field(default_factory=list)
    jobs_seen: int = 0
    jobs_new: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    postings_ingested: int = 0
    canonical_opportunities: int = 0
    duplicates_removed: int = 0
    multi_source_opportunities: int = 0
    cross_source_pairs: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def sources_ok(self) -> int:
        return sum(1 for s in self.sources if s.ok)

    @property
    def sources_failed(self) -> int:
        return sum(1 for s in self.sources if not s.ok)

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": str(self.status),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "elapsed_seconds": round((self.finished_at - self.started_at).total_seconds(), 2),
            "sources_total": len(self.sources),
            "sources_ok": self.sources_ok,
            "sources_failed": self.sources_failed,
            "jobs_seen": self.jobs_seen,
            "jobs_new": self.jobs_new,
            "jobs_updated": self.jobs_updated,
            "jobs_unchanged": self.jobs_unchanged,
            "postings_ingested": self.postings_ingested,
            "canonical_opportunities": self.canonical_opportunities,
            "duplicates_removed": self.duplicates_removed,
            "multi_source_opportunities": self.multi_source_opportunities,
            "cross_source_pairs": self.cross_source_pairs,
            "errors": self.errors,
        }


class DiscoveryService:
    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx

    # -- public API -----------------------------------------------------

    def run(
        self,
        *,
        candidate_id: str,
        search_profile_id: str | None = None,
        source_ids: list[str] | None = None,
        max_items_per_source: int | None = None,
        http: HttpClient | None = None,
        group: bool = True,
    ) -> DiscoveryRunResult:
        profile_id = search_profile_id or ""
        started_at = datetime.now(UTC)
        definitions = self._select_sources(source_ids)

        with self.ctx.repos() as repos:
            repos.events.append(
                EntityType.SYSTEM,
                "discovery",
                EventType.DISCOVERY_RUN_STARTED,
                {"candidate_id": candidate_id, "search_profile_id": profile_id,
                 "sources": [d.id for d in definitions]},
            )
            run = repos.runs.start(
                candidate_id=candidate_id,
                search_profile_id=profile_id,
                sources_total=len(definitions),
            )
            run_id = run.id

        search_profile = self._load_search_profile(candidate_id, profile_id)
        outcomes: list[SourceOutcome] = []
        owns_http = http is None
        client = http or HttpClient(
            timeout=self.ctx.settings.discovery_timeout_seconds,
            user_agent=self.ctx.settings.discovery_user_agent,
        )

        try:
            for definition in definitions:
                outcome = self._run_source(
                    definition,
                    client=client,
                    candidate_id=candidate_id,
                    max_items=max_items_per_source,
                )
                outcomes.append(outcome)
                self._record_source_result(definition, outcome)
        finally:
            if owns_http:
                client.close()

        result = DiscoveryRunResult(
            run_id=run_id,
            status=DiscoveryRunStatus.RUNNING,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            sources=outcomes,
        )
        result.jobs_seen = sum(o.jobs_seen for o in outcomes)
        result.jobs_new = sum(o.jobs_new for o in outcomes)
        result.jobs_updated = sum(o.jobs_updated for o in outcomes)
        result.jobs_unchanged = sum(o.jobs_unchanged for o in outcomes)
        result.errors = [f"{o.source_id}: {o.error}" for o in outcomes if o.error]

        if group:
            self._group_opportunities(candidate_id, profile_id, result)

        result.finished_at = datetime.now(UTC)
        if not outcomes:
            result.status = DiscoveryRunStatus.FAILED
        elif result.sources_failed == 0:
            result.status = DiscoveryRunStatus.COMPLETED
        elif result.sources_ok == 0:
            result.status = DiscoveryRunStatus.FAILED
        else:
            result.status = DiscoveryRunStatus.PARTIAL

        with self.ctx.repos() as repos:
            repos.runs.finish(
                run_id,
                status=str(result.status),
                sources_ok=result.sources_ok,
                sources_failed=result.sources_failed,
                jobs_seen=result.jobs_seen,
                jobs_new=result.jobs_new,
                jobs_updated=result.jobs_updated,
                jobs_unchanged=result.jobs_unchanged,
                per_source={o.source_id: o.as_dict() for o in outcomes},
                errors=result.errors,
            )
            if result.status is not DiscoveryRunStatus.FAILED:
                repos.events.append(
                    EntityType.SYSTEM,
                    run_id,
                    EventType.DISCOVERY_SOURCE_COMPLETED
                    if result.status is DiscoveryRunStatus.COMPLETED
                    else EventType.DISCOVERY_RUN_STARTED,
                    result.summary(),
                )
        return result

    # -- internals ------------------------------------------------------

    def _select_sources(self, source_ids: list[str] | None) -> list[SourceDefinition]:
        definitions = list(self.ctx.registry.enabled)
        if source_ids:
            wanted = set(source_ids)
            unknown = wanted - {d.id for d in self.ctx.registry}
            if unknown:
                raise ProviderError("registry", f"unknown source ids: {sorted(unknown)}")
            definitions = [d for d in definitions if d.id in wanted]
        return definitions

    def _load_search_profile(self, candidate_id: str, profile_id: str) -> SearchProfile | None:
        if not profile_id:
            return None
        with self.ctx.read_repos() as repos:
            return repos.search_profiles.get(profile_id)

    def _run_source(
        self,
        definition: SourceDefinition,
        *,
        client: HttpClient,
        candidate_id: str,
        max_items: int | None,
    ) -> SourceOutcome:
        outcome = SourceOutcome(
            source_id=definition.id,
            kind=str(definition.entry.kind),
            token=definition.entry.token,
            display_name=definition.entry.display_name,
            ok=False,
        )
        started = time.perf_counter()
        provider: Provider | None = None
        before = len(client.calls)
        try:
            provider = self.ctx.registry.build_provider(
                definition.id, http=client, settings=self.ctx.settings, max_items=max_items
            )
            batch = provider.discover()
            outcome.total_reported = batch.total_reported
            outcome.truncated = batch.truncated
            outcome.warnings.extend(batch.warnings)
            outcome.jobs_seen = batch.count

            rows: list[tuple[Any, bool, bool]] = []
            with self.ctx.repos() as repos:
                for raw in batch.raw_jobs:
                    normalized = provider.normalize(raw)
                    outcome.warnings.extend(normalized.warnings)
                    row, created, changed = repos.jobs.upsert_canonical(
                        normalized.job, raw_payload=raw.payload
                    )
                    rows.append((row, created, changed))
                self._materialize(
                    repos, rows, normalized_source=definition, candidate_id=candidate_id,
                    outcome=outcome,
                )
            outcome.ok = True
        except ProviderError as exc:
            outcome.error = exc.message
        except Exception as exc:  # a source must never take the run down
            outcome.error = f"{type(exc).__name__}: {exc}"
        finally:
            if provider is not None:
                provider.close()
            outcome.http_requests = len(client.calls) - before
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    def _materialize(
        self,
        repos: Repos,
        rows: list[tuple[Any, bool, bool]],
        *,
        normalized_source: SourceDefinition,
        candidate_id: str,
        outcome: SourceOutcome,
    ) -> None:
        for row, created, changed in rows:
            if created:
                outcome.jobs_new += 1
                repos.events.append(
                    EntityType.JOB,
                    row.id,
                    EventType.JOB_DISCOVERED,
                    {
                        "source": row.source,
                        "source_job_id": row.source_job_id,
                        "company": row.company,
                        "title": row.title,
                        "url": row.source_url,
                    },
                )
            elif changed:
                outcome.jobs_updated += 1
            else:
                outcome.jobs_unchanged += 1
            if row.status == str(JobStatus.DISCOVERED):
                row.status = str(JobStatus.NORMALIZED)
                repos.events.append(
                    EntityType.JOB,
                    row.id,
                    EventType.JOB_NORMALIZED,
                    {"canonical_key": row.canonical_key, "warnings": []},
                )

    def _record_source_result(self, definition: SourceDefinition, outcome: SourceOutcome) -> None:
        with self.ctx.repos() as repos:
            provider_row = repos.providers.upsert(
                kind=str(definition.entry.kind),
                token=definition.entry.token,
                display_name=definition.entry.display_name,
                industry=definition.entry.industry,
                locator=definition.entry.locator,
                enabled=definition.entry.enabled,
                notes=definition.entry.notes,
            )
            if outcome.ok:
                repos.providers.record_success(provider_row.id, outcome.jobs_seen)
            else:
                repos.providers.record_failure(provider_row.id, outcome.error or "unknown failure")
            repos.events.append(
                EntityType.SYSTEM,
                definition.id,
                EventType.DISCOVERY_SOURCE_COMPLETED if outcome.ok else EventType.DISCOVERY_SOURCE_FAILED,
                outcome.as_dict(),
            )

    def _record_merge(
        self, repos: Repos, opportunity: Any, member: Any, reason: str, canonical_key: str
    ) -> None:
        """Append the DUPLICATE_MERGED audit entry for one folded-in posting.

        Emitted on the first grouping pass as well as on later ones: recording it
        only when an opportunity already existed left the initial collapse with no
        explanation at all, so the ledger could not say why two postings became
        one opportunity.
        """

        repos.events.append(
            EntityType.OPPORTUNITY,
            opportunity.id,
            EventType.DUPLICATE_MERGED,
            {
                "source": member.source,
                "source_job_id": member.source_job_id,
                "url": member.source_url,
                "reason": reason,
                "canonical_key": canonical_key,
            },
        )

    def _group_opportunities(
        self, candidate_id: str, profile_id: str, result: DiscoveryRunResult
    ) -> None:
        """Collapse postings into canonical opportunities (requirement 9).

        Postings are looked up by `(source, source_job_id)` rather than by list
        position, and each member carries its own merge reason, so the audit trail
        names why a specific posting was folded into a specific opportunity.
        """

        from sabra_domain.models import Job

        with self.ctx.repos() as repos:
            statuses = [str(s) for s in GROUPABLE_STATUSES]
            rows = list(
                repos.session.execute(
                    select(Job).where(Job.status.in_(statuses)).order_by(Job.id)
                ).scalars()
            )
            if not rows:
                return

            keys = [JobKey.from_row(row) for row in rows]
            rows_by_identity = {(row.source, row.source_job_id): row for row in rows}
            keys_by_identity = {(k.source, k.source_job_id): k for k in keys}

            groups = group_records(keys)
            for group in groups:
                primary_key = group.primary
                primary_row = rows_by_identity[(primary_key.source, primary_key.source_job_id)]

                opportunity = repos.opportunities.find(candidate_id, profile_id, group.canonical_key)
                created = opportunity is None
                if created:
                    opportunity = repos.opportunities.create(
                        candidate_id=candidate_id,
                        search_profile_id=profile_id,
                        canonical_key=group.canonical_key,
                        title=primary_key.title,
                        company=primary_row.company,
                        location=primary_row.location,
                        primary_job_id=primary_row.id,
                        source_ids=[m.as_source_entry() for m in group.members],
                    )
                    repos.events.append(
                        EntityType.OPPORTUNITY,
                        opportunity.id,
                        EventType.JOB_DISCOVERED,
                        {
                            "canonical_key": group.canonical_key,
                            "company": primary_row.company,
                            "title": primary_key.title,
                            "source_count": group.source_count,
                        },
                    )

                for index, member in enumerate(group.members):
                    row = rows_by_identity[(member.source, member.source_job_id)]
                    if row.opportunity_id != opportunity.id:
                        row.opportunity_id = opportunity.id
                    if created:
                        continue
                    before = opportunity.source_count
                    repos.opportunities.attach_source(opportunity, member.as_source_entry())
                    if opportunity.source_count > before:
                        reason = (
                            group.merge_reasons[index]
                            if index < len(group.merge_reasons)
                            else "exact_key_match"
                        )
                        self._record_merge(repos, opportunity, member, reason, group.canonical_key)

                if created:
                    for index, member in enumerate(group.members[1:], start=1):
                        reason = (
                            group.merge_reasons[index]
                            if index < len(group.merge_reasons)
                            else "exact_key_match"
                        )
                        self._record_merge(repos, opportunity, member, reason, group.canonical_key)
            repos.session.flush()

            total_postings = sum(g.source_count for g in groups)
            multi = [g for g in groups if g.source_count > 1]
            result.postings_ingested = total_postings
            result.canonical_opportunities = len(groups)
            result.duplicates_removed = total_postings - len(groups)
            result.multi_source_opportunities = len(multi)
            result.cross_source_pairs = sum(
                1 for g in multi if len({m.source for m in g.members}) > 1
            )


__all__ = [
    "DiscoveryService",
    "DiscoveryRunResult",
    "SourceOutcome",
    "SourceHealth",
]
