"""Local browser surface over the Sabra engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
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
from sabra_domain.settings import REPO_ROOT
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
from sabra_engine.documents import DocumentService, package_summary
from sabra_engine.execution import ExecutionService
from sabra_engine.handoff_manager import HANDOFFS
from sabra_engine.intelligence import pass_opportunity
from sabra_engine.refresh import PackageRefreshService
from sabra_engine.runs import RunService
from sqlalchemy.exc import OperationalError

from .pages import (
    CANDIDATE_BODY,
    PIPELINE_BODY,
    SETTINGS_BODY,
    TODAY_BODY,
    TODAY_CSS,
    page_shell,
    searches_body,
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


app = FastAPI(title="Sabra Job Engine", version="0.1.0", lifespan=_lifespan)


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
            "cannot pass",
        )
    ):
        status = 409
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(OperationalError)
async def database_error_handler(_request: Request, exc: OperationalError) -> JSONResponse:
    """A locked SQLite file is another Sabra process, not a server bug."""

    if "database is locked" in str(exc).lower():
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "The database is locked: another Sabra process holds it (a CLI command or "
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
    employment = [
        {
            "label": " at ".join(filter(None, [entry.title, entry.employer])) or "Position",
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
        resume_id = DocumentService(ctx).ingest_master_resume(
            candidate_id,
            text,
            label=resume.filename or "Master resume",
            source_format=source_format,
            source_path=resume.filename,
        )
        return {
            "candidate_id": candidate_id,
            "resume_id": resume_id,
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


def _compensation_text(job: Any) -> str:
    """The posted pay as a person would read it, or "not listed"."""

    if job is None:
        return "not listed"
    low, high = job.salary_min, job.salary_max
    if not low and not high:
        return "not listed"
    currency = job.salary_currency or "USD"
    symbol = "$" if currency == "USD" else f"{currency} "

    def money(value: float) -> str:
        return f"{symbol}{value:,.0f}"

    if low and high and low != high:
        text = f"{money(low)} to {money(high)}"
    elif low and high:
        text = money(low)
    elif low:
        text = f"from {money(low)}"
    else:
        text = f"up to {money(high)}"
    suffix = {"YEAR": "a year", "MONTH": "a month", "HOUR": "an hour"}.get(
        str(job.salary_period or "").upper(), ""
    )
    return f"{text} {suffix}".strip()


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


def _annotate_package(repos: Any, row: Any, *, dry_run: bool) -> dict[str, Any]:
    summary = package_summary(row)
    opportunity = repos.opportunities.get(row.opportunity_id)
    application = repos.applications.for_opportunity(row.opportunity_id)
    payload = dict(row.payload or {})
    summary["title"] = opportunity.title
    summary["company"] = opportunity.company
    summary["application_id"] = application.id if application else None
    summary["application_status"] = application.status if application else None
    summary["approvable"] = bool(
        row.status == PackageStatus.COMPLETE.value
        and row.truth_clean
        and application is not None
        and application.status == ApplicationStatus.AWAITING_APPROVAL.value
    )
    summary["cover_letter"] = payload.get("cover_letter")
    summary["hiring_manager_message"] = payload.get("hiring_manager_message")
    summary["tailored_resume_markdown"] = payload.get("tailored_resume_markdown")
    summary["candidate_summary"] = payload.get("candidate_summary")
    summary["unresolved_questions"] = list(payload.get("unresolved_questions") or [])
    if application is not None and application.status == ApplicationStatus.APPROVED.value:
        summary["review_label"] = "Approved"
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
            rows = [
                _annotate_package(repos, row, dry_run=dry_run)
                for row in _latest_packages(repos, candidate.id)
            ]
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


@app.post("/api/runs", status_code=202)
def run_start(body: RunRequest) -> dict[str, Any]:
    """Start a run on a background thread. 409 while another run is active."""

    return RunService.start_background(body.kind, body.prepare, ctx_factory=EngineContext.create)


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


OPPORTUNITIES_CSS = (
    "main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;"
    "text-decoration:none}h1{font-size:38px;letter-spacing:-.04em;margin:28px 0 8px}p{color:#6c7f7a}"
    ".summary{display:flex;gap:12px;flex-wrap:wrap;margin:28px 0}.stat{background:#fff;"
    "border:1px solid #dce6df;border-radius:10px;padding:14px 18px}.stat strong{display:block;"
    "font-size:25px}.filters{display:flex;gap:10px;flex-wrap:wrap;background:#fff;"
    "border:1px solid #dce6df;border-radius:12px;padding:16px;margin-bottom:18px}"
    "input{border:1px solid #cbd9d1;border-radius:7px;padding:9px;width:150px}"
    "button{background:#18332f;color:#fff;border:0;border-radius:7px;padding:9px 14px;"
    "font-weight:700;cursor:pointer}button.pass{background:#fff;color:#18332f;"
    "border:1px solid #cbd9d1}.queue{background:#fff;border:1px solid #dce6df;"
    "border-radius:12px;padding:22px}.row{border-top:1px solid #dce6df;padding:16px 0;"
    "display:flex;justify-content:space-between;gap:16px}.row:first-child{margin-top:8px}"
    ".score{color:#62877a;font-weight:750;text-align:right;min-width:170px}"
    ".score button{margin-top:6px}.card-line{color:#6c7f7a;font-size:14px}"
    "@media(max-width:700px){.row{flex-direction:column}.score{text-align:left}}"
)

OPPORTUNITIES_BODY = """<main><a href="/">Back to overview</a><h1>Opportunities</h1><p>Ranked roles from the current discovery and intelligence run.</p><section class="summary"><div class="stat"><strong>__OPPORTUNITIES__</strong> opportunities</div><div class="stat"><strong>__SHORTLISTED__</strong> shortlisted</div><div class="stat"><strong>__JOBS__</strong> jobs discovered</div></section><form class="filters" id="filters"><input name="location" placeholder="Location"><input name="min_salary" type="number" placeholder="Min salary"><input name="max_salary" type="number" placeholder="Max salary"><input name="industry" placeholder="Industry"><button type="submit">Filter jobs</button><button type="button" id="clear">Clear</button></form><section class="queue" id="queue"><h2>Shortlist</h2><div class="row"><div><strong>Loading shortlisted opportunities...</strong></div></div></section><p id="error"></p><script>function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}let all=[];function showError(message){const line=document.querySelector('#error');line.textContent=message||'The request failed.';}async function prepare(id){const r=await fetch(`/api/opportunities/${encodeURIComponent(id)}/prepare`,{method:'POST'});const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}if(!r.ok){const detail=typeof d.detail==='string'?d.detail:(text||'Preparation failed');showError(detail);alert(detail);return}showError('');alert(`Package ${d.status}. Unresolved questions: ${(d.unresolved_questions||[]).length}`)}async function pass(id){if(!confirm('Pass on this role? It leaves your list and will not come back.'))return;const r=await fetch(`/api/opportunities/${encodeURIComponent(id)}/pass`,{method:'POST'});const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}if(!r.ok){showError(typeof d.detail==='string'?d.detail:(text||'Pass failed'));return}showError('');all=all.filter(o=>o.id!==id);render(all)}function render(rows){const q=document.querySelector('#queue');q.innerHTML='<h2>Shortlist</h2>'+ (rows.length?rows.map(o=>`<div class="row"><div><strong>${esc(o.rank_position??'')}. ${esc(o.title)}</strong><br><span>${esc(o.company)} · ${esc(o.location||'Location not listed')} · ${esc(o.status)}</span><br><span class="card-line">Pay: ${esc(o.compensation_text||'not listed')} · Confidence: ${o.confidence==null?'not assessed':esc(Math.round(o.confidence*100)+'%')}</span><br><span>${esc(o.why_this_job||o.shortlist_reason||'Shortlisted by the ranking engine.')}</span><br><span class="card-line">Biggest gap: ${esc(o.primary_gap||'none noted')}</span></div><span class="score">${o.fit_score==null?'Fit pending':`Fit ${Math.round(o.fit_score*100)}%`}<br><button type="button" data-id="${esc(o.id)}">Prepare application</button><br><button type="button" class="pass" data-pass="${esc(o.id)}">Pass</button></span></div>`).join(''):'<div class="row"><div><strong>No jobs match those filters.</strong></div></div>');q.querySelectorAll('button[data-id]').forEach(btn=>btn.addEventListener('click',()=>prepare(btn.dataset.id)));q.querySelectorAll('button[data-pass]').forEach(btn=>btn.addEventListener('click',()=>pass(btn.dataset.pass)));}fetch('/api/opportunities').then(async r=>{if(!r.ok)throw new Error(await r.text());return r.json()}).then(d=>{all=d.opportunities;render(all)}).catch(error=>{document.querySelector('#queue').innerHTML='<h2>Shortlist</h2><div class="row"><div><strong>The opportunity queue could not be loaded.</strong></div></div>';showError(error.message)});document.querySelector('#filters').addEventListener('submit',e=>{e.preventDefault();const f=new FormData(e.target),loc=(f.get('location')||'').toString().toLowerCase(),ind=(f.get('industry')||'').toString().toLowerCase(),min=Number(f.get('min_salary'))||0,max=Number(f.get('max_salary'))||Infinity;render(all.filter(o=>(!loc||`${o.location}`.toLowerCase().includes(loc))&&(!ind||`${o.company} ${o.title}`.toLowerCase().includes(ind))&&(!min||o.salary_max>=min)&&(!isFinite(max)||o.salary_min<=max)))});document.querySelector('#clear').onclick=()=>{document.querySelector('#filters').reset();render(all)};</script></main>"""


@app.get("/opportunities", response_class=HTMLResponse)
def opportunities() -> str:
    data = _status_payload()
    body = (
        OPPORTUNITIES_BODY.replace("__OPPORTUNITIES__", str(data["opportunities"]))
        .replace("__SHORTLISTED__", str(data["shortlisted"]))
        .replace("__JOBS__", str(data["jobs_discovered"]))
    )
    return page_shell("Opportunities", body, active="/opportunities", css=OPPORTUNITIES_CSS)


@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page() -> str:
    return page_shell("Pipeline", PIPELINE_BODY, active="/pipeline")


@app.get("/candidate", response_class=HTMLResponse)
def candidate_page() -> str:
    return page_shell("Candidate", CANDIDATE_BODY, active="/candidate")


@app.get("/searches", response_class=HTMLResponse)
def searches_page() -> str:
    return page_shell("Searches", searches_body(), active="/searches")


@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> str:
    return page_shell("Settings", SETTINGS_BODY, active="/settings")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    """Today: what requires the owner's attention."""

    return page_shell("Today", TODAY_BODY, active="/", css=TODAY_CSS)


