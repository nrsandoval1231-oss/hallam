"""Greenhouse application adapter (requirement 20, 21, 36).

Written against the *real* Greenhouse application form, discovered by inspecting a
live board with a browser (see `docs/operations/m6-greenhouse-form-findings.md` and
`tests/fixtures/greenhouse_application_form.json`). The measured structure is:

* identity inputs by id — `first_name`, `last_name`, `preferred_name`, `email`,
  `country`, `phone`;
* file inputs `resume` and `cover_letter`;
* a repeatable employment block — `company-name-0`, `title-0`,
  `start-date-month-0`, `start-date-year-0`, `end-date-month-0`, `end-date-year-0`,
  `current-role-0_1`;
* a repeatable education block — `school--0`, `degree--0`, `discipline--0`,
  `start-month--0`, `start-year--0`, `end-month--0`, `end-year--0`;
* board-specific questions as `question_<id>`, labelled in the DOM;
* a submit control whose accessible name is "Submit application".

**The form is CAPTCHA-protected.** The captured page carried three iframes and a
CAPTCHA element, and one board in the registry sits behind a "Vercel Security
Checkpoint" interstitial. Requirement 21 says a CAPTCHA means stop safely and ask a
human; it does not say work around it. This adapter therefore *detects* those
conditions and stops, rather than attempting to defeat a control the employer put
there deliberately.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sabra_documents.answers import classify_question
from sabra_domain.enums import SafetyStopKind, SourceKind, SubmissionMethod
from sabra_domain.errors import SafetyStop
from sabra_domain.identity import sha256_text
from sabra_domain.schemas import ApplicationAnswerSchema, ApplicationPackageSchema, CanonicalJob

from .base import (
    ApplicationAdapter,
    ApplicationForm,
    ClaimLost,
    FormField,
    PreparedSubmission,
    SubmissionReceipt,
    ValidationReport,
)
from .confirmation import detect_confirmation, take_snapshot
from .history import plan_greenhouse_field
from .playwright_driver import (
    CAPTCHA_SELECTORS,
    FormPage,
    looks_like_bot_wall,
    looks_like_login_wall,
)

# Selector sets derived from the captured fixture, with fallbacks so a board that
# renames an id does not silently stop working. Every selector is tried in order.
IDENTITY_SELECTORS: dict[str, tuple[str, ...]] = {
    "first_name": ("#first_name", 'input[name="first_name"]', 'input[autocomplete="given-name"]'),
    "last_name": ("#last_name", 'input[name="last_name"]', 'input[autocomplete="family-name"]'),
    "preferred_name": ("#preferred_name",),
    "email": ("#email", 'input[name="email"]', 'input[type="email"]'),
    "phone": ("#phone", 'input[name="phone"]', 'input[type="tel"]'),
    "country": ("#country", 'input[name="country"]'),
    "street_address": (
        "#street_address",
        'input[name="street_address"]',
        "#address",
        'input[name="address"]',
        'input[autocomplete="street-address"]',
    ),
    "resume": ("#resume", 'input[type="file"][id*="resume" i]'),
    "cover_letter": ("#cover_letter", 'input[type="file"][id*="cover" i]'),
}

IDENTITY_FIELD_NAMES: tuple[str, ...] = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "country",
)

# Page titles Greenhouse uses for a real application form, e.g.
# "Job Application for Clinical Field Specialist at Natera".
APPLICATION_TITLE_SIGNALS: tuple[str, ...] = ("job application", "apply for", "application for")

SUBMIT_SELECTORS: tuple[str, ...] = (
    'button[type="submit"]',
    'input[type="submit"]',
    "#submit_app",
    'button:has-text("Submit application")',
)

# Attestations that must be the candidate's own statement.
ATTESTATION_SIGNALS: tuple[str, ...] = (
    "i certify",
    "i agree",
    "i acknowledge",
    "terms and conditions",
    "privacy policy",
    "arbitration",
    "at-will",
    "background check authorization",
    "i consent",
)

ENUMERATE_FIELDS_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('input, textarea, select').forEach((el) => {
    if (el.type === 'hidden') return;
    const id = el.id || null;
    const key = id || (el.getAttribute('name') || '') + '|' + el.tagName + '|' + (el.getAttribute('type') || '');
    if (seen.has(key)) return;
    seen.add(key);
    const index = out.length;
    let label = null;
    if (el.id) {
      const escaped = (window.CSS && CSS.escape) ? CSS.escape(el.id) : el.id;
      const labelEl = document.querySelector(`label[for="${escaped}"]`);
      if (labelEl) label = labelEl.innerText;
    }
    if (!label) {
      const wrap = el.closest('label');
      if (wrap) label = wrap.innerText;
    }
    if (!label) label = el.getAttribute('aria-label');
    const tag = el.tagName.toLowerCase();
    let type = el.getAttribute('type');
    if (tag === 'select') type = 'select';
    else if (tag === 'textarea') type = 'textarea';
    else if (!type) type = 'text';
    let context = null;
    const holder = el.closest('fieldset, [class*="field"], [class*="question"], div');
    if (holder && holder.innerText) {
      context = holder.innerText.replace(/\\s+/g, ' ').trim().slice(0, 120) || null;
    }
    out.push({
      id: id,
      name: el.getAttribute('name') || null,
      ariaLabel: el.getAttribute('aria-label') || null,
      autocomplete: el.getAttribute('autocomplete') || null,
      index: index,
      context: context,
      tag: tag,
      type: type,
      required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      label: label ? label.replace(/\\s+/g, ' ').trim().slice(0, 300) : null,
      placeholder: el.getAttribute('placeholder'),
      value: type === 'file' ? null : (el.value || '').slice(0, 200),
      options: tag === 'select' ? Array.from(el.options).map(o => o.value).slice(0, 50) : [],
    });
  });
  return out;
}
"""


