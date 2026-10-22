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

import hashlib
import json
import re
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sabra_documents.answers import classify_question
from sabra_domain.enums import SafetyStopKind, SourceKind, SubmissionMethod
from sabra_domain.errors import SafetyStop
from sabra_domain.identity import sha256_text
from sabra_domain.schemas import ApplicationPackageSchema, CanonicalJob

from .answer_match import (
    OPTION_READ_LIMIT,
    is_typable,
    match_answer,
    option_problem,
    write_value,
)
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

# Prefix of a `skipped` entry that means a document could not be produced.
DOCUMENT_ERROR = "document render failed:"

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
      // Visible labels, not values: an answer is matched to the words the person sees.
      options: tag === 'select'
        ? Array.from(el.options).map(o => (o.textContent || '').replace(/\\s+/g, ' ').trim() || o.value).slice(0, __LIMIT__)
        : [],
    });
  });
  return out;
}
"""


ENUMERATE_FIELDS_JS = ENUMERATE_FIELDS_JS.replace("__LIMIT__", str(OPTION_READ_LIMIT))

_PLAIN_ID = re.compile(r"^[A-Za-z_][\w-]*$")


def _css_attr(value: str) -> str:
    """Escape a value for a double-quoted CSS attribute selector.

    A raw newline is a syntax error inside a CSS string; `\a ` is its escape.
    """

    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\a ")


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


def split_name(full_name: str) -> tuple[str | None, str | None]:
    """First and last name from a full name, without turning a middle initial into a surname.

    "JORDAN A. RIVERA" is first "JORDAN", last "RIVERA": a single letter (with or without
    a period) between the first and last tokens is a middle initial. Anything longer
    stays in the last name ("Mary Jo Smith" is last "Jo Smith"), because guessing a
    middle name would be a guess. Structured first/last fields on the package win over
    this split; see `_identity_values`.
    """

    parts = (full_name or "").split()
    if not parts:
        return None, None
    first = parts[0]
    rest = parts[1:]
    while len(rest) > 1 and re.fullmatch(r"[A-Za-z]\.?", rest[0]):
        rest = rest[1:]
    return first, " ".join(rest) or None


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
        # Render the documents BEFORE the page is touched. In a real run `prepare_documents`
        # already ran before the browser opened (the execution service calls it), so this
        # is a cache hit; either way a render failure stops here, with nothing typed.
        documents, notes, report = self._documents_for(package)
        with self._page_for(url) as page:
            if in_session:
                self._require_live_page(url)
            filled, skipped = self.apply_to_page(page, package, form, documents=documents)
            if in_session:
                self._filled_page = page
            return PreparedSubmission(
                url=url,
                filled=filled,
                files={k: v for k, v in filled.items() if k in {"resume", "cover_letter"}},
                skipped=[*notes, *skipped],
                prepared_at=datetime.now(UTC),
                dry_run=self.dry_run,
                submit_selector=form.submit_selector,
                unfilled_required=self._unfilled_required(package, form, filled, skipped),
                documents=report,
            )

    def _unfilled_required(
        self,
        package: ApplicationPackageSchema,
        form: ApplicationForm,
        filled: dict[str, str],
        skipped: list[str],
    ) -> list[str]:
        """Required fields and optional answers that could not be written safely.

        A field counts as filled only when it was written under its own name (or the
        identity key it stands for). A *question key* in `filled` proves nothing about
        another field that happens to share it. A history field the plan deliberately
        leaves blank (the end date of a current role) is covered; anything else that is
        required and untouched blocks the click.
        """

        missing: list[str] = []
        for field in form.fields:
            if field.kind == "file":
                continue
            aliases = self._filled_aliases(field)
            if not field.required:
                if field.name in skipped or any(alias in skipped for alias in aliases):
                    missing.append(describe_field(field))
                continue
            if field.name in filled or any(
                alias in filled for alias in self._filled_aliases(field)
            ):
                continue
            plan = plan_greenhouse_field(field.name, package.employment, package.education)
            if plan is not None and plan.covered and plan.action == "blank":
                continue
            missing.append(describe_field(field))
        return missing

    def _validation_fields(
        self, package: ApplicationPackageSchema, form: ApplicationForm, identity: dict[str, str]
    ) -> list[FormField]:
        """Validate required controls and every optional control with a proposed answer."""

        fields: list[FormField] = []
        for field in form.fields:
            if field.required:
                fields.append(field)
                continue
            if field.kind == "file":
                continue
            if any(identity.get(alias) for alias in self._filled_aliases(field)):
                fields.append(field)
                continue
            if self.kind == "greenhouse":
                plan = plan_greenhouse_field(field.name, package.employment, package.education)
                if plan is not None and plan.covered and plan.action != "blank":
                    fields.append(field)
                    continue
            if not self._is_structural_field(field, package):
                resolved, _refusal = self._resolve_answer(package, field)
                if resolved is not None:
                    fields.append(field)
        return fields

    def _filled_aliases(self, field: FormField) -> list[str]:
        """Other keys `filled` may hold for this field (the identity key it stands for)."""

        key = self._identity_key(field.name)
        return [key] if key else []

    def apply_to_page(
        self,
        page: FormPage,
        package: ApplicationPackageSchema,
        form: ApplicationForm,
        *,
        documents: dict[str, Path] | None = None,
    ) -> tuple[dict[str, str], list[str]]:
        """Type verified values onto an already-open page. Never clicks submit.

        `documents` are the PDFs rendered ahead of time (the real path). Without them
        (the human handoff) they are rendered here, and a failure is only noted in
        `skipped`, because a partial fill is still useful to the person finishing it.

        Every write goes through `write_value`, which knows what kind of control it is
        writing to. A value that cannot be written (a drop-down with no option that
        equals the answer, a radio group) leaves the field unfilled and named in
        `skipped`; a required one then blocks the click.
        """

        filled: dict[str, str] = {}
        skipped: list[str] = []
        if documents is None:
            documents = self._prepare_documents(package, skipped, {})

        identity = self._identity_values(package)
        for name, value in identity.items():
            if not value:
                skipped.append(name)
                continue
            field = self._identity_field(form, name)
            selector = self._existing_selector(page, IDENTITY_SELECTORS.get(name, ()))
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

        for field in form.fields:
            plan = plan_greenhouse_field(field.name, package.employment, package.education)
            if plan is None or not plan.covered or plan.action == "blank":
                continue
            selector = field.selector or (f"#{field.name}" if field.name else None)
            if not selector:
                skipped.append(field.name)
                continue
            if plan.action == "check":
                written = write_value(page, replace(field, kind="checkbox"), selector, "checked")
                value = "checked"
            elif plan.value:
                value = plan.value
                written = write_value(page, field, selector, value)
            else:
                continue
            if written:
                filled[field.name] = value
            else:
                skipped.append(field.name)

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

    def _fill_answers(
        self,
        page: FormPage,
        package: ApplicationPackageSchema,
        form: ApplicationForm,
        filled: dict[str, str],
        skipped: list[str],
    ) -> None:
        """Type the verified answer for every question the form asks.

        Each field is resolved on its own: fields the structured plan recognises
        (employment and education blocks, identity, files) are never treated as
        questions, so "when can you start?" cannot land in `start-date-month-0`. A
        question is answered by its key or, where the key is unknown, by its wording.
        """

        for field in form.fields:
            if (
                not field.selector
                or field.name in filled
                or self._is_structural_field(field, package)
            ):
                continue
            resolved, refusal = self._resolve_answer(package, field)
            if resolved is None:
                if refusal:
                    skipped.append(field.name)
                continue
            answer_key, value = resolved
            if write_value(page, field, field.selector, value):
                filled[field.name] = value
                # The answer's own key is recorded as well, for the audit trail. Whether a
                # field was filled is decided by the field's own name, never by this key.
                if not any(other.name == answer_key for other in form.fields):
                    filled.setdefault(answer_key, value)
            else:
                skipped.append(field.name)

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

        for field in self._validation_fields(package, form, identity):
            if field.kind == "file":
                if (
                    "resume" in field.name.lower()
                    and not (package.tailored_resume_markdown or "").strip()
                ):
                    unknown_required.append(
                        f"{describe_field(field)}: no tailored résumé is available to upload"
                    )
                elif (
                    package.tailored_resume_markdown or ""
                ).strip() and "resume" in field.name.lower():
                    mapped[field.name] = "resume"
                continue
            # Structural identity first. The captured form labels its employment-block
            # date inputs "Start date month" / "Start date year", which the question
            # catalogue classifies as the "when can you start?" question — a false
            # blocker, because the field belongs to a known repeating block rather
            # than to a board question.
            if self._is_identity_field(field):
                key = self._identity_key(field.name)
                value = identity.get(key) if key else None
                if not value:
                    unknown_required.append(f"{describe_field(field)}: no verified candidate value")
                    continue
                problem = option_problem(field, value)
                if problem:
                    unknown_required.append(f"{describe_field(field)}: {problem}")
                    continue
                mapped[field.name] = "identity"
                continue
            plan = plan_greenhouse_field(field.name, package.employment, package.education)
            if plan is not None:
                if not plan.covered:
                    unknown_required.append(plan.reason or describe_field(field))
                    continue
                problem = (
                    option_problem(field, plan.value)
                    if plan.action == "fill" and plan.value
                    else None
                )
                if problem:
                    unknown_required.append(f"{describe_field(field)}: {problem}")
                    continue
                mapped[field.name] = plan.section
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
                "refusing to submit: fields could not be completed safely: "
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
        confirmation = None
        try:
            page.click(submit_selector)
            page.wait(settle_ms)
            confirmation = detect_confirmation(page, self.kind, before)
            after_url = confirmation.url or prepared.url
            after_title = page.title()
            if capture_evidence:
                screenshot = self._capture(page, "after_submit")
                if screenshot:
                    evidence.append(screenshot)
        except Exception as exc:  # noqa: BLE001 - the click may have gone through
            if confirmation is not None and confirmation.confirmed:
                return SubmissionReceipt(
                    submitted=True,
                    clicked=True,
                    method=SubmissionMethod.PLAYWRIGHT_ADAPTER,
                    external_confirmation={
                        "url": confirmation.url or prepared.url,
                        "confirmation_detected": True,
                        "confirmation_reason": confirmation.reason,
                        "confirmation_marker": confirmation.marker,
                        "form_url": prepared.url,
                        "metadata_read_error": str(exc)[:300],
                    },
                    confirmation_text=confirmation.text or confirmation.reason,
                    evidence_paths=evidence,
                    submitted_at=datetime.now(UTC),
                    dry_run=False,
                )
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
            autocomplete=entry.get("autocomplete") or None,
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

    def _resolve_answer(
        self, package: ApplicationPackageSchema, field: FormField
    ) -> tuple[tuple[str, str] | None, str | None]:
        """`((answer key, verified text), refusal)` for this field's question.

        A verified package answer wins: by the question's wording, else by question key
        (see `match_answer` for when a key match is refused). Failing that, a recognised
        key may be answered from a verified package field such as the city or the current
        employer. Nothing is invented. A refusal is returned only when nothing else
        answers the field.
        """

        own_key = self._own_field_key(field)
        key = own_key or field.question_key or classify_question(field.label or "")
        match = match_answer(
            package, field, question_key=key, key_is_trusted=own_key is not None and own_key == key
        )
        if match.answer is not None:
            return (match.answer.question_key, str(match.answer.answer)), None
        if key:
            bridged = self._bridge_value(package, key)
            if bridged:
                if own_key == key:
                    return (key, bridged), None
                # The catalogue recognised a phrase, not a location input: "What city would
                # you like to work in?" is not the candidate's home city. Wording that was
                # recorded with an answer is handled above (`match_answer`).
                return None, (
                    f"the form's question {(field.label or field.name)!r} only resembles "
                    f"{key!r}, and this field is not recognisable as that input by its id; "
                    "the candidate's value is typed only when the question wording was "
                    "recorded with an answer (record the answer again with the exact "
                    "question wording and verify it)"
                )
        return None, match.refused

    @staticmethod
    def _own_field_key(field: FormField) -> str | None:
        """The answer key a control declares by its own id, name or autocomplete attribute.

        This is structure, not wording: a `city` input or `candidate-location` field holds
        the city whatever its label says. An id that is really a label (it has spaces, as
        when a control had no id and was named from its text) never counts.
        """

        autocomplete = (field.autocomplete or "").strip().lower()
        by_autocomplete = {
            "address-level2": "city",
            "address-level1": "state_residence",
            "postal-code": "postal_code",
            "organization": "current_employer",
        }
        if autocomplete in by_autocomplete:
            return by_autocomplete[autocomplete]
        name = (field.name or "").strip().lower()
        if not name or any(ch.isspace() for ch in name):
            return None
        if name in {"city", "candidate-location", "candidate_location", "location-city"}:
            return "city"
        if name in {"state", "state_residence", "province"}:
            return "state_residence"
        if name in {"zip", "zipcode", "zip_code", "postal_code", "postal-code", "postcode"}:
            return "postal_code"
        if name in {"employer", "current_employer", "current-employer", "current_company"}:
            return "current_employer"
        if "linkedin" in name:
            return "linkedin"
        return None

    def _classify_question(
        self,
        package: ApplicationPackageSchema,
        field: FormField,
        mapped: dict[str, str],
        unknown_required: list[str],
        unanswered: list[str],
    ) -> None:
        """Validation of one required question field against verified answers."""

        key = (
            self._own_field_key(field) or field.question_key or classify_question(field.label or "")
        )
        resolved, refusal = self._resolve_answer(package, field)
        if resolved is not None:
            problem = option_problem(field, resolved[1])
            if problem:
                unknown_required.append(f"{describe_field(field)}: {problem}")
            else:
                mapped[field.name] = key or "answer"
            return
        if refusal:
            unknown_required.append(f"{describe_field(field)}: {refusal}")
        elif key:
            unanswered.append(field.label or field.name)
        else:
            unknown_required.append(describe_field(field))

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
        verified (`level` VERIFIED and `not requires_user_input`). Nothing is parsed out of the résumé text
        and nothing is inferred: a name, an e-mail address or a country that is not on
        the package is simply absent, and validation then blocks on the field.
        """

        values: dict[str, str] = {}
        if package.applicant_name:
            first, last = split_name(package.applicant_name)
            if first:
                values["first_name"] = first
            if last:
                values["last_name"] = last
        # A structured name on the package is the owner's own split and wins.
        if package.applicant_first_name:
            values["first_name"] = package.applicant_first_name
        if package.applicant_last_name:
            values["last_name"] = package.applicant_last_name
        if package.applicant_email:
            values["email"] = package.applicant_email
        if package.applicant_phone:
            values["phone"] = package.applicant_phone
        if package.applicant_country:
            values["country"] = package.applicant_country
        if package.applicant_street_address:
            values["street_address"] = package.applicant_street_address
        for answer in package.answers:
            if not is_typable(answer):
                continue
            key = answer.question_key
            if key in {"first_name", "last_name", "email", "phone", "country", "preferred_name"}:
                # A structured name on the package is the owner's own and is not
                # overridden by a memory answer.
                if key == "first_name" and package.applicant_first_name:
                    continue
                if key == "last_name" and package.applicant_last_name:
                    continue
                if key in {"email", "phone", "country"} and getattr(package, f"applicant_{key}"):
                    continue
                values[key] = str(answer.answer)
        return values

    def _identity_field(self, form: ApplicationForm, name: str) -> FormField | None:
        """The form field that stands for identity value `name`, if the form has one."""

        for field in form.fields:
            if field.kind != "file" and self._identity_key(field.name) == name:
                return field
        return None

    def _is_structural_field(self, field: FormField, package: ApplicationPackageSchema) -> bool:
        """A field that belongs to a known section, never to a board question.

        File inputs, identity inputs and the employment / education blocks (including
        the ones the candidate has no entry for) are filled from structured data or not
        at all. Matching a question's answer to one of them is how a verified start
        date once landed in the employment block's start-month input.
        """

        if field.kind == "file":
            return True
        if plan_greenhouse_field(field.name, package.employment, package.education) is not None:
            return True
        return self._identity_key(field.name) is not None

    @staticmethod
    def _is_identity_field(field: FormField) -> bool:
        return GreenhouseApplicationAdapter._identity_key(field.name) is not None

    def _artifact_dir(self, package: ApplicationPackageSchema) -> Path:
        """Where this application's documents are written: the evidence dir if one was
        given, else the settings' artifacts dir (never a path relative to the cwd)."""

        if self._evidence_dir is not None:
            base = Path(self._evidence_dir)
        else:
            configured = getattr(self.settings, "artifacts_dir", None)
            if configured is None:
                from sabra_domain.settings import get_settings

                configured = get_settings().artifacts_dir
            base = Path(configured)
        return base / "applications" / package.opportunity_id

    def _document_stem(self, package: ApplicationPackageSchema) -> str:
        """`First-Last` for the uploaded file's name, from the verified name only.

        Accents are stripped first ("José García" is "Jose-Garcia"), so a name is never
        reduced to the letters left after dropping what is not ASCII.
        """

        values = self._identity_values(package)
        words: list[str] = []
        for key in ("first_name", "last_name"):
            for token in (values.get(key) or "").split():
                plain = "".join(
                    c for c in unicodedata.normalize("NFKD", token) if not unicodedata.combining(c)
                )
                cleaned = re.sub(r"[^A-Za-z0-9]+", "", plain)
                if cleaned:
                    words.append(cleaned.title() if cleaned.isupper() else cleaned)
        return "-".join(words) or "Candidate"

    @staticmethod
    def _document_key(package: ApplicationPackageSchema) -> tuple[Any, ...]:
        return (
            package.opportunity_id,
            package.applicant_name,
            package.applicant_first_name,
            package.applicant_last_name,
            package.tailored_resume_markdown,
            package.cover_letter,
        )

    def prepare_documents(self, package: ApplicationPackageSchema) -> dict[str, Path]:
        """Render the PDFs now, before any browser or page exists.

        Called by the execution service ahead of opening the session, so a document that
        cannot be produced blocks the application before the employer's site is touched.
        Raises `SafetyStop` (UNSUPPORTED_FILE_REQUIREMENT) on failure. The result is
        cached for `prepare`.
        """

        notes: list[str] = []
        report: dict[str, Any] = {}
        documents = self._prepare_documents(package, notes, report)
        failures = [item for item in notes if item.startswith(DOCUMENT_ERROR)]
        if failures:
            raise SafetyStop(
                SafetyStopKind.UNSUPPORTED_FILE_REQUIREMENT,
                "; ".join(failures),
                {"documents": failures},
            )
        self._rendered = (self._document_key(package), documents, notes, report)
        return documents

    def _documents_for(
        self, package: ApplicationPackageSchema
    ) -> tuple[dict[str, Path], list[str], dict[str, Any]]:
        cached = getattr(self, "_rendered", None)
        if cached is None or cached[0] != self._document_key(package):
            self.prepare_documents(package)
            cached = self._rendered
        return cached[1], list(cached[2]), dict(cached[3])

    def _prepare_documents(
        self,
        package: ApplicationPackageSchema,
        skipped: list[str],
        report: dict[str, Any],
    ) -> dict[str, Path]:
        """Write the owner's Markdown copies and the PDFs that will be uploaded.

        The PDFs are rendered here, at submit time, from the approved package's own
        Markdown, so the file an employer receives is bound to the approval hash by
        construction. Markdown is never uploaded: an employer would see `#` and `**`.
        Any failure (the render, or a file the OS will not let us write) is recorded in
        `skipped` prefixed with `DOCUMENT_ERROR` and nothing is uploaded in its place.
        `report` receives the sha256 of each PDF, the number of characters that had no
        safe mapping, and the renderer version, for the attempt event.
        """

        from sabra_documents import render as renderer

        stem = self._document_stem(package)
        who = package.applicant_name or "Candidate"
        documents: dict[str, Path] = {}
        for key, text, md_name, pdf_name, label in (
            (
                "resume",
                package.tailored_resume_markdown,
                "tailored_resume.md",
                f"{stem}-Resume.pdf",
                "Resume",
            ),
            (
                "cover_letter",
                package.cover_letter,
                "cover_letter.md",
                f"{stem}-Cover-Letter.pdf",
                "Cover Letter",
            ),
        ):
            if not (text or "").strip():
                if key == "resume":
                    skipped.append("resume upload: no tailored resume is available")
                continue
            try:
                base = self._artifact_dir(package)
                base.mkdir(parents=True, exist_ok=True)
                (base / md_name).write_text(text or "", encoding="utf-8")
                pdf = renderer.render_resume_pdf(text or "", title=f"{who} - {label}")
                path = base / pdf_name
                path.write_bytes(pdf)
                report[key] = {
                    "file": pdf_name,
                    "sha256": hashlib.sha256(pdf).hexdigest(),
                    "bytes": len(pdf),
                    "unmapped_characters": renderer.count_unmapped(text or ""),
                }
            except Exception as exc:  # noqa: BLE001 - reported, and nothing else is uploaded
                skipped.append(
                    f"{DOCUMENT_ERROR} the {label.lower()} PDF could not be produced "
                    f"({type(exc).__name__}: {exc}); no file was uploaded in its place"
                )
                continue
            documents[key] = path
        if report:
            report["renderer"] = renderer.renderer_version()
        return documents

    def _upload_documents(
        self,
        page: FormPage,
        form: ApplicationForm,
        package: ApplicationPackageSchema,
        documents: dict[str, Path],
        filled: dict[str, str],
        skipped: list[str],
    ) -> None:
        """Upload the rendered PDFs when the form accepts files."""

        for key, label in (("resume", "resume"), ("cover_letter", "cover letter")):
            path = documents.get(key)
            if path is None:
                continue
            selector = self._existing_selector(page, IDENTITY_SELECTORS[key])
            if selector:
                page.set_input_files(selector, str(path))
                filled[key] = str(path)
            else:
                skipped.append(f"{label} upload not available on this form")

    def _capture(self, page: FormPage, label: str) -> str | None:
        base = Path(
            getattr(self.settings, "automation_screenshot_dir", None) or Path("var/automation")
        )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return page.screenshot(str(base / f"{stamp}_{label}.png"))
