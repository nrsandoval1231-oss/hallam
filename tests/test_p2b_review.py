"""Application review (PRD section 31): the payload, its notes, and what may be done."""

from __future__ import annotations

import pytest
from p2b_support import api, approve, make_complete, mark_applied, seed_scoring
from sabra_domain.enums import ApplicationStatus, JobStatus, PackageStatus
from sabra_engine.approval import ApprovalService
from sabra_engine.package_review import generation_notes


def review(prepared, package_id: str | None = None) -> dict:
    return ApprovalService(prepared.ctx).review(package_id or prepared.package_id)


# ---------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------


def test_review_carries_everything_the_page_shows(prepared, monkeypatch) -> None:
    seed_scoring(prepared)
    body = api(prepared, monkeypatch).get(f"/api/packages/{prepared.package_id}/review").json()

    assert body["package"]["content_hash"] == prepared.package_hash
    assert body["package"]["status"] == "INCOMPLETE"
    assert body["job"]["compensation_text"] == "$110,000 to $140,000 a year"
    assert body["job"]["source_url"].startswith("https://") and body["job"]["description"]
    assert body["fit"]["fit_score"] == pytest.approx(0.82)
    assert body["fit"]["gaps"] and body["fit"]["risks"] and body["fit"]["strengths"]
    materials = body["materials"]
    assert materials["tailored_resume"] and materials["cover_letter"]
    assert {"recruiter_message", "hiring_manager_message", "interview_preparation"} <= set(
        materials
    )
    answers = {a["question_key"]: a for a in materials["answers"]}
    assert answers["work_authorization"]["level"] == "VERIFIED"
    assert answers["sponsorship"]["requires_user_input"] is True
    assert body["unresolved_questions"]
    assert body["application_id"] == prepared.application_id
    assert body["application_status"] == "AWAITING_APPROVAL"


def test_review_without_a_stored_assessment_has_no_fit_block(prepared) -> None:
    assert review(prepared)["fit"] is None


