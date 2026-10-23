"""Automation adapter contract (requirement 36).

`supports / inspect / prepare / validate / submit`, with the browser isolated behind
this interface.

Properties enforced here rather than in the adapter implementations, so no individual
adapter can weaken them:

* The adapter owns exactly one live browser page per run (`session`). Inspect, prepare,
  validate and submit all act on that same page, and `submit` refuses unless that page
  is the one `prepare` filled. A click can never land on a fresh, empty form.
* A dry run is the default. `submit` refuses to act unless the caller has explicitly
  opted in, so the safe behaviour is the one you get by not asking.

Approval is not checked here: an adapter never sees an approval. The execution service
re-verifies the hash-bound approval and claims the application in one transaction
before any adapter is touched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sabra_domain.enums import SafetyStopKind, SubmissionMethod
from sabra_domain.errors import ApprovalRequired, SafetyStop
from sabra_domain.schemas import ApplicationPackageSchema, CanonicalJob

from .confirmation import normalize_url


@dataclass(slots=True)
class FormField:
    """One field on an application form, as found on the page."""

    name: str
    label: str | None = None
    kind: str = "text"  # text | email | tel | textarea | select | checkbox | radio | file
    required: bool = False
    options: list[str] = field(default_factory=list)
    selector: str | None = None
    question_key: str | None = None
    value_source: str | None = None
    # Text near an unlabelled control. Shown to a human by `describe_field`; never used
    # to classify the field, because container text is not the field's own question.
    context: str | None = None
    # The control's `autocomplete` attribute (e.g. "address-level2"), when it has one.
    autocomplete: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "options": self.options[:20],
            "selector": self.selector,
            "question_key": self.question_key,
            "value_source": self.value_source,
            "context": self.context,
            "autocomplete": self.autocomplete,
        }


@dataclass(slots=True)
class ApplicationForm:
    """What an adapter found on the employer's application page."""

    url: str
    fields: list[FormField] = field(default_factory=list)
    has_captcha: bool = False
    # True when the challenge probe itself failed. Callers must treat that as a
    # challenge: an unknown page is not safe to submit.
    challenge_detection_failed: bool = False
    requires_login: bool = False
    has_file_upload: bool = False
    attestations: list[str] = field(default_factory=list)
    submit_selector: str | None = None
    page_hash: str | None = None
    title: str | None = None
    inspected_at: datetime | None = None

    def field(self, name: str) -> FormField | None:
        for entry in self.fields:
            if entry.name == name:
                return entry
        return None

    @property
    def required_fields(self) -> list[FormField]:
        return [f for f in self.fields if f.required]

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "field_count": len(self.fields),
            "required_count": len(self.required_fields),
            "has_captcha": self.has_captcha,
            "challenge_detection_failed": self.challenge_detection_failed,
            "requires_login": self.requires_login,
            "has_file_upload": self.has_file_upload,
            "attestations": self.attestations,
            "submit_selector": self.submit_selector,
            "page_hash": self.page_hash,
            "fields": [f.as_dict() for f in self.fields],
        }


@dataclass(slots=True)
class ValidationReport:
    """Pre-submission check. `ok` means every requirement is satisfied and known."""

    ok: bool
    stops: list[SafetyStop] = field(default_factory=list)
    unknown_required_fields: list[str] = field(default_factory=list)
    unanswered_questions: list[str] = field(default_factory=list)
    mapped_fields: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stops": [
                {"kind": str(s.kind), "message": s.message, "details": s.details}
                for s in self.stops
            ],
            "unknown_required_fields": self.unknown_required_fields,
            "unanswered_questions": self.unanswered_questions,
            "mapped_fields": self.mapped_fields,
            "notes": self.notes,
        }


