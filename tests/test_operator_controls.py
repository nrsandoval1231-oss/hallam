"""Verified answers, form fill, batch approval, and challenge handoff.

Every page in here is a local HTML fixture. Nothing in this module opens a
network connection or a live employer form.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest_app import build_job
from html_form import HtmlFormPage
from sabra_automation.base import ApplicationForm, FormField
from sabra_automation.confirmation import PageSnapshot, detect_confirmation
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_automation.handoff import HandoffResult
from sabra_automation.lever_adapter import LeverApplicationAdapter
from sabra_domain.enums import (
    ApplicationStatus,
    PackageStatus,
    TruthLevel,
)
from sabra_domain.errors import ApprovalScopeMismatch, ValidationFailure
from sabra_domain.schemas import (
    AnswerRecord,
    ApplicationAnswerSchema,
    ApplicationPackageSchema,
    EducationEntry,
    EmploymentEntry,
)
from sabra_engine.answer_memory import AnswerMemoryService
from sabra_engine.approval import ApprovalService
from sabra_engine.documents import DocumentService
from sabra_engine.execution import AdapterRegistry, ExecutionService

FIXTURES = Path(__file__).parent / "fixtures"


def _package() -> ApplicationPackageSchema:
    return ApplicationPackageSchema(
        opportunity_id="opp_1",
        candidate_id="cand_1",
        job_id="job_1",
        applicant_name="JORDAN A. RIVERA",
        applicant_email="jordan.rivera@example.test",
        applicant_phone="(214) 555-0142",
        applicant_city="Dallas",
        applicant_state="TX",
        applicant_country="United States",
        tailored_resume_markdown="# JORDAN A. RIVERA\njordan.rivera@example.test\n",
        cover_letter="Hello.",
        employment=[
            EmploymentEntry(
                employer="Meridian Aesthetics",
                title="Regional Sales Manager",
                start_date="Mar 2019",
                end_date="Present",
                is_current=True,
            )
        ],
        education=[
            EducationEntry(
                institution="University of North Texas",
                degree="Bachelor of Business Administration",
                field_of_study="Business",
                end_date="2012",
            )
        ],
        answers=[
            ApplicationAnswerSchema(
                question_key="work_authorization",
                question_text="Are you legally authorized to work in the United States?",
                answer="Yes",
                level=TruthLevel.VERIFIED,
            ),
            ApplicationAnswerSchema(
                question_key="salary_expectations",
                question_text="What are your salary expectations?",
                answer="$130,000",
                level=TruthLevel.VERIFIED,
                sensitive=True,
            ),
        ],
    )


def _open(html_name: str, *, raises: bool = False):
    html = (FIXTURES / html_name).read_text(encoding="utf-8")
    pages: list[HtmlFormPage] = []

    def factory(_url: str) -> HtmlFormPage:
        page = HtmlFormPage(html, captcha_probe_raises=raises)
        pages.append(page)
        return page

    return factory, pages


def test_greenhouse_fixture_fills_history_resume_and_verified_answers(tmp_path) -> None:
    factory, pages = _open("mock_greenhouse_form.html")
    adapter = GreenhouseApplicationAdapter(
        dry_run=True, page_factory=factory, settings=type("S", (), {"artifacts_dir": tmp_path})()
    )
    form = adapter.inspect(build_job())
    prepared = adapter.prepare(_package(), form)
    assert prepared.filled["first_name"] == "JORDAN"
    assert prepared.filled["company-name-0"] == "Meridian Aesthetics"
    assert prepared.filled["title-0"] == "Regional Sales Manager"
    assert prepared.filled["start-date-month-0"] == "3"
    assert prepared.filled["start-date-year-0"] == "2019"
    assert prepared.filled["school--0"] == "University of North Texas"
    assert prepared.filled["degree--0"] == "Bachelor of Business Administration"
    assert prepared.filled["salary_expectations"] == "$130,000"
    assert prepared.files.get("resume")
    assert all(page.clicked == [] for page in pages)
    report = adapter.validate(_package(), form)
    assert not report.ok
    named = " ".join(report.unanswered_questions + report.unknown_required_fields)
    assert "non-compete" in named.lower()


def _lever_package(fixture_resume_text: str, *, wording: str | None) -> ApplicationPackageSchema:
    """A package built from owner memory: the salary answer recorded (and verified) with
    the Lever form's own wording, or with none."""

    from conftest_app import build_profile
    from sabra_documents.master_resume import parse_master_resume
    from sabra_documents.package import PackageFactory

    profile = build_profile("cand_lever")
    profile.answer_memory["salary_expectations"] = AnswerRecord(
        question_key="salary_expectations",
        question_text=wording,
        value="$130,000",
        level=TruthLevel.VERIFIED,
        sensitive=True,
        evidence=["owner_verified"],
    )
    return (
        PackageFactory()
        .build(
            profile=profile,
            master=parse_master_resume(fixture_resume_text),
            job=build_job(source="LEVER", source_url="https://jobs.lever.co/example/1"),
            master_resume_id="resume_1",
            job_id="job_1",
            opportunity_id="opp_1",
            questions=DocumentService._default_questions(),
        )
        .schema
    )


