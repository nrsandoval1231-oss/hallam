"""The submission boundary: one browser session, no inferred confirmation, a handoff
that waits for the person, an atomic claim, and no unverified identity on the form.

Every page is a local double. Nothing here opens a browser, a network connection or an
employer form.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import pytest
from conftest_app import build_job
from fake_pages import GH_URL, FakeFormPage, answerable_capture
from fastapi.testclient import TestClient
from html_form import HtmlFormPage
from sabra_automation.base import ApplicationForm, FormField
from sabra_automation.confirmation import PageSnapshot, detect_confirmation, normalize_url
from sabra_automation.greenhouse_adapter import (
    ENUMERATE_FIELDS_JS,
    GreenhouseApplicationAdapter,
)
from sabra_automation.handoff import (
    HandoffControl,
    HandoffLaunchError,
    HandoffResult,
    PlaywrightHandoff,
)
from sabra_automation.lever_adapter import LeverApplicationAdapter
from sabra_domain.enums import (
    ApplicationStatus,
    EventType,
    JobStatus,
    SafetyStopKind,
    TruthLevel,
)
from sabra_domain.errors import (
    ApprovalRequired,
    ApprovalScopeMismatch,
    InvalidTransition,
    SafetyStop,
    ValidationFailure,
)
from sabra_domain.identity import content_hash
from sabra_domain.schemas import (
    ApplicationAnswerSchema,
    ApplicationPackageSchema,
    EmploymentEntry,
)
from sabra_domain.state import application_path
from sabra_engine.approval import ApprovalService
from sabra_engine.context import EngineContext
from sabra_engine.execution import AdapterRegistry, ExecutionService
from sabra_engine.handoff_manager import HANDOFFS, HandoffManager

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_handoffs():
    HANDOFFS.clear()
    yield
    HANDOFFS.clear()


def _approve(prepared) -> None:
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )


def _live(prepared) -> None:
    prepared.ctx.settings.automation_dry_run = False


def _status(prepared) -> str:
    with prepared.ctx.read_repos() as repos:
        return repos.applications.get(prepared.application_id).status


def _events(prepared) -> list[Any]:
    with prepared.ctx.read_repos() as repos:
        return list(repos.events.for_entity("application", prepared.application_id))


def _event_types(prepared) -> list[str]:
    return [e.event_type for e in _events(prepared)]


def _service(prepared, adapter, **kwargs) -> ExecutionService:
    return ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter]), **kwargs)


def _answerable(capture: dict) -> dict:
    return answerable_capture(capture)


def _pkg(**overrides: Any) -> ApplicationPackageSchema:
    base: dict[str, Any] = dict(
        opportunity_id="opp_1",
        candidate_id="cand_1",
        job_id="job_1",
        applicant_name="JORDAN A. RIVERA",
        applicant_email="jordan.rivera@example.test",
        applicant_phone="(214) 555-0142",
        applicant_country="United States",
        tailored_resume_markdown="# JORDAN A. RIVERA\njordan.rivera@example.test | USA\n",
        cover_letter="Hello.",
        answers=[
            ApplicationAnswerSchema(
                question_key="work_authorization",
                question_text="Are you legally authorized to work in the United States?",
                answer="Yes",
                level=TruthLevel.VERIFIED,
            )
        ],
        employment=[
            EmploymentEntry(
                employer="Meridian Aesthetics",
                title="Regional Sales Manager",
                start_date="Mar 2019",
                end_date="Present",
                is_current=True,
            )
        ],
    )
    base.update(overrides)
    return ApplicationPackageSchema(**base)


class FakeHandoff:
    """A headed browser that stays "open" until a person reports an outcome."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        *,
        adapter,
        package,
        form,
        url,
        settings=None,
        control=None,
        on_event=None,
    ) -> HandoffResult:
        self.calls.append({"url": url, "form": form})
        if on_event:
            on_event("opened", {"url": url})
        self.started.set()
        outcome = control.wait(30) if control is not None else None
        if outcome is None:
            return HandoffResult(outcome="timeout", source="timeout", opened_headed=True)
        return HandoffResult(outcome=outcome, source="user_report", opened_headed=True)


class ScriptedHandoff:
    """A handoff that returns a fixed result at once."""

    def __init__(self, result: HandoffResult | Exception) -> None:
        self.result = result
        self.calls = 0

    def run(self, **_kwargs: Any) -> HandoffResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _gh_adapter(capture: dict, pages: list | None = None, **page_kwargs: Any):
    pages = pages if pages is not None else []

    def factory(_url: str) -> FakeFormPage:
        page = FakeFormPage(capture, **page_kwargs)
        pages.append(page)
        return page

    return GreenhouseApplicationAdapter(dry_run=False, page_factory=factory), pages


# ---------------------------------------------------------------------------
# 1. one browser session per application
# ---------------------------------------------------------------------------


def test_submit_clicks_the_same_page_prepare_filled(prepared, greenhouse_form_capture) -> None:
    """A factory that returns a NEW page on every call must still be called once, and the
    click must land on the object that was filled."""

    _approve(prepared)
    _live(prepared)
    capture = _answerable(greenhouse_form_capture)
    adapter, pages = _gh_adapter(
        capture,
        captcha=False,
        confirmation_url=GH_URL + "/confirmation",
    )
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "SUBMITTED", (result.outcome, result.detail, result.stops)
    assert len(pages) == 1, "the factory must be called once per run, not once per operation"
    (page,) = pages
    assert page.filled, "prepare typed into this page"
    assert page.clicked, "submit clicked this same page"
    assert page.closed, "the session closes the page when the run ends"


def test_adapter_session_reuses_one_page_across_inspect_prepare_submit(
    greenhouse_form_capture,
) -> None:
    adapter, pages = _gh_adapter(
        _answerable(greenhouse_form_capture),
        captcha=False,
        confirmation_url=GH_URL + "/confirmation",
    )
    job = build_job()
    with adapter.session(adapter.application_url(job)) as page:
        form = adapter.inspect(job)
        prepared = adapter.prepare(_pkg(), form)
        receipt = adapter.submit(prepared)
        assert page is pages[0]
    assert len(pages) == 1
    assert pages[0].clicked and pages[0].filled
    assert receipt.submitted is True
    assert pages[0].closed


def test_submit_without_a_prior_prepare_in_the_same_session_is_refused(
    greenhouse_form_capture,
) -> None:
    from sabra_automation.base import PreparedSubmission

    adapter, pages = _gh_adapter(greenhouse_form_capture)
    job = build_job()
    prepared = PreparedSubmission(url=GH_URL, filled={"first_name": "x"})

    # No session at all.
    with pytest.raises(SafetyStop) as excinfo:
        adapter.submit(prepared)
    assert excinfo.value.kind == SafetyStopKind.MATERIAL_AMBIGUITY

    # A session that inspected but never prepared.
    with adapter.session(adapter.application_url(job)):
        adapter.inspect(job)
        with pytest.raises(SafetyStop) as excinfo:
            adapter.submit(prepared)
        assert excinfo.value.kind == SafetyStopKind.MATERIAL_AMBIGUITY
    assert all(page.clicked == [] for page in pages)

    # A one-shot prepare (its page is discarded) cannot be submitted afterwards either.
    form = adapter.inspect(job)
    adapter.prepare(_pkg(), form)
    with pytest.raises(SafetyStop):
        adapter.submit(prepared)
    assert all(page.clicked == [] for page in pages)


def test_a_second_session_cannot_be_nested(greenhouse_form_capture) -> None:
    adapter, _pages = _gh_adapter(greenhouse_form_capture)
    with adapter.session(GH_URL), pytest.raises(SafetyStop), adapter.session(GH_URL):
        pass


def test_the_live_page_must_still_be_the_inspected_form(greenhouse_form_capture) -> None:
    adapter, pages = _gh_adapter(greenhouse_form_capture)
    job = build_job()
    with adapter.session(adapter.application_url(job)):
        form = adapter.inspect(job)
        pages[0]._url = "https://elsewhere.example/other"
        with pytest.raises(SafetyStop):
            adapter.prepare(_pkg(), form)


def test_a_failed_open_still_closes_the_browser(monkeypatch) -> None:
    class Boom:
        def open(self, _url: str) -> None:
            raise RuntimeError("navigation timed out")

    class FakeSession:
        instances: list[FakeSession] = []

        def __init__(self, **_kwargs: Any) -> None:
            self.closed = False
            FakeSession.instances.append(self)

        def __enter__(self) -> FakeSession:
            return self

        def new_page(self) -> Boom:
            return Boom()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("sabra_automation.playwright_driver.BrowserSession", FakeSession)
    adapter = GreenhouseApplicationAdapter(dry_run=False)
    with pytest.raises(RuntimeError):
        adapter.inspect(build_job())
    assert [s.closed for s in FakeSession.instances] == [True]


def test_the_claim_happens_before_any_browser_opens(prepared, greenhouse_form_capture) -> None:
    _approve(prepared)
    _live(prepared)
    seen: dict[str, Any] = {}

    def factory(_url: str) -> FakeFormPage:
        seen["status"] = _status(prepared)
        seen["events"] = _event_types(prepared)
        return FakeFormPage(_answerable(greenhouse_form_capture), captcha=False)

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=factory)
    _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert seen["status"] == ApplicationStatus.APPLYING.value
    assert EventType.APPLICATION_STARTED.value in seen["events"]


# ---------------------------------------------------------------------------
# 2. confirmation is never inferred from body keywords
# ---------------------------------------------------------------------------


