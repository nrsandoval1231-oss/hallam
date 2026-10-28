"""The decision pages render every dynamic string as text, and their controls call the right routes.

Each page's script runs under node against a canned payload. Nothing here starts a server.
"""

from __future__ import annotations

import json
import re

from p2b_support import EVIL, EVIL_ATTR, Markup, assert_inert, render
from sabra_api.main import applications_page, dashboard, opportunities
from sabra_api.review_pages import opportunity_detail_body, pipeline_body, review_body

JAVASCRIPT_URL = "javascript:alert(1)"


def headings(markup: str) -> list[str]:
    return re.findall(r"<h2>(.*?)</h2>", markup)


def probe(expression: str) -> str:
    """Wrap an expression so its value lands in a node the harness reports back."""

    return f"document.querySelector('#probe').textContent = String({expression});"


# ---------------------------------------------------------------------------
# opportunity detail
# ---------------------------------------------------------------------------


def detail_payload(**overrides) -> dict:
    payload = {
        "opportunity": {
            "id": "opp_1",
            "title": EVIL,
            "company": EVIL,
            "location": EVIL,
            "status": EVIL,
            "shortlisted": True,
            "shortlist_reason": EVIL,
            "source_count": 2,
        },
        "job": {
            "title": EVIL,
            "company": EVIL,
            "location": EVIL,
            "remote_status": EVIL,
            "employment_type": "FULL_TIME",
            "compensation_text": EVIL,
            "date_posted": "2026-08-01",
            "date_discovered": "2026-08-02T00:00:00+00:00",
            "last_verified": None,
            "source": EVIL,
            "source_url": JAVASCRIPT_URL,
            "description": EVIL,
            "requirements": EVIL,
            "preferred_qualifications": None,
            "responsibilities": EVIL,
            "travel": None,
            "department": EVIL,
            "seniority": None,
            "postings": [{"source": "A", "source_job_id": "1", "url": "https://a.test/1"}] * 2,
        },
        "rank": {"score": 0.71234, "position": 2, "of": 9},
        "fit": {
            "fit_score": 0.82,
            "confidence": 0.74,
            "engine": EVIL,
            "components": [
                {"key": "career_score", "label": EVIL, "score": 0.9, "rationale": EVIL},
                {"key": "location_score", "label": "Location", "score": 1.0, "rationale": ""},
            ],
            "strengths": [EVIL],
            "gaps": [EVIL],
            "risks": [],
            "why_this_job": EVIL,
        },
        "hard_filter": {
            "decision": "rejected",
            "reasons": [EVIL],
            "detail": {EVIL: EVIL},
            "checks_run": [EVIL],
            "checks_skipped": [EVIL],
            "evaluated_at": "2026-08-03T00:00:00+00:00",
            "summary": EVIL,
            "owner_passed": False,
        },
        "research": {
            "engine": "deterministic",
            "updated_at": "2026-08-04T00:00:00+00:00",
            "company_description": EVIL,
            "company_website": "https://example.test",
            "industry": EVIL,
            "products_services": [],
            "role_context": EVIL,
            "likely_reporting_organization": None,
            "hiring_context": None,
            "recent_developments": [EVIL],
            "candidate_company_intersections": [],
            "verified_facts": ["VERIFIED FACT " + EVIL],
            "inferences": ["INFERRED " + EVIL],
            "sources": [EVIL],
        },
        "application": {
            "id": "app_1",
            "status": EVIL,
            "submission_time": None,
            "submission_method": None,
        },
        "package": {
            "id": 'pkg_1"><b>',
            "revision": 2,
            "status": EVIL,
            "truth_clean": True,
            "unresolved_count": 0,
            "content_hash": EVIL,
            "generated_at": "2026-08-05T00:00:00+00:00",
            "review_url": "/packages/pkg_1",
        },
        "actions": {"can_prepare": True, "can_pass": True, "passed": False},
        "history": [
            {
                "seq": 1,
                "entity_type": EVIL,
                "event_type": EVIL,
                "occurred_at": "2026-08-01T00:00:00+00:00",
                "actor": EVIL,
                "summary": EVIL,
            },
            {
                "seq": 2,
                "entity_type": "package",
                "event_type": "PACKAGE_GENERATED",
                "occurred_at": "2026-08-05T00:00:00+00:00",
                "actor": "system",
                "summary": "",
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_the_detail_page_renders_every_section_as_text() -> None:
    out = render(opportunity_detail_body("opp_1"), detail_payload())
    nodes = out["nodes"]
    sections = nodes["#sections"]["innerHTML"]
    parsed = assert_inert(sections, EVIL)
    assert parsed.joined.count(EVIL) >= 20
    assert headings(sections) == [
        "Why Artemis recommends it",
        "Candidate fit",
        "Hard filter",
        "The posting",
        "Company research",
        "Application",
        "History",
    ]
    assert_inert(nodes["#meta"]["innerHTML"], EVIL)
    assert_inert(nodes["#actions"]["innerHTML"], "Prepare application")
    assert nodes["#title"]["textContent"] == f"{EVIL} at {EVIL}"


def test_the_detail_page_links_only_http_urls_and_never_a_javascript_url() -> None:
    sections = render(opportunity_detail_body("opp_1"), detail_payload())["nodes"]["#sections"][
        "innerHTML"
    ]
    hrefs = Markup(sections).hrefs()
    assert JAVASCRIPT_URL not in hrefs and not any(h.startswith("javascript") for h in hrefs)
    assert "https://example.test" in hrefs  # the company website is an http link
    assert "/packages/pkg_1%22%3E%3Cb%3E" in hrefs  # the id is encoded, not trusted


def test_the_detail_page_keeps_verified_facts_apart_from_inferences() -> None:
    sections = render(opportunity_detail_body("opp_1"), detail_payload())["nodes"]["#sections"][
        "innerHTML"
    ]
    verified = sections.index("Verified facts")
    inferred = sections.index("Inferences")
    assert verified < sections.index("VERIFIED FACT") < inferred < sections.index("INFERRED")


def test_the_detail_page_offers_review_prepare_and_pass() -> None:
    nodes = render(opportunity_detail_body("opp_1"), detail_payload())["nodes"]
    actions = Markup(nodes["#actions"]["innerHTML"])
    buttons = {attrs["id"]: attrs for tag, attrs in actions.tags if tag == "button"}
    assert set(buttons) == {"prepare", "pass"} and "disabled" not in buttons["prepare"]
    assert "Prepare application" in actions.joined and "Pass" in actions.joined
    assert any(h.startswith("/packages/") for h in actions.hrefs())
    assert "Review application" in actions.joined


def test_the_detail_page_disables_actions_the_engine_would_refuse() -> None:
    payload = detail_payload(
        actions={"can_prepare": False, "can_pass": False, "passed": True}, package=None
    )
    actions = Markup(render(opportunity_detail_body("opp_1"), payload)["nodes"]["#actions"][
        "innerHTML"
    ])
    buttons = {attrs["id"]: attrs for tag, attrs in actions.tags if tag == "button"}
    assert "disabled" in buttons["prepare"] and "disabled" in buttons["pass"]
    assert "You passed on this role." in actions.joined
    assert not actions.hrefs()


def test_an_unscored_unresearched_opportunity_says_so() -> None:
    payload = detail_payload(
        fit=None, research=None, application=None, package=None, history=[], hard_filter={
            "decision": "not_evaluated", "reasons": [], "detail": {}, "checks_run": [],
            "checks_skipped": [], "evaluated_at": None, "summary": "not run", "owner_passed": False,
        },
    )
    sections = render(opportunity_detail_body("opp_1"), payload)["nodes"]["#sections"]["innerHTML"]
    text = Markup(sections).joined
    assert "has not been scored yet" in text
    assert "No research has been done" in text
    assert "No application has been prepared" in text
    assert "No events recorded" in text


def test_prepare_on_the_detail_page_posts_then_reloads_and_links_to_the_review() -> None:
    routes = {
        "POST /api/opportunities/opp_1/prepare": {
            "status": "INCOMPLETE",
            "package_id": "pkg_new",
            "unresolved_questions": ["a", "b"],
        }
    }
    out = render(
        opportunity_detail_body("opp_1"), detail_payload(), "await prepare();", routes=routes
    )
    methods = [(c["method"], c["url"]) for c in out["calls"]]
    assert ("POST", "/api/opportunities/opp_1/prepare") in methods
    assert methods[-1] == ("GET", "/api/opportunities/opp_1")
    line = out["nodes"]["#line"]["innerHTML"]
    assert "2 unresolved question(s)" in line and 'href="/packages/pkg_new"' in line


def test_a_refused_prepare_shows_the_engines_reason() -> None:
    out = render(
        opportunity_detail_body("opp_1"),
        detail_payload(),
        "await prepare();",
        routes={"POST /api/opportunities/opp_1/prepare": {"detail": "already approved"}},
        statuses={"POST /api/opportunities/opp_1/prepare": 409},
    )
    assert out["nodes"]["#line"]["textContent"] == "already approved"


def test_pass_asks_first() -> None:
    declined = render(opportunity_detail_body("opp_1"), detail_payload(), "await pass();", confirm=False)
    assert not [c for c in declined["calls"] if c["method"] == "POST"]
    accepted = render(opportunity_detail_body("opp_1"), detail_payload(), "await pass();")
    assert ("POST", "/api/opportunities/opp_1/pass") in [
        (c["method"], c["url"]) for c in accepted["calls"]
    ]


def test_the_detail_page_shows_the_error_for_an_unknown_id() -> None:
    out = render(
        opportunity_detail_body("opp_x"),
        {"detail": "application opp_x was not found"},
        statuses={"/api/opportunities/opp_x": 404},
        routes={"/api/opportunities/opp_x": {"detail": "opportunity opp_x was not found"}},
    )
    assert out["nodes"]["#line"]["textContent"] == "opportunity opp_x was not found"


def test_the_id_in_the_script_cannot_break_out_of_it() -> None:
    body = opportunity_detail_body('x";</script><script>alert(1)//')
    assert body.count("<script>") == 1 and body.count("</script>") == 1


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


def review_payload(**overrides) -> dict:
    payload = {
        "package": {
            "id": "pkg_1",
            "revision": 3,
            "status": "COMPLETE",
            "content_hash": "ab" * 32,
            "truth_clean": True,
        },
        "opportunity": {"id": "opp_1", "title": EVIL, "company": EVIL},
        "job": {
            "title": EVIL,
            "company": EVIL,
            "location": EVIL,
            "compensation_text": EVIL,
            "employment_type": "FULL_TIME",
            "date_posted": "2026-08-01",
            "source_url": JAVASCRIPT_URL,
            "description": EVIL,
        },
        "materials": {
            "tailored_resume": EVIL,
            "cover_letter": EVIL,
            "answers": [
                {
                    "question_key": "work_authorization",
                    "question_text": EVIL,
                    "answer": EVIL,
                    "level": EVIL_ATTR,
                    "requires_user_input": False,
                    "sensitive": True,
                },
                {
                    "question_key": "sponsorship",
                    "question_text": None,
                    "answer": None,
                    "level": "UNKNOWN",
                    "requires_user_input": True,
                    "sensitive": False,
                },
                {"question_key": "veteran", "answer": "No", "level": "VERIFIED"},
            ],
            "recruiter_message": EVIL,
            "hiring_manager_message": None,
            "interview_preparation": EVIL,
        },
        "analysis": {"fit_explanation": EVIL, "qualification_gaps": [EVIL]},
        "fit": {
            "fit_score": 0.8,
            "confidence": 0.7,
            "strengths": [EVIL],
            "gaps": [EVIL],
            "risks": [EVIL],
            "why_this_job": EVIL,
        },
        "warnings": [EVIL],
        "generation_notes": [EVIL, "Model calls: 2. Tokens: 10 in, 5 out."],
        "unresolved_questions": [EVIL],
        "application_id": "app_1",
        "application_status": "AWAITING_APPROVAL",
        "actions": {
            "can_approve": True,
            "approve_reason": None,
            "refresh": "prepare",
            "refresh_reason": None,
            "can_reject": True,
            "reject_reason": None,
        },
    }
    payload.update(overrides)
    return payload


def test_the_review_page_shows_the_sections_in_the_documented_order() -> None:
    out = render(review_body("pkg_1"), review_payload())
    sections = out["nodes"]["#sections"]["innerHTML"]
    parsed = assert_inert(sections, EVIL)
    assert headings(sections) == [
        "1. The original opening",
        "2. Fit analysis",
        "3. Tailored résumé",
        "4. Cover letter",
        "5. Proposed answers",
        "6. Unresolved questions",
        "7. Warnings",
        "Decision",
    ]
    assert "<summary>8. Recruiter and hiring-manager messages, interview preparation</summary>" in sections
    assert sections.index("Decision") > sections.index("8. Recruiter")
    # The long description is collapsed; so are the secondary messages.
    assert sections.count("<details>") == 2
    assert parsed.joined.count(EVIL) >= 15
    assert not [h for h in parsed.hrefs() if h.startswith("javascript")]


def test_the_review_page_labels_each_answer_with_its_truth_level_and_flags() -> None:
    sections = render(review_body("pkg_1"), review_payload())["nodes"]["#sections"]["innerHTML"]
    table = sections[sections.index("5. Proposed answers"):sections.index("6. Unresolved")]
    assert "verified" in table and "not verified" in table
    assert "sensitive" in table and "needs your input" in table
    assert "(blank)" in table
    assert table.count("<tr>") == 4  # header row plus three answers
    pill = next(a for t, a in Markup(table).tags if t == "span" and EVIL_ATTR in a.get("class", ""))
    assert pill["class"] == "pill " + EVIL_ATTR  # one attribute, no handler


def test_the_warnings_section_has_package_warnings_and_how_it_was_written() -> None:
    sections = render(review_body("pkg_1"), review_payload())["nodes"]["#sections"]["innerHTML"]
    warnings = sections[sections.index("7. Warnings"):sections.index('class="card decision"')]
    assert "Package warnings" in warnings and "How this package was written" in warnings
    assert "Model calls: 2. Tokens: 10 in, 5 out." in warnings


def test_the_decision_shows_the_hash_and_one_primary_approve_button() -> None:
    nodes = render(review_body("pkg_1"), review_payload())["nodes"]
    decision = nodes["#sections"]["innerHTML"].split('class="card decision"')[1]
    assert "ab" * 32 in decision
    buttons = {a["id"]: a for t, a in Markup(decision).tags if t == "button"}
    assert set(buttons) == {"approve", "refresh", "reject"}
    assert "disabled" not in buttons["approve"] and buttons["approve"].get("class") is None
    assert buttons["refresh"]["class"] == "secondary" and buttons["reject"]["class"] == "secondary"
    assert "Approve application" in decision and "Refresh package" in decision


def test_approve_is_disabled_with_the_reason_when_the_package_cannot_be_approved() -> None:
    reason = "This package is INCOMPLETE: 6 unresolved question(s) must be answered"
    payload = review_payload(
        actions={
            "can_approve": False,
            "approve_reason": reason + " " + EVIL,
            "refresh": None,
            "refresh_reason": "Only the current package can be refreshed.",
            "can_reject": False,
            "reject_reason": "No.",
        }
    )
    nodes = render(review_body("pkg_1"), payload)["nodes"]
    decision = nodes["#sections"]["innerHTML"].split('class="card decision"')[1]
    parsed = assert_inert(decision, reason)
    buttons = {a["id"]: a for t, a in parsed.tags if t == "button"}
    assert all("disabled" in b for b in buttons.values())


def test_a_disabled_approve_never_posts_even_if_called() -> None:
    payload = review_payload(
        actions={"can_approve": False, "approve_reason": "no", "refresh": None,
                 "refresh_reason": "", "can_reject": False, "reject_reason": ""}
    )
    out = render(review_body("pkg_1"), payload, "await approve(); await reject(); await refresh();")
    assert not [c for c in out["calls"] if c["method"] == "POST"]


def test_approve_posts_exactly_this_package_and_the_hash_that_was_shown() -> None:
    out = render(
        review_body("pkg_1"),
        review_payload(),
        "await approve();",
        routes={
            "POST /api/packages/approve": {
                "approved": [{"package_id": "pkg_1", "approved_package_hash": "ab" * 32}],
                "refused": [],
            }
        },
    )
    posts = [c for c in out["calls"] if c["method"] == "POST"]
    assert len(posts) == 1 and posts[0]["url"] == "/api/packages/approve"
    assert json.loads(posts[0]["body"]) == {
        "approved_by": "owner",
        "packages": [{"package_id": "pkg_1", "content_hash": "ab" * 32}],
    }
    assert out["nodes"]["#line"]["textContent"].startswith("Approved. The approval is bound to")
    assert out["calls"][-1]["url"] == "/api/packages/pkg_1/review"  # reloaded afterwards


def test_a_refused_approval_shows_why_and_does_not_claim_success() -> None:
    out = render(
        review_body("pkg_1"),
        review_payload(),
        "await approve();",
        routes={
            "POST /api/packages/approve": {
                "approved": [],
                "refused": [{"package_id": "pkg_1", "message": "does not bind to this revision"}],
            }
        },
    )
    assert out["nodes"]["#line"]["textContent"] == "Not approved: does not bind to this revision"


def test_refresh_uses_the_route_that_matches_the_application_state() -> None:
    approved = review_payload(
        application_status="APPROVED",
        actions={"can_approve": False, "approve_reason": "Already approved.", "refresh": "refresh",
                 "refresh_reason": None, "can_reject": False, "reject_reason": "x"},
    )
    out = render(
        review_body("pkg_1"),
        approved,
        "await refresh();",
        routes={"POST /api/applications/app_1/refresh": {"package_id": "pkg_2", "revision": 4}},
    )
    assert ("POST", "/api/applications/app_1/refresh") in [
        (c["method"], c["url"]) for c in out["calls"]
    ]
    assert out["href"] == "/packages/pkg_2"

    awaiting = render(
        review_body("pkg_1"),
        review_payload(),
        "await refresh();",
        routes={"POST /api/opportunities/opp_1/prepare": {"package_id": "pkg_3", "revision": 4}},
    )
    assert ("POST", "/api/opportunities/opp_1/prepare") in [
        (c["method"], c["url"]) for c in awaiting["calls"]
    ]
    assert awaiting["href"] == "/packages/pkg_3"


def test_reject_asks_first_and_posts_to_the_reject_route() -> None:
    declined = render(review_body("pkg_1"), review_payload(), "await reject();", confirm=False)
    assert not [c for c in declined["calls"] if c["method"] == "POST"]
    accepted = render(
        review_body("pkg_1"),
        review_payload(),
        "await reject();",
        routes={"POST /api/packages/pkg_1/reject": {"decision": "REJECTED"}},
    )
    assert ("POST", "/api/packages/pkg_1/reject") in [
        (c["method"], c["url"]) for c in accepted["calls"]
    ]
    assert accepted["nodes"]["#line"]["textContent"] == "Rejected. Nothing was submitted."


def test_the_review_page_without_a_fit_assessment_falls_back_to_the_package_gaps() -> None:
    sections = render(review_body("pkg_1"), review_payload(fit=None))["nodes"]["#sections"]["innerHTML"]
    assert "No stored fit assessment." in sections and EVIL in Markup(sections).joined


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------


def row(**overrides) -> dict:
    base = {
        "application_id": "app_1",
        "opportunity_id": "opp_1",
        "package_id": "pkg_1",
        "company": EVIL,
        "title": EVIL,
        "application_status": "APPLIED",
        "opportunity_status": "APPLIED",
        "submitted_at": "2026-10-01T12:00:00+00:00",
        "submission_method": "HEADED_HUMAN",
        "last_event": {
            "event_type": EVIL,
            "occurred_at": "2026-10-02T12:00:00+00:00",
            "summary": EVIL,
        },
        "outcome_count": 0,
        "latest_outcome": None,
        "allowed_outcomes": ["EMPLOYER_RESPONSE", "INTERVIEW", EVIL_ATTR],
    }
    base.update(overrides)
    return base


def pipeline_payload(rows_by_stage: dict[str, list[dict]]) -> dict:
    stages = [
        ("approved", "Approved"), ("applying", "Applying / handoff"), ("applied", "Applied"),
        ("response", "Response"), ("screen", "Screen"), ("interview", "Interview"),
        ("offer", "Offer"), ("rejected", "Rejected by employer"), ("withdrawn", "Withdrawn"),
        ("expired", "Expired"),
    ]
    return {
        "board": [
            {
                "key": key,
                "label": label,
                "count": len(rows_by_stage.get(key, [])),
                "applications": rows_by_stage.get(key, []),
            }
            for key, label in stages
        ],
        "outcome_kinds": [
            {"kind": "EMPLOYER_RESPONSE", "label": "Employer response"},
            {"kind": "INTERVIEW", "label": "Interview"},
            {"kind": EVIL_ATTR, "label": EVIL},
        ],
    }


def test_the_pipeline_page_groups_applications_by_stage_as_text() -> None:
    payload = pipeline_payload({"applied": [row()], "interview": [row(application_id="app_2")]})
    nodes = render(pipeline_body(), payload)["nodes"]
    stages = nodes["#stages"]["innerHTML"]
    parsed = assert_inert(stages, EVIL)
    assert headings(stages) == ["Applied (1)", "Interview (1)"]
    assert parsed.joined.count(EVIL) >= 6
    chips = Markup(nodes["#chips"]["innerHTML"]).joined
    assert "Applied 1" in chips and "Interview 1" in chips and "Expired 0" in chips


def test_each_application_has_an_outcome_control_and_review_and_history_links() -> None:
    stages = render(pipeline_body(), pipeline_payload({"applied": [row()]}))["nodes"]["#stages"][
        "innerHTML"
    ]
    parsed = Markup(stages)
    select = next(a for t, a in parsed.tags if t == "select")
    assert select["class"] == "oc-kind"
    options = [a["value"] for t, a in parsed.tags if t == "option"]
    assert options == ["EMPLOYER_RESPONSE", "INTERVIEW", EVIL_ATTR]
    assert "Record outcome" in parsed.joined
    assert "/packages/pkg_1" in parsed.hrefs()
    assert any(t == "button" and "hist" in a.get("class", "") for t, a in parsed.tags)
    assert "Employer response" in parsed.joined  # the label, not the code


def test_a_state_with_no_possible_outcome_offers_no_control() -> None:
    stages = render(
        pipeline_body(), pipeline_payload({"rejected": [row(allowed_outcomes=[])]})
    )["nodes"]["#stages"]["innerHTML"]
    assert "No outcome can be recorded in this state." in stages
    assert "<select" not in stages


def test_an_empty_pipeline_says_nothing_has_been_approved() -> None:
    stages = render(pipeline_body(), pipeline_payload({}))["nodes"]["#stages"]["innerHTML"]
    assert "Nothing has been approved yet." in stages


def test_recording_an_outcome_posts_the_trimmed_fields_then_reloads() -> None:
    out = render(
        pipeline_body(),
        pipeline_payload({"applied": [row()]}),
        "await recordOutcome('app_1', 'INTERVIEW', '  Panel Friday ', ' Pat Lee ', '2026-10-05');",
        routes={
            "POST /api/applications/app_1/outcomes": {
                "kind": "INTERVIEW",
                "applied_state": "INTERVIEW",
                "note": None,
            }
        },
    )
    posts = [c for c in out["calls"] if c["method"] == "POST"]
    assert len(posts) == 1
    assert posts[0]["url"] == "/api/applications/app_1/outcomes"
    assert json.loads(posts[0]["body"]) == {
        "kind": "INTERVIEW",
        "note": "Panel Friday",
        "contact": "Pat Lee",
        "occurred_at": "2026-10-05",
    }
    assert out["nodes"]["#line"]["textContent"].startswith("Recorded: Interview.")
    assert out["calls"][-1]["url"] == "/api/pipeline"


def test_blank_optional_fields_are_left_out_of_the_request() -> None:
    out = render(
        pipeline_body(),
        pipeline_payload({"applied": [row()]}),
        "await recordOutcome('app_1', 'EMPLOYER_RESPONSE', '  ', '', '');",
        routes={"POST /api/applications/app_1/outcomes": {"kind": "EMPLOYER_RESPONSE"}},
    )
    post = next(c for c in out["calls"] if c["method"] == "POST")
    assert json.loads(post["body"]) == {"kind": "EMPLOYER_RESPONSE"}


def test_a_refused_outcome_shows_the_reason_and_does_not_reload() -> None:
    out = render(
        pipeline_body(),
        pipeline_payload({"applied": [row()]}),
        "await recordOutcome('app_1', 'INTERVIEW', '', '', '2099-01-01');",
        routes={"POST /api/applications/app_1/outcomes": {"detail": "that is in the future"}},
        statuses={"POST /api/applications/app_1/outcomes": 422},
    )
    assert out["nodes"]["#line"]["textContent"] == "that is in the future"
    assert out["calls"][-1]["method"] == "POST"


def test_the_history_panel_renders_events_and_outcomes_as_text() -> None:
    ledger = {
        "events": [
            {"event_type": EVIL, "occurred_at": "2026-10-01T00:00:00+00:00", "actor": EVIL},
        ],
        "outcomes": [
            {
                "kind": "INTERVIEW",
                "occurred_at": "2026-10-03T00:00:00+00:00",
                "notes": EVIL,
                "payload": {"contact": EVIL},
            }
        ],
    }
    expression = (
        "const box={hidden:true,innerHTML:'',textContent:''};"
        "await toggleHistory({dataset:{id:'app_1'},closest(){return {querySelector(){return box}}}});"
        + probe("box.innerHTML")
    )
    out = render(
        pipeline_body(),
        pipeline_payload({"applied": [row()]}),
        expression,
        routes={"/api/applications/app_1/ledger": ledger},
    )
    panel = out["nodes"]["#probe"]["textContent"]
    parsed = assert_inert(panel, EVIL)
    assert parsed.joined.count(EVIL) >= 4
    assert any(c["url"] == "/api/applications/app_1/ledger" for c in out["calls"])


# ---------------------------------------------------------------------------
# applications filter and Today links
# ---------------------------------------------------------------------------


def packages_payload() -> dict:
    def package(package_id: str, attention: str | None, approvable: bool) -> dict:
        return {
            "package_id": package_id,
            "title": f"Role {package_id}",
            "company": "Acme",
            "status": "COMPLETE" if approvable else "INCOMPLETE",
            "content_hash": "h" * 64,
            "approvable": approvable,
            "attention": attention,
            "application_id": "app_" + package_id,
            "application_status": "AWAITING_APPROVAL",
        }

    return {
        "automation_dry_run": True,
        "packages": [
            package("p1", "ready", True),
            package("p2", "needs_information", False),
            package("p3", "needs_information", False),
            package("p4", "handoffs", False),
            package("p5", None, False),
        ],
    }


def listed(out: dict) -> list[str]:
    return re.findall(r"Role (p\d)", out["nodes"]["#list"]["innerHTML"])


def test_the_applications_page_shows_everything_without_a_filter() -> None:
    out = render(applications_page(), packages_payload())
    assert listed(out) == ["p1", "p2", "p3", "p4", "p5"]
    assert out["nodes"]["#filter-note"]["innerHTML"] == ""


def test_each_attention_filter_shows_just_its_rows_and_a_way_back() -> None:
    for name, expected in (
        ("ready", ["p1"]),
        ("needs_information", ["p2", "p3"]),
        ("handoffs", ["p4"]),
    ):
        out = render(applications_page(), packages_payload(), search=f"?filter={name}")
        assert listed(out) == expected, name
        note = out["nodes"]["#filter-note"]["innerHTML"]
        assert f"{len(expected)} of 5" in note and 'href="/applications"' in note


def test_an_unknown_or_hostile_filter_is_ignored() -> None:
    for search in ("?filter=bogus", "?filter=" + EVIL, "?filter=__proto__", ""):
        out = render(applications_page(), packages_payload(), search=search)
        assert listed(out) == ["p1", "p2", "p3", "p4", "p5"], search


def test_an_empty_filtered_view_says_so() -> None:
    payload = packages_payload()
    payload["packages"] = payload["packages"][:1]
    out = render(applications_page(), payload, search="?filter=handoffs")
    assert out["nodes"]["#list"]["textContent"] == "Nothing in this view."


def test_every_application_row_links_to_its_review_page() -> None:
    out = render(applications_page(), packages_payload())
    hrefs = Markup(out["nodes"]["#list"]["innerHTML"]).hrefs()
    assert hrefs == [f"/packages/p{n}" for n in range(1, 6)]


def test_each_count_on_home_links_to_the_page_that_acts_on_it() -> None:
    payload = {
        "today": {
            "counts": {
                "ready_for_approval": 1,
                "needs_information": 2,
                "handoffs_waiting": 3,
                "new_high_fit": 4,
                "employer_responses": 5,
            },
            "has_resume": True,
        },
        "recent": [],
        "generated_at": "2026-10-21T10:00:00+00:00",
    }
    nodes = render(dashboard(), payload)["nodes"]
    hrefs = Markup(nodes["#upcoming-list"]["innerHTML"]).hrefs()
    assert hrefs == [
        "/applications?filter=ready",
        "/applications?filter=needs_information",
        "/applications?filter=handoffs",
        "/opportunities",
        "/pipeline",
    ]


def test_the_opportunity_cards_link_to_the_detail_page() -> None:
    row_ = {"id": 'opp_"1', "title": EVIL, "company": "Acme", "location": "x", "status": "y"}
    nodes = render(opportunities(), {"opportunities": [row_]}, "render(all)")["nodes"]
    parsed = assert_inert(nodes["#queue"]["innerHTML"], EVIL)
    assert parsed.hrefs() == ["/opportunities/opp_%221"]
