"""The opportunity detail projection (requirement 30).

One read of everything a person needs to decide whether to pursue a role: the posting,
why it scored as it did, whether it passed the hard filter, what is verified versus
inferred about the company, where the application stands, and what happened and when.

Nothing here writes. Every figure comes from a stored row or an event, so the page can
only say what the engine recorded.
"""

from __future__ import annotations

from typing import Any

from sabra_domain import models
from sabra_domain.enums import ApplicationStatus, EntityType, EventType, JobStatus
from sabra_domain.state import PRE_SUBMISSION_STATES, can_transition_job

from .context import EngineContext
from .display import compensation_text
from .documents import DocumentService
from .event_summary import summarize_payload

# Fit components in the order the page lists them, with the key the fit engine stores
# their rationale under.
FIT_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("career_score", "Career match"),
    ("qualification_score", "Qualifications"),
    ("compensation_score", "Compensation"),
    ("location_score", "Location"),
    ("industry_score", "Industry"),
    ("trajectory_score", "Trajectory"),
)

# The history list is a window onto an append-only log; a very long one keeps its newest.
HISTORY_LIMIT = 300


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _filter_decision(opportunity: Any, events: list[Any]) -> dict[str, Any]:
    """Why the opportunity passed or failed the hard filter, from what the filter recorded.

    The filter leaves one event per evaluation: JOB_SCORED with `stage: hard_filter_passed`
    when it passed, JOB_REJECTED with the reasons and `checks_run` when it did not. The
    latest of those is the decision. An owner's Pass is a rejection too, but it is not the
    filter's, and is reported on its own.
    """

    latest: Any | None = None
    for event in events:
        payload = event.payload or {}
        passed = event.event_type == EventType.JOB_SCORED.value and (
            payload.get("stage") == "hard_filter_passed"
        )
        rejected = event.event_type == EventType.JOB_REJECTED.value and "checks_run" in payload
        if passed or rejected:
            latest = event

    rejection = opportunity.rejection or {}
    owner_passed = "OWNER_PASSED" in (rejection.get("reasons") or [])
    if latest is None:
        decision: dict[str, Any] = {
            "decision": "not_evaluated",
            "reasons": [],
            "detail": {},
            "checks_run": [],
            "checks_skipped": [],
            "evaluated_at": None,
            "summary": "The hard filter has not been run on this opportunity yet.",
        }
    else:
        payload = latest.payload or {}
        checks_run = [str(c) for c in payload.get("checks_run") or []]
        checks_skipped = [str(c) for c in payload.get("checks_skipped") or []]
        if latest.event_type == EventType.JOB_REJECTED.value:
            reasons = [str(r) for r in payload.get("reasons") or []]
            detail = {str(k): str(v) for k, v in (payload.get("detail") or {}).items()}
            summary = "Rejected by the hard filter: " + (
                "; ".join(f"{r}" + (f" ({detail[r]})" if detail.get(r) else "") for r in reasons)
                or "no reason recorded"
            )
            decision = {"decision": "rejected", "reasons": reasons, "detail": detail}
        else:
            decision = {"decision": "passed", "reasons": [], "detail": {}}
            summary = f"Passed the hard filter: {len(checks_run)} check(s) run" + (
                f", {len(checks_skipped)} skipped for lack of data." if checks_skipped else "."
            )
        decision.update(
            checks_run=checks_run,
            checks_skipped=checks_skipped,
            evaluated_at=_iso(latest.occurred_at),
            summary=summary,
        )
    decision["owner_passed"] = owner_passed
    return decision


