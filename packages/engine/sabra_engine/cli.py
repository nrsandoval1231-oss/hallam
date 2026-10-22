"""`sabra` command line — the operator surface for the whole loop.

Every command is a thin wrapper over a service, so the CLI, the API and the
workers share one implementation of every rule. Commands print machine-readable
JSON with `--json` so a run can be audited from a log.
"""

from __future__ import annotations

import json
import sys
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
    load_search_profile,
    store_search_profile_file,
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


def _replaced_message(profile_id: str, old_name: str) -> str:
    return f"Replaced existing search {profile_id} (was {old_name!r})"


@app.command("init")
def init_command(
    candidate_name: str | None = typer.Option(
        None, "--candidate-name", help="Candidate display name"
    ),
    profile: Path | None = typer.Option(
        None, "--profile", help="Owner-supplied candidate profile (YAML or JSON)"
    ),
    search_profile: Path | None = typer.Option(
        None,
        "--search-profile",
        help=(
            "Search profile to load and activate (YAML or JSON; "
            "see config/search_profile.example.yaml)"
        ),
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Replace a stored search with the same id when --search-profile differs from it",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create the database, load the source registry and set up a search profile.

    Without --search-profile, a stored search profile is kept as it is; the built-in seed
    profile is created only when the candidate has none.
    """

    ctx = _context()
    try:
        synced = ctx.sync_source_registry()
        candidate_id = ensure_candidate(
            ctx, profile_path=profile, display_name=candidate_name, create_placeholder=True
        )
        replaced: str | None = None
        if search_profile is not None:
            stored = store_search_profile_file(
                ctx, candidate_id, load_search_profile(search_profile), replace=replace
            )
            search_id, replaced = stored["id"], stored["replaced"]
        else:
            search_id = ensure_search_profile(ctx, candidate_id)
        payload = {
            "database_url": ctx.settings.database_url,
            "candidate_id": candidate_id,
            "search_profile_id": search_id,
            "sources_synced": synced,
            "sources_enabled": len(ctx.registry.enabled),
            "llm_provider": ctx.llm.name,
        }
        if replaced is not None:
            payload["replaced"] = _replaced_message(search_id, replaced)
            if not as_json:
                typer.echo(payload["replaced"])
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
    from .runs import RunService

    ctx = _context()
    try:
        RunService.refuse_if_active(ctx)
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
    research: bool = typer.Option(
        True,
        "--research/--no-research",
        help="Research the shortlist (company sites are read; a packet under 7 days old is kept)",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Hard-filter, assess fit, rank, shortlist and research the shortlist (requirements 11-14).

    Uses the active search profile unless --search-profile-id is given.
    """

    from .candidate_intake import ensure_candidate, ensure_search_profile
    from .intelligence import IntelligenceService
    from .research import ResearchService
    from .runs import RunService, build_research_http

    ctx = _context()
    try:
        RunService.refuse_if_active(ctx)
        candidate = candidate_id or ensure_candidate(ctx)
        profile = search_profile_id or ensure_search_profile(ctx, candidate)
        result = IntelligenceService(ctx).run(
            candidate_id=candidate,
            search_profile_id=profile,
            shortlist_limit=shortlist_limit,
            rescore=rescore,
        )
        research_summary: dict[str, Any] | None = None
        if research:
            with ctx.read_repos() as repos:
                limit = shortlist_limit or repos.search_profiles.get(profile).shortlist_target
            research_summary = ResearchService(ctx, http=build_research_http(ctx)).research_fresh(
                candidate, search_profile_id=profile, limit=limit
            )
        if as_json:
            payload = result.summary()
            if research_summary is not None:
                payload["research"] = research_summary
            typer.echo(json.dumps(payload, indent=2, default=str))
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
        if research_summary is not None:
            console.print_json(json.dumps({"research": research_summary}, default=str))
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


searches_app = typer.Typer(help="Define, list and switch search profiles.")
app.add_typer(searches_app, name="searches")


def _profile_row(profile: Any, active_id: str | None) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "active": profile.id == active_id,
        "target_titles": profile.target_titles,
        "compensation_minimum": profile.compensation.minimum,
        "compensation_target": profile.compensation.target,
        "shortlist_target": profile.shortlist_target,
        "prepare_target": profile.prepare_target,
    }


@searches_app.command("load")
def searches_load(
    path: Path = typer.Argument(..., help="Search profile file (YAML or JSON)"),
    activate: bool = typer.Option(
        True, "--activate/--no-activate", help="Make it the profile discover and rank use"
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Replace a stored search with the same id when the file differs from it",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Store a search profile from a file.

    A file whose id matches a stored search that is different (another name, or edited
    content) is refused unless --replace is given; the same file again is harmless.
    """

    ctx = _context()
    try:
        candidate_id = ensure_candidate(ctx)
        profile = load_search_profile(path)
        stored = store_search_profile_file(
            ctx, candidate_id, profile, replace=replace, activate=True if activate else None
        )
        profile_id = stored["id"]
        with ctx.read_repos() as repos:
            active = repos.search_profiles.active_one(candidate_id)
            payload = _profile_row(
                repos.search_profiles.get(profile_id), active.id if active else None
            )
        if stored["replaced"] is not None:
            payload["replaced"] = _replaced_message(profile_id, stored["replaced"])
            if not as_json:
                typer.echo(payload["replaced"])
        _emit(payload, as_json, "Loaded")
    finally:
        ctx.close()


@searches_app.command("list")
def searches_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List the stored search profiles; the active one is marked."""

    from sabra_domain.schemas import SearchProfile

    ctx = _context()
    try:
        candidate_id = ensure_candidate(ctx)
        ensure_search_profile(ctx, candidate_id)
        with ctx.read_repos() as repos:
            active = repos.search_profiles.active_one(candidate_id)
            active_id = active.id if active else None
            rows = [
                _profile_row(SearchProfile.model_validate(row.payload), active_id)
                for row in repos.search_profiles.list_for_candidate(candidate_id)
            ]
        if as_json:
            typer.echo(json.dumps(rows, indent=2, default=str))
            return
        table = Table(title="Search profiles")
        for column in ("", "id", "name", "min", "target", "titles"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                "*" if row["active"] else "",
                row["id"],
                row["name"],
                str(row["compensation_minimum"] or "-"),
                str(row["compensation_target"] or "-"),
                ", ".join(row["target_titles"][:3]),
            )
        console.print(table)
    finally:
        ctx.close()


@searches_app.command("show")
def searches_show(
    profile_id: str | None = typer.Argument(None, help="Profile id; the active one by default"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one search profile in full."""

    ctx = _context()
    try:
        candidate_id = ensure_candidate(ctx)
        chosen = profile_id or ensure_search_profile(ctx, candidate_id)
        with ctx.read_repos() as repos:
            payload = repos.search_profiles.get(chosen).model_dump(mode="json")
        _emit(payload, as_json, "Search profile")
    finally:
        ctx.close()


@searches_app.command("activate")
def searches_activate(
    profile_id: str = typer.Argument(..., help="Profile id to make active"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Make a stored profile the one discover, rank and run use."""

    from sabra_domain.enums import EntityType, EventType

    ctx = _context()
    try:
        candidate_id = ensure_candidate(ctx)
        with ctx.repos() as repos:
            profile = repos.search_profiles.activate(candidate_id, profile_id)
            repos.events.append(
                EntityType.SEARCH_PROFILE,
                profile_id,
                EventType.SEARCH_PROFILE_ACTIVATED,
                {"name": profile.name},
            )
        _emit(_profile_row(profile, profile_id), as_json, "Activated")
    finally:
        ctx.close()


@app.command("run")
def run_command(
    kind: str = typer.Option(
        "full", "--kind", help="discover, rank (rank + research), or full (everything)"
    ),
    prepare: bool = typer.Option(
        False, "--prepare", help="Also prepare packages for the top shortlisted roles"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run the loop now: discover, rank, research the shortlist, optionally prepare.

    The same run the dashboard's "Run now" starts. Nothing is submitted: preparing a
    package waits for your approval.
    """

    from .runs import RunService, build_discovery_http, build_research_http

    ctx = _context()
    try:
        service = RunService(
            ctx,
            discovery_http=build_discovery_http(ctx),
            research_http=build_research_http(ctx),
            on_update=None if as_json else _print_stage_changes(),
        )
        record = service.run_sync(kind, prepare)
        if as_json:
            typer.echo(json.dumps(record, indent=2, default=str))
        else:
            _print_run_table(record)
        if record["status"] == "FAILED":
            raise typer.Exit(code=1)
    finally:
        ctx.close()


def _print_stage_changes() -> Any:
    seen: dict[str, str] = {}

    def show(progress: dict[str, Any]) -> None:
        for name, stage in progress.get("stages", {}).items():
            state = str(stage.get("status", ""))
            if seen.get(name) != state:
                seen[name] = state
                console.print(f"[bold]{name}[/bold] {state}")

    return show


def _print_run_table(record: dict[str, Any]) -> None:
    progress = record.get("progress") or {}
    table = Table(title=f"Run {record['id']} - {record['status']}")
    for column in ("stage", "status", "detail"):
        table.add_column(column)
    for name, stage in (progress.get("stages") or {}).items():
        detail = ", ".join(
            f"{key}={value}"
            for key, value in stage.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        table.add_row(name, str(stage.get("status", "")), detail)
    console.print(table)
    sources = progress.get("sources") or []
    if sources:
        source_table = Table(title="Sources")
        for column in ("source", "ok", "seen", "new", "ms", "error"):
            source_table.add_column(column)
        for item in sources:
            source_table.add_row(
                str(item.get("source_id")),
                "yes" if item.get("ok") else "NO",
                str(item.get("jobs_seen")),
                str(item.get("jobs_new")),
                str(item.get("elapsed_ms")),
                str(item.get("error") or "")[:60],
            )
        console.print(source_table)
    for warning in progress.get("warnings") or []:
        console.print(f"[yellow]{warning}[/yellow]")
    console.print(progress.get("summary") or "")
    if record.get("error"):
        console.print(f"[red]{record['error']}[/red]")


answers_app = typer.Typer(help="Record and verify reusable application answers.")
app.add_typer(answers_app, name="answers")


@answers_app.command("list")
def answers_list(
    candidate_id: str | None = typer.Option(None, "--candidate-id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show recorded answers and whether each one is verified."""

    from .answer_memory import AnswerMemoryService

    ctx = _context()
    try:
        payload = AnswerMemoryService(ctx).list_answers(candidate_id)
        _emit(payload, as_json, "Answer memory")
    finally:
        ctx.close()


@answers_app.command("record")
def answers_record(
    key: str = typer.Argument(..., help="Canonical question key, such as salary_expectations"),
    value: str = typer.Argument(..., help="The owner's answer, in their words"),
    question: str | None = typer.Option(None, "--question", help="The form wording, if you have it"),
    candidate_id: str | None = typer.Option(None, "--candidate-id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Store an answer. It is not used on forms until you verify it."""

    from .answer_memory import AnswerMemoryService

    ctx = _context()
    try:
        payload = AnswerMemoryService(ctx).record(
            key, value, question_text=question, candidate_id=candidate_id
        )
        _emit(payload, as_json, "Recorded")
    finally:
        ctx.close()


@answers_app.command("verify")
def answers_verify(
    key: str = typer.Argument(..., help="Question key to authorize for auto-fill"),
    candidate_id: str | None = typer.Option(None, "--candidate-id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Mark a recorded answer verified so packages may use it."""

    from .answer_memory import AnswerMemoryService

    ctx = _context()
    try:
        payload = AnswerMemoryService(ctx).verify(key, candidate_id=candidate_id)
        _emit(payload, as_json, "Verified")
    finally:
        ctx.close()


packages_app = typer.Typer(help="Rebuild approved, blocked or failed packages after answer memory changes.")
app.add_typer(packages_app, name="packages")


@packages_app.command("refresh")
def packages_refresh(
    application_id: str | None = typer.Argument(None, help="Approved, blocked or failed application to rebuild"),
    all_unsubmitted: bool = typer.Option(
        False,
        "--all-unsubmitted",
        help="Refresh every approved, blocked or failed application that has not been submitted",
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt for --all-unsubmitted"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Revoke approval and prepare a new package revision.

    The owner must approve the new revision. A BLOCKED or FAILED application can be
    refreshed too, provided nothing was submitted and no click is awaiting a report;
    submitted applications are refused.
    """

    from sabra_domain.errors import ValidationFailure

    from .refresh import PackageRefreshService

    if all_unsubmitted and application_id:
        raise ValidationFailure("pass either an application id or --all-unsubmitted, not both")
    if not all_unsubmitted and not application_id:
        raise ValidationFailure("pass an application id, or --all-unsubmitted")
    if all_unsubmitted and not yes:
        confirmed = typer.confirm(
            "Refresh every approved, blocked or failed application that has not been submitted? "
            "Each approval is revoked and must be given again."
        )
        if not confirmed:
            raise typer.Exit(code=1)

    ctx = _context()
    try:
        service = PackageRefreshService(ctx)
        if all_unsubmitted:
            payload = service.refresh_all_unsubmitted().as_dict()
        else:
            payload = service.refresh(application_id or "").as_dict()
        _emit(payload, as_json, "Refreshed")
    finally:
        ctx.close()


HANDOFF_ANSWERS = ("submitted", "abandoned", "failed")


def prompt_for_outcome(ask: Any = None) -> str:
    """Ask until the answer is one of submitted / abandoned / failed ('q' = abandoned).

    A typo is asked again; it is never recorded as an outcome.
    """

    ask = ask or typer.prompt
    while True:
        raw = str(
            ask("Outcome after you submit or leave the form (submitted/abandoned/failed, q)")
        ).strip().lower()
        if raw == "q":
            return "abandoned"
        if raw in HANDOFF_ANSWERS:
            return raw
        typer.echo("Please type submitted, abandoned, failed, or q (the same as abandoned).")


@app.command("apply")
def apply_command(
    application_id: str = typer.Argument(..., help="Approved application to submit or hand off"),
    real: bool = typer.Option(
        False,
        "--real",
        help="Allow a real submission. Refused while SABRA_AUTOMATION_DRY_RUN is true (the default).",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Submit an approved application, or hand a challenged form to the person here.

    Dry-run is the default: nothing is opened, typed, uploaded or submitted, and the
    result is a plan of what a real run would fill. A CAPTCHA is never solved by this
    process. With --real, a challenged form opens in a visible browser for the person
    to solve and submit, and you are asked for the outcome.
    """

    from .execution import ExecutionService

    ctx = _context()
    try:
        result = ExecutionService(ctx, handoff_prompt=prompt_for_outcome).submit(
            application_id, allow_real_submission=real
        )
        _emit(result.as_dict(), as_json, "Submission")
    finally:
        ctx.close()


def prompt_for_forced_confirmation(application_id: str, ask: Any = None) -> str:
    """Make the person type the application id back. `--yes` does not stand in for it."""

    ask = ask or typer.prompt
    return str(
        ask(
            "A forced report records an outcome without Sabra seeing it. "
            f"Type the application id to confirm ({application_id})"
        )
    ).strip()


@app.command("handoff-outcome")
def handoff_outcome_command(
    application_id: str = typer.Argument(..., help="Application left APPLYING after a handoff"),
    outcome: str = typer.Argument(..., help="submitted, abandoned, or failed"),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "For an attempt stuck APPLYING after a hard kill, when nothing will ever write "
            "the event a normal report waits for. You must type the application id back. "
            "Recorded as forced."
        ),
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Report how a human handoff or an unconfirmed click ended.

    This is the only way an application leaves APPLYING without the employer's
    confirmation page. `abandoned` returns it to BLOCKED so it can be retried.
    """

    from .execution import ExecutionService

    ctx = _context()
    try:
        service = ExecutionService(ctx)
        if force:
            status = service.force_report_handoff_outcome(
                application_id,
                outcome.strip().lower(),
                confirm_id=prompt_for_forced_confirmation(application_id),
            )
        else:
            status = service.report_handoff_outcome(application_id, outcome.strip().lower())
        _emit(status, as_json, "Handoff")
    finally:
        ctx.close()


def main() -> None:
    try:
        app()
    except SabraError as exc:
        from sabra_domain.errors import NotFound

        from .candidate_intake import missing_candidate_message

        text = str(exc)
        if isinstance(exc, NotFound) and exc.entity == "candidate":
            from sabra_domain.settings import get_settings

            text = missing_candidate_message(get_settings().database_url)
        # typer.echo, not Rich: a non-interactive capture still has to show the
        # database path instead of a traceback.
        typer.echo(f"{type(exc).__name__}: {text}")
        # sys.exit, not typer.Exit: this runs outside Click, where typer.Exit would
        # surface as a second traceback.
        sys.exit(1)


if __name__ == "__main__":
    main()


__all__ = ["app", "main", "SEED_SEARCH_PROFILE_ID"]
