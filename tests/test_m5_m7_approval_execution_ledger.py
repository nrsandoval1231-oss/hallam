"""M5 approval boundary, M6 execution adapter, M7 ledger and outcomes.

The tests that matter most here are the refusal paths:

* an unapproved application must not reach a submission, and must not be recorded as
  one;
* an approval must not survive the package changing underneath it;
* a CAPTCHA must stop the attempt rather than being worked around.

`FakeFormPage` substitutes for Playwright and is driven by the *real* field inventory
captured from a live Greenhouse board (`tests/fixtures/greenhouse_application_form.json`),
so the adapter's field handling is exercised against the shape Greenhouse actually
serves.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest_app import build_job
from sabra_automation.base import (
    ApplicationForm,
    PreparedSubmission,
    SourceNotSupported,
)
from sabra_automation.greenhouse_adapter import (
    ENUMERATE_FIELDS_JS,
    GreenhouseApplicationAdapter,
)
from sabra_automation.playwright_driver import looks_like_bot_wall, looks_like_login_wall
from sabra_domain import models
from sabra_domain.enums import (
    ApplicationStatus,
    JobStatus,
    OutcomeKind,
    PackageStatus,
    SafetyStopKind,
    SubmissionMethod,
)
from sabra_domain.errors import (
    ApprovalRequired,
    ApprovalScopeMismatch,
    SafetyStop,
)
from sabra_engine.approval import ApprovalService
from sabra_engine.execution import AdapterRegistry, ExecutionService
from sabra_engine.ledger import LedgerService

# ---------------------------------------------------------------------------
# Test double for the browser
# ---------------------------------------------------------------------------


class FakeFormPage:
    """A `FormPage` driven by the real captured field inventory."""

    def __init__(
        self,
        capture: dict,
        *,
        title: str | None = None,
        body: str | None = None,
        captcha: bool | None = None,
        submit_selector: str | None = "button[type='submit']",
        confirm_after_submit: bool = True,
    ) -> None:
        self.capture = capture
        self._title = title or capture.get("title") or "Job Application"
        self._body = body if body is not None else "Apply for this job"
        self._captcha = capture.get("captcha", False) if captcha is None else captcha
        self._submit_selector = submit_selector
        self._confirm = confirm_after_submit
        self._url = "https://job-boards.greenhouse.io/revance/jobs/9001"
        self.filled: dict[str, str] = {}
        self.files: dict[str, str] = {}
        self.clicked: list[str] = []
        self.screenshots: list[str] = []
        self.closed = False

    # -- FormPage ------------------------------------------------------

    def open(self, url: str) -> None:
        self._url = url

    def title(self) -> str:
        return self._title

    def current_url(self) -> str:
        return self._url

    def evaluate(self, script: str) -> Any:
        if script.strip() == ENUMERATE_FIELDS_JS.strip():
            return [
                {
                    "id": field.get("id"),
                    "tag": field.get("tag"),
                    "type": field.get("type"),
                    "required": field.get("required"),
                    "label": field.get("label"),
                    "placeholder": field.get("placeholder"),
                    "value": field.get("value"),
                    "options": field.get("options") or [],
                }
                for field in self.capture["fields"]
            ]
        if ".some(s => !!document.querySelector(s))" in script:
            return self._captcha
        if ".length" in script:
            selector = script.split("querySelectorAll(")[-1].split(")")[0].strip().strip("'\"")
            return self._count(selector)
        if "querySelectorAll" in script:
            return self._count(
                script.split("querySelectorAll(")[-1].split(")")[0].strip().strip("'\"")
            )
        return None

    def _count(self, selector: str) -> int:
        # Normalize quote style: the adapter builds selectors in either style and the
        # real DOM does not care, but a naive comparison does.
        normalized = selector.replace("'", '"')
        if normalized.replace("'", '"') == (self._submit_selector or "").replace("'", '"'):
            return 1
        if selector.startswith("#"):
            identifier = selector[1:]
            return sum(1 for f in self.capture["fields"] if f.get("id") == identifier)
        if "type=" in normalized and "file" in normalized:
            return sum(1 for f in self.capture["fields"] if f.get("type") == "file")
        if "recaptcha" in selector or "captcha" in selector or "sitekey" in selector:
            return 1 if self._captcha else 0
        if selector.startswith('input[name="'):
            name = selector.split('"')[1]
            return sum(1 for f in self.capture["fields"] if f.get("id") == name)
        return 0

    def body_text(self) -> str:
        return self._body

    def fill(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    def select_option(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    def check(self, selector: str) -> None:
        self.filled[selector] = "checked"

    def set_input_files(self, selector: str, path: str) -> None:
        self.files[selector] = path

    def click(self, selector: str) -> None:
        self.clicked.append(selector)

    def wait(self, milliseconds: int) -> None:
        pass

    def screenshot(self, path: str) -> str | None:
        self.screenshots.append(path)
        return path

    def close(self) -> None:
        self.closed = True


def make_adapter(
    capture: dict, **page_kwargs: Any
) -> tuple[GreenhouseApplicationAdapter, FakeFormPage, list]:
    """Adapter whose page factory yields one scripted fake page per call."""

    pages: list[FakeFormPage] = []

    def factory(_url: str) -> FakeFormPage:
        page = FakeFormPage(capture, **page_kwargs)
        pages.append(page)
        return page

    adapter = GreenhouseApplicationAdapter(dry_run=True, page_factory=factory)
    return adapter, pages, pages  # `pages` is populated lazily, so it is the same list


# ---------------------------------------------------------------------------
# M6: bot protection and inspection
# ---------------------------------------------------------------------------


def test_bot_wall_detection_matches_the_real_interstitial() -> None:
    """Measured live: one board served "Vercel Security Checkpoint" instead of a form."""

    assert looks_like_bot_wall("Vercel Security Checkpoint", "")
    assert looks_like_bot_wall("Just a moment...", "")
    assert not looks_like_bot_wall(
        "Job Application for Clinical Field Specialist at Natera", "Apply"
    )


def test_login_wall_detection() -> None:
    assert looks_like_login_wall("Please sign in to continue")
    assert not looks_like_login_wall("Apply for this job")


def test_supports_greenhouse_postings_only() -> None:
    adapter = GreenhouseApplicationAdapter(dry_run=True)
    assert adapter.supports(build_job())
    # A Lever posting is not this adapter's workflow, even if a wrong board_token
    # happens to be attached.
    assert not adapter.supports(
        build_job(source="LEVER", source_url="https://jobs.lever.co/acme/1")
    )
    assert not adapter.supports(
        build_job(source="LEVER", source_url="https://jobs.lever.co/acme/1", board_token=None)
    )


def test_inspect_stops_on_a_bot_wall() -> None:
    adapter, pages, _ = make_adapter({}, title="Vercel Security Checkpoint", body="")
    with pytest.raises(SafetyStop) as excinfo:
        adapter.inspect(build_job())
    assert excinfo.value.kind == SafetyStopKind.CAPTCHA


def test_inspect_enumerates_the_real_form(greenhouse_form_capture: dict) -> None:
    adapter, pages, _ = make_adapter(greenhouse_form_capture)
    form = adapter.inspect(build_job())
    assert form.url.endswith("/revance/jobs/9001")
    # The captured board served 51 fields and a CAPTCHA.
    assert len(form.fields) == len(greenhouse_form_capture["fields"]) == 51
    assert form.has_captcha is True
    assert form.submit_selector == 'button[type="submit"]'
    names = {f.name for f in form.fields}
    assert {"first_name", "last_name", "email", "phone", "resume", "cover_letter"} <= names
    assert any(name.startswith("question_") for name in names)
    assert form.required_fields
    assert any(f.kind == "file" for f in form.fields)


def test_inspect_labels_are_classified_into_question_keys(greenhouse_form_capture: dict) -> None:
    adapter, _pages, _ = make_adapter(greenhouse_form_capture)
    form = adapter.inspect(build_job())
    keys = {f.question_key for f in form.fields if f.question_key}
    assert "work_authorization" in keys
    assert "sponsorship" in keys


def test_captcha_is_always_a_stop(greenhouse_form_capture: dict) -> None:
    """Requirement 21: a CAPTCHA means stop; it does not mean find a way round."""

    adapter, _pages, _ = make_adapter(greenhouse_form_capture)
    form = adapter.inspect(build_job())
    stops = adapter.detect_stops(form, _package_stub())
    assert any(stop.kind == SafetyStopKind.CAPTCHA for stop in stops)


def test_legal_attestations_are_stops() -> None:
    adapter = GreenhouseApplicationAdapter(dry_run=True)
    form = ApplicationForm(
        url="https://example.test",
        fields=[],
        attestations=["I certify that the information provided is accurate"],
    )
    stops = adapter.detect_stops(form, _package_stub())
    assert any(stop.kind == SafetyStopKind.LEGAL_ATTESTATION for stop in stops)


def test_no_adapter_for_a_posting_is_reported_not_guessed() -> None:
    registry = AdapterRegistry([])
    with pytest.raises(SourceNotSupported):
        registry.for_job(build_job())


# ---------------------------------------------------------------------------
# M6: validation and preparation
# ---------------------------------------------------------------------------


def _package_stub(**overrides: Any):
    from sabra_domain.schemas import ApplicationAnswerSchema, ApplicationPackageSchema

    base = dict(
        opportunity_id="opp_1",
        candidate_id="cand_1",
        job_id="job_1",
        master_resume_id="res_master",
        tailored_resume_markdown="# JORDAN A. RIVERA\njordan.rivera@example.test | Dallas, TX\n",
        cover_letter="Dear Hiring Team,",
        answers=[
            ApplicationAnswerSchema(
                question_key="work_authorization", question_text="Authorized?", answer="Yes"
            ),
            ApplicationAnswerSchema(
                question_key="sponsorship",
                question_text="Sponsorship?",
                answer=None,
                requires_user_input=True,
            ),
        ],
        content_hash="package-hash",
    )
    base.update(overrides)
    return ApplicationPackageSchema(**base)


def test_validate_requires_input_for_unanswered_required_questions(
    greenhouse_form_capture: dict,
) -> None:
    """The captured form has required questions no candidate memory can answer yet."""

    adapter, _pages, _ = make_adapter(greenhouse_form_capture)
    form = adapter.inspect(build_job())
    report = adapter.validate(_package_stub(), form)
    assert not report.ok
    kinds = {str(stop.kind) for stop in report.stops}
    assert SafetyStopKind.CAPTCHA in kinds
    assert SafetyStopKind.UNKNOWN_REQUIRED_QUESTION in kinds
    assert report.unknown_required_fields or report.unanswered_questions
    # Every required question is accounted for: mapped, answered, or explicitly named.
    assert report.mapped_fields


def test_validate_reports_clean_when_every_required_field_is_known() -> None:
    adapter = GreenhouseApplicationAdapter(dry_run=True)
    form = ApplicationForm(
        url="https://example.test",
        fields=[],
        has_captcha=False,
        submit_selector="button[type='submit']",
    )
    report = adapter.validate(_package_stub(), form)
    assert report.ok
    assert report.stops == []


def test_prepare_fills_identity_from_the_package_and_never_guesses(
    greenhouse_form_capture: dict,
) -> None:
    adapter, _pages, _ = make_adapter(greenhouse_form_capture)
    form = adapter.inspect(build_job())
    prepared = adapter.prepare(_package_stub(), form)
    assert prepared.dry_run is True
    assert prepared.filled["first_name"] == "JORDAN"
    assert prepared.filled["email"] == "jordan.rivera@example.test"
    # The sponsorship answer requires user input, so it must not be typed.
    assert "sponsorship" not in prepared.filled
    assert prepared.files, "a résumé file should have been attached"
    # Every required field the adapter could not fill is named, by its own field id.
    assert any(item.startswith("question_") for item in prepared.skipped), prepared.skipped


def test_prepare_submits_nothing(greenhouse_form_capture: dict) -> None:
    adapter, pages, _ = make_adapter(greenhouse_form_capture)
    form = adapter.inspect(build_job())
    adapter.prepare(_package_stub(), form)
    assert all(page.clicked == [] for page in pages)


def test_submit_is_refused_in_dry_run(greenhouse_form_capture: dict) -> None:
    adapter, _pages, _ = make_adapter(greenhouse_form_capture)
    with pytest.raises(ApprovalRequired):
        adapter.submit(PreparedSubmission(url="https://example.test", filled={"#email": "a@b.c"}))


def test_submit_refuses_an_empty_form(greenhouse_form_capture: dict) -> None:
    adapter, _pages, _ = make_adapter(greenhouse_form_capture)
    adapter.dry_run = False
    with pytest.raises(SafetyStop):
        adapter.submit(PreparedSubmission(url="https://example.test"))


def test_submit_requires_confirmation_to_claim_success(greenhouse_form_capture: dict) -> None:
    """A click that produces no confirmation must not be recorded as submitted."""

    adapter, pages, _ = make_adapter(greenhouse_form_capture, confirm_after_submit=False)
    adapter.dry_run = False
    prepared = PreparedSubmission(url="https://example.test", filled={"#email": "a@b.c"})

    # The fake returns a generic page after the click, so no confirmation is found.
    pages.clear()
    receipt = adapter.submit(prepared)
    assert receipt.submitted is False
    assert receipt.submitted_at is None
    assert receipt.error and "NOT recorded as submitted" in receipt.error


# ---------------------------------------------------------------------------
# M5: approval boundary
# ---------------------------------------------------------------------------


def test_preparation_reaches_awaiting_approval(prepared) -> None:
    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        opportunity = repos.opportunities.get(prepared.opportunity_id)
        package_row = repos.packages.get(prepared.package_id)
    assert application.status == ApplicationStatus.AWAITING_APPROVAL.value
    assert opportunity.status == JobStatus.AWAITING_APPROVAL.value
    assert package_row.status == PackageStatus.INCOMPLETE.value, (
        "the placeholder-free fixture still has unanswered posted questions, so the "
        "package is honestly incomplete rather than silently complete"
    )
    assert prepared.preparation.complete is False or prepared.preparation.unresolved_questions == []


def test_unapproved_application_cannot_be_submitted(prepared) -> None:
    service = ExecutionService(prepared.ctx)
    result = service.submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "BLOCKED"
    assert "approved" in (result.detail or "").lower()

    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        assert application.status != ApplicationStatus.APPLIED.value
        assert application.submission_time is None


def test_approval_requires_acknowledging_unresolved_questions(prepared) -> None:
    service = ApprovalService(prepared.ctx)
    if prepared.preparation.unresolved_questions:
        with pytest.raises(ApprovalRequired):
            service.approve(prepared.package_id, approved_by="test")
        outcome = service.approve(
            prepared.package_id, approved_by="test", acknowledge_unresolved=True
        )
    else:
        outcome = service.approve(prepared.package_id, approved_by="test")
    assert outcome.approved_package_hash == prepared.package_hash
    assert outcome.approved_by == "test"


def test_approval_binds_to_the_exact_package_hash(prepared) -> None:
    service = ApprovalService(prepared.ctx)
    with pytest.raises(ApprovalScopeMismatch):
        service.approve(
            prepared.package_id,
            approved_by="test",
            expected_hash="not-the-hash",
            acknowledge_unresolved=True,
        )


def test_approval_does_not_survive_a_package_change(prepared) -> None:
    """The core property of the irreversible boundary."""

    approval = ApprovalService(prepared.ctx)
    approval.approve(prepared.package_id, approved_by="test", acknowledge_unresolved=True)
    # The package content changes after approval (e.g. it was regenerated).
    with prepared.ctx.repos() as repos:
        package_row = repos.packages.get(prepared.package_id)
        payload = dict(package_row.payload)
        payload["cover_letter"] = (payload.get("cover_letter") or "") + "\nEdited after approval."
        package_row.payload = payload
        from sabra_domain.identity import content_hash
        from sabra_domain.schemas import ApplicationPackageSchema

        schema = ApplicationPackageSchema.model_validate(payload)
        package_row.content_hash = content_hash(schema.semantic_payload())
        repos.session.flush()

    with pytest.raises(ApprovalScopeMismatch):
        approval.assert_submission_authorised(prepared.application_id)


def test_approved_application_reaches_dry_run_and_does_not_claim_submission(
    prepared, greenhouse_form_capture: dict
) -> None:
    """The dry-run path, exercised offline against the captured real form.

    This test previously used a live adapter against a fixture job URL that does not
    exist. It passed for the wrong reason: the 404 page enumerated zero fields, a form
    with no required fields validated as ok, and the run reported DRY_RUN without ever
    having been on a real form. A stub page over the real captured inventory makes the
    test both offline and meaningful.
    """

    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )
    adapter = GreenhouseApplicationAdapter(
        dry_run=True,
        page_factory=lambda _url: FakeFormPage(
            answerable_capture(greenhouse_form_capture), captcha=False
        ),
    )
    result = ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter])).submit(
        prepared.application_id, allow_real_submission=False
    )
    assert result.outcome == "DRY_RUN", f"{result.outcome}: {result.detail} {result.stops}"
    assert result.receipt is None

    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        assert application.status == ApplicationStatus.APPROVED.value
        assert application.submission_time is None


def answerable_capture(capture: dict, *, answerable_keys: set[str] | None = None) -> dict:
    """The captured form reduced to the fields this test can actually answer.

    Derived from the real inventory rather than invented: the real Natera form asks
    board-specific required questions ("Have you previously worked at Natera?") that
    no memory can answer, so a submission-path test needs a form whose required
    questions are all resolvable. Everything the adapter recognizes as a known
    section (identity, employment, education) is kept.
    """

    from sabra_documents.answers import classify_question

    answerable = answerable_keys or {"work_authorization"}
    keep = []
    for field in capture["fields"]:
        identifier = (field.get("id") or "").lower()
        label = field.get("label") or ""
        if identifier.startswith("question_"):
            if classify_question(label) in answerable:
                keep.append(field)
            continue
        # Keep identity/file/employment/education fields, drop the phone-country
        # widget and any unnamed inputs the enumerator could not identify.
        if identifier and not identifier.startswith("iti-"):
            keep.append(field)
    return {**capture, "fields": keep, "captcha": False}


def test_global_dry_run_lock_overrides_a_per_call_opt_in(
    prepared, greenhouse_form_capture: dict
) -> None:
    """The settings switch is a floor, not a default: it cannot be opted out of
    from a call site, so an operator's script cannot accidentally submit."""

    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )
    assert prepared.ctx.settings.automation_dry_run is True
    # A captcha-free, fully answerable form, so the dry-run path is what this test
    # exercises rather than the safety stop a real Greenhouse form would produce.
    capture = answerable_capture(greenhouse_form_capture)
    adapter = GreenhouseApplicationAdapter(
        dry_run=False, page_factory=lambda _url: FakeFormPage(capture, captcha=False)
    )
    result = ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter])).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "DRY_RUN", (
        "settings.automation_dry_run=True must keep the submission dry even when the "
        f"caller passes allow_real_submission=True (got {result.outcome}: "
        f"{result.detail}; stops={result.stops})"
    )


