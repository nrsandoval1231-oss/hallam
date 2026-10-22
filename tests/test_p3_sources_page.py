"""The Settings page's "Job boards" section renders every dynamic string as text.

The page script is run under node with a stubbed DOM. `fetch` answers by URL, every call
is recorded, and the captured event listeners let a test press the page's buttons.
"""

from __future__ import annotations

from html.parser import HTMLParser

from p3_support import run_page_script
from sabra_api.main import settings_page

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


def run_page(routes: dict, steps: list | None = None) -> dict:
    return run_page_script(settings_page(), routes, steps)


def assert_inert(markup: str, expected_text: str) -> Markup:
    parsed = Markup(markup)
    assert all(tag != "img" for tag, _ in parsed.tags), markup
    assert all(not key.lower().startswith("on") for _, attrs in parsed.tags for key in attrs)
    assert expected_text in parsed.joined
    return parsed


SETTINGS = {
    "automation_dry_run": True,
    "dry_run_env_var": "SABRA_AUTOMATION_DRY_RUN",
    "llm_provider": "none",
    "database_url": "x",
    "artifacts_dir": "y",
    "handoff_timeout_seconds": 2700,
    "playwright": {"chromium_installed": True},
    "version": "1",
}


def source(**overrides) -> dict:
    base = {
        "id": "ashby-siepe",
        "kind": "ASHBY",
        "token": "siepe",
        "display_name": "Siepe",
        "enabled": True,
        "verified_jobs": 10,
        "health": "HEALTHY",
        "total_discovered": 10,
        "last_error": None,
    }
    base.update(overrides)
    return base


def routes(sources: list[dict]) -> dict:
    return {
        "GET /api/settings": {"status": 200, "body": SETTINGS},
        "GET /api/sources": {"status": 200, "body": {"sources": sources}},
    }


def test_the_page_has_one_script_and_the_new_controls() -> None:
    html = settings_page()
    assert html.count("<script>") == 1
    for needle in (
        "Job boards",
        "Add a board",
        'id="board-url"',
        'id="add-btn"',
        "Check this board",
        "/api/sources/probe",
        "/api/sources/",
    ):
        assert needle in html


def test_every_board_field_is_rendered_as_text() -> None:
    evil = source(
        id=EVIL_ATTR,
        kind=EVIL,
        display_name=EVIL,
        health=EVIL,
        last_error=EVIL,
        verified_jobs=EVIL,
        total_discovered=EVIL,
    )
    out = run_page(routes([evil]))
    html = out["nodes"]["#sources"]["innerHTML"]
    parsed = assert_inert(html, EVIL)
    buttons = [attrs for tag, attrs in parsed.tags if tag == "button"]
    assert buttons and buttons[0]["data-id"] == EVIL_ATTR
    assert buttons[0]["data-act"] == "disable"


def test_the_row_shows_name_kind_state_verified_jobs_health_and_error() -> None:
    row = source(enabled=False, last_error="HTTP 503", health="DEGRADED")
    out = run_page(routes([row]))
    text = Markup(out["nodes"]["#sources"]["innerHTML"]).joined
    for expected in ("Siepe", "ASHBY", "Off", "DEGRADED", "10 postings when added", "HTTP 503"):
        assert expected in text, expected
    assert "Enable" in text and "Disable" not in text


def test_an_empty_registry_and_a_failed_load_say_so() -> None:
    assert "No job boards" in run_page(routes([]))["nodes"]["#sources"]["innerHTML"]
    failing = routes([])
    failing["GET /api/sources"] = {"status": 500, "body": {"detail": EVIL}}
    out = run_page(failing)
    assert out["nodes"]["#sources"]["textContent"] == EVIL  # set as text, never as markup


def test_enable_and_disable_post_and_reload() -> None:
    steps = [
        {
            "fire": {
                "selector": "#sources",
                "type": "click",
                "button": {"act": "disable", "id": "ashby-siepe"},
            }
        }
    ]
    table = routes([source()])
    table["POST /api/sources/ashby-siepe/disable"] = {"status": 200, "body": {}}
    out = run_page(table, steps)
    posts = [c for c in out["calls"] if c["method"] == "POST"]
    assert [c["url"] for c in posts] == ["/api/sources/ashby-siepe/disable"]
    reads = [c for c in out["calls"] if c["method"] == "GET" and c["url"] == "/api/sources"]
    assert len(reads) == 2, "the list is reloaded"


