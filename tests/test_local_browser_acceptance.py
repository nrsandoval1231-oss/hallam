"""Real Chromium acceptance against synthetic loopback employer forms only.

These tests intentionally fail when Chromium is missing: install it with
`uv run playwright install chromium`. They never contact a real employer.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sabra_api.main import app
from sabra_automation.answer_match import write_value
from sabra_automation.base import FormField
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_automation.handoff import HandoffControl, PlaywrightHandoff
from sabra_automation.lever_adapter import LeverApplicationAdapter
from sabra_automation.playwright_driver import PlaywrightPage
from sabra_domain.enums import ApplicationStatus, TruthLevel
from sabra_domain.errors import ApprovalRequired
from sabra_domain.schemas import ApplicationAnswerSchema, ApplicationPackageSchema
from sabra_engine.approval import ApprovalService
from sabra_engine.execution import ExecutionService


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture()
def employer():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path in {"/confirmation", "/thanks"}:
                html = '<div id="application_confirmation">Application has been submitted</div>'
            else:
                html = """<!doctype html><title>Job Application for Synthetic Example</title>
                <form id="application-form" method="post" action="/submit">
                  <label for="first_name">First name</label><input id="first_name" name="first_name" required>
                  <label for="last_name">Last name</label><input id="last_name" name="last_name" required>
                  <label for="name">Full name</label><input id="name" name="name">
                  <label for="email">Email</label><input id="email" name="email" required>
                  <label for="phone">Phone</label><input id="phone" name="phone">
                  <label for="question_1">Do you require sponsorship?</label>
                  <input id="question_1" name="sponsorship" type="checkbox" checked>
                  <label for="question_2">Are you authorized to work?</label>
                  <select id="question_2" name="authorization" required>
                    <option value="">Choose</option><option value="1">Yes</option><option value="0">No</option>
                  </select>
                  <button type="submit">Submit application</button>
                </form>"""
            payload = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            received.append(parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()))
            self.send_response(303)
            self.send_header("Location", "/confirmation")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}/apply", received=received)
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture()
def browser_page(chromium, employer):
    context = chromium.new_context()
    # Permit only the fixture origin, including redirects and favicon requests.
    context.route(
        "**/*",
        lambda route: (
            route.continue_()
            if route.request.url.startswith(employer.url.rsplit("/", 1)[0] + "/")
            else route.abort()
        ),
    )
    raw = context.new_page()
    raw.goto(employer.url)
    yield raw
    context.close()


def package():
    def answer(key, text, value):
        return ApplicationAnswerSchema(
            question_key=key, question_text=text, answer=value, level=TruthLevel.VERIFIED
        )

    return ApplicationPackageSchema(
        opportunity_id="synthetic-opportunity",
        candidate_id="synthetic-candidate",
        job_id="synthetic-job",
        applicant_name="Synthetic Example",
        applicant_first_name="Synthetic",
        applicant_last_name="Example",
        applicant_email="current@example.test",
        applicant_phone="555-0100",
        applicant_country="Canada",
        answers=[
            answer("sponsorship", "Do you require sponsorship?", "No"),
            answer("work_authorization", "Are you authorized to work?", "Yes"),
            answer("email", "Email", "stale@example.test"),
            answer("phone", "Phone", "555-0199"),
        ],
    )


def test_browser_negative_checkbox_clears_default(browser_page):
    page = PlaywrightPage(browser_page, timeout_ms=500)
    assert browser_page.is_checked("#question_1")
    assert write_value(page, FormField("question_1", kind="checkbox"), "#question_1", "No")
    assert not browser_page.is_checked("#question_1")


def test_browser_select_matches_label_not_numeric_value(browser_page):
    page = PlaywrightPage(browser_page, timeout_ms=500)
    assert write_value(
        page,
        FormField("question_2", kind="select", options=["Choose", "Yes", "No"]),
        "#question_2",
        "Yes",
    )
    assert browser_page.input_value("#question_2") == "1"


@pytest.mark.parametrize("adapter_type", [GreenhouseApplicationAdapter, LeverApplicationAdapter])
def test_browser_adapter_fill_preserves_current_identity(
    browser_page, employer, tmp_path, adapter_type
):
    if adapter_type is LeverApplicationAdapter:
        browser_page.evaluate("""() => {
          document.querySelector('#first_name').removeAttribute('required');
          document.querySelector('#last_name').removeAttribute('required');
          document.querySelector('#name').required = true;
        }""")
    page = PlaywrightPage(browser_page, timeout_ms=700)
    adapter = adapter_type(dry_run=False, page_factory=lambda _url: page, evidence_dir=tmp_path)
    with adapter.session(employer.url):
        form = adapter.inspect(SimpleNamespace(source_url=employer.url))
        prepared = adapter.prepare(package(), form)
        assert not prepared.unfilled_required
        assert browser_page.input_value("#email") == "current@example.test"
        assert browser_page.input_value("#phone") == "555-0100"
        assert not browser_page.is_checked("#question_1")
        assert browser_page.input_value("#question_2") == "1"
        assert employer.received == []


def test_browser_locked_negative_checkbox_fails_without_submission(browser_page, employer):
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    browser_page.locator("#question_1").evaluate("el => el.disabled = true")
    with pytest.raises(PlaywrightTimeoutError):
        write_value(
            PlaywrightPage(browser_page, timeout_ms=100),
            FormField("question_1", kind="checkbox"),
            "#question_1",
            "No",
        )
    assert browser_page.is_checked("#question_1")
    assert employer.received == []


def test_browser_body_text_does_not_confirm_a_rejected_form(browser_page, employer, tmp_path):
    browser_page.evaluate("""() => document.querySelector('form').addEventListener('submit', e => {
      e.preventDefault();
      document.body.append('Thank you for applying. Please correct the form.');
    })""")
    page = PlaywrightPage(browser_page, timeout_ms=1000)
    adapter = GreenhouseApplicationAdapter(
        dry_run=False, page_factory=lambda _url: page, evidence_dir=tmp_path
    )
    with adapter.session(employer.url):
        form = adapter.inspect(SimpleNamespace(source_url=employer.url))
        prepared = adapter.prepare(package(), form)
        receipt = adapter._submit_live(prepared, settle_ms=0, capture_evidence=False)
    assert receipt.clicked and not receipt.submitted
    assert not receipt.external_confirmation["confirmation_detected"]
    assert employer.received == []


@pytest.mark.parametrize("failure", ["title", "screenshot"])
def test_browser_observed_confirmation_survives_metadata_failure(
    browser_page, employer, tmp_path, failure
):
    class EvidenceFailure(PlaywrightPage):
        def title(self):
            if failure == "title" and "/confirmation" in self._page.url:
                raise RuntimeError("synthetic closed-title error")
            return super().title()

        def screenshot(self, path):
            if failure == "screenshot" and "/confirmation" in self._page.url:
                raise RuntimeError("synthetic screenshot error")
            return super().screenshot(path)

    page = EvidenceFailure(browser_page, timeout_ms=1000)
    adapter = GreenhouseApplicationAdapter(
        dry_run=False,
        page_factory=lambda _url: page,
        evidence_dir=tmp_path,
        settings=SimpleNamespace(automation_screenshot_dir=tmp_path),
    )
    with adapter.session(employer.url):
        form = adapter.inspect(SimpleNamespace(source_url=employer.url))
        prepared = adapter.prepare(package(), form)
        receipt = adapter._submit_live(prepared, settle_ms=100, capture_evidence=True)
    assert receipt.clicked and receipt.submitted
    assert receipt.external_confirmation["confirmation_detected"]
    assert len(employer.received) == 1
    assert "sponsorship" not in employer.received[0]
    assert employer.received[0]["authorization"] == ["1"]
    assert employer.received[0]["email"] == ["current@example.test"]


def test_browser_fast_human_submission_cannot_become_retryable(browser_page, employer, prepared):
    # A real browser click occurs between the first and second observation polls.
    browser_page.fill("#first_name", "Synthetic")
    browser_page.fill("#last_name", "Example")
    browser_page.fill("#email", "synthetic@example.test")
    browser_page.select_option("#question_2", label="Yes")

    class FastPerson(HandoffControl):
        def wait(self, seconds):
            if not employer.received:
                browser_page.click('button[type="submit"]')
            browser_page.wait_for_timeout(20)
            return super().wait(min(seconds, 0.01))

    result = PlaywrightHandoff(poll_seconds=0.01)._await(
        PlaywrightPage(browser_page),
        "greenhouse",
        employer.url,
        FastPerson(0.3),
        lambda *_args: None,
    )
    assert len(employer.received) == 1
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="synthetic owner", acknowledge_unresolved=True
    )
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
    ExecutionService(prepared.ctx)._finish_handoff(
        prepared.application_id, prepared.opportunity_id, "greenhouse", result
    )
    with prepared.ctx.read_repos() as repos:
        status = repos.applications.get(prepared.application_id).status
    assert status in {"APPLYING", "APPLIED"}, (
        "a received application cannot become a retryable timeout"
    )
    with pytest.raises(ApprovalRequired):
        ApprovalService(prepared.ctx).assert_submission_authorised(prepared.application_id)


def test_browser_opportunity_filter_uses_industry(chromium):
    html = TestClient(app).get("/opportunities").text
    rows = [
        {
            "id": "synthetic-medical",
            "company": "Example A",
            "title": "Operations",
            "industry": "Medical Devices",
            "status": "SHORTLISTED",
        },
        {
            "id": "synthetic-other",
            "company": "Example B",
            "title": "Operations",
            "industry": "Construction",
            "status": "SHORTLISTED",
        },
    ]
    context = chromium.new_context()

    def serve(route):
        if route.request.url == "http://127.0.0.1/api/opportunities":
            route.fulfill(json={"opportunities": rows})
        elif route.request.url == "http://127.0.0.1/opportunities":
            route.fulfill(body=html, content_type="text/html")
        else:
            route.abort()

    context.route("**/*", serve)
    try:
        page = context.new_page()
        page.goto("http://127.0.0.1/opportunities")
        page.wait_for_selector('button[data-id="synthetic-other"]')
        page.fill('input[name="industry"]', "medical")
        page.click('button[type="submit"]')
        assert page.locator('button[data-id="synthetic-medical"]').count() == 1
        assert page.locator('button[data-id="synthetic-other"]').count() == 0
    finally:
        context.close()
