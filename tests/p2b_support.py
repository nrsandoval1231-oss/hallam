"""Shared helpers for the decision-page tests (opportunity detail, review, pipeline, export).

Everything here goes through the product's own services and repositories, so a test sets up
a state by the same edges the engine takes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from sabra_api.main import app
from sabra_domain.enums import ApplicationStatus, EntityType, EventType, JobStatus, TruthLevel
from sabra_domain.schemas import AnswerRecord
from sabra_engine.approval import ApprovalService
from sabra_engine.documents import DocumentService

EVIL = '<img src=x onerror="alert(1)">'
EVIL_ATTR = '" onmouseover="alert(1)'


def api(prepared, monkeypatch) -> TestClient:
    """A client whose routes read the `prepared` fixture's database."""

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    return TestClient(app)


def make_complete(prepared):
    """Verify the answers the fixture package was missing and prepare a COMPLETE revision 2."""

    def verified(key: str, value: str, **extra) -> AnswerRecord:
        return AnswerRecord(question_key=key, value=value, level=TruthLevel.VERIFIED, **extra)

    with prepared.ctx.repos() as repos:
        profile = repos.candidates.active_profile(prepared.candidate_id)
        profile.answer_memory.update(
            {
                "sponsorship": verified("sponsorship", "No"),
                "salary_expectations": verified(
                    "salary_expectations",
                    "$130,000",
                    sensitive=True,
                    evidence=["owner_verified"],
                ),
                "start_date": verified("start_date", "2 weeks"),
                "background_check": verified(
                    "background_check", "Yes", sensitive=True, evidence=["owner_verified"]
                ),
                "relocation": verified("relocation", "No"),
                "travel": verified("travel", "Yes"),
            }
        )
        repos.candidates.save_profile(prepared.candidate_id, profile)
    result = DocumentService(prepared.ctx).prepare_package(prepared.opportunity_id)
    with prepared.ctx.read_repos() as repos:
        assert repos.packages.get(result.package_id).status == "COMPLETE"
    return result


def approve(prepared) -> None:
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="owner", acknowledge_unresolved=True
    )


def mark_applied(prepared, *, submitted_days_ago: int = 30) -> None:
    """Approve, then take the application to APPLIED by the engine's own edges.

    Submitted `submitted_days_ago` days back, so an outcome can be dated after it.
    """

    approve(prepared)
    with prepared.ctx.repos() as repos:
        repos.applications.set_status(prepared.application_id, ApplicationStatus.APPLYING)
        repos.applications.set_status(
            prepared.application_id,
            ApplicationStatus.APPLIED,
            submission_time=datetime.now(UTC) - timedelta(days=submitted_days_ago),
            submission_method="HEADED_HUMAN",
        )
        repos.opportunities.set_status(prepared.opportunity_id, JobStatus.APPLYING)
        repos.opportunities.set_status(prepared.opportunity_id, JobStatus.APPLIED)


def application_status(prepared) -> str:
    with prepared.ctx.read_repos() as repos:
        return repos.applications.get(prepared.application_id).status


def opportunity_status(prepared) -> str:
    with prepared.ctx.read_repos() as repos:
        return repos.opportunities.get(prepared.opportunity_id).status


def job_statuses(prepared) -> set[str]:
    with prepared.ctx.read_repos() as repos:
        return {j.status for j in repos.opportunities.list_jobs(prepared.opportunity_id)}


def seed_scoring(prepared, *, passed: bool = True) -> None:
    """A fit assessment, a research packet and the hard-filter event, as ranking leaves them."""

    with prepared.ctx.repos() as repos:
        repos.fit.upsert(
            prepared.opportunity_id,
            prepared.candidate_id,
            {
                "fit_score": 0.82,
                "career_score": 0.9,
                "qualification_score": 0.7,
                "compensation_score": 0.8,
                "location_score": 1.0,
                "industry_score": 0.6,
                "trajectory_score": 0.5,
                "confidence": 0.74,
                "rank_score": 0.71,
                "strengths": ["Eleven years in aesthetics sales"],
                "gaps": ["No stated experience with Botox portfolios"],
                "risks": ["Territory is larger than the last one"],
                "why_this_job": "Your territory-management record matches the regional scope.",
                "component_rationale": {
                    "career_score": "title overlaps with Regional Sales Manager",
                    "qualification_score": "4 of 6 requirements evidenced",
                },
            },
        )
        repos.opportunities.set_rank(prepared.opportunity_id, 0.71, 1)
        repos.research.upsert(
            prepared.opportunity_id,
            {
                "company_description": "Aesthetics company.",
                "industry": "Medical aesthetics",
                "verified_facts": ["Headquartered in Nashville"],
                "inferences": ["Likely reports to a VP of Sales"],
                "sources": ["https://example.test/about"],
            },
            "deterministic",
        )
        if passed:
            repos.events.append(
                EntityType.OPPORTUNITY,
                prepared.opportunity_id,
                EventType.JOB_SCORED,
                {
                    "stage": "hard_filter_passed",
                    "checks_run": ["compensation", "geography"],
                    "checks_skipped": ["travel"],
                },
            )
        else:
            repos.events.append(
                EntityType.OPPORTUNITY,
                prepared.opportunity_id,
                EventType.JOB_REJECTED,
                {
                    "reasons": ["BELOW_MINIMUM_COMPENSATION"],
                    "detail": {"BELOW_MINIMUM_COMPENSATION": "pays 60000, minimum is 90000"},
                    "checks_run": ["compensation"],
                    "checks_skipped": [],
                },
            )


