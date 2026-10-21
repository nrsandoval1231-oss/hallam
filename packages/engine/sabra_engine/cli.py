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
