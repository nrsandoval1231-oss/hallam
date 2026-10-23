"""Manual handoff: the "adapter" for a posting no application adapter understands.

Ashby and company career pages have no adapter in this product. Rather than leave an
approved application BLOCKED, the execution service hands the posting to the person: the
apply page opens in a visible browser, the approved package's résumé and cover letter are
rendered to PDF files whose paths are shown, and the person fills the form, attaches the
files and submits it themselves. Nothing is typed, uploaded or clicked here.

This class exists so that path reuses the claim, handoff, outcome and blocked-on-abandon
machinery unchanged. It is never selected by `AdapterRegistry.for_job`: `supports` is
always False, and `inspect`, `prepare` and `submit` refuse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sabra_domain.enums import SafetyStopKind
from sabra_domain.errors import SafetyStop
from sabra_domain.schemas import ApplicationPackageSchema, CanonicalJob

from .base import ApplicationAdapter, ApplicationForm, PreparedSubmission, SubmissionReceipt

MANUAL_KIND = "manual"


def manual_apply_url(job: CanonicalJob | Any) -> str:
    """The page the person applies on: the source's own `applyUrl`, else the posting URL."""

    payload = getattr(job, "raw_payload", None)
    if isinstance(payload, dict):
        for key in ("applyUrl", "apply_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                return value
    fallback = str(getattr(job, "source_url", "") or "")
    return fallback if fallback.startswith(("https://", "http://")) else ""


def manual_plan(package: Any, job: Any) -> dict[str, Any]:
    """What a real run would do for this posting, for the dry-run result. Opens nothing."""

    return {
        "manual_handoff": True,
        "navigated": False,
        "typed": False,
        "uploaded": False,
        "employer_url": manual_apply_url(job),
        "would_open": manual_apply_url(job),
        "would_save": {
            "resume": bool((getattr(package, "tailored_resume_markdown", None) or "").strip()),
            "cover_letter": bool((getattr(package, "cover_letter", None) or "").strip()),
        },
        "note": (
            "manual handoff; no adapter: a real run opens this posting in a browser for you "
            "to fill and submit. Nothing is typed or uploaded"
        ),
    }


class ManualHandoffAdapter(ApplicationAdapter):
    kind = MANUAL_KIND

    def __init__(self, *, settings: Any = None, evidence_dir: Any = None) -> None:
        super().__init__(dry_run=False, settings=settings)
        from .greenhouse_adapter import GreenhouseApplicationAdapter

        # Only the document rendering is borrowed: the same PDFs, file names and folder
        # (`<artifacts>/applications/<opportunity id>/`) the automated adapters upload.
        self._documents = GreenhouseApplicationAdapter(
            dry_run=True, settings=settings, evidence_dir=evidence_dir
        )

    def supports(self, job: CanonicalJob | Any) -> bool:
        return False

    def application_url(self, job: CanonicalJob | Any) -> str:
        return manual_apply_url(job)

    def prepare_documents(
        self,
        package: ApplicationPackageSchema,
        *,
        job: Any = None,
        dated: str | None = None,
    ) -> dict[str, Path]:
        """Render the PDFs the person will attach. Raises `SafetyStop` if one cannot be made."""

        return self._documents.prepare_documents(package, job=job, dated=dated)

    def _refuse(self) -> SafetyStop:
        return SafetyStop(
            SafetyStopKind.ADAPTER_UNSUPPORTED,
            "the manual handoff never fills or submits a form; a person does",
        )

    def inspect(self, job: CanonicalJob | Any) -> ApplicationForm:
        raise self._refuse()

    def prepare(
        self, package: ApplicationPackageSchema, form: ApplicationForm
    ) -> PreparedSubmission:
        raise self._refuse()

    def submit(self, prepared: PreparedSubmission) -> SubmissionReceipt:
        raise self._refuse()
