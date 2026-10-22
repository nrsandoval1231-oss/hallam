"""Opportunity detail (PRD section 30): the payload, the page, and the 404."""

from __future__ import annotations

import pytest
from p2b_support import (
    api,
    approve,
    mark_applied,
    seed_scoring,
)
from sabra_domain.enums import EntityType, EventType, JobStatus
from sabra_domain.errors import NotFound
from sabra_engine.event_summary import summarize_payload
from sabra_engine.opportunity_detail import OpportunityDetailService


def test_detail_payload_has_every_section(prepared, monkeypatch) -> None:
    seed_scoring(prepared)
    body = api(prepared, monkeypatch).get(f"/api/opportunities/{prepared.opportunity_id}").json()

    job = body["job"]
    assert job["title"] == "Regional Sales Manager - Medical Aesthetics"
    assert job["company"] == "Revance Therapeutics"
    assert job["compensation_text"] == "$110,000 to $140,000 a year"
    assert job["remote_status"] == "ONSITE" and job["employment_type"] == "FULL_TIME"
    assert job["date_posted"] == "2026-08-01"
    assert job["source"] == "GREENHOUSE" and job["source_url"].startswith("https://")
    assert "Own the Dallas region" in job["description"]
    assert job["requirements"] and job["responsibilities"]
    assert {"preferred_qualifications", "date_discovered", "last_verified"} <= set(job)

    fit = body["fit"]
    assert fit["fit_score"] == pytest.approx(0.82)
    assert fit["confidence"] == pytest.approx(0.74)
    components = {c["key"]: c for c in fit["components"]}
    assert set(components) == {
        "career_score",
        "qualification_score",
        "compensation_score",
        "location_score",
        "industry_score",
        "trajectory_score",
    }
    assert components["career_score"]["score"] == pytest.approx(0.9)
    assert components["career_score"]["rationale"].startswith("title overlaps")
    assert components["trajectory_score"]["rationale"] == ""
    assert fit["strengths"] and fit["gaps"] and fit["risks"]
    assert fit["why_this_job"].startswith("Your territory")

    assert body["rank"] == {"score": 0.71, "position": 1, "of": 1}
    assert body["application"]["status"] == "AWAITING_APPROVAL"
    package = body["package"]
    assert package["id"] == prepared.package_id
    assert package["review_url"] == f"/packages/{prepared.package_id}"
    assert package["content_hash"] == prepared.package_hash
    assert body["actions"] == {"can_prepare": True, "can_pass": True, "passed": False}


def test_research_keeps_verified_facts_apart_from_inferences(prepared, monkeypatch) -> None:
    seed_scoring(prepared)
    research = (
        api(prepared, monkeypatch).get(f"/api/opportunities/{prepared.opportunity_id}").json()[
            "research"
        ]
    )
    assert research["verified_facts"] == ["Headquartered in Nashville"]
    assert research["inferences"] == ["Likely reports to a VP of Sales"]
    assert research["sources"] == ["https://example.test/about"]
    assert research["industry"] == "Medical aesthetics"


def test_unscored_and_unresearched_say_so(prepared) -> None:
    body = OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)
    assert body["fit"] is None
    assert body["research"] is None
    assert body["hard_filter"]["decision"] == "not_evaluated"
    assert "not been run" in body["hard_filter"]["summary"]


def test_hard_filter_decision_says_why_it_passed(prepared) -> None:
    seed_scoring(prepared, passed=True)
    decision = OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)["hard_filter"]
    assert decision["decision"] == "passed"
    assert decision["checks_run"] == ["compensation", "geography"]
    assert decision["checks_skipped"] == ["travel"]
    assert "2 check(s) run" in decision["summary"] and "1 skipped" in decision["summary"]


def test_hard_filter_decision_says_why_it_was_rejected(prepared) -> None:
    seed_scoring(prepared, passed=False)
    decision = OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)["hard_filter"]
    assert decision["decision"] == "rejected"
    assert decision["reasons"] == ["BELOW_MINIMUM_COMPENSATION"]
    assert decision["detail"]["BELOW_MINIMUM_COMPENSATION"] == "pays 60000, minimum is 90000"
    assert "BELOW_MINIMUM_COMPENSATION" in decision["summary"]


def test_the_latest_filter_decision_wins_and_an_owner_pass_is_reported_apart(prepared) -> None:
    seed_scoring(prepared, passed=False)
    seed_scoring(prepared, passed=True)
    from sabra_engine.intelligence import pass_opportunity

    pass_opportunity(prepared.ctx, prepared.opportunity_id)
    body = OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)
    assert body["hard_filter"]["decision"] == "passed"
    assert body["hard_filter"]["owner_passed"] is True
    assert body["actions"] == {"can_prepare": False, "can_pass": False, "passed": True}


