"""Posting check before a real submission, verify-what-you-saw, warnings, copy fixes."""

from __future__ import annotations

import pytest
from fake_pages import GH_URL, FakeFormPage, answerable_capture
from fastapi.testclient import TestClient
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_domain.enums import ApplicationStatus, EventType, JobStatus
from sabra_domain.errors import ValidationFailure
from sabra_engine.answer_memory import AnswerMemoryService
from sabra_engine.approval import ApprovalService
from sabra_engine.execution import (
    BLOCKED_POSTING_GONE,
    AdapterRegistry,
    ExecutionService,
    posting_is_gone,
)
from sabra_engine.execution import default_posting_verifier as real_default_verifier
from sabra_engine.handoff_manager import HANDOFFS
from sabra_providers.base import VerifyResult


@pytest.fixture(autouse=True)
def _clean_handoffs():
    HANDOFFS.clear()
    yield
    HANDOFFS.clear()


def _live(prepared) -> None:
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="owner", acknowledge_unresolved=True
    )
    prepared.ctx.settings.automation_dry_run = False


def _events(prepared, entity: str = "application", entity_id: str | None = None) -> list:
    with prepared.ctx.read_repos() as repos:
        return list(repos.events.for_entity(entity, entity_id or prepared.application_id))


def _status_of(prepared) -> str:
    with prepared.ctx.read_repos() as repos:
        return repos.applications.get(prepared.application_id).status


def _warnings(prepared, kind: str) -> list:
    return [
        e
        for e in _events(prepared)
        if e.event_type == EventType.WARNING_RECORDED.value and e.payload.get("warning") == kind
    ]


def _service(prepared, capture, verify, pages: list) -> ExecutionService:
    def factory(_url: str) -> FakeFormPage:
        page = FakeFormPage(capture, captcha=False, confirmation_url=GH_URL + "/confirmation")
        pages.append(page)
        return page

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=factory)
    return ExecutionService(
        prepared.ctx, registry=AdapterRegistry([adapter]), verify_posting=verify
    )


# ---------------------------------------------------------------------------
# 5c. the posting must still be up
# ---------------------------------------------------------------------------


def test_a_closed_posting_expires_the_job_blocks_the_application_and_opens_nothing(
    prepared, greenhouse_form_capture
) -> None:
    _live(prepared)
    pages: list = []
    calls: list = []

    def verify(job):
        calls.append(job)
        return VerifyResult(available=False, changed=False, http_status=404, detail="posting closed")

    service = _service(prepared, answerable_capture(greenhouse_form_capture), verify, pages)
    result = service.submit(prepared.application_id, allow_real_submission=True)

    assert result.outcome == "BLOCKED"
    assert BLOCKED_POSTING_GONE in (result.detail or "")
    assert len(calls) == 1 and calls[0].source_job_id == "9001"
    assert pages == [], "no browser page may be opened for a posting that is gone"
    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        assert application.status == ApplicationStatus.BLOCKED.value
        assert application.blocked_reason == BLOCKED_POSTING_GONE
        assert application.submission_time is None
        assert repos.opportunities.get(prepared.opportunity_id).status == JobStatus.EXPIRED.value
        assert repos.jobs.get(prepared.job_id).status == JobStatus.EXPIRED.value
    expired = [
        e
        for e in _events(prepared, "opportunity", prepared.opportunity_id)
        if e.event_type == EventType.JOB_EXPIRED.value
    ]
    assert expired and expired[-1].payload["reason"] == BLOCKED_POSTING_GONE
    assert any(
        e.event_type == EventType.APPLICATION_BLOCKED.value
        and e.payload.get("reason") == BLOCKED_POSTING_GONE
        for e in _events(prepared)
    )


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("network is down"),
        VerifyResult(available=False, changed=False, http_status=None, detail="timed out"),
        VerifyResult(available=False, changed=False, http_status=503, detail="HTTP 503"),
    ],
)
def test_a_failed_check_does_not_expire_the_job(prepared, greenhouse_form_capture, failure) -> None:
    _live(prepared)
    pages: list = []

    def verify(_job):
        if isinstance(failure, Exception):
            raise failure
        return failure

    service = _service(prepared, answerable_capture(greenhouse_form_capture), verify, pages)
    result = service.submit(prepared.application_id, allow_real_submission=True)

    assert result.outcome == "SUBMITTED", (result.outcome, result.detail)
    assert len(pages) == 1
    with prepared.ctx.read_repos() as repos:
        assert repos.opportunities.get(prepared.opportunity_id).status == JobStatus.APPLIED.value
    if isinstance(failure, Exception):
        warnings = _warnings(prepared, "posting_check_failed")
        assert warnings
        assert "network is down" in warnings[0].payload["message"]


