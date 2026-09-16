"""Application ledger, pipeline and outcomes (requirements 22, 23, 24, 25).

The ledger is a projection over the append-only event log plus the durable
application row — never a place where state is written twice. Outcomes are captured
manually first; email integration is explicitly not a V1 blocker, so the same shape
is available for an integration to fill later.

Requirement 25's training evidence is assembled here from day one: job features,
candidate features, strategy and artifacts, joined to whatever outcome exists. The
dataset is useful precisely because it was collected before anyone knew which
features would matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sabra_domain.enums import (
    ApplicationStatus,
    EntityType,
    EventType,
    JobStatus,
    OutcomeKind,
)
from sabra_domain.errors import ValidationFailure
from sabra_domain.schemas import ApplicationLedgerSchema, LearningRecord
from sabra_scoring.features import extract_candidate_features, extract_job_features

from .context import EngineContext

# Outcome kind -> the job/application state it implies. Kept as data so the mapping
# is inspectable rather than buried in a chain of ifs.
OUTCOME_STATE: dict[str, JobStatus] = {
    OutcomeKind.EMPLOYER_RESPONSE.value: JobStatus.RESPONSE,
    OutcomeKind.RECRUITER_CONTACT.value: JobStatus.RESPONSE,
    OutcomeKind.PHONE_SCREEN.value: JobStatus.SCREEN,
    OutcomeKind.INTERVIEW.value: JobStatus.INTERVIEW,
    OutcomeKind.OFFER.value: JobStatus.OFFER,
    OutcomeKind.REJECTION.value: JobStatus.REJECTED_BY_EMPLOYER,
    OutcomeKind.WITHDRAWAL.value: JobStatus.WITHDRAWN,
    # NO_RESPONSE is a judgement about silence, not a lifecycle move: it is recorded
    # as an outcome and deliberately leaves the job where it is.
    OutcomeKind.NO_RESPONSE.value: None,  # type: ignore[dict-item]
}

OUTCOME_EVENT: dict[str, EventType] = {
    OutcomeKind.EMPLOYER_RESPONSE.value: EventType.EMPLOYER_RESPONSE,
    OutcomeKind.RECRUITER_CONTACT.value: EventType.EMPLOYER_RESPONSE,
    OutcomeKind.PHONE_SCREEN.value: EventType.SCREEN_REQUESTED,
    OutcomeKind.INTERVIEW.value: EventType.INTERVIEW_REQUESTED,
    OutcomeKind.OFFER.value: EventType.OFFER_RECEIVED,
    OutcomeKind.REJECTION.value: EventType.REJECTED,
    OutcomeKind.WITHDRAWAL.value: EventType.WITHDRAWN,
    OutcomeKind.NO_RESPONSE.value: EventType.NO_RESPONSE,
}


@dataclass(slots=True)
class PipelineSummary:
    candidate_id: str
    applications_by_status: dict[str, int] = field(default_factory=dict)
    opportunities_by_status: dict[str, int] = field(default_factory=dict)
    outcomes_by_kind: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    stages: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "applications_by_status": self.applications_by_status,
            "opportunities_by_status": self.opportunities_by_status,
            "outcomes_by_kind": self.outcomes_by_kind,
            "counts": self.counts,
            "stages": self.stages,
        }


class LedgerService:
    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx

    # -- ledger ---------------------------------------------------------

    def application_ledger(self, application_id: str) -> ApplicationLedgerSchema:
        """The durable record required by requirement 22."""

        with self.ctx.read_repos() as repos:
            application = repos.applications.get(application_id)
            package_row = repos.packages.get(application.package_id)
            schema = repos.packages.schema(package_row)
            job = repos.jobs.get(application.job_id)
            candidate = repos.candidates.get(application.candidate_id)
            master = (
                repos.resumes.get(application.master_resume_id)
                if application.master_resume_id
                else None
            )
            events = repos.events.for_entity(EntityType.APPLICATION, application.id)
            outcomes = repos.outcomes.for_application(application.id)

            return ApplicationLedgerSchema(
                application_id=application.id,
                job={
                    "id": job.id,
                    "source": job.source,
                    "source_job_id": job.source_job_id,
                    "source_url": job.source_url,
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "salary_min": job.salary_min,
                    "salary_max": job.salary_max,
                    "date_posted": job.date_posted.isoformat() if job.date_posted else None,
                    "status": job.status,
                },
                candidate={
                    "id": candidate.id,
                    "display_name": candidate.display_name,
                    "email": candidate.email,
                },
                application_package={
                    "id": package_row.id,
                    "revision": package_row.revision,
                    "content_hash": package_row.content_hash,
                    "truth_clean": package_row.truth_clean,
                    "unresolved_questions": schema.unresolved_questions,
                    "tailored_resume_id": package_row.tailored_resume_id,
                },
                resume_version=f"v{master.version} ({master.content_hash[:12]})"
                if master
                else None,
                answers=list(application.answers or []),
                submission_time=application.submission_time,
                submission_method=application.submission_method,
                status=application.status,
                external_confirmation=dict(application.external_confirmation or {}),
                events=[
                    {
                        "seq": e.seq,
                        "event_type": e.event_type,
                        "occurred_at": e.occurred_at.isoformat(),
                        "actor": e.actor,
                        "payload": e.payload,
                    }
                    for e in events
                ],
                outcomes=[
                    {
                        "kind": o.kind,
                        "occurred_at": o.occurred_at.isoformat(),
                        "source": o.source,
                        "notes": o.notes,
                        "payload": o.payload,
                    }
                    for o in outcomes
                ],
            )

    def event_stream(
        self, *, limit: int = 100, application_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.ctx.read_repos() as repos:
            if application_id:
                rows = repos.events.for_entity(EntityType.APPLICATION, application_id)
            else:
                rows = list(reversed(repos.events.recent(limit=limit)))
            return [
                {
                    "seq": e.seq,
                    "entity_type": e.entity_type,
                    "entity_id": e.entity_id,
                    "event_type": e.event_type,
                    "occurred_at": e.occurred_at.isoformat(),
                    "actor": e.actor,
                    "payload": e.payload,
                }
                for e in rows
            ]

    # -- outcomes -------------------------------------------------------

    def record_outcome(
        self,
        application_id: str,
        kind: str | OutcomeKind,
        *,
        notes: str | None = None,
        source: str = "manual",
        occurred_at: datetime | None = None,
        payload: dict[str, Any] | None = None,
        external_confirmation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Capture an outcome and advance the lifecycle if the kind implies it."""

        kind_value = str(kind)
        valid = {k.value for k in OutcomeKind}
        if kind_value not in valid:
            raise ValidationFailure(
                f"unknown outcome kind {kind_value!r}; expected one of {sorted(valid)}"
            )

        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            opportunity_id = application.opportunity_id
            outcome_row = repos.outcomes.add(
                application_id=application.id,
                kind=kind_value,
                opportunity_id=opportunity_id,
                notes=notes,
                source=source,
                payload=payload or {},
                occurred_at=occurred_at,
            )
            if external_confirmation:
                merged = dict(application.external_confirmation or {})
                merged.update(external_confirmation)
                application.external_confirmation = merged

            target = OUTCOME_STATE.get(kind_value)
            applied_state: str | None = None
            if target is not None:
                opportunity = repos.opportunities.get(opportunity_id)
                current = JobStatus(opportunity.status)
                if _reachable(current, target):
                    repos.opportunities.set_status(opportunity_id, target)
                    applied_state = str(target)

            repos.events.append(
                EntityType.APPLICATION,
                application.id,
                OUTCOME_EVENT.get(kind_value, EventType.EMPLOYER_RESPONSE),
                {
                    "outcome_kind": kind_value,
                    "notes": notes,
                    "source": source,
                    "applied_state": applied_state,
                },
                actor=source,
                occurred_at=occurred_at,
            )
            repos.session.flush()
            return {
                "outcome_id": outcome_row.id,
                "application_id": application.id,
                "opportunity_id": opportunity_id,
                "kind": kind_value,
                "occurred_at": outcome_row.occurred_at.isoformat(),
                "applied_state": applied_state,
            }

    # -- projections ----------------------------------------------------

    def pipeline(self, candidate_id: str) -> PipelineSummary:
        with self.ctx.read_repos() as repos:
            applications = repos.applications.list_for_candidate(candidate_id)
            opportunities = repos.opportunities.list_for_candidate(candidate_id)
            outcomes = repos.outcomes.list_all(candidate_id)

            by_status: dict[str, int] = {}
            for row in applications:
                by_status[row.status] = by_status.get(row.status, 0) + 1
            opp_by_status: dict[str, int] = {}
            for row in opportunities:
                opp_by_status[row.status] = opp_by_status.get(row.status, 0) + 1
            outcome_counts: dict[str, int] = {}
            for row in outcomes:
                outcome_counts[row.kind] = outcome_counts.get(row.kind, 0) + 1

            return PipelineSummary(
                candidate_id=candidate_id,
                applications_by_status=by_status,
                opportunities_by_status=opp_by_status,
                outcomes_by_kind=outcome_counts,
                counts={
                    "opportunities": len(opportunities),
                    "applications": len(applications),
                    "outcomes": len(outcomes),
                    "awaiting_approval": sum(
                        1
                        for r in applications
                        if r.status == ApplicationStatus.AWAITING_APPROVAL.value
                    ),
                    "approved": sum(
                        1 for r in applications if r.status == ApplicationStatus.APPROVED.value
                    ),
                    "submitted": sum(
                        1 for r in applications if r.status == ApplicationStatus.APPLIED.value
                    ),
                },
                stages=_stage_counts(opp_by_status),
            )

    # -- learning dataset ------------------------------------------------

    def learning_records(self, candidate_id: str) -> list[LearningRecord]:
        """Requirement 25: job features + candidate features + strategy + artifacts."""

        records: list[LearningRecord] = []
        with self.ctx.read_repos() as repos:
            profile = None
            try:
                profile = repos.candidates.active_profile(candidate_id)
            except Exception:
                profile = None
            candidate_features = extract_candidate_features(profile) if profile else None

            for application in repos.applications.list_for_candidate(candidate_id):
                package_row = repos.packages.get(application.package_id)
                schema = repos.packages.schema(package_row)
                job = repos.jobs.get(application.job_id)
                outcomes = repos.outcomes.for_application(application.id)
                latest = outcomes[-1] if outcomes else None

                records.append(
                    LearningRecord(
                        application_id=application.id,
                        job_features=extract_job_features(job).as_dict(),
                        candidate_features=candidate_features.as_dict()
                        if candidate_features
                        else {},
                        strategy={
                            "rank_score": getattr(
                                repos.opportunities.get(application.opportunity_id),
                                "rank_score",
                                None,
                            ),
                            "submission_method": application.submission_method,
                            "attempts": application.attempts,
                            "engine": schema.engine,
                        },
                        artifacts={
                            "package_id": package_row.id,
                            "content_hash": package_row.content_hash,
                            "unresolved_count": package_row.unresolved_count,
                            "cover_letter_present": bool(schema.cover_letter),
                            "answers_provided": sum(1 for a in schema.answers if a.answer),
                            "answers_requiring_input": sum(
                                1 for a in schema.answers if a.requires_user_input
                            ),
                        },
                        outcome=latest.kind if latest else None,
                        outcome_recorded_at=latest.occurred_at if latest else None,
                    )
                )
        return records


def _reachable(current: JobStatus, target: JobStatus) -> bool:
    """Whether the lifecycle can move from `current` to `target` right now."""

    from sabra_domain.state import JOB_TRANSITIONS

    return target in JOB_TRANSITIONS.get(current, frozenset())


def _stage_counts(opp_by_status: dict[str, int]) -> dict[str, int]:
    """Group the 19 lifecycle states into the pipeline stages the UI shows."""

    groups = {
        "discovered": {"DISCOVERED", "NORMALIZED"},
        "qualified": {"QUALIFIED"},
        "shortlisted": {"SHORTLISTED", "RESEARCHED"},
        "prepared": {"PREPARED", "AWAITING_APPROVAL"},
        "approved": {"APPROVED", "APPLYING"},
        "submitted": {"APPLIED", "FAILED"},
        "responding": {"RESPONSE", "SCREEN", "INTERVIEW"},
        "offer": {"OFFER"},
        "closed": {"REJECTED", "REJECTED_BY_EMPLOYER", "WITHDRAWN", "EXPIRED"},
    }
    return {
        stage: sum(opp_by_status.get(state, 0) for state in states)
        for stage, states in groups.items()
    }
