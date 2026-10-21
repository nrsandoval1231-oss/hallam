"""Rebuild an approved, unsubmitted package after answer memory changes.

An approval is bound to a content hash. New verified contact answers do not
alter that package. Refresh revokes the approval, writes the old hash to the
ledger, and prepares a new revision the owner must approve again.

An APPROVED application can be refreshed. So can a BLOCKED or FAILED one, because
those are the states a missing or wrong answer leaves behind, and without this path
the owner could neither fix the package nor approve it again. That is allowed only
when nothing can have reached the employer: no submission time, no
APPLICATION_SUBMITTED event, and no unresolved APPLICATION_UNCONFIRMED in the latest
attempt. Submitted applications, and any application with a submission in progress,
are refused. The move back to AWAITING_APPROVAL is allowed only with `revoke=True` on
the state helper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sabra_domain.enums import (
    ApplicationStatus,
    EntityType,
    EventType,
    JobStatus,
)
from sabra_domain.errors import NotFound, ValidationFailure

from .attempt_events import submission_evidence
from .context import EngineContext
from .documents import DocumentService


@dataclass(slots=True)
class RefreshResult:
    application_id: str
    opportunity_id: str
    old_package_id: str
    old_hash: str | None
    package_id: str
    content_hash: str
    revision: int
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "opportunity_id": self.opportunity_id,
            "old_package_id": self.old_package_id,
            "old_hash": self.old_hash,
            "package_id": self.package_id,
            "content_hash": self.content_hash,
            "revision": self.revision,
            "status": self.status,
            "application_status": ApplicationStatus.AWAITING_APPROVAL.value,
        }


@dataclass(slots=True)
class BulkRefreshResult:
    refreshed: list[RefreshResult] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "refreshed": [item.as_dict() for item in self.refreshed],
            "refused": self.refused,
        }


_IN_OR_PAST_SUBMISSION = frozenset(
    {ApplicationStatus.APPLIED.value, ApplicationStatus.APPLYING.value}
)
# Statuses from which a refresh is possible at all (when nothing was submitted).
_REFRESHABLE = frozenset(
    {
        ApplicationStatus.APPROVED.value,
        ApplicationStatus.BLOCKED.value,
        ApplicationStatus.FAILED.value,
    }
)


class PackageRefreshService:
    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx

    def refresh(self, application_id: str) -> RefreshResult:
        opportunity_id, old_package_id, old_hash = self._revoke(application_id)
        prepared = DocumentService(self.ctx).prepare_package(opportunity_id)
        with self.ctx.read_repos() as repos:
            row = repos.packages.get(prepared.package_id)
            revision = row.revision
        return RefreshResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            old_package_id=old_package_id,
            old_hash=old_hash,
            package_id=prepared.package_id,
            content_hash=prepared.content_hash,
            revision=revision,
            status=str(prepared.status),
        )

    def refresh_all_unsubmitted(self) -> BulkRefreshResult:
        with self.ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return BulkRefreshResult()
            ids = [
                row.id
                for row in repos.applications.list_for_candidate(candidate.id)
                if row.status in _REFRESHABLE
            ]
        result = BulkRefreshResult()
        for application_id in ids:
            try:
                result.refreshed.append(self.refresh(application_id))
            except ValidationFailure as exc:
                result.refused.append({"application_id": application_id, "message": str(exc)})
        return result

    def _revoke(self, application_id: str) -> tuple[str, str, str | None]:
        with self.ctx.repos() as repos:
            try:
                application = repos.applications.get(application_id)
            except NotFound as exc:
                raise ValidationFailure(
                    f"application {application_id} was not found; nothing was refreshed"
                ) from exc
            self._refuse_if_submitted(repos, application)
            if application.status not in _REFRESHABLE:
                raise ValidationFailure(
                    f"application {application_id} is {application.status}; only an "
                    "approved, blocked or failed application that was not submitted "
                    "can be refreshed"
                )
            previous_status = application.status
            old_hash = application.approved_package_hash
            old_package_id = application.package_id
            opportunity_id = application.opportunity_id
            opportunity = repos.opportunities.get(opportunity_id)
            if JobStatus(opportunity.status) is JobStatus.EXPIRED:
                raise ValidationFailure(
                    "the posting is no longer available, so this application was not refreshed"
                )
            application.approved_package_hash = None
            application.approval = None
            application.blocked_reason = None
            application.last_error = None
            repos.session.flush()
            repos.applications.set_status(
                application.id,
                ApplicationStatus.AWAITING_APPROVAL,
                revoke=True,
            )
            if JobStatus(opportunity.status) in {JobStatus.APPROVED, JobStatus.FAILED}:
                repos.opportunities.set_status(
                    opportunity_id,
                    JobStatus.AWAITING_APPROVAL,
                    revoke=True,
                )
            repos.events.append(
                EntityType.APPLICATION,
                application.id,
                EventType.APPROVAL_REVOKED,
                {
                    "action": "approval_revoked",
                    "old_hash": old_hash,
                    "old_package_id": old_package_id,
                    "from_status": previous_status,
                    "reason": "package refresh before resubmission",
                },
            )
            return opportunity_id, old_package_id, old_hash

    @staticmethod
    def _refuse_if_submitted(repos: Any, application: Any) -> None:
        """Refuse anything that is, or may be, a submission to the employer.

        APPLIED and APPLYING are refused outright. BLOCKED and FAILED are refused only
        on evidence of a submission (see `submission_evidence`): a blocked attempt that
        never clicked is exactly the one that needs a corrected package.
        """

        if application.status in _IN_OR_PAST_SUBMISSION:
            raise ValidationFailure(
                "already submitted; this application was not refreshed"
                if application.status == ApplicationStatus.APPLIED.value
                or application.submission_time is not None
                else "a submission attempt is already recorded; this application was not refreshed"
            )
        events = repos.events.for_entity(EntityType.APPLICATION, application.id)
        evidence = submission_evidence(application, events)
        if evidence is not None:
            raise ValidationFailure(f"{evidence}; this application was not refreshed")


__all__ = ["BulkRefreshResult", "PackageRefreshService", "RefreshResult"]
