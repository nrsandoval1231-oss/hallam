"""Lever application adapter.

Lever's public apply form is a single page: name, email, phone, current company,
résumé, and labelled custom questions. This adapter fills those from the approved
package and blocks on any required field it cannot answer from verified data.
It never clicks submit while a challenge is present; that decision lives in the
execution service.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sabra_domain.enums import SafetyStopKind, SourceKind
from sabra_domain.errors import SafetyStop
from sabra_domain.identity import sha256_text
from sabra_domain.schemas import ApplicationPackageSchema, CanonicalJob

from .answer_match import is_typable, option_problem, write_value
from .base import (
    ApplicationForm,
    FormField,
    PreparedSubmission,
    SubmissionReceipt,
    ValidationReport,
)
from .greenhouse_adapter import (
    ENUMERATE_FIELDS_JS,
    GreenhouseApplicationAdapter,
    describe_field,
)
from .playwright_driver import (
    CAPTCHA_SELECTORS,
    FormPage,
    looks_like_bot_wall,
    looks_like_login_wall,
)

LEVER_IDENTITY: dict[str, tuple[str, ...]] = {
    "name": ("#name", 'input[name="name"]'),
    "email": ("#email", 'input[name="email"]', 'input[type="email"]'),
    "phone": ("#phone", 'input[name="phone"]', 'input[type="tel"]'),
    "org": ("#org", 'input[name="org"]', 'input[name="org"]'),
    "resume": ("#resume", 'input[type="file"][name="resume"]', 'input[type="file"]'),
    "comments": ("#comments", 'textarea[name="comments"]'),
}

SUBMIT_SELECTORS: tuple[str, ...] = (
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Submit application")',
)


class LeverApplicationAdapter(GreenhouseApplicationAdapter):
    """Applies to a posting hosted on Lever.

    Subclasses the Greenhouse adapter only to reuse field enumeration, document
    upload, and the verified-answer helpers. The selectors and the history blocks
    are Lever's, not Greenhouse's.
    """

    kind = "lever"
    FIELD_KEYS = frozenset(
        {
            "name",
            "email",
            "phone",
            "org",
            "linkedin",
            "comments",
            "country",
            "city",
            "state_residence",
            "postal_code",
            "street_address",
            "us_citizen",
            "spanish",
            "resume_headline",
        }
    )

    def supports(self, job: CanonicalJob | Any) -> bool:
        if str(getattr(job, "source", "")) == str(SourceKind.LEVER):
            return True
        url = str(getattr(job, "source_url", "") or "")
        return "lever.co" in url

    def inspect(self, job: CanonicalJob | Any) -> ApplicationForm:
        url = self.application_url(job)
        with self._page_for(url) as page:
            return self._inspect_lever_page(page, url)

    def _inspect_lever_page(self, page: FormPage, url: str) -> ApplicationForm:
        title = page.title()
        body = page.body_text()
        if looks_like_bot_wall(title, body):
            raise SafetyStop(
                SafetyStopKind.CAPTCHA,
                f"the posting is behind a bot-protection interstitial ({title!r}); "
                "a human must open and complete this application",
                {"url": url, "title": title},
            )
        if looks_like_login_wall(body):
            raise SafetyStop(
                SafetyStopKind.IDENTITY_VERIFICATION,
                "the application requires signing in or identity verification",
                {"url": url},
            )
        raw_fields = page.evaluate(ENUMERATE_FIELDS_JS) or []
        fields = [self._to_field(entry) for entry in raw_fields]
        captcha_script = (
            "() => { try { return "
            f"{json.dumps(list(CAPTCHA_SELECTORS))}.some(s => !!document.querySelector(s))"
            "; } catch (e) { return true; } }"
        )
        detection_failed = False
        try:
            captcha_present = bool(page.evaluate(captcha_script))
        except Exception:  # noqa: BLE001
            captcha_present = True
            detection_failed = True
        submit_selector = self._find_submit_selector(page, fields)
        if not _looks_like_lever_form(title, fields, submit_selector):
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "the page could not be confirmed as a Lever application form "
                f"(title {title!r}, {len(fields)} field(s))",
                {"url": page.current_url() or url, "title": title},
            )
        return ApplicationForm(
            url=page.current_url() or url,
            fields=fields,
            has_captcha=captcha_present,
            challenge_detection_failed=detection_failed,
            has_file_upload=any(f.kind == "file" for f in fields),
            submit_selector=submit_selector,
            page_hash=sha256_text(title + "|" + str(len(fields))),
            title=title,
            inspected_at=datetime.now(UTC),
        )

    def apply_to_page(
        self,
        page: FormPage,
        package: ApplicationPackageSchema,
        form: ApplicationForm,
        *,
        documents: dict[str, Path] | None = None,
    ) -> tuple[dict[str, str], list[str]]:
        filled: dict[str, str] = {}
        skipped: list[str] = []
        if documents is None:
            documents = self._prepare_documents(package, skipped, {})
        values = _lever_values(package)
        for name, value in values.items():
            if not value:
                continue
            field = next((f for f in form.fields if _lever_field_key(f) == name), None)
            selector = self._existing_selector(page, LEVER_IDENTITY.get(name, ()))
            if selector is None and field is not None:
                selector = field.selector
            if selector is None:
                skipped.append(name)
                continue
            target = field or FormField(name=name, kind="text")
            if not write_value(page, target, selector, value):
                skipped.append(name)
                continue
            filled[name] = value
            if field is not None:
                filled[field.name] = value

        self._fill_answers(page, package, form, filled, skipped)

        for field in form.fields:
            if (
                field.required
                and field.kind != "file"
                and field.name not in filled
                and field.name not in skipped
            ):
                skipped.append(field.name)

        self._upload_documents(page, form, package, documents, filled, skipped)
        return filled, skipped

    def _is_structural_field(self, field: FormField, package: ApplicationPackageSchema) -> bool:
        """Lever's own identity inputs and file inputs are not questions."""

        return field.kind == "file" or _lever_field_key(field) is not None

    def _filled_aliases(self, field: FormField) -> list[str]:
        key = _lever_field_key(field)
        return [key] if key else []

    def validate(
        self, package: ApplicationPackageSchema, form: ApplicationForm
    ) -> ValidationReport:
        stops = list(self.detect_stops(form, package))
        unknown_required: list[str] = []
        unanswered: list[str] = []
        mapped: dict[str, str] = {}
        values = _lever_values(package)

        for field in self._validation_fields(package, form, values):
            if field.kind == "file":
                if not (package.tailored_resume_markdown or "").strip():
                    unknown_required.append(
                        f"{describe_field(field)}: no tailored résumé is available to upload"
                    )
                else:
                    mapped[field.name] = "resume"
                continue
            lever_key = _lever_field_key(field)
            if lever_key and values.get(lever_key):
                problem = option_problem(field, values[lever_key])
                if problem:
                    unknown_required.append(f"{describe_field(field)}: {problem}")
                    continue
                mapped[field.name] = lever_key
                continue
            if lever_key:
                unknown_required.append(
                    f"{describe_field(field)}: no verified candidate value"
                )
                continue
            self._classify_question(package, field, mapped, unknown_required, unanswered)

        if unanswered:
            stops.append(
                SafetyStop(
                    SafetyStopKind.UNKNOWN_REQUIRED_QUESTION,
                    "these required questions have no verified answer and must be answered by "
                    "the candidate: " + "; ".join(sorted(set(unanswered))[:8]),
                    {"questions": sorted(set(unanswered))},
                )
            )
        if unknown_required:
            stops.append(
                SafetyStop(
                    SafetyStopKind.UNKNOWN_REQUIRED_QUESTION,
                    "these required fields cannot be completed from verified candidate data: "
                    + "; ".join(sorted(set(unknown_required))[:8]),
                    {"fields": sorted(set(unknown_required))},
                )
            )
        return ValidationReport(
            ok=not stops,
            stops=stops,
            unknown_required_fields=sorted(set(unknown_required)),
            unanswered_questions=sorted(set(unanswered)),
            mapped_fields=mapped,
            notes=["answers were drawn only from candidate-verified data"],
        )

    def application_url(self, job: Any) -> str:
        url = str(getattr(job, "source_url", "") or "")
        if "lever.co" in url:
            return url if url.rstrip("/").endswith("/apply") else url.rstrip("/") + "/apply"
        return url

    def submit(self, prepared: PreparedSubmission) -> SubmissionReceipt:
        """Click submit on the page `prepare` filled. Confirmation is the Lever
        `/thanks` page, decided by `detect_confirmation`, never by body keywords."""

        return self._submit_live(prepared, settle_ms=4000, capture_evidence=True)