def test_real_submission_on_a_captcha_form_is_blocked_not_bypassed(prepared) -> None:
    """Requirement 21, end to end: the approved attempt stops at the CAPTCHA."""

    # Lift the global dry-run floor for this test: without it no real path is
    # reachable at all, and the CAPTCHA stop would never be exercised.
    prepared.ctx.settings.automation_dry_run = False
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )
    adapter = GreenhouseApplicationAdapter(
        dry_run=False,
        page_factory=lambda _url: FakeFormPage(
            {"fields": [], "captcha": True, "title": "Vercel Security Checkpoint"}, body=""
        ),
    )
    result = ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter])).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "BLOCKED"
    assert any("CAPTCHA" in stop["kind"] for stop in result.stops)

    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        assert application.status == ApplicationStatus.BLOCKED.value
        assert application.submission_time is None
        assert application.external_confirmation == {}
        events = [
            e.event_type for e in repos.events.for_entity("application", prepared.application_id)
        ]
        assert "APPLICATION_SUBMITTED" not in events
        assert "APPLICATION_BLOCKED" in events


def test_a_consent_overlay_is_not_mistaken_for_an_application_form() -> None:
    """Measured live: a board served a cookie-consent overlay whose only field was the
    consent widget. A consent page has no required fields, so treating it as the form
    made validation return ok=True and let the service proceed toward submission."""

    consent_only = {
        "title": "Job Search | Revance",
        "captcha": False,
        "fields": [
            {
                "id": "ckyCCPAOptOut",
                "tag": "input",
                "type": "checkbox",
                "required": False,
                "label": "Do not sell my personal information",
                "placeholder": None,
                "value": None,
                "options": [],
            }
        ],
    }
    adapter, _pages, _ = make_adapter(consent_only)
    with pytest.raises(SafetyStop) as excinfo:
        adapter.inspect(build_job())
    assert excinfo.value.kind is SafetyStopKind.MATERIAL_AMBIGUITY
    assert "consent" in excinfo.value.message


