"""Local browser surface over the Artemis engine (Sabra internals)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sabra_automation.form_keys import answer_key_choices
from sabra_domain import models
from sabra_domain.enums import (
    ApplicationStatus,
    EntityType,
    EventType,
    PackageStatus,
    TruthLevel,
)
from sabra_domain.errors import (
    ApprovalRequired,
    ApprovalScopeMismatch,
    InvalidTransition,
    NotFound,
    SabraError,
)
from sabra_domain.identity import new_id
from sabra_domain.repositories import ApplicationRepository, OpportunityRepository
from sabra_domain.schemas import SearchProfile
from sabra_domain.settings import REPO_ROOT, get_settings
from sabra_engine.answer_memory import AnswerMemoryService
from sabra_engine.approval import ApprovalService
from sabra_engine.candidate_intake import (
    ensure_candidate,
    ensure_search_profile,
    extract_resume_text,
    format_validation_errors,
    owner_identity_levels,
    owner_identity_view,
    save_owner_identity,
)
from sabra_engine.context import EngineContext
from sabra_engine.display import compensation_text as _compensation_text
from sabra_engine.documents import DocumentService, package_summary
from sabra_engine.execution import AdapterRegistry, ExecutionService, default_registry
from sabra_engine.handoff_manager import HANDOFFS
from sabra_engine.intelligence import pass_opportunity
from sabra_engine.ledger import (
    CONTACT_MAX,
    NOTE_MAX,
    OUTCOME_LABELS,
    LedgerService,
    parse_occurred_at,
)
from sabra_engine.opportunity_detail import OpportunityDetailService
from sabra_engine.refresh import PackageRefreshService
from sabra_engine.runs import RunService
from sabra_engine.sources import SourcesService
from sqlalchemy.exc import OperationalError

from .pages import (
    ASK_JS,
    CANDIDATE_BODY,
    HOME_CSS,
    PLAN_BODY,
    PLAN_CSS,
    RESEARCH_BODY,
    RESEARCH_CSS,
    SETTINGS_BODY,
    home_body,
    page_shell,
    searches_body,
)
from .review_pages import (
    DETAIL_CSS,
    PIPELINE_CSS,
    opportunity_detail_body,
    pipeline_body,
    review_body,
)


def record_server_stopped() -> int:
    """Note on every application with a run or window in this process that the server stopped.

    The thread or run dies with the process, so nothing will ever report for it. The
    event is what lets a person report the outcome afterwards (`sabra handoff-outcome`).
    """

    recorded = 0
    # Windows with a live thread, and synchronous runs that never got that far.
    for application_id in dict.fromkeys([*HANDOFFS.active_ids(), *HANDOFFS.running_ids()]):
        with suppress(Exception):
            ctx = EngineContext.create()
            try:
                with ctx.repos() as repos:
                    if (
                        repos.applications.get(application_id).status
                        == ApplicationStatus.APPLYING.value
                    ):
                        repos.events.append(
                            EntityType.APPLICATION,
                            application_id,
                            EventType.HANDED_TO_HUMAN,
                            {"state": "server_stopped"},
                        )
                        recorded += 1
            finally:
                ctx.close()
    return recorded


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    record_server_stopped()
    with suppress(Exception):
        RunService.record_server_stopped()


app = FastAPI(title="Artemis", version="0.1.0", lifespan=_lifespan)


@app.exception_handler(SabraError)
async def sabra_error_handler(_request: Request, exc: SabraError) -> JSONResponse:
    """Operator mistakes are JSON the pages can show, not a plain-text 500."""

    status = 422
    if isinstance(exc, (InvalidTransition, ApprovalRequired, ApprovalScopeMismatch)):
        status = 409
    elif isinstance(exc, NotFound):
        status = 404
    lowered = str(exc).lower()
    if any(
        phrase in lowered
        for phrase in (
            "already approved",
            "was not refreshed",
            "already submitted",
            "already in progress",
            "no submission waiting",
            "already active",
            "already in the registry",
            "cannot pass",
            "cannot withdraw",
        )
    ):
        status = 409
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(OperationalError)
async def database_error_handler(_request: Request, exc: OperationalError) -> JSONResponse:
    """A locked SQLite file is another Artemis process, not a server bug."""

    if "database is locked" in str(exc).lower():
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "The database is locked: another Artemis process holds it (a CLI command or "
                    "a second dashboard). Wait for it to finish, or stop it, then retry."
                )
            },
        )
    return JSONResponse(status_code=500, content={"detail": "A database error occurred."})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _latest_packages(repos: Any, candidate_id: str | None) -> list[Any]:
    """One row per opportunity: the highest revision. Older incomplete rows stay stored."""

    if candidate_id is None:
        return []
    latest: dict[str, Any] = {}
    for row in repos.packages.list_all(candidate_id):
        current = latest.get(row.opportunity_id)
        if current is None or (row.revision or 0) > (current.revision or 0):
            latest[row.opportunity_id] = row
    return list(latest.values())


def _status_payload() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            profile_name = None
            if candidate:
                try:
                    profile_name = repos.candidates.active_profile(candidate.id).identity.full_name
                except Exception:
                    profile_name = None
            return {
                "generated_at": datetime.now(UTC).isoformat(),
                "candidate_id": candidate.id if candidate else None,
                "candidate_name": profile_name or (candidate.display_name if candidate else None),
                "jobs_discovered": repos.session.query(models.Job).count(),
                "opportunities": repos.session.query(models.Opportunity).count(),
                "shortlisted": sum(
                    1
                    for item in repos.opportunities.list_for_candidate(candidate.id)
                    if item.shortlisted
                )
                if candidate
                else 0,
                "packages": repos.session.query(models.ApplicationPackageRow).count(),
                "packages_latest": len(_latest_packages(repos, candidate.id)) if candidate else 0,
                "automation_dry_run": ctx.settings.automation_dry_run,
                "applications": repos.session.query(models.ApplicationRow).count(),
                "jobs_by_status": repos.jobs.count_by_status(),
                "opportunities_by_status": OpportunityRepository(repos.session).count_by_status(
                    candidate.id if candidate else None
                ),
                "applications_by_status": ApplicationRepository(repos.session).count_by_status(
                    candidate.id if candidate else None
                ),
            }
    finally:
        ctx.close()


def _truth_sections(profile: Any) -> list[dict[str, Any]]:
    """The profile as rows a person can read, each with its truth level.

    A row's level is the level recorded for its section (a resume import marks whole
    sections INFERRED; the owner's own statements are VERIFIED); a missing core fact is
    shown as UNKNOWN rather than left out, so the gap is visible.
    """

    def level(path: str, present: bool) -> str:
        return profile.provenance_for(path).level.value if present else TruthLevel.UNKNOWN.value

    identity = profile.identity
    core = (
        ("full_name", "Full name"),
        ("email", "Email"),
        ("phone", "Phone"),
        ("city", "City"),
        ("state", "State"),
    )
    extra = (
        ("first_name", "First name"),
        ("last_name", "Last name"),
        ("postal_code", "Postal code"),
        ("country", "Country"),
        ("linkedin_url", "LinkedIn"),
    )
    identity_rows = []
    for field, label in core:
        value = getattr(identity, field, None)
        identity_rows.append(
            {
                "label": label,
                "detail": value or "not provided",
                "level": level(f"identity.{field}", bool(value)),
            }
        )
    for field, label in extra:
        value = getattr(identity, field, None)
        if value:
            identity_rows.append(
                {"label": label, "detail": value, "level": level(f"identity.{field}", True)}
            )
    street = profile.answer_memory.get("street_address")
    if street and street.value:
        identity_rows.append(
            {"label": "Street address", "detail": str(street.value), "level": street.level.value}
        )

    about = []
    if profile.headline:
        about.append(
            {"label": "Headline", "detail": profile.headline, "level": level("headline", True)}
        )
    if profile.summary:
        about.append(
            {"label": "Summary", "detail": profile.summary, "level": level("summary", True)}
        )

    employment_level = level("employment", bool(profile.employment))
    def employment_label(entry: Any) -> str:
        label = " at ".join(filter(None, [entry.title, entry.employer])) or "Position"
        if entry.is_break and "career break" not in label.lower():
            label += " (career break)"
        return label

    employment = [
        {
            "label": employment_label(entry),
            "detail": " – ".join(filter(None, [entry.start_date, entry.end_date])),
            "level": employment_level,
        }
        for entry in profile.employment
    ]
    education_level = level("education", bool(profile.education))
    education = [
        {
            "label": " – ".join(filter(None, [entry.degree, entry.field_of_study])) or "Education",
            "detail": entry.institution or "",
            "level": education_level,
        }
        for entry in profile.education
    ]
    cert_level = level("certifications", bool(profile.certifications))
    certifications = [
        {"label": entry.name, "detail": entry.issuer or "", "level": cert_level}
        for entry in profile.certifications
    ]
    skill_level = level("skills", bool(profile.skills))
    skills = [
        {"label": entry.name, "detail": entry.category or "", "level": skill_level}
        for entry in profile.skills
    ]
    return [
        {"title": "Identity", "rows": identity_rows},
        {"title": "Headline and summary", "rows": about},
        {"title": "Employment", "rows": employment},
        {"title": "Education", "rows": education},
        {"title": "Certifications", "rows": certifications},
        {"title": "Skills", "rows": skills},
    ]


@app.get("/api/candidate")
def candidate_profile() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return {
                    "candidate_id": None,
                    "profile": None,
                    "resume": None,
                    "resumes": [],
                    "identity": {},
                    "identity_levels": {},
                    "sections": [],
                }
            profile = None
            try:
                profile = repos.candidates.active_profile(candidate.id)
                profile_payload = profile.model_dump(mode="json")
                provenance = {
                    key: value.model_dump(mode="json") for key, value in profile.provenance.items()
                }
            except Exception:
                profile_payload = None
                provenance = {}
            resume = repos.resumes.master(candidate.id)
            resumes = [
                {
                    "id": row.id,
                    "version": row.version,
                    "label": row.label,
                    "is_master": bool(row.is_master),
                    "source_format": row.source_format,
                    "content_hash": row.content_hash,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    # None, not [], for a version stored before the lines were recorded.
                    "unparsed_lines": (
                        list(row.parsed["unparsed_lines"])
                        if "unparsed_lines" in (row.parsed or {})
                        else None
                    ),
                    "unparsed_line_count": (row.parsed or {}).get("unparsed_line_count"),
                    "warnings": list((row.parsed or {}).get("warnings") or []),
                }
                for row in repos.resumes.list_for_candidate(candidate.id)
                # Tailored resumes belong to a package; these are the owner's own versions.
                if row.derived_from_resume_id is None and row.opportunity_id is None
            ]
            return {
                "candidate_id": candidate.id,
                "candidate_name": candidate.display_name,
                "profile": profile_payload,
                "provenance": provenance,
                "identity": owner_identity_view(profile) if profile else {},
                "identity_levels": owner_identity_levels(profile) if profile else {},
                "sections": _truth_sections(profile) if profile else [],
                "resumes": resumes,
                "resume": {
                    "id": resume.id,
                    "version": resume.version,
                    "label": resume.label,
                    "content_hash": resume.content_hash,
                }
                if resume
                else None,
            }
    finally:
        ctx.close()


class IdentityForm(BaseModel):
    """The owner's own statements. A blank field leaves the stored value alone."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    headline: str | None = None