_PLAIN_ID = re.compile(r"^[A-Za-z_][\w-]*$")


def _css_attr(value: str) -> str:
    """Escape a value for a double-quoted CSS attribute selector.

    A raw newline is a syntax error inside a CSS string; `\a ` is its escape.
    """

    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "")
        .replace("\n", "\\a ")
    )


def _count_matches(page: Any, selector: str) -> int:
    value = page.evaluate("() => document.querySelectorAll(" + repr(selector) + ").length")
    return int(value or 0)


def field_selector(entry: dict[str, Any]) -> str | None:
    """A selector for an enumerated field: `#id`, else `[name]`, else `[aria-label]`."""

    identifier = entry.get("id")
    if identifier:
        if _PLAIN_ID.match(str(identifier)):
            return f"#{identifier}"
        return f'[id="{_css_attr(str(identifier))}"]'
    if entry.get("name"):
        return f'[name="{_css_attr(str(entry["name"]))}"]'
    if entry.get("ariaLabel"):
        return f'[aria-label="{_css_attr(str(entry["ariaLabel"]))}"]'
    return None


def describe_field(field: FormField) -> str:
    """How a blocked field is named in a report: always something findable."""

    if field.label and field.label.strip() and field.label.strip() != field.name:
        return f"{field.label.strip()} [{field.name}]"
    if field.context and field.name.startswith("unnamed-"):
        return f"{field.name} (near: {field.context.strip()[:80]})"
    return field.name


def _matching_option(options: list[str], value: str) -> str | None:
    """Pick a select option that equals the verified value, ignoring case."""

    target = value.strip().lower()
    for option in options:
        if option.strip().lower() == target:
            return option
    for option in options:
        if target and target in option.strip().lower():
            return option
    return None


def looks_like_application_form(
    title: str, fields: list[FormField], submit_selector: str | None
) -> bool:
    """Whether an inspected page can be trusted to be the application form.

    Two independent signals are required, so a consent or interstitial page cannot
    pass by accident:

    1. a submit control exists; and
    2. either the page title names it as an application, or at least two of the known
       identity inputs are present.

    Measured live: a board served a cookie-consent overlay whose only field was the
    consent widget. Without this check the adapter treated it as a form, and because
    a consent page has no required fields, validation returned `ok=True`.
    """

    if not submit_selector:
        return False
    lowered_title = (title or "").lower()
    if any(signal in lowered_title for signal in APPLICATION_TITLE_SIGNALS):
        return True
    names = {field.name.lower() for field in fields}
    present = sum(1 for name in IDENTITY_FIELD_NAMES if name in names)
    return present >= 2


