"""`sabra` command line — the operator surface for the whole loop.

Every command is a thin wrapper over a service, so the CLI, the API and the
workers share one implementation of every rule. Commands print machine-readable
JSON with `--json` so a run can be audited from a log.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from sabra_domain.errors import SabraError

from .candidate_intake import (
    SEED_SEARCH_PROFILE_ID,
    ensure_candidate,
    ensure_search_profile,
)
from .context import EngineContext

app = typer.Typer(
    add_completion=False,
    help="Sabra Job Engine — discover, rank, prepare and track job applications.",
    no_args_is_help=True,
)
console = Console()


def _emit(payload: Any, as_json: bool, title: str | None = None) -> None:
    if as_json:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    if title:
        console.print(f"[bold]{title}[/bold]")
    console.print_json(json.dumps(payload, default=str))


def _context() -> EngineContext:
    return EngineContext.create()


@app.command("init")
def init_command(
    candidate_name: str | None = typer.Option(
        None, "--candidate-name", help="Candidate display name"
    ),
    profile: Path | None = typer.Option(
        None, "--profile", help="Owner-supplied candidate profile (YAML or JSON)"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create the database, load the source registry and seed a search profile."""

    ctx = _context()
    try:
        synced = ctx.sync_source_registry()
        candidate_id = ensure_candidate(
            ctx, profile_path=profile, display_name=candidate_name, create_placeholder=True
        )
        search_id = ensure_search_profile(ctx, candidate_id)
        payload = {
            "database_url": ctx.settings.database_url,
            "candidate_id": candidate_id,
            "search_profile_id": search_id,
            "sources_synced": synced,
            "sources_enabled": len(ctx.registry.enabled),
            "llm_provider": ctx.llm.name,
        }
        _emit(payload, as_json, "Initialised")
    finally:
        ctx.close()


@app.command("discover")
def discover_command(
    candidate_id: str | None = typer.Option(None, "--candidate-id"),
    search_profile_id: str | None = typer.Option(None, "--search-profile-id"),
    source: list[str] | None = typer.Option(None, "--source", help="Limit to these source ids"),
    max_items: int | None = typer.Option(None, "--max-items", help="Cap postings per source"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run discovery across every enabled source (requirement 7)."""

    from .discovery import DiscoveryService

    ctx = _context()
    try:
        candidate = candidate_id or ensure_candidate(ctx)
        profile = search_profile_id or ensure_search_profile(ctx, candidate)
        result = DiscoveryService(ctx).run(
            candidate_id=candidate,
            search_profile_id=profile,
            source_ids=list(source) if source else None,
            max_items_per_source=max_items,
        )
        if as_json:
            typer.echo(json.dumps(result.summary(), indent=2, default=str))
            return
        table = Table(title="Discovery run", show_lines=False)
        for column in ("source", "kind", "seen", "new", "upd", "unchanged", "ms", "error"):
            table.add_column(column)
        for outcome in result.sources:
            table.add_row(
                outcome.source_id,
                outcome.kind,
                str(outcome.jobs_seen),
                str(outcome.jobs_new),
                str(outcome.jobs_updated),
                str(outcome.jobs_unchanged),
                str(outcome.elapsed_ms),
                (outcome.error or "")[:60],
            )
        console.print(table)
        console.print_json(json.dumps(result.summary(), default=str))
    finally:
        ctx.close()


@app.command("rank")
def rank_command(
    candidate_id: str | None = typer.Option(None, "--candidate-id"),
    search_profile_id: str | None = typer.Option(None, "--search-profile-id"),
    shortlist_limit: int | None = typer.Option(
        None, "--shortlist-limit", help="How many opportunities to shortlist"
    ),
    rescore: bool = typer.Option(
        False, "--rescore", help="Re-filter and re-assess opportunities already decided"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Hard-filter, assess fit, rank and shortlist (requirements 11-13)."""

    from .candidate_intake import ensure_candidate, ensure_search_profile
    from .intelligence import IntelligenceService

    ctx = _context()
    try:
        candidate = candidate_id or ensure_candidate(ctx)
        profile = search_profile_id or ensure_search_profile(ctx, candidate)
        result = IntelligenceService(ctx).run(
            candidate_id=candidate,
            search_profile_id=profile,
            shortlist_limit=shortlist_limit,
            rescore=rescore,
        )
        if as_json:
            typer.echo(json.dumps(result.summary(), indent=2, default=str))
            return
        table = Table(title="Ranked opportunities")
        for column in ("#", "score", "title", "company", "location", "fit", "conf"):
            table.add_column(column)
        for index, entry in enumerate(result.top, start=1):
            table.add_row(
                str(index),
                f"{entry['rank_score']:.4f}",
                entry["title"][:46],
                entry["company"][:20],
                (entry["location"] or "-")[:20],
                f"{entry['components']['fit']:.2f}",
                f"{entry['components']['confidence']:.2f}",
            )
        console.print(table)
        console.print_json(json.dumps(result.summary(), default=str))
    finally:
        ctx.close()


@app.command("status")
def status_command(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show counts across every stage of the loop."""

    from sabra_domain import models
    from sabra_domain.enums import JobStatus
    from sabra_domain.repositories import (
        ApplicationRepository,
        OpportunityRepository,
    )

    ctx = _context()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            payload: dict[str, Any] = {
                "generated_at": datetime.now(UTC).isoformat(),
                "database_url": ctx.settings.database_url,
                "llm_provider": ctx.llm.name,
                "candidate_id": candidate.id if candidate else None,
                "candidate_name": candidate.display_name if candidate else None,
                "jobs_by_status": repos.jobs.count_by_status(),
                "opportunities_by_status": OpportunityRepository(repos.session).count_by_status(
                    candidate.id if candidate else None
                ),
                "applications_by_status": ApplicationRepository(repos.session).count_by_status(
                    candidate.id if candidate else None
                ),
                "jobs_discovered": repos.session.query(models.Job).count(),
                "opportunities": repos.session.query(models.Opportunity).count(),
                "packages": repos.session.query(models.ApplicationPackageRow).count(),
                "applications": repos.session.query(models.ApplicationRow).count(),
                "events": len(repos.events.recent(limit=100000)),
                "sources": [
                    {
                        "id": row.id,
                        "kind": row.kind,
                        "token": row.token,
                        "enabled": row.enabled,
                        "health": row.health,
                        "total_discovered": row.total_discovered,
                        "last_error": row.last_error,
                    }
                    for row in repos.providers.list_all()
                ],
                "shortlisted": sum(
                    1 for o in repos.opportunities.list_for_candidate(candidate.id) if o.shortlisted
                )
                if candidate
                else 0,
                "job_statuses_declared": [str(s) for s in JobStatus],
            }
        _emit(payload, as_json, "Status")
    finally:
        ctx.close()


def main() -> None:
    try:
        app()
    except SabraError as exc:
        console.print(f"[red]{type(exc).__name__}[/red]: {exc}")
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    main()


__all__ = ["app", "main", "SEED_SEARCH_PROFILE_ID"]
