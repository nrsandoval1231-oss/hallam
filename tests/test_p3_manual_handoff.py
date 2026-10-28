"""Manual handoff: an approved application on a source with no adapter.

Ashby and company career pages have no application adapter. Instead of BLOCKED /
ADAPTER_UNSUPPORTED, a real run opens the apply page for the person, saves the rendered
PDFs, types nothing, and then follows the ordinary handoff rules.

The browser is a double throughout; nothing here opens a window or a network connection.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sabra_automation.base import ApplicationAdapter
from sabra_automation.handoff import HandoffControl, HandoffResult, PlaywrightHandoff
from sabra_automation.manual_adapter import ManualHandoffAdapter, manual_apply_url, manual_plan
from sabra_domain.enums import ApplicationStatus, EventType, JobStatus, SafetyStopKind
from sabra_domain.errors import SafetyStop, ValidationFailure
from sabra_engine.approval import ApprovalService
from sabra_engine.context import EngineContext
from sabra_engine.execution import (
    AdapterRegistry,
    ExecutionService,
    default_registry,
)
from sabra_engine.handoff_manager import HANDOFFS
from test_submission_boundary import browser  # noqa: F401 - the fake Chromium fixture

APPLY_URL = "https://jobs.ashbyhq.com/siepe/8f17f764-aa6f/application"
POSTING_URL = "https://jobs.ashbyhq.com/siepe/8f17f764-aa6f"


@pytest.fixture(autouse=True)
def _clean_handoffs():
    HANDOFFS.clear()
    yield
    HANDOFFS.clear()


class SpyAdapter(ApplicationAdapter):
    """A Greenhouse-only adapter that records any use. It must never be touched."""

    kind = "greenhouse"

    def __init__(self) -> None:
        super().__init__(dry_run=False)
        self.touched: list[str] = []

    def supports(self, job: Any) -> bool:
        return str(getattr(job, "source", "")) == "GREENHOUSE"

    def inspect(self, job: Any):
        self.touched.append("inspect")
        raise AssertionError("no adapter may inspect a manual posting")

    def prepare(self, package, form):
        self.touched.append("prepare")
        raise AssertionError("no adapter may fill a manual posting")

    def submit(self, prepared):
        self.touched.append("submit")
        raise AssertionError("no adapter may submit a manual posting")


class RecordingHandoff:
    """A headed browser double. Returns `result`, or waits for a person's report."""

    def __init__(self, result: HandoffResult | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []
        self.started = threading.Event()

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
        documents=None,
    ) -> HandoffResult:
        self.calls.append(
            {
                "adapter": adapter,
                "package": package,
                "form": form,
                "url": url,
                "documents": dict(documents or {}),
                "files_existed": {k: Path(v).exists() for k, v in (documents or {}).items()},
            }
        )
        if on_event:
            on_event("opened", {"url": url})
        self.started.set()
        if self.result is not None:
            return self.result
        outcome = control.wait(30) if control is not None else None
        if outcome is None:
            return HandoffResult(outcome="timeout", source="timeout", opened_headed=True)
        return HandoffResult(outcome=outcome, source="user_report", opened_headed=True)


def make_ashby(prepared, *, apply_url: str | None = APPLY_URL) -> None:
    """Turn the prepared application's posting into an Ashby posting."""

    with prepared.ctx.repos() as repos:
        job = repos.jobs.get(prepared.job_id)
        job.source = "ASHBY"
        job.source_url = POSTING_URL
        job.board_token = "siepe"
        job.raw_payload = {"id": "8f17f764-aa6f", "applyUrl": apply_url} if apply_url else {}


def approve(prepared) -> None:
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )


def go_live(prepared) -> None:
    prepared.ctx.settings.automation_dry_run = False


def service(prepared, spy: SpyAdapter | None = None, **kwargs) -> ExecutionService:
    registry = AdapterRegistry([spy or SpyAdapter()])
    return ExecutionService(prepared.ctx, registry=registry, **kwargs)


def status_of(prepared) -> str:
    with prepared.ctx.read_repos() as repos:
        return repos.applications.get(prepared.application_id).status


def events_of(prepared) -> list[Any]:
    with prepared.ctx.read_repos() as repos:
        return list(repos.events.for_entity("application", prepared.application_id))


