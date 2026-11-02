"""Security regression tests for the optional bearer-token auth (SABRA_API_TOKEN).

Local single-user mode (no token configured) must stay exactly as open as before;
with a token set, every route except the health check and brand assets requires
a credential, and the browser sign-in flow cannot leak the token into history.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sabra_api.main import app
from sabra_domain.settings import reset_settings_cache

TOKEN = "test-secret-token-0123456789"
OTHER = "a-different-token"


@pytest.fixture()
def token_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SABRA_API_TOKEN", TOKEN)
    reset_settings_cache()


def test_without_a_token_everything_stays_open() -> None:
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/status").status_code == 200
    page = client.get("/")
    assert page.status_code == 200 and "Artemis" in page.text


def test_health_and_brand_assets_stay_public(token_enabled: None) -> None:
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/favicon.svg").status_code == 200
    assert client.get("/brand/logo/favicon.svg").status_code == 200


def test_api_routes_require_a_token(token_enabled: None) -> None:
    client = TestClient(app)
    missing = client.get("/api/status")
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert "Authentication required" in missing.json()["detail"]
    wrong = client.get("/api/status", headers={"Authorization": f"Bearer {OTHER}"})
    assert wrong.status_code == 401
    ok = client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200


def test_pages_require_a_token_and_answer_with_html(token_enabled: None) -> None:
    client = TestClient(app)
    missing = client.get("/")
    assert missing.status_code == 401
    assert "text/html" in missing.headers["content-type"]
    assert "token" in missing.text
    ok = client.get("/", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200


def test_a_valid_bearer_sets_an_httponly_cookie(token_enabled: None) -> None:
    client = TestClient(app)
    response = client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "")
    assert "artemis_token=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict".lower() in cookie.lower()
    # The cookie alone now carries the session.
    follow = client.get("/")
    assert follow.status_code == 200


def test_the_url_token_is_exchanged_for_a_cookie_and_dropped(token_enabled: None) -> None:
    client = TestClient(app, follow_redirects=False)
    response = client.get("/", params={"token": TOKEN})
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "artemis_token=" in response.headers.get("set-cookie", "")
    # A bad URL token gets the 401 page, never a cookie.
    bad = client.get("/", params={"token": "nope"})
    assert bad.status_code == 401
    assert "artemis_token=" not in bad.headers.get("set-cookie", "")


def test_mutating_routes_are_covered_too(token_enabled: None) -> None:
    client = TestClient(app)
    assert client.post("/api/today/seen").status_code == 401
    ok = client.post("/api/today/seen", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200
    # The cookie from the sign-in also authorizes mutations.
    assert client.post("/api/today/seen").status_code == 200