def test_form_confirmation_requires_two_signals() -> None:
    from sabra_automation.base import FormField
    from sabra_automation.greenhouse_adapter import looks_like_application_form

    identity = [
        FormField(name="first_name"),
        FormField(name="email"),
    ]
    # No submit control: not a form, whatever is on the page.
    assert not looks_like_application_form("Job Application for X at Y", identity, None)
    # Title signal alone is enough.
    assert looks_like_application_form("Job Application for X at Y", [], 'button[type="submit"]')
    # Or two identity inputs.
    assert looks_like_application_form("Natera", identity, 'button[type="submit"]')
    # One identity input and no title signal is not enough.
    assert not looks_like_application_form(
        "Careers", [FormField(name="first_name")], 'button[type="submit"]'
    )


def test_application_path_derives_the_legal_route_from_the_graph() -> None:
    """The bug this prevents: APPROVED -> APPLIED is illegal, and writing it by hand
    raised *after* the employer had already received the application."""

    from sabra_domain.state import APPLICATION_TRANSITIONS, application_path

    assert application_path(ApplicationStatus.APPROVED, ApplicationStatus.APPLIED) == [
        ApplicationStatus.APPLYING,
        ApplicationStatus.APPLIED,
    ]
    assert application_path(ApplicationStatus.APPLIED, ApplicationStatus.APPLIED) == []
    assert application_path(ApplicationStatus.AWAITING_APPROVAL, ApplicationStatus.APPROVED) == [
        ApplicationStatus.APPROVED
    ]

    # Every derived path must itself be a chain of legal edges.
    for start in ApplicationStatus:
        for target in ApplicationStatus:
            try:
                path = application_path(start, target)
            except Exception:
                continue
            node = start
            for step in path:
                assert step in APPLICATION_TRANSITIONS[node], (
                    f"{node} -> {step} is not a legal edge"
                )
                node = step
            assert node is target

    # An unreachable target is refused rather than silently produced.
    import pytest as _pytest
    from sabra_domain.errors import InvalidTransition

    with _pytest.raises(InvalidTransition):
        application_path(ApplicationStatus.WITHDRAWN, ApplicationStatus.APPLIED)


