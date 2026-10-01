"""Verified contact answers fill blank package fields, and refresh revokes approval.

Nothing here opens a network connection or a live employer form.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest_app import build_job, build_profile
from sabra_automation.base import ApplicationForm, FormField
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_automation.lever_adapter import _lever_field_key, _lever_values
from sabra_documents.package import _package_contact
from sabra_domain.enums import ApplicationStatus, JobStatus, TruthLevel
from sabra_domain.errors import ApprovalRequired, InvalidTransition, ValidationFailure
from sabra_domain.schemas import AnswerRecord, ApplicationPackageSchema
from sabra_domain.state import application_path, assert_application_transition
from sabra_engine.approval import ApprovalService
from sabra_engine.documents import DocumentService
from sabra_engine.refresh import PackageRefreshService


def _verified(key: str, value: str) -> AnswerRecord:
    return AnswerRecord(
        question_key=key,
        value=value,
        level=TruthLevel.VERIFIED,
        evidence=["owner_verified"],
    )


def test_verified_answers_fill_blank_contact_fields_and_profile_wins() -> None:
    profile = build_profile("cand_contact")
    profile.identity.phone = None
    profile.identity.city = None
    profile.identity.state = None
    profile.identity.postal_code = None
    profile.identity.country = "USA"
    profile.provenance.pop("identity.country", None)
    profile.answer_memory.update(
        {
            "phone": _verified("phone", "(214) 555-0199"),
            "city": _verified("city", "Dallas"),
            "state_residence": _verified("state_residence", "TX"),
            "postal_code": _verified("postal_code", "75201"),
            "country": _verified("country", "USA"),
            "street_address": _verified("street_address", "4100 Ross Avenue"),
            "salary_expectations": AnswerRecord(
                question_key="salary_expectations",
                value="should not be used",
                level=TruthLevel.INFERRED,
            ),
        }
    )
    contact = _package_contact(profile)
    assert contact["phone"] == "(214) 555-0199"
    assert contact["city"] == "Dallas"
    assert contact["state"] == "TX"
    assert contact["postal_code"] == "75201"
    assert contact["country"] == "United States"
    assert contact["street_address"] == "4100 Ross Avenue"

    profile.identity.phone = "(214) 555-0100"
    profile.answer_memory["phone"] = _verified("phone", "(214) 555-0199")
    assert _package_contact(profile)["phone"] == "(214) 555-0100"

    profile.identity.phone = None
    profile.answer_memory["phone"] = AnswerRecord(
        question_key="phone", value="(214) 555-0199", level=TruthLevel.INFERRED
    )
    assert _package_contact(profile)["phone"] is None


def test_street_address_maps_onto_greenhouse_and_lever_fields() -> None:
    package = ApplicationPackageSchema(
        opportunity_id="opp",
        candidate_id="cand",
        job_id="job",
        applicant_street_address="4100 Ross Avenue",
        applicant_name="Jordan Rivera",
        applicant_email="jordan.rivera@example.test",
    )
    adapter = GreenhouseApplicationAdapter(dry_run=True)
    assert adapter._bridge_value(package, "street_address") == "4100 Ross Avenue"
    form = ApplicationForm(
        url="http://127.0.0.1/fixture",
        fields=[
            FormField(name="address", label="Street address", required=True, selector="#address"),
        ],
        submit_selector='button[type="submit"]',
    )
    report = adapter.validate(package, form)
    assert "4100" not in " ".join(report.unknown_required_fields)
    assert report.mapped_fields.get("address") == "identity"
    field = FormField(name="address", label="Address")
    assert _lever_field_key(field) == "street_address"
    assert _lever_values(package)["street_address"] == "4100 Ross Avenue"


def test_applications_page_offers_refresh_and_a_confirm_dialog() -> None:
    from fastapi.testclient import TestClient
    from sabra_api.main import app

    html = TestClient(app).get("/applications").text
    assert "Refresh package" in html
    assert 'id="refresh-all"' in html
    assert "Refresh all unsubmitted" in html
    assert 'id="confirm-refresh"' in html
    assert "Each approval is revoked" in html


def test_plain_status_change_cannot_return_an_approved_application() -> None:
    with pytest.raises(InvalidTransition):
        assert_application_transition(
            ApplicationStatus.APPROVED, ApplicationStatus.AWAITING_APPROVAL
        )
    assert_application_transition(
        ApplicationStatus.APPROVED, ApplicationStatus.AWAITING_APPROVAL, revoke=True
    )
    with pytest.raises(InvalidTransition):
        application_path(ApplicationStatus.APPROVED, ApplicationStatus.AWAITING_APPROVAL)


def _second_approved(prepared):
    with prepared.ctx.repos() as repos:
        row, _, _ = repos.jobs.upsert_canonical(
            build_job(source_job_id="9002", title="Account Executive"), raw_payload={}
        )
        row.status = str(JobStatus.NORMALIZED)
        opportunity = repos.opportunities.create(
            candidate_id=prepared.candidate_id,
            search_profile_id=prepared.search_profile_id,
            canonical_key=row.canonical_key,
            title=row.title,
            company=row.company,
            location=row.location,
            primary_job_id=row.id,
            source_ids=[
                {"source": row.source, "source_job_id": row.source_job_id, "url": row.source_url}
            ],
        )
        row.opportunity_id = opportunity.id
        opportunity.status = str(JobStatus.SHORTLISTED)
        opportunity.shortlisted = True
        opportunity_id = opportunity.id
        repos.session.flush()
    prepared_row = DocumentService(prepared.ctx).prepare_package(opportunity_id)
    ApprovalService(prepared.ctx).approve(
        prepared_row.package_id, approved_by="owner", acknowledge_unresolved=True
    )
    return prepared_row


def test_refresh_revokes_approval_and_builds_a_new_revision(prepared) -> None:
    approval = ApprovalService(prepared.ctx)
    approval.approve(prepared.package_id, approved_by="owner", acknowledge_unresolved=True)
    with prepared.ctx.repos() as repos:
        profile = repos.candidates.active_profile(prepared.candidate_id)
        profile.answer_memory["street_address"] = _verified("street_address", "4100 Ross Avenue")
        repos.candidates.save_profile(prepared.candidate_id, profile)
        before = repos.applications.get(prepared.application_id)
        old_hash = before.approved_package_hash

    with pytest.raises(InvalidTransition), prepared.ctx.repos() as repos:
        repos.applications.set_status(
            prepared.application_id, ApplicationStatus.AWAITING_APPROVAL
        )

    other = _second_approved(prepared)
    result = PackageRefreshService(prepared.ctx).refresh_all_unsubmitted()
    assert len(result.refreshed) == 2
    assert result.refused == []
    refreshed = {item.application_id: item for item in result.refreshed}
    first = refreshed[prepared.application_id]
    assert first.old_hash == old_hash
    assert first.package_id != prepared.package_id
    assert first.revision >= 2

    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        assert application.status == ApplicationStatus.AWAITING_APPROVAL.value
        assert application.approved_package_hash is None
        latest = repos.packages.latest_for_opportunity(prepared.opportunity_id)
        assert latest.id == first.package_id
        assert latest.payload["applicant_street_address"] == "4100 Ross Avenue"
        assert latest.content_hash != old_hash
        events = repos.events.for_entity("application", prepared.application_id)
        revoked = [event for event in events if event.event_type == "APPROVAL_REVOKED"]
        assert revoked and revoked[-1].payload["old_hash"] == old_hash
        other_app = repos.applications.get(other.application_id)
        assert other_app.status == ApplicationStatus.AWAITING_APPROVAL.value

    with pytest.raises(ApprovalRequired):
        approval.assert_submission_authorised(prepared.application_id)


def test_refresh_refuses_a_submitted_application(prepared, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from sabra_api.main import app

    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="owner", acknowledge_unresolved=True
    )
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLIED)
        application = repos.applications.get(prepared.application_id)
        application.submission_time = datetime.now(UTC)
        repos.session.flush()

    with pytest.raises(ValidationFailure) as excinfo:
        PackageRefreshService(prepared.ctx).refresh(prepared.application_id)
    assert "already submitted" in str(excinfo.value).lower()
    assert "not refreshed" in str(excinfo.value).lower()

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    response = TestClient(app).post(f"/api/applications/{prepared.application_id}/refresh")
    assert response.status_code == 409
    assert "already submitted" in response.json()["detail"].lower()

    bulk = PackageRefreshService(prepared.ctx).refresh_all_unsubmitted()
    assert bulk.refreshed == []