def test_a_live_posting_proceeds_without_a_warning(prepared, greenhouse_form_capture) -> None:
    _live(prepared)
    pages: list = []
    live = VerifyResult(available=True, changed=False, detail="live")
    service = _service(prepared, answerable_capture(greenhouse_form_capture), lambda _j: live, pages)
    assert service.submit(prepared.application_id, allow_real_submission=True).outcome == "SUBMITTED"
    assert _warnings(prepared, "posting_check_failed") == []
    assert _warnings(prepared, "posting_check_unavailable") == []


def test_a_dry_run_never_checks_the_posting(prepared, greenhouse_form_capture) -> None:
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="owner", acknowledge_unresolved=True
    )
    calls: list = []
    service = _service(
        prepared, answerable_capture(greenhouse_form_capture), lambda j: calls.append(j), []
    )
    result = service.submit(prepared.application_id, allow_real_submission=False)
    assert result.outcome == "DRY_RUN"
    assert calls == []


def test_posting_is_gone_only_on_a_clear_answer() -> None:
    gone = VerifyResult(available=False, changed=False, http_status=410)
    assert posting_is_gone(gone)
    assert posting_is_gone(VerifyResult(available=False, changed=False, detail="posting closed"))
    assert not posting_is_gone(VerifyResult(available=False, changed=False, detail="timed out"))
    assert not posting_is_gone(VerifyResult(available=False, changed=False, http_status=500))
    assert not posting_is_gone(VerifyResult(available=True, changed=False, http_status=404))
    assert not posting_is_gone(None)


def test_the_default_check_is_replaced_in_the_suite_so_no_test_reaches_the_network() -> None:
    from sabra_engine import execution

    verifier = execution.default_posting_verifier(None)
    assert verifier(object()).available is True


def test_the_real_default_check_asks_the_jobs_own_provider(prepared, monkeypatch) -> None:
    """Exercises `default_posting_verifier` itself with the provider's `verify` stubbed."""

    from types import SimpleNamespace

    from sabra_providers.greenhouse import GreenhouseProvider

    seen: dict = {}

    def fake_verify(self, ref, known_hash=None):
        seen["ref"] = ref
        seen["token"] = self.token
        return VerifyResult(available=False, changed=False, http_status=404, detail="posting closed")

    monkeypatch.setattr(GreenhouseProvider, "verify", fake_verify)
    job = SimpleNamespace(
        source="GREENHOUSE",
        board_token="test",  # the token of the registry entry the test fixture defines
        source_job_id="9001",
        source_url="https://job-boards.greenhouse.io/test/jobs/9001",
    )
    verifier = real_default_verifier(prepared.ctx)
    result = verifier(job)
    assert posting_is_gone(result)
    assert seen["ref"].source_job_id == "9001" and seen["token"] == "test"

    # An unregistered board still gets its provider type; a job that names no board gets none.
    other = SimpleNamespace(**{**job.__dict__, "board_token": "unregistered"})
    assert posting_is_gone(verifier(other))
    assert seen["token"] == "unregistered"
    assert verifier(SimpleNamespace(**{**job.__dict__, "board_token": None})) is None
    assert verifier(SimpleNamespace(**{**job.__dict__, "source": "WORKDAY"})) is None


# ---------------------------------------------------------------------------
# 5e. verify exactly what was shown
# ---------------------------------------------------------------------------


def _client(prepared, monkeypatch) -> TestClient:
    from sabra_api.main import app

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    return TestClient(app)


