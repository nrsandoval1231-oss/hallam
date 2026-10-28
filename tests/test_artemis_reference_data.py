"""Reference Home data stays grounded in candidate-owned engine records."""

from fastapi.testclient import TestClient
from sabra_api.main import HOME_HIGH_CONFIDENCE_THRESHOLD, app
from sabra_domain import models
from sabra_domain.enums import EntityType, EventType


def test_empty_home_has_no_synthetic_metrics_or_activity() -> None:
    body = TestClient(app).get("/api/home").json()
    assert body["reference_metrics"] == {
        "analyzed": 0, "high_confidence": 0, "ready": 0, "updates": 0
    }
    assert body["brief_items"] == [{
        "title": "Create your candidate profile",
        "detail": "Add your profile before ARTEMIS can assess roles.",
        "href": "/candidate",
    }]
    assert body["top_opportunities"] == []
    assert body["recent_activity"] == []


def test_reference_home_counts_active_search_and_excludes_other_candidate(prepared, monkeypatch) -> None:
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    with prepared.ctx.repos() as repos:
        repos.session.add(models.FitAssessmentRow(
            opportunity_id=prepared.opportunity_id,
            candidate_id=prepared.candidate_id,
            confidence=HOME_HIGH_CONFIDENCE_THRESHOLD,
            fit_score=0.82,
            why_this_job="Recorded match across the candidate's experience and location.",
        ))
        other = repos.candidates.create("Other Candidate", email="other@example.test")
        other.is_primary = False
        other_opportunity = models.Opportunity(
            candidate_id=other.id,
            search_profile_id="other-search",
            canonical_key="other-canonical-key",
            title="Confidential Role",
            company="Other Candidate Company",
            status="SHORTLISTED",
            shortlisted=True,
        )
        repos.session.add(other_opportunity)
        repos.session.flush()
        repos.events.append(
            EntityType.OPPORTUNITY,
            other_opportunity.id,
            EventType.JOB_SHORTLISTED,
            {"title": "Confidential Role", "company": "Other Candidate Company"},
        )
        long_run = models.PipelineRunRow(
            id="run-long-summary",
            kind="full",
            status="COMPLETED",
            prepare=False,
            candidate_id=prepared.candidate_id,
            search_profile_id=prepared.search_profile_id,
        )
        repos.session.add(long_run)
        repos.session.flush()
        repos.events.append(
            EntityType.PIPELINE_RUN,
            long_run.id,
            EventType.PIPELINE_RUN_COMPLETED,
            {"summary": "Completed run evidence. " * 40},
        )

    body = TestClient(app).get("/api/home").json()
    metrics = body["reference_metrics"]
    assert metrics["analyzed"] == 1
    assert metrics["high_confidence"] == 1
    assert metrics["ready"] == body["today"]["counts"]["ready_for_approval"]
    assert metrics["updates"] == body["today"]["counts"]["employer_responses"]
    assert len(body["top_opportunities"]) == 1
    top = body["top_opportunities"][0]
    assert top["title"] == "Regional Sales Manager - Medical Aesthetics"
    assert top["confidence"] == HOME_HIGH_CONFIDENCE_THRESHOLD
    assert top["why_this_job"]
    assert top["compensation_text"]
    assert top["href"] == f"/opportunities/{prepared.opportunity_id}"
    assert len(body["brief_items"]) <= 4
    assert len(body["recent_activity"]) <= 5
    assert any(item["label"] == "Application package prepared" for item in body["recent_activity"])
    all_activity = " ".join(item["detail"] for item in body["recent_activity"])
    assert "Other Candidate Company" not in all_activity
    assert "Confidential Role" not in str(body["top_opportunities"])
    assert all(item["occurred_at"] and item["href"] for item in body["recent_activity"])
    run_activity = next(item for item in body["recent_activity"] if item["label"] == "Search run completed")
    assert run_activity["detail"].startswith("Completed run evidence.")
    assert len(run_activity["detail"]) <= 240


def test_activity_distinguishes_owner_rejection_from_employer_rejection(prepared, monkeypatch):
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    client = TestClient(app)
    response = client.post(f"/api/packages/{prepared.package_id}/reject", json={"reason": "Not a role I want"})
    assert response.status_code == 200
    activity = client.get("/api/home").json()["recent_activity"]
    assert "Application declined by owner" in [row["label"] for row in activity]
    assert "Employer rejection recorded" not in [row["label"] for row in activity]
    with prepared.ctx.repos() as repos:
        repos.events.append(EntityType.APPLICATION, prepared.application_id, EventType.REJECTED,
                            {"outcome_kind": "REJECTION"})
    activity = client.get("/api/home").json()["recent_activity"]
    assert activity[0]["label"] == "Employer rejection recorded"


def test_activity_distinguishes_document_draft_from_execution_start(prepared, monkeypatch):
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    client = TestClient(app)
    labels = [row["label"] for row in client.get("/api/home").json()["recent_activity"]]
    assert "Application draft prepared" in labels
    assert "Application execution started" not in labels
    with prepared.ctx.repos() as repos:
        # A local event fixture checks presentation; no browser adapter or submission runs.
        repos.events.append(EntityType.APPLICATION, prepared.application_id, EventType.APPLICATION_STARTED,
                            {"adapter": "GREENHOUSE", "dry_run": False})
    activity = client.get("/api/home").json()["recent_activity"]
    assert activity[0]["label"] == "Application execution started"
    assert "submitted" not in activity[0]["label"].lower()
