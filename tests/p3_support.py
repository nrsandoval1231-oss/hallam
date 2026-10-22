"""Shared doubles for the phase 3 tests: a canned HTTP client and registry helpers.

Nothing here touches a network. `ScriptedHttp` answers by URL substring, can answer with
an HTTP error or a transport failure, and records every request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sabra_domain.errors import ProviderError
from sabra_providers.http import FetchRecord, FetchResult

FIXTURES = Path(__file__).parent / "fixtures"


def ashby_board() -> dict[str, Any]:
    return json.loads((FIXTURES / "ashby_board_sample.json").read_text(encoding="utf-8"))


class ScriptedHttp:
    """`rules` maps a URL substring to a body, an int HTTP status, or an Exception.

    A body is JSON-encoded unless it is a `str` or `bytes`. An int raises the same
    `ProviderError` the real client raises for that status.
    """

    def __init__(self, rules: dict[str, Any]) -> None:
        self.rules = rules
        self.requests: list[tuple[str, dict | None]] = []
        self.calls: list[FetchRecord] = []
        self.deadline: float | None = None
        self.closed = False

    def get(self, url: str, *, params: dict | None = None) -> FetchResult:
        self.requests.append((url, params))
        for pattern, rule in self.rules.items():
            if pattern not in url:
                continue
            if isinstance(rule, Exception):
                raise rule
            if isinstance(rule, int):
                raise ProviderError("http", f"GET {url} returned HTTP {rule}")
            if isinstance(rule, bytes):
                payload = rule
            elif isinstance(rule, str):
                payload = rule.encode("utf-8")
            else:
                payload = json.dumps(rule).encode("utf-8")
            record = FetchRecord(
                url=url,
                status=200,
                bytes_read=len(payload),
                content_hash="stub",
                elapsed_ms=1,
                attempts=1,
            )
            self.calls.append(record)
            return FetchResult(url=url, status=200, body=payload, record=record)
        raise AssertionError(f"unexpected URL: {url}")

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Running a page's script under node
# ---------------------------------------------------------------------------

NODE_HARNESS = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const nodes = {};
const node = selector => nodes[selector] ||= {
  innerHTML: '', textContent: '', value: '', disabled: false, hidden: false,
  listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback },
  querySelectorAll() { return [] },
};
const calls = [];
const ctx = {
  document: {querySelector: node, querySelectorAll: () => []},
  fetch: async (url, options) => {
    calls.push({url, method: (options && options.method) || 'GET',
                body: options && options.body ? JSON.parse(options.body) : null});
    const reply = input.routes[(options && options.method || 'GET') + ' ' + url]
      || input.routes[url] || {status: 200, body: {}};
    const text = typeof reply.body === 'string' ? reply.body : JSON.stringify(reply.body);
    return {ok: reply.status < 400, status: reply.status, text: async () => text};
  },
  alert() {}, confirm() { return true }, console, setInterval() { return 1 }, clearInterval() {},
  Date, Math, JSON, Object, Array, String, Number, Set, encodeURIComponent
};
vm.createContext(ctx);
const settle = () => new Promise(resolve => setImmediate(resolve));
const evaluated = [];
(async () => {
  vm.runInContext(input.script, ctx);
  for (let i = 0; i < 5; i++) await settle();
  for (const step of input.steps) {
    if (step.set) for (const [selector, value] of Object.entries(step.set)) node(selector).value = value;
    if (step.eval) evaluated.push(vm.runInContext(step.eval, ctx));
    if (step.fire) {
      const target = node(step.fire.selector);
      const event = Object.assign({preventDefault() {}}, step.fire.event || {});
      if (step.fire.button) {
        event.target = {closest: () => ({dataset: step.fire.button})};
      }
      await target.listeners[step.fire.type](event);
    }
    for (let i = 0; i < 5; i++) await settle();
  }
  process.stdout.write(JSON.stringify({nodes, calls, evaluated}));
})().catch(error => { console.error(error); process.exit(1) });
"""


def run_page_script(html: str, routes: dict, steps: list | None = None) -> dict:
    """Run the page's one `<script>` under node; skip the test when node is missing.

    `routes` maps "METHOD /url" (or just "/url") to {"status", "body"}. A step may set
    input values, evaluate an expression in the page, or fire a captured listener.
    """

    import re
    import shutil
    import subprocess

    import pytest

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    result = subprocess.run(
        ["node", "-e", NODE_HARNESS],
        input=json.dumps({"script": script, "routes": routes, "steps": steps or []}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)