def event_types(prepared) -> list[str]:
    return [e.event_type for e in events_of(prepared)]


@pytest.fixture()
def ashby_app(prepared):
    make_ashby(prepared)
    approve(prepared)
    return prepared


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


def test_dry_run_returns_a_plan_that_says_manual_handoff_and_opens_nothing(ashby_app) -> None:
    handoff = RecordingHandoff()
    spy = SpyAdapter()
    for allow in (False, True):  # SABRA_AUTOMATION_DRY_RUN is on, so both are a dry run
        result = service(ashby_app, spy, handoff=handoff).submit(
            ashby_app.application_id, allow_real_submission=allow
        )
        assert result.outcome == "DRY_RUN" and result.adapter == "manual"
        assert "manual handoff; no adapter" in result.plan["note"]
        assert result.plan["manual_handoff"] is True
        assert (result.plan["navigated"], result.plan["typed"], result.plan["uploaded"]) == (
            False,
            False,
            False,
        )
        assert result.plan["would_open"] == APPLY_URL
        assert result.plan["would_save"] == {"resume": True, "cover_letter": True}
    assert handoff.calls == [] and spy.touched == []
    assert status_of(ashby_app) == ApplicationStatus.APPROVED.value
    attempts = [
        e for e in events_of(ashby_app) if e.event_type == EventType.APPLICATION_ATTEMPTED.value
    ]
    assert attempts and all(e.payload["dry_run"] is True for e in attempts)
    assert attempts[0].payload["url"] == APPLY_URL
    assert not list(ashby_app.ctx.settings.artifacts_dir.rglob("*.pdf")), "nothing is rendered"
    assert EventType.APPLICATION_BLOCKED.value not in event_types(ashby_app)


def test_live_settings_without_the_real_flag_is_still_a_dry_run(ashby_app) -> None:
    go_live(ashby_app)
    handoff = RecordingHandoff()
    result = service(ashby_app, handoff=handoff).submit(
        ashby_app.application_id, allow_real_submission=False
    )
    assert result.outcome == "DRY_RUN" and handoff.calls == []


# ---------------------------------------------------------------------------
# a real run
# ---------------------------------------------------------------------------


def test_a_real_run_claims_opens_the_apply_url_and_never_uses_an_adapter(ashby_app) -> None:
    go_live(ashby_app)
    handoff = RecordingHandoff(HandoffResult(outcome="submitted", source="user_report"))
    spy = SpyAdapter()
    announced: list[str] = []
    result = service(ashby_app, spy, handoff=handoff, announce=announced.append).submit(
        ashby_app.application_id, allow_real_submission=True
    )

    assert spy.touched == [], "no adapter is invoked"
    (call,) = handoff.calls
    assert call["url"] == APPLY_URL, "the source's applyUrl, not the posting page"
    assert call["form"] is None, "there is no form to fill"
    assert isinstance(call["adapter"], ManualHandoffAdapter) and call["adapter"].kind == "manual"
    assert set(call["documents"]) == {"resume", "cover_letter"}
    assert all(call["files_existed"].values()), "the files exist before the browser opens"
    for label, path in call["documents"].items():
        pdf = Path(path)
        assert pdf.suffix == ".pdf" and pdf.read_bytes().startswith(b"%PDF"), label
        assert ashby_app.ctx.settings.artifacts_dir in pdf.parents
        assert ashby_app.opportunity_id in pdf.parts
    assert "Resume" in Path(call["documents"]["resume"]).name
    assert "Cover" in Path(call["documents"]["cover_letter"]).name

    # The person was told where the files are, before the window opened.
    (message,) = announced
    assert APPLY_URL in message and "Nothing will be typed" in message
    assert all(path in message for path in call["documents"].values())

    # Synchronous handoff: the person said it was submitted.
    assert result.outcome == "SUBMITTED" and result.adapter == "manual"
    assert status_of(ashby_app) == ApplicationStatus.APPLIED.value
    with ashby_app.ctx.read_repos() as repos:
        row = repos.applications.get(ashby_app.application_id)
        assert row.submission_method == "HEADED_HUMAN" and row.submission_time is not None
        assert repos.opportunities.get(ashby_app.opportunity_id).status == JobStatus.APPLIED.value

    events = events_of(ashby_app)
    started = [e for e in events if e.event_type == EventType.APPLICATION_STARTED.value][-1]
    assert started.payload["adapter"] == "manual" and started.payload["url"] == APPLY_URL
    assert started.payload["manual"] is True and started.payload["nothing_typed"] is True
    submitted = next(e for e in events if e.event_type == EventType.APPLICATION_SUBMITTED.value)
    assert submitted.payload["adapter"] == "manual"
    assert submitted.payload["manual"] is True and submitted.payload["nothing_typed"] is True
    handed = [
        e
        for e in events
        if e.event_type == EventType.HANDED_TO_HUMAN.value and e.payload.get("manual")
    ]
    assert handed and handed[0].payload["documents"] == call["documents"]
    assert EventType.CHALLENGE_DETECTED.value not in event_types(ashby_app)
    assert EventType.APPLICATION_BLOCKED.value not in event_types(ashby_app)


