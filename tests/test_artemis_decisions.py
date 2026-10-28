"""Decision-surface navigation and truth cues stay tied to real page state."""

from sabra_api.review_pages import DETAIL_CSS, opportunity_detail_body, pipeline_body, review_body


def test_opportunity_detail_has_semantic_section_navigation_and_factual_readiness() -> None:
    body = opportunity_detail_body("opp_1")
    assert 'role="tablist" aria-label="Opportunity details"' in body
    for target in ("overview", "fit", "company", "role", "insights", "next"):
        assert f'id="op-tab-{target}"' in body
        assert f'aria-controls="op-panel-{target}"' in body
    assert "Source: original employer posting" in body
    assert "Source-backed" in body and "Unverified" in body
    assert "p.unresolved_count" in body and "p.truth_clean" in body
    assert "table-scroll" in body and "tabindex=\"0\"" in body
    assert "@media(max-width:480px)" in DETAIL_CSS


def test_application_review_exposes_review_sections_and_hash_bound_action() -> None:
    body = review_body("pkg_1")
    for target in ("opening", "fit-review", "resume", "cover", "answers", "questions", "warnings", "decision"):
        assert f"'{target}'" in body or f'"{target}"' in body
    assert "d.package.content_hash" in body
    assert "packages:[{package_id:d.package.id,content_hash:d.package.content_hash}]" in body
    assert "a.can_approve" in body and "unresolved_questions" in body
    assert "aria-label=\"Proposed application answers\"" in body


def test_pipeline_stage_navigation_and_action_labels_describe_their_effect() -> None:
    body = pipeline_body()
    assert 'aria-label="Application stages"' in body
    assert 'href="#${stageId(s.label,i)}"' in body
    assert "Review application" in body
    assert "View history" in body
    assert "Record outcome" in body
    assert "Nothing has been approved yet." in body


def test_document_formatting_keeps_malicious_markdown_inert():
    from p2b_support import assert_inert, render
    from test_p2b_pages import review_payload

    malicious = '<img src=x onerror="alert(1)">'
    payload = review_payload()
    payload["materials"]["tailored_resume"] = (
        "# " + malicious + "\n_" + malicious + "_\n## Experience\n- **" + malicious + "**\n"
        "[unsafe](javascript:alert(1))\n![image](https://employer.test/pixel)\n</article><script>alert(1)</script>"
    )
    output = render(review_body("pkg_1"), payload)["nodes"]["#sections"]["innerHTML"]
    parsed = assert_inert(output, malicious)
    assert "<article class=\"document-preview\">" in output
    assert "<h4>Experience</h4>" in output
    assert "<em>&lt;img" in output
    assert not any(tag in {"script", "img"} for tag, _attrs in parsed.tags)
    assert "[unsafe](javascript:alert(1))" in parsed.joined
