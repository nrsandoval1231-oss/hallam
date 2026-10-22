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

import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from sabra_domain.state import can_transition_application
from sabra_scoring.features import extract_candidate_features, extract_job_features

from .attempt_events import submission_evidence
from .context import EngineContext
from .event_summary import summarize_payload

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


OUTCOME_LABELS: dict[str, str] = {
    OutcomeKind.EMPLOYER_RESPONSE.value: "Employer response",
    OutcomeKind.RECRUITER_CONTACT.value: "Recruiter contact",
    OutcomeKind.PHONE_SCREEN.value: "Phone screen",
    OutcomeKind.INTERVIEW.value: "Interview",
    OutcomeKind.OFFER.value: "Offer",
    OutcomeKind.REJECTION.value: "Rejection",
    OutcomeKind.WITHDRAWAL.value: "Withdrawal",
    OutcomeKind.NO_RESPONSE.value: "No response",
}

# Pipeline stages in the order the page shows them (requirements 22-24). A stage is a
# projection of the opportunity's lifecycle state and the application's status; nothing
# is stored under these names.
PIPELINE_STAGES: tuple[tuple[str, str], ...] = (
    ("approved", "Approved"),
    ("applying", "Applying / handoff"),
    ("applied", "Applied"),
    ("response", "Response"),
    ("screen", "Screen"),
    ("interview", "Interview"),
    ("offer", "Offer"),
    ("rejected", "Rejected by employer"),
    ("withdrawn", "Withdrawn"),
    ("expired", "Expired"),
)

_OPPORTUNITY_STAGE: dict[str, str] = {
    JobStatus.RESPONSE.value: "response",
    JobStatus.SCREEN.value: "screen",
    JobStatus.INTERVIEW.value: "interview",
    JobStatus.OFFER.value: "offer",
    JobStatus.REJECTED_BY_EMPLOYER.value: "rejected",
    JobStatus.WITHDRAWN.value: "withdrawn",
    JobStatus.EXPIRED.value: "expired",
}

NOTE_MAX = 4000
CONTACT_MAX = 500
# A date typed without a time is read as local midnight, so an outcome on the day of
# submission can sit a few hours before the submission time. A day of slack.
_SUBMISSION_SLACK = timedelta(days=1)

# An outcome the clock has not reached yet is a typo, not a record. A few minutes of
# slack for a clock that is slightly ahead.
_FUTURE_SLACK = timedelta(minutes=5)


def normalize_outcome_kind(raw: str | OutcomeKind) -> str:
    """`phone-screen`, `Phone screen` and `PHONE_SCREEN` all name the same kind."""

    return str(raw).strip().upper().replace("-", "_").replace(" ", "_")


def parse_occurred_at(raw: str | datetime | None) -> datetime | None:
    """An ISO date or datetime as UTC. A value with no zone is read as local time."""

    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        value = raw
    else:
        try:
            value = datetime.fromisoformat(str(raw).strip())
        except ValueError as exc:
            raise ValidationFailure(
                f"{raw!r} is not a date; use ISO form, for example 2026-10-21 or "
                "2026-10-21T14:30"
            ) from exc
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(UTC)


def pipeline_stage(application_status: str, opportunity_status: str) -> str | None:
    """The stage an application sits in, or None when it has not reached the employer's side.

    An application still being prepared (DRAFT, AWAITING_APPROVAL) is not in the
    pipeline, and neither is one the owner passed on before approving it: that leaves the
    opportunity REJECTED, not WITHDRAWN.
    """

    if application_status in {
        ApplicationStatus.DRAFT.value,
        ApplicationStatus.AWAITING_APPROVAL.value,
    }:
        return None
    if opportunity_status in _OPPORTUNITY_STAGE:
        return _OPPORTUNITY_STAGE[opportunity_status]
    if application_status == ApplicationStatus.APPROVED.value:
        return "approved"
    if application_status in {
        ApplicationStatus.APPLYING.value,
        ApplicationStatus.BLOCKED.value,
        ApplicationStatus.FAILED.value,
    }:
        return "applying"
    if application_status == ApplicationStatus.APPLIED.value:
        return "applied"
    return None