@app.put("/api/candidate/identity")
def save_identity(body: IdentityForm) -> dict[str, Any]:
    """Store the identity form as VERIFIED owner statements in a new profile version."""

    ctx = EngineContext.create()
    try:
        candidate_id = ensure_candidate(ctx, display_name=None, create_placeholder=True)
        profile = save_owner_identity(ctx, candidate_id, body.model_dump())
        return {
            "candidate_id": candidate_id,
            "truth_level": TruthLevel.VERIFIED.value,
            "identity": owner_identity_view(profile),
            "identity_levels": owner_identity_levels(profile),
            "version": profile.version,
        }
    finally:
        ctx.close()


@app.post("/api/candidate/resume")
async def upload_resume(resume: Annotated[UploadFile, File(...)]) -> dict[str, Any]:
    content = await resume.read()
    # .pdf, .docx and .txt; an unreadable file is a 422 (ValidationFailure), not a 500.
    text, source_format = extract_resume_text(resume.filename, content)
    if not text.strip():
        raise HTTPException(status_code=422, detail="The uploaded resume contains no readable text")
    ctx = EngineContext.create()
    try:
        candidate_id = ensure_candidate(ctx, display_name=None, create_placeholder=True)
        report = DocumentService(ctx).ingest_master_resume_report(
            candidate_id,
            text,
            label=resume.filename or "Master resume",
            source_format=source_format,
            source_path=resume.filename,
        )
        return {
            "candidate_id": candidate_id,
            "resume_id": report["resume_id"],
            "unparsed_lines": report["unparsed_lines"],
            "warnings": report["warnings"],
            "source_format": source_format,
            "truth_level": TruthLevel.INFERRED.value,
            "message": (
                "Resume stored as evidence. Resume-derived profile fields remain inferred "
                "until confirmed by the candidate."
            ),
        }
    finally:
        ctx.close()


@app.get("/api/status")
def status() -> dict[str, Any]:
    return _status_payload()


@app.get("/api/opportunities")
def opportunity_list(all_profiles: bool = False) -> dict[str, Any]:
    """The ranked shortlist for the active search (`?all_profiles=true` for every search)."""

    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return {"opportunities": []}
            active = None if all_profiles else repos.search_profiles.active_one(candidate.id)
            rows = []
            for item in repos.opportunities.shortlist(candidate.id):
                if active is not None and (item.search_profile_id or "") != active.id:
                    continue
                jobs = repos.opportunities.list_jobs(item.id)
                job = jobs[0] if jobs else None
                fit = item.fit_assessment
                gaps = list(fit.gaps) if fit and fit.gaps else []
                rows.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "company": item.company,
                        "location": item.location,
                        "salary_min": job.salary_min if job else None,
                        "salary_max": job.salary_max if job else None,
                        "compensation_text": _compensation_text(job),
                        "industry": job.department if job else None,
                        "rank_score": item.rank_score,
                        "rank_position": item.rank_position,
                        "status": item.status,
                        "shortlist_reason": item.shortlist_reason,
                        "source_url": job.source_url if job else None,
                        "fit_score": fit.fit_score if fit else None,
                        "confidence": fit.confidence if fit else None,
                        "why_this_job": fit.why_this_job if fit else None,
                        "strengths": fit.strengths if fit else [],
                        "gaps": gaps,
                        "primary_gap": gaps[0] if gaps else None,
                    }
                )
            return {"opportunities": rows}
    finally:
        ctx.close()


@app.get("/api/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: str) -> dict[str, Any]:
    """The opportunity detail projection (PRD section 30)."""

    ctx = EngineContext.create()
    try:
        return OpportunityDetailService(ctx).detail(opportunity_id)
    finally:
        ctx.close()


class AnswerWrite(BaseModel):
    key: str
    value: str = ""
    question_text: str | None = None


class AnswerVerify(BaseModel):
    key: str
    # The value the owner saw. Required: it must equal the stored value, so the owner
    # verifies exactly what was on the page and not a value that changed since. (The CLI
    # calls the service directly and may omit it.)
    value: str


class PackageSelection(BaseModel):
    package_id: str
    content_hash: str


class BatchApprovalRequest(BaseModel):
    approved_by: str = "owner"
    packages: list[PackageSelection] = Field(default_factory=list)


def _job_sources(repos: Any) -> dict[str, str]:
    """`job id -> source` for every job that has an application, in one query."""

    rows = (
        repos.session.query(models.Job.id, models.Job.source)
        .filter(models.Job.id.in_(repos.session.query(models.ApplicationRow.job_id)))
        .all()
    )
    return {job_id: str(source) for job_id, source in rows}


def _is_manual(
    application: Any,
    registry: AdapterRegistry,
    sources: dict[str, str],
    repos: Any,
) -> bool:
    """Whether this application is opened for the owner instead of filled by an adapter.

    Asks the same registry `submit` uses (adapters are chosen by source kind), so the
    label on the page and what Apply does cannot disagree.
    """

    if application is None:
        return False
    source = sources.get(application.job_id)
    if source is None:
        with suppress(NotFound):
            source = str(repos.jobs.get(application.job_id).source)
    if source is None:
        return False
    return registry.needs_manual(SimpleNamespace(source=source, source_url=None))


def _annotate_package(
    repos: Any,
    row: Any,
    *,
    dry_run: bool,
    registry: AdapterRegistry | None = None,
    sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    summary = package_summary(row)
    opportunity = repos.opportunities.get(row.opportunity_id)
    application = repos.applications.for_opportunity(row.opportunity_id)
    payload = dict(row.payload or {})
    summary["title"] = opportunity.title
    summary["company"] = opportunity.company
    summary["application_id"] = application.id if application else None
    summary["application_status"] = application.status if application else None
    # An approved application on a source with no adapter (Ashby, a company careers page)
    # is opened in the owner's browser to fill; the page says so instead of "Apply".
    manual_handoff = _is_manual(
        application,
        registry if registry is not None else default_registry(dry_run=True),
        sources if sources is not None else {},
        repos,
    )
    summary["manual_handoff"] = manual_handoff
    summary["approvable"] = bool(
        row.status == PackageStatus.COMPLETE.value
        and row.truth_clean
        and application is not None
        and application.status == ApplicationStatus.AWAITING_APPROVAL.value
    )
    # The same buckets the Today view counts, so a count links to exactly its rows.
    application_status = application.status if application else None
    if summary["approvable"]:
        summary["attention"] = "ready"
    elif row.status == PackageStatus.INCOMPLETE.value and application_status in {
        ApplicationStatus.DRAFT.value,
        ApplicationStatus.AWAITING_APPROVAL.value,
    }:
        summary["attention"] = "needs_information"
    else:
        # "handoffs" (an APPLYING row that is waiting for the person) needs the live
        # handoff state, which `_package_rows` adds; see there.
        summary["attention"] = None
    summary["cover_letter"] = payload.get("cover_letter")
    summary["hiring_manager_message"] = payload.get("hiring_manager_message")
    summary["tailored_resume_markdown"] = payload.get("tailored_resume_markdown")
    summary["candidate_summary"] = payload.get("candidate_summary")
    summary["unresolved_questions"] = list(payload.get("unresolved_questions") or [])
    if application is not None and application.status == ApplicationStatus.APPROVED.value:
        summary["review_label"] = "Approved"
        if manual_handoff:
            summary["next_step"] = (
                "Artemis cannot fill this employer's form. "
                + (
                    "Dry run is on, so this stays off until SABRA_AUTOMATION_DRY_RUN=false. "
                    if dry_run
                    else ""
                )
                + "Open it in your browser: Artemis saves your résumé and cover letter as PDFs, "
                "shows where, and types nothing. You fill and submit it, then report the result."
            )
        else:
            summary["next_step"] = (
                "Dry run is on, so Apply stays off until SABRA_AUTOMATION_DRY_RUN=false. "
                "Then confirm Apply. You solve any CAPTCHA; the engine does not."
                if dry_run
                else (
                    "Apply. A form with no challenge is submitted. "
                    "A CAPTCHA opens in a headed browser for you to solve."
                )
            )
    elif application is not None and application.status in {
        ApplicationStatus.BLOCKED.value,
        ApplicationStatus.FAILED.value,
    }:
        summary["review_label"] = (
            "Blocked" if application.status == ApplicationStatus.BLOCKED.value else "Failed"
        )
        summary["next_step"] = (
            f"{application.last_error or 'The last attempt did not finish.'} "
            "Nothing is recorded as submitted. Fix the cause, then Apply again. If the cause is a "
            "missing or wrong answer, record and verify it on the Answers page, then press "
            "Refresh package and approve the new revision."
        )
    elif application is not None and application.status == ApplicationStatus.APPLYING.value:
        summary["review_label"] = "Applying"
        summary["next_step"] = (
            "A submission is in progress or waiting for you to report its outcome."
        )
    elif summary["approvable"]:
        summary["review_label"] = "ready to approve"
        summary["next_step"] = "Read the package, then approve it with the others you select."
    else:
        summary["review_label"] = "not approvable"
        summary["next_step"] = None
    return summary


def _package_rows() -> tuple[list[dict[str, Any]], bool]:
    ctx = EngineContext.create()
    try:
        dry_run = ctx.settings.automation_dry_run
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return [], dry_run
            registry = default_registry(dry_run=True)
            sources = _job_sources(repos)
            rows = [
                _annotate_package(repos, row, dry_run=dry_run, registry=registry, sources=sources)
                for row in _latest_packages(repos, candidate.id)
            ]
        # The same rule as the Today count: an APPLYING row is a handoff only while it is
        # waiting for the person's report, not while a run is still going.
        service = _execution_service(ctx)
        for summary in rows:
            if summary["application_status"] == ApplicationStatus.APPLYING.value:
                status = service.handoff_status(summary["application_id"])
                if status["awaiting_person"]:
                    summary["attention"] = "handoffs"
        return rows, dry_run
    finally:
        ctx.close()


@app.get("/api/packages")
def package_list() -> dict[str, Any]:
    rows, dry_run = _package_rows()
    return {"packages": rows, "automation_dry_run": dry_run}


@app.get("/api/packages/{package_id}")
def package_detail(package_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            row = repos.packages.get(package_id)
            return _annotate_package(repos, row, dry_run=ctx.settings.automation_dry_run)
    finally:
        ctx.close()


@app.get("/api/packages/{package_id}/review")
def package_review(package_id: str) -> dict[str, Any]:
    """Everything the review page shows, in one payload (PRD section 31)."""

    ctx = EngineContext.create()
    try:
        return ApprovalService(ctx).review(package_id)
    finally:
        ctx.close()


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@app.post("/api/packages/{package_id}/reject")
def reject_package(package_id: str, body: RejectRequest | None = None) -> dict[str, Any]:
    """The owner rejects the application's current package. Nothing is submitted or deleted."""

    ctx = EngineContext.create()
    try:
        outcome = ApprovalService(ctx).reject(
            package_id, decided_by="owner", reason=body.reason if body else None
        )
        return outcome.as_dict()
    finally:
        ctx.close()


class ApplyRequest(BaseModel):
    confirm: bool = False


class HandoffOutcomeRequest(BaseModel):
    outcome: Literal["submitted", "abandoned", "failed"]


class ForcedHandoffRequest(BaseModel):
    outcome: Literal["submitted", "abandoned", "failed"]
    confirm_id: str


def _execution_service(ctx: EngineContext) -> ExecutionService:
    """The submission service for a request.

    A challenge starts the headed browser on a background thread (the request must not
    wait on a person), and that thread builds its own engine context because this
    request's context is closed when the request ends.
    """

    return ExecutionService(ctx, background_handoff=True, ctx_factory=EngineContext.create)


@app.post("/api/applications/refresh-unsubmitted")
def refresh_unsubmitted(body: ApplyRequest) -> dict[str, Any]:
    """Revoke approval and rebuild every approved application that has not been submitted."""

    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="Confirm before refreshing unsubmitted applications. Nothing was changed.",
        )
    ctx = EngineContext.create()
    try:
        return PackageRefreshService(ctx).refresh_all_unsubmitted().as_dict()
    finally:
        ctx.close()


@app.post("/api/applications/{application_id}/refresh")
def refresh_application(application_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return PackageRefreshService(ctx).refresh(application_id).as_dict()
    finally:
        ctx.close()


@app.post("/api/applications/{application_id}/apply")
def apply_application(application_id: str, body: ApplyRequest) -> dict[str, Any]:
    """Run the same submission path as `sabra apply`.

    Refused while dry-run is on, and refused without an explicit confirm.
    A CAPTCHA is never solved here. When a challenge is detected the headed browser is
    started in the background and this returns at once with outcome HANDED_TO_HUMAN;
    the person then reports the result with `POST .../handoff/outcome`.
    """

    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="Confirm the submission before applying. Nothing was opened or submitted.",
        )
    ctx = EngineContext.create()
    try:
        if ctx.settings.automation_dry_run:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Dry run is on. Set SABRA_AUTOMATION_DRY_RUN=false to apply. "
                    "Nothing was opened or submitted."
                ),
            )
        result = _execution_service(ctx).submit(application_id, allow_real_submission=True)
        return result.as_dict()
    finally:
        ctx.close()


