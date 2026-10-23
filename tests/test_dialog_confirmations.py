"""Controls that need a yes use the page's own <dialog>, never a native confirm() or prompt().

The Claude desktop app's embedded browser suppresses native dialogs, so confirm() returned
false there and the Apply button silently did nothing. These tests run the real page
scripts under node (see `p2b_support.render`), whose context has no `confirm`, `prompt`
or `showModal`-less dialog: a page that reached for a native one would throw.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from p2b_support import render
from sabra_api.main import app, applications_page, opportunities
from sabra_api.review_pages import opportunity_detail_body, pipeline_body, review_body
from test_p2b_pages import detail_payload, review_payload

NATIVE_DIALOG = re.compile(r"(?<![\w.$])(?:confirm|prompt|alert)\s*\(")
PAGES = ("/", "/opportunities", "/applications", "/pipeline", "/candidate", "/searches", "/answers", "/settings")

APPLY_TEXT = (
    "Submit this approved application? A CAPTCHA opens in a headed browser for you to solve. "
    "This does not solve a CAPTCHA."
)
MANUAL_TEXT = (
    "Open this posting in your browser? Sabra cannot fill this employer form, so it types "
    "nothing. It saves your resume and cover letter as PDFs and shows where, then waits for "
    "you to submit the form and report the result."
)
FORCE_TEXT = (
    "Forcing a report records an outcome without Sabra seeing it. "
    "Type the application id to confirm: app_1"
)


def posts(out: dict) -> list[tuple[str, str]]:
    return [(c["method"], c["url"]) for c in out["calls"] if c["method"] == "POST"]


def apply_button(manual: bool = False) -> str:
    return (
        "{dataset:{id:'app_1',manual:'%s',dry:'0'},disabled:false}" % ("1" if manual else "0")
    )


APPLIED = {"packages": [], "automation_dry_run": False, "outcome": "SUBMITTED", "detail": "ok"}


# ---------------------------------------------------------------------------
# No native dialogs anywhere; every page carries the dialog
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES)
def test_no_served_page_calls_a_native_dialog_and_each_carries_the_dialog(path: str) -> None:
    html = TestClient(app).get(path).text
    assert not NATIVE_DIALOG.search(html), NATIVE_DIALOG.search(html)
    assert html.count('<dialog id="ask-dialog"') == 1
    assert 'id="ask-ok"' in html and 'id="ask-cancel"' in html and 'id="ask-input"' in html
    assert html.count("<script>") == 1


def test_no_page_body_calls_a_native_dialog() -> None:
    for body in (
        applications_page(),
        opportunities(),
        opportunity_detail_body("opp_1"),
        review_body("pkg_1"),
        pipeline_body(),
    ):
        assert not NATIVE_DIALOG.search(body), NATIVE_DIALOG.search(body)


# ---------------------------------------------------------------------------
# The dialog helper itself
# ---------------------------------------------------------------------------


def test_the_confirm_button_resolves_true_and_cancel_false() -> None:
    probe = "document.querySelector('#probe').textContent"
    out = render(
        applications_page(),
        APPLIED,
        f"const p=ask('Sure?'); document.querySelector('#ask-ok').onclick(); {probe}=String(await p);",
        dialog="manual",
    )
    assert out["nodes"]["#probe"]["textContent"] == "true"
    assert out["shown"] == [{"text": "Sure?", "input": False, "label": "Confirm"}]
    out = render(
        applications_page(),
        APPLIED,
        f"const p=ask('Sure?'); document.querySelector('#ask-cancel').onclick(); {probe}=String(await p);",
        dialog="manual",
    )
    assert out["nodes"]["#probe"]["textContent"] == "false"


def test_closing_the_dialog_with_escape_counts_as_cancel() -> None:
    probe = "document.querySelector('#probe').textContent"
    out = render(
        applications_page(),
        APPLIED,
        "const p=ask('Sure?'); const d=document.querySelector('#ask-dialog'); d.open=false; d.onclose();"
        f"{probe}=String(await p);",
        dialog="manual",
    )
    assert out["nodes"]["#probe"]["textContent"] == "false"


def test_the_text_box_variant_returns_what_was_typed_and_null_on_cancel() -> None:
    probe = "document.querySelector('#probe').textContent"
    out = render(
        applications_page(),
        APPLIED,
        "const p=ask('Type it',{input:true,confirmLabel:'Go'});"
        "document.querySelector('#ask-input').value='abc';"
        f"document.querySelector('#ask-ok').onclick(); {probe}=String(await p);",
        dialog="manual",
    )
    assert out["nodes"]["#probe"]["textContent"] == "abc"
    assert out["shown"] == [{"text": "Type it", "input": True, "label": "Go"}]
    out = render(
        applications_page(),
        APPLIED,
        "const p=ask('Type it',{input:true});document.querySelector('#ask-input').value='abc';"
        f"document.querySelector('#ask-cancel').onclick(); {probe}=String(await p);",
        dialog="manual",
    )
    assert out["nodes"]["#probe"]["textContent"] == "null"


def test_enter_in_the_text_box_confirms() -> None:
    probe = "document.querySelector('#probe').textContent"
    out = render(
        applications_page(),
        APPLIED,
        "const p=ask('Type it',{input:true});document.querySelector('#ask-input').value='abc';"
        f"document.querySelector('#ask-input').onkeydown({{key:'Enter'}}); {probe}=String(await p);",
        dialog="manual",
    )
    assert out["nodes"]["#probe"]["textContent"] == "abc"


def test_a_second_question_is_not_answered_by_the_first_ones_handlers() -> None:
    probe = "document.querySelector('#probe').textContent"
    out = render(
        applications_page(),
        APPLIED,
        "const first=ask('One?'); document.querySelector('#ask-ok').onclick(); await first;"
        "const second=ask('Two?'); document.querySelector('#ask-cancel').onclick();"
        f"{probe}=String(await second);",
        dialog="manual",
    )
    assert out["nodes"]["#probe"]["textContent"] == "false"
    assert [s["text"] for s in out["shown"]] == ["One?", "Two?"]


# ---------------------------------------------------------------------------
# Apply (Applications page)
# ---------------------------------------------------------------------------


def test_apply_reaches_the_fetch_when_the_dialog_is_confirmed() -> None:
    out = render(applications_page(), APPLIED, f"await applyOne({apply_button()});", confirm=True)
    assert ("POST", "/api/applications/app_1/apply") in posts(out)
    call = next(c for c in out["calls"] if c["url"] == "/api/applications/app_1/apply")
    assert json.loads(call["body"]) == {"confirm": True}
    assert out["shown"] == [{"text": APPLY_TEXT, "input": False, "label": "Submit"}]
    assert out["nodes"]["#result"]["textContent"] == "SUBMITTED: ok"


def test_apply_does_not_reach_the_fetch_when_the_dialog_is_cancelled() -> None:
    out = render(applications_page(), APPLIED, f"await applyOne({apply_button()});", confirm=False)
    assert posts(out) == []
    assert out["shown"][0]["text"] == APPLY_TEXT


def test_apply_with_the_dialog_left_open_sends_nothing() -> None:
    out = render(
        applications_page(),
        APPLIED,
        f"applyOne({apply_button()});",
        dialog="manual",
    )
    assert posts(out) == []
    assert len(out["shown"]) == 1


def test_a_manual_source_keeps_its_own_wording() -> None:
    out = render(
        applications_page(), APPLIED, f"await applyOne({apply_button(True)});", confirm=True
    )
    assert out["shown"] == [{"text": MANUAL_TEXT, "input": False, "label": "Open in browser"}]
    assert ("POST", "/api/applications/app_1/apply") in posts(out)


def test_a_disabled_apply_button_asks_nothing() -> None:
    out = render(
        applications_page(),
        APPLIED,
        "await applyOne({dataset:{id:'app_1',manual:'0',dry:'1'},disabled:true});",
    )
    assert out["shown"] == [] and posts(out) == []


def test_the_apply_buttons_are_wired_to_the_dialog_flow() -> None:
    html = applications_page()
    assert "addEventListener('click',()=>applyOne(btn))" in html


# ---------------------------------------------------------------------------
# Forced report (Applications page): the dialog has a text box
# ---------------------------------------------------------------------------

FORCE_EXPRESSION = (
    "const btn={dataset:{outcome:'abandoned'},addEventListener(t,cb){this.cb=cb}};"
    "wireForce({querySelectorAll:()=>[btn]},'app_1'); await btn.cb();"
)


def test_a_forced_report_sends_what_was_typed_in_the_dialog() -> None:
    out = render(
        applications_page(),
        {"packages": [], "automation_dry_run": False, "application_status": "BLOCKED"},
        FORCE_EXPRESSION,
        typed="app_1",
    )
    assert out["shown"] == [{"text": FORCE_TEXT, "input": True, "label": "Force report"}]
    call = next(c for c in out["calls"] if c["url"].endswith("/handoff/force"))
    assert json.loads(call["body"]) == {"outcome": "abandoned", "confirm_id": "app_1"}


def test_a_cancelled_forced_report_sends_nothing() -> None:
    out = render(applications_page(), {"packages": []}, FORCE_EXPRESSION, confirm=False)
    assert out["shown"][0]["input"] is True
    assert [c for c in out["calls"] if c["url"].endswith("/handoff/force")] == []


# ---------------------------------------------------------------------------
# Pass and Reject
# ---------------------------------------------------------------------------

PASS_TEXT = "Pass on this role? It leaves your list and will not come back."


def test_pass_on_the_opportunities_page_uses_the_dialog() -> None:
    row = {"id": "opp_1", "title": "T", "company": "C", "status": "SHORTLISTED", "fit_score": None}
    payload = {"opportunities": [row]}
    declined = render(opportunities(), payload, "await pass('opp_1');", confirm=False)
    assert posts(declined) == []
    assert declined["shown"] == [{"text": PASS_TEXT, "input": False, "label": "Pass"}]
    accepted = render(opportunities(), payload, "await pass('opp_1');", confirm=True)
    assert ("POST", "/api/opportunities/opp_1/pass") in posts(accepted)


def test_pass_on_the_detail_page_uses_the_dialog() -> None:
    declined = render(
        opportunity_detail_body("opp_1"), detail_payload(), "await pass();", confirm=False
    )
    assert posts(declined) == []
    assert declined["shown"][0]["text"] == PASS_TEXT
    accepted = render(opportunity_detail_body("opp_1"), detail_payload(), "await pass();")
    assert ("POST", "/api/opportunities/opp_1/pass") in posts(accepted)


def test_reject_on_the_review_page_uses_the_dialog() -> None:
    payload = review_payload()
    payload["actions"] = {**payload["actions"], "can_reject": True, "reject_reason": None}
    declined = render(review_body("pkg_1"), payload, "await reject();", confirm=False)
    assert posts(declined) == []
    assert declined["shown"] == [
        {
            "text": "Reject this application? The role leaves your list.",
            "input": False,
            "label": "Reject",
        }
    ]
    accepted = render(review_body("pkg_1"), payload, "await reject();", confirm=True)
    assert ("POST", "/api/packages/pkg_1/reject") in posts(accepted)