class _Page:
    def __init__(self, url: str, body: str = "", *, form: int = 1, selectors=()) -> None:
        self.url, self.body, self.form, self.selectors = url, body, form, tuple(selectors)

    def current_url(self) -> str:
        return self.url

    def body_text(self) -> str:
        return self.body

    def evaluate(self, script: str) -> int:
        if any(sel in script for sel in self.selectors):
            return 1
        return self.form if "submit" in script or "form" in script else 0


GH_FORM = "https://job-boards.greenhouse.io/acme/jobs/1"


def _before() -> PageSnapshot:
    return PageSnapshot(url_key=normalize_url(GH_FORM), form_present=True)


def test_thank_you_text_with_the_form_still_present_is_not_a_confirmation() -> None:
    page = _Page(GH_FORM + "?utm=1", "Thank you for your interest in this role. Apply now.")
    result = detect_confirmation(page, "greenhouse", _before())
    assert result.confirmed is False


def test_greenhouse_confirmation_url_confirms() -> None:
    page = _Page(GH_FORM + "/confirmation", "", form=0)
    assert detect_confirmation(page, "greenhouse", _before()).confirmed is True


def test_greenhouse_marker_selector_needs_the_form_to_be_gone() -> None:
    present = _Page(GH_FORM, "", form=1, selectors=["#application_confirmation"])
    assert detect_confirmation(present, "greenhouse", _before()).confirmed is False
    gone = _Page(GH_FORM, "", form=0, selectors=["#application_confirmation"])
    # Same URL but the form is gone and the confirmation element is there.
    assert detect_confirmation(gone, "greenhouse", _before()).confirmed is True


def test_greenhouse_text_marker_counts_only_after_leaving_the_form() -> None:
    same_url = _Page(GH_FORM, "Your application has been submitted", form=0)
    assert detect_confirmation(same_url, "greenhouse", _before()).confirmed is False
    moved = _Page(GH_FORM + "/done", "Thank you for applying!", form=0)
    assert detect_confirmation(moved, "greenhouse", _before()).confirmed is True


def test_query_and_fragment_do_not_count_as_navigation() -> None:
    assert normalize_url("https://a.test/x/?q=1#frag") == normalize_url("https://a.test/x")


def test_lever_confirms_only_on_the_thanks_page() -> None:
    before = PageSnapshot(url_key=normalize_url("https://jobs.lever.co/acme/1/apply"), form_present=True)
    thanks = _Page("https://jobs.lever.co/acme/1/thanks?source=x", "", form=0)
    assert detect_confirmation(thanks, "lever", before).confirmed is True
    body_only = _Page("https://jobs.lever.co/acme/1/apply", "Thank you for applying", form=1)
    assert detect_confirmation(body_only, "lever", before).confirmed is False
    moved_no_marker = _Page("https://jobs.lever.co/acme/1/other", "Thank you", form=0)
    assert detect_confirmation(moved_no_marker, "lever", before).confirmed is False


def test_an_unreadable_page_is_not_a_confirmation() -> None:
    class Broken(_Page):
        def evaluate(self, script: str) -> int:
            raise RuntimeError("page crashed")

    assert detect_confirmation(Broken(GH_FORM), "greenhouse", _before()).confirmed is False


def test_greenhouse_adapter_does_not_confirm_a_job_description_thank_you(
    prepared, greenhouse_form_capture
) -> None:
    """The defect: "Thank you for your interest" in the posting made a click look
    like a submission. It must end UNCONFIRMED, with the click recorded."""

    _approve(prepared)
    _live(prepared)
    adapter, _pages = _gh_adapter(
        _answerable(greenhouse_form_capture),
        captcha=False,
        confirmation_body="Thank you for your interest in this role. Submit application",
    )
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "UNCONFIRMED", (result.outcome, result.detail)
    assert result.receipt is not None
    assert result.receipt.clicked is True and result.receipt.submitted is False
    assert _status(prepared) == ApplicationStatus.APPLYING.value
    types = _event_types(prepared)
    assert EventType.APPLICATION_UNCONFIRMED.value in types
    assert EventType.APPLICATION_SUBMITTED.value not in types
    assert EventType.APPLICATION_FAILED.value not in types
    with prepared.ctx.read_repos() as repos:
        assert repos.applications.get(prepared.application_id).submission_time is None


def test_lever_adapter_confirms_on_thanks_and_not_on_a_body_keyword(tmp_path) -> None:
    html = (FIXTURES / "mock_lever_form.html").read_text(encoding="utf-8")
    # Drop the board-specific questions this package has no verified answers for.
    html = re.sub(r'<label for="cards-q\d">.*?</label>\s*<input id="cards-q\d"[^>]*>', "", html, flags=re.S)
    job = build_job(source="LEVER", source_url="https://jobs.lever.co/example/1")

    class ThanksPage(HtmlFormPage):
        def click(self, selector: str) -> None:
            super().click(selector)
            self._url = "https://jobs.lever.co/example/1/thanks"

    def run(page_cls: type[HtmlFormPage]):
        pages: list[HtmlFormPage] = []

        def factory(_url: str) -> HtmlFormPage:
            page = page_cls(html)
            pages.append(page)
            return page

        adapter = LeverApplicationAdapter(
            dry_run=False,
            page_factory=factory,
            settings=type("S", (), {"artifacts_dir": tmp_path})(),
        )
        with adapter.session(adapter.application_url(job)):
            form = adapter.inspect(job)
            prepared_form = adapter.prepare(_pkg(), form)
            return adapter.submit(prepared_form), pages

    # HtmlFormPage.click writes "Thank you..." into the body and leaves the form up.
    receipt, pages = run(HtmlFormPage)
    assert receipt.clicked and not receipt.submitted
    assert len(pages) == 1

    receipt, _ = run(ThanksPage)
    assert receipt.submitted is True


def test_a_click_that_raises_is_unconfirmed_not_failed(prepared, greenhouse_form_capture) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _pages = _gh_adapter(
        _answerable(greenhouse_form_capture),
        captcha=False,
        click_raises=RuntimeError("navigation interrupted"),
    )
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "UNCONFIRMED"
    assert _status(prepared) == ApplicationStatus.APPLYING.value
    assert EventType.APPLICATION_UNCONFIRMED.value in _event_types(prepared)


# ---------------------------------------------------------------------------
# 3. the handoff waits for the person
# ---------------------------------------------------------------------------


class LivePage:
    """A headed page for PlaywrightHandoff: a URL, a body, and a form-present flag."""

    def __init__(self, url: str = GH_FORM) -> None:
        self.url = url
        self.body = "Apply for this job"
        self.form = 1
        self.marker_count = 0
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def current_url(self) -> str:
        return self.url

    def body_text(self) -> str:
        return self.body

    def evaluate(self, script: str) -> int:
        # Confirmation-marker selectors and form/submit selectors are counted separately.
        if "confirmation" in script:
            return self.marker_count
        return self.form

    def open(self, url: str) -> None:
        self.url = url


class _FakeBrowser:
    """Stands in for `BrowserSession`; nothing is launched."""

    instances: list[_FakeBrowser] = []
    page: LivePage
    open_error: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        _FakeBrowser.instances.append(self)

    def __enter__(self) -> _FakeBrowser:
        return self

    def new_page(self) -> LivePage:
        if _FakeBrowser.open_error is not None:
            raise _FakeBrowser.open_error
        return _FakeBrowser.page

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def browser(monkeypatch):
    _FakeBrowser.instances = []
    _FakeBrowser.page = LivePage()
    _FakeBrowser.open_error = None
    monkeypatch.setattr("sabra_automation.playwright_driver.BrowserSession", _FakeBrowser)
    return _FakeBrowser


class _FillingAdapter:
    kind = "greenhouse"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.filled = 0

    def apply_to_page(self, page, package, form):
        self.filled += 1
        if self.error is not None:
            raise self.error
        return {}, []


def _run_handoff(adapter, *, control=None, prompt=None, events=None, **kwargs):
    handoff = PlaywrightHandoff(prompt=prompt, poll_seconds=0.01, **kwargs)
    sink = (lambda state, payload: events.append((state, payload))) if events is not None else None
    return handoff.run(
        adapter=adapter,
        package=object(),
        form=object(),
        url=GH_FORM,
        settings=None,
        control=control or HandoffControl(5),
        on_event=sink,
    )


def test_handoff_keeps_the_browser_open_until_the_person_reports(browser) -> None:
    control = HandoffControl(5)
    events: list = []
    timer = threading.Timer(0.15, lambda: control.report("submitted"))
    timer.start()
    result = _run_handoff(_FillingAdapter(), control=control, events=events)
    assert result.outcome == "submitted" and result.source == "user_report"
    assert [state for state, _ in events] == ["opened"]
    assert browser.instances[0].closed is True


def test_handoff_confirms_from_the_page_not_from_body_text(browser) -> None:
    page = browser.page
    page.body = "Thank you for your interest in this role"  # a job description
    flip = threading.Timer(
        0.15, lambda: (setattr(page, "url", GH_FORM + "/confirmation"), setattr(page, "form", 0))
    )
    flip.start()
    result = _run_handoff(_FillingAdapter())
    assert result.outcome == "submitted" and result.source == "confirmation_page"


def test_handoff_does_not_confirm_on_body_text_alone(browser) -> None:
    browser.page.body = "Thank you. Your application has been submitted."
    control = HandoffControl(0.2)
    result = _run_handoff(_FillingAdapter(), control=control)
    assert result.outcome == "timeout"


def test_handoff_closed_browser_needs_a_report_it_does_not_abandon(browser) -> None:
    page = browser.page
    events: list = []
    threading.Timer(0.1, lambda: setattr(page, "closed", True)).start()
    result = _run_handoff(_FillingAdapter(), events=events)
    assert result.outcome == "closed"
    assert ("browser_closed", {"url": GH_FORM}) in events


