"""Grounding checks for the ARTEMIS brief, career plan and deterministic Ask."""

from fastapi.testclient import TestClient
from sabra_api.main import _next_action, app


def test_empty_state_has_honest_brief_and_setup_action() -> None:
    body = TestClient(app).get("/api/home").json()
    assert body["morning_brief"]["run_status"] == "NEVER_RUN"
    assert "has not completed a search run" in body["morning_brief"]["narrative"]
    assert body["next_action"]["href"] == "/candidate"
    assert set(body["next_action"]) == {"label", "href", "reason"}


def test_seeded_state_explains_shortlist_and_why_intent(prepared, monkeypatch) -> None:
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    client = TestClient(app)
    home = client.get("/api/home").json()
    assert home["metrics"]["matches"] == 1
    assert home["next_action"]["href"] == "/applications?filter=needs_information"
    assert home["morning_brief"]["run_status"] == "NEVER_RUN"
    plan = client.get("/api/career-plan").json()
    assert {"evidence", "inferences", "practical_actions"} <= plan.keys()
    answer = client.post(
        "/api/ask", json={"question": "Why is this opportunity recommended?"}
    ).json()["answer"]
    assert "Regional Sales Manager" in answer
    assert "strongest matches" not in answer
    assert "Deterministic answer" in answer


def test_changed_question_uses_stored_run_baseline_language(prepared, monkeypatch) -> None:
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    answer = TestClient(app).post(
        "/api/ask", json={"question": "What changed since yesterday?"}
    ).json()["answer"]
    assert "no completed run yet" in answer
    assert "run-to-run" in answer


def test_next_action_prioritizes_live_work_and_uses_real_page_filters() -> None:
    base = {"candidate_id": "candidate", "has_resume": True, "search_profile_id": "search",
            "counts": {"handoffs_waiting": 1, "needs_information": 1,
                       "ready_for_approval": 1, "employer_responses": 1, "new_high_fit": 1}}
    assert _next_action(base)["href"] == "/applications?filter=handoffs"
    base["counts"]["handoffs_waiting"] = 0
    assert _next_action(base)["href"] == "/applications?filter=needs_information"
    base["counts"]["needs_information"] = 0
    assert _next_action(base)["href"] == "/applications?filter=ready"
    base["counts"]["ready_for_approval"] = 0
    assert _next_action(base)["href"] == "/pipeline"


def test_brief_uses_only_the_active_search_and_labels_research_units(prepared, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sabra_domain import models

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    now = datetime.now(UTC)
    with prepared.ctx.repos() as repos:
        # An older run from this search must survive a newer run on another profile.
        repos.session.add(models.PipelineRunRow(
            id="run-active", kind="full", status="COMPLETED", prepare=False,
            candidate_id=prepared.candidate_id, search_profile_id=prepared.search_profile_id,
            created_at=now - timedelta(hours=2), started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1),
            progress={"stages": {"discover": {"jobs_new": 3, "jobs_seen": 99},
                                  "research": {"researched": 2}}},
        ))
        repos.session.add(models.PipelineRunRow(
            id="run-other", kind="full", status="COMPLETED", prepare=False,
            candidate_id=prepared.candidate_id, search_profile_id="other-search",
            created_at=now, started_at=now, finished_at=now,
            progress={"stages": {"discover": {"jobs_new": 999}}},
        ))
    client = TestClient(app)
    brief = client.get("/api/home").json()["morning_brief"]
    assert brief["run_at"] == (now - timedelta(hours=1)).isoformat()
    assert [(row["label"], row["value"]) for row in brief["changes"]] == [
        ("New jobs", "3"), ("Roles researched", "2")]
    # Switching searches must not borrow findings from the previous profile.
    with prepared.ctx.repos() as repos:
        previous = repos.search_profiles.get(prepared.search_profile_id)
        new = previous.model_copy(update={"id": "new-search", "name": "New search", "is_active": False})
        repos.search_profiles.create(new)
        repos.search_profiles.activate(prepared.candidate_id, new.id)
    switched = client.get("/api/home").json()
    assert switched["search_profile_name"] == "New search"
    assert switched["morning_brief"]["run_status"] == "NEVER_RUN"
    assert switched["morning_brief"]["changes"] == []
