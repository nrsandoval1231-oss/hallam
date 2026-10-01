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
from sabra_automation.handoff import HandoffResult, PlaywrightHandoff
from sabra_domain.enums import (
    ApplicationStatus,
    EntityType,
    EventType,
    JobStatus,
    SafetyStopKind,
    SubmissionMethod,
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
    plan: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "opportunity_id": self.opportunity_id,
            "outcome": self.outcome,
            "adapter": self.adapter,
            "detail": self.detail,
            "stops": self.stops,
            "plan": self.plan,
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


def fill_plan(package: Any, job: Any) -> dict[str, Any]:
    """What a real run would type, without opening a page or reading a network.

    Values already on the approved package are named. Nothing is invented, and
    the résumé text itself is not copied into the plan.
    """

    answers = [
        answer.question_key
        for answer in getattr(package, "answers", []) or []
        if getattr(answer, "answer", None) and not getattr(answer, "requires_user_input", False)
    ]
    return {
        "navigated": False,
        "typed": False,
        "uploaded": False,
        "employer_url": str(getattr(job, "source_url", "") or ""),
        "would_fill": {
            "name": bool(getattr(package, "applicant_name", None)),
            "email": bool(getattr(package, "applicant_email", None)),
            "phone": bool(getattr(package, "applicant_phone", None)),
            "employment": len(getattr(package, "employment", []) or []),
            "education": len(getattr(package, "education", []) or []),
            "resume": bool((getattr(package, "tailored_resume_markdown", None) or "").strip()),
            "answers": answers,
        },
        "note": "dry run stopped before opening the employer form; nothing was typed or uploaded",
    }


def default_registry(*, dry_run: bool = True, settings: Any = None) -> AdapterRegistry:
    """The adapters shipped in V1. Import is deferred so the browser dependency is
    only required when automation is actually used."""

    from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
    from sabra_automation.lever_adapter import LeverApplicationAdapter

    return AdapterRegistry(
        [
            GreenhouseApplicationAdapter(dry_run=dry_run, settings=settings),
            LeverApplicationAdapter(dry_run=dry_run, settings=settings),
        ]
    )


class ExecutionService:
    def __init__(
        self,
        ctx: EngineContext,
        *,
        registry: AdapterRegistry | None = None,
        approval: ApprovalService | None = None,
        handoff: Any = None,
        handoff_prompt: Any = None,
    ) -> None:
        self.ctx = ctx
        self.registry = registry or default_registry(
            dry_run=ctx.settings.automation_dry_run, settings=ctx.settings
        )
        self.approval = approval or ApprovalService(ctx)
        self.handoff = handoff
        self.handoff_prompt = handoff_prompt

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

        `allow_real_submission=False` (the default) does not open the employer
        form, does not type, and does not upload. It reports a fill plan built
        from the approved package and stops. A dry run that opened the live form
        would type the résumé into an ATS that can store the upload immediately.
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
        plan = fill_plan(package, job) if adapter.dry_run else None
        attempted = {
            "adapter": adapter.kind,
            "dry_run": adapter.dry_run,
            "url": str(getattr(job, "source_url", "") or ""),
        }
        if adapter.dry_run:
            attempted.update(
                {"navigated": False, "typed": False, "uploaded": False, "plan": plan}
            )
        self._record(application_id, EventType.APPLICATION_ATTEMPTED, attempted)
        if adapter.dry_run:
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="DRY_RUN",
                adapter=adapter.kind,
                detail=(
                    "dry run did not open the employer form and did not type or upload anything"
                ),
                plan=plan,
            )

        # 1. Inspect the live form. A challenge, including a probe that failed,
        # is handed to a person. It is never submitted by the adapter.
        try:
            form = adapter.inspect(job)
        except SafetyStop as stop:
            if stop.kind == SafetyStopKind.CAPTCHA:
                return self._hand_to_human(
                    application_id,
                    opportunity_id,
                    adapter,
                    package,
                    form=None,
                    url=str(getattr(job, "source_url", "") or ""),
                    detail=stop.message,
                )
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

        if form.has_captcha or form.challenge_detection_failed:
            return self._hand_to_human(
                application_id,
                opportunity_id,
                adapter,
                package,
                form=form,
                url=form.url,
                detail=(
                    "challenge detection failed; refusing to submit"
                    if form.challenge_detection_failed
                    else "a CAPTCHA or bot challenge is on the form; a person must submit it"
                ),
            )

        # 2. Anything else that requires a human (an attestation, a login wall)
        # blocks. Those are not challenges a person solves by clicking the widget.
        stops = [
            stop
            for stop in adapter.detect_stops(form, package)
            if stop.kind != SafetyStopKind.CAPTCHA
        ]
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

        # 3. Fill and validate. An unknown required field blocks this application.
        try:
            prepared = adapter.prepare(package, form)
            report = adapter.validate(package, form)
        except SafetyStop as stop:
            if stop.kind == SafetyStopKind.CAPTCHA:
                return self._hand_to_human(
                    application_id,
                    opportunity_id,
                    adapter,
                    package,
                    form=form,
                    url=form.url,
                    detail=stop.message,
                )
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
            captcha_only = report.stops and all(
                stop.kind == SafetyStopKind.CAPTCHA for stop in report.stops
            )
            if captcha_only:
                return self._hand_to_human(
                    application_id,
                    opportunity_id,
                    adapter,
                    package,
                    form=form,
                    url=form.url,
                    detail=report.stops[0].message,
                )
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

        # 4. Submit, or stop short. Dry-run never clicks.
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

    def _hand_to_human(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        package: Any,
        *,
        form: ApplicationForm | None,
        url: str,
        detail: str,
    ) -> ExecutionResult:
        """Do not submit. In a real run, open the pre-filled form headed."""

        self._record(
            application_id,
            EventType.CHALLENGE_DETECTED,
            {
                "adapter": adapter.kind,
                "url": url,
                "detail": detail,
                "dry_run": adapter.dry_run,
            },
        )
        if adapter.dry_run:
            self._record(
                application_id,
                EventType.HANDED_TO_HUMAN,
                {
                    "opened": False,
                    "dry_run": True,
                    "detail": "dry-run does not open a browser and does not submit",
                },
            )
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="DRY_RUN",
                adapter=adapter.kind,
                detail=(
                    "a challenge was detected; dry-run did not submit and did not open a browser. "
                    + detail
                ),
                stops=[{"kind": str(SafetyStopKind.CAPTCHA), "message": detail}],
            )

        if form is not None:
            try:
                adapter.prepare(package, form)
            except SabraError as exc:
                self._mark_failed(application_id, str(exc))
                return ExecutionResult(
                    application_id=application_id,
                    opportunity_id=opportunity_id,
                    outcome="FAILED",
                    adapter=adapter.kind,
                    detail=str(exc),
                )

        self._record(
            application_id,
            EventType.HANDED_TO_HUMAN,
            {"opened": True, "headed": True, "url": url, "dry_run": False},
        )
        handoff = self.handoff or PlaywrightHandoff(prompt=self.handoff_prompt)
        result = handoff.run(
            adapter=adapter,
            package=package,
            form=form,
            url=url,
            settings=self.ctx.settings,
        )
        return self._record_handoff(application_id, opportunity_id, adapter, result)

    def _record_handoff(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        result: HandoffResult,
    ) -> ExecutionResult:
        outcome = result.outcome if result.outcome in {"submitted", "abandoned", "failed"} else "failed"
        event = {
            "submitted": EventType.APPLICATION_SUBMITTED,
            "abandoned": EventType.APPLICATION_ABANDONED,
            "failed": EventType.APPLICATION_FAILED,
        }[outcome]
        payload = {
            "outcome": outcome,
            "source": result.source,
            "url": result.url,
            "confirmation_text": result.confirmation_text,
            "opened_headed": result.opened_headed,
            "error": result.error,
            "adapter": adapter.kind,
        }
        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            application.attempts = (application.attempts or 0) + 1
            if outcome == "submitted":
                application.submission_time = datetime.now(UTC)
                application.submission_method = str(SubmissionMethod.HEADED_HUMAN)
                merged = dict(application.external_confirmation or {})
                merged.update(
                    {
                        "source": result.source,
                        "url": result.url,
                        "confirmation_detected": result.source == "confirmation_page",
                    }
                )
                application.external_confirmation = merged
                repos.session.flush()
                for step in application_path(
                    ApplicationStatus(application.status), ApplicationStatus.APPLIED
                ):
                    repos.applications.set_status(application_id, step)
                opportunity = repos.opportunities.get(opportunity_id)
                if JobStatus(opportunity.status) is JobStatus.APPROVED:
                    repos.opportunities.set_status(opportunity_id, JobStatus.APPLYING)
                    repos.opportunities.set_status(opportunity_id, JobStatus.APPLIED)
            elif outcome == "abandoned":
                application.last_error = result.error or "the person left the form without submitting"
                repos.session.flush()
                for step in application_path(
                    ApplicationStatus(application.status), ApplicationStatus.WITHDRAWN
                ):
                    repos.applications.set_status(application_id, step)
                opportunity = repos.opportunities.get(opportunity_id)
                if JobStatus(opportunity.status) is JobStatus.APPROVED:
                    repos.opportunities.set_status(opportunity_id, JobStatus.WITHDRAWN)
            else:
                application.last_error = result.error or "the headed attempt failed"
                repos.session.flush()
                for step in application_path(
                    ApplicationStatus(application.status), ApplicationStatus.FAILED
                ):
                    repos.applications.set_status(application_id, step)
                opportunity = repos.opportunities.get(opportunity_id)
                if JobStatus(opportunity.status) is JobStatus.APPROVED:
                    repos.opportunities.set_status(opportunity_id, JobStatus.APPLYING)
                    repos.opportunities.set_status(opportunity_id, JobStatus.FAILED)
            repos.events.append(EntityType.APPLICATION, application_id, event, payload)
        return ExecutionResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            outcome=outcome.upper(),
            adapter=adapter.kind,
            detail=result.error or result.source,
        )

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
