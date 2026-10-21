"""The approval boundary (requirements 19 and 21).

`PREPARED -> AWAITING_APPROVAL -> APPROVED -> APPLY` is the one irreversible line in
the product. Everything on the left of it is autonomous; nothing on the right of it
happens without a human decision bound to one exact package revision.

The binding is a content hash. An approval records the hash of the package the
human actually read; the execution path recomputes it and refuses on any
difference. That is what makes "explicit approval" mean something specific rather
than "the user clicked once".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sabra_domain.enums import (
    ApplicationStatus,
    ApprovalDecision,
    EntityType,
    EventType,
    JobStatus,
    PackageStatus,
)
from sabra_domain.errors import (
    ApprovalRequired,
    ApprovalScopeMismatch,
    NotFound,
    SabraError,
    TruthBoundaryViolation,
    ValidationFailure,
)
from sabra_domain.identity import content_hash
from sabra_domain.schemas import ApplicationPackageSchema, ApprovalRecord

from .attempt_events import unresolved_unconfirmed
from .context import EngineContext


@dataclass(slots=True)
class BatchApprovalResult:
    """One batch action, with each package approved or refused on its own hash."""

    approved: list[ApprovalOutcome]
    refused: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved": [item.as_dict() for item in self.approved],
            "refused": self.refused,
        }


@dataclass(slots=True)
class ApprovalOutcome:
    application_id: str
    opportunity_id: str
    package_id: str
    decision: str
    approved_package_hash: str | None
    acknowledged_unresolved: list[str]
    approved_by: str
    approved_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "opportunity_id": self.opportunity_id,
            "package_id": self.package_id,
            "decision": self.decision,
            "approved_package_hash": self.approved_package_hash,
            "acknowledged_unresolved": self.acknowledged_unresolved,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat(),
        }


class ApprovalService:
    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx

    # -- review surface -------------------------------------------------

    def review(self, package_id: str) -> dict[str, Any]:
        """Everything the human needs to decide, in one payload (requirement 31)."""

        with self.ctx.read_repos() as repos:
            package_row = repos.packages.get(package_id)
            schema = repos.packages.schema(package_row)
            opportunity = repos.opportunities.get(package_row.opportunity_id)
            job = repos.jobs.get(package_row.job_id)
            application = repos.applications.for_opportunity(package_row.opportunity_id)
            events = repos.events.for_entity(EntityType.PACKAGE, package_row.id)
            return {
                "package": {
                    "id": package_row.id,
                    "revision": package_row.revision,
                    "status": package_row.status,
                    "content_hash": package_row.content_hash,
                    "truth_clean": package_row.truth_clean,
                },
                "opportunity": {
                    "id": opportunity.id,
                    "title": opportunity.title,
                    "company": opportunity.company,
                    "location": opportunity.location,
                    "status": opportunity.status,
                    "rank_score": opportunity.rank_score,
                    "shortlist_reason": opportunity.shortlist_reason,
                },
                "job": {
                    "id": job.id,
                    "source": job.source,
                    "source_url": job.source_url,
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "salary_min": job.salary_min,
                    "salary_max": job.salary_max,
                    "salary_currency": job.salary_currency,
                    "salary_period": job.salary_period,
                    "travel": job.travel,
                    "employment_type": job.employment_type,
                    "date_posted": job.date_posted.isoformat() if job.date_posted else None,
                    "description": job.description,
                },
                "materials": {
                    "tailored_resume": schema.tailored_resume_markdown,
                    "cover_letter": schema.cover_letter,
                    "answers": [a.model_dump(mode="json") for a in schema.answers],
                    "recruiter_message": schema.recruiter_message,
                    "hiring_manager_message": schema.hiring_manager_message,
                },
                "analysis": {
                    "fit_explanation": schema.fit_explanation,
                    "qualification_gaps": schema.qualification_gaps,
                    "candidate_summary": schema.candidate_summary,
                },
                "warnings": schema.warnings,
                "unresolved_questions": schema.unresolved_questions,
                "truth_ledger": schema.truth_ledger,
                "application_id": application.id if application else None,
                "events": [
                    {
                        "event_type": e.event_type,
                        "occurred_at": e.occurred_at.isoformat(),
                        "actor": e.actor,
                        "payload": e.payload,
                    }
                    for e in events
                ],
            }

    def pending(self, candidate_id: str | None = None) -> list[dict[str, Any]]:
        with self.ctx.read_repos() as repos:
            rows = repos.packages.list_pending_approval(candidate_id)
            return [
                {
                    "package_id": row.id,
                    "opportunity_id": row.opportunity_id,
                    "revision": row.revision,
                    "status": row.status,
                    "content_hash": row.content_hash,
                    "truth_clean": row.truth_clean,
                    "unresolved_count": row.unresolved_count,
                    "title": repos.opportunities.get(row.opportunity_id).title,
                    "company": repos.opportunities.get(row.opportunity_id).company,
                }
                for row in rows
            ]

    # -- decisions ------------------------------------------------------

    def approve_selected(
        self,
        selections: list[dict[str, str]],
        *,
        approved_by: str,
    ) -> BatchApprovalResult:
        """Approve every selected COMPLETE package, each bound to its content hash.

        A selection is `{package_id, content_hash}`. The hash is the one the person
        saw. A mismatch, an incomplete package, or any other refusal leaves that
        package unapproved and does not approve a different revision in its place.
        """

        approved: list[ApprovalOutcome] = []
        refused: list[dict[str, Any]] = []
        for selection in selections:
            package_id = selection.get("package_id") or ""
            expected_hash = selection.get("content_hash") or ""
            try:
                with self.ctx.read_repos() as repos:
                    row = repos.packages.get(package_id)
                    status = row.status
                    current_hash = row.content_hash
                if status != PackageStatus.COMPLETE.value:
                    raise ValidationFailure(
                        f"package {package_id} is {status}; batch approval accepts only COMPLETE packages"
                    )
                if not expected_hash:
                    raise ApprovalRequired(
                        f"package {package_id} was submitted without the content hash that was reviewed"
                    )
                if expected_hash != current_hash:
                    raise ApprovalScopeMismatch(
                        "the approval does not bind to this package revision: "
                        f"expected {current_hash}, received {expected_hash}"
                    )
                approved.append(
                    self.approve(
                        package_id,
                        approved_by=approved_by,
                        expected_hash=expected_hash,
                        acknowledge_unresolved=False,
                    )
                )
            except SabraError as exc:
                refused.append(
                    {
                        "package_id": package_id,
                        "content_hash": expected_hash,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    }
                )
        return BatchApprovalResult(approved=approved, refused=refused)

    def approve(
        self,
        package_id: str,
        *,
        approved_by: str,
        expected_hash: str | None = None,
        acknowledge_unresolved: bool = False,
        scope_note: str | None = None,
    ) -> ApprovalOutcome:
        """Approve exactly one package revision.

        Refuses when: the package is not truth-clean; the caller's expected hash does
        not match the stored content; or unresolved questions exist and the approver
        has not explicitly acknowledged them.
        """

        with self.ctx.repos() as repos:
            package_row = repos.packages.get(package_id)
            schema = repos.packages.schema(package_row)

            if not package_row.truth_clean:
                raise TruthBoundaryViolation(
                    f"package {package_id} is not truth-clean; it cannot be approved"
                )
            if expected_hash and expected_hash != package_row.content_hash:
                # The human approved a different revision than the one being approved
                # now — usually because the package was regenerated in between.
                raise ApprovalScopeMismatch(
                    "the approval does not bind to this package revision: "
                    f"expected {package_row.content_hash}, received {expected_hash}"
                )

            unresolved = list(schema.unresolved_questions)
            if unresolved and not acknowledge_unresolved:
                raise ApprovalRequired(
                    "this package has "
                    f"{len(unresolved)} unresolved question(s) that must be acknowledged "
                    "before approval: " + "; ".join(unresolved[:5])
                )

            application = repos.applications.for_opportunity(package_row.opportunity_id)
            if application is None:
                raise ValidationFailure(
                    "no application record exists for this package; prepare the package first"
                )
            if application.package_id != package_row.id:
                raise ValidationFailure(
                    f"package {package_id} is not the current package of application "
                    f"{application.id} (current package: {application.package_id}); "
                    "only the current package can be approved"
                )
            if application.status in {
                ApplicationStatus.APPLIED.value,
                ApplicationStatus.APPLYING.value,
            }:
                raise ValidationFailure(
                    f"application {application.id} is already {application.status}; "
                    "approval would re-authorise a completed submission"
                )

            approved_at = datetime.now(UTC)
            record = ApprovalRecord(
                package_id=package_row.id,
                package_content_hash=package_row.content_hash,
                approved_by=approved_by,
                approved_at=approved_at,
                decision=str(ApprovalDecision.APPROVED),
                scope_note=scope_note,
                acknowledged_unresolved=unresolved if acknowledge_unresolved else [],
            )
            application.approval = record.model_dump(mode="json")
            application.approved_package_hash = package_row.content_hash
            repos.session.flush()

            if application.status == ApplicationStatus.DRAFT.value:
                repos.applications.set_status(application.id, ApplicationStatus.AWAITING_APPROVAL)
            if application.status != ApplicationStatus.APPROVED.value:
                repos.applications.set_status(application.id, ApplicationStatus.APPROVED)

            repos.packages.set_status(package_row.id, PackageStatus.APPROVED)
            opportunity = repos.opportunities.get(package_row.opportunity_id)
            if JobStatus(opportunity.status) is JobStatus.AWAITING_APPROVAL:
                repos.opportunities.set_status(package_row.opportunity_id, JobStatus.APPROVED)

            payload = record.model_dump(mode="json")
            repos.events.append(
                EntityType.APPLICATION, application.id, EventType.PACKAGE_APPROVED, payload
            )
            repos.events.append(
                EntityType.PACKAGE, package_row.id, EventType.PACKAGE_APPROVED, payload
            )

            return ApprovalOutcome(
                application_id=application.id,
                opportunity_id=package_row.opportunity_id,
                package_id=package_row.id,
                decision=str(ApprovalDecision.APPROVED),
                approved_package_hash=package_row.content_hash,
                acknowledged_unresolved=record.acknowledged_unresolved,
                approved_by=approved_by,
                approved_at=approved_at,
            )

    def reject(
        self,
        package_id: str,
        *,
        decided_by: str,
        reason: str | None = None,
    ) -> ApprovalOutcome:
        """The candidate passes on an opportunity. Recorded, never deleted."""

        with self.ctx.repos() as repos:
            package_row = repos.packages.get(package_id)
            decided_at = datetime.now(UTC)
            repos.packages.set_status(package_row.id, PackageStatus.SUPERSEDED)

            opportunity = repos.opportunities.get(package_row.opportunity_id)
            repos.opportunities.set_shortlist(
                opportunity.id, False, reason=reason or "passed by the candidate"
            )
            repos.opportunities.set_status(
                opportunity.id,
                JobStatus.REJECTED,
                rejection={
                    "reasons": ["DECLINED_BY_CANDIDATE"],
                    "detail": {"reason": reason or ""},
                },
            )

            application = repos.applications.for_opportunity(package_row.opportunity_id)
            if application is not None and application.status not in {
                ApplicationStatus.APPLIED.value,
                ApplicationStatus.WITHDRAWN.value,
            }:
                if application.status != ApplicationStatus.AWAITING_APPROVAL.value:
                    repos.applications.set_status(
                        application.id, ApplicationStatus.AWAITING_APPROVAL
                    )
                repos.applications.set_status(application.id, ApplicationStatus.WITHDRAWN)

            payload = {
                "package_id": package_row.id,
                "decision": str(ApprovalDecision.REJECTED),
                "decided_by": decided_by,
                "decided_at": decided_at.isoformat(),
                "reason": reason,
            }
            repos.events.append(
                EntityType.PACKAGE, package_row.id, EventType.PACKAGE_REJECTED, payload
            )
            if application is not None:
                repos.events.append(
                    EntityType.APPLICATION, application.id, EventType.REJECTED, payload
                )

            return ApprovalOutcome(
                application_id=application.id if application else "",
                opportunity_id=package_row.opportunity_id,
                package_id=package_row.id,
                decision=str(ApprovalDecision.REJECTED),
                approved_package_hash=None,
                acknowledged_unresolved=[],
                approved_by=decided_by,
                approved_at=decided_at,
            )

    # -- authorisation check used by the execution path ------------------

    def assert_submission_authorised(
        self, application_id: str, *, repos: Any = None
    ) -> tuple[Any, ApplicationPackageSchema]:
        """Verify a bound approval for the current package content.

        Called immediately before any external submission. Raises rather than
        returning a flag so there is no way to proceed by ignoring a boolean.

        Pass `repos` to run the check inside a caller's transaction; the execution
        service does, so the check and the claim of the application are one unit.

        Authorised statuses: APPROVED; and BLOCKED or FAILED only when nothing was
        submitted. APPLYING is never re-authorised: a submission in progress, or one
        whose click is unconfirmed, is resolved by a reported outcome, not by trying
        again.
        """

        if repos is not None:
            return self._authorise(repos, application_id)
        with self.ctx.read_repos() as read:
            return self._authorise(read, application_id)

    @staticmethod
    def _authorise(repos: Any, application_id: str) -> tuple[Any, ApplicationPackageSchema]:
        application = repos.applications.get(application_id)
        status = application.status
        if status == ApplicationStatus.APPLYING.value:
            raise ApprovalRequired(
                f"application {application_id} is APPLYING: a submission is in progress or "
                "is waiting for its outcome to be reported, and it is never re-authorised"
            )
        if status not in {
            ApplicationStatus.APPROVED.value,
            ApplicationStatus.FAILED.value,
            ApplicationStatus.BLOCKED.value,
        }:
            raise ApprovalRequired(
                f"application {application_id} is {status}; "
                "an external submission requires an approved application"
            )
        if status in {ApplicationStatus.FAILED.value, ApplicationStatus.BLOCKED.value}:
            events = list(repos.events.for_entity(EntityType.APPLICATION, application_id))
            submitted = application.submission_time is not None or any(
                e.event_type == EventType.APPLICATION_SUBMITTED.value for e in events
            )
            # Order matters: an UNCONFIRMED click in the latest attempt that no person
            # has reported on since may be a real submission. An old one that a person
            # resolved (or that belongs to an earlier attempt) does not count.
            if submitted or unresolved_unconfirmed(events):
                raise ApprovalRequired(
                    f"application {application_id} is {status} but a submission was recorded "
                    "or may have reached the employer; it is not re-authorised"
                )
        package_row = repos.packages.get(application.package_id)
        schema = repos.packages.schema(package_row)
        stored_hash = package_row.content_hash
        if not application.approved_package_hash:
            raise ApprovalRequired("no approval is bound to this application; refusing to submit")
        if application.approved_package_hash != stored_hash:
            raise ApprovalScopeMismatch(
                "the package content has changed since approval "
                f"(approved {application.approved_package_hash}, now {stored_hash}); "
                "re-approval is required"
            )
        # The column is not trusted on its own: the hash is recomputed from the stored
        # payload, so a payload edited without touching the column is refused too.
        recomputed = content_hash(schema.semantic_payload())
        if recomputed != stored_hash:
            raise ApprovalScopeMismatch(
                "the stored package payload does not match its recorded content hash "
                f"(recorded {stored_hash}, recomputed {recomputed}); re-approval is required"
            )
        if not package_row.truth_clean:
            raise TruthBoundaryViolation(
                "the approved package is not truth-clean; refusing to submit"
            )
        return application, schema

    def approval_status(self, application_id: str) -> dict[str, Any]:
        with self.ctx.read_repos() as repos:
            try:
                application = repos.applications.get(application_id)
            except NotFound:
                return {"application_id": application_id, "found": False}
            package_row = (
                repos.packages.get(application.package_id) if application.package_id else None
            )
            return {
                "application_id": application.id,
                "found": True,
                "status": application.status,
                "approved_at": application.approval.get("approved_at")
                if application.approval
                else None,
                "approved_by": application.approval.get("approved_by")
                if application.approval
                else None,
                "approved_package_hash": application.approved_package_hash,
                "current_package_hash": package_row.content_hash if package_row else None,
                "binding_valid": bool(
                    package_row and application.approved_package_hash == package_row.content_hash
                ),
            }