@app.get("/api/applications/{application_id}/handoff")
def handoff_status(application_id: str) -> dict[str, Any]:
    """Live status of the human handoff for one application."""

    ctx = EngineContext.create()
    try:
        return _execution_service(ctx).handoff_status(application_id)
    finally:
        ctx.close()


@app.post("/api/applications/{application_id}/handoff/outcome")
def handoff_outcome(application_id: str, body: HandoffOutcomeRequest) -> dict[str, Any]:
    """The person reports how the handoff ended: submitted, abandoned or failed."""

    ctx = EngineContext.create()
    try:
        return _execution_service(ctx).report_handoff_outcome(application_id, body.outcome)
    finally:
        ctx.close()


@app.post("/api/applications/{application_id}/handoff/force")
def handoff_force(application_id: str, body: ForcedHandoffRequest) -> dict[str, Any]:
    """Record a report for a row stuck APPLYING after a hard kill.

    Fail-closed: the id must be typed back, nothing may be registered for it in this
    process, and the row must have been silent for longer than the handoff timeout. The
    report is recorded as forced.
    """

    ctx = EngineContext.create()
    try:
        return _execution_service(ctx).force_report_handoff_outcome(
            application_id, body.outcome, confirm_id=body.confirm_id, require_stale=True
        )
    finally:
        ctx.close()


@app.get("/api/answer-keys")
def answer_keys() -> dict[str, Any]:
    return {"keys": answer_key_choices()}


@app.get("/api/answers")
def answer_list() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return {"answers": AnswerMemoryService(ctx).list_answers()}
    except SabraError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        ctx.close()


@app.post("/api/answers/record")
def answer_record(body: AnswerWrite) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return AnswerMemoryService(ctx).record(body.key, body.value, question_text=body.question_text)
    except SabraError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        ctx.close()


@app.post("/api/answers/verify")
def answer_verify(body: AnswerVerify) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return AnswerMemoryService(ctx).verify(body.key, expected_value=body.value)
    except SabraError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        ctx.close()


@app.post("/api/packages/approve")
def approve_packages(body: BatchApprovalRequest) -> dict[str, Any]:
    """Approve the selected COMPLETE packages. Each approval is bound to the hash sent."""

    ctx = EngineContext.create()
    try:
        result = ApprovalService(ctx).approve_selected(
            [item.model_dump() for item in body.packages],
            approved_by=body.approved_by,
        )
        return result.as_dict()
    finally:
        ctx.close()


@app.post("/api/opportunities/{opportunity_id}/prepare")
def prepare_opportunity(opportunity_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return DocumentService(ctx).prepare_package(opportunity_id).as_dict()
    finally:
        ctx.close()


@app.post("/api/opportunities/{opportunity_id}/pass")
def pass_on_opportunity(opportunity_id: str) -> dict[str, Any]:
    """Pass on a role: REJECTED with reason OWNER_PASSED, never brought back by ranking."""

    ctx = EngineContext.create()
    try:
        return pass_opportunity(ctx, opportunity_id)
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Pipeline, ledger, outcomes and the learning dataset
# ---------------------------------------------------------------------------


@app.get("/api/pipeline")
def pipeline() -> dict[str, Any]:
    """Every application past approval, grouped by stage (PRD sections 22-24)."""

    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            candidate_id = candidate.id if candidate else None
        outcome_kinds = [{"kind": kind, "label": label} for kind, label in OUTCOME_LABELS.items()]
        if candidate_id is None:
            return {"candidate_id": None, "board": [], "counts": {}, "outcome_kinds": outcome_kinds}
        payload = LedgerService(ctx).pipeline(candidate_id).as_dict()
        payload["outcome_kinds"] = outcome_kinds
        return payload
    finally:
        ctx.close()


@app.get("/api/applications/{application_id}/ledger")
def application_ledger(application_id: str) -> dict[str, Any]:
    """The durable application record: package, answers, submission, events and outcomes."""

    ctx = EngineContext.create()
    try:
        return LedgerService(ctx).application_ledger(application_id).model_dump(mode="json")
    finally:
        ctx.close()


class OutcomeRequest(BaseModel):
    kind: str = Field(max_length=40)
    # ISO date or datetime. Left out, it is now; a date or time with no zone is local time.
    occurred_at: str | None = None
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    contact: str | None = Field(default=None, max_length=CONTACT_MAX)


@app.post("/api/applications/{application_id}/outcomes")
def record_application_outcome(application_id: str, body: OutcomeRequest) -> dict[str, Any]:
    """Record what an employer did. The lifecycle moves through the transition graph."""

    ctx = EngineContext.create()
    try:
        return LedgerService(ctx).record_outcome(
            application_id,
            body.kind,
            notes=body.note,
            occurred_at=parse_occurred_at(body.occurred_at),
            contact=body.contact,
        )
    finally:
        ctx.close()


@app.get("/api/learning-records")
def learning_records() -> Response:
    """The learning dataset (PRD section 25) as JSON lines, with every identity field removed."""

    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            candidate_id = candidate.id if candidate else None
        text = LedgerService(ctx).learning_jsonl(candidate_id) if candidate_id else ""
    finally:
        ctx.close()
    return Response(
        content=text,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="sabra-learning-records.jsonl"'},
    )


# ---------------------------------------------------------------------------
# Search profiles
# ---------------------------------------------------------------------------


def _search_summary(profile: SearchProfile, active_id: str | None) -> dict[str, Any]:
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


def _invalid_search(exc: ValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": "The search has errors: "
            + "; ".join(f"{e['field']}: {e['message']}" for e in format_validation_errors(exc)),
            "errors": format_validation_errors(exc),
        },
    )


@app.get("/api/searches")
def search_list() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return {"active_id": None, "searches": []}
            active = repos.search_profiles.active_one(candidate.id)
            active_id = active.id if active else None
            return {
                "active_id": active_id,
                "searches": [
                    _search_summary(SearchProfile.model_validate(row.payload), active_id)
                    for row in repos.search_profiles.list_for_candidate(candidate.id)
                ],
            }
    finally:
        ctx.close()


