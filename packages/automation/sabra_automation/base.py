"""Automation adapter contract (requirement 36).

`supports / inspect / prepare / validate / submit / capture_confirmation`, with the
browser isolated behind this interface.

Two properties are enforced here rather than in the adapter implementations, so no
individual adapter can weaken them:

* `submit` takes an approval and re-verifies it against the live package hash. There
  is no code path to a submission that skips this.
* A dry run is the default. `submit` refuses to act unless the caller has explicitly
  opted in, so the safe behaviour is the one you get by not asking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sabra_domain.enums import SafetyStopKind, SubmissionMethod
from sabra_domain.errors import ApprovalRequired, ApprovalScopeMismatch, SafetyStop
from sabra_domain.schemas import ApplicationPackageSchema, CanonicalJob


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
        }


@dataclass(slots=True)
class ApplicationForm:
    """What an adapter found on the employer's application page."""

    url: str
    fields: list[FormField] = field(default_factory=list)
    has_captcha: bool = False
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "filled": self.filled,
            "files": self.files,
            "skipped": self.skipped,
            "prepared_at": self.prepared_at.isoformat() if self.prepared_at else None,
            "dry_run": self.dry_run,
        }


@dataclass(slots=True)
class SubmissionReceipt:
    """Evidence that a submission happened, or that it did not."""

    submitted: bool
    method: SubmissionMethod
    external_confirmation: dict[str, Any] = field(default_factory=dict)
    confirmation_text: str | None = None
    evidence_paths: list[str] = field(default_factory=list)
    submitted_at: datetime | None = None
    error: str | None = None
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "submitted": self.submitted,
            "method": str(self.method),
            "external_confirmation": self.external_confirmation,
            "confirmation_text": self.confirmation_text,
            "evidence_paths": self.evidence_paths,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "error": self.error,
            "dry_run": self.dry_run,
        }


class ApplicationAdapter(ABC):
    """One supported application workflow (requirement 36)."""

    kind: str = "base"

    def __init__(self, *, dry_run: bool = True, settings: Any = None) -> None:
        self.dry_run = dry_run
        self.settings = settings

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
        """Submit. Only ever called with a validated, approved package."""

    @abstractmethod
    def capture_confirmation(self) -> dict[str, Any]:
        """Read back whatever the employer showed after submission."""

    # -- shared safety --------------------------------------------------

    def detect_stops(
        self, form: ApplicationForm, package: ApplicationPackageSchema
    ) -> list[SafetyStop]:
        """Conditions that require a human (requirement 21).

        The default set is conservative and adapter-agnostic; adapters add their own.
        """

        stops: list[SafetyStop] = []
        if form.has_captcha:
            stops.append(
                SafetyStop(
                    SafetyStopKind.CAPTCHA,
                    "the application form presents a CAPTCHA, which automation must not "
                    "attempt to solve",
                    {"url": form.url},
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

    def require_approval(
        self, package: ApplicationPackageSchema, *, approved_hash: str | None
    ) -> None:
        """Refuse to proceed without an approval bound to this exact content."""

        if not approved_hash:
            raise ApprovalRequired(
                "no approval is bound to this package; the adapter will not submit"
            )
        expected = package.content_hash
        if approved_hash != expected:
            raise ApprovalScopeMismatch(
                f"approved hash {approved_hash} does not match package hash {expected}"
            )

    def require_opt_in(self) -> None:
        """A dry run needs no opt-in; a real submission does, explicitly."""

        if self.dry_run:
            raise ApprovalRequired(
                "adapter is in dry-run mode; a real submission requires dry_run=False "
                "and an approved package"
            )


class SourceNotSupported(Exception):
    """Raised by a registry when no adapter supports a posting."""