def test_the_apply_url_falls_back_to_the_posting_url(prepared) -> None:
    make_ashby(prepared, apply_url=None)
    approve(prepared)
    go_live(prepared)
    handoff = RecordingHandoff(HandoffResult(outcome="abandoned", source="user_report"))
    service(prepared, handoff=handoff).submit(prepared.application_id, allow_real_submission=True)
    assert handoff.calls[0]["url"] == POSTING_URL


def test_abandoning_blocks_the_application_and_it_can_be_tried_again(ashby_app) -> None:
    go_live(ashby_app)
    first = RecordingHandoff(HandoffResult(outcome="abandoned", source="user_report"))
    result = service(ashby_app, handoff=first).submit(
        ashby_app.application_id, allow_real_submission=True
    )
    assert result.outcome == "ABANDONED"
    assert status_of(ashby_app) == ApplicationStatus.BLOCKED.value
    with ashby_app.ctx.read_repos() as repos:
        assert repos.applications.get(ashby_app.application_id).blocked_reason == (
            "HANDOFF_ABANDONED"
        )
    assert EventType.APPLICATION_ABANDONED.value in event_types(ashby_app)

    second = RecordingHandoff(HandoffResult(outcome="submitted", source="user_report"))
    retry = service(ashby_app, handoff=second).submit(
        ashby_app.application_id, allow_real_submission=True
    )
    assert retry.outcome == "SUBMITTED"
    assert status_of(ashby_app) == ApplicationStatus.APPLIED.value


def test_a_failed_attempt_is_failed_not_submitted(ashby_app) -> None:
    go_live(ashby_app)
    handoff = RecordingHandoff(HandoffResult(outcome="failed", source="user_report", error="x"))
    result = service(ashby_app, handoff=handoff).submit(
        ashby_app.application_id, allow_real_submission=True
    )
    assert result.outcome == "FAILED"
    assert status_of(ashby_app) == ApplicationStatus.FAILED.value
    with ashby_app.ctx.read_repos() as repos:
        assert repos.applications.get(ashby_app.application_id).submission_time is None


def test_a_closed_window_waits_for_a_report_and_the_report_is_marked_manual(ashby_app) -> None:
    go_live(ashby_app)
    handoff = RecordingHandoff(HandoffResult(outcome="closed", source="browser_closed"))
    result = service(ashby_app, handoff=handoff).submit(
        ashby_app.application_id, allow_real_submission=True
    )
    assert result.outcome == "HANDED_TO_HUMAN"
    assert status_of(ashby_app) == ApplicationStatus.APPLYING.value

    reporter = service(ashby_app)
    status = reporter.handoff_status(ashby_app.application_id)
    assert status["awaiting_person"] is True and status["manual"] is True
    assert set(status["documents"]) == {"resume", "cover_letter"}

    done = reporter.report_handoff_outcome(ashby_app.application_id, "submitted")
    assert done["application_status"] == ApplicationStatus.APPLIED.value
    submitted = next(
        e for e in events_of(ashby_app) if e.event_type == EventType.APPLICATION_SUBMITTED.value
    )
    # A report after the window is gone has no adapter in hand; the attempt's own events
    # say that it was manual and that nothing was typed.
    assert submitted.payload["adapter"] == "manual" and submitted.payload["nothing_typed"] is True
    with ashby_app.ctx.read_repos() as repos:
        assert repos.applications.get(ashby_app.application_id).submission_method == (
            "HEADED_HUMAN"
        )


