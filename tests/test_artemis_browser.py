"""Desktop and mobile Chromium smoke checks for the ARTEMIS pages.

The HTML is rendered by the real FastAPI app. All API responses and static assets
are synthetic/local, so a browser run cannot reach an employer or provider.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Browser, Page, sync_playwright
from sabra_api.main import app
from sabra_api.pages import LOGO_MARK_SVG, page_shell
from sabra_api.review_pages import DETAIL_CSS, review_body


@pytest.fixture(scope="module")
def chromium() -> Browser:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


EMPTY_HOME = {
    "generated_at": "2026-10-28T12:00:00Z",
    "search_profile_name": "Synthetic career search",
    "automation_dry_run": True,
    "metrics": {"matches": 0, "applications": 0, "interviews": 0, "offers": 0},
    "featured": None,
    "recent": [],
    "progress": {"shortlisted": 0, "applied": 0, "interviews": 0, "offers": 0},
    "today": {
        "counts": {"ready_for_approval": 0, "needs_information": 0, "handoffs_waiting": 0,
                   "new_high_fit": 0, "employer_responses": 0},
        "has_resume": False,
        "last_run": None,
        "active_run": None,
        "open_questions": 0,
    },
}

EMPTY_PACKAGES = {"packages": [], "automation_dry_run": True}


def page_html(path: str) -> str:
    response = TestClient(app).get(path)
    assert response.status_code == 200, f"HTML route {path} returned {response.status_code}"
    return response.text


def api_json(path: str) -> dict:
    if path == "/api/home":
        return EMPTY_HOME
    if path == "/api/today":
        return {"counts": EMPTY_HOME["today"]["counts"]}
    if path == "/api/status":
        return {"candidate_name": "Synthetic Candidate"}
    if path == "/api/packages":
        return EMPTY_PACKAGES
    if path == "/api/ask":
        return {"answer": "There are no synthetic items requiring attention.", "links": []}
    return {"opportunities": []}


def open_page(chromium: Browser, path: str, viewport: dict[str, int]) -> tuple[Page, list[str]]:
    html = page_html(path)
    context = chromium.new_context(viewport=viewport, device_scale_factor=1)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route_request(route):
        request = route.request
        url = request.url
        request_path = url.split("?", 1)[0].split("127.0.0.1", 1)[-1]
        if request.method == "GET" and request_path in {path, "/opportunities"}:
            route.fulfill(status=200, body=html if request_path == path else page_html("/opportunities"),
                          content_type="text/html; charset=utf-8")
        elif "/api/" in url:
            route.fulfill(json=api_json(request.url.split("127.0.0.1", 1)[-1].split("?", 1)[0]))
        elif url.endswith("/favicon.svg"):
            route.fulfill(status=200, body="<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/svg+xml")
        else:
            # Fonts and every other remote/static request are deliberately blocked.
            route.abort()

    context.route("**/*", route_request)
    page.goto(f"http://127.0.0.1{path}", wait_until="domcontentloaded")
    page.wait_for_timeout(50)
    return page, errors


def assert_shared_chrome(page: Page, errors: list[str], viewport_width: int) -> None:
    assert not errors, f"page JavaScript errors: {errors}"
    text = page.locator("body").inner_text().upper()
    assert "VELORA" not in text
    assert page.get_by_role("link", name="ARTEMIS").is_visible()
    assert page.locator(".brand .mark").evaluate("el => el.getAttribute('viewBox')") == "0 0 64 64"
    assert page.locator(".brand .mark path").evaluate_all("els => els.map(el => el.getAttribute('d'))") == re.findall(
        r'<path d="([^"]+)"', LOGO_MARK_SVG
    )
    assert page.locator(".brand .mark").evaluate("el => getComputedStyle(el).width") == "30px"
    assert page.evaluate("document.documentElement.scrollWidth") <= viewport_width


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
@pytest.mark.parametrize(
    ("path", "visible_text"),
    [
        ("/", "Your next move is taking shape."),
        ("/opportunities", "Opportunities"),
        ("/applications", "Applications"),
    ],
)
def test_artemis_pages_render_at_desktop_and_mobile(chromium, viewport, path, visible_text):
    page, errors = open_page(chromium, path, viewport)
    try:
        assert_shared_chrome(page, errors, viewport["width"])
        if path == "/opportunities":
            page.get_by_role("heading", name="Opportunities").wait_for()
        elif path == "/applications":
            page.get_by_role("heading", name="Applications").wait_for()
        else:
            page.get_by_role("heading", name=visible_text).wait_for()
        if path == "/":
            assert page.get_by_text("No featured opportunity yet", exact=True).is_visible()
            page.locator("#upcoming-list").get_by_text("Upload your resume").wait_for()
        if path == "/applications":
            assert page.get_by_text("No packages yet.", exact=True).is_visible()
    finally:
        page.context.close()


def test_mobile_menu_navigates_to_opportunities(chromium):
    page, errors = open_page(chromium, "/", {"width": 390, "height": 844})
    try:
        assert_shared_chrome(page, errors, 390)
        page.locator(".navtoggle summary").click()
        page.locator('.mobilemenu a[href="/opportunities"]').click()
        assert page.url.endswith("/opportunities")
        assert page.get_by_role("heading", name="Opportunities").is_visible()
    finally:
        page.context.close()


def test_ask_artemis_modal_opens_and_answers_from_synthetic_api(chromium):
    page, errors = open_page(chromium, "/", {"width": 1440, "height": 900})
    try:
        assert_shared_chrome(page, errors, 1440)
        page.get_by_role("button", name=re.compile("Ask Artemis")).click()
        dialog = page.get_by_role("dialog", name="Ask Artemis")
        assert dialog.is_visible()
        page.get_by_role("textbox", name="Ask Artemis a question").fill("What should I do?")
        page.get_by_role("button", name="Ask", exact=True).click()
        page.wait_for_function("document.querySelector('#velora-ask-out').textContent.includes('synthetic items')")
        assert not errors, f"page JavaScript errors: {errors}"
    finally:
        page.context.close()


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_package_review_page_renders_with_synthetic_package(chromium, viewport):
    package_id = "synthetic-package"
    package = {
        "package": {"id": package_id, "revision": 1, "status": "NEEDS_INFORMATION",
                     "content_hash": "synthetic-hash", "truth_clean": False, "unresolved_count": 1,
                     "generated_at": "2026-10-28T12:00:00Z"},
        "application_id": "synthetic-application", "application_status": "PREPARED",
        "opportunity": {"id": "synthetic-opportunity", "title": "Operations Director", "company": "Example Co"},
        "job": {"title": "Operations Director", "company": "Example Co", "description": "Synthetic opening"},
        "fit": None, "analysis": {}, "materials": {"answers": []}, "unresolved_questions": ["Confirm location"],
        "warnings": [], "generation_notes": [],
        "actions": {"can_approve": False, "approve_reason": "Resolve the outstanding question first.",
                    "refresh": None, "can_reject": False},
    }
    html = page_shell("Review application", review_body(package_id), active="/applications", css=DETAIL_CSS)
    context = chromium.new_context(viewport=viewport)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        if route.request.url.endswith("/applications"):
            route.fulfill(body=page_html("/applications"), content_type="text/html; charset=utf-8")
        elif route.request.url.endswith(f"/packages/{package_id}"):
            route.fulfill(body=html, content_type="text/html; charset=utf-8")
        elif route.request.url.endswith("/api/packages"):
            route.fulfill(json={"packages": [{"package_id": package_id, "content_hash": "synthetic-hash",
                                                 "title": "Operations Director", "company": "Example Co",
                                                 "status": "NEEDS_INFORMATION", "review_label": "needs information",
                                                 "approvable": False, "application_id": "synthetic-application"}],
                                "automation_dry_run": True})
        elif route.request.url.endswith(f"/api/packages/{package_id}/review"):
            route.fulfill(json=package)
        elif "/api/" in route.request.url:
            route.fulfill(json=api_json(route.request.url.split("127.0.0.1", 1)[-1].split("?", 1)[0]))
        else:
            route.abort()

    context.route("**/*", serve)
    try:
        page.goto("http://127.0.0.1/applications", wait_until="domcontentloaded")
        page.get_by_role("link", name="Read package").click()
        page.wait_for_url("**/packages/synthetic-package")
        page.wait_for_selector("#sections h2")
        assert page.get_by_role("heading", name="Operations Director at Example Co").is_visible()
        assert page.get_by_role("heading", name="6. Unresolved questions").is_visible()
        assert_shared_chrome(page, errors, viewport["width"])
    finally:
        context.close()
