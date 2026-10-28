"""The opportunity and application decision surfaces follow the approved ARTEMIS reference."""

from p2b_support import Markup, render
from sabra_api.review_pages import DETAIL_CSS, opportunity_detail_body, review_body


def opportunity_payload() -> dict:
    return {
        "opportunity": {"id": "opp_1", "title": "Product Director", "company": "Northstar Labs",
                        "location": "Boston, MA", "status": "SHORTLISTED", "shortlist_reason": "Role aligns with the candidate's product leadership."},
        "job": {"title": "Product Director", "company": "Northstar Labs", "location": "Boston, MA",
                "compensation_text": "$180,000–$220,000", "description": "Original posting text",
                "requirements": "Leadership experience", "preferred_qualifications": "Platform experience",
                "responsibilities": "Lead product", "source": "Employer", "source_url": "https://example.test/job",
                "date_posted": "2026-10-01", "postings": []},
        "fit": {"fit_score": .82, "confidence": .74, "engine": "fit-v1", "components": [],
                "strengths": ["Product leadership"], "gaps": ["Platform experience not recorded"],
                "risks": [], "why_this_job": "Role aligns with the candidate's product leadership."},
        "rank": {"position": 1, "of": 4, "score": .71},
        "hard_filter": {"decision": "passed", "summary": "No hard filters blocked this role.", "reasons": [], "detail": {},
                        "checks_run": [], "checks_skipped": [], "owner_passed": False},
        "research": {"verified_facts": ["Founded in 2012"], "inferences": ["Likely expanding platform team"],
                      "sources": ["https://example.test/about"], "recent_developments": ["Opened a Boston office"],
                      "candidate_company_intersections": [], "updated_at": "2026-10-02", "engine": "research-v1"},
        "application": None, "package": None, "history": [],
        "actions": {"can_prepare": True, "can_pass": True, "passed": False},
    }


def review_payload() -> dict:
    return {
        "package": {"id": "pkg_1", "revision": 1, "status": "NEEDS_INFORMATION", "content_hash": "sha256-example",
                    "truth_clean": False, "unresolved_count": 1},
        "application_id": "app_1", "application_status": "PREPARED",
        "opportunity": {"id": "opp_1", "title": "Product Director", "company": "Northstar Labs"},
        "job": {"title": "Product Director", "company": "Northstar Labs", "location": "Boston, MA",
                "compensation_text": "$180,000–$220,000", "description": "Original employer posting"},
        "fit": {"fit_score": .82, "confidence": .74, "strengths": ["Product leadership"],
                "gaps": [], "risks": [], "why_this_job": "Leadership match."},
        "analysis": {},
        "materials": {"tailored_resume": "# Product leader\n- Built product teams", "cover_letter": "Dear team,\nI am interested.",
                      "answers": [{"question_key": "sponsorship", "question_text": "Will you need sponsorship?",
                                   "answer": None, "level": "UNKNOWN", "requires_user_input": True, "sensitive": False}]},
        "unresolved_questions": ["Confirm sponsorship answer"], "warnings": ["The cover letter includes an unverified claim."],
        "generation_notes": [],
        "actions": {"can_approve": False, "approve_reason": "Resolve the outstanding question first.",
                    "refresh": None, "refresh_reason": "", "can_reject": False, "reject_reason": ""},
    }


def test_opportunity_reference_tabs_are_real_and_keep_all_evidence_reachable() -> None:
    body = opportunity_detail_body("opp_1")
    out = render(body, opportunity_payload(), "await load(); document.querySelector('#op-tab-fit').onclick();")
    nodes = out["nodes"]
    tabs = Markup(body)
    assert [text for text in ("Overview", "Why it fits", "Company", "Role", "Insights", "Next steps") if text in tabs.joined] == [
        "Overview", "Why it fits", "Company", "Role", "Insights", "Next steps"
    ]
    assert nodes["#op-panel-fit"]["hidden"] is False
    assert nodes["#op-panel-overview"]["hidden"] is True
    rendered = nodes["#sections"]["innerHTML"]
    for evidence in ("Original posting text", "Founded in 2012", "Likely expanding platform team",
                     "Opened a Boston office", "No events recorded"):
        assert evidence in Markup(rendered).joined
    assert 'aria-selected="true"' in body and "ArrowRight" in body
    assert "list.onkeydown=event=>" in body and "tab.onclick=" in body
    assert 'Confidence' in nodes["#confidence"]["innerHTML"]
    assert nodes["#company-tile"]["textContent"] == "NL"
    assert "company-tile" in DETAIL_CSS and "background:var(--evergreen)" in DETAIL_CSS


def test_application_studio_steps_toggle_and_keep_incomplete_readiness_visible() -> None:
    body = review_body("pkg_1")
    out = render(body, review_payload(), "document.querySelector('#studio-tab-review').onclick();")
    nodes = out["nodes"]
    assert nodes["#studio-panel-review"]["hidden"] is False
    assert nodes["#studio-panel-review-followup"]["hidden"] is False
    assert nodes["#studio-panel-resume"]["hidden"] is True
    assert nodes["#studio-status"]["textContent"] == "Needs information"
    assert "1 unresolved question" in nodes["#studio-readiness-text"]["textContent"]
    sections = Markup(nodes["#sections"]["innerHTML"]).joined
    for visible in ("Built product teams", "Dear team", "Will you need sponsorship?", "Confirm sponsorship answer",
                    "unverified claim", "sha256-example", "Approve application"):
        assert visible in sections
    assert "Resolve the outstanding question first." in sections
    assert "Application studio" in body and "Review and approve before submission." in body
    assert body.count('<button type="button" data-section=') == 5
    assert 'role="tablist"' in body and 'aria-controls="studio-panel-review"' in body
    assert "ArrowRight" in body and "aria-current" in body


def test_application_studio_supports_arrow_navigation_between_numbered_steps() -> None:
    out = render(
        review_body("pkg_1"),
        review_payload(),
        "await load(); document.activeElement=document.querySelector('#studio-tab-resume');"
        "document.querySelector('#studio-steps').onkeydown({key:'ArrowRight',preventDefault(){}});",
    )
    assert out["nodes"]["#studio-panel-cover"]["hidden"] is False
    assert out["nodes"]["#studio-panel-resume"]["hidden"] is True
    assert "#studio-steps').onkeydown=event=>" in review_body("pkg_1")
