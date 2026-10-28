"""Real local API/browser acceptance over explicitly synthetic candidate evidence.

Every browser request is fulfilled through TestClient; external hosts are refused.
Optional screenshots contain test fixtures only and never claim a private pilot.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sabra_api.main import app


@pytest.mark.parametrize("width,height", [(1280, 853), (1440, 1000), (820, 900), (375, 812)])
def test_complete_local_journeys_use_real_api_and_local_fonts(prepared, monkeypatch, width, height):
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    client = TestClient(app)
    paths = ["/", "/opportunities", f"/opportunities/{prepared.opportunity_id}",
             f"/packages/{prepared.package_id}", "/applications", "/pipeline", "/career-plan", "/research"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        external = []
        writes = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            if url.hostname != "artemis.test":
                external.append(request.url)
                route.abort()
                return
            if request.method != "GET":
                writes.append((request.method, url.path))
                # Ask is read-only despite HTTP POST; all other browser writes refused.
                if url.path != "/api/ask":
                    route.abort()
                    return
            response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                      content=request.post_data,
                                      headers={"content-type": "application/json"} if request.post_data else {})
            route.fulfill(status=response.status_code, body=response.content,
                          content_type=response.headers.get("content-type", "application/octet-stream"))

        page.route("**/*", serve)
        for index, path in enumerate(paths):
            page.goto("http://artemis.test" + path, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            assert not errors, errors
            assert page.locator("main").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth") <= width, path
            assert page.locator(".brand").is_visible()
            assert page.locator("nav.primary").is_visible() or page.locator(".navtoggle summary").is_visible()
            assert page.evaluate("document.fonts.check('16px \"Artemis Sans\"')")
            assert page.evaluate("document.fonts.check('32px \"Artemis Display\"')")
            assert page.locator("body").evaluate("el=>getComputedStyle(el).backgroundColor") == ("rgb(247, 246, 241)" if width < 760 else "rgb(20, 45, 41)")
            if path == "/":
                assert page.locator("#brief-list").inner_text().strip()
                assert page.get_by_role("heading", name="Today’s brief").count() or page.get_by_role("heading", name="Today's brief").count()
            if path.startswith("/opportunities/"):
                page.locator("#op-tab-fit").click()
                assert page.locator("#op-panel-fit").is_visible()
                page.locator("#op-tab-role").click()
                assert page.locator("#op-panel-role").is_visible()
                assert "The posting" in page.locator("#op-panel-role").inner_text()
                page.locator("#op-tab-overview").click()
            if path == "/career-plan":
                for section in ("goals", "skills", "industries", "locations"):
                    page.locator("#tab-" + section).click()
                    assert page.locator("#panel-" + section).is_visible()
                    assert page.locator("#tab-" + section).get_attribute("aria-selected") == "true"
                page.locator("#tab-overview").click()
            if path.startswith("/packages/"):
                page.locator("#studio-tab-cover").click()
                assert page.locator("#studio-panel-cover").is_visible()
                page.locator("#studio-tab-answers").click()
                assert page.locator("#studio-panel-answers").is_visible()
                page.locator("#studio-tab-review").click()
                assert page.locator("#approve").is_visible()
                assert page.locator("#approve").is_disabled()
                assert "unresolved" in page.locator("#studio-panel-review-followup").inner_text().lower()
                page.locator("#studio-tab-resume").click()
            if path == "/":
                if width < 760:
                    page.locator(".navtoggle summary").click()
                    assert page.locator('.mobilemenu a[href="/candidate"]').is_visible()
                    assert page.locator('.mobilemenu a[href="/settings"]').is_visible()
                    assert page.locator("#mobile-ask-open").is_visible()
                    page.locator(".navtoggle summary").click()
                else:
                    page.locator(".usermenu summary").click()
                    assert page.locator('.usermenu .menu a[href="/candidate"]').is_visible()
                    assert page.locator('.usermenu .menu a[href="/settings"]').is_visible()
                    page.locator(".usermenu summary").click()
            evidence = os.getenv("ARTEMIS_SCREENSHOT_DIR")
            if evidence:
                directory = Path(evidence)
                directory.mkdir(parents=True, exist_ok=True)
                label = ["home", "opportunities", "detail", "review", "applications", "pipeline", "career-plan", "research"][index]
                page.screenshot(path=str(directory / f"{label}-{width}.png"), full_page=True)
        page.goto("http://artemis.test/", wait_until="networkidle")
        if width < 760:
            page.locator(".navtoggle summary").click()
            page.locator("#mobile-ask-open").click()
        else:
            page.locator("#velora-ask-open").click()
        page.get_by_role("textbox", name="Ask Artemis a question").fill("Why is this opportunity recommended?")
        page.get_by_role("button", name="Ask", exact=True).click()
        page.wait_for_function("document.querySelector('#velora-ask-out').textContent.includes('Regional Sales Manager')")
        assert "strongest matches" not in page.locator("#velora-ask-out").inner_text()
        assert not errors
        assert not external
        assert writes == [("POST", "/api/ask")]
        browser.close()


def test_local_brand_assets_are_served_and_traversal_cannot_escape():
    client = TestClient(app)
    for path in ("/brand/fonts/CormorantGaramond-Variable.ttf", "/brand/fonts/Manrope-Variable.ttf",
                 "/brand/logo/favicon.svg", "/brand/logo/exports/favicon-16-light.png"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.content
    assert client.get("/brand/%2e%2e/AGENTS.md").status_code == 404
    assert "fonts.googleapis" not in client.get("/").text