@app.get("/applications", response_class=HTMLResponse)
def applications_page() -> str:
    return page_shell(
        "Applications",
        """<main><a href="/">Back to overview</a><h1>Applications</h1>
<p>Select complete packages and approve them together. Each approval is stored against that package's content hash, so a later edit invalidates it. The list shows the latest revision of each application.</p>
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
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function readBody(r){const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}return {ok:r.ok,d,text};}
async function load(){
  const line=document.querySelector('#result');
  const r=await fetch('/api/packages');
  const body=await readBody(r);
  const list=document.querySelector('#list');
  if(!body.ok){list.textContent='Packages could not be loaded.';line.textContent=body.d.detail||body.text||'Request failed';return;}
  const d=body.d;
  const dry=d.automation_dry_run!==false;
  const rows=(d.packages||[]);
  if(!rows.length){list.textContent='No packages yet.';return;}
  list.innerHTML=rows.map(p=>{
    const label=p.review_label||(p.approvable?'ready to approve':'not approvable');
    const next=p.next_step?`<span class="why">${esc(p.next_step)}</span>`:'';
    const appId=p.application_id||'';
    const refresh=['APPROVED','BLOCKED','FAILED'].includes(p.application_status)?`<button type="button" class="refresh-one" data-id="${esc(appId)}">Refresh package</button>`:'';
    const canApply=['APPROVED','BLOCKED','FAILED'].includes(p.application_status);
    const apply=canApply?`<button type="button" class="apply" data-id="${esc(appId)}" data-dry="${dry?'1':'0'}" ${dry?'disabled':''}>Apply</button>${dry?'<span class="why">Dry run is on, so Apply is off. Set SABRA_AUTOMATION_DRY_RUN=false and restart the dashboard.</span>':''}`:'';
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
  list.querySelectorAll('button.apply').forEach(btn=>btn.addEventListener('click',async()=>{
    if(btn.disabled)return;
    const id=btn.dataset.id;
    const ok=confirm('Submit this approved application? A CAPTCHA opens in a headed browser for you to solve. This does not solve a CAPTCHA.');
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
  }));
  list.querySelectorAll('.handoff').forEach(box=>refreshHandoff(box.dataset.id,box));
  inflight.forEach(id=>setApplyDisabled(id,true));
}
const inflight=new Set();
function setApplyDisabled(id,disabled){
  document.querySelectorAll('button.apply').forEach(b=>{if(b.dataset.id===id)b.disabled=disabled||b.dataset.dry==='1';});
}
function forceSection(id,s){
  if(!s.can_force)return '';
  return `<div class="force"><span class="why">This attempt has had no activity for longer than the handoff timeout and nothing is running for it here. If you know how it ended, you can force a report. It is recorded as forced.</span><button type="button" class="force-report" data-outcome="submitted">Force a report: it was submitted</button><button type="button" class="force-report" data-outcome="abandoned">Force a report: it was not submitted</button></div>`;
}
function wireForce(box,id){
  box.querySelectorAll('button.force-report').forEach(b=>b.addEventListener('click',async()=>{
    const typed=prompt('Forcing a report records an outcome without Sabra seeing it. Type the application id to confirm: '+id);
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
  const where=s.active?`The browser window is open (${esc(s.state||'open')}). Solve the check and press Submit there, then tell Sabra what happened.`:'No browser window is open for this application. Tell Sabra whether it was submitted.';
  box.innerHTML=`<span class="why">${where}</span><button type="button" class="outcome" data-outcome="submitted">I submitted it</button><button type="button" class="outcome" data-outcome="abandoned">I didn't submit</button>`+forceSection(id,s);
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
</script></main>""",
        active="/applications",
        css="""body{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;text-decoration:none}h1{font-size:38px;letter-spacing:-.04em;margin:28px 0 8px}p{color:#6c7f7a}.row{display:flex;gap:12px;align-items:flex-start;background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px;margin:10px 0}button{background:#18332f;color:#fff;border:0;border-radius:7px;padding:11px 16px;font-weight:700}button.copy,button.apply,button.refresh-one{padding:6px 10px;margin-left:8px}button:disabled{background:#9aada6;cursor:not-allowed}button.outcome,button.force-report{padding:6px 10px;margin:6px 8px 0 0}.force{margin-top:10px;padding-top:8px;border-top:2px solid #b3261e}button.force-report{background:#b3261e}.handoff{margin-top:8px;padding:8px;border:1px dashed #9aada6;border-radius:8px}code{font-size:12px}.why{color:#6c7f7a;display:block;margin-top:6px}dialog{border:1px solid #dce6df;border-radius:12px;padding:22px;max-width:440px;background:#fff;color:#18332f}dialog::backdrop{background:rgba(24,51,47,.45)}dialog p{margin:0 0 16px;color:#18332f}dialog button{margin-right:8px}""",
    )