def test_a_confirmed_submission_is_recorded_with_its_confirmation(
    prepared, greenhouse_form_capture: dict
) -> None:
    prepared.ctx.settings.automation_dry_run = False
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )

    class ConfirmingPage(FakeFormPage):
        def body_text(self) -> str:
            return "Thank you! Your application has been submitted."

    adapter = GreenhouseApplicationAdapter(
        dry_run=False,
        page_factory=lambda _url: ConfirmingPage(
            answerable_capture(greenhouse_form_capture), captcha=False
        ),
    )
    result = ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter])).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "SUBMITTED"
    assert result.receipt is not None and result.receipt.submitted is True

    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        assert application.status == ApplicationStatus.APPLIED.value
        assert application.submission_time is not None
        assert application.submission_method == str(SubmissionMethod.PLAYWRIGHT_ADAPTER)
        opportunity = repos.opportunities.get(prepared.opportunity_id)
        assert opportunity.status == JobStatus.APPLIED.value
        ledger = LedgerService(prepared.ctx).application_ledger(prepared.application_id)
        assert ledger.status == ApplicationStatus.APPLIED.value
        assert any(e["event_type"] == "APPLICATION_SUBMITTED" for e in ledger.events)
        assert ledger.external_confirmation


def test_duplicate_application_is_structurally_impossible(prepared) -> None:
    """Requirement 9: enforced by a unique constraint, not by a check."""

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), prepared.ctx.repos() as repos:
        repos.session.add(
            models.ApplicationRow(
                opportunity_id=prepared.opportunity_id,
                package_id=prepared.package_id,
                job_id=prepared.job_id,
                candidate_id=prepared.candidate_id,
            )
        )


