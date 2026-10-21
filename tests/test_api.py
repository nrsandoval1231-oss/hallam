import pytest
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


def test_resume_upload_persists_candidate_evidence(isolate_database) -> None:
    from pathlib import Path

    from sabra_domain.db import Database
    from sabra_domain.models import Resume
    from sabra_domain.settings import get_settings

    real_database = Path("var/sabra.db")
    before = real_database.stat().st_mtime_ns if real_database.exists() else None
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

    settings = get_settings()
    assert str(isolate_database) in settings.database_url
    assert "var/sabra.db" not in settings.database_url.replace("\\", "/")
    database = Database(settings.database_url)
    try:
        with database.read_session() as session:
            stored = session.get(Resume, payload["resume_id"])
            assert stored is not None
            assert "Operations leader" in stored.content_text
    finally:
        database.dispose()
    after = real_database.stat().st_mtime_ns if real_database.exists() else None
    assert before == after


def test_prepare_of_an_approved_application_is_json_and_does_not_reprepare(prepared, monkeypatch) -> None:
    from sabra_engine.approval import ApprovalService

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    ApprovalService(prepared.ctx).approve(
        prepared.package_id, approved_by="test", acknowledge_unresolved=True
    )
    with prepared.ctx.read_repos() as repos:
        before = len(repos.packages.list_all(prepared.candidate_id))
    response = TestClient(app).post(f"/api/opportunities/{prepared.opportunity_id}/prepare")
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert "already approved" in body["detail"].lower()
    with prepared.ctx.read_repos() as repos:
        assert len(repos.packages.list_all(prepared.candidate_id)) == before


def test_package_list_keeps_only_the_latest_revision(prepared, monkeypatch) -> None:
    from sabra_engine.documents import DocumentService

    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    DocumentService(prepared.ctx).prepare_package(prepared.opportunity_id)
    payload = TestClient(app).get("/api/packages").json()
    rows = payload["packages"]
    assert len(rows) == 1
    assert rows[0]["revision"] == 2
    assert rows[0]["package_id"] != prepared.package_id
    page = TestClient(app).get(f"/packages/{rows[0]['package_id']}")
    assert page.status_code == 200
    assert "Cover letter" in page.text
    assert "Hiring manager note" in page.text
    detail = TestClient(app).get(f"/api/packages/{rows[0]['package_id']}")
    assert detail.status_code == 200
    assert detail.json()["content_hash"]


def test_apply_is_refused_while_dry_run_is_on(prepared, monkeypatch) -> None:
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    response = TestClient(app).post(
        f"/api/applications/{prepared.application_id}/apply",
        json={"confirm": True},
    )
    assert response.status_code == 409
    assert "dry run is on" in response.json()["detail"].lower()
    assert "nothing was opened" in response.json()["detail"].lower()


def test_answer_keys_include_adapter_fields() -> None:
    payload = TestClient(app).get("/api/answer-keys")
    assert payload.status_code == 200
    keys = set(payload.json()["keys"])
    assert {
        "us_citizen",
        "spanish",
        "resume_headline",
        "phone",
        "country",
        "city",
        "state_residence",
        "postal_code",
        "street_address",
        "age_eligibility",
        "non_compete",
    } <= keys


def test_private_dir_database_is_under_var(tmp_path, monkeypatch) -> None:
    from sabra_domain.settings import Settings

    for name in (
        "SABRA_DATABASE_URL",
        "SABRA_DATA_DIR",
        "SABRA_ARTIFACTS_DIR",
        "SABRA_EVIDENCE_DIR",
        "SABRA_AUTOMATION_SCREENSHOT_DIR",
        "SABRA_CANDIDATE_DIR",
        "SABRA_PRIVATE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    private = tmp_path / "hallam-private"
    settings = Settings(private_dir=private)
    assert settings.database_url.endswith(f"{private.as_posix()}/var/sabra.db")
    override = Settings(
        private_dir=private,
        database_url="sqlite+pysqlite:///C:/Users/nrsan/hallam-private/custom.db",
    )
    assert override.database_url.endswith("custom.db")


def test_missing_candidate_names_the_database(isolate_database, monkeypatch, capsys) -> None:
    import sys

    from sabra_engine.cli import main

    monkeypatch.setattr(sys, "argv", ["sabra", "answers", "list"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "No candidate is stored" in captured.out
    assert str(isolate_database) in captured.out
    assert "SABRA_DATABASE_URL" in captured.out
    assert "var/sabra.db" in captured.out


def test_health_is_machine_readable() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
