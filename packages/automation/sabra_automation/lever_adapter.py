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
from typing import Any

from sabra_documents.answers import classify_question
from sabra_domain.enums import SafetyStopKind, SourceKind, SubmissionMethod
from sabra_domain.errors import SafetyStop
from sabra_domain.identity import sha256_text
from sabra_domain.schemas import ApplicationPackageSchema, CanonicalJob

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
    _matching_option,
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

    def supports(self, job: CanonicalJob | Any) -> bool:
        if str(getattr(job, "source", "")) == str(SourceKind.LEVER):
            return True
        url = str(getattr(job, "source_url", "") or "")
        return "lever.co" in url

    def inspect(self, job: CanonicalJob | Any) -> ApplicationForm:
        url = self._application_url(job)
        page, session = self._open(url)
        try:
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
            self._last_page = page
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
        finally:
            if session is not None:
                self._close(session)
                self._last_page = None

    def apply_to_page(
        self,
        page: FormPage,
        package: ApplicationPackageSchema,
        form: ApplicationForm,
    ) -> tuple[dict[str, str], list[str]]:
        filled: dict[str, str] = {}
        skipped: list[str] = []
        values = _lever_values(package)
        for name, value in values.items():
            if not value:
                continue
            selector = self._existing_selector(page, LEVER_IDENTITY.get(name, ()))
            if selector is None:
                skipped.append(name)
                continue
            page.fill(selector, value)
            filled[name] = value

        for answer in package.answers:
            if answer.requires_user_input or not answer.answer:
                continue
            selector = self._question_selector(form, answer.question_key)
            if selector is None:
                skipped.append(f"question:{answer.question_key}")
                continue
            page.fill(selector, str(answer.answer))
            filled[answer.question_key] = str(answer.answer)

        for field in form.fields:
            if field.name in filled or (field.question_key and field.question_key in filled):
                continue
            bridged = self._bridged_answer(package, field)
            if bridged is None or not field.selector or not bridged.answer:
                continue
            if field.kind == "select":
                page.select_option(field.selector, _matching_option(field.options, bridged.answer) or bridged.answer)
            else:
                page.fill(field.selector, bridged.answer)
            filled[field.name] = bridged.answer

        for field in form.fields:
            if field.required and field.kind != "file" and field.name not in filled:
                skipped.append(field.name)

        self._upload_documents(page, form, package, filled, skipped)
        return filled, skipped

    def validate(
        self, package: ApplicationPackageSchema, form: ApplicationForm
    ) -> ValidationReport:
        stops = list(self.detect_stops(form, package))
        unknown_required: list[str] = []
        unanswered: list[str] = []
        mapped: dict[str, str] = {}
        values = _lever_values(package)

        for field in form.required_fields:
            if field.kind == "file":
                if not (package.tailored_resume_markdown or "").strip():
                    unknown_required.append(
                        f"{field.label or field.name}: no tailored résumé is available to upload"
                    )
                else:
                    mapped[field.name] = "resume"
                continue
            lever_key = _lever_field_key(field)
            if lever_key and values.get(lever_key):
                mapped[field.name] = lever_key
                continue
            if lever_key:
                unknown_required.append(
                    f"{field.label or field.name}: no verified candidate value"
                )
                continue
            key = field.question_key or classify_question(field.label or "")
            answer = self._answer_text(package, key) if key else None
            if key and answer:
                mapped[field.name] = key
                continue
            if key:
                unanswered.append(field.label or field.name)
                continue
            unknown_required.append(field.label or field.name)

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

    def _application_url(self, job: Any) -> str:
        url = str(getattr(job, "source_url", "") or "")
        if "lever.co" in url:
            return url if url.rstrip("/").endswith("/apply") else url.rstrip("/") + "/apply"
        return url

    def submit(self, prepared: PreparedSubmission) -> SubmissionReceipt:
        self.require_opt_in()
        if not prepared.filled and not prepared.files:
            raise SafetyStop(SafetyStopKind.MATERIAL_AMBIGUITY, "refusing to submit an empty form")
        page, session = self._open(prepared.url)
        try:
            title = page.title()
            body = page.body_text()
            if looks_like_bot_wall(title, body):
                raise SafetyStop(
                    SafetyStopKind.CAPTCHA,
                    f"bot-protection interstitial appeared at submission time ({title!r})",
                    {"url": prepared.url},
                )
            page.click(SUBMIT_SELECTORS[0])
            page.wait(1000)
            after_body = page.body_text()
            confirmed = any(
                signal in after_body.lower()
                for signal in ("thank you", "application submitted", "we received", "submitted")
            )
            return SubmissionReceipt(
                submitted=confirmed,
                method=SubmissionMethod.PLAYWRIGHT_ADAPTER,
                external_confirmation={
                    "url": page.current_url(),
                    "page_title": page.title(),
                    "confirmation_detected": confirmed,
                    "form_url": prepared.url,
                },
                confirmation_text=after_body[:2000] if confirmed else None,
                submitted_at=datetime.now(UTC) if confirmed else None,
                error=None
                if confirmed
                else "no confirmation was detected on the page after submitting; "
                "the application is NOT recorded as submitted",
                dry_run=False,
            )
        finally:
            if session is not None:
                self._close(session)


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
    if package.applicant_linkedin:
        values["linkedin"] = package.applicant_linkedin
    if package.employment and package.employment[0].employer:
        values["org"] = package.employment[0].employer
    if package.cover_letter:
        values["comments"] = package.cover_letter
    return values


# Re-export so a registry can import the adapter without pulling Greenhouse's
# private helpers into call sites that only need the class.
__all__ = ["LeverApplicationAdapter"]