def _lever_adapter(tmp_path):
    factory, pages = _open("mock_lever_form.html")
    adapter = LeverApplicationAdapter(
        dry_run=True, page_factory=factory, settings=type("S", (), {"artifacts_dir": tmp_path})()
    )
    return adapter, pages


def test_lever_fixture_fills_recognised_fields_and_blocks_an_unknown_question(
    tmp_path, fixture_resume_text
) -> None:
    adapter, pages = _lever_adapter(tmp_path)
    job = build_job(source="LEVER", source_url="https://jobs.lever.co/example/1")
    form = adapter.inspect(job)
    # The owner recorded the salary answer with this form's own wording; it rides on the
    # package next to the default-wording one and is what fills the free-text field.
    package = _lever_package(fixture_resume_text, wording="What is your desired salary?")
    prepared = adapter.prepare(package, form)
    assert prepared.filled["name"] == "JORDAN A. RIVERA"
    assert prepared.filled["email"] == "jordan.rivera@example.test"
    assert prepared.filled["org"] == "Meridian Aesthetics"
    assert prepared.filled["salary_expectations"] == "$130,000"
    assert prepared.filled["cards-q2"] == "$130,000"
    assert prepared.files.get("resume")
    assert all(page.clicked == [] for page in pages)
    report = adapter.validate(package, form)
    assert not report.ok
    named = " ".join(report.unanswered_questions + report.unknown_required_fields)
    assert "certification" in named.lower()
    assert "desired salary" not in named.lower()


def test_lever_salary_recorded_without_the_forms_wording_is_refused_into_free_text(
    tmp_path, fixture_resume_text
) -> None:
    adapter, _pages = _lever_adapter(tmp_path)
    form = adapter.inspect(build_job(source="LEVER", source_url="https://jobs.lever.co/example/1"))
    package = _lever_package(fixture_resume_text, wording=None)

    report = adapter.validate(package, form)
    refused = [item for item in report.unknown_required_fields if "desired salary" in item.lower()]
    assert refused and "exact question wording" in refused[0]
    prepared = adapter.prepare(package, form)
    assert "cards-q2" not in prepared.filled
    assert any("desired salary" in item.lower() for item in prepared.unfilled_required)


def test_captcha_fixture_is_not_submitted_and_a_failed_probe_is_a_challenge(prepared, tmp_path) -> None:
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )
    factory, pages = _open("mock_greenhouse_captcha.html")
    adapter = GreenhouseApplicationAdapter(dry_run=True, page_factory=factory)
    result = ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter])).submit(
        prepared.application_id
    )
    assert result.outcome == "DRY_RUN"
    assert "did not open" in (result.detail or "").lower()
    assert pages == []
    assert result.plan is not None
    assert result.plan["navigated"] is False
    assert result.plan["uploaded"] is False
    with prepared.ctx.read_repos() as repos:
        events = [e.event_type for e in repos.events.for_entity("application", prepared.application_id)]
        assert "APPLICATION_SUBMITTED" not in events
        assert "CHALLENGE_DETECTED" not in events
        attempted = [
            e
            for e in repos.events.for_entity("application", prepared.application_id)
            if e.event_type == "APPLICATION_ATTEMPTED"
        ]
        assert attempted and attempted[-1].payload["navigated"] is False
        assert attempted[-1].payload["uploaded"] is False

    class Exploding(HtmlFormPage):
        def evaluate(self, script: str):
            if ".some(s => !!document.querySelector(s))" in script:
                raise RuntimeError("probe failed")
            return super().evaluate(script)

    html = (FIXTURES / "mock_greenhouse_form.html").read_text(encoding="utf-8")
    boom_pages: list[Exploding] = []

    def boom_factory(_url: str) -> Exploding:
        page = Exploding(html)
        boom_pages.append(page)
        return page

    class Recorded:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, **_kwargs: object) -> HandoffResult:
            self.calls += 1
            return HandoffResult(outcome="failed", source="user_confirmation", opened_headed=True)

    prepared.ctx.settings.automation_dry_run = False
    handoff = Recorded()
    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=boom_factory)
    result = ExecutionService(
        prepared.ctx, registry=AdapterRegistry([adapter]), handoff=handoff
    ).submit(prepared.application_id, allow_real_submission=True)
    assert handoff.calls == 1
    assert result.outcome == "FAILED"
    assert all(page.clicked == [] for page in boom_pages)


