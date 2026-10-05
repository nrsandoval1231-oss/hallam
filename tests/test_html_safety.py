"""Execute dashboard JavaScript offline and inspect generated markup."""

import json
import re
import subprocess
import tomllib
from html.parser import HTMLParser
from pathlib import Path

from sabra_api.main import answers_page, opportunities


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def render(html, payload, expression):
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const nodes = {};
const node = selector => nodes[selector] ||= {
  innerHTML: '', textContent: '', value: '',
  listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback }, querySelectorAll() { return [] }, reset() {}
};
const ctx = {
  document: {querySelector: node},
  fetch: async () => ({ok:true, json:async()=>input.payload,
    text:async()=>JSON.stringify(input.payload)}),
  FormData: class { constructor(values) { this.values = values } get(key) { return this.values[key] } },
  alert() {}, console
};
vm.createContext(ctx);
vm.runInContext(input.script, ctx);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  if(input.expression) vm.runInContext(input.expression, ctx);
  process.stdout.write(JSON.stringify(nodes));
})();
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=json.dumps(dict(script=script, payload=payload, expression=expression)),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def assert_inert(markup, payload):
    parsed = Markup(markup)
    assert all(tag != "img" for tag, _ in parsed.tags)
    assert all(not key.lower().startswith("on") for _, attrs in parsed.tags for key in attrs)
    assert payload in "".join(parsed.text)
    return parsed


def test_opportunities_render_external_fields_as_text():
    evil = '<img src=x onerror="alert(1)">'
    row = dict(
        id="x'\");alert(1);//",
        title=evil,
        company=evil,
        location=evil,
        status=evil,
        why_this_job=evil,
        rank_position=evil,
        fit_score=None,
    )
    nodes = render(opportunities(), dict(opportunities=[row]), "render(all)")
    parsed = assert_inert(nodes["#queue"]["innerHTML"], evil)
    button = next(attrs for tag, attrs in parsed.tags if tag == "button")
    assert button["data-id"] == row["id"]


def test_answer_memory_values_and_attribute_keys_are_inert():
    evil = '<img src=x onerror="alert(1)">'
    key = '" onmouseover="alert(1)'
    nodes = render(
        answers_page(),
        dict(
            answers=[dict(question_key=key, level=evil, value=evil, verified=False, sensitive=True)]
        ),
        "",
    )
    parsed = assert_inert(nodes["#saved"]["innerHTML"], evil)
    button = next(attrs for tag, attrs in parsed.tags if tag == "button")
    assert button["data-key"] == key


def test_pdf_parser_is_installed_by_api_extra():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert any(
        dep.lower().startswith("pymupdf")
        for dep in project["project"]["optional-dependencies"]["api"]
    )


def test_pdf_resume_upload_extracts_inferred_evidence():
    import fitz
    from fastapi.testclient import TestClient
    from sabra_api.main import app

    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "TEST CANDIDATE Operations leader")
        content = pdf.tobytes()
    response = TestClient(app).post(
        "/api/candidate/resume",
        files={"resume": ("resume.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["resume_id"]
    assert response.json()["truth_level"] == "INFERRED"


def test_industry_filter_matches_literal_company_punctuation():
    row = dict(
        id="one",
        title="R&D Manager",
        company="AT&T",
        location="Dallas",
        status="SHORTLISTED",
        fit_score=None,
    )
    nodes = render(
        opportunities(),
        dict(opportunities=[row]),
        "document.querySelector('#filters').listeners.submit({preventDefault(){}, "
        "target:{industry:'AT&T'}})",
    )
    parsed = Markup(nodes["#queue"]["innerHTML"])
    assert "AT&T" in "".join(parsed.text)
    assert "R&D Manager" in "".join(parsed.text)
    assert "No jobs match" not in "".join(parsed.text)