# ---------------------------------------------------------------------------
# The node harness for page scripts
# ---------------------------------------------------------------------------

HARNESS = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const nodes = {};
const calls = [];
const shown = [];
// The page's own <dialog>. Native confirm()/prompt() are deliberately absent from the
// context, so a page that still calls one fails here instead of passing silently.
// `input.dialog` is 'auto' (the "person" answers at once: Confirm when `input.confirm` is
// not false, else Cancel, typing `input.typed` first when given) or 'manual' (the test
// drives #ask-ok / #ask-cancel itself).
const make = () => ({
  innerHTML: '', textContent: '', value: '', disabled: false, checked: false, hidden: false,
  dataset: {}, listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback },
  querySelectorAll() { return [] }, querySelector() { return make() }, closest() { return make() },
  reset() {}, focus() {}, setAttribute() {}, open: false,
  showModal() {
    this.open = true;
    shown.push({text: node('#ask-text').textContent, input: !node('#ask-input').hidden,
                label: node('#ask-ok').textContent});
    if (input.dialog === 'manual') return;
    setImmediate(() => {
      if (input.confirm !== false) {
        if (input.typed !== undefined && input.typed !== null) node('#ask-input').value = input.typed;
        const ok = node('#ask-ok').onclick; if (ok) ok();
      } else {
        const cancel = node('#ask-cancel').onclick; if (cancel) cancel();
      }
    });
  },
  // Like a browser, close() queues its `close` event instead of firing it at once, so a
  // handler left over from an earlier question can see it after the dialog reopened.
  close() { this.open = false; setImmediate(() => { if (this.onclose) this.onclose({}) }) }
});
const node = selector => nodes[selector] ||= make();
const respond = (url, options) => {
  const method = (options && options.method) || 'GET';
  const key = method + ' ' + url;
  const routes = input.routes || {};
  const hit = routes[key] !== undefined ? routes[key] : (routes[url] !== undefined ? routes[url] : input.payload);
  const status = (input.statuses || {})[key] || (input.statuses || {})[url] || 200;
  calls.push({url, method, body: options && options.body ? options.body : null});
  return {ok: status < 400, status, json: async () => hit, text: async () => JSON.stringify(hit)};
};
const ctx = {
  document: {querySelector: node, querySelectorAll: () => []},
  fetch: async (url, options) => respond(url, options),
  FormData: class { constructor(values) { this.values = values } get(key) { return this.values[key] } },
  console, setImmediate,
  setInterval() { return 1 }, clearInterval() {},
  Date, Math, JSON, Object, Array, String, Number, encodeURIComponent, URLSearchParams,
  location: {search: input.search || '', href: ''}
};
vm.createContext(ctx);
vm.runInContext(input.script, ctx);
(async () => {
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle(); await settle();
  if (input.expression) await vm.runInContext('(async () => {' + input.expression + '})()', ctx);
  await settle(); await settle();
  process.stdout.write(JSON.stringify({nodes, calls, shown, href: ctx.location.href}));
})().catch(error => { console.error(error); process.exit(1) });
"""


def render(
    html: str,
    payload: dict | None = None,
    expression: str = "",
    *,
    routes: dict | None = None,
    statuses: dict | None = None,
    search: str = "",
    confirm: bool = True,
    typed: str | None = None,
    dialog: str = "auto",
) -> dict:
    """Run a page's script under node and return `{nodes, calls, shown, href}`.

    `shown` lists every dialog the page opened (its text, whether it had a text box, and
    the label on its Confirm button). By default the person answers at once; pass
    `confirm=False` to Cancel, `typed=` to type into the text box first, or
    `dialog="manual"` to drive the buttons from `expression`.
    """

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    result = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps(
            dict(
                script=script,
                payload=payload or {},
                expression=expression,
                routes=routes or {},
                statuses=statuses or {},
                search=search,
                confirm=confirm,
                typed=typed,
                dialog=dialog,
            )
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


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

    def hrefs(self) -> list[str]:
        return [attrs["href"] for tag, attrs in self.tags if tag == "a" and "href" in attrs]


def assert_inert(markup: str, expected_text: str) -> Markup:
    """No element or handler built from data: the evil string is only ever text."""

    parsed = Markup(markup)
    assert all(tag != "img" for tag, _ in parsed.tags), markup
    assert all(not key.lower().startswith("on") for _, attrs in parsed.tags for key in attrs)
    assert expected_text in parsed.joined
    return parsed