def _research(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = dict(row.payload or {})

    def texts(key: str) -> list[str]:
        value = payload.get(key) or []
        return [str(item) for item in value] if isinstance(value, list) else []

    return {
        "engine": row.engine,
        "updated_at": _iso(row.updated_at),
        "company_description": payload.get("company_description"),
        "company_website": payload.get("company_website"),
        "industry": payload.get("industry"),
        "products_services": texts("products_services"),
        "role_context": payload.get("role_context"),
        "likely_reporting_organization": payload.get("likely_reporting_organization"),
        "hiring_context": payload.get("hiring_context"),
        "recent_developments": texts("recent_developments"),
        "candidate_company_intersections": texts("candidate_company_intersections"),
        # Facts read from a source stay apart from what was concluded from them.
        "verified_facts": texts("verified_facts"),
        "inferences": texts("inferences"),
        "sources": texts("sources"),
    }


class OpportunityDetailService:
    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx

    def detail(self, opportunity_id: str) -> dict[str, Any]:
        """Everything on the opportunity detail page. Raises `NotFound` for an unknown id."""

        with self.ctx.read_repos() as repos:
            opportunity = repos.opportunities.get(opportunity_id)
            job = DocumentService._primary_job(repos, opportunity)
            jobs = repos.opportunities.list_jobs(opportunity.id)
            fit = repos.fit.for_opportunity(opportunity.id)
            research = repos.research.for_opportunity(opportunity.id)
            application = repos.applications.for_opportunity(opportunity.id)
            package = repos.packages.latest_for_opportunity(opportunity.id)
            packages = (
                repos.session.query(models.ApplicationPackageRow)
                .filter(models.ApplicationPackageRow.opportunity_id == opportunity.id)
                .all()
            )
            ranked_total = repos.opportunities.count_ranked(
                opportunity.candidate_id, opportunity.search_profile_id
            )

            opportunity_events = repos.events.for_entity(EntityType.OPPORTUNITY, opportunity.id)
            history = list(opportunity_events)
            for posting in jobs:
                history += repos.events.for_entity(EntityType.JOB, posting.id)
            for row in packages:
                history += repos.events.for_entity(EntityType.PACKAGE, row.id)
            if application is not None:
                history += repos.events.for_entity(EntityType.APPLICATION, application.id)
            history.sort(key=lambda e: (e.occurred_at, e.seq))

            status = JobStatus(opportunity.status)
            owner_passed = "OWNER_PASSED" in ((opportunity.rejection or {}).get("reasons") or [])
            # The same conditions the Prepare and Pass routes enforce, so the page offers
            # only what the engine will accept: nothing once an approval exists, and a
            # Pass only while nothing has been approved or submitted.
            before_approval = application is None or application.status in {
                ApplicationStatus.DRAFT.value,
                ApplicationStatus.AWAITING_APPROVAL.value,
            }
            can_prepare = status in PRE_SUBMISSION_STATES and before_approval
            can_pass = (
                not owner_passed
                and (before_approval or application.status == ApplicationStatus.WITHDRAWN.value)
                and (status is JobStatus.REJECTED or can_transition_job(status, JobStatus.REJECTED))
            )
            fit_payload = None
            if fit is not None:
                rationale = dict(fit.component_rationale or {})
                fit_payload = {
                    "fit_score": fit.fit_score,
                    "confidence": fit.confidence,
                    "engine": fit.engine,
                    "components": [
                        {
                            "key": key,
                            "label": label,
                            "score": getattr(fit, key),
                            "rationale": rationale.get(key, ""),
                        }
                        for key, label in FIT_COMPONENTS
                    ],
                    "strengths": list(fit.strengths or []),
                    "gaps": list(fit.gaps or []),
                    "risks": list(fit.risks or []),
                    "why_this_job": fit.why_this_job,
                }

            return {
                "opportunity": {
                    "id": opportunity.id,
                    "title": opportunity.title,
                    "company": opportunity.company,
                    "location": opportunity.location,
                    "status": opportunity.status,
                    "shortlisted": bool(opportunity.shortlisted),
                    "shortlist_reason": opportunity.shortlist_reason,
                    "source_count": opportunity.source_count,
                },
                "job": {
                    "id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "remote_status": job.remote_status,
                    "employment_type": job.employment_type,
                    "salary_min": job.salary_min,
                    "salary_max": job.salary_max,
                    "salary_currency": job.salary_currency,
                    "salary_period": job.salary_period,
                    "compensation_text": compensation_text(job),
                    "date_posted": _iso(job.date_posted),
                    "date_discovered": _iso(job.date_discovered),
                    "last_verified": _iso(job.last_verified),
                    "source": job.source,
                    "source_url": job.source_url,
                    "description": job.description,
                    "requirements": job.requirements,
                    "preferred_qualifications": job.preferred_qualifications,
                    "responsibilities": job.responsibilities,
                    "travel": job.travel,
                    "department": job.department,
                    "seniority": job.seniority,
                    "postings": [
                        {"source": p.source, "source_job_id": p.source_job_id, "url": p.source_url}
                        for p in jobs
                    ],
                },
                "rank": {
                    "score": opportunity.rank_score,
                    "position": opportunity.rank_position,
                    "of": ranked_total,
                },
                "fit": fit_payload,
                "hard_filter": _filter_decision(opportunity, opportunity_events),
                "research": _research(research),
                "application": {
                    "id": application.id,
                    "status": application.status,
                    "submission_time": _iso(application.submission_time),
                    "submission_method": application.submission_method,
                }
                if application is not None
                else None,
                "package": {
                    "id": package.id,
                    "revision": package.revision,
                    "status": package.status,
                    "truth_clean": package.truth_clean,
                    "unresolved_count": package.unresolved_count,
                    "content_hash": package.content_hash,
                    "generated_at": _iso(package.created_at),
                    "review_url": f"/packages/{package.id}",
                }
                if package is not None
                else None,
                "actions": {
                    "can_prepare": can_prepare,
                    "can_pass": can_pass,
                    "passed": owner_passed,
                },
                "history": [
                    {
                        "seq": e.seq,
                        "entity_type": e.entity_type,
                        "event_type": e.event_type,
                        "occurred_at": e.occurred_at.isoformat(),
                        "actor": e.actor,
                        "summary": summarize_payload(e.payload),
                    }
                    for e in history[-HISTORY_LIMIT:]
                ],
            }
