"""The new pages render every dynamic string as text. Runs each page's script under node."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest
from sabra_api.main import (
    candidate_page,
    dashboard,
    opportunities,
    searches_page,
    settings_page,
)
from sabra_api.pages import page_shell

EVIL = '<img src=x onerror="alert(1)">'
EVIL_ATTR = '" onmouseover="alert(1)'


class Markup(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.text: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    @property
    def joined(self) -> str:
        return "".join(self.text)


def render(html: str, payload: dict, expression: str = "") -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const nodes = {};
const node = selector => nodes[selector] ||= {
  innerHTML: '', textContent: '', value: '', disabled: false, checked: false,
  listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback },
  querySelectorAll() { return [] }, querySelector() { return node('nested') }, reset() {}
};
const ctx = {
  document: {querySelector: node, querySelectorAll: () => []},
  fetch: async () => ({ok:true, json:async()=>input.payload,
    text:async()=>JSON.stringify(input.payload)}),
  FormData: class { constructor(values) { this.values = values } get(key) { return this.values[key] } },
  alert() {}, confirm() { return true }, console, setInterval() { return 1 }, clearInterval() {},
  Date, Math, JSON, Object, Array, String, Number, encodeURIComponent
};
vm.createContext(ctx);
vm.runInContext(input.script, ctx);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  if (input.expression) vm.runInContext(input.expression, ctx);
  await new Promise(resolve => setImmediate(resolve));
  process.stdout.write(JSON.stringify(nodes));
})().catch(error => { console.error(error); process.exit(1) });
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=json.dumps(dict(script=script, payload=payload, expression=expression)),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def assert_inert(markup: str, expected_text: str) -> Markup:
    parsed = Markup(markup)
    assert all(tag != "img" for tag, _ in parsed.tags), markup
    assert all(not key.lower().startswith("on") for _, attrs in parsed.tags for key in attrs)
    assert expected_text in parsed.joined
    return parsed


def test_the_shell_escapes_the_title_and_contains_no_script() -> None:
    html = page_shell("<b>x</b>", "<main>body</main>", active="/")
    assert "<title>Sabra | &lt;b&gt;x&lt;/b&gt;</title>" in html
    assert "<script" not in html


def test_every_page_has_exactly_one_script_so_the_harness_reads_the_right_one() -> None:
    for page in (dashboard, candidate_page, searches_page, settings_page, opportunities):
        assert page().count("<script>") == 1, page.__name__


def test_today_renders_run_progress_and_the_queue_as_text() -> None:
    payload = {
        "counts": {
            "ready_for_approval": 2,
            "needs_information": 1,
            "handoffs_waiting": 0,
            "new_high_fit": 4,
            "employer_responses": 0,
        },
        "open_questions": 5,
        "has_resume": True,
        "search_profile_name": EVIL,
        "automation_dry_run": True,
        "generated_at": "2026-10-21T10:00:00+00:00",
        "last_run": {
            "id": "prun_1",
            "kind": EVIL,
            "status": EVIL,
            "error": EVIL,
            "progress": {
                "summary": EVIL,
                "warnings": [EVIL],
                "stages": {EVIL: {"status": EVIL, "jobs_seen": 3}},
                "sources": [
                    {
                        "source_id": EVIL,
                        "display_name": EVIL,
                        "ok": False,
                        "jobs_seen": 1,
                        "jobs_new": 0,
                        "error": EVIL,
                    }
                ],
            },
        },
        "active_run": None,
        "opportunities": [
            {"title": EVIL, "company": EVIL, "location": EVIL, "rank_position": EVIL}
        ],
    }
    nodes = render(dashboard(), payload)
    attention = assert_inert(nodes["#attention"]["innerHTML"], "packages ready for approval")
    hrefs = {attrs.get("href") for tag, attrs in attention.tags if tag == "a"}
    assert hrefs == {"/applications?filter=ready", "/applications?filter=needs_information", "/opportunities"}
    assert "5 open questions" in attention.joined
    progress = assert_inert(nodes["#run-progress"]["innerHTML"], EVIL)
    assert progress.joined.count(EVIL) >= 5
    assert_inert(nodes["#overview-queue"]["innerHTML"], EVIL)
    assert nodes["#today-sub"]["textContent"].startswith("Searching: " + EVIL)


def test_today_with_nothing_to_do_says_so_and_offers_the_resume_when_missing() -> None:
    payload = {"counts": {}, "has_resume": False, "opportunities": [], "generated_at": "x"}
    nodes = render(dashboard(), payload)
    text = Markup(nodes["#attention"]["innerHTML"])
    assert 'href="/candidate"' in nodes["#attention"]["innerHTML"]
    assert "Upload your resume" in text.joined
    ok = render(dashboard(), {"counts": {}, "has_resume": True, "opportunities": []})
    assert "Nothing needs your attention right now." in ok["#attention"]["innerHTML"]


def test_the_candidate_page_renders_profile_rows_and_resume_versions_as_text() -> None:
    payload = {
        "sections": [
            {
                "title": EVIL,
                "rows": [{"label": EVIL, "detail": EVIL, "level": EVIL_ATTR}],
            }
        ],
        "resumes": [
            {
                "version": EVIL,
                "label": EVIL,
                "source_format": EVIL,
                "content_hash": EVIL,
                "is_master": True,
            }
        ],
        "identity": {"first_name": EVIL},
    }
    nodes = render(candidate_page(), payload)
    profile = assert_inert(nodes["#profile"]["innerHTML"], EVIL)
    pill = next(attrs for tag, attrs in profile.tags if tag == "span")
    assert pill["class"] == "pill " + EVIL_ATTR
    assert_inert(nodes["#resumes"]["innerHTML"], EVIL[:10])


def test_the_searches_page_lists_profiles_as_text_with_inert_edit_buttons() -> None:
    row = {
        "id": EVIL_ATTR,
        "name": EVIL,
        "active": True,
        "target_titles": [EVIL, "Clerk"],
    }
    payload = {"searches": [row], "profile": {"name": EVIL}, "active": True}
    nodes = render(searches_page(), payload)
    listing = assert_inert(nodes["#list"]["innerHTML"], EVIL)
    button = next(attrs for tag, attrs in listing.tags if tag == "button")
    assert button["data-edit"] == EVIL_ATTR


def test_the_searches_form_collects_one_item_per_line_and_keeps_unlisted_fields() -> None:
    html = searches_page()
    expression = """