@dataclass(slots=True)
class PreparedSubmission:
    """A filled form that has not been submitted."""

    url: str
    filled: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    prepared_at: datetime | None = None
    dry_run: bool = True
    # The control `inspect` found; the only thing `submit` will click.
    submit_selector: str | None = None
    # Required fields or proposed optional answers prepare could not fill safely.
    # Any entry blocks the click; the legacy field name is retained for compatibility.
    unfilled_required: list[str] = field(default_factory=list)
    # What was rendered for upload: per document the file name, sha256 and byte count,
    # the number of characters with no safe mapping, and the renderer version.
    documents: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "filled": self.filled,
            "files": self.files,
            "skipped": self.skipped,
            "prepared_at": self.prepared_at.isoformat() if self.prepared_at else None,
            "dry_run": self.dry_run,
            "submit_selector": self.submit_selector,
            "unfilled_required": self.unfilled_required,
            "documents": self.documents,
        }


@dataclass(slots=True)
class SubmissionReceipt:
    """Evidence that a submission happened, or that it did not."""

    submitted: bool
    method: SubmissionMethod
    # True once the submit control was clicked. A receipt that clicked but is not
    # `submitted` is UNCONFIRMED: the employer may have received it.
    clicked: bool = False
    external_confirmation: dict[str, Any] = field(default_factory=dict)
    confirmation_text: str | None = None
    evidence_paths: list[str] = field(default_factory=list)
    submitted_at: datetime | None = None
    error: str | None = None
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "submitted": self.submitted,
            "clicked": self.clicked,
            "method": str(self.method),
            "external_confirmation": self.external_confirmation,
            "confirmation_text": self.confirmation_text,
            "evidence_paths": self.evidence_paths,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "error": self.error,
            "dry_run": self.dry_run,
        }


class ClaimLost(SafetyStop):
    """The application stopped being APPLYING before the click; nothing was clicked."""

    def __init__(self, message: str) -> None:
        super().__init__(SafetyStopKind.MATERIAL_AMBIGUITY, message)