# ---------------------------------------------------------------------------
# M7: ledger, outcomes, learning
# ---------------------------------------------------------------------------


def test_ledger_contains_every_field_requirement_22_names(prepared) -> None:
    ledger = LedgerService(prepared.ctx).application_ledger(prepared.application_id)
    payload = ledger.model_dump()
    for field in (
        "job",
        "candidate",
        "application_package",
        "resume_version",
        "answers",
        "submission_time",
        "submission_method",
        "status",
        "external_confirmation",
        "events",
    ):
        assert field in payload, f"ledger is missing {field}"
    assert ledger.job["title"]
    assert ledger.job["source_url"].startswith("http")
    assert ledger.application_package["content_hash"] == prepared.package_hash
    assert ledger.resume_version and "v" in ledger.resume_version
    assert ledger.events


def test_events_are_append_only_and_ordered(prepared) -> None:
    ledger = LedgerService(prepared.ctx)
    before = ledger.application_ledger(prepared.application_id).events
    ledger.record_outcome(prepared.application_id, OutcomeKind.RECRUITER_CONTACT, notes="called")
    after = ledger.application_ledger(prepared.application_id).events
    assert len(after) > len(before)
    assert [e["seq"] for e in after] == sorted(e["seq"] for e in after)
    assert after[: len(before)] == before, "prior events must be untouched"


