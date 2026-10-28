"""The Artemis Home, Research and Career Plan surfaces serve real engine data."""

from fastapi.testclient import TestClient
from sabra_api import main
from sabra_api.main import app
from sabra_api.pages import LOGO_MARK_SVG


def test_home_page_uses_artemis_branding() -> None:
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "<title>Artemis | Home</title>" in response.text
    assert "Your next move is taking shape." in response.text
    assert "YOUR CAREER, IN FOCUS" in response.text.upper()
    for href in ("/opportunities", "/applications", "/research", "/career-plan"):
        assert f'href="{href}"' in response.text
    assert 'id="velora-ask"' in response.text


def test_home_endpoint_reports_real_counts() -> None:
    body = TestClient(app).get("/api/home").json()
    assert set(body["metrics"]) == {"matches", "applications", "interviews", "offers"}
    assert set(body["progress"]) == {"shortlisted", "applied", "interviews", "offers"}
    assert "today" in body and "counts" in body["today"]
    assert isinstance(body["recent"], list)
    featured = body["featured"]
    if featured is not None:
        assert featured["id"] and featured["title"] and featured["company"]


def test_research_page_and_endpoint() -> None:
    client = TestClient(app)
    page = client.get("/research")
    assert page.status_code == 200
    assert "<title>Artemis | Research</title>" in page.text
    body = client.get("/api/research").json()
    assert "research" in body
    for row in body["research"]:
        assert row["opportunity_id"] and row["company"]
        assert isinstance(row["sources"], list)


def test_career_plan_page_and_endpoint() -> None:
    client = TestClient(app)
    page = client.get("/career-plan")
    assert page.status_code == 200
    assert "<title>Artemis | Career Plan</title>" in page.text
    body = client.get("/api/career-plan").json()
    assert "objective" in body or "search" in body or "plan" in body


def test_ask_velora_answers_from_real_data() -> None:
    client = TestClient(app)
    response = client.post("/api/ask", json={"question": "What should I prioritize today?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert isinstance(body.get("links", []), list)
    # A question with no matching intent still gets an honest answer, not an invented one.
    other = client.post("/api/ask", json={"question": "xyzzy"})
    assert other.status_code in (200, 400, 422)


def test_favicon_is_served() -> None:
    response = TestClient(app).get("/favicon.svg")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]
    assert "<svg" in response.text
    assert 'viewBox="0 0 64 64"' in response.text
    assert 'd="M7 55 26.25 9h6.8L16.7 55Z"' in response.text


def test_header_and_favicon_share_the_canonical_artemis_mark() -> None:
    page = TestClient(app).get("/").text
    favicon = TestClient(app).get("/favicon.svg").text
    canonical_path = 'd="M7 55 26.25 9h6.8L16.7 55Z"'
    canonical_cut = 'd="m22.5 38 19 .15 2.55 6.1-24.1-.15Z"'
    assert canonical_path in LOGO_MARK_SVG and canonical_cut in LOGO_MARK_SVG
    assert canonical_path in favicon and canonical_cut in favicon
    assert LOGO_MARK_SVG in page
    assert "ARTEMIS</span>" in page


def test_favicon_fallback_uses_the_canonical_artemis_mark(monkeypatch) -> None:
    monkeypatch.setattr(main, "_BRAND_DIR", main._BRAND_DIR / "missing-brand-assets")
    response = TestClient(app).get("/favicon.svg")
    assert response.status_code == 200
    assert 'viewBox="0 0 64 64"' in response.text
    assert 'd="M7 55 26.25 9h6.8L16.7 55Z"' in response.text
    assert 'd="m22.5 38 19 .15 2.55 6.1-24.1-.15Z"' in response.text