def _looks_like_lever_form(title: str, fields: list[FormField], submit_selector: str | None) -> bool:
    if not submit_selector:
        return False
    lowered = (title or "").lower()
    if "apply" in lowered or "application" in lowered:
        return True
    names = {field.name.lower() for field in fields}
    return "name" in names and "email" in names


def _lever_field_key(field: FormField) -> str | None:
    name = field.name.lower()
    if name in {"name", "email", "phone", "org", "resume", "comments"}:
        return name
    if "street" in name or name in {"address", "street_address"}:
        return "street_address"
    if name in {"urls[linkedin]", "linkedin"} or "linkedin" in name:
        return "linkedin"
    return None


def _lever_values(package: ApplicationPackageSchema) -> dict[str, str]:
    values: dict[str, str] = {}
    if package.applicant_name:
        values["name"] = package.applicant_name
    if package.applicant_email:
        values["email"] = package.applicant_email
    if package.applicant_phone:
        values["phone"] = package.applicant_phone
    if package.applicant_street_address:
        values["street_address"] = package.applicant_street_address
    if package.applicant_linkedin:
        values["linkedin"] = package.applicant_linkedin
    if package.employment and package.employment[0].employer:
        values["org"] = package.employment[0].employer
    if package.cover_letter:
        values["comments"] = package.cover_letter
    # Verified answers may supply identity the package fields lack. Nothing is read
    # out of the résumé text.
    for answer in package.answers:
        if not is_typable(answer):
            continue
        if answer.question_key in {"name", "email", "phone"}:
            values.setdefault(answer.question_key, str(answer.answer))
    return values


# Re-export so a registry can import the adapter without pulling Greenhouse's
# private helpers into call sites that only need the class.
__all__ = ["LeverApplicationAdapter"]