def test_a_refused_toggle_shows_the_server_message_as_text() -> None:
    table = routes([source()])
    table["POST /api/sources/ashby-siepe/enable"] = {"status": 404, "body": {"detail": EVIL}}
    out = run_page(
        table,
        [
            {
                "fire": {
                    "selector": "#sources",
                    "type": "click",
                    "button": {"act": "enable", "id": "ashby-siepe"},
                }
            }
        ],
    )
    assert out["nodes"]["#sources-line"]["textContent"] == EVIL


PROBE_OK = {
    "kind": "ASHBY",
    "token": "siepe",
    "live": True,
    "posting_count": 10,
    "local_or_remote_count": 7,
    "sample_titles": [EVIL, "Senior Software Engineer"],
    "profile_has_cities": True,
    "already_in_registry": None,
    "error": None,
}


def probe_steps(url: str = "https://jobs.ashbyhq.com/siepe") -> list:
    return [
        {"set": {"#board-url": url, "#board-kind": ""}},
        {"fire": {"selector": "#probe-form", "type": "submit"}},
    ]


def test_probing_shows_the_result_as_text_and_offers_add() -> None:
    table = routes([])
    table["POST /api/sources/probe"] = {"status": 200, "body": PROBE_OK}
    out = run_page(table, probe_steps())
    line = out["nodes"]["#probe-result"]["textContent"]
    assert "Live. 10 postings, 7 mention your cities or remote." in line
    assert EVIL in line  # kept verbatim as text
    assert out["nodes"]["#add-btn"]["hidden"] is False
    probe = next(c for c in out["calls"] if c["url"] == "/api/sources/probe")
    assert probe["body"] == {"url_or_token": "https://jobs.ashbyhq.com/siepe", "kind": None}


def test_a_dead_or_known_board_cannot_be_added() -> None:
    dead = routes([])
    dead["POST /api/sources/probe"] = {
        "status": 200,
        "body": {**PROBE_OK, "live": False, "error": "HTTP 404"},
    }
    out = run_page(dead, probe_steps())
    assert "did not answer as a live job board: HTTP 404" in out["nodes"]["#probe-result"]["textContent"]
    assert out["nodes"]["#add-btn"]["hidden"] is True

    known = routes([])
    known["POST /api/sources/probe"] = {
        "status": 200,
        "body": {**PROBE_OK, "already_in_registry": "ashby-siepe"},
    }
    out = run_page(known, probe_steps())
    assert "already in your list as ashby-siepe" in out["nodes"]["#probe-result"]["textContent"]
    assert out["nodes"]["#add-btn"]["hidden"] is True


def test_add_posts_what_was_probed_and_reloads_the_list() -> None:
    table = routes([])
    table["POST /api/sources/probe"] = {"status": 200, "body": PROBE_OK}
    table["POST /api/sources"] = {
        "status": 201,
        "body": {"source": {"display_name": EVIL}},
    }
    steps = [
        *probe_steps("https://jobs.ashbyhq.com/siepe"),
        {"set": {"#board-name": "Siepe", "#board-industry": "Fintech"}},
        {"fire": {"selector": "#add-btn", "type": "click"}},
    ]
    out = run_page(table, steps)
    created = next(c for c in out["calls"] if c["method"] == "POST" and c["url"] == "/api/sources")
    assert created["body"] == {
        "url_or_token": "https://jobs.ashbyhq.com/siepe",
        "kind": None,
        "display_name": "Siepe",
        "industry": "Fintech",
    }
    assert out["nodes"]["#probe-result"]["textContent"].startswith("Added ")
    assert out["nodes"]["#add-btn"]["hidden"] is True
    reads = [c for c in out["calls"] if c["method"] == "GET" and c["url"] == "/api/sources"]
    assert len(reads) == 2, "the list is reloaded after adding"


def test_a_refused_add_shows_the_reason() -> None:
    table = routes([])
    table["POST /api/sources/probe"] = {"status": 200, "body": PROBE_OK}
    table["POST /api/sources"] = {"status": 409, "body": {"detail": "already in the registry"}}
    steps = [*probe_steps(), {"fire": {"selector": "#add-btn", "type": "click"}}]
    out = run_page(table, steps)
    assert out["nodes"]["#probe-result"]["textContent"] == "already in the registry"


def test_the_settings_values_still_render_beside_the_new_section() -> None:
    out = run_page(routes([source()]))
    assert "Dry run" in out["nodes"]["#settings"]["innerHTML"]