def allowed_outcomes(
    application_status: str, opportunity_status: str, *, may_withdraw: bool = True
) -> list[str]:
    """Outcome kinds that make sense to offer for an application right now.

    The service refuses every kind except a withdrawal unless the application is
    APPLIED, and a control should not offer a step the lifecycle cannot take. Before
    submission only a withdrawal is meaningful; once applied, every kind that the
    lifecycle can still follow, and the two that do not move it. `may_withdraw` is False
    for a blocked or failed application that may have reached the employer (see
    `submission_evidence`): such a withdrawal is refused, so it is not offered.
    """

    try:
        current = JobStatus(opportunity_status)
    except ValueError:
        return []
    kinds: list[str] = []
    submitted = application_status == ApplicationStatus.APPLIED.value
    for kind in OutcomeKind:
        target = OUTCOME_STATE.get(kind.value)
        if target is None:
            # Silence is only a finding while the employer has not answered.
            if submitted and current is JobStatus.APPLIED:
                kinds.append(kind.value)
        elif kind is OutcomeKind.WITHDRAWAL:
            if (
                may_withdraw
                and _reachable(current, target)
                and can_transition_application(
                    ApplicationStatus(application_status), ApplicationStatus.WITHDRAWN
                )
            ):
                kinds.append(kind.value)
        elif submitted and (
            _reachable(current, target)
            # A further word from an employer that has already answered does not move the
            # lifecycle, but it is still worth recording.
            or (kind in _REPEATABLE and current in _ANSWERED)
        ):
            kinds.append(kind.value)
    return kinds


_REPEATABLE = frozenset({OutcomeKind.EMPLOYER_RESPONSE, OutcomeKind.RECRUITER_CONTACT})
_ANSWERED = frozenset(
    {JobStatus.RESPONSE, JobStatus.SCREEN, JobStatus.INTERVIEW, JobStatus.OFFER}
)