current = {id:'srch_1', excluded_companies:['Acme'], seniority_levels:['ENTRY'],
  locations:{allow_relocation:false, cities:[]}, compensation:{period:'YEAR', notes:'n'}};
isNew = false;
const f = new FormData({
  name:' My search ', target_titles:'Clerk\\n\\n  Teller \\r\\nAssistant', cities:'Dallas\\nPlano',
  allow_remote:'on', compensation_minimum:'55000', compensation_target:'',
  compensation_currency:'', travel_max_percent:'10', max_age_days:'', shortlist_target:'8',
  type_FULL_TIME:'on', type_CONTRACT:'on'
});
globalThis.collected = collect(f);
document.querySelector('#out').textContent = JSON.stringify(globalThis.collected);
"""
    nodes = render(html, {"searches": [], "profile": {}}, expression)
    collected = json.loads(nodes["#out"]["textContent"])
    assert collected["name"] == "My search"
    assert collected["target_titles"] == ["Clerk", "Teller", "Assistant"]
    assert collected["locations"]["cities"] == ["Dallas", "Plano"]
    assert collected["locations"]["allow_remote"] is True
    assert collected["locations"]["allow_hybrid"] is False
    assert collected["compensation"] == {
        "period": "YEAR",
        "notes": "n",
        "minimum": 55000,
        "target": None,
        "currency": "USD",
    }
    assert collected["travel_max_percent"] == 10
    assert collected["max_age_days"] is None
    assert collected["shortlist_target"] == 8
    assert collected["employment_types"] == ["FULL_TIME", "CONTRACT"]
    # Fields the form does not show survive the round trip, so saving cannot wipe them.
    assert collected["excluded_companies"] == ["Acme"]
    assert collected["seniority_levels"] == ["ENTRY"]
    assert collected["id"] == "srch_1"


def test_the_settings_page_renders_values_as_text() -> None:
    payload = {
        "automation_dry_run": True,
        "dry_run_env_var": EVIL,
        "llm_provider": EVIL,
        "llm_model": EVIL,
        "database_url": EVIL,
        "private_dir": EVIL,
        "artifacts_dir": EVIL,
        "handoff_timeout_seconds": 2700,
        "playwright": {"chromium_installed": False},
        "active_search_profile_name": EVIL,
        "active_search_profile_id": EVIL,
        "version": EVIL,
    }
    nodes = render(settings_page(), payload)
    assert_inert(nodes["#settings"]["innerHTML"], EVIL)


def test_the_opportunity_card_shows_pay_confidence_gap_and_both_actions_as_text() -> None:
    row = {
        "id": EVIL_ATTR,
        "title": EVIL,
        "company": EVIL,
        "location": EVIL,
        "status": EVIL,
        "compensation_text": EVIL,
        "confidence": 0.72,
        "primary_gap": EVIL,
        "why_this_job": EVIL,
        "rank_position": 1,
        "fit_score": 0.81,
        "salary_min": 1,
        "salary_max": 2,
    }
    nodes = render(opportunities(), {"opportunities": [row]}, "render(all)")
    card = assert_inert(nodes["#queue"]["innerHTML"], EVIL)
    assert "Confidence: 72%" in card.joined
    assert "Fit 81%" in card.joined
    buttons = [attrs for tag, attrs in card.tags if tag == "button"]
    # Prepare stays first; Pass follows it.
    assert buttons[0]["data-id"] == EVIL_ATTR
    assert buttons[1]["data-pass"] == EVIL_ATTR
    bare = {"id": "x", "title": "T", "company": "C", "status": "SHORTLISTED", "fit_score": None}
    nodes = render(opportunities(), {"opportunities": [bare]}, "render(all)")
    text = Markup(nodes["#queue"]["innerHTML"]).joined
    assert "Pay: not listed" in text and "Confidence: not assessed" in text
    assert "Biggest gap: none noted" in text