@app.get("/packages/{package_id}", response_class=HTMLResponse)
def package_page(package_id: str) -> str:
    safe_id = package_id.replace("&", "").replace("<", "").replace('"', "")
    return page_shell(
        "Package",
        """<main><a href="/applications">Back to applications</a><h1 id="title">Package</h1>
<p id="meta"></p><p id="error"></p>
<section><h2>Cover letter</h2><div id="cover"></div></section>
<section><h2>Hiring manager note</h2><div id="hiring"></div></section>
<section><h2>Answers</h2><div id="answers"></div></section>
<section><h2>Unresolved</h2><div id="unresolved"></div></section>
<section><h2>Tailored resume</h2><pre id="resume"></pre></section>
<script>
const id="__PACKAGE_ID__";
function text(sel, value){document.querySelector(sel).textContent=value||'';}
fetch('/api/packages/'+encodeURIComponent(id)).then(async r=>{
  const raw=await r.text();
  let d={};
  try{d=JSON.parse(raw)}catch(e){d={detail:raw}}
  if(!r.ok){text('#error', d.detail||raw||'This package could not be loaded.');return;}
  text('#title', (d.title||'Package')+' at '+(d.company||'the company'));
  text('#meta', (d.status||'')+' · application '+(d.application_id||'')+' · hash '+(d.content_hash||''));
  text('#cover', d.cover_letter||'No cover letter on this package.');
  text('#hiring', d.hiring_manager_message||'No hiring-manager note on this package.');
  text('#answers', (d.answers||[]).map(a=>(a.question_key||'')+': '+(a.answer||'(blank)')).join('\\n')||'No answers.');
  text('#unresolved', (d.unresolved_questions||[]).join('\\n')||'None.');
  text('#resume', d.tailored_resume_markdown||'');
}).catch(error=>{text('#error', error.message);});
</script></main>""".replace("__PACKAGE_ID__", safe_id),
        active="/applications",
        css="""body{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;text-decoration:none}h1{font-size:32px;letter-spacing:-.04em}pre,section{background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px;white-space:pre-wrap}p{color:#6c7f7a}code{font-size:12px}""",
    )


@app.get("/answers", response_class=HTMLResponse)
def answers_page() -> str:
    options = "".join(f'<option value="{key}">{key}</option>' for key in answer_key_choices())
    options += '<option value="__custom__">Custom key</option>'
    return page_shell(
        "Answers",
        """<main><a href="/">Back to overview</a><h1>Answer memory</h1>
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
        css="""body{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;text-decoration:none}h1{font-size:38px;letter-spacing:-.04em;margin:28px 0 8px}p,label{color:#6c7f7a}form,li{background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px;margin:12px 0}input,select{display:block;width:100%;margin:6px 0 12px;padding:8px;border:1px solid #cbd9d1;border-radius:7px}button{background:#18332f;color:#fff;border:0;border-radius:7px;padding:9px 14px;font-weight:700}""",
    )