def test_handoff_hard_timeout(browser) -> None:
    result = _run_handoff(_FillingAdapter(), control=HandoffControl(0.1))
    assert result.outcome == "timeout"
    assert "no outcome" in (result.error or "")


def test_a_fill_failure_is_logged_and_the_page_is_left_open(browser) -> None:
    control = HandoffControl(5)
    events: list = []
    adapter = _FillingAdapter(RuntimeError("selector vanished"))
    page = browser.page

    def report() -> None:
        assert page.closed is False
        control.report("abandoned")

    threading.Timer(0.1, report).start()
    result = _run_handoff(adapter, control=control, events=events)
    assert adapter.filled == 1
    assert result.outcome == "abandoned"
    states = [state for state, _ in events]
    assert "fill_failed" in states and "opened" in states


def test_handoff_that_cannot_open_raises_a_launch_error_and_closes(browser) -> None:
    browser.open_error = RuntimeError("no chromium")
    with pytest.raises(HandoffLaunchError):
        _run_handoff(_FillingAdapter())
    assert browser.instances[0].closed is True


def test_prompt_mode_asks_again_on_a_typo(browser) -> None:
    answers = iter(["sumbitted", "yes", "  FAILED "])
    result = _run_handoff(_FillingAdapter(), prompt=lambda: next(answers))
    assert result.outcome == "failed"


def test_prompt_mode_never_turns_garbage_into_abandoned(browser) -> None:
    result = _run_handoff(
        _FillingAdapter(), prompt=lambda: "garbage", max_invalid_answers=3
    )
    assert result.outcome == "closed"


def test_cli_prompt_reprompts_until_valid_and_q_is_abandoned(capsys) -> None:
    from sabra_engine.cli import prompt_for_outcome

    answers = iter(["", "subm", "submited", "SUBMITTED"])
    assert prompt_for_outcome(lambda _text: next(answers)) == "submitted"
    assert "Please type" in capsys.readouterr().out
    assert prompt_for_outcome(lambda _text: "q") == "abandoned"
    answers = iter(["nope", "failed"])
    assert prompt_for_outcome(lambda _text: next(answers)) == "failed"


def _captcha_adapter(capture: dict, pages: list | None = None):
    return _gh_adapter(capture, pages, captcha=True)