@app.get("/api/searches/{profile_id}")
def search_detail(profile_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            profile = repos.search_profiles.get(profile_id)
            active = repos.search_profiles.active_one(profile.candidate_id)
            return {
                "profile": profile.model_dump(mode="json"),
                "active": bool(active and active.id == profile_id),
            }
    finally:
        ctx.close()


@app.put("/api/searches/{profile_id}", response_model=None)
def search_replace(profile_id: str, body: dict[str, Any]) -> Any:
    """Replace a search profile in full, validated through the schema.

    `id`, `candidate_id`, `is_active` and `created_at` are not the person's to change here
    (activation has its own call); everything else is taken from the body as sent, so a
    field left out falls back to its default. A bad body is a 422 naming each field.
    """

    ctx = EngineContext.create()
    try:
        with ctx.repos() as repos:
            stored = repos.search_profiles.get(profile_id)
            if body.get("id") not in (None, "", profile_id):
                raise HTTPException(
                    status_code=422, detail="The id in the body does not match the id in the URL."
                )
            raw = dict(body)
            raw.update(
                id=profile_id,
                candidate_id=stored.candidate_id,
                is_active=stored.is_active,
                created_at=stored.created_at.isoformat() if stored.created_at else None,
            )
            try:
                profile = SearchProfile.model_validate(raw)
            except ValidationError as exc:
                return _invalid_search(exc)
            changed = sorted(
                key
                for key, value in profile.model_dump(mode="json").items()
                if value != stored.model_dump(mode="json").get(key)
            )
            repos.search_profiles.upsert(profile)
            repos.events.append(
                EntityType.SEARCH_PROFILE,
                profile_id,
                EventType.SEARCH_PROFILE_UPDATED,
                {"name": profile.name, "changed_fields": changed},
                actor="owner",
            )
            return {"profile": profile.model_dump(mode="json"), "changed_fields": changed}
    finally:
        ctx.close()


@app.post("/api/searches", status_code=201, response_model=None)
def search_create(body: dict[str, Any]) -> Any:
    """Create a search profile. It becomes active only if the candidate has no active one."""

    ctx = EngineContext.create()
    try:
        candidate_id = ensure_candidate(ctx, display_name=None, create_placeholder=True)
        raw = dict(body)
        raw["id"] = str(raw.get("id") or new_id("srch"))
        raw["candidate_id"] = candidate_id
        raw["is_active"] = False
        raw.pop("created_at", None)
        try:
            profile = SearchProfile.model_validate(raw)
        except ValidationError as exc:
            return _invalid_search(exc)
        profile.created_at = datetime.now(UTC)
        with ctx.read_repos() as repos:
            if repos.search_profiles.exists(profile.id):
                raise HTTPException(
                    status_code=409, detail=f"A search with id {profile.id!r} already exists."
                )
        ensure_search_profile(ctx, candidate_id, profile=profile)
        with ctx.read_repos() as repos:
            stored = repos.search_profiles.get(profile.id)
        return {"profile": stored.model_dump(mode="json"), "active": stored.is_active}
    finally:
        ctx.close()


@app.post("/api/searches/{profile_id}/activate")
def search_activate(profile_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                raise NotFound("candidate", "primary")
            profile = repos.search_profiles.activate(candidate.id, profile_id)
            repos.events.append(
                EntityType.SEARCH_PROFILE,
                profile_id,
                EventType.SEARCH_PROFILE_ACTIVATED,
                {"name": profile.name},
                actor="owner",
            )
            return {"active_id": profile_id, "profile": profile.model_dump(mode="json")}
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    kind: Literal["discover", "rank", "full"] = "full"
    prepare: bool = False
    # Skip boards read successfully within the last N hours (default 20 when true).
    only_new: bool = False


@app.post("/api/runs", status_code=202)
def run_start(body: RunRequest) -> dict[str, Any]:
    """Start a run on a background thread. 409 while another run is active."""

    hours = get_settings().discovery_fresh_hours if body.only_new else None
    return RunService.start_background(
        body.kind, body.prepare, ctx_factory=EngineContext.create, only_new_hours=hours
    )


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return RunService(ctx).get(run_id)
    finally:
        ctx.close()


@app.get("/api/runs")
def run_list(limit: int = 20) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return {"runs": RunService(ctx).list_recent(max(1, min(limit, 200)))}
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------

LAST_SEEN_KEY = "today.last_seen_at"
# Outcome kinds that are an employer answering. A withdrawal or "no response" is not one.
NON_RESPONSE_OUTCOMES = {"WITHDRAWAL", "NO_RESPONSE"}


def _parse_when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _today_payload() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        runs = RunService(ctx).list_recent(10)
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            payload: dict[str, Any] = {
                "generated_at": datetime.now(UTC).isoformat(),
                "candidate_id": candidate.id if candidate else None,
                "automation_dry_run": ctx.settings.automation_dry_run,
                "has_resume": False,
                "search_profile_id": None,
                "search_profile_name": None,
                "counts": {
                    "ready_for_approval": 0,
                    "needs_information": 0,
                    "handoffs_waiting": 0,
                    "new_high_fit": 0,
                    "employer_responses": 0,
                },
                "open_questions": 0,
                "last_seen_at": None,
                "active_run": next(
                    (r for r in runs if r["status"] in ("QUEUED", "RUNNING")), None
                ),
                "last_run": next(
                    (r for r in runs if r["status"] in ("COMPLETED", "FAILED")), None
                ),
            }
            if candidate is None:
                return payload
            counts = payload["counts"]
            payload["has_resume"] = repos.resumes.master(candidate.id) is not None
            active = repos.search_profiles.active_one(candidate.id)
            if active is not None:
                payload["search_profile_id"] = active.id
                payload["search_profile_name"] = active.name

            open_questions = 0
            for row in _latest_packages(repos, candidate.id):
                application = repos.applications.for_opportunity(row.opportunity_id)
                if application is None:
                    continue
                if (
                    row.status == PackageStatus.COMPLETE.value
                    and row.truth_clean
                    and application.status == ApplicationStatus.AWAITING_APPROVAL.value
                ):
                    counts["ready_for_approval"] += 1
                elif row.status == PackageStatus.INCOMPLETE.value and application.status in {
                    ApplicationStatus.DRAFT.value,
                    ApplicationStatus.AWAITING_APPROVAL.value,
                }:
                    counts["needs_information"] += 1
                    open_questions += row.unresolved_count or 0
            payload["open_questions"] = open_questions

            applying = [
                row.id
                for row in repos.applications.list_for_candidate(candidate.id)
                if row.status == ApplicationStatus.APPLYING.value
            ]

            # "New" means shortlisted since the last finished run began (before any run,
            # every shortlisted role). It is the JOB_SHORTLISTED event that counts, not the
            # opportunity's creation: a discover-only run creates opportunities and a later
            # rank run shortlists them, and that rank run's finds are the new ones.
            baseline = repos.pipeline_runs.latest_finished(started_only=True)
            since = baseline.started_at if baseline is not None else None
            shortlisted_since: set[str] | None = None
            if since is not None:
                shortlisted_since = {
                    row.entity_id
                    for row in repos.session.query(models.EventRow).filter(
                        models.EventRow.event_type == EventType.JOB_SHORTLISTED.value,
                        models.EventRow.entity_type == EntityType.OPPORTUNITY.value,
                        models.EventRow.occurred_at > since,
                    )
                }
            counts["new_high_fit"] = sum(
                1
                for item in repos.opportunities.shortlist(candidate.id)
                if item.status != "REJECTED"
                and (active is None or (item.search_profile_id or "") == active.id)
                and (shortlisted_since is None or item.id in shortlisted_since)
            )

            seen = repos.app_state.get(LAST_SEEN_KEY) or {}
            seen_at = _parse_when(seen.get("at"))
            payload["last_seen_at"] = seen_at.isoformat() if seen_at else None
            counts["employer_responses"] = sum(
                1
                for outcome in repos.outcomes.recorded_since(seen_at, candidate.id)
                if outcome.kind not in NON_RESPONSE_OUTCOMES
            )
        service = _execution_service(ctx)
        counts["handoffs_waiting"] = sum(
            1 for application_id in applying if service.handoff_status(application_id)["awaiting_person"]
        )
        return payload
    finally:
        ctx.close()


@app.get("/api/today")
def today() -> dict[str, Any]:
    """What requires attention: counts with the pages that act on them."""

    return _today_payload()


@app.post("/api/today/seen")
def today_seen() -> dict[str, Any]:
    """Record that the owner has looked at the employer responses so far."""

    ctx = EngineContext.create()
    try:
        now = datetime.now(UTC).isoformat()
        with ctx.repos() as repos:
            repos.app_state.set(LAST_SEEN_KEY, {"at": now})
        return {"last_seen_at": now}
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Home, Research, Career Plan and Ask Artemis
# ---------------------------------------------------------------------------


def _shortlist_rows(repos: Any, candidate_id: str) -> list[Any]:
    """The shortlist in rank order (unranked rows last)."""

    rows = list(repos.opportunities.shortlist(candidate_id))
    return sorted(
        rows,
        key=lambda item: (item.rank_position is None, item.rank_position or 0),
    )


def _opportunity_card(repos: Any, item: Any) -> dict[str, Any]:
    jobs = repos.opportunities.list_jobs(item.id)
    job = jobs[0] if jobs else None
    fit = item.fit_assessment
    return {
        "id": item.id,
        "title": item.title,
        "company": item.company,
        "location": item.location or (job.location if job else None),
        "status": item.status,
        "rank_position": item.rank_position,
        "fit_score": fit.fit_score if fit else None,
        "confidence": fit.confidence if fit else None,
        "why_this_job": fit.why_this_job if fit else None,
        "compensation_text": _compensation_text(job),
        "industry": job.department if job else None,
        "arrangement": (job.remote_status or "").replace("_", " ").title() if job else None,
    }


def _home_payload() -> dict[str, Any]:
    """Everything the Home dashboard shows, all of it real engine data."""

    today = _today_payload()
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_name": None,
        "automation_dry_run": today["automation_dry_run"],
        "search_profile_name": today["search_profile_name"],
        "metrics": {"matches": 0, "applications": 0, "interviews": 0, "offers": 0},
        "featured": None,
        "recent": [],
        "progress": {"shortlisted": 0, "applied": 0, "interviews": 0, "offers": 0},
        "today": today,
    }
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return payload
            payload["candidate_name"] = candidate.display_name
            active = repos.search_profiles.active_one(candidate.id)
            shortlist = [
                item
                for item in _shortlist_rows(repos, candidate.id)
                if item.status != "REJECTED"
                and (active is None or (item.search_profile_id or "") == active.id)
            ]
            cards = [_opportunity_card(repos, item) for item in shortlist]
            payload["metrics"]["matches"] = len(cards)
            payload["progress"]["shortlisted"] = len(cards)
            payload["featured"] = cards[0] if cards else None
            payload["recent"] = cards[1:4] if len(cards) > 1 else cards
            app_counts = repos.applications.count_by_status(candidate.id)
            payload["metrics"]["applications"] = sum(app_counts.values())
            stages = LedgerService(ctx).pipeline(candidate.id).stages
            payload["progress"]["applied"] = stages.get("applied", 0)
            payload["progress"]["interviews"] = stages.get("interview", 0)
            payload["progress"]["offers"] = stages.get("offer", 0)
            payload["metrics"]["interviews"] = stages.get("interview", 0)
            payload["metrics"]["offers"] = stages.get("offer", 0)
        return payload
    finally:
        ctx.close()


@app.get("/api/home")
def home() -> dict[str, Any]:
    return _home_payload()


def _research_payload() -> dict[str, Any]:
    """Company research packets for the candidate's opportunities, with sources."""

    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return {"research": []}
            rows = (
                repos.session.query(models.ResearchPacketRow, models.Opportunity)
                .join(
                    models.Opportunity,
                    models.Opportunity.id == models.ResearchPacketRow.opportunity_id,
                )
                .filter(models.Opportunity.candidate_id == candidate.id)
                .order_by(models.ResearchPacketRow.updated_at.desc())
                .all()
            )
            def texts(body: dict[str, Any], key: str) -> list[str]:
                value = body.get(key) or []
                return [str(item) for item in value] if isinstance(value, list) else []

            out = []
            for packet, opportunity in rows:
                body = dict(packet.payload or {})
                out.append(
                    {
                        "opportunity_id": opportunity.id,
                        "title": opportunity.title,
                        "company": opportunity.company,
                        "engine": packet.engine,
                        "updated_at": packet.updated_at.isoformat() if packet.updated_at else None,
                        "company_description": body.get("company_description"),
                        "company_website": body.get("company_website"),
                        "industry": body.get("industry"),
                        "verified_facts": texts(body, "verified_facts"),
                        "inferences": texts(body, "inferences"),
                        "recent_developments": texts(body, "recent_developments"),
                        "sources": texts(body, "sources"),
                    }
                )
            return {"research": out}
    finally:
        ctx.close()


@app.get("/api/research")
def research() -> dict[str, Any]:
    return _research_payload()


def _career_plan_payload() -> dict[str, Any]:
    """The active search as a career objective, plus gaps, progress and next actions."""

    today = _today_payload()
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return {
                    "search": None,
                    "search_summary": None,
                    "skill_gaps": [],
                    "progress": {},
                    "actions": [{"label": "Create your profile", "href": "/candidate"}],
                }
            active = repos.search_profiles.active_one(candidate.id)
            shortlist = [
                item
                for item in repos.opportunities.shortlist(candidate.id)
                if item.status != "REJECTED"
                and (active is None or (item.search_profile_id or "") == active.id)
            ]
            gap_counts: dict[str, int] = {}
            for item in shortlist:
                fit = item.fit_assessment
                for gap in (fit.gaps if fit and fit.gaps else []):
                    key = " ".join(str(gap).split())
                    gap_counts[key] = gap_counts.get(key, 0) + 1
            gaps = [
                {"gap": gap, "count": count}
                for gap, count in sorted(
                    gap_counts.items(), key=lambda pair: (-pair[1], pair[0])
                )[:8]
            ]
            prepared = sum(
                1
                for row in _latest_packages(repos, candidate.id)
                if row.status
                in {
                    PackageStatus.COMPLETE.value,
                    PackageStatus.AWAITING_APPROVAL.value,
                    PackageStatus.APPROVED.value,
                }
            )
            search_payload = None
            search_summary = None
            if active is not None:
                search_payload = active.model_dump(mode="json")
                comp = active.compensation
                places = [
                    *active.locations.cities,
                    *active.locations.metro_areas,
                    *active.locations.states,
                ]
                work = [
                    name
                    for name, on in (
                        ("remote", active.locations.allow_remote),
                        ("hybrid", active.locations.allow_hybrid),
                        ("on site", active.locations.allow_onsite),
                    )
                    if on
                ]
                parts = []
                if comp.minimum or comp.target:
                    parts.append(
                        "Pay: "
                        + " / ".join(
                            f"{comp.currency or 'USD'} {int(value):,}"
                            for value in (comp.minimum, comp.target)
                            if value
                        )
                    )
                if places:
                    parts.append("Where: " + ", ".join(places))
                if work:
                    parts.append("Arrangement: " + ", ".join(work))
                search_summary = " · ".join(parts)
            answers = AnswerMemoryService(ctx).list_answers()
            unverified = sum(1 for answer in answers if not answer.get("verified"))
            actions: list[dict[str, str]] = []
            if repos.resumes.master(candidate.id) is None:
                actions.append(
                    {"label": "Upload your master résumé so packages can be prepared",
                     "href": "/candidate"}
                )
            if active is None:
                actions.append(
                    {"label": "Define the search for the role you want", "href": "/searches"}
                )
            if unverified:
                actions.append(
                    {
                        "label": f"Verify {unverified} recorded answer(s) so forms can use them",
                        "href": "/answers",
                    }
                )
            ready = today["counts"].get("ready_for_approval", 0)
            if ready:
                actions.append(
                    {
                        "label": f"Review and approve {ready} prepared application(s)",
                        "href": "/applications?filter=ready",
                    }
                )
            if not shortlist and active is not None:
                actions.append({"label": "Run a search to build your shortlist", "href": "/"})
            return {
                "search": search_payload,
                "search_summary": search_summary,
                "skill_gaps": gaps,
                "progress": {
                    "shortlisted": len(shortlist),
                    "prepared": prepared,
                },
                "actions": actions,
            }
    finally:
        ctx.close()


@app.get("/api/career-plan")
def career_plan() -> dict[str, Any]:
    return _career_plan_payload()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@app.post("/api/ask")
def ask_velora(body: AskRequest) -> dict[str, Any]:
    """Ask Artemis. Answers are computed from the engine's real data — never invented.

    This is deterministic intent routing, not a simulated model: when no language model
    is configured the answer says so and still reports the real numbers.
    """

    question = body.question.lower()
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return {
                    "answer": "There is no candidate profile yet. Create your profile and "
                    "upload your résumé first, then I can help.",
                    "links": [{"label": "Open your profile", "href": "/candidate"}],
                }
            home = _home_payload()
            counts = home["today"]["counts"]
            llm_note = (
                ""
                if ctx.settings.llm_provider not in ("", "none")
                else "\n\n(answered from your data — no language model is configured)"
            )
            if any(word in question for word in ("prioritize", "today", "attention", "next")):
                parts = []
                if counts.get("ready_for_approval"):
                    parts.append(f"{counts['ready_for_approval']} application(s) are ready for your review")
                if counts.get("needs_information"):
                    parts.append(
                        f"{counts['needs_information']} application(s) need more information"
                    )
                if counts.get("handoffs_waiting"):
                    parts.append(
                        f"{counts['handoffs_waiting']} submission(s) are waiting for your report"
                    )
                if counts.get("employer_responses"):
                    parts.append(f"{counts['employer_responses']} employer response(s) are unread")
                if counts.get("new_high_fit"):
                    parts.append(f"{counts['new_high_fit']} new high-fit role(s) came in")
                answer = (
                    "In order: " + "; ".join(parts) + "."
                    if parts
                    else "Nothing needs your attention right now. A run will surface new work."
                )
                return {"answer": answer + llm_note, "links": [{"label": "Open Home", "href": "/"}]}
            if any(word in question for word in ("match", "find role", "roles", "opportunit")):
                cards = [home["featured"], *home["recent"]]
                seen: set[str] = set()
                top = []
                for card in cards:
                    if card and card["id"] not in seen:
                        seen.add(card["id"])
                        top.append(card)
                if not top:
                    answer = "Your shortlist is empty. Run a search and I will rank what comes back."
                    links = [{"label": "Run a search", "href": "/"}]
                else:
                    lines = ["Your strongest matches right now:"]
                    for card in top[:5]:
                        fit = (
                            f" — {round(card['fit_score'] * 100)}% fit"
                            if card.get("fit_score") is not None
                            else ""
                        )
                        lines.append(f"• {card['title']} at {card['company']}{fit}")
                    answer = "\n".join(lines)
                    links = [{"label": "Open Opportunities", "href": "/opportunities"}]
                return {"answer": answer + llm_note, "links": links}
            if "why" in question and any(
                word in question for word in ("fit", "position", "job", "role")
            ):
                featured = home["featured"]
                if featured and featured.get("why_this_job"):
                    answer = (
                        f"{featured['title']} at {featured['company']}: "
                        + featured["why_this_job"]
                    )
                    links = [
                        {
                            "label": "Read the full analysis",
                            "href": f"/opportunities/{featured['id']}",
                        }
                    ]
                elif featured:
                    answer = (
                        f"{featured['title']} at {featured['company']} leads your shortlist, "
                        "but no fit explanation is recorded yet."
                    )
                    links = [{"label": "Open the opportunity", "href": f"/opportunities/{featured['id']}"}]
                else:
                    answer = "There is no scored opportunity yet. Run a search first."
                    links = [{"label": "Run a search", "href": "/"}]
                return {"answer": answer + llm_note, "links": links}
            if any(word in question for word in ("tailor", "resume", "résumé", "cover letter")):
                featured = home["featured"]
                if featured:
                    answer = (
                        f"Open {featured['title']} at {featured['company']} and press "
                        "Prepare application. The package is built only from facts your profile "
                        "supports, and nothing is submitted without your approval."
                    )
                    links = [
                        {"label": "Tailor for the top match", "href": f"/opportunities/{featured['id']}"}
                    ]
                else:
                    answer = "Run a search first; then I can tailor your résumé for a real role."
                    links = [{"label": "Run a search", "href": "/"}]
                return {"answer": answer + llm_note, "links": links}
            if any(word in question for word in ("research", "company", "companies")):
                research_rows = _research_payload()["research"]
                if research_rows:
                    names = ", ".join(dict.fromkeys(r["company"] for r in research_rows[:6]))
                    answer = f"I have researched: {names}. Facts and inferences are kept apart, with sources."
                else:
                    answer = "No company research yet. Research runs on your shortlist at the end of each run."
                return {
                    "answer": answer + llm_note,
                    "links": [{"label": "Open Research", "href": "/research"}],
                }
            if "interview" in question:
                interviews = home["metrics"]["interviews"]
                if interviews:
                    answer = (
                        f"You have {interviews} application(s) at interview stage. Interview "
                        "preparation notes live on each application's review page."
                    )
                else:
                    answer = (
                        "No interviews yet. When one comes in, preparation notes are on the "
                        "application's review page."
                    )
                return {
                    "answer": answer + llm_note,
                    "links": [{"label": "Open the pipeline", "href": "/pipeline"}],
                }
            metrics = home["metrics"]
            answer = (
                f"Right now: {metrics['matches']} matches, {metrics['applications']} applications, "
                f"{metrics['interviews']} interviews, {metrics['offers']} offers. "
                "I can find roles, explain a fit, point you to research, or tell you what to do next."
            )
            return {"answer": answer + llm_note, "links": [{"label": "Open Home", "href": "/"}]}
    finally:
        ctx.close()


_BRAND_DIR = REPO_ROOT / "brand"


@app.get("/favicon.svg")
def favicon() -> Response:
    """The Artemis mark as the favicon (served from the brand kit when present)."""

    candidate = _BRAND_DIR / "logo" / "artemis-mark.svg"
    if candidate.is_file():
        return Response(candidate.read_text(encoding="utf-8"), media_type="image/svg+xml")
    return Response(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
        '<path d="M5.5 5 L14.5 4 L29.5 44.5 L23 48 Z" fill="#718C9A"/>'
        '<path d="M42.5 5 L33.5 4 L18.5 44.5 L25 48 Z" fill="#A7C3D5" fill-opacity="0.8"/>'
        "</svg>",
        media_type="image/svg+xml",
    )


# ---------------------------------------------------------------------------
# Settings (read only)
# ---------------------------------------------------------------------------


def _app_version() -> str:
    import tomllib

    try:
        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except Exception:
        pass
    try:
        from importlib.metadata import version

        return version("sabra-job-engine")
    except Exception:
        return "unknown"


def _playwright_status() -> dict[str, Any]:
    """Whether Playwright and its Chromium are installed. Nothing is launched."""

    import importlib.util
    import os
    import sys

    package = importlib.util.find_spec("playwright") is not None
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured and configured != "0":
        base = Path(configured)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        base = Path.home() / ".cache" / "ms-playwright"
    chromium = False
    try:
        chromium = base.is_dir() and any(
            child.name.startswith("chromium") for child in base.iterdir()
        )
    except OSError:
        chromium = False
    return {
        "package_installed": package,
        "chromium_installed": chromium,
        "browsers_path": str(base),
    }


@app.get("/api/settings")
def settings_view() -> dict[str, Any]:
    from sqlalchemy.engine import make_url

    ctx = EngineContext.create()
    try:
        settings = ctx.settings
        try:
            database_url = make_url(settings.database_url).render_as_string(hide_password=True)
        except Exception:
            database_url = settings.database_url
        profile_id = profile_name = None
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            active = repos.search_profiles.active_one(candidate.id) if candidate else None
            if active is not None:
                profile_id, profile_name = active.id, active.name
        return {
            "automation_dry_run": settings.automation_dry_run,
            "dry_run_env_var": "SABRA_AUTOMATION_DRY_RUN",
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "database_url": database_url,
            "private_dir": str(settings.private_dir) if settings.private_dir else None,
            "data_dir": str(settings.data_dir),
            "artifacts_dir": str(settings.artifacts_dir),
            "handoff_timeout_seconds": settings.handoff_timeout_seconds,
            "playwright": _playwright_status(),
            "active_search_profile_id": profile_id,
            "active_search_profile_name": profile_name,
            "sources_enabled": len(ctx.registry.enabled),
            "version": _app_version(),
        }
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Job boards (the source registry)
# ---------------------------------------------------------------------------


class ProbeRequest(BaseModel):
    url_or_token: str = Field(min_length=1, max_length=2000)
    kind: str | None = Field(default=None, max_length=32)


class AddSourceRequest(ProbeRequest):
    display_name: str | None = Field(default=None, max_length=256)
    industry: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=2000)