def test_the_forced_report_path_works_for_a_manual_attempt(ashby_app) -> None:
    go_live(ashby_app)
    handoff = RecordingHandoff(HandoffResult(outcome="timeout", source="timeout"))
    service(ashby_app, handoff=handoff).submit(
        ashby_app.application_id, allow_real_submission=True
    )
    assert status_of(ashby_app) == ApplicationStatus.APPLYING.value
    reporter = service(ashby_app)
    with pytest.raises(ValidationFailure, match="typing the application id"):
        reporter.force_report_handoff_outcome(
            ashby_app.application_id, "abandoned", confirm_id="wrong"
        )
    reporter.force_report_handoff_outcome(
        ashby_app.application_id, "abandoned", confirm_id=ashby_app.application_id
    )
    assert status_of(ashby_app) == ApplicationStatus.BLOCKED.value
    abandoned = next(
        e for e in events_of(ashby_app) if e.event_type == EventType.APPLICATION_ABANDONED.value
    )
    assert abandoned.payload["forced"] is True and abandoned.payload["adapter"] == "manual"


def test_a_document_that_cannot_be_rendered_stops_before_the_browser_opens(
    ashby_app, monkeypatch
) -> None:
    go_live(ashby_app)

    def boom(*_args, **_kwargs):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr("sabra_documents.render.render_resume_pdf", boom)
    handoff = RecordingHandoff(HandoffResult(outcome="submitted", source="user_report"))
    result = service(ashby_app, handoff=handoff).submit(
        ashby_app.application_id, allow_real_submission=True
    )
    assert result.outcome == "BLOCKED" and "could not be produced" in result.detail
    assert handoff.calls == []
    assert status_of(ashby_app) == ApplicationStatus.BLOCKED.value
    with ashby_app.ctx.read_repos() as repos:
        assert repos.applications.get(ashby_app.application_id).blocked_reason == str(
            SafetyStopKind.UNSUPPORTED_FILE_REQUIREMENT
        )


def test_a_posting_that_is_gone_is_not_opened(ashby_app) -> None:
    go_live(ashby_app)
    handoff = RecordingHandoff(HandoffResult(outcome="submitted", source="user_report"))
    gone = SimpleNamespace(available=False, http_status=404, detail="posting closed")
    result = service(ashby_app, handoff=handoff, verify_posting=lambda _job: gone).submit(
        ashby_app.application_id, allow_real_submission=True
    )
    assert result.outcome == "BLOCKED" and "no longer available" in result.detail
    assert handoff.calls == []
    assert status_of(ashby_app) == ApplicationStatus.BLOCKED.value


def test_an_unapproved_application_is_still_refused(prepared) -> None:
    make_ashby(prepared)  # not approved
    go_live(prepared)
    handoff = RecordingHandoff(HandoffResult(outcome="submitted", source="user_report"))
    result = service(prepared, handoff=handoff).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "BLOCKED"
    assert handoff.calls == [] and status_of(prepared) != ApplicationStatus.APPLIED.value


def test_a_greenhouse_posting_does_not_take_the_manual_path(prepared) -> None:
    approve(prepared)
    result = service(prepared).submit(prepared.application_id, allow_real_submission=True)
    # Dry run: the supported-adapter plan, not the manual one.
    assert result.outcome == "DRY_RUN" and result.adapter == "greenhouse"
    assert "manual_handoff" not in result.plan


# ---------------------------------------------------------------------------
# background handoff through the dashboard API
# ---------------------------------------------------------------------------


@pytest.fixture()
def api(ashby_app, monkeypatch):
    from sabra_api import main

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: ashby_app.ctx.settings)
    go_live(ashby_app)
    handoff = RecordingHandoff()
    spy = SpyAdapter()

    def build(ctx):
        return ExecutionService(
            ctx,
            registry=AdapterRegistry([spy]),
            handoff=handoff,
            background_handoff=True,
            ctx_factory=EngineContext.create,
        )

    monkeypatch.setattr(main, "_execution_service", build)
    client = TestClient(main.app)
    yield client, handoff, spy
    for record in list(HANDOFFS._records.values()):
        record.control.report("abandoned")
        if record.thread:
            record.thread.join(5)