def test_abandoned_handoff_is_blocked_and_retryable_not_withdrawn(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, pages = _captcha_adapter(_answerable(greenhouse_form_capture))
    handoff = ScriptedHandoff(HandoffResult(outcome="abandoned", source="user_report"))
    result = _service(prepared, adapter, handoff=handoff).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "ABANDONED"
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    # The headless inspection session never typed into the form: the person's headed
    # browser is the only place it is filled.
    assert all(page.filled == {} and page.files == {} and page.clicked == [] for page in pages)
    types = _event_types(prepared)
    assert EventType.APPLICATION_ABANDONED.value in types

    # Retry: BLOCKED with nothing submitted is authorised again and can complete.
    confirm_adapter, _ = _gh_adapter(
        _answerable(greenhouse_form_capture),
        captcha=False,
        confirmation_url=GH_URL + "/confirmation",
    )
    retry = _service(prepared, confirm_adapter).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert retry.outcome == "SUBMITTED", (retry.outcome, retry.detail)
    assert _status(prepared) == ApplicationStatus.APPLIED.value
    with prepared.ctx.read_repos() as repos:
        assert repos.opportunities.get(prepared.opportunity_id).status == JobStatus.APPLIED.value


def test_timed_out_and_closed_handoffs(prepared, greenhouse_form_capture) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    timeout = ScriptedHandoff(HandoffResult(outcome="timeout", source="timeout", error="too slow"))
    result = _service(prepared, adapter, handoff=timeout).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "BLOCKED"
    with prepared.ctx.read_repos() as repos:
        row = repos.applications.get(prepared.application_id)
        assert row.status == ApplicationStatus.BLOCKED.value
        assert row.blocked_reason == "HANDOFF_TIMEOUT"
        assert "too slow" in (row.last_error or "")

    # A second attempt whose browser is closed leaves the application APPLYING.
    closed = ScriptedHandoff(HandoffResult(outcome="closed", source="browser_closed"))
    again = _service(prepared, adapter, handoff=closed).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert again.outcome == "HANDED_TO_HUMAN"
    assert _status(prepared) == ApplicationStatus.APPLYING.value
    states = [
        e.payload.get("state") for e in _events(prepared) if e.event_type == "HANDED_TO_HUMAN"
    ]
    assert "needs_report" in states

    # The person reports afterwards; no thread is left, so it is recorded directly.
    status = _service(prepared, adapter).report_handoff_outcome(
        prepared.application_id, "submitted"
    )
    assert status["application_status"] == ApplicationStatus.APPLIED.value
    with prepared.ctx.read_repos() as repos:
        row = repos.applications.get(prepared.application_id)
        assert row.submission_time is not None
        assert row.submission_method == "HEADED_HUMAN"


def test_handoff_exceptions_become_a_failed_result_not_a_raise(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    result = _service(prepared, adapter, handoff=ScriptedHandoff(RuntimeError("no display"))).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "FAILED" and "no display" in (result.detail or "")
    assert _status(prepared) == ApplicationStatus.FAILED.value


def test_handoff_manager_refuses_a_second_start_while_one_is_active() -> None:
    manager = HandoffManager()
    release = threading.Event()
    manager.start("app_1", url="u", timeout_seconds=5, target=lambda _record: release.wait(5))
    with pytest.raises(ValidationFailure):
        manager.start("app_1", url="u", timeout_seconds=5, target=lambda _record: None)
    release.set()
    manager.get("app_1").thread.join(5)
    manager.start("app_1", url="u", timeout_seconds=5, target=lambda _record: None)


# -- the API path --------------------------------------------------------------


@pytest.fixture()
def api(prepared, greenhouse_form_capture, monkeypatch):
    """The dashboard app wired to this test's database and a fake headed browser."""

    from sabra_api import main

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    _approve(prepared)
    _live(prepared)
    handoff = FakeHandoff()
    pages: list[FakeFormPage] = []

    def build(ctx):
        adapter, _ = _gh_adapter(_answerable(greenhouse_form_capture), pages, captcha=True)
        return ExecutionService(
            ctx,
            registry=AdapterRegistry([adapter]),
            handoff=handoff,
            background_handoff=True,
            ctx_factory=EngineContext.create,
        )

    monkeypatch.setattr(main, "_execution_service", build)
    client = TestClient(main.app)
    yield client, handoff, pages
    for record in list(HANDOFFS._records.values()):
        record.control.report("abandoned")
        if record.thread:
            record.thread.join(5)


def _apply(client, prepared):
    return client.post(
        f"/api/applications/{prepared.application_id}/apply", json={"confirm": True}
    )


def test_api_apply_returns_at_once_and_waits_for_the_outcome_endpoint(prepared, api) -> None:
    client, handoff, pages = api
    response = _apply(client, prepared)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "HANDED_TO_HUMAN"
    assert body["url"]
    assert handoff.started.wait(5)

    # Still APPLYING while the person works; nothing was decided for them.
    assert _status(prepared) == ApplicationStatus.APPLYING.value
    live = client.get(f"/api/applications/{prepared.application_id}/handoff").json()
    assert live["active"] is True and live["state"] == "open"
    assert live["awaiting_person"] is True
    assert live["application_status"] == "APPLYING"

    # A second Apply is refused while the first is in progress.
    second = _apply(client, prepared)
    assert second.status_code == 409
    assert "already in progress" in second.json()["detail"]

    # The headless inspection page was only read: nothing typed, nothing clicked.
    assert all(p.filled == {} and p.clicked == [] for p in pages)

    done = client.post(
        f"/api/applications/{prepared.application_id}/handoff/outcome",
        json={"outcome": "submitted"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["application_status"] == "APPLIED"
    assert _status(prepared) == ApplicationStatus.APPLIED.value
    types = _event_types(prepared)
    assert "HANDED_TO_HUMAN" in types and "APPLICATION_SUBMITTED" in types
    with prepared.ctx.read_repos() as repos:
        row = repos.applications.get(prepared.application_id)
        assert row.submission_method == "HEADED_HUMAN"
        assert repos.opportunities.get(prepared.opportunity_id).status == "APPLIED"


def test_api_outcome_abandoned_returns_the_application_to_blocked(prepared, api) -> None:
    client, handoff, _pages = api
    assert _apply(client, prepared).json()["outcome"] == "HANDED_TO_HUMAN"
    assert handoff.started.wait(5)
    done = client.post(
        f"/api/applications/{prepared.application_id}/handoff/outcome",
        json={"outcome": "abandoned"},
    )
    assert done.status_code == 200
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    assert "APPLICATION_ABANDONED" in _event_types(prepared)


def test_api_outcome_rejects_bad_input_and_idle_applications(prepared, api) -> None:
    client, _handoff, _pages = api
    bad = client.post(
        f"/api/applications/{prepared.application_id}/handoff/outcome",
        json={"outcome": "withdrawn"},
    )
    assert bad.status_code == 422
    idle = client.post(
        f"/api/applications/{prepared.application_id}/handoff/outcome",
        json={"outcome": "submitted"},
    )
    assert idle.status_code == 409
    status = client.get(f"/api/applications/{prepared.application_id}/handoff").json()
    assert status["active"] is False and status["awaiting_person"] is False


def test_applications_page_has_the_handoff_controls() -> None:
    from sabra_api.main import app

    html = TestClient(app).get("/applications").text
    assert "I submitted it" in html and "I didn't submit" in html
    assert "/handoff/outcome" in html
    assert "inflight" in html


# ---------------------------------------------------------------------------
# 4. atomic claim and no escaping exceptions
# ---------------------------------------------------------------------------


def test_a_plain_runtime_error_during_prepare_fails_the_application(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _pages = _gh_adapter(
        _answerable(greenhouse_form_capture),
        captcha=False,
        fill_raises=RuntimeError("selector exploded"),
    )
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "FAILED"
    assert "selector exploded" in (result.detail or "")
    assert _status(prepared) == ApplicationStatus.FAILED.value
    failed = [e for e in _events(prepared) if e.event_type == "APPLICATION_FAILED"]
    assert failed and "selector exploded" in failed[-1].payload["message"]


def test_a_plain_error_while_opening_the_page_fails_cleanly(prepared) -> None:
    _approve(prepared)
    _live(prepared)

    def factory(_url: str):
        raise RuntimeError("browser executable missing")

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=factory)
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "FAILED"
    assert _status(prepared) == ApplicationStatus.FAILED.value


def test_a_second_submit_during_applying_is_refused(prepared, greenhouse_form_capture) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    handoff = FakeHandoff()
    service = _service(prepared, adapter, handoff=handoff, background_handoff=True)
    assert service.submit(prepared.application_id, allow_real_submission=True).outcome == (
        "HANDED_TO_HUMAN"
    )
    assert handoff.started.wait(5)
    with pytest.raises(ValidationFailure) as excinfo:
        service.submit(prepared.application_id, allow_real_submission=True)
    assert "already in progress" in str(excinfo.value)
    # Dry runs are refused too: nothing may start while a submission is in flight.
    with pytest.raises(ValidationFailure):
        service.submit(prepared.application_id)
    HANDOFFS.get(prepared.application_id).control.report("abandoned")
    HANDOFFS.get(prepared.application_id).thread.join(5)
    assert _status(prepared) == ApplicationStatus.BLOCKED.value


def test_claim_is_a_compare_and_set_through_the_graph(prepared) -> None:
    _approve(prepared)
    with prepared.ctx.repos() as repos:
        assert repos.applications.claim_status(
            prepared.application_id,
            expected=ApplicationStatus.APPROVED,
            target=ApplicationStatus.APPLYING,
        )
        # The loser of a race finds the row already moved.
        assert not repos.applications.claim_status(
            prepared.application_id,
            expected=ApplicationStatus.APPROVED,
            target=ApplicationStatus.APPLYING,
        )
        with pytest.raises(InvalidTransition):
            repos.applications.claim_status(
                prepared.application_id,
                expected=ApplicationStatus.APPLYING,
                target=ApplicationStatus.AWAITING_APPROVAL,
            )


def test_an_unconfirmed_click_is_not_reauthorised_and_is_resolved_by_a_report(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _gh_adapter(_answerable(greenhouse_form_capture), captcha=False)
    service = _service(prepared, adapter)
    result = service.submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "UNCONFIRMED"
    assert _status(prepared) == ApplicationStatus.APPLYING.value

    with pytest.raises(ApprovalRequired):
        ApprovalService(prepared.ctx).assert_submission_authorised(prepared.application_id)
    with pytest.raises(ValidationFailure):
        service.submit(prepared.application_id, allow_real_submission=True)

    reported = service.report_handoff_outcome(prepared.application_id, "submitted")
    assert reported["application_status"] == "APPLIED"
    with prepared.ctx.read_repos() as repos:
        row = repos.applications.get(prepared.application_id)
        assert row.submission_method == "PLAYWRIGHT_ADAPTER"
        assert row.submission_time is not None


def test_a_person_reporting_not_submitted_after_an_unconfirmed_click_unblocks_it(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _gh_adapter(_answerable(greenhouse_form_capture), captcha=False)
    service = _service(prepared, adapter)
    assert service.submit(prepared.application_id, allow_real_submission=True).outcome == (
        "UNCONFIRMED"
    )
    service.report_handoff_outcome(prepared.application_id, "failed")
    assert _status(prepared) == ApplicationStatus.BLOCKED.value


def test_failed_is_authorised_only_with_no_submission_evidence(prepared) -> None:
    _approve(prepared)
    approval = ApprovalService(prepared.ctx)
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
        repos.applications.set_status(prepared.application_id, ApplicationStatus.FAILED)
    approval.assert_submission_authorised(prepared.application_id)  # a clean failure may retry

    with prepared.ctx.repos() as repos:
        repos.events.append(
            "application", prepared.application_id, EventType.APPLICATION_UNCONFIRMED, {}
        )
    with pytest.raises(ApprovalRequired):
        approval.assert_submission_authorised(prepared.application_id)


def test_blocked_with_a_recorded_submission_is_not_authorised(prepared) -> None:
    _approve(prepared)
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
        repos.applications.set_status(prepared.application_id, ApplicationStatus.BLOCKED)
        repos.events.append(
            "application", prepared.application_id, EventType.APPLICATION_SUBMITTED, {}
        )
    with pytest.raises(ApprovalRequired):
        ApprovalService(prepared.ctx).assert_submission_authorised(prepared.application_id)


def test_applying_is_never_authorised(prepared) -> None:
    _approve(prepared)
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
    with pytest.raises(ApprovalRequired):
        ApprovalService(prepared.ctx).assert_submission_authorised(prepared.application_id)


def test_blocked_to_applying_is_the_retry_edge_and_blocked_cannot_skip_approval() -> None:
    assert application_path(ApplicationStatus.BLOCKED, ApplicationStatus.APPLYING) == [
        ApplicationStatus.APPLYING
    ]
    with pytest.raises(InvalidTransition):
        application_path(ApplicationStatus.APPROVED, ApplicationStatus.AWAITING_APPROVAL)


def test_dead_code_is_gone() -> None:
    from sabra_automation.base import ApplicationAdapter

    for name in ("inspect_application", "validate_application"):
        assert not hasattr(ExecutionService, name)
    for name in ("require_approval", "capture_confirmation"):
        assert not hasattr(ApplicationAdapter, name)


# ---------------------------------------------------------------------------
# 5. no unverified identity on the form
# ---------------------------------------------------------------------------


def _identity_form() -> ApplicationForm:
    return ApplicationForm(
        url=GH_URL,
        fields=[
            FormField(name="first_name", label="First Name", required=True, selector="#first_name"),
            FormField(name="last_name", label="Last Name", required=True, selector="#last_name"),
            FormField(name="email", label="Email", required=True, selector="#email"),
            FormField(name="country", label="Country", required=True, selector="#country"),
        ],
        submit_selector='button[type="submit"]',
    )


def test_identity_is_never_read_out_of_the_resume() -> None:
    adapter = GreenhouseApplicationAdapter(dry_run=True)
    package = _pkg(applicant_email=None, applicant_name=None, applicant_country=None)
    assert "@" in (package.tailored_resume_markdown or "")
    assert GreenhouseApplicationAdapter._identity_values(package) == {"phone": "(214) 555-0142"}

    report = adapter.validate(package, _identity_form())
    assert not report.ok
    joined = " ".join(report.unknown_required_fields).lower()
    for name in ("email", "first_name", "last_name", "country"):
        assert name in joined, (name, report.unknown_required_fields)
    assert any(s.kind == SafetyStopKind.UNKNOWN_REQUIRED_QUESTION for s in report.stops)


def test_resume_email_is_not_typed_onto_the_form(tmp_path) -> None:
    page = FakeFormPage(
        {
            "fields": [
                {"id": "email", "tag": "input", "type": "email", "required": True, "label": "Email"},
                {"id": "first_name", "tag": "input", "type": "text", "required": True},
            ],
            "title": "Job Application for X at Y",
        },
        captcha=False,
    )
    adapter = GreenhouseApplicationAdapter(
        dry_run=True,
        page_factory=lambda _url: page,
        settings=type("S", (), {"artifacts_dir": tmp_path})(),
    )
    job = build_job()
    form = adapter.inspect(job)
    prepared_form = adapter.prepare(_pkg(applicant_email=None, applicant_name=None), form)
    assert "email" not in prepared_form.filled
    assert "first_name" not in prepared_form.filled
    assert "#email" not in page.filled
    assert "email" in prepared_form.skipped


def test_verified_answers_still_supply_identity_and_unverified_ones_do_not() -> None:
    package = _pkg(
        applicant_email=None,
        answers=[
            ApplicationAnswerSchema(
                question_key="email",
                question_text="Email",
                answer="v@x.test",
                level=TruthLevel.VERIFIED,
            ),
            ApplicationAnswerSchema(
                question_key="phone",
                question_text="Phone",
                answer="555",
                requires_user_input=True,
            ),
        ],
        applicant_phone=None,
    )
    values = GreenhouseApplicationAdapter._identity_values(package)
    assert values["email"] == "v@x.test"
    assert "phone" not in values


def test_lever_blocks_on_missing_identity_and_never_reads_the_resume() -> None:
    adapter = LeverApplicationAdapter(dry_run=True)
    form = ApplicationForm(
        url="https://jobs.lever.co/x/1/apply",
        fields=[
            FormField(name="name", label="Full name", required=True, selector="#name"),
            FormField(name="email", label="Email", required=True, selector="#email"),
        ],
        submit_selector='button[type="submit"]',
    )
    package = _pkg(applicant_email=None, applicant_name=None)
    report = adapter.validate(package, form)
    assert not report.ok
    joined = " ".join(report.unknown_required_fields).lower()
    assert "email" in joined and "name" in joined


# ---------------------------------------------------------------------------
# 6. the approval hash is recomputed
# ---------------------------------------------------------------------------


def test_a_payload_edited_without_the_hash_column_is_refused(prepared) -> None:
    _approve(prepared)
    approval = ApprovalService(prepared.ctx)
    approval.assert_submission_authorised(prepared.application_id)
    with prepared.ctx.repos() as repos:
        row = repos.packages.get(prepared.package_id)
        payload = dict(row.payload)
        payload["cover_letter"] = (payload.get("cover_letter") or "") + "\nSneaked in."
        row.payload = payload
        repos.session.flush()
        assert row.content_hash == prepared.package_hash  # the column was left alone
    with pytest.raises(ApprovalScopeMismatch) as excinfo:
        approval.assert_submission_authorised(prepared.application_id)
    assert "payload" in str(excinfo.value)


def test_recomputed_hash_matches_for_an_untouched_package(prepared) -> None:
    _approve(prepared)
    with prepared.ctx.read_repos() as repos:
        row = repos.packages.get(prepared.package_id)
        schema = repos.packages.schema(row)
        assert content_hash(schema.semantic_payload()) == row.content_hash


def test_approve_refuses_a_package_that_is_not_the_applications_current_one(prepared) -> None:
    from sabra_engine.documents import DocumentService

    old_package_id = prepared.package_id
    newer = DocumentService(prepared.ctx).prepare_package(prepared.opportunity_id)
    assert newer.package_id != old_package_id
    with pytest.raises(ValidationFailure) as excinfo:
        ApprovalService(prepared.ctx).approve(
            old_package_id, approved_by="test", acknowledge_unresolved=True
        )
    assert "current package" in str(excinfo.value)
    # The current one is approvable.
    ApprovalService(prepared.ctx).approve(
        newer.package_id, approved_by="test", acknowledge_unresolved=True
    )


# ---------------------------------------------------------------------------
# 7. field enumeration returns a name
# ---------------------------------------------------------------------------


def test_enumeration_script_returns_name_and_aria_label() -> None:
    assert "name: el.getAttribute('name')" in ENUMERATE_FIELDS_JS
    assert "ariaLabel:" in ENUMERATE_FIELDS_JS


def test_no_field_is_named_unnamed_when_a_name_or_label_exists(greenhouse_form_capture) -> None:
    from fake_pages import make_adapter

    adapter, _pages, _ = make_adapter(greenhouse_form_capture)
    form = adapter.inspect(build_job())
    raw = {i: f for i, f in enumerate(greenhouse_form_capture["fields"])}
    assert len(form.fields) == len(raw)
    for field, entry in zip(form.fields, raw.values(), strict=True):
        findable = entry.get("id") or entry.get("name") or entry.get("label") or entry.get("ariaLabel")
        if findable or entry.get("placeholder"):
            assert not field.name.startswith("unnamed"), (field.name, entry)
        else:
            # Nothing on the element names it, so its position does.
            assert field.name.startswith("unnamed-")
    # Every named field is addressable.
    assert all(f.selector for f in form.fields if f.name == "g-recaptcha-response-100000")


def test_field_naming_and_selector_precedence() -> None:
    to_field = GreenhouseApplicationAdapter._to_field
    by_id = to_field({"id": "email", "name": "n", "label": "L", "tag": "input", "type": "email"})
    assert (by_id.name, by_id.selector) == ("email", "#email")
    by_name = to_field({"id": None, "name": "job[x]", "label": "Why us?", "tag": "textarea"})
    assert (by_name.name, by_name.selector) == ("job[x]", '[name="job[x]"]')
    by_aria = to_field({"ariaLabel": 'Say "hi"', "tag": "input", "required": True})
    assert by_aria.name == 'Say "hi"'
    assert by_aria.selector == '[aria-label="Say \\"hi\\""]'
    by_label = to_field({"label": "Preferred pronouns", "tag": "input"})
    assert by_label.name == "Preferred pronouns" and by_label.selector is None
    odd_id = to_field({"id": "5th:field", "tag": "input"})
    assert odd_id.selector == '[id="5th:field"]'
    nothing = to_field({"tag": "input", "index": 7, "context": "Phone country"})
    # Container text is shown to a human but never used as the field's own label.
    assert nothing.name == "unnamed-input-7" and nothing.label is None
    assert nothing.context == "Phone country"


def test_a_blocked_anonymous_field_is_named_in_the_report() -> None:
    adapter = GreenhouseApplicationAdapter(dry_run=True)
    anon = GreenhouseApplicationAdapter._to_field(
        {"tag": "input", "index": 3, "required": True, "context": "Pronouns"}
    )
    form = ApplicationForm(url=GH_URL, fields=[anon], submit_selector='button[type="submit"]')
    report = adapter.validate(_pkg(), form)
    assert not report.ok
    assert any("unnamed-input-3" in item for item in report.unknown_required_fields)


# ---------------------------------------------------------------------------
# 8. sqlite and paths
# ---------------------------------------------------------------------------


def test_default_database_url_is_anchored_on_the_data_dir_not_the_cwd(
    tmp_path, monkeypatch
) -> None:
    from sabra_domain.settings import REPO_ROOT, Settings

    for name in ("SABRA_DATABASE_URL", "SABRA_DATA_DIR", "SABRA_PRIVATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    here = Settings(_env_file=None)
    assert here.database_url == f"sqlite+pysqlite:///{(REPO_ROOT / 'var' / 'sabra.db').as_posix()}"
    monkeypatch.chdir(tmp_path.parent)
    assert Settings(_env_file=None).database_url == here.database_url

    private = tmp_path / "hallam-private"
    assert Settings(private_dir=private, _env_file=None).database_url.endswith(
        f"{private.as_posix()}/var/sabra.db"
    )
    data = tmp_path / "data"
    assert Settings(data_dir=data, _env_file=None).database_url.endswith(
        f"{data.as_posix()}/sabra.db"
    )
    explicit = "sqlite+pysqlite:///C:/x/custom.db"
    assert Settings(database_url=explicit, _env_file=None).database_url == explicit


def test_handoff_timeout_defaults_to_forty_five_minutes() -> None:
    from sabra_domain.settings import Settings

    assert Settings(_env_file=None).handoff_timeout_seconds == 2700


def test_sqlite_connections_wait_for_a_locked_database(tmp_path) -> None:
    from sabra_domain.db import Database
    from sqlalchemy import text

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'wait.db'}")
    try:
        with database.read_session() as session:
            assert session.execute(text("PRAGMA busy_timeout")).scalar() == 30000
    finally:
        database.dispose()


def test_locked_database_maps_to_503() -> None:
    import asyncio

    from sabra_api.main import database_error_handler
    from sqlalchemy.exc import OperationalError

    locked = OperationalError("UPDATE x", {}, Exception("database is locked"))
    response = asyncio.run(database_error_handler(None, locked))
    assert response.status_code == 503
    assert b"another Sabra process" in response.body

    other = OperationalError("SELECT x", {}, Exception("no such table: x"))
    assert asyncio.run(database_error_handler(None, other)).status_code == 500


# ---------------------------------------------------------------------------
# B1. an outcome report can never land while a run is still in flight
# ---------------------------------------------------------------------------


def test_a_report_or_second_apply_during_a_synchronous_run_is_refused(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    seen: dict[str, Any] = {}
    holder: dict[str, ExecutionService] = {}

    def factory(_url: str) -> FakeFormPage:
        service = holder["service"]
        calls = (
            ("report", lambda: service.report_handoff_outcome(prepared.application_id, "submitted")),
            ("apply", lambda: service.submit(prepared.application_id, allow_real_submission=True)),
        )
        for name, call in calls:
            try:
                call()
            except ValidationFailure as exc:
                seen[name] = str(exc)
        seen["status"] = service.handoff_status(prepared.application_id)
        return FakeFormPage(_answerable(greenhouse_form_capture), captcha=False)

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=factory)
    holder["service"] = _service(prepared, adapter)
    holder["service"].submit(prepared.application_id, allow_real_submission=True)

    assert "already in progress" in seen["report"]
    assert "already in progress" in seen["apply"]
    live = seen["status"]
    assert live["state"] == "running" and live["awaiting_person"] is False
    assert live["running"] is True
    # The run registry is cleared afterwards, whatever the outcome.
    assert not HANDOFFS.run_in_progress(prepared.application_id)


def test_a_run_is_registered_before_the_claim_and_cleared_after(prepared) -> None:
    manager = HandoffManager()
    manager.begin_run("a")
    with pytest.raises(ValidationFailure):
        manager.begin_run("a")
    assert manager.run_in_progress("a")
    manager.end_run("a")
    manager.begin_run("a")

    _approve(prepared)
    _live(prepared)

    def no_browser(_url: str):
        raise RuntimeError("no browser")

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=no_browser)
    service = _service(prepared, adapter, handoffs=manager)
    manager.begin_run(prepared.application_id)  # another run is in flight
    with pytest.raises(ValidationFailure):
        service.submit(prepared.application_id, allow_real_submission=True)
    assert _status(prepared) == ApplicationStatus.APPROVED.value  # nothing was claimed
    manager.end_run(prepared.application_id)
    result = service.submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "FAILED"
    assert not manager.run_in_progress(prepared.application_id)  # cleared in `finally`


def test_a_report_from_another_process_is_refused_while_the_window_is_open(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    handoff = FakeHandoff()
    service = _service(prepared, adapter, handoff=handoff, background_handoff=True)
    service.submit(prepared.application_id, allow_real_submission=True)
    assert handoff.started.wait(5)

    # A second process has no registry entry, but the event log says the window is open.
    other = ExecutionService(
        prepared.ctx, registry=AdapterRegistry([adapter]), handoffs=HandoffManager()
    )
    with pytest.raises(ValidationFailure) as excinfo:
        other.report_handoff_outcome(prepared.application_id, "abandoned")
    assert "no submission waiting" in str(excinfo.value)
    assert _status(prepared) == ApplicationStatus.APPLYING.value

    service.report_handoff_outcome(prepared.application_id, "abandoned")
    assert _status(prepared) == ApplicationStatus.BLOCKED.value


class _MovingHandoff:
    """Moves the application off APPLYING mid-handoff, as a cross-process report would."""

    def __init__(self, prepared, then: HandoffResult) -> None:
        self.prepared, self.then = prepared, then

    def run(self, **_kwargs: Any) -> HandoffResult:
        with self.prepared.ctx.repos() as repos:
            repos.applications.set_status(self.prepared.application_id, ApplicationStatus.BLOCKED)
        return self.then


def test_a_confirmation_after_a_cross_process_report_is_still_in_the_ledger(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    confirmed = HandoffResult(
        outcome="submitted", source="confirmation_page", url=GH_URL + "/confirmation"
    )
    result = _service(prepared, adapter, handoff=_MovingHandoff(prepared, confirmed)).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "CONFLICT"
    late = [
        e
        for e in _events(prepared)
        if e.event_type == "APPLICATION_SUBMITTED" and e.payload.get("late_confirmation")
    ]
    assert late and late[0].payload["conflicting_status"] == "BLOCKED"
    with prepared.ctx.read_repos() as repos:
        row = repos.applications.get(prepared.application_id)
        assert row.status == ApplicationStatus.APPLIED.value  # the ledger tells the truth
        assert row.submission_time is not None


def test_a_non_submitted_result_after_a_move_is_noted_not_applied(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    result = _service(
        prepared,
        adapter,
        handoff=_MovingHandoff(prepared, HandoffResult(outcome="abandoned", source="user_report")),
    ).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "CONFLICT"
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    assert "APPLICATION_SUBMITTED" not in _event_types(prepared)


def test_the_claim_is_rechecked_immediately_before_the_click(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)

    class MovesBeforeClick(FakeFormPage):
        moved = False

        def set_input_files(self, selector: str, path: str) -> None:
            super().set_input_files(selector, path)
            if self.moved:
                return
            self.moved = True
            with prepared.ctx.repos() as repos:
                repos.applications.set_status(prepared.application_id, ApplicationStatus.BLOCKED)

    pages: list[MovesBeforeClick] = []

    def factory(_url: str) -> MovesBeforeClick:
        page = MovesBeforeClick(_answerable(greenhouse_form_capture), captcha=False)
        pages.append(page)
        return page

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=factory)
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "CONFLICT", result.detail
    assert pages[0].clicked == [], "no click once the claim was lost"
    assert "claim_lost" in [e.payload.get("stage") for e in _events(prepared)], result.detail
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    stages = [e.payload.get("stage") for e in _events(prepared)]
    assert "claim_lost" in stages


def test_a_receipt_for_a_row_that_moved_never_overwrites_it(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)

    class Page(FakeFormPage):
        def click(self, selector: str) -> None:
            with prepared.ctx.repos() as repos:
                repos.applications.set_status(prepared.application_id, ApplicationStatus.BLOCKED)
            super().click(selector)

    adapter = GreenhouseApplicationAdapter(
        dry_run=False,
        page_factory=lambda _u: Page(_answerable(greenhouse_form_capture), captcha=False),
    )
    # No confirmation: the click is recorded as a conflict, the row is left alone, and
    # the application is not re-authorised because the click may have gone through.
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "CONFLICT"
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    conflict = [e for e in _events(prepared) if e.event_type == "APPLICATION_UNCONFIRMED"]
    assert conflict and conflict[-1].payload["conflict"] is True
    with pytest.raises(ApprovalRequired):
        ApprovalService(prepared.ctx).assert_submission_authorised(prepared.application_id)


def test_a_confirmed_receipt_for_a_row_that_moved_is_recorded_late(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)

    class Page(FakeFormPage):
        def click(self, selector: str) -> None:
            with prepared.ctx.repos() as repos:
                repos.applications.set_status(prepared.application_id, ApplicationStatus.BLOCKED)
            super().click(selector)

    adapter = GreenhouseApplicationAdapter(
        dry_run=False,
        page_factory=lambda _u: Page(
            _answerable(greenhouse_form_capture),
            captcha=False,
            confirmation_url=GH_URL + "/confirmation",
        ),
    )
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "CONFLICT"
    late = [e for e in _events(prepared) if e.payload.get("late_confirmation")]
    assert late and late[0].event_type == "APPLICATION_SUBMITTED"
    assert _status(prepared) == ApplicationStatus.APPLIED.value


def test_unconfirmed_resolution_is_order_aware(prepared) -> None:
    _approve(prepared)
    approval = ApprovalService(prepared.ctx)

    def add(event: EventType, **payload: Any) -> None:
        with prepared.ctx.repos() as repos:
            repos.events.append("application", prepared.application_id, event, payload)

    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
    add(EventType.APPLICATION_STARTED)
    add(EventType.APPLICATION_UNCONFIRMED, message="clicked")
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.BLOCKED)
    with pytest.raises(ApprovalRequired):  # BLOCKED too, not only FAILED
        approval.assert_submission_authorised(prepared.application_id)

    # A person's report after it resolves it.
    add(EventType.APPLICATION_ABANDONED, source="user_report")
    approval.assert_submission_authorised(prepared.application_id)

    # A later attempt that goes unconfirmed again is unresolved again.
    add(EventType.APPLICATION_STARTED)
    add(EventType.APPLICATION_UNCONFIRMED, message="clicked again")
    with pytest.raises(ApprovalRequired):
        approval.assert_submission_authorised(prepared.application_id)

    # And an UNCONFIRMED from an earlier attempt does not poison a clean new one.
    add(EventType.APPLICATION_ABANDONED, source="user_report")
    add(EventType.APPLICATION_STARTED)
    approval.assert_submission_authorised(prepared.application_id)


def test_the_handoff_closes_itself_when_the_application_is_resolved_elsewhere(browser) -> None:
    control = HandoffControl(5)
    control.superseded = lambda: True
    result = _run_handoff(_FillingAdapter(), control=control)
    assert result.outcome == "superseded"
    assert browser.instances[0].closed is True


def test_the_handoff_thread_watches_the_database_status(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    probes: dict[str, bool] = {}

    class Watching:
        def run(self, *, control, **_kwargs: Any) -> HandoffResult:
            probes["before"] = control.superseded()
            with prepared.ctx.repos() as repos:
                repos.applications.set_status(prepared.application_id, ApplicationStatus.BLOCKED)
            probes["after"] = control.superseded()
            return HandoffResult(outcome="superseded", source="superseded")

    service = _service(prepared, adapter, handoff=Watching(), background_handoff=True)
    service.submit(prepared.application_id, allow_real_submission=True)
    record = HANDOFFS.get(prepared.application_id)
    record.thread.join(5)
    assert probes == {"before": False, "after": True}
    assert record.state == "superseded"


def test_a_handoff_thread_that_dies_early_goes_to_error_and_can_be_reported(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))

    def broken_context():
        raise RuntimeError("database gone")

    service = _service(
        prepared,
        adapter,
        handoff=FakeHandoff(),
        background_handoff=True,
        ctx_factory=broken_context,
    )
    assert service.submit(prepared.application_id, allow_real_submission=True).outcome == (
        "HANDED_TO_HUMAN"
    )
    record = HANDOFFS.get(prepared.application_id)
    record.thread.join(5)
    assert record.state == "error" and "database gone" in (record.detail or "")
    handed = [e for e in _events(prepared) if e.event_type == "HANDED_TO_HUMAN"]
    assert handed[-1].payload["state"] == "error"
    # No window will ever report, so a person's report is accepted directly.
    status = service.report_handoff_outcome(prepared.application_id, "abandoned")
    assert status["application_status"] == "BLOCKED"


def test_the_server_stopping_is_recorded_so_a_person_can_report(
    prepared, greenhouse_form_capture, monkeypatch
) -> None:
    from sabra_api.main import record_server_stopped

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    handoff = FakeHandoff()
    service = _service(prepared, adapter, handoff=handoff, background_handoff=True)
    service.submit(prepared.application_id, allow_real_submission=True)
    assert handoff.started.wait(5)

    assert record_server_stopped() == 1
    handed = [e for e in _events(prepared) if e.event_type == "HANDED_TO_HUMAN"]
    assert handed[-1].payload["state"] == "server_stopped"
    # A fresh process (no registry) may now report.
    fresh = ExecutionService(
        prepared.ctx, registry=AdapterRegistry([adapter]), handoffs=HandoffManager()
    )
    reported = fresh.report_handoff_outcome(prepared.application_id, "failed")
    assert reported["application_status"] == "FAILED"
    HANDOFFS.get(prepared.application_id).control.report("abandoned")
    HANDOFFS.get(prepared.application_id).thread.join(5)


def test_hand_to_human_does_not_claim_the_browser_opened_before_it_did(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    _service(
        prepared, adapter, handoff=ScriptedHandoff(HandoffResult("abandoned", "user_report"))
    ).submit(prepared.application_id, allow_real_submission=True)
    handed = [e for e in _events(prepared) if e.event_type == "HANDED_TO_HUMAN"]
    assert handed[0].payload["state"] == "starting"
    assert not any(e.payload.get("opened") for e in handed)


# ---------------------------------------------------------------------------
# S1 / S2 / S5 and nits
# ---------------------------------------------------------------------------


def test_prepare_outside_a_session_without_a_factory_never_opens_a_browser(monkeypatch) -> None:
    launched: list[bool] = []

    class Boom:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            launched.append(True)

    monkeypatch.setattr("sabra_automation.playwright_driver.BrowserSession", Boom)
    adapter = GreenhouseApplicationAdapter(dry_run=False)
    with pytest.raises(SafetyStop) as excinfo:
        adapter.prepare(_pkg(), _identity_form())
    assert "live session" in excinfo.value.message
    assert launched == []


def test_submit_clicks_the_control_inspect_found_not_the_first_selector(
    greenhouse_form_capture,
) -> None:
    adapter, pages = _gh_adapter(
        _answerable(greenhouse_form_capture),
        captcha=False,
        submit_selector="#submit_app",
        confirmation_url=GH_URL + "/confirmation",
    )
    job = build_job()
    with adapter.session(adapter.application_url(job)):
        form = adapter.inspect(job)
        assert form.submit_selector == "#submit_app"
        prepared_form = adapter.prepare(_pkg(), form)
        assert prepared_form.submit_selector == "#submit_app"
        receipt = adapter.submit(prepared_form)
    assert pages[0].clicked == ["#submit_app"]
    assert receipt.submitted is True


def test_submit_refuses_a_control_that_matches_more_than_one_element(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, pages = _gh_adapter(
        _answerable(greenhouse_form_capture), captcha=False, submit_count=2
    )
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "BLOCKED"
    assert "matches 2 elements" in (result.detail or "")
    assert pages[0].clicked == []
    assert _status(prepared) == ApplicationStatus.BLOCKED.value


def test_the_run_types_the_package_the_claim_verified(prepared, greenhouse_form_capture) -> None:
    class SwappingApproval(ApprovalService):
        def assert_submission_authorised(self, application_id, *, repos=None):
            application, schema = super().assert_submission_authorised(
                application_id, repos=repos
            )
            if repos is not None:  # the locked authorisation inside the claim
                schema = schema.model_copy(update={"cover_letter": "CLAIMED CONTENT"})
            return application, schema

    _approve(prepared)
    _live(prepared)
    seen: list[str | None] = []
    adapter, _ = _gh_adapter(_answerable(greenhouse_form_capture), captcha=False)
    original = adapter.prepare

    def spy(package, form):
        seen.append(package.cover_letter)
        return original(package, form)

    adapter.prepare = spy  # type: ignore[method-assign]
    _service(prepared, adapter, approval=SwappingApproval(prepared.ctx)).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert seen == ["CLAIMED CONTENT"]


def test_click_attempted_is_reset_before_a_new_session_opens() -> None:
    def broken(_url: str):
        raise RuntimeError("no page")

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=broken)
    adapter.click_attempted = True
    with pytest.raises(RuntimeError), adapter.session("https://x.test"):
        pass
    assert adapter.click_attempted is False


def test_aria_label_newlines_are_escaped_in_selectors() -> None:
    field = GreenhouseApplicationAdapter._to_field(
        {"ariaLabel": "Line one\nLine two", "tag": "input", "required": True}
    )
    assert "\n" not in (field.selector or "")
    assert field.selector == '[aria-label="Line one\\a Line two"]'


def test_blocked_and_failed_rows_say_nothing_is_recorded_as_submitted(
    prepared, monkeypatch
) -> None:
    from sabra_api.main import app

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    _approve(prepared)
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
        repos.applications.set_status(prepared.application_id, ApplicationStatus.BLOCKED)
    rows = TestClient(app).get("/api/packages").json()["packages"]
    assert "Nothing is recorded as submitted" in rows[0]["next_step"]


def test_relative_data_dir_and_unc_paths_make_an_absolute_database_url(monkeypatch) -> None:
    import os

    from sabra_domain.settings import REPO_ROOT, Settings
    from sqlalchemy.engine import make_url

    for name in ("SABRA_DATABASE_URL", "SABRA_DATA_DIR", "SABRA_PRIVATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    relative = Settings(data_dir=Path("custom-var"), _env_file=None)
    assert relative.data_dir == REPO_ROOT / "custom-var"
    assert relative.database_url.endswith("/custom-var/sabra.db")
    if os.name == "nt":
        unc = Settings(data_dir=Path(r"\\server\share\sabra"), _env_file=None)
        assert make_url(unc.database_url).database == "//server/share/sabra/sabra.db"


# ---------------------------------------------------------------------------
# S3. the handoff snapshot is taken lazily, from the real form
# ---------------------------------------------------------------------------


def _interstitial() -> LivePage:
    page = LivePage(GH_FORM + "/verify")
    page.form = 0
    page.marker_count = 1  # a stray marker on the interstitial
    page.body = "Thank you for applying"
    return page


def test_an_interstitial_is_never_the_baseline(browser) -> None:
    page = browser.page = _interstitial()

    def confirm() -> None:
        page.url, page.form = GH_FORM + "/confirmation", 0

    def reach_form() -> None:
        page.marker_count, page.url, page.form = 0, GH_FORM, 1  # the person reaches the form
        threading.Timer(0.3, confirm).start()

    threading.Timer(0.2, reach_form).start()
    result = _run_handoff(_FillingAdapter(), control=HandoffControl(5))
    assert result.outcome == "submitted" and result.source == "confirmation_page"


def test_jumping_from_an_interstitial_straight_to_a_marker_is_not_a_confirmation(browser) -> None:
    page = browser.page = _interstitial()

    def jump() -> None:
        page.url, page.marker_count = GH_FORM + "/confirmation", 1

    threading.Timer(0.1, jump).start()
    # The form was never on screen, so there is no baseline and nothing can confirm.
    result = _run_handoff(_FillingAdapter(), control=HandoffControl(0.5))
    assert result.outcome == "timeout"


def test_a_stray_marker_on_the_form_page_does_not_confirm_without_leaving(browser) -> None:
    browser.page.marker_count = 1  # a marker is on the page, but so is the form, same URL
    result = _run_handoff(_FillingAdapter(), control=HandoffControl(0.4))
    assert result.outcome == "timeout"


# ---------------------------------------------------------------------------
# S4. container text never classifies a field; the unreduced real form blocks
# ---------------------------------------------------------------------------


def test_container_text_is_context_not_a_label() -> None:
    adapter = GreenhouseApplicationAdapter(dry_run=True)
    anon = GreenhouseApplicationAdapter._to_field(
        {"tag": "input", "index": 12, "required": True, "context": "Are you 18 or older?"}
    )
    assert anon.label is None and anon.question_key is None
    form = ApplicationForm(url=GH_URL, fields=[anon], submit_selector='button[type="submit"]')
    report = adapter.validate(_pkg(), form)
    assert not report.ok
    assert report.mapped_fields == {}
    assert any(
        "unnamed-input-12 (near: Are you 18 or older?)" in item
        for item in report.unknown_required_fields
    )


def test_the_unreduced_real_form_blocks_and_names_the_anonymous_required_fields(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    capture = {
        **greenhouse_form_capture,
        "fields": [dict(f) for f in greenhouse_form_capture["fields"]],
    }
    assert len(capture["fields"]) == 51
    anonymous = [
        f
        for f in capture["fields"]
        if f.get("required")
        and not (f.get("id") or f.get("name") or f.get("label") or f.get("ariaLabel"))
    ]
    assert len(anonymous) >= 9
    anonymous[0]["context"] = "Are you 18 or older?"
    adapter, pages = _gh_adapter(capture, captcha=False)
    result = _service(prepared, adapter).submit(prepared.application_id, allow_real_submission=True)

    assert result.outcome == "BLOCKED", (result.outcome, result.detail)
    assert result.validation is not None
    named = " | ".join(result.validation.unknown_required_fields)
    assert named.count("unnamed-input-") >= len(anonymous)
    assert "(near: Are you 18 or older?)" in named
    assert "age_eligibility" not in result.validation.mapped_fields.values()
    assert pages[0].clicked == [] and pages[0].filled == {} and pages[0].files == {}
    assert _status(prepared) == ApplicationStatus.BLOCKED.value


def test_prepare_reports_required_fields_it_left_empty_and_submit_refuses(
    greenhouse_form_capture,
) -> None:
    adapter, pages = _gh_adapter(_answerable(greenhouse_form_capture), captcha=False)
    job = build_job()
    with adapter.session(adapter.application_url(job)):
        real = adapter.inspect(job)
        form = ApplicationForm(
            url=real.url,
            fields=[
                *real.fields,
                FormField(name="mystery", label="Mystery", required=True, selector="#mystery"),
                FormField(name="resume_file", label="CV", kind="file", required=True),
            ],
            submit_selector=real.submit_selector,
        )
        prepared_form = adapter.prepare(_pkg(), form)
        assert prepared_form.unfilled_required == ["Mystery [mystery]"]
        with pytest.raises(SafetyStop) as excinfo:
            adapter.submit(prepared_form)
        assert "Mystery" in excinfo.value.message
    assert pages[0].clicked == []


# ---------------------------------------------------------------------------
# Final loop: interrupted runs, forced reports, prompt timeout, lazy CLI baseline
# ---------------------------------------------------------------------------


def _stuck_applying(prepared) -> None:
    """A hard-killed process: APPLYING, STARTED is the last event, nothing registered."""

    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
        repos.events.append("application", prepared.application_id, EventType.APPLICATION_STARTED, {})


def test_ctrl_c_during_the_drive_is_recorded_and_the_row_can_be_reported(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)

    def interrupted(_url: str):
        raise KeyboardInterrupt

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=interrupted)
    service = _service(prepared, adapter)
    with pytest.raises(KeyboardInterrupt):
        service.submit(prepared.application_id, allow_real_submission=True)
    assert not HANDOFFS.run_in_progress(prepared.application_id)
    assert _status(prepared) == ApplicationStatus.APPLYING.value
    last = _events(prepared)[-1]
    assert last.event_type == "HANDED_TO_HUMAN"
    assert last.payload["state"] == "interrupted"
    assert last.payload["exception"] == "KeyboardInterrupt"

    live = service.handoff_status(prepared.application_id)
    assert live["awaiting_person"] is True and live["state"] == "needs_report"
    reported = service.report_handoff_outcome(prepared.application_id, "abandoned")
    assert reported["application_status"] == "BLOCKED"


def test_a_hard_stuck_row_needs_a_forced_report_that_echoes_the_id(prepared) -> None:
    _approve(prepared)
    _stuck_applying(prepared)
    service = ExecutionService(prepared.ctx, handoffs=HandoffManager())

    # The normal report is refused: nothing says anyone is waiting.
    with pytest.raises(ValidationFailure):
        service.report_handoff_outcome(prepared.application_id, "abandoned")
    # So is a forced one with the wrong id, or none.
    for typed in ("", "app_wrong", prepared.application_id.upper()):
        with pytest.raises(ValidationFailure) as excinfo:
            service.force_report_handoff_outcome(
                prepared.application_id, "abandoned", confirm_id=typed
            )
        assert "typing the application id" in str(excinfo.value).lower()
    assert _status(prepared) == ApplicationStatus.APPLYING.value

    status = service.force_report_handoff_outcome(
        prepared.application_id, "abandoned", confirm_id=prepared.application_id
    )
    assert status["application_status"] == "BLOCKED"
    abandoned = [e for e in _events(prepared) if e.event_type == "APPLICATION_ABANDONED"][-1]
    assert abandoned.payload["forced"] is True
    assert abandoned.payload["previous_event"]["event_type"] == "APPLICATION_STARTED"
    # A forced report is still a person's report: it resolves the attempt for retry.
    ApprovalService(prepared.ctx).assert_submission_authorised(prepared.application_id)


def test_a_forced_report_of_submitted_walks_to_applied(prepared) -> None:
    _approve(prepared)
    _stuck_applying(prepared)
    service = ExecutionService(prepared.ctx, handoffs=HandoffManager())
    service.force_report_handoff_outcome(
        prepared.application_id, "submitted", confirm_id=prepared.application_id
    )
    with prepared.ctx.read_repos() as repos:
        row = repos.applications.get(prepared.application_id)
        assert row.status == ApplicationStatus.APPLIED.value
        assert row.submission_time is not None
    submitted = [e for e in _events(prepared) if e.event_type == "APPLICATION_SUBMITTED"][-1]
    assert submitted.payload["forced"] is True


def test_a_forced_report_is_refused_while_this_process_has_a_run_or_window(prepared) -> None:
    _approve(prepared)
    _stuck_applying(prepared)
    manager = HandoffManager()
    service = ExecutionService(prepared.ctx, handoffs=manager)
    manager.begin_run(prepared.application_id)
    with pytest.raises(ValidationFailure) as excinfo:
        service.force_report_handoff_outcome(
            prepared.application_id, "abandoned", confirm_id=prepared.application_id
        )
    assert "already in progress" in str(excinfo.value)
    manager.end_run(prepared.application_id)

    release = threading.Event()
    manager.start(
        prepared.application_id, url="u", timeout_seconds=5, target=lambda _r: release.wait(5)
    )
    with pytest.raises(ValidationFailure):
        service.force_report_handoff_outcome(
            prepared.application_id, "abandoned", confirm_id=prepared.application_id
        )
    release.set()
    manager.get(prepared.application_id).thread.join(5)
    assert _status(prepared) == ApplicationStatus.APPLYING.value


def test_the_dashboard_offers_a_forced_report_only_when_the_row_is_stale(
    prepared, monkeypatch
) -> None:
    import time

    from sabra_api.main import app

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    _approve(prepared)
    _stuck_applying(prepared)
    client = TestClient(app)
    url = f"/api/applications/{prepared.application_id}/handoff"

    fresh = client.get(url).json()
    assert fresh["can_force"] is False and fresh["awaiting_person"] is False
    too_soon = client.post(
        url + "/force",
        json={"outcome": "abandoned", "confirm_id": prepared.application_id},
    )
    assert too_soon.status_code == 422 and "not offered yet" in too_soon.json()["detail"]

    prepared.ctx.settings.handoff_timeout_seconds = 0
    time.sleep(0.02)
    stale = client.get(url).json()
    assert stale["can_force"] is True
    wrong = client.post(url + "/force", json={"outcome": "abandoned", "confirm_id": "nope"})
    assert wrong.status_code == 422
    done = client.post(
        url + "/force",
        json={"outcome": "abandoned", "confirm_id": prepared.application_id},
    )
    assert done.status_code == 200 and done.json()["application_status"] == "BLOCKED"


def test_the_applications_page_has_a_separate_forced_report_control() -> None:
    from sabra_api.main import app

    html = TestClient(app).get("/applications").text
    assert "Force a report" in html and "/handoff/force" in html
    assert "Type the application id to confirm" in html
    # one poller drives load(); in-flight Apply buttons are re-disabled after a reload
    assert "let polling=false" in html
    assert "inflight.forEach(id=>setApplyDisabled(id,true))" in html


def test_the_server_stopping_covers_a_synchronous_run(prepared, monkeypatch) -> None:
    from sabra_api.main import record_server_stopped

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    _approve(prepared)
    _stuck_applying(prepared)
    HANDOFFS.begin_run(prepared.application_id)  # a headless run, no window thread
    try:
        assert record_server_stopped() == 1
    finally:
        HANDOFFS.end_run(prepared.application_id)
    assert _events(prepared)[-1].payload["state"] == "server_stopped"
    ExecutionService(prepared.ctx, handoffs=HandoffManager()).report_handoff_outcome(
        prepared.application_id, "failed"
    )
    assert _status(prepared) == ApplicationStatus.FAILED.value


def test_cli_forced_report_needs_the_id_typed_back(prepared, monkeypatch) -> None:
    from sabra_engine import cli
    from typer.testing import CliRunner

    _approve(prepared)
    _stuck_applying(prepared)
    monkeypatch.setattr(cli, "_context", lambda: EngineContext.create(settings=prepared.ctx.settings))
    runner = CliRunner()
    base = ["handoff-outcome", prepared.application_id, "abandoned"]

    # `--yes` is not an option, and a normal report is refused for a stuck row.
    assert runner.invoke(cli.app, [*base, "--force", "--yes"]).exit_code == 2
    refused = runner.invoke(cli.app, base)
    assert isinstance(refused.exception, ValidationFailure)

    wrong = runner.invoke(cli.app, [*base, "--force"], input="not-the-id\n")
    assert isinstance(wrong.exception, ValidationFailure)
    assert _status(prepared) == ApplicationStatus.APPLYING.value

    right = runner.invoke(cli.app, [*base, "--force"], input=prepared.application_id + "\n")
    assert right.exit_code == 0, right.output
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    assert cli.prompt_for_forced_confirmation("a1", lambda _t: "  a1 ") == "a1"


def test_prompt_mode_honours_the_timeout(browser) -> None:
    release = threading.Event()
    handoff = PlaywrightHandoff(prompt=lambda: (release.wait(5), "submitted")[1], poll_seconds=0.01)
    result = handoff.run(
        adapter=_FillingAdapter(),
        package=object(),
        form=object(),
        url=GH_FORM,
        settings=None,
        control=HandoffControl(0.2),
    )
    release.set()
    assert result.outcome == "timeout"
    assert browser.instances[0].closed is True


def test_prompt_mode_timeout_blocks_the_application_like_the_dashboard(
    prepared, greenhouse_form_capture
) -> None:
    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    timeout = HandoffResult(outcome="timeout", source="timeout", error="no answer")
    _service(prepared, adapter, handoff=ScriptedHandoff(timeout)).submit(
        prepared.application_id, allow_real_submission=True
    )
    with prepared.ctx.read_repos() as repos:
        row = repos.applications.get(prepared.application_id)
        assert (row.status, row.blocked_reason) == ("BLOCKED", "HANDOFF_TIMEOUT")


def test_cli_prompt_never_trusts_an_interstitial_baseline(browser) -> None:
    page = browser.page = _interstitial()  # opens on a bot wall that carries a marker

    def answer() -> str:
        # By the time the person answers, the page shows a confirmation marker, but the
        # form was never on screen, so there is no baseline to compare against.
        page.url, page.form, page.marker_count = GH_FORM + "/confirmation", 0, 1
        return "abandoned"

    handoff = PlaywrightHandoff(prompt=answer, poll_seconds=0.01)
    result = handoff.run(
        adapter=_FillingAdapter(),
        package=object(),
        form=None,
        url=GH_FORM,
        settings=None,
        control=HandoffControl(5),
    )
    assert result.outcome == "abandoned" and result.source == "user_confirmation"


def test_a_failure_after_the_page_confirmed_keeps_what_the_page_showed(
    prepared, greenhouse_form_capture
) -> None:
    class Failing(ExecutionService):
        def _finish_handoff(self, *args: Any, **kwargs: Any):
            raise RuntimeError("ledger write failed")

    _approve(prepared)
    _live(prepared)
    adapter, _ = _captcha_adapter(_answerable(greenhouse_form_capture))
    confirmed = HandoffResult(
        outcome="submitted", source="confirmation_page", url=GH_URL + "/confirmation"
    )
    service = Failing(
        prepared.ctx,
        registry=AdapterRegistry([adapter]),
        handoff=ScriptedHandoff(confirmed),
        background_handoff=True,
    )
    service.submit(prepared.application_id, allow_real_submission=True)
    HANDOFFS.get(prepared.application_id).thread.join(5)
    error = [
        e
        for e in _events(prepared)
        if e.event_type == "HANDED_TO_HUMAN" and e.payload.get("state") == "error"
    ][-1]
    assert error.payload["outcome"] == "submitted"
    assert error.payload["source"] == "confirmation_page"
    assert error.payload["url"].endswith("/confirmation")
    assert "ledger write failed" in error.payload["error"]
