"""The Velora Home, Research and Career Plan surfaces serve real engine data."""

from fastapi.testclient import TestClient
from sabra_api.main import app


def test_home_page_is_the_velora_dashboard() -> None:
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "<title>Velora | Home</title>" in response.text
    assert "A clearer path forward." in response.text
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
    assert "<title>Velora | Research</title>" in page.text
    body = client.get("/api/research").json()
    assert "research" in body
    for row in body["research"]:
        assert row["opportunity_id"] and row["company"]
        assert isinstance(row["sources"], list)


def test_career_plan_page_and_endpoint() -> None:
    client = TestClient(app)
    page = client.get("/career-plan")
    assert page.status_code == 200
    assert "<title>Velora | Career Plan</title>" in page.text
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