def test_outcome_capture_advances_the_lifecycle(prepared) -> None:
    """An outcome advances the job only when the lifecycle can take that step.

    Set up through the repository so the test states the precondition directly rather
    than pretending an unsubmitted application has been submitted.
    """

    ledger = LedgerService(prepared.ctx)
    # Approve first: the application lifecycle has no AWAITING_APPROVAL -> APPLYING
    # edge, which is the approval boundary itself.
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLIED)
        # The job lifecycle mirrors it: APPROVED -> APPLYING -> APPLIED. Without this
        # the opportunity cannot legally reach RESPONSE, and the assertion below would
        # be testing an edge the state machine does not have.
        repos.opportunities.set_status(prepared.opportunity_id, JobStatus.APPLYING)
        repos.opportunities.set_status(prepared.opportunity_id, JobStatus.APPLIED)

    result = ledger.record_outcome(
        prepared.application_id, OutcomeKind.RECRUITER_CONTACT, notes="Recruiter emailed"
    )
    assert result["kind"] == OutcomeKind.RECRUITER_CONTACT.value
    assert result["applied_state"] == JobStatus.RESPONSE.value
    with prepared.ctx.read_repos() as repos:
        outcomes = repos.outcomes.for_application(prepared.application_id)
        assert len(outcomes) == 1
        assert outcomes[0].notes == "Recruiter emailed"
        assert repos.opportunities.get(prepared.opportunity_id).status == JobStatus.RESPONSE.value