def apply(client, prepared):
    return client.post(
        f"/api/applications/{prepared.application_id}/apply", json={"confirm": True}
    )


def test_the_dashboard_apply_returns_at_once_with_the_url_and_the_files(ashby_app, api) -> None:
    client, handoff, spy = api
    response = apply(client, ashby_app)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "HANDED_TO_HUMAN" and body["adapter"] == "manual"
    assert body["url"] == APPLY_URL and body["stops"] == []
    assert set(body["documents"]) == {"resume", "cover_letter"}
    assert "Nothing was typed" in body["detail"]
    assert handoff.started.wait(5)
    assert handoff.calls[0]["documents"] == body["documents"] and spy.touched == []

    live = client.get(f"/api/applications/{ashby_app.application_id}/handoff").json()
    assert live["manual"] is True and live["awaiting_person"] is True
    assert live["documents"] == body["documents"] and live["url"] == APPLY_URL

    assert apply(client, ashby_app).status_code == 409, "a second Apply is refused meanwhile"

    done = client.post(
        f"/api/applications/{ashby_app.application_id}/handoff/outcome",
        json={"outcome": "submitted"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["application_status"] == "APPLIED"
    with ashby_app.ctx.read_repos() as repos:
        row = repos.applications.get(ashby_app.application_id)
        assert row.submission_method == "HEADED_HUMAN"
    submitted = next(
        e for e in events_of(ashby_app) if e.event_type == EventType.APPLICATION_SUBMITTED.value
    )
    assert submitted.payload["adapter"] == "manual" and submitted.payload["nothing_typed"] is True


def test_the_dashboard_outcome_abandoned_returns_the_application_to_blocked(
    ashby_app, api
) -> None:
    client, handoff, _spy = api
    assert apply(client, ashby_app).json()["outcome"] == "HANDED_TO_HUMAN"
    assert handoff.started.wait(5)
    done = client.post(
        f"/api/applications/{ashby_app.application_id}/handoff/outcome",
        json={"outcome": "abandoned"},
    )
    assert done.status_code == 200
    assert status_of(ashby_app) == ApplicationStatus.BLOCKED.value
    assert "APPLICATION_ABANDONED" in event_types(ashby_app)


# ---------------------------------------------------------------------------
# the Applications page
# ---------------------------------------------------------------------------


def test_packages_are_flagged_for_manual_handoff(prepared, monkeypatch) -> None:
    from sabra_api.main import app

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    approve(prepared)
    client = TestClient(app)
    (row,) = client.get("/api/packages").json()["packages"]
    assert row["manual_handoff"] is False
    assert "Open it in your browser" not in (row["next_step"] or "")

    make_ashby(prepared)
    (row,) = client.get("/api/packages").json()["packages"]
    assert row["manual_handoff"] is True
    assert "cannot fill this employer" in row["next_step"]
    assert "types nothing" in row["next_step"]


def test_the_applications_page_labels_the_button_and_shows_the_files() -> None:
    from sabra_api.main import app

    html = TestClient(app).get("/applications").text
    assert ">Apply<" in html
    assert "Open in browser for you to fill" in html
    assert "p.manual_handoff" in html and "s.documents" in html
    assert "Attach these files" in html


# ---------------------------------------------------------------------------
# the pieces
# ---------------------------------------------------------------------------


def test_adapters_are_chosen_by_source_kind_only() -> None:
    registry = default_registry()
    assert {k for k in registry.supported_kinds()} == {"greenhouse", "lever"}

    def job(source: str, url: str = "") -> SimpleNamespace:
        return SimpleNamespace(source=source, source_url=url, raw_payload={})

    assert registry.for_job(job("GREENHOUSE")).kind == "greenhouse"
    assert registry.for_job(job("LEVER")).kind == "lever"
    for source in ("ASHBY", "DIRECT_CAREERS", "ashby", ""):
        assert registry.needs_manual(job(source))
    for source in ("GREENHOUSE", "LEVER"):
        assert not registry.needs_manual(job(source))
    # A URL never selects an adapter: not a greenhouse.io one, not a host that merely
    # contains "lever.co" (clever.com), not a lookalike.
    for url in (
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://job-boards.greenhouse.io/acme/jobs/1",
        "https://www.clever.com/careers/1",
        "https://jobs.lever.co/acme/1",
        "https://evil.example/?u=greenhouse.io",
    ):
        assert registry.needs_manual(job("DIRECT_CAREERS", url)), url
        assert registry.needs_manual(job("ASHBY", url)), url
        with pytest.raises(Exception, match="no application adapter"):
            registry.for_job(job("DIRECT_CAREERS", url))


def make_direct_careers(prepared, url: str) -> None:
    with prepared.ctx.repos() as repos:
        row = repos.jobs.get(prepared.job_id)
        row.source = "DIRECT_CAREERS"
        row.source_url = url
        row.board_token = None
        row.raw_payload = {}


@pytest.mark.parametrize(
    "url",
    ["https://boards.greenhouse.io/acme/jobs/55", "https://www.clever.com/careers/55"],
)
def test_a_careers_page_on_an_ats_looking_url_is_still_a_manual_handoff(prepared, url) -> None:
    """The label, the dry run and the real run all take the same manual path."""

    from sabra_api.main import app

    make_direct_careers(prepared, url)
    approve(prepared)

    # What the page says...
    import sabra_engine.context as context_module

    original = context_module.get_settings
    context_module.get_settings = lambda: prepared.ctx.settings
    try:
        (row,) = TestClient(app).get("/api/packages").json()["packages"]
    finally:
        context_module.get_settings = original
    assert row["manual_handoff"] is True and "types nothing" in row["next_step"]

    # ...is what Apply does, using the shipped adapters (not a test spy).
    real = default_registry(dry_run=False, settings=prepared.ctx.settings)
    dry = ExecutionService(prepared.ctx, registry=real).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert dry.outcome == "DRY_RUN" and dry.adapter == "manual"

    go_live(prepared)
    handoff = RecordingHandoff(HandoffResult(outcome="abandoned", source="user_report"))
    result = ExecutionService(
        prepared.ctx, registry=default_registry(dry_run=False, settings=prepared.ctx.settings),
        handoff=handoff,
    ).submit(prepared.application_id, allow_real_submission=True)
    assert result.outcome == "ABANDONED" and result.adapter == "manual"
    (call,) = handoff.calls
    assert call["url"] == url and call["form"] is None
    assert isinstance(call["adapter"], ManualHandoffAdapter), "no Greenhouse or Lever adapter"
    started = [
        e for e in events_of(prepared) if e.event_type == EventType.APPLICATION_STARTED.value
    ][-1]
    assert started.payload["adapter"] == "manual" and started.payload["nothing_typed"] is True


def test_the_dashboard_label_asks_the_same_registry(prepared, monkeypatch) -> None:
    from sabra_api.main import app

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    approve(prepared)
    client = TestClient(app)
    for source, expected in (("GREENHOUSE", False), ("LEVER", False), ("ASHBY", True)):
        with prepared.ctx.repos() as repos:
            repos.jobs.get(prepared.job_id).source = source
        (row,) = client.get("/api/packages").json()["packages"]
        assert row["manual_handoff"] is expected, source
        detail = client.get(f"/api/packages/{prepared.package_id}").json()
        assert detail["manual_handoff"] is expected, source


def test_the_manual_adapter_is_never_chosen_and_never_fills() -> None:
    adapter = ManualHandoffAdapter()
    job = SimpleNamespace(source="ASHBY", source_url=POSTING_URL, raw_payload={})
    assert adapter.supports(job) is False
    assert AdapterRegistry([adapter]).adapters == [adapter]
    with pytest.raises(Exception, match="no application adapter"):
        AdapterRegistry([adapter]).for_job(job)
    for call in (
        lambda: adapter.inspect(job),
        lambda: adapter.prepare(None, None),
        lambda: adapter.submit(None),
    ):
        with pytest.raises(SafetyStop):
            call()


def test_manual_apply_url_prefers_the_sources_apply_url() -> None:
    job = SimpleNamespace(source_url=POSTING_URL, raw_payload={"applyUrl": APPLY_URL})
    assert manual_apply_url(job) == APPLY_URL
    assert manual_apply_url(SimpleNamespace(source_url=POSTING_URL, raw_payload={})) == POSTING_URL
    assert manual_apply_url(SimpleNamespace(source_url=POSTING_URL, raw_payload=None)) == (
        POSTING_URL
    )
    hostile = SimpleNamespace(source_url=POSTING_URL, raw_payload={"applyUrl": "javascript:x"})
    assert manual_apply_url(hostile) == POSTING_URL
    plan = manual_plan(SimpleNamespace(tailored_resume_markdown="", cover_letter="hi"), job)
    assert plan["would_save"] == {"resume": False, "cover_letter": True}


def test_the_real_handoff_types_nothing_and_never_self_confirms(browser) -> None:  # noqa: F811
    events: list = []
    control = HandoffControl(0.3)
    result = PlaywrightHandoff(poll_seconds=0.01).run(
        adapter=ManualHandoffAdapter(),
        package=object(),
        form=None,
        url=APPLY_URL,
        settings=None,
        control=control,
        on_event=lambda state, payload: events.append((state, payload)),
        documents={"resume": "C:/files/Resume.pdf"},
    )
    # Even a page that looks submitted proves nothing without rules for this source.
    assert result.outcome == "timeout"
    states = [state for state, _ in events]
    assert states[:2] == ["opened", "documents_ready"]
    assert dict(events[1][1]["documents"]) == {"resume": "C:/files/Resume.pdf"}
    assert browser.instances[0].closed is True
    assert browser.page.url == APPLY_URL


def test_the_real_handoff_ends_on_the_persons_report(browser) -> None:  # noqa: F811
    control = HandoffControl(5)
    threading.Timer(0.1, lambda: control.report("submitted")).start()
    result = PlaywrightHandoff(poll_seconds=0.01).run(
        adapter=ManualHandoffAdapter(),
        package=object(),
        form=None,
        url=APPLY_URL,
        control=control,
        documents={},
    )
    assert result.outcome == "submitted" and result.source == "user_report"


def test_cli_apply_prints_where_the_files_are(ashby_app, monkeypatch) -> None:
    from sabra_engine import cli
    from typer.testing import CliRunner

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: ashby_app.ctx.settings)
    go_live(ashby_app)
    handoff = RecordingHandoff(HandoffResult(outcome="submitted", source="user_report"))
    monkeypatch.setattr(cli, "_context", lambda: ashby_app.ctx)
    monkeypatch.setattr(ashby_app.ctx, "close", lambda: None)
    real_init = ExecutionService.__init__

    def init(self, ctx, **kwargs):
        real_init(self, ctx, registry=AdapterRegistry([SpyAdapter()]), handoff=handoff, **kwargs)

    monkeypatch.setattr(ExecutionService, "__init__", init)
    result = CliRunner().invoke(cli.app, ["apply", ashby_app.application_id, "--real"])
    assert result.exit_code == 0, result.output
    assert "Nothing will be typed" in result.output and "Resume.pdf" in result.output
    assert "SUBMITTED" in result.output


# ---------------------------------------------------------------------------
# the Applications page script, run under node
# ---------------------------------------------------------------------------

EVIL = '<img src=x onerror="alert(1)">'


def package_row(**overrides) -> dict:
    base = {
        "package_id": "pkg_1",
        "content_hash": "hash_1",
        "application_id": "app_1",
        "application_status": "APPROVED",
        "status": "COMPLETE",
        "title": "Role",
        "company": "Siepe",
        "approvable": False,
        "manual_handoff": True,
        "next_step": "Velora cannot fill this employer's form.",
    }
    base.update(overrides)
    return base


def test_the_applications_row_says_open_in_browser_not_apply_for_a_manual_source() -> None:
    from html.parser import HTMLParser

    from p3_support import run_page_script
    from sabra_api.main import applications_page

    rows = [
        package_row(),
        package_row(package_id="pkg_2", application_id="app_2", manual_handoff=False),
        package_row(package_id="pkg_3", application_id="app_3", title=EVIL, company=EVIL),
    ]
    live = {"GET /api/packages": {"status": 200, "body": {"automation_dry_run": False, "packages": rows}}}
    dry = {"GET /api/packages": {"status": 200, "body": {"automation_dry_run": True, "packages": rows}}}

    class Buttons(HTMLParser):
        def __init__(self, html: str) -> None:
            super().__init__()
            self.buttons: list[dict] = []
            self.text: list[str] = []
            self.tags: list[tuple[str, dict]] = []
            self._open: dict | None = None
            self.feed(html)

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))
            if tag == "button" and dict(attrs).get("class") == "apply":
                self._open = {**dict(attrs), "label": ""}
                self.buttons.append(self._open)

        def handle_data(self, data):
            self.text.append(data)
            if self._open is not None:
                self._open["label"] += data

        def handle_endtag(self, tag):
            if tag == "button":
                self._open = None

    html = run_page_script(applications_page(), live)["nodes"]["#list"]["innerHTML"]
    parsed = Buttons(html)
    labels = {b["data-id"]: (b["label"], b["data-manual"]) for b in parsed.buttons}
    assert labels["app_1"] == ("Open in browser for you to fill", "1")
    assert labels["app_2"] == ("Apply", "0")
    assert all(tag != "img" for tag, _ in parsed.tags), "markup from the server stays text"
    assert EVIL in "".join(parsed.text)

    off = Buttons(run_page_script(applications_page(), dry)["nodes"]["#list"]["innerHTML"])
    assert all("disabled" in b for b in off.buttons)
    assert "Dry run is on, so Open in browser for you to fill is off" in "".join(off.text)


