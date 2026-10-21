"""A BLOCKED or FAILED application that never reached the employer can be rebuilt,
approved again, and submitted; one that may have reached the employer cannot.

Pages are local doubles. Nothing here opens a browser, a network connection or an
employer form.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fake_pages import GH_URL, FakeFormPage, answerable_capture
from fastapi.testclient import TestClient
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_domain.enums import ApplicationStatus, EntityType, EventType, JobStatus
from sabra_domain.errors import InvalidTransition, ValidationFailure
from sabra_domain.state import (
    application_path,
    assert_application_transition,
    assert_job_transition,
)
from sabra_engine.answer_memory import AnswerMemoryService
from sabra_engine.approval import ApprovalService
from sabra_engine.execution import AdapterRegistry, ExecutionService
from sabra_engine.handoff_manager import HANDOFFS
from sabra_engine.refresh import PackageRefreshService


@pytest.fixture(autouse=True)
def _clean_handoffs():
    HANDOFFS.clear()
    yield
    HANDOFFS.clear()


def _approve(prepared, package_id: str | None = None) -> None:
    ApprovalService(prepared.ctx).approve(
        package_id or prepared.package_id, approved_by="owner", acknowledge_unresolved=True
    )


def _status(prepared) -> str:
    with prepared.ctx.read_repos() as repos:
        return repos.applications.get(prepared.application_id).status


def _opportunity_status(prepared) -> str:
    with prepared.ctx.read_repos() as repos:
        return repos.opportunities.get(prepared.opportunity_id).status


def _events(prepared) -> list:
    with prepared.ctx.read_repos() as repos:
        return list(repos.events.for_entity("application", prepared.application_id))


def _force_state(prepared, *steps: ApplicationStatus) -> None:
    """Walk an approved application along legal edges, as a real attempt would."""

    with prepared.ctx.repos() as repos:
        for step in steps:
            repos.applications.set_status(prepared.application_id, step)


def _blocked(prepared) -> None:
    _approve(prepared)
    _force_state(prepared, ApplicationStatus.APPLYING, ApplicationStatus.BLOCKED)
    with prepared.ctx.repos() as repos:
        repos.applications.get(prepared.application_id).blocked_reason = "UNKNOWN_REQUIRED_QUESTION"


def _citizenship_capture(base: dict) -> dict:
    capture = answerable_capture(base)
    capture["fields"] = [
        *capture["fields"],
        {
            "id": "question_citizen",
            "tag": "input",
            "type": "text",
            "required": True,
            "label": "Are you a U.S. citizen?*",
            "options": [],
        },
    ]
    return capture


def _service(prepared, capture: dict) -> ExecutionService:
    def factory(_url: str) -> FakeFormPage:
        return FakeFormPage(capture, captcha=False, confirmation_url=GH_URL + "/confirmation")

    adapter = GreenhouseApplicationAdapter(dry_run=False, page_factory=factory)
    return ExecutionService(prepared.ctx, registry=AdapterRegistry([adapter]))


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------


def test_blocked_and_failed_return_to_approval_only_as_a_revocation() -> None:
    for status in (ApplicationStatus.BLOCKED, ApplicationStatus.FAILED):
        with pytest.raises(InvalidTransition):
            assert_application_transition(status, ApplicationStatus.AWAITING_APPROVAL)
        assert_application_transition(status, ApplicationStatus.AWAITING_APPROVAL, revoke=True)
        with pytest.raises(InvalidTransition):
            application_path(status, ApplicationStatus.AWAITING_APPROVAL)
    # The approved path into the queue is still not a plain move.
    with pytest.raises(InvalidTransition):
        application_path(ApplicationStatus.APPROVED, ApplicationStatus.AWAITING_APPROVAL)

    with pytest.raises(InvalidTransition):
        assert_job_transition(JobStatus.FAILED, JobStatus.AWAITING_APPROVAL)
    assert_job_transition(JobStatus.FAILED, JobStatus.AWAITING_APPROVAL, revoke=True)
    # Nothing was submitted, so a failed posting may still expire.
    assert_job_transition(JobStatus.FAILED, JobStatus.EXPIRED)


# ---------------------------------------------------------------------------
# refresh from BLOCKED / FAILED
# ---------------------------------------------------------------------------


def test_blocked_with_no_submission_refreshes_is_approved_again_and_submits(
    prepared, greenhouse_form_capture
) -> None:
    capture = _citizenship_capture(greenhouse_form_capture)
    prepared.ctx.settings.automation_dry_run = False
    _approve(prepared)
    service = _service(prepared, capture)

    blocked = service.submit(prepared.application_id, allow_real_submission=True)
    assert blocked.outcome == "BLOCKED", (blocked.outcome, blocked.detail)
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    old_hash = prepared.package_hash
    with prepared.ctx.read_repos() as repos:
        assert repos.applications.get(prepared.application_id).last_error, "the block explained itself"

    # The owner records and verifies the missing answer, then rebuilds the package.
    answers = AnswerMemoryService(prepared.ctx)
    answers.record("us_citizen", "Yes", question_text="Are you a U.S. citizen?")
    answers.verify("us_citizen", expected_value="Yes")
    refreshed = PackageRefreshService(prepared.ctx).refresh(prepared.application_id)

    assert refreshed.old_hash == old_hash
    assert refreshed.package_id != prepared.package_id
    assert refreshed.revision >= 2
    assert _status(prepared) == ApplicationStatus.AWAITING_APPROVAL.value
    assert _opportunity_status(prepared) == JobStatus.AWAITING_APPROVAL.value
    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        assert application.approved_package_hash is None
        assert application.blocked_reason is None
        assert application.last_error is None, "a stale error must not follow the new revision"
        package = repos.packages.get(refreshed.package_id)
        assert any(
            a["question_key"] == "us_citizen" and a["answer"] == "Yes"
            for a in package.payload["answers"]
        )
    revoked = [e for e in _events(prepared) if e.event_type == EventType.APPROVAL_REVOKED.value]
    assert revoked[-1].payload["old_hash"] == old_hash
    assert revoked[-1].payload["from_status"] == "BLOCKED"

    # Until it is approved again, nothing can be submitted.
    again = service.submit(prepared.application_id, allow_real_submission=True)
    assert again.outcome == "BLOCKED"
    assert _status(prepared) == ApplicationStatus.AWAITING_APPROVAL.value

    _approve(prepared, refreshed.package_id)
    submitted = service.submit(prepared.application_id, allow_real_submission=True)
    assert submitted.outcome == "SUBMITTED", (submitted.outcome, submitted.detail, submitted.stops)
    assert _status(prepared) == ApplicationStatus.APPLIED.value


def test_failed_application_and_failed_opportunity_refresh(prepared) -> None:
    _approve(prepared)
    _force_state(prepared, ApplicationStatus.APPLYING, ApplicationStatus.FAILED)
    with prepared.ctx.repos() as repos:
        repos.opportunities.set_status(prepared.opportunity_id, JobStatus.APPLYING)
        repos.opportunities.set_status(prepared.opportunity_id, JobStatus.FAILED)
    assert _opportunity_status(prepared) == JobStatus.FAILED.value

    result = PackageRefreshService(prepared.ctx).refresh(prepared.application_id)

    assert result.package_id != prepared.package_id
    assert _status(prepared) == ApplicationStatus.AWAITING_APPROVAL.value
    assert _opportunity_status(prepared) == JobStatus.AWAITING_APPROVAL.value
    _approve(prepared, result.package_id)
    assert _status(prepared) == ApplicationStatus.APPROVED.value
    assert _opportunity_status(prepared) == JobStatus.APPROVED.value


def test_blocked_with_an_unresolved_unconfirmed_click_is_not_refreshed(prepared) -> None:
    _blocked(prepared)
    with prepared.ctx.repos() as repos:
        repos.events.append(
            EntityType.APPLICATION,
            prepared.application_id,
            EventType.APPLICATION_STARTED,
            {"stage": "claim"},
        )
        repos.events.append(
            EntityType.APPLICATION,
            prepared.application_id,
            EventType.APPLICATION_UNCONFIRMED,
            {"message": "clicked; nothing confirms it"},
        )

    with pytest.raises(ValidationFailure) as excinfo:
        PackageRefreshService(prepared.ctx).refresh(prepared.application_id)
    assert "unconfirmed" in str(excinfo.value).lower()
    assert "not refreshed" in str(excinfo.value).lower()
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    assert PackageRefreshService(prepared.ctx).refresh_all_unsubmitted().refreshed == []

    # Once a person has reported on that click, the attempt is resolved and refresh opens.
    with prepared.ctx.repos() as repos:
        repos.events.append(
            EntityType.APPLICATION,
            prepared.application_id,
            EventType.HANDED_TO_HUMAN,
            {"source": "user_report", "outcome": "abandoned"},
        )
    PackageRefreshService(prepared.ctx).refresh(prepared.application_id)
    assert _status(prepared) == ApplicationStatus.AWAITING_APPROVAL.value


@pytest.mark.parametrize("evidence", ["submission_time", "submitted_event"])
def test_blocked_with_a_recorded_submission_is_not_refreshed(prepared, evidence) -> None:
    _blocked(prepared)
    with prepared.ctx.repos() as repos:
        if evidence == "submission_time":
            repos.applications.get(prepared.application_id).submission_time = datetime.now(UTC)
        else:
            repos.events.append(
                EntityType.APPLICATION,
                prepared.application_id,
                EventType.APPLICATION_SUBMITTED,
                {"late_confirmation": True},
            )
    with pytest.raises(ValidationFailure) as excinfo:
        PackageRefreshService(prepared.ctx).refresh(prepared.application_id)
    assert "already submitted" in str(excinfo.value).lower()


def test_an_expired_posting_is_not_refreshed(prepared) -> None:
    _blocked(prepared)
    with prepared.ctx.repos() as repos:
        repos.opportunities.set_status(prepared.opportunity_id, JobStatus.EXPIRED)
    with pytest.raises(ValidationFailure) as excinfo:
        PackageRefreshService(prepared.ctx).refresh(prepared.application_id)
    assert "no longer available" in str(excinfo.value)


def test_prepare_on_a_blocked_application_points_to_refresh(prepared) -> None:
    from sabra_engine.documents import DocumentService

    _blocked(prepared)
    with pytest.raises(ValidationFailure) as excinfo:
        DocumentService(prepared.ctx).prepare_package(prepared.opportunity_id)
    assert "Refresh package" in str(excinfo.value)


# ---------------------------------------------------------------------------
# the dashboard
# ---------------------------------------------------------------------------


def test_applications_page_offers_refresh_on_blocked_and_failed_rows() -> None:
    from sabra_api.main import app

    html = TestClient(app).get("/applications").text
    assert "['APPROVED','BLOCKED','FAILED'].includes(p.application_status)" in html
    assert "Refresh package" in html


def test_refresh_endpoint_rebuilds_a_blocked_application(prepared, monkeypatch) -> None:
    from sabra_api.main import app

    _blocked(prepared)
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    client = TestClient(app)
    response = client.post(f"/api/applications/{prepared.application_id}/refresh")
    assert response.status_code == 200, response.text
    assert response.json()["application_status"] == ApplicationStatus.AWAITING_APPROVAL.value

    rows = {p["application_id"]: p for p in client.get("/api/packages").json()["packages"]}
    assert rows[prepared.application_id]["application_status"] == "AWAITING_APPROVAL"


def test_refresh_endpoint_refuses_a_blocked_application_with_an_unconfirmed_click(
    prepared, monkeypatch
) -> None:
    from sabra_api.main import app

    _blocked(prepared)
    with prepared.ctx.repos() as repos:
        repos.events.append(
            EntityType.APPLICATION,
            prepared.application_id,
            EventType.APPLICATION_UNCONFIRMED,
            {"message": "clicked"},
        )
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    response = TestClient(app).post(f"/api/applications/{prepared.application_id}/refresh")
    assert response.status_code == 409
    assert "unconfirmed" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# giving up
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("final", [ApplicationStatus.BLOCKED, ApplicationStatus.FAILED])
def test_the_owner_can_reject_a_blocked_or_failed_application(prepared, final) -> None:
    _approve(prepared)
    _force_state(prepared, ApplicationStatus.APPLYING, final)

    outcome = ApprovalService(prepared.ctx).reject(
        prepared.package_id, decided_by="owner", reason="not worth another attempt"
    )

    assert outcome.decision == "REJECTED"
    assert _status(prepared) == ApplicationStatus.WITHDRAWN.value
    assert _opportunity_status(prepared) == JobStatus.WITHDRAWN.value
    with pytest.raises(InvalidTransition):
        application_path(ApplicationStatus.WITHDRAWN, ApplicationStatus.APPROVED)


def test_reject_is_refused_when_a_click_may_have_reached_the_employer(prepared) -> None:
    _blocked(prepared)
    with prepared.ctx.repos() as repos:
        repos.events.append(
            EntityType.APPLICATION,
            prepared.application_id,
            EventType.APPLICATION_UNCONFIRMED,
            {"message": "clicked"},
        )
    with pytest.raises(ValidationFailure):
        ApprovalService(prepared.ctx).reject(prepared.package_id, decided_by="owner")
    assert _status(prepared) == ApplicationStatus.BLOCKED.value
    assert _opportunity_status(prepared) == JobStatus.APPROVED.value
