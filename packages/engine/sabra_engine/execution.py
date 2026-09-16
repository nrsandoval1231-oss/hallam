"""Execution service: the only path from an approved package to an external submission.

Order of operations is the safety property. Authorisation is checked first, then the
live form is inspected for conditions requiring a human, then the form is filled and
validated, and only then is anything submitted. Every step that can refuse does
refuse, and a refusal leaves the application at a state that says what happened
rather than at one that implies a submission occurred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sabra_automation.base import (
    ApplicationAdapter,
    ApplicationForm,
    SourceNotSupported,
    SubmissionReceipt,
    ValidationReport,
)
from sabra_domain.enums import (
    ApplicationStatus,
    EntityType,
    EventType,
    JobStatus,
    SafetyStopKind,
)
from sabra_domain.errors import (
    ApprovalRequired,
    ApprovalScopeMismatch,
    NotFound,
    SabraError,
    SafetyStop,
    TruthBoundaryViolation,
)
from sabra_domain.state import application_path

from .approval import ApprovalService
from .context import EngineContext


@dataclass(slots=True)
class ExecutionResult:
    application_id: str
    opportunity_id: str
    outcome: str  # DRY_RUN | SUBMITTED | BLOCKED | FAILED | NOT_SUPPORTED
    receipt: SubmissionReceipt | None = None
    validation: ValidationReport | None = None
    stops: list[dict[str, Any]] = field(default_factory=list)
    adapter: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "opportunity_id": self.opportunity_id,
            "outcome": self.outcome,
            "adapter": self.adapter,
            "detail": self.detail,
            "stops": self.stops,
            "validation": self.validation.as_dict() if self.validation else None,
            "receipt": self.receipt.as_dict() if self.receipt else None,
        }


class AdapterRegistry:
    """Chooses the adapter that understands a posting's application flow."""

    def __init__(self, adapters: list[ApplicationAdapter] | None = None) -> None:
        self.adapters: list[ApplicationAdapter] = adapters or []

    def register(self, adapter: ApplicationAdapter) -> None:
        self.adapters.append(adapter)

    def for_job(self, job: Any) -> ApplicationAdapter:
        for adapter in self.adapters:
            if adapter.supports(job):
                return adapter
        raise SourceNotSupported(
            f"no application adapter supports {getattr(job, 'source', '?')} posting "
            f"{getattr(job, 'source_url', '?')}"
        )

    def supported_kinds(self) -> list[str]:
        return [a.kind for a in self.adapters]


def default_registry(*, dry_run: bool = True, settings: Any = None) -> AdapterRegistry:
    """The adapters shipped in V1. Import is deferred so the browser dependency is
    only required when automation is actually used."""

    from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter

    return AdapterRegistry([GreenhouseApplicationAdapter(dry_run=dry_run, settings=settings)])


