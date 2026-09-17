from fastapi.testclient import TestClient
from sabra_api.main import app


def test_dashboard_is_browser_ready() -> None:
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "Sabra" in response.text
    assert "Opportunity queue" in response.text


def test_opportunities_route_is_available() -> None:
    client = TestClient(app)
    response = client.get("/opportunities")
    assert response.status_code == 200
    assert "Opportunities" in response.text
    assert "Back to overview" in response.text
    assert 'id="filters"' in response.text
    assert 'name="location"' in response.text
    assert 'name="min_salary"' in response.text
    assert 'name="industry"' in response.text


def test_dashboard_links_to_opportunities_route() -> None:
    response = TestClient(app).get("/")
    assert 'href="/opportunities"' in response.text


def test_candidate_profile_endpoint_is_available() -> None:
    response = TestClient(app).get("/api/candidate")
    assert response.status_code == 200
    assert "candidate_id" in response.json()


def test_application_factory_routes_are_exposed() -> None:
    client = TestClient(app)
    assert client.get("/api/packages").status_code == 200
    response = client.get("/api/opportunities")
    assert response.status_code == 200
    assert "opportunities" in response.json()


def test_resume_upload_persists_candidate_evidence() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/candidate/resume",
        files={
            "resume": (
                "resume.txt",
                "TEST CANDIDATE\\n\\nPROFESSIONAL PROFILE\\nOperations leader",
                "text/plain",
            )
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["resume_id"]
    assert payload["truth_level"] == "INFERRED"


def test_health_is_machine_readable() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