def test_verify_requires_the_value_to_match_when_one_is_sent(prepared, monkeypatch) -> None:
    client = _client(prepared, monkeypatch)
    recorded = client.post(
        "/api/answers/record", json={"key": "us_citizen", "value": "Yes"}
    )
    assert recorded.status_code == 200, recorded.text

    # The owner is shown "Yes"; the stored value then changes underneath them.
    client.post("/api/answers/record", json={"key": "us_citizen", "value": "No"})
    mismatch = client.post("/api/answers/verify", json={"key": "us_citizen", "value": "Yes"})
    assert mismatch.status_code == 422
    assert "not the value you were shown" in mismatch.json()["detail"]
    rows = {a["question_key"]: a for a in client.get("/api/answers").json()["answers"]}
    assert rows["us_citizen"]["verified"] is False

    empty = client.post("/api/answers/verify", json={"key": "us_citizen", "value": ""})
    assert empty.status_code == 422, "a blank value is a mismatch, not a wildcard"

    ok = client.post("/api/answers/verify", json={"key": "us_citizen", "value": "No"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["verified"] is True


def test_verify_without_a_value_still_works_for_the_cli(prepared) -> None:
    service = AnswerMemoryService(prepared.ctx)
    service.record("spanish", "No")
    assert service.verify("spanish")["verified"] is True
    service.record("shirt_size", "Large")
    with pytest.raises(ValidationFailure):
        service.verify("shirt_size", expected_value="Small")
    assert service.verify("shirt_size", expected_value="Large")["verified"] is True


def test_the_api_requires_the_value_to_verify(prepared, monkeypatch) -> None:
    client = _client(prepared, monkeypatch)
    client.post("/api/answers/record", json={"key": "spanish", "value": "No"})
    missing = client.post("/api/answers/verify", json={"key": "spanish"})
    assert missing.status_code == 422
    rows = {a["question_key"]: a for a in client.get("/api/answers").json()["answers"]}
    assert rows["spanish"]["verified"] is False


def test_a_yaml_boolean_is_compared_as_the_page_shows_it(prepared, monkeypatch) -> None:
    from sabra_domain.enums import TruthLevel
    from sabra_domain.schemas import AnswerRecord

    with prepared.ctx.repos() as repos:
        profile = repos.candidates.active_profile(prepared.candidate_id)
        profile.answer_memory["us_citizen"] = AnswerRecord(
            question_key="us_citizen", value=True, level=TruthLevel.INFERRED
        )
        profile.answer_memory["years"] = AnswerRecord(
            question_key="years", value=5.0, level=TruthLevel.INFERRED
        )
        repos.candidates.save_profile(prepared.candidate_id, profile)
    client = _client(prepared, monkeypatch)
    shown = {a["question_key"]: a["value"] for a in client.get("/api/answers").json()["answers"]}
    assert shown["us_citizen"] is True  # JSON true, which the page prints as "true"

    wrong = client.post("/api/answers/verify", json={"key": "us_citizen", "value": "True"})
    assert wrong.status_code == 422, "Python's spelling is not what the page showed"
    right = client.post("/api/answers/verify", json={"key": "us_citizen", "value": "true"})
    assert right.status_code == 200, right.text
    assert client.post("/api/answers/verify", json={"key": "years", "value": "5"}).status_code == 200


def test_the_answers_page_sends_the_value_it_displays() -> None:
    from sabra_api.main import app

    html = TestClient(app).get("/answers").text
    assert "value:btn.dataset.value" in html
    assert 'data-value="' in html
    assert "value:''" not in html


# ---------------------------------------------------------------------------
# 5f. acknowledging unresolved questions is not a path the product offers
# ---------------------------------------------------------------------------


def test_acknowledging_unresolved_questions_leaves_a_warning_event(prepared) -> None:
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )
    warnings = [e for e in _events(prepared) if e.event_type == EventType.WARNING_RECORDED.value]
    assert len(warnings) == 1
    assert warnings[0].payload["warning"] == "approved_with_unresolved_questions"
    assert warnings[0].payload["unresolved_questions"]
    assert warnings[0].payload["content_hash"] == prepared.package_hash


def test_the_api_never_approves_an_incomplete_package_and_leaves_no_warning(
    prepared, monkeypatch
) -> None:
    client = _client(prepared, monkeypatch)
    response = client.post(
        "/api/packages/approve",
        json={
            "approved_by": "owner",
            "packages": [
                {"package_id": prepared.package_id, "content_hash": prepared.package_hash}
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["approved"] == []
    assert body["refused"] and "COMPLETE" in body["refused"][0]["message"]
    assert _status_of(prepared) == ApplicationStatus.AWAITING_APPROVAL.value
    assert not [e for e in _events(prepared) if e.event_type == EventType.WARNING_RECORDED.value]


def test_the_cli_has_no_approve_command_that_could_acknowledge_unresolved() -> None:
    from sabra_engine.cli import app as cli_app

    names = {command.name or command.callback.__name__ for command in cli_app.registered_commands}
    groups = {group.name for group in cli_app.registered_groups}
    assert not any("approve" in str(name) for name in names | groups)


def test_batch_approval_refuses_an_incomplete_package(prepared) -> None:
    result = ApprovalService(prepared.ctx).approve_selected(
        [{"package_id": prepared.package_id, "content_hash": prepared.package_hash}],
        approved_by="owner",
    )
    assert result.approved == []
    assert result.refused and "COMPLETE" in result.refused[0]["message"]


# ---------------------------------------------------------------------------
# 5a. wording
# ---------------------------------------------------------------------------


def test_the_generated_hiring_manager_message_uses_the_agreed_wording(
    prepared, fixture_resume_text
) -> None:
    from conftest_app import build_job, build_profile
    from sabra_documents.master_resume import parse_master_resume
    from sabra_documents.package import PackageFactory

    # The message the product stored for the prepared application.
    with prepared.ctx.read_repos() as repos:
        stored = repos.packages.get(prepared.package_id).payload["hiring_manager_message"]
    assert stored.endswith("what success looks like in the first months.")
    assert "first year" not in stored

    # A profile whose own bullets do not use the word, so any "territory" in the output
    # would be the generator's, not the candidate's.
    profile = build_profile("cand_wording")
    for entry in profile.employment:
        entry.accomplishments = ["Grew regional revenue 43% over three years."]
    built = PackageFactory().build(
        profile=profile,
        master=parse_master_resume(fixture_resume_text),
        job=build_job(),
        master_resume_id="resume_1",
        job_id="job_1",
        opportunity_id="opp_1",
    )
    message = built.schema.hiring_manager_message or ""
    assert message.endswith("what success looks like in the first months.")
    assert "first year" not in message
    assert "territory" not in message.lower()
