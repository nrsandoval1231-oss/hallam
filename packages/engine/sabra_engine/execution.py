"""Execution service: the only path from an approved package to an external submission.

Order of operations is the safety property:

1. Authorise and claim, in ONE write transaction: the hash-bound approval is re-checked,
   the application moves to APPLYING, and APPLICATION_STARTED is recorded. Only one
   caller can win the claim, and it happens before any browser opens.
2. One browser session for the whole run (`adapter.session`). The live form is
   inspected, validated, filled and submitted on that same page.
3. A challenge is never solved: the pre-filled form is handed to a person in a headed
   browser, and the application stays APPLYING until an outcome is known.

A refusal leaves the application at a state that says what happened rather than at one
that implies a submission occurred. Nothing below raises out of a browser or handoff
failure: it is recorded and returned as an `ExecutionResult`. A click that nothing
confirms is UNCONFIRMED, never FAILED and never SUBMITTED, and it is resolved only by a
reported outcome.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sabra_automation.base import (
    ApplicationAdapter,
    ApplicationForm,
    ClaimLost,
    SourceNotSupported,
    SubmissionReceipt,
    ValidationReport,
)
from sabra_automation.handoff import (
    HANDOFF_OUTCOMES,
    OUTCOME_CLOSED,
    OUTCOME_SUPERSEDED,
    OUTCOME_TIMEOUT,
    HandoffControl,
    HandoffResult,
    PlaywrightHandoff,
)
from sabra_domain.enums import (
    ApplicationStatus,
    EntityType,
    EventType,
    JobStatus,
    SafetyStopKind,
    SourceKind,
    SubmissionMethod,
    TruthLevel,
)
from sabra_domain.errors import (
    ApprovalRequired,
    ApprovalScopeMismatch,
    NotFound,
    SafetyStop,
    TruthBoundaryViolation,
    ValidationFailure,
)
from sabra_domain.state import application_path, can_transition_job

from .approval import ApprovalService
from .attempt_events import attempt_has_unconfirmed, waiting_for_person
from .context import EngineContext
from .handoff_manager import (
    HANDOFFS,
    STATE_NEEDS_REPORT,
    STATE_OPEN,
    HandoffManager,
    HandoffRecord,
)


@dataclass(slots=True)
class ExecutionResult:
    application_id: str
    opportunity_id: str
    # DRY_RUN | SUBMITTED | BLOCKED | FAILED | NOT_SUPPORTED | UNCONFIRMED | ABANDONED
    # | HANDED_TO_HUMAN | CONFLICT (the application moved under this run; see detail)
    outcome: str
    receipt: SubmissionReceipt | None = None
    validation: ValidationReport | None = None
    stops: list[dict[str, Any]] = field(default_factory=list)
    adapter: str | None = None
    detail: str | None = None
    plan: dict[str, Any] | None = None
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "opportunity_id": self.opportunity_id,
            "outcome": self.outcome,
            "adapter": self.adapter,
            "detail": self.detail,
            "url": self.url,
            "stops": self.stops,
            "plan": self.plan,
            "validation": self.validation.as_dict() if self.validation else None,
            "receipt": self.receipt.as_dict() if self.receipt else None,
        }


@dataclass(slots=True)
class _HandoffRequest:
    """The run met a challenge: hand the form to a person after the session closes."""

    form: ApplicationForm | None
    url: str
    detail: str


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
        if getattr(answer, "answer", None)
        and not getattr(answer, "requires_user_input", False)
        and getattr(answer, "level", None) == TruthLevel.VERIFIED
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


BLOCKED_POSTING_GONE = "posting no longer available"


def default_posting_verifier(ctx: EngineContext) -> Callable[[Any], Any]:
    """The check that a posting is still live: the provider's own `verify`.

    Used only on the real-submission path, so a dry run and the whole test suite never
    reach it. It returns the provider's `VerifyResult`, or None when no provider can be
    built for the job. It may raise (network down); the caller treats that as "could not
    check", never as "gone".
    """

    def verify(job: Any) -> Any:
        from sabra_providers.base import ProviderRef
        from sabra_providers.http import HttpClient
        from sabra_providers.registry import PROVIDER_TYPES

        try:
            kind = SourceKind(str(getattr(job, "source", "")).upper())
        except ValueError:
            return None
        token = getattr(job, "board_token", None)
        job_id = getattr(job, "source_job_id", None)
        if not token or not job_id:
            return None
        ref = ProviderRef(
            kind=kind,
            token=str(token),
            source_job_id=str(job_id),
            url=str(getattr(job, "source_url", "") or ""),
        )
        # One attempt with the discovery timeout and no retries: this runs inside a
        # dashboard request, and a hung network must not hold it for a minute.
        http = HttpClient(
            timeout=ctx.settings.discovery_timeout_seconds,
            max_attempts=1,
            user_agent=ctx.settings.discovery_user_agent,
        )
        try:
            provider = None
            for definition in ctx.registry:
                if definition.entry.kind is kind and definition.entry.token == token:
                    provider = ctx.registry.build_provider(
                        definition.id, http=http, settings=ctx.settings
                    )
                    break
            if provider is None:
                provider_type = PROVIDER_TYPES.get(kind)
                if provider_type is None:
                    return None
                provider = provider_type(str(token), http=http, settings=ctx.settings)
            return provider.verify(ref)
        finally:
            http.close()

    return verify


def posting_is_gone(result: Any) -> bool:
    """Whether a provider's `VerifyResult` says the posting was taken down.

    Only a clear answer counts: not available AND either an HTTP 404 / 410 or a detail
    that says the posting is closed. An unavailable result for any other reason (a
    timeout, a 5xx, a refused connection) is "could not check" and never expires a job.
    """

    if result is None or getattr(result, "available", True):
        return False
    status = getattr(result, "http_status", None)
    if status in {404, 410}:
        return True
    detail = str(getattr(result, "detail", "") or "").lower()
    return "posting closed" in detail or "no longer on" in detail


def _advance_opportunity(repos: Any, opportunity_id: str, target: JobStatus) -> None:
    """Move the opportunity toward APPLIED/FAILED along legal edges only.

    APPROVED and FAILED (a retry) pass through APPLYING. Any other state is left
    alone: this never invents a lifecycle step that did not happen.
    """

    opportunity = repos.opportunities.get(opportunity_id)
    current = JobStatus(opportunity.status)
    if current is target:
        return
    if current in {JobStatus.APPROVED, JobStatus.FAILED}:
        repos.opportunities.set_status(opportunity_id, JobStatus.APPLYING)
        current = JobStatus.APPLYING
    if current is JobStatus.APPLYING:
        repos.opportunities.set_status(opportunity_id, target)


class ExecutionService:
    def __init__(
        self,
        ctx: EngineContext,
        *,
        registry: AdapterRegistry | None = None,
        approval: ApprovalService | None = None,
        handoff: Any = None,
        handoff_prompt: Any = None,
        background_handoff: bool = False,
        ctx_factory: Any = None,
        handoffs: HandoffManager | None = None,
        verify_posting: Callable[[Any], Any] | None = None,
    ) -> None:
        """`handoff` is injectable (tests use a fake that needs no Playwright).

        `verify_posting(job)` checks that the posting is still live before a real
        submission is claimed. It is injectable so tests never touch the network; the
        default asks the job's provider. It returns a `VerifyResult` (or None), and a
        raise means "could not check".

        `background_handoff=True` is the dashboard path: a challenge starts the headed
        browser in a thread and `submit` returns at once. `ctx_factory` builds the
        thread's own engine context, because the request's context is closed when the
        request ends.
        """

        self.ctx = ctx
        self.registry = registry or default_registry(
            dry_run=ctx.settings.automation_dry_run, settings=ctx.settings
        )
        self.approval = approval or ApprovalService(ctx)
        self.handoff = handoff
        self.handoff_prompt = handoff_prompt
        self.background_handoff = background_handoff
        self.ctx_factory = ctx_factory
        self.handoffs = handoffs or HANDOFFS
        self.verify_posting = verify_posting

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

        self._refuse_if_in_progress(application_id)
        try:
            application, package = self.approval.assert_submission_authorised(application_id)
        except (ApprovalRequired, ApprovalScopeMismatch, TruthBoundaryViolation) as exc:
            return self._authorisation_refused(application_id, exc)
        authorised_status = ApplicationStatus(application.status)

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
        if adapter.dry_run:
            plan = fill_plan(package, job)
            self._record(
                application_id,
                EventType.APPLICATION_ATTEMPTED,
                {
                    "adapter": adapter.kind,
                    "dry_run": True,
                    "url": str(getattr(job, "source_url", "") or ""),
                    "navigated": False,
                    "typed": False,
                    "uploaded": False,
                    "plan": plan,
                },
            )
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

        # Real submission. The run is registered as in flight for its whole length, so
        # no outcome can be reported (and no second run started) while it is still
        # deciding what happened. Then re-authorise and claim in one transaction, before
        # any browser opens. Whoever loses the claim is refused, not queued.
        self.handoffs.begin_run(application_id)
        claimed = False
        try:
            # The posting must still exist. This runs under the run registration, so no
            # second run or report can start meanwhile, and a closed posting is decided
            # by a compare-and-set from the status that was authorised.
            gone = self._check_posting_live(
                application_id, opportunity_id, adapter, job, authorised_status
            )
            if gone is not None:
                return gone

            try:
                _claimed, package = self._claim(application_id, adapter, job)
            except (ApprovalRequired, ApprovalScopeMismatch, TruthBoundaryViolation) as exc:
                return self._authorisation_refused(application_id, exc)
            claimed = True

            try:
                outcome = self._drive(application_id, opportunity_id, adapter, package, job)
                if isinstance(outcome, _HandoffRequest):
                    return self._hand_to_human(
                        application_id,
                        opportunity_id,
                        adapter,
                        package,
                        form=outcome.form,
                        url=outcome.url,
                        detail=outcome.detail,
                    )
                return outcome
            except Exception as exc:  # noqa: BLE001 - nothing may escape with the claim held
                return self._unexpected(application_id, opportunity_id, adapter, exc)
        except BaseException as exc:
            # Ctrl+C, SystemExit or a failure while recording: the claim is held and no
            # outcome is known. Leave a mark the report path recognises, then re-raise.
            # Before the claim there is nothing of ours to mark.
            if claimed:
                self._record_interrupted(application_id, exc)
            raise
        finally:
            self.handoffs.end_run(application_id)

    def _check_posting_live(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        job: Any,
        authorised_status: ApplicationStatus,
    ) -> ExecutionResult | None:
        """Ask the provider whether the posting is still up, before any claim or browser.

        Gone (a clear 404 / 410 or "closed"): the opportunity moves to EXPIRED through the
        graph and the application is BLOCKED with reason "posting no longer available",
        by a compare-and-set from the status that was authorised, never by walking the
        graph (a walk could pass through an approval-gated state, or move a row another
        process now owns). If the row moved while the network was being asked, only
        JOB_EXPIRED and a warning are recorded and the attempt is refused. Anything else,
        including a verify that raises because the network is down, proceeds with a
        WARNING_RECORDED event: a failed check is not evidence that the posting is gone.
        """

        verifier = self.verify_posting or default_posting_verifier(self.ctx)
        try:
            result = verifier(job)
        except Exception as exc:  # noqa: BLE001 - "could not check" is not "gone"
            self._record(
                application_id,
                EventType.WARNING_RECORDED,
                {
                    "warning": "posting_check_failed",
                    "message": (
                        "could not confirm the posting is still live: "
                        f"{type(exc).__name__}: {exc}"
                    )[:500],
                    "proceeding": True,
                },
            )
            return None
        if result is None:
            self._record(
                application_id,
                EventType.WARNING_RECORDED,
                {
                    "warning": "posting_check_unavailable",
                    "message": "no provider could check that the posting is still live",
                    "proceeding": True,
                },
            )
            return None
        if not posting_is_gone(result):
            return None

        detail = str(getattr(result, "detail", "") or "")
        status = getattr(result, "http_status", None)
        with self.ctx.repos() as repos:
            if authorised_status is ApplicationStatus.BLOCKED:
                # Already where it needs to be; confirm it is still there.
                held = repos.applications.confirm_status(
                    application_id, expected=ApplicationStatus.BLOCKED
                )
            else:
                held = repos.applications.claim_status(
                    application_id,
                    expected=authorised_status,
                    target=ApplicationStatus.BLOCKED,
                )
            moved_to = None if held else repos.applications.get(application_id).status
            # Another process that holds the claim may be about to submit: leave the job
            # alone, or it would sit EXPIRED beside an APPLIED application. Any other move
            # (a refresh, a withdrawal) leaves nothing in flight, so the job still expires.
            if moved_to != ApplicationStatus.APPLYING.value:
                opportunity = repos.opportunities.get(opportunity_id)
                current = JobStatus(opportunity.status)
                if can_transition_job(current, JobStatus.EXPIRED):
                    repos.opportunities.set_status(opportunity_id, JobStatus.EXPIRED)
                repos.events.append(
                    EntityType.OPPORTUNITY,
                    opportunity_id,
                    EventType.JOB_EXPIRED,
                    {
                        "reason": BLOCKED_POSTING_GONE,
                        "from": str(current),
                        "http_status": status,
                        "detail": detail,
                        "application_id": application_id,
                    },
                )
            if not held:
                repos.events.append(
                    EntityType.APPLICATION,
                    application_id,
                    EventType.WARNING_RECORDED,
                    {
                        "warning": "posting_gone_status_moved",
                        "message": (
                            f"{BLOCKED_POSTING_GONE}, but the application was {moved_to} "
                            f"when the check finished (it was {authorised_status} when "
                            "authorised); nothing was changed on it"
                        ),
                        "detail": detail,
                    },
                )
                return ExecutionResult(
                    application_id=application_id,
                    opportunity_id=opportunity_id,
                    outcome="CONFLICT",
                    adapter=adapter.kind,
                    detail=(
                        f"{BLOCKED_POSTING_GONE}; the application is now {moved_to}, so "
                        "this attempt was refused and nothing was opened"
                    ),
                )
            application = repos.applications.get(application_id)
            application.blocked_reason = BLOCKED_POSTING_GONE
            application.last_error = (BLOCKED_POSTING_GONE + (": " + detail if detail else ""))[
                :2000
            ]
            repos.session.flush()
            repos.events.append(
                EntityType.APPLICATION,
                application_id,
                EventType.APPLICATION_BLOCKED,
                {
                    "stage": "posting_check",
                    "reason": BLOCKED_POSTING_GONE,
                    "from": str(authorised_status),
                    "http_status": status,
                    "detail": detail,
                },
            )
        return ExecutionResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            outcome="BLOCKED",
            adapter=adapter.kind,
            detail=f"{BLOCKED_POSTING_GONE}; no browser was opened",
            stops=[
                {
                    "kind": str(SafetyStopKind.MATERIAL_AMBIGUITY),
                    "message": BLOCKED_POSTING_GONE,
                }
            ],
        )

    def _record_interrupted(self, application_id: str, exc: BaseException) -> None:
        with suppress(Exception):
            if self._status_of(application_id) == ApplicationStatus.APPLYING.value:
                self._record(
                    application_id,
                    EventType.HANDED_TO_HUMAN,
                    {
                        "state": "interrupted",
                        "exception": type(exc).__name__,
                        "message": str(exc)[:300],
                    },
                )

    def _refuse_if_in_progress(self, application_id: str) -> None:
        with self.ctx.read_repos() as repos:
            status = repos.applications.get(application_id).status
        if status == ApplicationStatus.APPLYING.value:
            raise ValidationFailure(
                f"a submission is already in progress for application {application_id}; "
                "wait for it to finish, or report its outcome if a person already handled it"
            )

    def _authorisation_refused(self, application_id: str, exc: Exception) -> ExecutionResult:
        self._record(
            application_id,
            EventType.APPLICATION_BLOCKED,
            {"stage": "authorisation", "reason": type(exc).__name__, "message": str(exc)},
        )
        return ExecutionResult(
            application_id=application_id,
            opportunity_id="",
            outcome="BLOCKED",
            detail=str(exc),
            stops=[{"kind": str(SafetyStopKind.APPROVAL_MISSING), "message": str(exc)}],
        )

    def _claim(
        self, application_id: str, adapter: ApplicationAdapter, job: Any
    ) -> tuple[Any, Any]:
        """Authorise, move to APPLYING and record APPLICATION_STARTED: one transaction.

        The move is a compare-and-set through the transition graph, so two callers
        cannot both claim the same application. If the authorisation fails, nothing
        is written. Returns the application and the package that THIS authorisation
        verified: the run must type that content, not whatever an earlier unlocked
        read saw.
        """

        url = str(getattr(job, "source_url", "") or "")
        with self.ctx.repos() as repos:
            if repos.applications.get(application_id).status == ApplicationStatus.APPLYING.value:
                raise ValidationFailure(
                    f"a submission is already in progress for application {application_id}"
                )
            application, package = self.approval.assert_submission_authorised(
                application_id, repos=repos
            )
            prior = ApplicationStatus(application.status)
            if not repos.applications.claim_status(
                application_id, expected=prior, target=ApplicationStatus.APPLYING
            ):
                raise ValidationFailure(
                    f"a submission is already in progress for application {application_id}"
                )
            application = repos.applications.get(application_id)
            application.blocked_reason = None
            repos.session.flush()
            payload = {"adapter": adapter.kind, "dry_run": False, "url": url, "from": str(prior)}
            repos.events.append(
                EntityType.APPLICATION, application_id, EventType.APPLICATION_ATTEMPTED, payload
            )
            repos.events.append(
                EntityType.APPLICATION, application_id, EventType.APPLICATION_STARTED, payload
            )
            return application, package

    def _drive(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        package: Any,
        job: Any,
    ) -> ExecutionResult | _HandoffRequest:
        """One browser session for the whole run; closed on every path out."""

        url = adapter.application_url(job)
        # Render the files first: a document that cannot be produced stops the run before
        # the employer's page is even opened.
        try:
            adapter.prepare_documents(package)
        except SafetyStop as stop:
            return self._blocked_by_stop(application_id, opportunity_id, adapter, stop)
        with adapter.session(url):
            # Checked inside the adapter immediately before the click: if a report (or
            # anything else) moved the application off APPLYING, nothing is clicked.
            adapter.pre_click_check = lambda: self._still_claimed(application_id)
            return self._drive_session(application_id, opportunity_id, adapter, package, job, url)

    def _still_claimed(self, application_id: str) -> bool:
        """Compare-and-set: the row is still APPLYING, checked under the write lock."""

        with self.ctx.repos() as repos:
            return repos.applications.confirm_status(
                application_id, expected=ApplicationStatus.APPLYING
            )

    def _status_of(self, application_id: str) -> str:
        with self.ctx.read_repos() as repos:
            return str(repos.applications.get(application_id).status)

    def _drive_session(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        package: Any,
        job: Any,
        url: str,
    ) -> ExecutionResult | _HandoffRequest:
        # 1. Inspect the live form. A challenge, including a probe that failed,
        # is handed to a person. It is never submitted by the adapter.
        try:
            form = adapter.inspect(job)
        except SafetyStop as stop:
            if stop.kind == SafetyStopKind.CAPTCHA:
                return _HandoffRequest(form=None, url=url, detail=stop.message)
            return self._blocked_by_stop(application_id, opportunity_id, adapter, stop)

        if form.has_captcha or form.challenge_detection_failed:
            return _HandoffRequest(
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

        # 3. Validate BEFORE typing anything: an unknown required field blocks this
        # application, and a form we are not going to submit is not filled or uploaded to.
        try:
            report = adapter.validate(package, form)
        except SafetyStop as stop:
            if stop.kind == SafetyStopKind.CAPTCHA:
                return _HandoffRequest(form=form, url=form.url, detail=stop.message)
            return self._blocked_by_stop(application_id, opportunity_id, adapter, stop)

        if not report.ok:
            captcha_only = report.stops and all(
                stop.kind == SafetyStopKind.CAPTCHA for stop in report.stops
            )
            if captcha_only:
                return _HandoffRequest(form=form, url=form.url, detail=report.stops[0].message)
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

        # 4. Fill the live page. This is the page `submit` will click.
        try:
            prepared = adapter.prepare(package, form)
        except SafetyStop as stop:
            if stop.kind == SafetyStopKind.CAPTCHA:
                return _HandoffRequest(form=form, url=form.url, detail=stop.message)
            return self._blocked_by_stop(application_id, opportunity_id, adapter, stop)

        self._record(
            application_id,
            EventType.APPLICATION_ATTEMPTED,
            {
                "stage": "prepared",
                "adapter": adapter.kind,
                "url": form.url,
                "dry_run": False,
                "form": form.as_dict(),
                "prepared": prepared.as_dict(),
            },
        )

        # 5. Submit on that same page.
        try:
            receipt = adapter.submit(prepared)
        except ClaimLost as lost:
            self._record(
                application_id,
                EventType.APPLICATION_BLOCKED,
                {"stage": "claim_lost", "message": lost.message},
            )
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="CONFLICT",
                adapter=adapter.kind,
                detail=lost.message,
            )
        except SafetyStop as stop:
            if stop.kind == SafetyStopKind.CAPTCHA:
                return _HandoffRequest(form=form, url=form.url, detail=stop.message)
            return self._blocked_by_stop(application_id, opportunity_id, adapter, stop)
        return self._record_receipt(application_id, opportunity_id, adapter, receipt)

    def _blocked_by_stop(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        stop: SafetyStop,
    ) -> ExecutionResult:
        self._mark_blocked(application_id, stop.kind, stop.message)
        return ExecutionResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            outcome="BLOCKED",
            adapter=adapter.kind,
            detail=stop.message,
            stops=[{"kind": str(stop.kind), "message": stop.message, "details": stop.details}],
        )

    def _unexpected(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        exc: Exception,
    ) -> ExecutionResult:
        """Record any exception from a browser or handoff call and return a result.

        If the submit control was clicked, the employer may have the application, so
        the state stays APPLYING with an UNCONFIRMED event. Otherwise nothing was
        sent and the attempt FAILED.
        """

        message = f"{type(exc).__name__}: {exc}"
        status = self._status_of(application_id)
        if status != ApplicationStatus.APPLYING.value:
            self._record(
                application_id,
                EventType.APPLICATION_BLOCKED,
                {"stage": "conflict", "conflicting_status": status, "message": message},
            )
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="CONFLICT",
                adapter=adapter.kind,
                detail=f"the application was {status} when this run failed: {message}",
            )
        if getattr(adapter, "click_attempted", False):
            return self._mark_unconfirmed(
                application_id,
                opportunity_id,
                adapter,
                message=f"an error followed the submit click: {message}",
            )
        self._mark_failed(application_id, message)
        return ExecutionResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            outcome="FAILED",
            adapter=adapter.kind,
            detail=message,
        )

    # -- human handoff ---------------------------------------------------

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
        """Do not submit. Open the form in a headed browser for the person.

        The form is filled only inside that headed session. The application stays
        APPLYING until a person reports an outcome or the page confirms.
        """

        self._record(
            application_id,
            EventType.CHALLENGE_DETECTED,
            {"adapter": adapter.kind, "url": url, "detail": detail, "dry_run": False},
        )
        self._record(
            application_id,
            EventType.HANDED_TO_HUMAN,
            {
                "headed": True,
                "url": url,
                "dry_run": False,
                "state": "starting",
                "background": self.background_handoff,
            },
        )
        handoff = self.handoff or PlaywrightHandoff(prompt=self.handoff_prompt)
        if self.background_handoff:
            return self._start_background_handoff(
                application_id, opportunity_id, adapter, package, handoff, form=form, url=url
            )
        control = HandoffControl(self.ctx.settings.handoff_timeout_seconds)
        try:
            result = handoff.run(
                adapter=adapter,
                package=package,
                form=form,
                url=url,
                settings=self.ctx.settings,
                control=control,
                on_event=self._event_sink(application_id),
            )
        except Exception as exc:  # noqa: BLE001 - the person never got the form
            return self._handoff_error(application_id, opportunity_id, adapter.kind, exc)
        return self._finish_handoff(application_id, opportunity_id, adapter.kind, result)

    def _start_background_handoff(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        package: Any,
        handoff: Any,
        *,
        form: ApplicationForm | None,
        url: str,
    ) -> ExecutionResult:
        adapter_kind = adapter.kind
        settings = self.ctx.settings

        def target(record: HandoffRecord) -> None:
            worker: ExecutionService | None = None
            result: HandoffResult | None = None
            close: Any = lambda: None  # noqa: E731
            try:
                worker, close = self._worker()
                running = worker
                # The window closes itself once the application is resolved elsewhere.
                record.control.superseded = (
                    lambda: running._status_of(application_id)
                    != ApplicationStatus.APPLYING.value
                )
                try:
                    result = handoff.run(
                        adapter=adapter,
                        package=package,
                        form=form,
                        url=url,
                        settings=settings,
                        control=record.control,
                        on_event=running._event_sink(application_id, record),
                    )
                except Exception as exc:  # noqa: BLE001
                    record.state = "error"
                    record.detail = str(exc)
                    running._handoff_error(application_id, opportunity_id, adapter_kind, exc)
                    return
                record.outcome = result.outcome
                record.detail = result.error
                if result.outcome == OUTCOME_SUPERSEDED:
                    record.state = "superseded"
                    return
                record.state = (
                    STATE_NEEDS_REPORT if result.outcome == OUTCOME_CLOSED else result.outcome
                )
                # A confirmation that arrives after the row moved on is still recorded.
                running._finish_handoff(application_id, opportunity_id, adapter_kind, result)
            except Exception as exc:  # noqa: BLE001 - never die silently at "starting"
                record.state = "error"
                record.detail = f"{record.detail or ''} {type(exc).__name__}: {exc}".strip()
                with suppress(Exception):
                    (worker or self)._record(
                        application_id,
                        EventType.HANDED_TO_HUMAN,
                        {
                            "state": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                            # What the page had already shown, so a person can tell
                            # "confirmed but not recorded" from "never opened".
                            "outcome": result.outcome if result else None,
                            "source": result.source if result else None,
                            "url": result.url if result else None,
                        },
                    )
            finally:
                with suppress(Exception):
                    close()

        self.handoffs.start(
            application_id,
            url=url,
            timeout_seconds=settings.handoff_timeout_seconds,
            target=target,
        )
        return ExecutionResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            outcome="HANDED_TO_HUMAN",
            adapter=adapter_kind,
            url=url,
            detail=(
                "a challenge was detected. The pre-filled form is open in a browser window for "
                "you to solve and submit; then report the result on the Applications page"
            ),
            stops=[{"kind": str(SafetyStopKind.CAPTCHA), "message": "handed to a person"}],
        )

    def _worker(self) -> tuple[ExecutionService, Any]:
        """A service with its own engine context, for use on a handoff thread."""

        if self.ctx_factory is None:
            return self, lambda: None
        ctx = self.ctx_factory()
        worker = ExecutionService(
            ctx,
            registry=self.registry,
            approval=ApprovalService(ctx),
            handoff=self.handoff,
            handoffs=self.handoffs,
        )
        return worker, ctx.close

    def _event_sink(self, application_id: str, record: HandoffRecord | None = None) -> Any:
        """Callback the handoff uses to log each step as an HANDED_TO_HUMAN event."""

        def sink(state: str, payload: dict[str, Any]) -> None:
            if record is not None:
                if state == "opened":
                    record.state = STATE_OPEN
                elif state in {"browser_closed", "no_valid_answer"}:
                    record.state = STATE_NEEDS_REPORT
                elif state == "fill_failed":
                    record.detail = f"form fill failed: {payload.get('error')}"
            # Logging must never close the person's browser.
            with suppress(Exception):
                self._record(
                    application_id, EventType.HANDED_TO_HUMAN, {"state": state, **payload}
                )

        return sink

    def _handoff_error(
        self, application_id: str, opportunity_id: str, adapter_kind: str | None, exc: Exception
    ) -> ExecutionResult:
        message = f"the headed handoff failed: {type(exc).__name__}: {exc}"
        self._mark_failed(application_id, message)
        return ExecutionResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            outcome="FAILED",
            adapter=adapter_kind,
            detail=message,
        )

    def _finish_handoff(
        self,
        application_id: str,
        opportunity_id: str,
        adapter_kind: str | None,
        result: HandoffResult,
        extra: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Record how a handoff ended.

        submitted -> APPLIED. abandoned or timeout -> BLOCKED (retryable, never
        WITHDRAWN). failed -> FAILED. closed -> stays APPLYING and waits for a report.
        Every move goes through the transition graph and appends an event.
        """

        outcome = result.outcome
        payload = {
            "outcome": outcome,
            "source": result.source,
            "url": result.url,
            "confirmation_text": result.confirmation_text,
            "opened_headed": result.opened_headed,
            "error": result.error,
            "adapter": adapter_kind,
            **(extra or {}),
        }
        if outcome == OUTCOME_CLOSED:
            self._record(
                application_id,
                EventType.HANDED_TO_HUMAN,
                {"state": "needs_report", **payload},
            )
            return ExecutionResult(
                application_id=application_id,
                opportunity_id=opportunity_id,
                outcome="HANDED_TO_HUMAN",
                adapter=adapter_kind,
                url=result.url,
                detail=(
                    "the browser closed before an outcome was reported; the application stays "
                    "APPLYING until you report whether it was submitted"
                ),
            )
        if outcome not in HANDOFF_OUTCOMES and outcome != OUTCOME_TIMEOUT:
            outcome = "failed"

        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            if application.status != ApplicationStatus.APPLYING.value:
                return self._outcome_after_move(
                    repos, application, opportunity_id, adapter_kind, outcome, result, payload
                )
            events = repos.events.for_entity(EntityType.APPLICATION, application_id)
            was_unconfirmed = attempt_has_unconfirmed(events)
            application.attempts = (application.attempts or 0) + 1
            result_label = outcome.upper()
            if outcome == "submitted":
                application.submission_time = datetime.now(UTC)
                application.submission_method = str(
                    SubmissionMethod.PLAYWRIGHT_ADAPTER
                    if was_unconfirmed
                    else SubmissionMethod.HEADED_HUMAN
                )
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
                self._walk(repos, application_id, ApplicationStatus.APPLIED)
                _advance_opportunity(repos, opportunity_id, JobStatus.APPLIED)
                event = EventType.APPLICATION_SUBMITTED
            elif outcome == "failed" and not was_unconfirmed:
                application.last_error = result.error or "the headed attempt failed"
                repos.session.flush()
                self._walk(repos, application_id, ApplicationStatus.FAILED)
                _advance_opportunity(repos, opportunity_id, JobStatus.FAILED)
                event = EventType.APPLICATION_FAILED
            else:
                # abandoned, timed out, or a person reporting that an unconfirmed click
                # did not go through: retryable, so BLOCKED with the reason.
                if outcome == "abandoned":
                    reason, event = "HANDOFF_ABANDONED", EventType.APPLICATION_ABANDONED
                    message = result.error or "the person left the form without submitting"
                elif outcome == OUTCOME_TIMEOUT:
                    reason, event = "HANDOFF_TIMEOUT", EventType.APPLICATION_BLOCKED
                    message = result.error or "the handoff timed out with no reported outcome"
                    result_label = "BLOCKED"
                else:
                    reason, event = "HANDOFF_NOT_SUBMITTED", EventType.APPLICATION_BLOCKED
                    message = result.error or "the person reported the attempt did not go through"
                    result_label = "BLOCKED"
                application.blocked_reason = reason
                application.last_error = message[:2000]
                repos.session.flush()
                self._walk(repos, application_id, ApplicationStatus.BLOCKED)
                payload["blocked_reason"] = reason
            repos.events.append(EntityType.APPLICATION, application_id, event, payload)
        return ExecutionResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            outcome=result_label,
            adapter=adapter_kind,
            url=result.url,
            detail=result.error or result.source,
        )

    @staticmethod
    def _walk(repos: Any, application_id: str, target: ApplicationStatus) -> None:
        application = repos.applications.get(application_id)
        for step in application_path(ApplicationStatus(application.status), target):
            repos.applications.set_status(application_id, step)

    def _outcome_after_move(
        self,
        repos: Any,
        application: Any,
        opportunity_id: str,
        adapter_kind: str | None,
        outcome: str,
        result: HandoffResult,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """A handoff ended after the application had already left APPLYING.

        Nothing is silently dropped. A confirmed submission is written to the ledger as
        a late confirmation (and, from BLOCKED or FAILED, the row is walked to APPLIED
        through the graph, since the employer's page is the evidence). Any other outcome
        is only noted: the row already says something else, and that stands.
        """

        status = application.status
        if outcome == "submitted":
            self._late_confirmation(
                repos,
                application,
                opportunity_id,
                {**payload, "late_confirmation": True, "conflicting_status": status},
                method=SubmissionMethod.HEADED_HUMAN,
                confirmation={
                    "source": result.source,
                    "url": result.url,
                    "confirmation_detected": result.source == "confirmation_page",
                },
            )
        else:
            repos.events.append(
                EntityType.APPLICATION,
                application.id,
                EventType.HANDED_TO_HUMAN,
                {"state": "result_ignored", "conflicting_status": status, **payload},
            )
        return ExecutionResult(
            application_id=application.id,
            opportunity_id=opportunity_id,
            outcome="CONFLICT",
            adapter=adapter_kind,
            url=result.url,
            detail=(
                f"the application was already {status} when the handoff ended ({outcome})"
            ),
        )

    def _late_confirmation(
        self,
        repos: Any,
        application: Any,
        opportunity_id: str,
        payload: dict[str, Any],
        *,
        method: SubmissionMethod,
        confirmation: dict[str, Any],
    ) -> None:
        repos.events.append(
            EntityType.APPLICATION, application.id, EventType.APPLICATION_SUBMITTED, payload
        )
        if application.status not in {
            ApplicationStatus.BLOCKED.value,
            ApplicationStatus.FAILED.value,
        }:
            return
        application.submission_time = datetime.now(UTC)
        application.submission_method = str(method)
        merged = dict(application.external_confirmation or {})
        merged.update(confirmation)
        application.external_confirmation = merged
        repos.session.flush()
        self._walk(repos, application.id, ApplicationStatus.APPLIED)
        _advance_opportunity(repos, opportunity_id, JobStatus.APPLIED)

    def report_handoff_outcome(self, application_id: str, outcome: str) -> dict[str, Any]:
        """A person reports how a handoff, or an unconfirmed click, ended.

        This is how an application leaves APPLYING without an automatic confirmation.
        It is refused while a run is still in flight, and accepted only when the
        application is actually waiting for a person. A running handoff thread is woken
        and records the result itself; otherwise the report is recorded directly.
        """

        if outcome not in HANDOFF_OUTCOMES:
            raise ValidationFailure(
                f"outcome must be one of {', '.join(sorted(HANDOFF_OUTCOMES))}; got {outcome!r}"
            )
        with self.ctx.read_repos() as repos:
            application = repos.applications.get(application_id)
            status = application.status
            opportunity_id = application.opportunity_id
        if status != ApplicationStatus.APPLYING.value:
            raise ValidationFailure(
                f"application {application_id} is {status}; there is no submission waiting "
                "for an outcome"
            )
        if self.handoffs.run_in_progress(application_id):
            raise ValidationFailure(
                f"a submission is already in progress for application {application_id}: the "
                "run is still going, so no outcome can be reported yet"
            )
        if self.handoffs.report(application_id, outcome):
            status = self._status_of(application_id)
        # A running handoff records its own result. Record directly only when no thread
        # is left to do it (the browser was closed, or the server restarted), and only
        # if the attempt really stopped waiting for a person.
        if status == ApplicationStatus.APPLYING.value and not self.handoffs.active(
            application_id
        ):
            with self.ctx.read_repos() as repos:
                events = list(repos.events.for_entity(EntityType.APPLICATION, application_id))
            if not waiting_for_person(events):
                raise ValidationFailure(
                    f"there is no submission waiting for your report on application "
                    f"{application_id}: its browser window may still be open (in this or "
                    "another process). Finish or close the window first"
                )
            self._finish_handoff(
                application_id,
                opportunity_id,
                None,
                HandoffResult(
                    outcome=outcome,
                    source="user_report",
                    error=None if outcome == "submitted" else f"person reported: {outcome}",
                ),
            )
        return self.handoff_status(application_id)

    def force_report_handoff_outcome(
        self,
        application_id: str,
        outcome: str,
        *,
        confirm_id: str,
        require_stale: bool = False,
    ) -> dict[str, Any]:
        """Record a person's report for an attempt that is stuck APPLYING.

        For the hard-kill case: the process that held the window or the run is gone, so
        nothing will ever write the event that makes a normal report acceptable. This
        fails closed. It needs the application id typed back, it is refused while this
        process still has a run or window registered, and the report is recorded with
        `forced: true` and the event that was last in the log. `require_stale` (the
        dashboard) additionally demands that the attempt has been silent for longer than
        the handoff timeout.
        """

        if outcome not in HANDOFF_OUTCOMES:
            raise ValidationFailure(
                f"outcome must be one of {', '.join(sorted(HANDOFF_OUTCOMES))}; got {outcome!r}"
            )
        if confirm_id != application_id:
            raise ValidationFailure(
                "a forced report must be confirmed by typing the application id exactly "
                f"({application_id}); nothing was recorded"
            )
        with self.ctx.read_repos() as repos:
            application = repos.applications.get(application_id)
            status = application.status
            opportunity_id = application.opportunity_id
            events = list(repos.events.for_entity(EntityType.APPLICATION, application_id))
        if status != ApplicationStatus.APPLYING.value:
            raise ValidationFailure(
                f"application {application_id} is {status}; there is no submission waiting "
                "for an outcome"
            )
        if self.handoffs.run_in_progress(application_id) or self.handoffs.active(application_id):
            raise ValidationFailure(
                f"a submission is already in progress for application {application_id} in "
                "this process; it cannot be forced"
            )
        last = events[-1] if events else None
        if require_stale and not self._is_stale(last):
            raise ValidationFailure(
                f"application {application_id} has not been silent for longer than "
                f"{self.ctx.settings.handoff_timeout_seconds} seconds, so a forced report is "
                "not offered yet"
            )
        previous = (
            {
                "event_type": last.event_type,
                "state": (last.payload or {}).get("state"),
                "seq": last.seq,
                "occurred_at": last.occurred_at.isoformat() if last.occurred_at else None,
            }
            if last is not None
            else None
        )
        self._finish_handoff(
            application_id,
            opportunity_id,
            None,
            HandoffResult(
                outcome=outcome,
                source="user_report",
                error=None if outcome == "submitted" else f"person reported (forced): {outcome}",
            ),
            extra={"forced": True, "previous_event": previous},
        )
        return self.handoff_status(application_id)

    def _is_stale(self, last_event: Any) -> bool:
        if last_event is None or last_event.occurred_at is None:
            return False
        moment = last_event.occurred_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - moment).total_seconds()
        return age > float(self.ctx.settings.handoff_timeout_seconds)

    def handoff_status(self, application_id: str) -> dict[str, Any]:
        """Live status of the human handoff, for the dashboard."""

        with self.ctx.read_repos() as repos:
            application = repos.applications.get(application_id)
            status = application.status
            summary = {
                "application_id": application_id,
                "application_status": status,
                "blocked_reason": application.blocked_reason,
                "last_error": application.last_error,
                "submitted": application.submission_time is not None,
            }
            events = list(repos.events.for_entity(EntityType.APPLICATION, application_id))
        record = self.handoffs.get(application_id)
        info: dict[str, Any] = (
            record.as_dict()
            if record is not None
            else {"active": False, "state": None, "url": None, "outcome": None}
        )
        applying = status == ApplicationStatus.APPLYING.value
        running = applying and not info["active"] and self.handoffs.run_in_progress(application_id)
        if running:
            info["state"] = "running"
        elif applying and not info["active"] and waiting_for_person(events):
            info["state"] = STATE_NEEDS_REPORT
        awaiting = applying and not running and (info["active"] or waiting_for_person(events))
        # Offered only for a row that is APPLYING, has nothing registered in this process,
        # and has been silent for longer than the handoff timeout.
        can_force = (
            applying
            and not info["active"]
            and not self.handoffs.run_in_progress(application_id)
            and self._is_stale(events[-1] if events else None)
        )
        return {
            **info,
            **summary,
            "awaiting_person": awaiting,
            "running": running,
            "can_force": can_force,
        }

    # -- state recording -------------------------------------------------

    def _record_receipt(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        receipt: SubmissionReceipt,
    ) -> ExecutionResult:
        if not receipt.submitted and receipt.clicked:
            return self._mark_unconfirmed(
                application_id,
                opportunity_id,
                adapter,
                message=receipt.error or "the submit was clicked but nothing confirms it",
                receipt=receipt,
            )
        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            if application.status != ApplicationStatus.APPLYING.value:
                # The row moved while this run was clicking. Never overwrite it, and never
                # lose a confirmation either.
                status = application.status
                if receipt.submitted:
                    self._late_confirmation(
                        repos,
                        application,
                        opportunity_id,
                        {
                            **receipt.as_dict(),
                            "late_confirmation": True,
                            "conflicting_status": status,
                        },
                        method=receipt.method,
                        confirmation=dict(receipt.external_confirmation or {}),
                    )
                else:
                    repos.events.append(
                        EntityType.APPLICATION,
                        application_id,
                        EventType.APPLICATION_BLOCKED,
                        {"stage": "conflict", "conflicting_status": status, **receipt.as_dict()},
                    )
                return ExecutionResult(
                    application_id=application_id,
                    opportunity_id=opportunity_id,
                    outcome="CONFLICT",
                    adapter=adapter.kind,
                    receipt=receipt,
                    detail=f"the application was already {status} when this run finished",
                )
            application.attempts = (application.attempts or 0) + 1

            if receipt.submitted:
                application.submission_time = receipt.submitted_at or datetime.now(UTC)
                application.submission_method = str(receipt.method)
                if receipt.external_confirmation:
                    merged = dict(application.external_confirmation or {})
                    merged.update(receipt.external_confirmation)
                    application.external_confirmation = merged
                repos.session.flush()
                # Walk the graph rather than assuming the next hop.
                self._walk(repos, application_id, ApplicationStatus.APPLIED)
                _advance_opportunity(repos, opportunity_id, JobStatus.APPLIED)
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
                self._walk(repos, application_id, ApplicationStatus.FAILED)
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

    def _mark_unconfirmed(
        self,
        application_id: str,
        opportunity_id: str,
        adapter: ApplicationAdapter,
        *,
        message: str,
        receipt: SubmissionReceipt | None = None,
    ) -> ExecutionResult:
        """The click happened and nothing confirms it. The application stays APPLYING.

        Not FAILED (the employer may have it) and not SUBMITTED (nothing proves it).
        It is resolved only by a reported outcome. If the row is no longer APPLYING the
        row is left alone and the unconfirmed click is recorded as a conflict.
        """

        payload = {
            "message": message,
            "adapter": adapter.kind,
            "receipt": receipt.as_dict() if receipt else None,
        }
        with self.ctx.repos() as repos:
            application = repos.applications.get(application_id)
            if application.status != ApplicationStatus.APPLYING.value:
                repos.events.append(
                    EntityType.APPLICATION,
                    application_id,
                    EventType.APPLICATION_UNCONFIRMED,
                    {**payload, "conflict": True, "conflicting_status": application.status},
                )
                return ExecutionResult(
                    application_id=application_id,
                    opportunity_id=opportunity_id,
                    outcome="CONFLICT",
                    adapter=adapter.kind,
                    receipt=receipt,
                    detail=(
                        f"the submit was clicked but the application was already "
                        f"{application.status}; a person must check whether it went through"
                    ),
                )
            application.attempts = (application.attempts or 0) + 1
            application.last_error = message[:2000]
            repos.session.flush()
            repos.events.append(
                EntityType.APPLICATION,
                application_id,
                EventType.APPLICATION_UNCONFIRMED,
                payload,
            )
        return ExecutionResult(
            application_id=application_id,
            opportunity_id=opportunity_id,
            outcome="UNCONFIRMED",
            adapter=adapter.kind,
            receipt=receipt,
            detail=(
                f"{message}. Check whether the employer received it, then report the outcome "
                "(Applications page, or `sabra handoff-outcome`)"
            ),
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
            if application.status != ApplicationStatus.APPLYING.value:
                # The row moved under this run; leave it, and say so.
                repos.events.append(
                    EntityType.APPLICATION,
                    application_id,
                    EventType.APPLICATION_BLOCKED,
                    {
                        "stage": "conflict",
                        "conflicting_status": application.status,
                        "message": message,
                    },
                )
                return
            application.last_error = message[:2000]
            application.attempts = (application.attempts or 0) + 1
            repos.session.flush()
            self._walk(repos, application_id, ApplicationStatus.FAILED)
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
            self._walk(repos, application_id, ApplicationStatus.BLOCKED)

    def _record(self, application_id: str, event_type: EventType, payload: dict[str, Any]) -> None:
        with self.ctx.repos() as repos:
            repos.events.append(EntityType.APPLICATION, application_id, event_type, payload)

    def _job_for(self, application: Any) -> Any:
        with self.ctx.read_repos() as repos:
            try:
                return repos.jobs.get(application.job_id)
            except NotFound as exc:
                raise NotFound("job", application.job_id) from exc
