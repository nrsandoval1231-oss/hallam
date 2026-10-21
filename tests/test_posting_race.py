"""The posting check runs under the run registration and decides by compare-and-set.

A refresh, or another process, can change the application while the network is being
asked. The check must never walk the graph from whatever the status is by then.
"""

from __future__ import annotations

import pytest
from fake_pages import GH_URL, FakeFormPage, answerable_capture
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_domain.enums import ApplicationStatus, EventType, JobStatus
from sabra_domain.errors import InvalidTransition
from sabra_domain.state import application_path, assert_application_transition
from sabra_engine.approval import ApprovalService
from sabra_engine.execution import BLOCKED_POSTING_GONE, AdapterRegistry, ExecutionService
from sabra_engine.execution import default_posting_verifier as REAL_DEFAULT_VERIFIER
from sabra_engine.handoff_manager import HANDOFFS
from sabra_engine.refresh import PackageRefreshService
from sabra_providers.base import VerifyResult

GONE = VerifyResult(available=False, changed=False, http_status=404, detail="posting closed")


@pytest.fixture(autouse=True)
def _clean_handoffs():
    HANDOFFS.clear()
    yield
    HANDOFFS.clear()


def _approved_live(prepared) -> None:
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="owner", acknowledge_unresolved=True
    )
    prepared.ctx.settings.automation_dry_run = False


def _events(prepared, entity: str = "application", entity_id: str | None = None) -> list:
    with prepared.ctx.read_repos() as repos:
        return list(repos.events.for_entity(entity, entity_id or prepared.application_id))


def _count(prepared, event_type: EventType) -> int:
    return sum(1 for e in _events(prepared) if e.event_type == event_type.value)


def _app(prepared):
    with prepared.ctx.read_repos() as repos:
        row = repos.applications.get(prepared.application_id)
        return row.status, row.approved_package_hash, row.attempts, row.blocked_reason


def _service(prepared, capture, verify, pages: list, **page_kwargs) -> ExecutionService:
    def factory(_url: str) -> FakeFormPage:
        page = FakeFormPage(
            capture, captcha=False, confirmation_url=GH_URL + "/confirmation", **page_kwargs
        )
        pages.append(page)
        return page

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=factory)
    return ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter]), verify_posting=verify)


def _move(prepared, *steps: ApplicationStatus) -> None:
    with prepared.ctx.repos() as repos:
        for step in steps:
            repos.applications.set_status(prepared.application_id, step)


def test_the_check_runs_under_the_run_registration(prepared, greenhouse_form_capture) -> None:
    _approved_live(prepared)
    seen: dict = {}

    def verify(_job):
        seen["in_progress"] = HANDOFFS.run_in_progress(prepared.application_id)
        return VerifyResult(available=True, changed=False, detail="live")

    service = _service(prepared, answerable_capture(greenhouse_form_capture), verify, [])
    assert service.submit(prepared.application_id, allow_real_submission=True).outcome == "SUBMITTED"
    assert seen["in_progress"] is True, "no second run or report may start during the check"
    assert not HANDOFFS.run_in_progress(prepared.application_id), "and it is released after"


def test_a_closed_posting_blocks_an_approved_application_without_ever_claiming_it(
    prepared, greenhouse_form_capture
) -> None:
    _approved_live(prepared)
    pages: list = []
    service = _service(prepared, answerable_capture(greenhouse_form_capture), lambda _j: GONE, pages)
    attempts_before = _app(prepared)[2]
    started_before = _count(prepared, EventType.APPLICATION_STARTED)

    result = service.submit(prepared.application_id, allow_real_submission=True)

    assert result.outcome == "BLOCKED" and pages == []
    status, _hash, attempts, reason = _app(prepared)
    assert (status, reason) == (ApplicationStatus.BLOCKED.value, BLOCKED_POSTING_GONE)
    assert attempts == attempts_before
    assert _count(prepared, EventType.APPLICATION_STARTED) == started_before, "nothing was claimed"
    blocked = [e for e in _events(prepared) if e.event_type == EventType.APPLICATION_BLOCKED.value]
    assert blocked[-1].payload["from"] == "APPROVED"
    # No APPLYING detour: the approval-gated state was never entered.
    with prepared.ctx.read_repos() as repos:
        assert repos.applications.get(prepared.application_id).status == "BLOCKED"