def test_outcome_on_an_unsubmitted_application_records_without_advancing(prepared) -> None:
    """Recording an outcome must not invent a lifecycle step that never happened."""

    ledger = LedgerService(prepared.ctx)
    result = ledger.record_outcome(
        prepared.application_id, OutcomeKind.RECRUITER_CONTACT, notes="premature"
    )
    assert result["applied_state"] is None
    with prepared.ctx.read_repos() as repos:
        assert (
            repos.applications.get(prepared.application_id).status
            == ApplicationStatus.AWAITING_APPROVAL.value
        )


def test_no_response_outcome_does_not_mutate_state(prepared) -> None:
    """Silence is an observation, not a lifecycle transition."""

    ledger = LedgerService(prepared.ctx)
    result = ledger.record_outcome(prepared.application_id, OutcomeKind.NO_RESPONSE)
    assert result["applied_state"] is None


def test_unknown_outcome_kind_is_rejected(prepared) -> None:
    from sabra_domain.errors import ValidationFailure

    with pytest.raises(ValidationFailure):
        LedgerService(prepared.ctx).record_outcome(prepared.application_id, "GHOSTED")


def test_pipeline_groups_the_nineteen_states_into_stages(prepared) -> None:
    summary = LedgerService(prepared.ctx).pipeline(prepared.candidate_id)
    payload = summary.as_dict()
    assert payload["counts"]["opportunities"] == 1
    assert payload["counts"]["awaiting_approval"] == 1
    assert sum(payload["stages"].values()) == 1
    assert payload["stages"]["prepared"] == 1


def test_learning_records_preserve_features_and_strategy(prepared) -> None:
    """Requirement 25: evidence collected before anyone knows what will matter."""

    ledger = LedgerService(prepared.ctx)
    ledger.record_outcome(prepared.application_id, OutcomeKind.INTERVIEW, notes="Interview booked")
    records = ledger.learning_records(prepared.candidate_id)
    assert len(records) == 1
    record = records[0]
    assert record.job_features["title"]
    assert record.candidate_features["populated"] is True
    assert record.artifacts["package_id"] == prepared.package_id
    assert record.artifacts["content_hash"] == prepared.package_hash
    assert record.outcome == OutcomeKind.INTERVIEW.value
    assert record.outcome_recorded_at is not None
    assert record.strategy["rank_score"] == pytest.approx(0.71)