class ApplicationAdapter(ABC):
    """One supported application workflow (requirement 36)."""

    kind: str = "base"

    def __init__(
        self, *, dry_run: bool = True, settings: Any = None, page_factory: Any = None
    ) -> None:
        self.dry_run = dry_run
        self.settings = settings
        # Test seam: `page_factory(url)` returns a ready `FormPage`. It is called once
        # per `session`, never once per operation.
        self._page_factory = page_factory
        self._page: Any = None
        self._page_closer: Callable[[], None] | None = None
        self._filled_page: Any = None
        self.click_attempted = False
        # Set by the execution service for a run: called immediately before the click,
        # returns False if the application is no longer the caller's to submit.
        self.pre_click_check: Callable[[], bool] | None = None

    def prepare_documents(
        self, package: ApplicationPackageSchema, *, job: Any = None, dated: str | None = None
    ) -> Any:
        """Produce the files this adapter will upload, before any browser opens.

        `job` and `dated` (the package's prepared date, formatted) feed the cover letter's
        recipient block and date line.

        The default has nothing to produce. An adapter that renders documents overrides
        this and raises `SafetyStop` if one cannot be made, so the failure is found
        before the employer's page is touched.
        """

        return None

    # -- browser session ------------------------------------------------

    @contextmanager
    def session(self, url: str) -> Iterator[Any]:
        """Open the one live page for this run and close it on exit.

        Inside the block, `inspect`, `prepare` and `submit` reuse this page. Nothing
        else opens a page for a submission, which is what makes "the form that was
        filled is the form that is submitted" a property of the adapter and not of
        its callers.
        """

        if self._page is not None:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "a browser session is already open for this adapter",
                {"url": url},
            )
        # Reset first: a failed open must not inherit the last run's flag.
        self.click_attempted = False
        page, closer = self._open_page(url)
        self._page = page
        self._page_closer = closer
        self._filled_page = None
        try:
            yield page
        finally:
            self._release_page()

    def _release_page(self) -> None:
        closer = self._page_closer
        self._page = None
        self._page_closer = None
        self._filled_page = None
        self.pre_click_check = None
        if closer is not None:
            # Teardown must not mask the error that ended the run.
            with suppress(Exception):
                closer()

    @contextmanager
    def _page_for(self, url: str) -> Iterator[Any]:
        """The live session page if there is one, else a one-shot session for `url`."""

        if self._page is not None:
            yield self._page
            return
        with self.session(url) as page:
            yield page

    def _open_page(self, url: str) -> tuple[Any, Callable[[], None] | None]:
        """Return `(page, closer)`. A failed open still releases the browser."""

        if self._page_factory is not None:
            page = self._page_factory(url)
            return page, getattr(page, "close", None)
        if self.dry_run:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "dry run will not open an employer form or type into it",
                {"url": url},
            )

        from .playwright_driver import BrowserSession

        headless = True
        timeout_ms = 30_000
        user_agent = None
        if self.settings is not None:
            headless = bool(getattr(self.settings, "playwright_headless", True))
            timeout_ms = int(getattr(self.settings, "playwright_timeout_ms", 30_000))
            user_agent = getattr(self.settings, "discovery_user_agent", None)
        browser = BrowserSession(headless=headless, timeout_ms=timeout_ms, user_agent=user_agent)
        browser.__enter__()
        try:
            page = browser.new_page()
            page.open(url)
        except BaseException:
            browser.close()
            raise

        def close_all() -> None:
            with suppress(Exception):
                page.close()
            browser.close()

        return page, close_all

    def application_url(self, job: CanonicalJob | Any) -> str:
        """The URL of the application form for a posting."""

        return str(getattr(job, "source_url", "") or "")

    def _require_live_page(self, url: str) -> Any:
        """The session page, refusing if it has moved off the form `url` names."""

        page = self._page
        if page is None:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "no live browser session is open; use `with adapter.session(url):`",
                {"url": url},
            )
        current = page.current_url()
        if current and normalize_url(current) != normalize_url(url):
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "the live page is no longer the form that was inspected "
                f"({current!r} is not {url!r})",
                {"url": url, "current_url": current},
            )
        return page

    # -- contract -------------------------------------------------------

    @abstractmethod
    def supports(self, job: CanonicalJob | Any) -> bool:
        """Whether this adapter understands the posting's application flow."""

    @abstractmethod
    def inspect(self, job: CanonicalJob | Any) -> ApplicationForm:
        """Read the application form without filling anything."""

    @abstractmethod
    def prepare(
        self, package: ApplicationPackageSchema, form: ApplicationForm
    ) -> PreparedSubmission:
        """Fill the form. Must not submit."""

    @abstractmethod
    def submit(self, prepared: PreparedSubmission) -> SubmissionReceipt:
        """Submit. Only ever called with a validated, approved package, on the page
        `prepare` filled in this session."""

    # -- shared safety --------------------------------------------------

    def detect_stops(
        self, form: ApplicationForm, package: ApplicationPackageSchema
    ) -> list[SafetyStop]:
        """Conditions that require a human (requirement 21).

        The default set is conservative and adapter-agnostic; adapters add their own.
        """

        stops: list[SafetyStop] = []
        if form.has_captcha or form.challenge_detection_failed:
            detail = (
                "challenge detection failed, so the form is treated as challenged"
                if form.challenge_detection_failed and not form.has_captcha
                else "the application form presents a CAPTCHA or bot challenge, which "
                "automation must not attempt to solve"
            )
            stops.append(
                SafetyStop(
                    SafetyStopKind.CAPTCHA,
                    detail,
                    {
                        "url": form.url,
                        "detection_failed": form.challenge_detection_failed,
                    },
                )
            )
        if form.requires_login:
            stops.append(
                SafetyStop(
                    SafetyStopKind.IDENTITY_VERIFICATION,
                    "the application requires signing in or identity verification",
                    {"url": form.url},
                )
            )
        for attestation in form.attestations:
            stops.append(
                SafetyStop(
                    SafetyStopKind.LEGAL_ATTESTATION,
                    f"the form contains a legal attestation requiring the candidate's own statement: "
                    f"{attestation[:200]}",
                    {"attestation": attestation},
                )
            )
        if form.has_file_upload:
            # Not a stop by itself — V1 supports résumé and cover-letter uploads — but
            # recorded so the caller can see it was considered.
            pass
        return stops

    def require_opt_in(self) -> None:
        """A dry run needs no opt-in; a real submission does, explicitly."""

        if self.dry_run:
            raise ApprovalRequired(
                "adapter is in dry-run mode; a real submission requires dry_run=False "
                "and an approved package"
            )


class SourceNotSupported(Exception):
    """Raised by a registry when no adapter supports a posting."""