@dataclass(slots=True)
class PipelineSummary:
    candidate_id: str
    applications_by_status: dict[str, int] = field(default_factory=dict)
    opportunities_by_status: dict[str, int] = field(default_factory=dict)
    outcomes_by_kind: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    stages: dict[str, int] = field(default_factory=dict)
    # Every application past approval, grouped into PIPELINE_STAGES (all stages present,
    # in order, even when empty).
    board: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "applications_by_status": self.applications_by_status,
            "opportunities_by_status": self.opportunities_by_status,
            "outcomes_by_kind": self.outcomes_by_kind,
            "counts": self.counts,
            "stages": self.stages,
            "board": self.board,
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
        contact: str | None = None,
    ) -> dict[str, Any]:
        """Capture an outcome and advance the lifecycle if the kind implies it.

        The outcome row and its event are written in one transaction. The opportunity
        (and with it every posting of the opportunity) moves only along an edge of the job
        graph, so a kind that does not fit where the lifecycle is now (an interview
        recorded before the application was submitted, a phone screen after an interview)
        is recorded without inventing the step; `applied_state` is None and `note` says why.

        Outcomes belong to a submitted application: every kind but a withdrawal is refused
        unless the application is APPLIED. A withdrawal moves the application and the
        opportunity together or neither: it is refused (`ValidationFailure`, nothing written)
        while a submission is in progress, when either graph forbids the move, and for a
        blocked or failed application that may have reached the employer. The status the
        application had is kept on the outcome as `application_status_at_record`.

        The application moves too, but only for a withdrawal: the application graph ends at
        APPLIED -> WITHDRAWN, and a response, screen, interview, offer or rejection is
        carried by the opportunity's state. `contact` (who wrote) is kept on the outcome row
        and never on the event, and never in the learning-dataset export.
        """

        kind_value = normalize_outcome_kind(kind)
        valid = {k.value for k in OutcomeKind}
        if kind_value not in valid:
            raise ValidationFailure(
                f"unknown outcome kind {str(kind)!r}; expected one of {sorted(valid)}"
            )
        if occurred_at is not None:
            occurred_at = parse_occurred_at(occurred_at)
            if occurred_at is not None and occurred_at > datetime.now(UTC) + _FUTURE_SLACK:
                raise ValidationFailure(
                    f"{occurred_at.isoformat()} is in the future; an outcome is recorded after it "
                    "happened"
                )
        if notes is not None and len(notes) > NOTE_MAX:
            raise ValidationFailure(f"the note is {len(notes)} characters; the limit is {NOTE_MAX}")
        if contact is not None and len(contact) > CONTACT_MAX:
            raise ValidationFailure(f"the contact is too long; the limit is {CONTACT_MAX}")
        stored_payload = dict(payload or {})
        if contact and contact.strip():
            stored_payload["contact"] = contact.strip()

        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            opportunity_id = application.opportunity_id
            opportunity = repos.opportunities.get(opportunity_id)
            current = JobStatus(opportunity.status)
            app_status = ApplicationStatus(application.status)
            target = OUTCOME_STATE.get(kind_value)

            # Everything that can be refused is decided before anything is written, so a
            # refusal leaves no outcome, no event and no half-made move.
            if kind_value == OutcomeKind.WITHDRAWAL.value:
                refusal = _withdrawal_refusal(repos, application, current)
                if refusal:
                    raise ValidationFailure(f"cannot withdraw: {refusal}")
            elif app_status is not ApplicationStatus.APPLIED:
                raise ValidationFailure(
                    f"cannot record {OUTCOME_LABELS[kind_value].lower()}: only a submitted "
                    f"application has employer outcomes, and this one is {app_status.value}"
                )
            if (
                occurred_at is not None
                and application.submission_time is not None
                and occurred_at + _SUBMISSION_SLACK < application.submission_time
            ):
                raise ValidationFailure(
                    f"{occurred_at.isoformat()} is before the application was submitted "
                    f"({application.submission_time.isoformat()})"
                )

            stored_payload["application_status_at_record"] = app_status.value
            outcome_row = repos.outcomes.add(
                application_id=application.id,
                kind=kind_value,
                opportunity_id=opportunity_id,
                notes=notes,
                source=source,
                payload=stored_payload,
                occurred_at=occurred_at,
            )
            if external_confirmation:
                merged = dict(application.external_confirmation or {})
                merged.update(external_confirmation)
                application.external_confirmation = merged

            applied_state: str | None = None
            application_state: str | None = None
            note: str | None = None
            if target is not None:
                if _reachable(current, target):
                    repos.opportunities.set_status(opportunity_id, target)
                    applied_state = str(target)
                    if target is JobStatus.WITHDRAWN:
                        # _withdrawal_refusal already proved this edge exists.
                        repos.applications.set_status(application.id, ApplicationStatus.WITHDRAWN)
                        application_state = ApplicationStatus.WITHDRAWN.value
                else:
                    note = (
                        f"recorded, but the lifecycle did not move: the opportunity is "
                        f"{current.value}, which cannot go to {target.value}"
                    )

            repos.events.append(
                EntityType.APPLICATION,
                application.id,
                OUTCOME_EVENT.get(kind_value, EventType.EMPLOYER_RESPONSE),
                {
                    "outcome_kind": kind_value,
                    "notes": notes,
                    "source": source,
                    "applied_state": applied_state,
                    "application_state": application_state,
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
                "application_status": application.status,
                "note": note,
            }

    # -- projections ----------------------------------------------------

    def pipeline(self, candidate_id: str) -> PipelineSummary:
        with self.ctx.read_repos() as repos:
            applications = repos.applications.list_for_candidate(candidate_id)
            # Counts only: a GROUP BY, not every opportunity row.
            opp_by_status = repos.opportunities.count_by_status(candidate_id)
            outcomes = repos.outcomes.list_all(candidate_id)

            by_status: dict[str, int] = {}
            for row in applications:
                by_status[row.status] = by_status.get(row.status, 0) + 1
            outcome_counts: dict[str, int] = {}
            outcomes_by_application: dict[str, list[Any]] = {}
            for row in outcomes:
                outcome_counts[row.kind] = outcome_counts.get(row.kind, 0) + 1
                outcomes_by_application.setdefault(row.application_id, []).append(row)
            for rows in outcomes_by_application.values():
                rows.sort(key=lambda o: o.occurred_at)

            board = self._board(repos, applications, outcomes_by_application)
            return PipelineSummary(
                candidate_id=candidate_id,
                applications_by_status=by_status,
                opportunities_by_status=opp_by_status,
                outcomes_by_kind=outcome_counts,
                counts={
                    "opportunities": sum(opp_by_status.values()),
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
                board=board,
            )

    @staticmethod
    def _board(
        repos: Any,
        applications: list[Any],
        outcomes_by_application: dict[str, list[Any]],
    ) -> list[dict[str, Any]]:
        """Applications past approval, grouped by stage, each with its last event.

        A fixed number of queries however many applications there are: the opportunities
        and last events are fetched once, for only the applications that can be on the
        board, and the outcomes arrive already grouped.
        """

        # An application still being prepared is never on the board, whatever its
        # opportunity says, so nothing about it is loaded.
        candidates = [
            row
            for row in applications
            if row.status
            not in {ApplicationStatus.DRAFT.value, ApplicationStatus.AWAITING_APPROVAL.value}
        ]
        opportunities = repos.opportunities.get_many([row.opportunity_id for row in candidates])
        last_events = repos.events.latest_for_entities(
            EntityType.APPLICATION, [row.id for row in candidates]
        )
        stages: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in PIPELINE_STAGES}
        for row in candidates:
            opportunity = opportunities.get(row.opportunity_id)
            if opportunity is None:
                continue
            stage = pipeline_stage(row.status, opportunity.status)
            if stage is None:
                continue
            last = last_events.get(row.id)
            outcomes = outcomes_by_application.get(row.id, [])
            may_withdraw = True
            if row.status in {ApplicationStatus.BLOCKED.value, ApplicationStatus.FAILED.value}:
                # Only these few rows read their own log: it is what shows whether a click
                # may have reached the employer.
                may_withdraw = (
                    submission_evidence(
                        row, repos.events.for_entity(EntityType.APPLICATION, row.id)
                    )
                    is None
                )
            stages[stage].append(
                {
                    "application_id": row.id,
                    "opportunity_id": row.opportunity_id,
                    "package_id": row.package_id,
                    "company": opportunity.company,
                    "title": opportunity.title,
                    "application_status": row.status,
                    "opportunity_status": opportunity.status,
                    "submitted_at": row.submission_time.isoformat()
                    if row.submission_time
                    else None,
                    "submission_method": row.submission_method,
                    "last_event": {
                        "event_type": last.event_type,
                        "occurred_at": last.occurred_at.isoformat(),
                        "summary": summarize_payload(last.payload),
                    }
                    if last
                    else None,
                    "outcome_count": len(outcomes),
                    "latest_outcome": outcomes[-1].kind if outcomes else None,
                    "allowed_outcomes": allowed_outcomes(
                        row.status, opportunity.status, may_withdraw=may_withdraw
                    ),
                }
            )
        for rows in stages.values():
            rows.sort(
                key=lambda r: (r["last_event"] or {}).get("occurred_at") or "",
                reverse=True,
            )
        return [
            {"key": key, "label": label, "count": len(stages[key]), "applications": stages[key]}
            for key, label in PIPELINE_STAGES
        ]

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
                # Only an outcome recorded on a submitted application labels the
                # record. Rows written before `application_status_at_record` existed
                # carry no field and are not trusted as labels either.
                outcomes = [
                    o
                    for o in repos.outcomes.for_application(application.id)
                    if (getattr(o, "payload", None) or {}).get("application_status_at_record")
                    == ApplicationStatus.APPLIED.value
                ]
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

    def export_learning_records(self, candidate_id: str) -> list[dict[str, Any]]:
        """Requirement 25 as plain dicts with every identity field removed.

        The dataset is evidence about jobs, strategies, artifacts and outcomes, not about
        who the candidate is, so a file shared for analysis carries no contact data: names,
        email, phone, address and the candidate's id are dropped, and any string that
        still looks like an email address is replaced. Outcome notes and contacts are not
        part of a learning record at all.
        """

        return [
            _strip_identity(record.model_dump(mode="json"))
            for record in self.learning_records(candidate_id)
        ]

    def learning_jsonl(self, candidate_id: str) -> str:
        """The export as JSON lines: one record per line, in application order."""

        return "".join(
            json.dumps(record, sort_keys=True, default=str) + "\n"
            for record in self.export_learning_records(candidate_id)
        )

    def write_learning_export(
        self, candidate_id: str, path: Path, *, overwrite: bool = False
    ) -> dict[str, Any]:
        """Write the JSONL export to `path`; refuses to replace a file unless told to."""

        target = Path(path)
        if target.exists() and not overwrite:
            raise ValidationFailure(
                f"{target} already exists; choose another path or pass --force to replace it"
            )
        text = self.learning_jsonl(candidate_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            os.replace(temp_name, target)
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise
        return {"path": str(target), "records": text.count("\n")}


# Fields that name or locate the candidate. Matched on the exact key, anywhere in a record.
_IDENTITY_KEYS = frozenset(
    {
        "candidate_id",
        "candidate_name",
        "display_name",
        "full_name",
        "first_name",
        "last_name",
        "name",
        "email",
        "phone",
        "street_address",
        "address",
        "postal_code",
        "linkedin",
        "linkedin_url",
        "contact",
        "notes",
        "approved_by",
    }
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _strip_identity(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_identity(item)
            for key, item in value.items()
            if str(key).lower() not in _IDENTITY_KEYS and not str(key).startswith("applicant_")
        }
    if isinstance(value, list):
        return [_strip_identity(item) for item in value]
    if isinstance(value, str):
        return _EMAIL.sub("[removed]", value)
    return value


def _withdrawal_refusal(repos: Any, application: Any, opportunity_status: JobStatus) -> str | None:
    """Why this application cannot be withdrawn right now, or None when it can.

    Both graphs must allow the move, since a withdrawal moves the application and the
    opportunity together. An application that has not been submitted may be withdrawn only
    when nothing can have reached the employer, the same rule `ApprovalService.reject`
    applies to a blocked or failed one.
    """

    status = ApplicationStatus(application.status)
    if status is ApplicationStatus.WITHDRAWN:
        return "the application is already withdrawn"
    if status is ApplicationStatus.APPLYING:
        return (
            "a submission is in progress or waiting for your report; report how it ended "
            "first (Applications page or `sabra handoff-outcome`)"
        )
    if not can_transition_application(status, ApplicationStatus.WITHDRAWN):
        return f"an application that is {status.value} cannot be withdrawn"
    if not _reachable(opportunity_status, JobStatus.WITHDRAWN):
        return f"the opportunity is {opportunity_status.value}, which cannot be withdrawn"
    if status is not ApplicationStatus.APPLIED:
        evidence = submission_evidence(
            application, repos.events.for_entity(EntityType.APPLICATION, application.id)
        )
        if evidence is not None:
            return evidence
    return None


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