class ExecutionService:
    def __init__(
        self,
        ctx: EngineContext,
        *,
        registry: AdapterRegistry | None = None,
        approval: ApprovalService | None = None,
    ) -> None:
        self.ctx = ctx
        self.registry = registry or default_registry(
            dry_run=ctx.settings.automation_dry_run, settings=ctx.settings
        )
        self.approval = approval or ApprovalService(ctx)

    # -- read-only operations -------------------------------------------

    def inspect_application(self, application_id: str) -> ApplicationForm:
        application, package = self.approval.assert_submission_authorised(application_id)
        job = self._job_for(application)
        adapter = self.registry.for_job(job)
        return adapter.inspect(job)

    def validate_application(self, application_id: str) -> ValidationReport:
        application, package = self.approval.assert_submission_authorised(application_id)
        job = self._job_for(application)
        adapter = self.registry.for_job(job)
        form = adapter.inspect(job)
        stops = adapter.detect_stops(form, package)
        report = adapter.validate(package, form) if hasattr(adapter, "validate") else None
        if report is None:
            report = ValidationReport(ok=not stops)
        report.stops = list(report.stops) + [s for s in stops if s not in report.stops]
        report.ok = report.ok and not report.stops
        return report

    # -- submission ------------------------------------------------------

    def submit(
        self,
        application_id: str,
        *,
        allow_real_submission: bool = False,
    ) -> ExecutionResult:
        """Attempt a submission for one approved application.

        `allow_real_submission=False` (the default) performs the full preparation
        path and stops short of clicking submit. That default is why a mistake in an
        operator's script cannot produce an unintended application.
        """

        try:
            application, package = self.approval.assert_submission_authorised(application_id)
        except (ApprovalRequired, ApprovalScopeMismatch, TruthBoundaryViolation) as exc:
            self._record(
                application_id,
                EventType.APPLICATION_BLOCKED,
                {
                    "stage": "authorisation",
                    "reason": type(exc).__name__,
                    "message": str(exc),
                },
            )
            return ExecutionResult(
                application_id=application_id,
                opportunity_id="",
                outcome="BLOCKED",
                detail=str(exc),
                stops=[
                    {
                        "kind": str(SafetyStopKind.APPROVAL_MISSING),
                        "message": str(exc),
                    }
                ],
            )

        job = self._job_for(application)
        opportunity_id = application.opportunity_id

        try:
            adapter = self.registry.for_job(job)
        except SourceNotSupported as exc:
            self._mark_blocked(application_id, SafetyStopKind.ADAPTER_UNSUPPORTED, str(exc))
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="NOT_SUPPORTED",
                detail=str(exc),
                stops=[{"kind": str(SafetyStopKind.ADAPTER_UNSUPPORTED), "message": str(exc)}],
            )

        adapter.dry_run = not allow_real_submission or self.ctx.settings.automation_dry_run

        # 1. Inspect the live form.
        try:
            form = adapter.inspect(job)
        except SafetyStop as stop:
            self._mark_blocked(application_id, stop.kind, stop.message)
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="BLOCKED",
                adapter=adapter.kind,
                detail=stop.message,
                stops=[{"kind": str(stop.kind), "message": stop.message, "details": stop.details}],
            )
        except SabraError as exc:
            self._mark_failed(application_id, str(exc))
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="FAILED",
                adapter=adapter.kind,
                detail=str(exc),
            )

        # 2. Conditions requiring a human stop the attempt here, before anything is typed.
        stops = adapter.detect_stops(form, package)
        if stops:
            self._record(
                application_id,
                EventType.APPLICATION_BLOCKED,
                {
                    "stage": "safety_detection",
                    "stops": [{"kind": str(s.kind), "message": s.message} for s in stops],
                    "form": form.as_dict(),
                },
            )
            self._set_application_blocked(application_id, str(stops[0].kind), stops[0].message)
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="BLOCKED",
                adapter=adapter.kind,
                detail=stops[0].message,
                stops=[
                    {"kind": str(s.kind), "message": s.message, "details": s.details} for s in stops
                ],
            )

        # 3. Fill and validate.
        try:
            prepared = adapter.prepare(package, form)
            report = adapter.validate(package, form)
        except SafetyStop as stop:
            self._set_application_blocked(application_id, stop.kind, stop.message)
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="BLOCKED",
                adapter=adapter.kind,
                detail=stop.message,
                stops=[{"kind": str(stop.kind), "message": stop.message}],
            )
        except SabraError as exc:
            self._mark_failed(application_id, str(exc))
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="FAILED",
                adapter=adapter.kind,
                detail=str(exc),
            )

        if not report.ok:
            self._set_application_blocked(
                application_id,
                str(report.stops[0].kind)
                if report.stops
                else str(SafetyStopKind.MATERIAL_AMBIGUITY),
                "; ".join(s.message for s in report.stops) or "validation failed before submission",
            )
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="BLOCKED",
                adapter=adapter.kind,
                validation=report,
                detail="validation failed",
                stops=[
                    {"kind": str(s.kind), "message": s.message, "details": s.details}
                    for s in report.stops
                ],
            )

        # 4. Submit, or stop short.
        self._record(
            application_id,
            EventType.APPLICATION_STARTED,
            {
                "adapter": adapter.kind,
                "url": form.url,
                "dry_run": adapter.dry_run,
                "form": form.as_dict(),
                "prepared": prepared.as_dict(),
            },
        )

        if adapter.dry_run:
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="DRY_RUN",
                adapter=adapter.kind,
                validation=report,
                detail=(
                    "form inspected, filled and validated; submit deliberately not clicked "
                    "(dry run)"
                ),
            )

        receipt = adapter.submit(prepared)
        return self._record_receipt(application_id, opportunity_id, adapter, receipt)

    # -- state recording -------------------------------------------------

    def _record_receipt(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        receipt: SubmissionReceipt,
    ) -> ExecutionResult:
        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            application.attempts = (application.attempts or 0) + 1

            if receipt.submitted:
                application.submission_time = receipt.submitted_at or datetime.now(UTC)
                application.submission_method = str(receipt.method)
                if receipt.external_confirmation:
                    merged = dict(application.external_confirmation or {})
                    merged.update(receipt.external_confirmation)
                    application.external_confirmation = merged
                repos.session.flush()
                # Walk the graph rather than assuming the next hop: an APPROVED
                # application has to pass through APPLYING, and jumping straight to
                # APPLIED raised *after* the employer had received the application.
                for step in application_path(
                    ApplicationStatus(application.status), ApplicationStatus.APPLIED
                ):
                    repos.applications.set_status(application_id, step)
                opportunity = repos.opportunities.get(opportunity_id)
                if JobStatus(opportunity.status) is JobStatus.APPLYING:
                    repos.opportunities.set_status(opportunity_id, JobStatus.APPLIED)
                elif JobStatus(opportunity.status) is JobStatus.APPROVED:
                    repos.opportunities.set_status(opportunity_id, JobStatus.APPLYING)
                    repos.opportunities.set_status(opportunity_id, JobStatus.APPLIED)
                repos.events.append(
                    EntityType.APPLICATION,
                    application_id,
                    EventType.APPLICATION_SUBMITTED,
                    receipt.as_dict(),
                )
                outcome = "SUBMITTED"
            else:
                application.last_error = receipt.error or "submission did not confirm"
                repos.session.flush()
                for step in application_path(
                    ApplicationStatus(application.status), ApplicationStatus.FAILED
                ):
                    repos.applications.set_status(application_id, step)
                repos.events.append(
                    EntityType.APPLICATION,
                    application_id,
                    EventType.APPLICATION_FAILED,
                    receipt.as_dict(),
                )
                outcome = "FAILED"

            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome=outcome,
                adapter=adapter.kind,
                receipt=receipt,
                detail=receipt.error,
            )

    def _mark_blocked(self, application_id: str, kind: Any, message: str) -> None:
        self._record(
            application_id,
            EventType.APPLICATION_BLOCKED,
            {"stage": "adapter", "kind": str(kind), "message": message},
        )
        self._set_application_blocked(application_id, str(kind), message)

    def _mark_failed(self, application_id: str, message: str) -> None:
        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            application.last_error = message[:2000]
            application.attempts = (application.attempts or 0) + 1
            repos.session.flush()
            for step in application_path(
                ApplicationStatus(application.status), ApplicationStatus.FAILED
            ):
                repos.applications.set_status(application_id, step)
            repos.events.append(
                EntityType.APPLICATION,
                application_id,
                EventType.APPLICATION_FAILED,
                {"stage": "adapter", "message": message},
            )

    def _set_application_blocked(self, application_id: str, kind: str, message: str) -> None:
        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            application.blocked_reason = kind
            application.last_error = message[:2000]
            repos.session.flush()
            # Same graph-derived path as the other state moves, so this cannot drift
            # out of step with APPLICATION_TRANSITIONS either.
            for step in application_path(
                ApplicationStatus(application.status), ApplicationStatus.BLOCKED
            ):
                repos.applications.set_status(application_id, step)

    def _record(self, application_id: str, event_type: EventType, payload: dict[str, Any]) -> None:
        with self.ctx.repos() as repos:
            repos.events.append(EntityType.APPLICATION, application_id, event_type, payload)

    def _job_for(self, application: Any) -> Any:
        with self.ctx.read_repos() as repos:
            try:
                return repos.jobs.get(application.job_id)
            except NotFound as exc:
                raise NotFound("job", application.job_id) from exc