def test_review_of_an_unknown_package_is_a_404(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    assert client.get("/api/packages/pkg_missing/review").status_code == 404
    assert client.get("/packages/pkg_missing").status_code == 404


def test_a_template_package_says_no_model_was_used(prepared) -> None:
    assert review(prepared)["generation_notes"] == [
        "No model was used for this package: every artifact came from the template."
    ]


# ---------------------------------------------------------------------------
# generation notes
# ---------------------------------------------------------------------------


def _generation(**overrides) -> dict:
    record = {
        "version": "1",
        "inputs_key": "k",
        "provider": "anthropic",
        "model": "claude-test",
        "reused": False,
        "unavailable": None,
        "generated_by": {
            "tailored_resume": "model:anthropic/claude-test",
            "cover_letter": "model:anthropic/claude-test",
            "fit_explanation": "template",
            "candidate_summary": "template",
            "recruiter_message": "template",
            "hiring_manager_message": "template",
            "interview_preparation": "template",
        },
        "truth_gate": {
            "tailoring": {
                "source": "model",
                "violations": [],
                "final_violations": [],
                "regenerated": False,
                "fell_back": False,
                "reason": None,
            },
            "cover_letter": {
                "source": "model",
                "violations": ["number 43% is not in the evidence"],
                "final_violations": [],
                "regenerated": True,
                "fell_back": False,
                "reason": None,
            },
            "narrative": {
                "source": "template",
                "violations": ["title 'VP' is not in the evidence", "second"],
                "final_violations": ["title 'VP' is not in the evidence"],
                "regenerated": True,
                "fell_back": True,
                "reason": "model output failed the truth gate twice",
            },
        },
        "calls": [
            {"purpose": "tailoring", "ok": True, "input_tokens": 1200, "output_tokens": 300},
            {"purpose": "cover_letter", "ok": True, "input_tokens": 1500, "output_tokens": 420},
            {
                "purpose": "fit_narrative",
                "ok": False,
                "error": "refused",
                "input_tokens": None,
                "output_tokens": None,
            },
        ],
    }
    record.update(overrides)
    return {"generation": record}


def test_notes_say_which_artifacts_are_model_written_and_which_are_template() -> None:
    notes = generation_notes(_generation())
    assert "Tailored résumé: written by the model (anthropic/claude-test)." in notes
    assert "Cover letter: written by the model (anthropic/claude-test)." in notes
    assert "Fit explanation: built from the template." in notes
    assert "Interview preparation: built from the template." in notes


def test_notes_report_gate_violations_regeneration_and_fallback() -> None:
    notes = "\n".join(generation_notes(_generation()))
    assert "Tailored résumé: passed the truth gate on the first draft." in notes
    assert "Cover letter: the first draft broke the truth gate (1 violation(s): number 43%" in notes
    assert "regenerated once and the second draft passed" in notes
    assert "Fit explanation: the truth gate rejected the model text (2 violation(s)" in notes
    assert "the template was used instead (model output failed the truth gate twice)" in notes


def test_notes_total_the_token_counts_and_name_a_failed_call() -> None:
    notes = generation_notes(_generation())
    assert "Model calls: 3, 1 failed. Tokens: 2,700 in, 720 out." in notes
    assert "A fit_narrative call failed: refused" in notes


def test_notes_for_unavailable_and_reused_generation() -> None:
    notes = generation_notes(
        _generation(unavailable="no API key", reused=True, calls=[], truth_gate={})
    )
    assert any(n.startswith("The model (anthropic/claude-test) was not available") for n in notes)
    assert any("reused" in n for n in notes)


@pytest.mark.parametrize("ledger", [None, {}, {"generation": None}, {"generation": {}}])
def test_notes_tolerate_a_ledger_without_generation(ledger) -> None:
    assert generation_notes(ledger) == [
        "No model was used for this package: every artifact came from the template."
    ]


def test_notes_tolerate_malformed_records() -> None:
    notes = generation_notes(
        {"generation": {"provider": "p", "truth_gate": {"x": "oops"}, "calls": ["bad", {"ok": True}]}}
    )
    assert notes[-1] == "Model calls: 1. Tokens: 0 in, 0 out."


def test_review_includes_the_stored_generation_notes(prepared) -> None:
    with prepared.ctx.repos() as repos:
        row = repos.packages.get(prepared.package_id)
        payload = dict(row.payload)
        payload["truth_ledger"] = {**payload.get("truth_ledger", {}), **_generation()}
        row.payload = payload
    notes = review(prepared)["generation_notes"]
    assert "Model calls: 3, 1 failed. Tokens: 2,700 in, 720 out." in notes


# ---------------------------------------------------------------------------
# what the page may offer
# ---------------------------------------------------------------------------


def test_an_incomplete_package_cannot_be_approved_and_says_why(prepared) -> None:
    actions = review(prepared)["actions"]
    assert actions["can_approve"] is False
    assert "INCOMPLETE" in actions["approve_reason"] and "6 unresolved" in actions["approve_reason"]
    assert actions["refresh"] == "prepare" and actions["can_reject"] is True


def test_a_complete_truth_clean_package_can_be_approved(prepared) -> None:
    complete = make_complete(prepared)
    actions = review(prepared, complete.package_id)["actions"]
    assert actions == {
        "can_approve": True,
        "approve_reason": None,
        "refresh": "prepare",
        "refresh_reason": None,
        "can_reject": True,
        "reject_reason": None,
    }


def test_an_older_revision_points_at_the_current_one(prepared) -> None:
    make_complete(prepared)
    actions = review(prepared)["actions"]
    assert actions["can_approve"] is False
    assert "replaced or withdrawn" in actions["approve_reason"]
    assert actions["refresh"] is None and actions["can_reject"] is False


def test_a_package_that_is_not_truth_clean_cannot_be_approved(prepared) -> None:
    complete = make_complete(prepared)
    with prepared.ctx.repos() as repos:
        repos.packages.get(complete.package_id).truth_clean = False
    actions = review(prepared, complete.package_id)["actions"]
    assert actions["can_approve"] is False and "not truth-clean" in actions["approve_reason"]


def test_an_approved_package_says_it_is_approved_and_offers_refresh_not_reject(prepared) -> None:
    complete = make_complete(prepared)
    ApprovalService(prepared.ctx).approve(complete.package_id, approved_by="owner")
    body = review(prepared, complete.package_id)
    assert body["actions"]["can_approve"] is False
    assert "Already approved" in body["actions"]["approve_reason"]
    assert body["actions"]["refresh"] == "refresh"
    assert body["actions"]["can_reject"] is False
    assert "Refresh the package" in body["actions"]["reject_reason"]
    assert body["application_status"] == ApplicationStatus.APPROVED.value


def test_a_submitted_application_offers_none_of_the_three(prepared) -> None:
    mark_applied(prepared)
    actions = review(prepared)["actions"]
    assert actions["can_approve"] is False and "already APPLIED" in actions["approve_reason"]
    assert actions["refresh"] is None and "can no longer change" in actions["refresh_reason"]
    assert actions["can_reject"] is False and "Pipeline" in actions["reject_reason"]


# ---------------------------------------------------------------------------
# the one primary action, bound to the hash on the page
# ---------------------------------------------------------------------------


def _approve_request(package_id: str, content_hash: str) -> dict:
    return {
        "approved_by": "owner",
        "packages": [{"package_id": package_id, "content_hash": content_hash}],
    }


def test_approving_from_the_review_binds_to_the_hash_it_showed(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    complete = make_complete(prepared)
    shown = client.get(f"/api/packages/{complete.package_id}/review").json()
    assert shown["actions"]["can_approve"] is True

    response = client.post(
        "/api/packages/approve",
        json=_approve_request(shown["package"]["id"], shown["package"]["content_hash"]),
    )
    result = response.json()
    assert response.status_code == 200 and result["refused"] == []
    assert result["approved"][0]["approved_package_hash"] == shown["package"]["content_hash"]

    after = client.get(f"/api/packages/{complete.package_id}/review").json()
    assert after["application_status"] == "APPROVED"
    assert after["actions"]["can_approve"] is False


def test_a_stale_hash_is_refused_and_nothing_is_approved(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    complete = make_complete(prepared)
    response = client.post(
        "/api/packages/approve", json=_approve_request(complete.package_id, "0" * 64)
    )
    assert response.json()["approved"] == []
    assert "does not bind" in response.json()["refused"][0]["message"]
    with prepared.ctx.read_repos() as repos:
        assert (
            repos.applications.get(prepared.application_id).status
            == ApplicationStatus.AWAITING_APPROVAL.value
        )


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


def test_reject_withdraws_the_application_and_passes_on_the_role(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    response = client.post(
        f"/api/packages/{prepared.package_id}/reject", json={"reason": "not for me"}
    )
    assert response.status_code == 200 and response.json()["decision"] == "REJECTED"
    with prepared.ctx.read_repos() as repos:
        assert repos.packages.get(prepared.package_id).status == PackageStatus.SUPERSEDED.value
        assert (
            repos.applications.get(prepared.application_id).status
            == ApplicationStatus.WITHDRAWN.value
        )
        assert repos.opportunities.get(prepared.opportunity_id).status == JobStatus.REJECTED.value
        assert {j.status for j in repos.opportunities.list_jobs(prepared.opportunity_id)} == {
            JobStatus.REJECTED.value
        }


def test_reject_without_a_body_works_too(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    assert client.post(f"/api/packages/{prepared.package_id}/reject").status_code == 200


def test_reject_refuses_an_older_revision_and_changes_nothing(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    make_complete(prepared)
    response = client.post(f"/api/packages/{prepared.package_id}/reject", json={})
    assert response.status_code == 422
    assert "only the current package can be rejected" in response.json()["detail"]
    with prepared.ctx.read_repos() as repos:
        assert (
            repos.applications.get(prepared.application_id).status
            == ApplicationStatus.AWAITING_APPROVAL.value
        )


def test_reject_is_refused_for_an_approved_application(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    approve(prepared)
    response = client.post(f"/api/packages/{prepared.package_id}/reject", json={})
    assert response.status_code == 409
    with prepared.ctx.read_repos() as repos:
        assert (
            repos.applications.get(prepared.application_id).status
            == ApplicationStatus.APPROVED.value
        )
        assert repos.packages.get(prepared.package_id).status == PackageStatus.APPROVED.value


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------


def test_the_review_page_is_one_script_under_the_shared_shell(prepared, monkeypatch) -> None:
    html = api(prepared, monkeypatch).get(f"/packages/{prepared.package_id}").text
    assert html.count("<script>") == 1 and 'class="topnav"' in html
    assert f'"{prepared.package_id}"' in html
    assert 'aria-current="page"' in html


def test_the_package_json_route_the_applications_list_uses_is_unchanged(
    prepared, monkeypatch
) -> None:
    body = api(prepared, monkeypatch).get(f"/api/packages/{prepared.package_id}").json()
    assert body["content_hash"] == prepared.package_hash and "approvable" in body
    assert body["attention"] == "needs_information"