@app.get("/api/sources")
def source_list() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return {"sources": SourcesService(ctx).list_sources()}
    finally:
        ctx.close()


@app.post("/api/sources/probe")
def source_probe(body: ProbeRequest) -> dict[str, Any]:
    """Read a careers URL once and report whether it is a live board. Nothing is saved."""

    ctx = EngineContext.create()
    try:
        return SourcesService(ctx).probe(body.url_or_token, body.kind)
    finally:
        ctx.close()


@app.post("/api/sources", status_code=201)
def source_add(body: AddSourceRequest) -> dict[str, Any]:
    """Probe a board and append it to the registry file. A dead or duplicate board is refused."""

    ctx = EngineContext.create()
    try:
        return SourcesService(ctx).add(
            body.url_or_token,
            body.kind,
            display_name=body.display_name,
            industry=body.industry,
            notes=body.notes,
        )
    finally:
        ctx.close()


@app.post("/api/sources/{source_id}/enable")
def source_enable(source_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return SourcesService(ctx).set_enabled(source_id, True)
    finally:
        ctx.close()


@app.post("/api/sources/{source_id}/disable")
def source_disable(source_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return SourcesService(ctx).set_enabled(source_id, False)
    finally:
        ctx.close()


OPPORTUNITIES_CSS = (
    ".summary{display:flex;gap:14px;flex-wrap:wrap;margin:22px 0}"
    ".stat{background:var(--glass);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);"
    "border:1px solid var(--glass-line);border-radius:var(--radius-sm);padding:14px 20px;"
    "box-shadow:var(--shadow-soft)}.stat strong{display:block;font-size:25px;"
    "font-family:Sora,Inter,sans-serif}"
    ".filters{display:flex;gap:10px;flex-wrap:wrap;background:var(--glass);"
    "backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);"
    "border:1px solid var(--glass-line);border-radius:var(--radius);padding:16px;"
    "margin-bottom:18px;box-shadow:var(--shadow-soft)}"
    ".filters input{border:1px solid var(--silver);border-radius:9px;padding:9px 11px;width:150px;"
    "font:inherit;background:rgba(255,255,255,.75);color:var(--ink)}"
    ".filters button{background:linear-gradient(140deg,#3E5769,var(--ink));color:#fff;border:0;"
    "border-radius:9px;padding:9px 14px;font-weight:600;cursor:pointer;font:inherit}"
    ".filters button.pass{background:var(--glass-soft);color:var(--ink);border:1px solid var(--silver)}"
    ".queue{background:var(--glass);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);"
    "border:1px solid var(--glass-line);border-radius:var(--radius);padding:22px 24px;"
    "box-shadow:var(--shadow)}"
    ".queue h2{font-family:Sora,Inter,sans-serif;font-size:17px;margin:0}"
    ".row{border-top:1px solid var(--line);padding:16px 0;"
    "display:flex;justify-content:space-between;gap:16px}.row:first-child{margin-top:8px}"
    ".score{color:var(--storm);font-weight:700;text-align:right;min-width:170px}"
    ".score button{margin-top:6px;background:linear-gradient(140deg,#3E5769,var(--ink));color:#fff;"
    "border:0;border-radius:9px;padding:8px 13px;font-weight:600;cursor:pointer;font:inherit}"
    ".score button.pass{background:var(--glass-soft);color:var(--ink);border:1px solid var(--silver)}"
    ".card-line{color:var(--muted);font-size:14px}"
    "@media(max-width:700px){.row{flex-direction:column}.score{text-align:left}}"
)

OPPORTUNITIES_BODY = """<main class="wrap"><a href="/">Back to overview</a><h1>Opportunities</h1><p>Ranked roles from the current discovery and intelligence run.</p><section class="summary"><div class="stat"><strong>__OPPORTUNITIES__</strong> opportunities</div><div class="stat"><strong>__SHORTLISTED__</strong> shortlisted</div><div class="stat"><strong>__JOBS__</strong> jobs discovered</div></section><form class="filters" id="filters"><input name="location" placeholder="Location"><input name="min_salary" type="number" placeholder="Min salary"><input name="max_salary" type="number" placeholder="Max salary"><input name="industry" placeholder="Industry"><button type="submit">Filter jobs</button><button type="button" id="clear">Clear</button></form><section class="queue" id="queue"><h2>Shortlist</h2><div class="row"><div><strong>Loading shortlisted opportunities...</strong></div></div></section><p id="error"></p><p id="status"></p><script>__ASK__function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}let all=[];function showError(message){document.querySelector('#status').textContent='';document.querySelector('#error').textContent=message||'The request failed.';}function showStatus(message){document.querySelector('#error').textContent='';document.querySelector('#status').textContent=message||'';}async function prepare(id){const r=await fetch(`/api/opportunities/${encodeURIComponent(id)}/prepare`,{method:'POST'});const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}if(!r.ok){const detail=typeof d.detail==='string'?d.detail:(text||'Preparation failed');showError(detail);return}showStatus(`Package ${d.status}. Unresolved questions: ${(d.unresolved_questions||[]).length}`)}async function pass(id){if(!await ask('Pass on this role? It leaves your list and will not come back.',{confirmLabel:'Pass'}))return;const r=await fetch(`/api/opportunities/${encodeURIComponent(id)}/pass`,{method:'POST'});const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}if(!r.ok){showError(typeof d.detail==='string'?d.detail:(text||'Pass failed'));return}showStatus('Passed. This role will not come back.');all=all.filter(o=>o.id!==id);render(all)}function render(rows){const q=document.querySelector('#queue');q.innerHTML='<h2>Shortlist</h2>'+ (rows.length?rows.map(o=>`<div class="row"><div><strong>${esc(o.rank_position??'')}. <a href="/opportunities/${encodeURIComponent(o.id)}">${esc(o.title)}</a></strong><br><span>${esc(o.company)} · ${esc(o.location||'Location not listed')} · ${esc(o.status)}</span><br><span class="card-line">Pay: ${esc(o.compensation_text||'not listed')} · Confidence: ${o.confidence==null?'not assessed':esc(Math.round(o.confidence*100)+'%')}</span><br><span>${esc(o.why_this_job||o.shortlist_reason||'Shortlisted by the ranking engine.')}</span><br><span class="card-line">Biggest gap: ${esc(o.primary_gap||'none noted')}</span></div><span class="score">${o.fit_score==null?'Fit pending':`Fit ${Math.round(o.fit_score*100)}%`}<br><button type="button" data-id="${esc(o.id)}">Prepare application</button><br><button type="button" class="pass" data-pass="${esc(o.id)}">Pass</button></span></div>`).join(''):'<div class="row"><div><strong>No jobs match those filters.</strong></div></div>');q.querySelectorAll('button[data-id]').forEach(btn=>btn.addEventListener('click',()=>prepare(btn.dataset.id)));q.querySelectorAll('button[data-pass]').forEach(btn=>btn.addEventListener('click',()=>pass(btn.dataset.pass)));}fetch('/api/opportunities').then(async r=>{if(!r.ok)throw new Error(await r.text());return r.json()}).then(d=>{all=d.opportunities;render(all)}).catch(error=>{document.querySelector('#queue').innerHTML='<h2>Shortlist</h2><div class="row"><div><strong>The opportunity queue could not be loaded.</strong></div></div>';showError(error.message)});document.querySelector('#filters').addEventListener('submit',e=>{e.preventDefault();const f=new FormData(e.target),loc=(f.get('location')||'').toString().toLowerCase(),ind=(f.get('industry')||'').toString().toLowerCase(),min=Number(f.get('min_salary'))||0,max=Number(f.get('max_salary'))||Infinity;render(all.filter(o=>(!loc||`${o.location}`.toLowerCase().includes(loc))&&(!ind||`${o.company} ${o.title} ${o.industry??''}`.toLowerCase().includes(ind))&&(!min||o.salary_max>=min)&&(!isFinite(max)||o.salary_min<=max)))});document.querySelector('#clear').onclick=()=>{document.querySelector('#filters').reset();render(all)};</script></main>"""


@app.get("/opportunities", response_class=HTMLResponse)
def opportunities() -> str:
    data = _status_payload()
    body = (
        OPPORTUNITIES_BODY.replace("__OPPORTUNITIES__", str(data["opportunities"]))
        .replace("__SHORTLISTED__", str(data["shortlisted"]))
        .replace("__JOBS__", str(data["jobs_discovered"]))
        .replace("__ASK__", ASK_JS)
    )
    return page_shell("Opportunities", body, active="/opportunities", css=OPPORTUNITIES_CSS)


def _exists(load: Any) -> bool:
    """Whether a lookup by id finds its row (used to give a missing id a real 404 page)."""

    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            load(repos)
        return True
    except NotFound:
        return False
    finally:
        ctx.close()


def _not_found_page(title: str, message: str, back_href: str, back_label: str, active: str) -> HTMLResponse:
    body = (
        f'<main class="wrap"><p><a href="{escape(back_href)}">{escape(back_label)}</a></p>'
        f"<h1>{escape(title)}</h1><p>{escape(message)}</p></main>"
    )
    return HTMLResponse(page_shell(title, body, active=active), status_code=404)


@app.get("/opportunities/{opportunity_id}", response_class=HTMLResponse, response_model=None)
def opportunity_detail_page(opportunity_id: str) -> Any:
    if not _exists(lambda repos: repos.opportunities.get(opportunity_id)):
        return _not_found_page(
            "Opportunity not found",
            "No opportunity has this id. It may belong to a different database.",
            "/opportunities",
            "Back to opportunities",
            "/opportunities",
        )
    return page_shell(
        "Opportunity",
        opportunity_detail_body(opportunity_id),
        active="/opportunities",
        css=DETAIL_CSS,
    )


@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page() -> str:
    return page_shell("Pipeline", pipeline_body(), active="/pipeline", css=DETAIL_CSS + PIPELINE_CSS)


@app.get("/candidate", response_class=HTMLResponse)
def candidate_page() -> str:
    return page_shell("Candidate", CANDIDATE_BODY, active="/candidate")


@app.get("/searches", response_class=HTMLResponse)
def searches_page() -> str:
    return page_shell("Searches", searches_body(), active="/searches")


@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> str:
    return page_shell("Settings", SETTINGS_BODY, active="/settings")


@app.get("/research", response_class=HTMLResponse)
def research_page() -> str:
    return page_shell("Research", RESEARCH_BODY, active="/research", css=RESEARCH_CSS)


@app.get("/career-plan", response_class=HTMLResponse)
def career_plan_page() -> str:
    return page_shell("Career Plan", PLAN_BODY, active="/career-plan", css=PLAN_CSS)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    """Home: a clearer path forward — metrics, the featured opportunity, progress, upcoming."""

    return page_shell("Home", home_body(), active="/", css=HOME_CSS)


@app.get("/applications", response_class=HTMLResponse)
def applications_page() -> str:
    return page_shell(
        "Applications",
        """<main class="wrap"><a href="/">Back to overview</a><h1>Applications</h1>
<p>Select complete packages and approve them together. Each approval is stored against that package's content hash, so a later edit invalidates it. The list shows the latest revision of each application.</p>
<p id="filter-note" class="why"></p>
<form id="batch"><div id="list">Loading packages...</div><button type="submit" id="approve">Approve selected</button> <button type="button" id="refresh-all">Refresh all unsubmitted</button></form>
<dialog id="confirm-refresh">
<p>Refresh every approved, blocked or failed application that has not been submitted? Each approval is revoked. You will need to approve the new packages.</p>
<form method="dialog">
<button type="submit" value="cancel">Cancel</button>
<button type="submit" id="confirm-refresh-yes" value="refresh">Refresh packages</button>
</form>
</dialog>
<p id="result"></p>
<script>
__ASK__function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function readBody(r){const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}return {ok:r.ok,d,text};}
const FILTERS={ready:'ready for approval',needs_information:'needs more information',handoffs:'waiting for your report'};
const FILTER=(()=>{try{const f=new URLSearchParams(location.search).get('filter');return Object.prototype.hasOwnProperty.call(FILTERS,f)?f:'';}catch(e){return '';}})();
async function load(){
  const line=document.querySelector('#result');
  const note=document.querySelector('#filter-note');
  const r=await fetch('/api/packages');
  const body=await readBody(r);
  const list=document.querySelector('#list');
  if(!body.ok){list.textContent='Packages could not be loaded.';line.textContent=body.d.detail||body.text||'Request failed';return;}
  const d=body.d;
  const dry=d.automation_dry_run!==false;
  const everything=(d.packages||[]);
  const rows=FILTER?everything.filter(p=>p.attention===FILTER):everything;
  note.innerHTML=FILTER?`Showing only: ${esc(FILTERS[FILTER])} (${rows.length} of ${everything.length}). <a href="/applications">Show all</a>`:'';
  if(!rows.length){list.textContent=FILTER?'Nothing in this view.':'No packages yet.';return;}
  list.innerHTML=rows.map(p=>{
    const label=p.review_label||(p.approvable?'ready to approve':'not approvable');
    const next=p.next_step?`<span class="why">${esc(p.next_step)}</span>`:'';
    const appId=p.application_id||'';
    const refresh=['APPROVED','BLOCKED','FAILED'].includes(p.application_status)?`<button type="button" class="refresh-one" data-id="${esc(appId)}">Refresh package</button>`:'';
    const canApply=['APPROVED','BLOCKED','FAILED'].includes(p.application_status);
    const manual=!!p.manual_handoff;
    const applyLabel=manual?'Open in browser for you to fill':'Apply';
    const applyAttrs=`class="apply" data-id="${esc(appId)}" data-dry="${dry?'1':'0'}" data-manual="${manual?'1':'0'}" ${dry?'disabled':''}`;
    const applyButton=manual?`<button type="button" ${applyAttrs}>Open in browser for you to fill</button>`:`<button type="button" ${applyAttrs}>Apply</button>`;
    const apply=canApply?applyButton+(dry?`<span class="why">Dry run is on, so ${esc(applyLabel)} is off. Set SABRA_AUTOMATION_DRY_RUN=false and restart the dashboard.</span>`:''):'';
    const handoff=p.application_status==='APPLYING'?`<div class="handoff" data-id="${esc(appId)}">Checking the handoff...</div>`:'';
    return `<div class="row"><label><input type="checkbox" name="package" value="${esc(p.package_id)}" data-hash="${esc(p.content_hash)}" ${p.approvable?'':'disabled'}></label><span><strong>${esc(p.title||'Package')} at ${esc(p.company||'the company')}</strong><br><a href="/packages/${esc(p.package_id)}">Read package</a><br>${esc(p.status)} · ${esc(label)}${next}<br>Application ID <code class="app-id">${esc(appId)}</code><button type="button" class="copy" data-copy="${esc(appId)}">Copy</button>${refresh}${apply}${handoff}<br><code>${esc(p.content_hash)}</code></span></div>`;
  }).join('');
  list.querySelectorAll('button.copy').forEach(btn=>btn.addEventListener('click',async()=>{
    const id=btn.dataset.copy||'';
    try{await navigator.clipboard.writeText(id);}catch(e){}
    btn.textContent='Copied';
  }));
  list.querySelectorAll('button.refresh-one').forEach(btn=>btn.addEventListener('click',async()=>{
    const response=await fetch('/api/applications/'+encodeURIComponent(btn.dataset.id)+'/refresh',{method:'POST'});
    const refreshed=await readBody(response);
    line.textContent=refreshed.ok?'Package refreshed. Approve the new revision; the previous approval no longer applies.':(refreshed.d.detail||refreshed.text||'Refresh failed');
    if(refreshed.ok) load();
  }));
  list.querySelectorAll('button.apply').forEach(btn=>btn.addEventListener('click',()=>applyOne(btn)));
  list.querySelectorAll('.handoff').forEach(box=>refreshHandoff(box.dataset.id,box));
  inflight.forEach(id=>setApplyDisabled(id,true));
}
const inflight=new Set();
async function applyOne(btn){
  if(btn.disabled)return;
  const id=btn.dataset.id;
  const line=document.querySelector('#result');
  const manual=btn.dataset.manual==='1';
  const ok=await ask(manual?'Open this posting in your browser? Artemis cannot fill this employer form, so it types nothing. It saves your resume and cover letter as PDFs and shows where, then waits for you to submit the form and report the result.':'Submit this approved application? A CAPTCHA opens in a headed browser for you to solve. This does not solve a CAPTCHA.',{confirmLabel:manual?'Open in browser':'Submit'});
  if(!ok)return;
  inflight.add(id);
  setApplyDisabled(id,true);
  try{
    const response=await fetch('/api/applications/'+encodeURIComponent(id)+'/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
    const applied=await readBody(response);
    line.textContent=applied.ok?(applied.d.outcome+': '+(applied.d.detail||'')):(applied.d.detail||applied.text||'Apply failed');
  }finally{
    inflight.delete(id);
  }
  await load();
}
function setApplyDisabled(id,disabled){
  document.querySelectorAll('button.apply').forEach(b=>{if(b.dataset.id===id)b.disabled=disabled||b.dataset.dry==='1';});
}
function forceSection(id,s){
  if(!s.can_force)return '';
  return `<div class="force"><span class="why">This attempt has had no activity for longer than the handoff timeout and nothing is running for it here. If you know how it ended, you can force a report. It is recorded as forced.</span><button type="button" class="force-report" data-outcome="submitted">Force a report: it was submitted</button><button type="button" class="force-report" data-outcome="abandoned">Force a report: it was not submitted</button></div>`;
}
function wireForce(box,id){
  box.querySelectorAll('button.force-report').forEach(b=>b.addEventListener('click',async()=>{
    const typed=await ask('Forcing a report records an outcome without Artemis seeing it. Type the application id to confirm: '+id,{input:true,confirmLabel:'Force report'});
    if(typed===null)return;
    const forced=await readBody(await fetch('/api/applications/'+encodeURIComponent(id)+'/handoff/force',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({outcome:b.dataset.outcome,confirm_id:typed})}));
    document.querySelector('#result').textContent=forced.ok?('Recorded as forced. The application is now '+forced.d.application_status+'.'):(forced.d.detail||forced.text||'Forced report refused');
    await load();
  }));
}
function showHandoff(box,id,s){
  const waiting=!!s.awaiting_person;
  setApplyDisabled(id,waiting||inflight.has(id)||s.application_status==='APPLYING');
  if(!waiting&&s.application_status==='APPLYING'){box.innerHTML='<span class="why">A submission is running. This row asks for your answer when it needs you.</span>'+forceSection(id,s);wireForce(box,id);return;}
  if(!waiting){box.remove();return;}
  const files=Object.entries(s.documents||{}).map(([k,v])=>`<li>${esc(k.replace(/_/g,' '))}: <code>${esc(v)}</code></li>`).join('');
  const attach=s.manual&&files?`<span class="why">Attach these files when the form asks:</span><ul class="files">${files}</ul>`:'';
  const where=s.manual?(s.active?`The posting is open in a browser window (${esc(s.state||'open')}). Nothing was typed. Fill it in, attach the files, submit it yourself, then tell Artemis what happened.`:'No browser window is open for this posting. Tell Artemis whether you submitted it.'):(s.active?`The browser window is open (${esc(s.state||'open')}). Solve the check and press Submit there, then tell Artemis what happened.`:'No browser window is open for this application. Tell Artemis whether it was submitted.');
  box.innerHTML=`<span class="why">${where}</span>${attach}<button type="button" class="outcome" data-outcome="submitted">I submitted it</button><button type="button" class="outcome" data-outcome="abandoned">I didn't submit</button>`+forceSection(id,s);
  wireForce(box,id);
  box.querySelectorAll('button.outcome').forEach(b=>b.addEventListener('click',async()=>{
    box.querySelectorAll('button').forEach(x=>{x.disabled=true;});
    const reported=await readBody(await fetch('/api/applications/'+encodeURIComponent(id)+'/handoff/outcome',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({outcome:b.dataset.outcome})}));
    document.querySelector('#result').textContent=reported.ok?('Recorded. The application is now '+reported.d.application_status+'.'):(reported.d.detail||reported.text||'Report failed');
    await load();
  }));
}
async function refreshHandoff(id,box){
  const r=await readBody(await fetch('/api/applications/'+encodeURIComponent(id)+'/handoff'));
  if(r.ok)showHandoff(box,id,r.d);
  return r;
}
let polling=false;
setInterval(async()=>{
  if(polling)return;
  polling=true;
  try{
    const boxes=[...document.querySelectorAll('.handoff')];
    const results=await Promise.all(boxes.map(box=>refreshHandoff(box.dataset.id,box)));
    if(results.some(r=>r.ok&&r.d.application_status!=='APPLYING'))await load();
  }finally{polling=false;}
},3000);
document.querySelector('#batch').addEventListener('submit',async e=>{
  e.preventDefault();
  const chosen=[...document.querySelectorAll('input[name=package]:checked')].map(el=>({package_id:el.value,content_hash:el.dataset.hash}));
  const line=document.querySelector('#result');
  if(!chosen.length){line.textContent='Select at least one complete package.';return;}
  const r=await fetch('/api/packages/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved_by:'owner',packages:chosen})});
  const body=await readBody(r);
  if(!body.ok){line.textContent=body.d.detail||body.text||'Approval failed';return;}
  const d=body.d;
  const ok=(d.approved||[]).length, no=(d.refused||[]).length;
  line.textContent=`Approved ${ok}. Refused ${no}.`+(no?(' '+d.refused.map(x=>x.message).join(' ')):'');
  load();
});
document.querySelector('#refresh-all').addEventListener('click',async()=>{
  const dialog=document.querySelector('#confirm-refresh');
  const choice=await new Promise(resolve=>{
    dialog.addEventListener('close',()=>resolve(dialog.returnValue),{once:true});
    dialog.showModal();
  });
  if(choice!=='refresh')return;
  const line=document.querySelector('#result');
  const r=await fetch('/api/applications/refresh-unsubmitted',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
  const body=await readBody(r);
  if(!body.ok){line.textContent=body.d.detail||body.text||'Refresh failed';return;}
  const n=(body.d.refreshed||[]).length, no=(body.d.refused||[]).length;
  line.textContent=`Refreshed ${n}. Refused ${no}. Approve the new packages; the old approvals no longer apply.`;
  load();
});
load();
</script></main>""".replace("__ASK__", ASK_JS),
        active="/applications",
        css=""".row{display:flex;gap:12px;align-items:flex-start;background:var(--glass);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid var(--glass-line);border-radius:var(--radius-sm);padding:16px;margin:10px 0;box-shadow:var(--shadow-soft)}button.copy,button.apply,button.refresh-one{padding:6px 10px;margin-left:8px}button.outcome,button.force-report{padding:6px 10px;margin:6px 8px 0 0}.force{margin-top:10px;padding-top:8px;border-top:2px solid var(--warn)}button.force-report{background:var(--warn)}.handoff{margin-top:8px;padding:10px;border:1px dashed var(--slate);border-radius:10px}code{font-size:12px}.why{color:var(--muted);display:block;margin-top:6px}#batch>button{margin-top:8px}""",
    )


@app.get("/packages/{package_id}", response_class=HTMLResponse, response_model=None)
def package_page(package_id: str) -> Any:
    """The application review page (PRD section 31)."""

    if not _exists(lambda repos: repos.packages.get(package_id)):
        return _not_found_page(
            "Package not found",
            "No application package has this id. It may belong to a different database.",
            "/applications",
            "Back to applications",
            "/applications",
        )
    return page_shell(
        "Review application",
        review_body(package_id),
        active="/applications",
        css=DETAIL_CSS,
    )


@app.get("/answers", response_class=HTMLResponse)
def answers_page() -> str:
    options = "".join(f'<option value="{key}">{key}</option>' for key in answer_key_choices())
    options += '<option value="__custom__">Custom key</option>'
    return page_shell(
        "Answers",
        """<main class="wrap"><a href="/">Back to overview</a><h1>Answer memory</h1>
<p>Record an answer, then verify it. Only a verified answer is typed onto a form. Salary, background check, and the other sensitive questions stay blank until you verify them.</p>
<form id="record"><label>Question key<select name="key" id="key">__OPTIONS__</select></label><label id="custom-label">Custom key<input name="custom_key" id="custom-key" placeholder="for example prior_employer"></label><label>Answer<input name="value" required></label><label>Question wording, optional<input name="question_text"></label><button type="submit">Record answer</button></form>
<p id="line"></p><ul id="saved"></ul>
<script>
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function readBody(r){const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}return {ok:r.ok,d};}
function syncCustom(){const custom=document.querySelector('#custom-label');custom.hidden=document.querySelector('#key').value!=='__custom__';}
async function load(){
  const r=await fetch('/api/answers');
  const body=await readBody(r);
  const ul=document.querySelector('#saved');
  if(!body.ok){ul.textContent=body.d.detail||'Answers could not be loaded.';return;}
  const d=body.d;
  ul.innerHTML=(d.answers||[]).map(a=>`<li><strong>${esc(a.question_key)}</strong> · ${esc(a.level)}${a.sensitive?' · sensitive':''}<br>${esc(a.value??'')} ${a.verified?'':`<button type="button" data-key="${esc(a.question_key)}" data-value="${esc(a.value??'')}">Verify</button>`}</li>`).join('')||'<li>No answers recorded.</li>';
  ul.querySelectorAll('button[data-key]').forEach(btn=>btn.addEventListener('click',async()=>{
    const response=await fetch('/api/answers/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:btn.dataset.key,value:btn.dataset.value})});
    const verified=await readBody(response);
    document.querySelector('#line').textContent=verified.ok?`${verified.d.question_key} verified.`:(verified.d.detail||'Verify failed');
    load();
  }));
}
document.querySelector('#key').addEventListener('change',syncCustom);
syncCustom();
document.querySelector('#record').addEventListener('submit',async e=>{
  e.preventDefault();
  const f=new FormData(e.target);
  let key=f.get('key');
  if(key==='__custom__') key=(f.get('custom_key')||'').toString().trim();
  const line=document.querySelector('#line');
  if(!key){line.textContent='Enter a question key.';return;}
  const r=await fetch('/api/answers/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key,value:f.get('value'),question_text:f.get('question_text')||null})});
  const body=await readBody(r);
  line.textContent=body.ok?`${body.d.question_key} recorded. Verify it before it can fill a form.`:(body.d.detail||'Record failed');
  e.target.querySelector('[name=value]').value='';
  load();
});
load();
</script></main>""".replace("__OPTIONS__", options),
        active="/answers",
        css="""form,li{background:var(--glass);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid var(--glass-line);border-radius:var(--radius-sm);padding:16px;margin:12px 0;box-shadow:var(--shadow-soft)}ul{padding-left:0;list-style:none}li{display:block}input,select{display:block;width:100%;margin:6px 0 12px}""",
    )