def test_history_merges_opportunity_job_package_and_application_events(prepared) -> None:
    seed_scoring(prepared)
    with prepared.ctx.repos() as repos:
        repos.events.append(
            EntityType.JOB,
            prepared.job_id,
            EventType.JOB_DISCOVERED,
            {"source": "GREENHOUSE", "source_job_id": "9001"},
        )
    history = OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)["history"]
    kinds = {(e["entity_type"], e["event_type"]) for e in history}
    assert ("job", "JOB_DISCOVERED") in kinds
    assert ("opportunity", "JOB_SCORED") in kinds
    assert ("package", "PACKAGE_GENERATED") in kinds
    assert [e["occurred_at"] for e in history] == sorted(e["occurred_at"] for e in history)
    scored = next(e for e in history if e["event_type"] == "JOB_SCORED")
    assert "stage: hard_filter_passed" in scored["summary"]
    discovered = next(e for e in history if e["event_type"] == "JOB_DISCOVERED")
    assert discovered["summary"] == "source: GREENHOUSE; source job id: 9001"


def test_history_follows_the_application_into_the_pipeline(prepared) -> None:
    mark_applied(prepared)
    from sabra_engine.ledger import LedgerService

    LedgerService(prepared.ctx).record_outcome(prepared.application_id, "PHONE_SCREEN")
    body = OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)
    assert any(
        e["entity_type"] == "application" and e["event_type"] == "SCREEN_REQUESTED"
        for e in body["history"]
    )
    assert body["application"]["status"] == "APPLIED"
    assert body["application"]["submission_method"] == "HEADED_HUMAN"
    assert body["actions"]["can_prepare"] is False and body["actions"]["can_pass"] is False


def test_actions_close_once_the_application_is_approved(prepared) -> None:
    approve(prepared)
    actions = OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)["actions"]
    assert actions == {"can_prepare": False, "can_pass": False, "passed": False}


def test_an_unknown_opportunity_is_a_404_in_the_api_and_the_service(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    assert client.get("/api/opportunities/opp_missing").status_code == 404
    with pytest.raises(NotFound):
        OpportunityDetailService(prepared.ctx).detail("opp_missing")


def test_the_detail_page_and_its_404(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    page = client.get(f"/opportunities/{prepared.opportunity_id}")
    assert page.status_code == 200
    assert page.text.count("<script>") == 1
    assert 'href="/opportunities"' in page.text and "Prepare application" in page.text
    assert f'"{prepared.opportunity_id}"' in page.text

    missing = client.get("/opportunities/opp_missing")
    assert missing.status_code == 404
    assert "Opportunity not found" in missing.text
    assert 'class="topnav"' in missing.text and "<script" not in missing.text


def test_the_id_is_embedded_as_an_inert_string(prepared, monkeypatch) -> None:
    from sabra_api.review_pages import js_string

    assert "<" not in js_string("</script><b>") and ">" not in js_string("</script><b>")
    assert js_string('a"b') == '"a\\"b"'
    # An unknown id with markup in it is a 404 page that shows no markup of its own.
    client = api(prepared, monkeypatch)
    response = client.get("/opportunities/%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert response.status_code == 404 and "<script>alert" not in response.text


def test_summaries_are_short_and_flat() -> None:
    assert summarize_payload({}) == ""
    assert summarize_payload({"a": None, "b": "", "c": [], "d": {"x": 1}}) == ""
    assert summarize_payload({"title": "X", "n": 3, "ok": True}) == "title: X; n: 3; ok: yes"
    assert summarize_payload({"items": ["a", "b"], "many": [1, 2, 3, 4]}) == (
        "items: a, b; many: 4 item(s)"
    )
    long = summarize_payload({"text": "word " * 200}, limit=60)
    assert len(long) <= 60 and long.endswith("…")


def test_the_pass_route_still_serves_the_detail_actions(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    assert client.post(f"/api/opportunities/{prepared.opportunity_id}/pass").status_code == 200
    body = client.get(f"/api/opportunities/{prepared.opportunity_id}").json()
    assert body["opportunity"]["status"] == JobStatus.REJECTED.value
    assert body["actions"]["passed"] is True


def test_opportunities_page_cards_link_to_the_detail_page() -> None:
    from sabra_api.main import opportunities

    html = opportunities()
    assert "/opportunities/${encodeURIComponent(o.id)}" in html