def test_confirmation_page_counts_as_submitted_without_the_adapter_clicking() -> None:
    """The person submitted; the page left the form for the employer's confirmation URL."""

    class Page:
        def __init__(self, url: str, body: str) -> None:
            self._url, self._body = url, body

        def current_url(self) -> str:
            return self._url

        def body_text(self) -> str:
            return self._body

        def evaluate(self, _script: str) -> int:
            return 0  # no submit control and no form on the page any more

    before = PageSnapshot(url_key="https://job-boards.greenhouse.io/acme/jobs/1", form_present=True)
    confirmed = detect_confirmation(
        Page("https://job-boards.greenhouse.io/acme/jobs/1/confirmation?x=1", "Thank you."),
        "greenhouse",
        before,
    )
    assert confirmed.confirmed
    # A solve-the-puzzle page that happens to be a different URL is not a confirmation.
    other = detect_confirmation(
        Page("https://job-boards.greenhouse.io/acme/jobs/1/verify", "Please solve the puzzle"),
        "greenhouse",
        before,
    )
    assert not other.confirmed


def test_unknown_required_field_blocks_with_a_named_reason() -> None:
    adapter = GreenhouseApplicationAdapter(dry_run=True)
    form = ApplicationForm(
        url="http://127.0.0.1/fixture",
        fields=[
            FormField(name="first_name", label="First Name", required=True, selector="#first_name"),
            FormField(
                name="secret_code",
                label="Favorite dinosaur",
                required=True,
                selector="#secret_code",
            ),
        ],
        submit_selector='button[type="submit"]',
    )
    report = adapter.validate(_package(), form)
    assert not report.ok
    assert any("Favorite dinosaur" in field for field in report.unknown_required_fields)


def test_record_then_verify_is_required_before_a_sensitive_answer_fills(engine) -> None:
    from sabra_engine.candidate_intake import ensure_candidate

    candidate_id = ensure_candidate(engine, display_name="Test", create_placeholder=True)
    service = AnswerMemoryService(engine)
    recorded = service.record(
        "background_check",
        "Yes",
        question_text="Are you willing to undergo a background check?",
        candidate_id=candidate_id,
    )
    assert recorded["verified"] is False
    listed = service.list_answers(candidate_id)
    stored = next(row for row in listed if row["question_key"] == "background_check")
    assert stored["auto_fillable"] is False
    verified = service.verify("background_check", candidate_id=candidate_id)
    assert verified["verified"] is True
    assert verified["value"] == "Yes"
    with pytest.raises(ValidationFailure):
        service.verify("salary_expectations", candidate_id=candidate_id)