class GreenhouseApplicationAdapter(ApplicationAdapter):
    """Applies to a posting hosted on a Greenhouse job board."""

    kind = "greenhouse"
    # Question and identity keys this adapter can fill from verified answers.
    # The answers page derives its dropdown from this set.
    FIELD_KEYS = frozenset(
        {
            "first_name",
            "last_name",
            "preferred_name",
            "email",
            "phone",
            "country",
            "city",
            "state_residence",
            "postal_code",
            "street_address",
            "us_citizen",
            "spanish",
            "resume_headline",
            "age_eligibility",
            "non_compete",
        }
    )

    def __init__(
        self,
        *,
        dry_run: bool = True,
        settings: Any = None,
        page_factory: Any = None,
        evidence_dir: Any = None,
    ) -> None:
        super().__init__(dry_run=dry_run, settings=settings, page_factory=page_factory)
        self._evidence_dir = evidence_dir

    # -- contract -------------------------------------------------------

    def supports(self, job: CanonicalJob | Any) -> bool:
        if str(getattr(job, "source", "")) == str(SourceKind.GREENHOUSE):
            return True
        url = str(getattr(job, "source_url", "") or "")
        return "greenhouse.io" in url

    def inspect(self, job: CanonicalJob | Any) -> ApplicationForm:
        url = self.application_url(job)
        with self._page_for(url) as page:
            return self._inspect_page(page, url)

    def _inspect_page(self, page: FormPage, url: str) -> ApplicationForm:
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
        # JSON-encoded, not hand-quoted: interpolating CSS selectors that contain
        # double quotes produced `["iframe[src*="recaptcha"]", ...]`, which is a
        # JavaScript syntax error. Playwright surfaced it as
        # "SyntaxError: Unexpected identifier 'recaptcha'".
        captcha_script = (
            "() => { try { return "
            f"{json.dumps(list(CAPTCHA_SELECTORS))}.some(s => !!document.querySelector(s))"
            "; } catch (e) { return true; } }"
        )
        detection_failed = False
        try:
            captcha_present = bool(page.evaluate(captcha_script))
        except Exception:  # noqa: BLE001 - a failed probe is a challenge, not a pass
            captcha_present = True
            detection_failed = True
        attestations = [
            f.label or ""
            for f in fields
            if f.label and any(signal in f.label.lower() for signal in ATTESTATION_SIGNALS)
        ]
        submit_selector = self._find_submit_selector(page, fields)

        # Confirm we are actually looking at an application form before doing
        # anything with it. Measured live: a board served a cookie-consent overlay
        # whose only field was the consent widget, and the adapter accepted it as
        # the form. A consent page has no required fields, so validation would
        # have returned ok=True and the service would have proceeded to "prepare"
        # — and with a real submission enabled, to click — something that is not
        # the application form.
        if not looks_like_application_form(title, fields, submit_selector):
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "the page could not be confirmed as an application form "
                f"(title {title!r}, {len(fields)} field(s), "
                f"submit control {'found' if submit_selector else 'not found'}); "
                "a human must open this posting and complete it. The page is "
                "usually a consent or bot interstitial in front of the form",
                {
                    "url": page.current_url() or url,
                    "title": title,
                    "field_names": [f.name for f in fields][:10],
                },
            )

        return ApplicationForm(
            url=page.current_url() or url,
            fields=fields,
            has_captcha=captcha_present,
            challenge_detection_failed=detection_failed,
            requires_login=False,
            has_file_upload=any(f.kind == "file" for f in fields),
            attestations=attestations,
            submit_selector=submit_selector,
            page_hash=sha256_text(title + "|" + str(len(fields))),
            title=title,
            inspected_at=datetime.now(UTC),
        )

    def prepare(
        self, package: ApplicationPackageSchema, form: ApplicationForm
    ) -> PreparedSubmission:
        """Fill recognised fields on the live session page. Does not submit.

        Identity, employment, education, the résumé, and verified answers are typed.
        A required field with no verified value is left blank and named. Inside
        `session()` this is the page `inspect` read and the page `submit` will click;
        outside a session it is a one-shot fill that is discarded and can never be
        submitted.
        """

        url = form.url
        in_session = self._page is not None
        if not in_session and self._page_factory is None:
            # Typing and uploading into a page that is then thrown away is never
            # useful, and with a real browser it is a side effect on the employer's
            # site. Reading (`inspect`) outside a session is allowed; filling is not.
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "prepare needs a live session: open one with `with adapter.session(url):`",
                {"url": url},
            )
        with self._page_for(url) as page:
            if in_session:
                self._require_live_page(url)
            filled, skipped = self.apply_to_page(page, package, form)
            if in_session:
                self._filled_page = page
            return PreparedSubmission(
                url=url,
                filled=filled,
                files={k: v for k, v in filled.items() if k in {"resume", "cover_letter"}},
                skipped=skipped,
                prepared_at=datetime.now(UTC),
                dry_run=self.dry_run,
                submit_selector=form.submit_selector,
                unfilled_required=self._unfilled_required(package, form, filled),
            )

    @staticmethod
    def _unfilled_required(
        package: ApplicationPackageSchema, form: ApplicationForm, filled: dict[str, str]
    ) -> list[str]:
        """Required non-file fields that were left empty and are not empty on purpose.

        A history field the plan deliberately leaves blank (the end date of a current
        role) is covered; anything else that is required and untouched blocks the click.
        """

        missing: list[str] = []
        for field in form.fields:
            if not field.required or field.kind == "file":
                continue
            if field.name in filled or (field.question_key and field.question_key in filled):
                continue
            plan = plan_greenhouse_field(field.name, package.employment, package.education)
            if plan is not None and plan.covered and plan.action == "blank":
                continue
            missing.append(describe_field(field))
        return missing

    def apply_to_page(
        self,
        page: FormPage,
        package: ApplicationPackageSchema,
        form: ApplicationForm,
    ) -> tuple[dict[str, str], list[str]]:
        """Type verified values onto an already-open page. Never clicks submit."""

        filled: dict[str, str] = {}
        skipped: list[str] = []
        identity = self._identity_values(package)
        for name, value in identity.items():
            if not value:
                skipped.append(name)
                continue
            selector = self._existing_selector(page, IDENTITY_SELECTORS.get(name, ()))
            if selector is None:
                skipped.append(name)
                continue
            page.fill(selector, value)
            filled[name] = value

        for field in form.fields:
            plan = plan_greenhouse_field(field.name, package.employment, package.education)
            if plan is None or not plan.covered or plan.action == "blank":
                continue
            selector = field.selector or (f"#{field.name}" if field.name else None)
            if not selector:
                skipped.append(field.name)
                continue
            if plan.action == "check":
                page.check(selector)
                filled[field.name] = "checked"
            elif plan.value:
                self._write_value(page, field, selector, plan.value)
                filled[field.name] = plan.value

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
            bridged = self._bridged_answer(package, field)
            if bridged is None or field.name in filled or bridged.question_key in filled:
                continue
            if not field.selector:
                continue
            self._write_value(page, field, field.selector, bridged.answer or "")
            filled[field.name] = bridged.answer or ""

        for field in form.fields:
            if (
                field.required
                and field.kind != "file"
                and field.name not in filled
                and field.name not in skipped
            ):
                skipped.append(field.name)

        self._upload_documents(page, form, package, filled, skipped)
        return filled, skipped

    def validate(
        self, package: ApplicationPackageSchema, form: ApplicationForm
    ) -> ValidationReport:
        """Decide whether this form can be completed without a human.

        A required question that no stored answer covers is a stop, per requirement
        21: the system must not guess at a consequential question.
        """

        stops = list(self.detect_stops(form, package))
        unknown_required: list[str] = []
        unanswered: list[str] = []
        mapped: dict[str, str] = {}
        identity = self._identity_values(package)

        for field in form.required_fields:
            if field.kind == "file":
                if "resume" in field.name.lower() and not (package.tailored_resume_markdown or "").strip():
                    unknown_required.append(
                        f"{describe_field(field)}: no tailored résumé is available to upload"
                    )
                elif (package.tailored_resume_markdown or "").strip() and "resume" in field.name.lower():
                    mapped[field.name] = "resume"
                continue
            # Structural identity first. The captured form labels its employment-block
            # date inputs "Start date month" / "Start date year", which the question
            # catalogue classifies as the "when can you start?" question — a false
            # blocker, because the field belongs to a known repeating block rather
            # than to a board question.
            if self._is_identity_field(field):
                key = self._identity_key(field.name)
                if key and identity.get(key):
                    mapped[field.name] = "identity"
                else:
                    unknown_required.append(
                        f"{describe_field(field)}: no verified candidate value"
                    )
                continue
            plan = plan_greenhouse_field(field.name, package.employment, package.education)
            if plan is not None:
                if plan.covered:
                    mapped[field.name] = plan.section
                else:
                    unknown_required.append(plan.reason or describe_field(field))
                continue
            key = field.question_key or classify_question(field.label or "")
            answer = self._answer_text(package, key) if key else None
            if key and answer:
                mapped[field.name] = key
                continue
            if key:
                unanswered.append(field.label or field.name)
                continue
            unknown_required.append(describe_field(field))

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
            notes=[
                f"form has {len(form.fields)} fields, {len(form.required_fields)} required",
                "answers were drawn only from candidate-verified memory",
            ],
        )

    def submit(self, prepared: PreparedSubmission) -> SubmissionReceipt:
        """Click submit on the page `prepare` filled. Only reachable with a validated,
        approved, opt-in submission."""

        return self._submit_live(prepared, settle_ms=6000, capture_evidence=True)

    def _submit_live(
        self,
        prepared: PreparedSubmission,
        *,
        settle_ms: int,
        capture_evidence: bool,
    ) -> SubmissionReceipt:
        """Shared by the Greenhouse and Lever adapters.

        Refuses unless the live session page is the one `prepare` filled, every
        required field was filled, and the control `inspect` found matches exactly one
        element. Whether the employer confirmed is decided by `detect_confirmation`,
        never by body keywords. A click with no confirmation is returned as
        `clicked=True, submitted=False`.
        """

        self.require_opt_in()
        if not prepared.filled and not prepared.files:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "refusing to submit an empty form",
            )
        page = self._page
        if page is None or self._filled_page is not page:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "refusing to submit: there is no live browser page that prepare filled in "
                "this session, so there is no form to click",
                {"url": prepared.url},
            )
        self._require_live_page(prepared.url)
        if prepared.unfilled_required:
            raise SafetyStop(
                SafetyStopKind.UNKNOWN_REQUIRED_QUESTION,
                "refusing to submit: required fields were left empty: "
                + "; ".join(prepared.unfilled_required[:8]),
                {"fields": prepared.unfilled_required},
            )
        submit_selector = prepared.submit_selector
        if not submit_selector:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "refusing to submit: inspect did not find a submit control",
                {"url": prepared.url},
            )
        matches = _count_matches(page, submit_selector)
        if matches != 1:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                f"refusing to submit: {submit_selector!r} matches {matches} elements, not one",
                {"url": prepared.url, "selector": submit_selector, "matches": matches},
            )

        evidence: list[str] = []
        title = page.title()
        body = page.body_text()
        if looks_like_bot_wall(title, body):
            raise SafetyStop(
                SafetyStopKind.CAPTCHA,
                f"bot-protection interstitial appeared at submission time ({title!r})",
                {"url": prepared.url},
            )
        before = take_snapshot(page, self.kind)
        if not before.form_present:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "refusing to submit: the application form is not on the page",
                {"url": prepared.url},
            )
        if capture_evidence:
            screenshot = self._capture(page, "before_submit")
            if screenshot:
                evidence.append(screenshot)

        if self.pre_click_check is not None and not self.pre_click_check():
            raise ClaimLost(
                "the application is no longer APPLYING under this run, so nothing was clicked"
            )

        # From here the employer may have received the application, so nothing below
        # may raise: any failure is reported as an unconfirmed click.
        self.click_attempted = True
        try:
            page.click(submit_selector)
            page.wait(settle_ms)
            confirmation = detect_confirmation(page, self.kind, before)
            after_url = page.current_url()
            after_title = page.title()
            if capture_evidence:
                screenshot = self._capture(page, "after_submit")
                if screenshot:
                    evidence.append(screenshot)
        except Exception as exc:  # noqa: BLE001 - the click may have gone through
            return SubmissionReceipt(
                submitted=False,
                clicked=True,
                method=SubmissionMethod.PLAYWRIGHT_ADAPTER,
                external_confirmation={"form_url": prepared.url, "confirmation_detected": False},
                evidence_paths=evidence,
                error=f"the submit click was attempted but the result could not be read ({exc}); "
                "the application is NOT recorded as submitted and must be checked by a person",
                dry_run=False,
            )

        confirmed = confirmation.confirmed
        return SubmissionReceipt(
            submitted=confirmed,
            clicked=True,
            method=SubmissionMethod.PLAYWRIGHT_ADAPTER,
            external_confirmation={
                "url": after_url,
                "page_title": after_title,
                "confirmation_detected": confirmed,
                "confirmation_reason": confirmation.reason,
                "confirmation_marker": confirmation.marker,
                "form_url": prepared.url,
            },
            confirmation_text=confirmation.text if confirmed else None,
            evidence_paths=evidence,
            submitted_at=datetime.now(UTC) if confirmed else None,
            error=None
            if confirmed
            else "the submit control was clicked but no confirmation was detected "
            f"({confirmation.reason}); the application is NOT recorded as submitted",
            dry_run=False,
        )

    # -- helpers --------------------------------------------------------

    def application_url(self, job: Any) -> str:
        url = str(getattr(job, "source_url", "") or "")
        if "greenhouse.io" in url or "revance.com" in url or "?gh_jid=" in url:
            return url
        token = getattr(job, "board_token", None)
        job_id = getattr(job, "source_job_id", None)
        if token and job_id:
            return f"https://job-boards.greenhouse.io/{token}/jobs/{job_id}"
        return url

    @staticmethod
    def _to_field(entry: dict[str, Any]) -> FormField:
        """Name and address a field so a human can find it on the page.

        Name: id, else `name` attribute, else label, else aria-label, else placeholder,
        else `unnamed-<tag>-<position>`. Selector: `#id`, else `[name="..."]`, else
        `[aria-label="..."]`.
        """

        identifier = (
            entry.get("id")
            or entry.get("name")
            or entry.get("label")
            or entry.get("ariaLabel")
            or entry.get("placeholder")
            or f"unnamed-{entry.get('tag') or 'input'}-{entry.get('index', 0)}"
        )
        label = entry.get("label") or entry.get("ariaLabel")
        kind = str(entry.get("type") or "text").lower()
        return FormField(
            name=" ".join(str(identifier).split()),
            label=label,
            kind=kind,
            required=bool(entry.get("required")),
            options=list(entry.get("options") or []),
            selector=field_selector(entry),
            question_key=classify_question(entry.get("label") or "") or None,
            context=entry.get("context") or None,
        )

    @staticmethod
    def _find_submit_selector(page: FormPage, fields: list[FormField]) -> str | None:
        """The submit control to click: the first selector matching exactly one element.

        If none matches exactly one, the first that matches anything is returned, and
        `submit` then refuses because the match is ambiguous.
        """

        first_any: str | None = None
        for selector in SUBMIT_SELECTORS:
            try:
                found = _count_matches(page, selector)
            except Exception:  # noqa: BLE001
                continue
            if found == 1:
                return selector
            if found and first_any is None:
                first_any = selector
        return first_any

    @staticmethod
    def _existing_selector(page: FormPage, selectors: tuple[str, ...]) -> str | None:
        for selector in selectors:
            try:
                found = page.evaluate(
                    "() => document.querySelectorAll(" + repr(selector) + ").length"
                )
            except Exception:  # noqa: BLE001
                continue
            if found:
                return selector
        return None

    @staticmethod
    def _identity_key(name: str) -> str | None:
        lowered = name.lower()
        if "street" in lowered or lowered in {"address", "street_address"}:
            return "street_address"
        for token in (
            "first_name",
            "last_name",
            "preferred_name",
            "email",
            "phone",
            "country",
        ):
            if token in lowered:
                return token
        return None

    @staticmethod
    def _write_value(page: FormPage, field: FormField, selector: str, value: str) -> None:
        if field.kind == "select" and field.options:
            match = _matching_option(field.options, value)
            page.select_option(selector, match or value)
            return
        if field.kind == "checkbox":
            page.check(selector)
            return
        page.fill(selector, value)

    def _answer_text(self, package: ApplicationPackageSchema, key: str | None) -> str | None:
        if not key:
            return None
        for answer in package.answers:
            if answer.question_key == key and answer.answer and not answer.requires_user_input:
                return str(answer.answer)
        bridged = self._bridge_value(package, key)
        return bridged

    def _bridged_answer(
        self, package: ApplicationPackageSchema, field: FormField
    ) -> ApplicationAnswerSchema | None:
        key = field.question_key or classify_question(field.label or "")
        if not key:
            return None
        # A stored answer already handled above wins; this only covers fields the
        # package can answer from verified identity or employment.
        if any(a.question_key == key and a.answer and not a.requires_user_input for a in package.answers):
            return None
        value = self._bridge_value(package, key)
        if not value:
            return None
        return ApplicationAnswerSchema(
            question_key=key,
            question_text=field.label,
            answer=value,
            requires_user_input=False,
        )

    @staticmethod
    def _bridge_value(package: ApplicationPackageSchema, key: str) -> str | None:
        """Verified package fields that answer a recognised question. No invention."""

        if key == "current_employer" and package.employment:
            return package.employment[0].employer or None
        mapping = {
            "city": package.applicant_city,
            "state_residence": package.applicant_state,
            "postal_code": package.applicant_postal_code,
            "phone": package.applicant_phone,
            "street_address": package.applicant_street_address,
            "linkedin": package.applicant_linkedin,
        }
        value = mapping.get(key)
        return value or None

    @staticmethod
    def _identity_values(package: ApplicationPackageSchema) -> dict[str, str]:
        """Identity values from verified sources only.

        Sources: the package's `applicant_*` fields, and package answers that are
        verified (`not requires_user_input`). Nothing is parsed out of the résumé text
        and nothing is inferred: a name, an e-mail address or a country that is not on
        the package is simply absent, and validation then blocks on the field.
        """

        values: dict[str, str] = {}
        if package.applicant_name:
            parts = package.applicant_name.split()
            if parts:
                values["first_name"] = parts[0]
            if len(parts) > 1:
                values["last_name"] = " ".join(parts[1:])
        if package.applicant_email:
            values["email"] = package.applicant_email
        if package.applicant_phone:
            values["phone"] = package.applicant_phone
        if package.applicant_country:
            values["country"] = package.applicant_country
        if package.applicant_street_address:
            values["street_address"] = package.applicant_street_address
        for answer in package.answers:
            if not answer.answer or answer.requires_user_input:
                continue
            key = answer.question_key
            if key in {"first_name", "last_name", "email", "phone", "country", "preferred_name"}:
                values[key] = str(answer.answer)
        return values

    @staticmethod
    def _question_selector(form: ApplicationForm, question_key: str) -> str | None:
        for field in form.fields:
            if field.question_key == question_key and field.selector:
                return field.selector
        return None

    @staticmethod
    def _is_identity_field(field: FormField) -> bool:
        return GreenhouseApplicationAdapter._identity_key(field.name) is not None

    @staticmethod
    def _is_structured_history_field(field: FormField) -> bool:
        """Employment and education blocks are known sections, not unknowns.

        They are recorded as mapped so the report distinguishes "a section we cannot
        fill" from "a question we do not understand" — the first is a known limit, the
        second is a reason to stop.
        """

        name = field.name.lower()
        patterns = (
            "company-name",
            "title-",
            "start-date",
            "end-date",
            "current-role",
            "school",
            "degree",
            "discipline",
            "start-month",
            "start-year",
            "end-month",
            "end-year",
        )
        return any(pattern in name for pattern in patterns)

    def _upload_documents(
        self,
        page: FormPage,
        form: ApplicationForm,
        package: ApplicationPackageSchema,
        filled: dict[str, str],
        skipped: list[str],
    ) -> None:
        """Upload the tailored résumé and cover letter when the form accepts files.

        Documents are written to the configured artifact directory first, so the file
        that was uploaded is retained and auditable alongside the submission evidence.
        """

        from pathlib import Path

        base = Path(
            getattr(self._evidence_dir, "__fspath__", lambda: None)()
            if self._evidence_dir is not None
            else (getattr(self.settings, "artifacts_dir", None) or Path("var/artifacts"))
        )
        base = Path(base) / "applications" / package.opportunity_id
        base.mkdir(parents=True, exist_ok=True)

        resume_path = base / "tailored_resume.md"
        resume_path.write_text(package.tailored_resume_markdown or "", encoding="utf-8")
        selector = self._existing_selector(page, IDENTITY_SELECTORS["resume"])
        if selector:
            page.set_input_files(selector, str(resume_path))
            filled["resume"] = str(resume_path)
        else:
            skipped.append("resume upload not available on this form")

        if package.cover_letter:
            cover_path = base / "cover_letter.md"
            cover_path.write_text(package.cover_letter, encoding="utf-8")
            selector = self._existing_selector(page, IDENTITY_SELECTORS["cover_letter"])
            if selector:
                page.set_input_files(selector, str(cover_path))
                filled["cover_letter"] = str(cover_path)
            else:
                skipped.append("cover letter upload not available on this form")

    def _capture(self, page: FormPage, label: str) -> str | None:
        from pathlib import Path

        base = Path(
            getattr(self.settings, "automation_screenshot_dir", None) or Path("var/automation")
        )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return page.screenshot(str(base / f"{stamp}_{label}.png"))