def test_a_refresh_during_the_check_is_not_walked_through_approval(
    prepared, greenhouse_form_capture
) -> None:
    _approved_live(prepared)
    pages: list = []
    started_before = _count(prepared, EventType.APPLICATION_STARTED)

    def verify(_job):
        # While the network is being asked, the owner refreshes the package.
        PackageRefreshService(prepared.ctx).refresh(prepared.application_id)
        return GONE

    service = _service(prepared, answerable_capture(greenhouse_form_capture), verify, pages)
    result = service.submit(prepared.application_id, allow_real_submission=True)

    assert result.outcome == "CONFLICT" and pages == []
    status, approved_hash, _attempts, reason = _app(prepared)
    assert status == ApplicationStatus.AWAITING_APPROVAL.value, "the refresh stands"
    assert approved_hash is None, "no approval was resurrected by a walk through APPROVED"
    assert reason is None
    # (The refresh itself prepares a package, which records its own STARTED; nothing here
    # claimed an attempt, so there is no claim event beyond that one.)
    assert _count(prepared, EventType.APPLICATION_STARTED) == started_before + 1
    assert _count(prepared, EventType.APPLICATION_ATTEMPTED) == 0
    assert not any(
        e.event_type == EventType.APPLICATION_BLOCKED.value
        and e.payload.get("stage") == "posting_check"
        for e in _events(prepared)
    )
    moved = [
        e
        for e in _events(prepared)
        if e.event_type == EventType.WARNING_RECORDED.value
        and e.payload.get("warning") == "posting_gone_status_moved"
    ]
    assert moved and "AWAITING_APPROVAL" in moved[0].payload["message"]
    expired = [
        e
        for e in _events(prepared, "opportunity", prepared.opportunity_id)
        if e.event_type == EventType.JOB_EXPIRED.value
    ]
    assert expired, "the posting really is gone, and that fact is recorded"


def test_a_row_another_process_is_applying_is_not_moved_to_blocked(
    prepared, greenhouse_form_capture
) -> None:
    _approved_live(prepared)

    def verify(_job):
        _move(prepared, ApplicationStatus.APPLYING)  # another process claimed it
        return GONE

    pages: list = []
    service = _service(prepared, answerable_capture(greenhouse_form_capture), verify, pages)
    result = service.submit(prepared.application_id, allow_real_submission=True)

    assert result.outcome == "CONFLICT" and pages == []
    assert _app(prepared)[0] == ApplicationStatus.APPLYING.value
    assert not any(
        e.event_type == EventType.HANDED_TO_HUMAN.value for e in _events(prepared)
    ), "this run never held the claim, so it must not leave an 'interrupted' mark"
    # The other process may be about to submit: the job must not be left EXPIRED beside an
    # APPLIED application. Only the warning is recorded.
    with prepared.ctx.read_repos() as repos:
        assert repos.opportunities.get(prepared.opportunity_id).status == JobStatus.APPROVED.value
        assert repos.jobs.get(prepared.job_id).status == JobStatus.APPROVED.value
    assert not [
        e
        for e in _events(prepared, "opportunity", prepared.opportunity_id)
        if e.event_type == EventType.JOB_EXPIRED.value
    ]
    assert any(
        e.event_type == EventType.WARNING_RECORDED.value
        and e.payload.get("warning") == "posting_gone_status_moved"
        for e in _events(prepared)
    )


def test_a_failed_application_is_blocked_by_the_same_compare_and_set(
    prepared, greenhouse_form_capture
) -> None:
    _approved_live(prepared)
    _move(prepared, ApplicationStatus.APPLYING, ApplicationStatus.FAILED)
    pages: list = []
    service = _service(prepared, answerable_capture(greenhouse_form_capture), lambda _j: GONE, pages)

    result = service.submit(prepared.application_id, allow_real_submission=True)

    assert result.outcome == "BLOCKED" and pages == []
    assert _app(prepared)[0] == ApplicationStatus.BLOCKED.value
    blocked = [e for e in _events(prepared) if e.event_type == EventType.APPLICATION_BLOCKED.value]
    assert blocked[-1].payload["from"] == "FAILED"


def test_an_already_blocked_application_stays_blocked_and_gets_the_reason(
    prepared, greenhouse_form_capture
) -> None:
    _approved_live(prepared)
    _move(prepared, ApplicationStatus.APPLYING, ApplicationStatus.BLOCKED)
    service = _service(prepared, answerable_capture(greenhouse_form_capture), lambda _j: GONE, [])
    assert service.submit(prepared.application_id, allow_real_submission=True).outcome == "BLOCKED"
    status, _hash, _attempts, reason = _app(prepared)
    assert (status, reason) == (ApplicationStatus.BLOCKED.value, BLOCKED_POSTING_GONE)