def test_batch_approval_binds_each_package_to_its_hash(prepared) -> None:
    from sabra_domain.identity import content_hash

    with prepared.ctx.repos() as repos:
        profile = repos.candidates.active_profile(prepared.candidate_id)
        profile.answer_memory.update(
            {
                "sponsorship": AnswerRecord(
                    question_key="sponsorship", value="No", level=TruthLevel.VERIFIED
                ),
                "salary_expectations": AnswerRecord(
                    question_key="salary_expectations",
                    value="$130,000",
                    level=TruthLevel.VERIFIED,
                    sensitive=True,
                    evidence=["owner_verified"],
                ),
                "start_date": AnswerRecord(
                    question_key="start_date", value="2 weeks", level=TruthLevel.VERIFIED
                ),
                "background_check": AnswerRecord(
                    question_key="background_check",
                    value="Yes",
                    level=TruthLevel.VERIFIED,
                    sensitive=True,
                    evidence=["owner_verified"],
                ),
                "relocation": AnswerRecord(
                    question_key="relocation", value="No", level=TruthLevel.VERIFIED
                ),
                "travel": AnswerRecord(
                    question_key="travel", value="Yes", level=TruthLevel.VERIFIED
                ),
            }
        )
        repos.candidates.save_profile(prepared.candidate_id, profile)

    documents = DocumentService(prepared.ctx)
    complete = documents.prepare_package(prepared.opportunity_id)
    assert complete.status is PackageStatus.COMPLETE

    service = ApprovalService(prepared.ctx)
    refused = service.approve_selected(
        [{"package_id": prepared.package_id, "content_hash": prepared.package_hash}],
        approved_by="owner",
    )
    assert refused.approved == []
    assert refused.refused and "COMPLETE" in refused.refused[0]["message"]

    mismatch = service.approve_selected(
        [{"package_id": complete.package_id, "content_hash": "not-the-hash"}],
        approved_by="owner",
    )
    assert mismatch.approved == []
    assert mismatch.refused[0]["error"] == "ApprovalScopeMismatch"

    approved = service.approve_selected(
        [{"package_id": complete.package_id, "content_hash": complete.content_hash}],
        approved_by="owner",
    )
    assert len(approved.approved) == 1
    assert approved.approved[0].approved_package_hash == complete.content_hash
    assert approved.refused == []

    with prepared.ctx.repos() as repos:
        row = repos.packages.get(complete.package_id)
        payload = dict(row.payload)
        payload["cover_letter"] = (payload.get("cover_letter") or "") + "\nEdited."
        row.payload = payload
        from sabra_domain.schemas import ApplicationPackageSchema as Schema

        row.content_hash = content_hash(Schema.model_validate(payload).semantic_payload())

    with pytest.raises(ApprovalScopeMismatch):
        service.assert_submission_authorised(complete.application_id)

    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(complete.application_id)
        assert application.status == ApplicationStatus.APPROVED.value
        assert application.approved_package_hash == complete.content_hash
        assert application.approved_package_hash != repos.packages.get(complete.package_id).content_hash


def test_dashboard_exposes_batch_approval_and_answer_verification() -> None:
    from fastapi.testclient import TestClient
    from sabra_api.main import app

    client = TestClient(app)
    applications = client.get("/applications")
    assert applications.status_code == 200
    assert "Approve selected" in applications.text
    assert "content_hash" in applications.text
    answers = client.get("/answers")
    assert answers.status_code == 200
    assert "salary_expectations" in answers.text
    assert "Verify" in answers.text
    overview = client.get("/")
    assert 'href="/applications"' in overview.text
    assert 'href="/answers"' in overview.text
    assert 'id="overview-queue"' in overview.text
    assert "/api/opportunities" in overview.text
    assert 'data-key="packages_latest"' in overview.text
    assert "Read package" in applications.text
    assert ">Copy<" in applications.text
    assert ">Apply<" in applications.text
    assert "SABRA_AUTOMATION_DRY_RUN" in applications.text
    assert 'id="ask-dialog"' in applications.text
    assert "confirm(" not in applications.text
    answers_body = answers.text
    for key in (
        "us_citizen",
        "spanish",
        "resume_headline",
        "phone",
        "country",
        "city",
        "state_residence",
        "postal_code",
        "street_address",
        "age_eligibility",
        "non_compete",
    ):
        assert key in answers_body
    assert "Custom key" in answers_body


def test_dry_run_never_opens_a_page_or_launches_a_browser(prepared, monkeypatch) -> None:
    """Dry run plans the fill and stops. No browser, no employer request."""

    opened: list[str] = []

    def factory(url: str):
        opened.append(url)
        raise AssertionError(f"dry run opened {url}")

    launched: list[bool] = []

    class Boom:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            launched.append(True)
            raise AssertionError("browser launched")

    monkeypatch.setattr("sabra_automation.playwright_driver.BrowserSession", Boom)
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )
    adapter = GreenhouseApplicationAdapter(dry_run=True, page_factory=factory)
    result = ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter])).submit(
        prepared.application_id
    )
    assert result.outcome == "DRY_RUN"
    assert opened == []
    assert launched == []
    assert result.plan["typed"] is False
    assert result.plan["uploaded"] is False
    assert result.plan["navigated"] is False

    from sabra_domain.errors import SafetyStop

    bare = GreenhouseApplicationAdapter(dry_run=True)
    with pytest.raises(SafetyStop) as excinfo:
        bare.inspect(build_job())
    assert launched == []
    assert "will not open" in str(excinfo.value).lower()