def test_the_handoff_box_lists_the_files_to_attach_as_text() -> None:
    from html.parser import HTMLParser

    from p3_support import run_page_script
    from sabra_api.main import applications_page

    status = {
        "awaiting_person": True,
        "active": True,
        "state": "open",
        "application_status": "APPLYING",
        "manual": True,
        "documents": {"resume": EVIL, "cover_letter": "C:/files/Cover-Letter.pdf"},
        "can_force": False,
    }
    expression = (
        "var box={innerHTML:'',querySelectorAll(){return []},remove(){}};"
        f"showHandoff(box,'app_1',{json.dumps(status)});box.innerHTML"
    )
    out = run_page_script(
        applications_page(),
        {"GET /api/packages": {"status": 200, "body": {"packages": []}}},
        [{"eval": expression}],
    )
    html = out["evaluated"][0]

    class Probe(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.tags: list[str] = []
            self.text: list[str] = []
            self.feed(html)

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)

        def handle_data(self, data):
            self.text.append(data)

    probe = Probe()
    text = "".join(probe.text)
    assert "img" not in probe.tags
    assert "Nothing was typed" in text and "Attach these files" in text
    assert EVIL in text and "C:/files/Cover-Letter.pdf" in text
    assert "Solve the check" not in text, "the CAPTCHA wording is for CAPTCHA handoffs"
    assert "I submitted it" in text and "I didn't submit" in text

    captcha = dict(status, manual=False, documents={})
    expression = (
        "var box={innerHTML:'',querySelectorAll(){return []},remove(){}};"
        f"showHandoff(box,'app_1',{json.dumps(captcha)});box.innerHTML"
    )
    again = run_page_script(
        applications_page(),
        {"GET /api/packages": {"status": 200, "body": {"packages": []}}},
        [{"eval": expression}],
    )
    assert "Solve the check" in again["evaluated"][0]
    assert "Attach these files" not in again["evaluated"][0]


def test_a_posting_with_no_usable_url_is_blocked_before_any_browser_opens(prepared) -> None:
    with prepared.ctx.repos() as repos:
        job = repos.jobs.get(prepared.job_id)
        job.source = "ASHBY"
        job.source_url = "javascript:alert(1)"
        job.raw_payload = {"applyUrl": "file:///C:/secret"}
    approve(prepared)
    go_live(prepared)
    handoff = RecordingHandoff(HandoffResult(outcome="submitted", source="user_report"))
    result = service(prepared, handoff=handoff).submit(
        prepared.application_id, allow_real_submission=True
    )
    assert result.outcome == "BLOCKED" and "no usable http(s) apply URL" in result.detail
    assert handoff.calls == []
    assert status_of(prepared) == ApplicationStatus.BLOCKED.value