def test_an_interrupt_during_the_check_leaves_no_interrupted_mark(
    prepared, greenhouse_form_capture
) -> None:
    _approved_live(prepared)

    def verify(_job):
        raise KeyboardInterrupt

    service = _service(prepared, answerable_capture(greenhouse_form_capture), verify, [])
    with pytest.raises(KeyboardInterrupt):
        service.submit(prepared.application_id, allow_real_submission=True)
    assert _app(prepared)[0] == ApplicationStatus.APPROVED.value
    assert not any(e.event_type == EventType.HANDED_TO_HUMAN.value for e in _events(prepared))
    assert not HANDOFFS.run_in_progress(prepared.application_id)


def test_the_new_direct_edges_do_not_reopen_a_way_back_into_the_approval_queue() -> None:
    assert_application_transition(ApplicationStatus.APPROVED, ApplicationStatus.BLOCKED)
    assert_application_transition(ApplicationStatus.FAILED, ApplicationStatus.BLOCKED)
    for start in (ApplicationStatus.APPROVED, ApplicationStatus.BLOCKED, ApplicationStatus.FAILED):
        with pytest.raises(InvalidTransition):
            application_path(start, ApplicationStatus.AWAITING_APPROVAL)
        with pytest.raises(InvalidTransition):
            assert_application_transition(start, ApplicationStatus.AWAITING_APPROVAL)


def test_the_default_check_is_one_attempt_with_the_discovery_timeout(prepared, monkeypatch) -> None:
    from types import SimpleNamespace

    from sabra_providers.greenhouse import GreenhouseProvider

    seen: list[dict] = []

    def fake_verify(self, ref, known_hash=None):
        seen.append({"attempts": self.http.max_attempts, "timeout": self.http.timeout})
        return VerifyResult(available=True, changed=False, detail="live")

    monkeypatch.setattr(GreenhouseProvider, "verify", fake_verify)
    job = SimpleNamespace(
        source="GREENHOUSE", board_token="test", source_job_id="9001", source_url="u"
    )
    verifier = REAL_DEFAULT_VERIFIER(prepared.ctx)
    verifier(job)  # a registered board
    verifier(SimpleNamespace(**{**job.__dict__, "board_token": "unregistered"}))
    expected = prepared.ctx.settings.discovery_timeout_seconds
    assert seen == [{"attempts": 1, "timeout": expected}] * 2, "no retries on the request path"


# ---------------------------------------------------------------------------
# documents are rendered before the browser opens, and the attempt records them
# ---------------------------------------------------------------------------


def test_a_render_failure_blocks_the_application_before_any_browser_page_opens(
    prepared, greenhouse_form_capture, monkeypatch
) -> None:
    _approved_live(prepared)

    def broken(_markdown: str, *, title: str) -> bytes:
        raise RuntimeError("reportlab is unavailable")

    monkeypatch.setattr("sabra_documents.render.render_resume_pdf", broken)
    pages: list = []
    live = VerifyResult(available=True, changed=False, detail="live")
    service = _service(prepared, answerable_capture(greenhouse_form_capture), lambda _j: live, pages)

    result = service.submit(prepared.application_id, allow_real_submission=True)

    assert result.outcome == "BLOCKED", (result.outcome, result.detail)
    assert pages == [], "the employer's page must not be opened when a document cannot be made"
    assert "document render failed" in (result.detail or "")
    assert _app(prepared)[0] == ApplicationStatus.BLOCKED.value


def test_the_attempt_event_records_each_pdfs_hash_and_the_renderer(
    prepared, greenhouse_form_capture
) -> None:
    import hashlib
    from pathlib import Path

    _approved_live(prepared)
    pages: list = []
    live = VerifyResult(available=True, changed=False, detail="live")
    service = _service(prepared, answerable_capture(greenhouse_form_capture), lambda _j: live, pages)
    assert service.submit(prepared.application_id, allow_real_submission=True).outcome == "SUBMITTED"

    attempted = [
        e
        for e in _events(prepared)
        if e.event_type == EventType.APPLICATION_ATTEMPTED.value
        and (e.payload or {}).get("stage") == "prepared"
    ]
    assert attempted
    documents = attempted[-1].payload["prepared"]["documents"]
    uploaded = Path(pages[0].files["#resume"]).read_bytes()
    assert documents["resume"]["sha256"] == hashlib.sha256(uploaded).hexdigest()
    assert documents["renderer"].startswith("reportlab ")
    assert JobStatus.APPLIED.value  # the run completed
