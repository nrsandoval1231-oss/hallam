"""Shared helpers for the browser-first workflow tests (no tests live here).

Nothing here reaches the network. Discovery reads the committed Greenhouse board capture
through an `httpx.MockTransport`; company-site research gets a canned page or a canned
failure.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
from sabra_domain.schemas import LocationIntent, SearchProfile
from sabra_domain.settings import reset_settings_cache
from sabra_engine.candidate_intake import ensure_candidate, ensure_search_profile
from sabra_engine.context import EngineContext
from sabra_engine.documents import DocumentService
from sabra_providers.http import HttpClient

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY_PAGE = (
    "<html><head><title>Revance</title>"
    '<meta name="description" content="Revance makes aesthetic and therapeutic products '
    'for clinics across the country."></head><body>hello</body></html>'
)

REGISTRY_YAML = (
    "version: 1\nsources:\n"
    "  - id: greenhouse-revance\n"
    "    kind: GREENHOUSE\n"
    "    token: revance\n"
    "    display_name: Revance Therapeutics\n"
    "    industry: Medical aesthetics\n"
    "    domain: revance.com\n"
    "    enabled: true\n"
)


def board_payload() -> dict:
    return json.loads((FIXTURES / "greenhouse_revance_board.json").read_text(encoding="utf-8"))


def mock_http(
    *,
    board: dict | None = None,
    site: Callable[[httpx.Request], httpx.Response] | None = None,
    requests: list[str] | None = None,
) -> HttpClient:
    """An HttpClient over canned responses.

    The Greenhouse board URL returns the committed capture (or a 500 when `board` is the
    string "fail"); any other URL goes to `site`, which defaults to a small company page.
    """

    payload = board if board is not None else board_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(str(request.url))
        if "boards-api.greenhouse.io" in str(request.url):
            if payload == "fail":
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json=payload)
        if site is not None:
            return site(request)
        return httpx.Response(200, text=COMPANY_PAGE)

    return HttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        max_attempts=1,
        backoff_seconds=0,
    )


def use_registry(tmp_path: Path, monkeypatch, *, yaml_text: str = REGISTRY_YAML) -> Path:
    """Point the default settings at a one-source registry (the real one lists live boards)."""

    path = tmp_path / "registry.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    monkeypatch.setenv("SABRA_SOURCE_REGISTRY_PATH", str(path))
    reset_settings_cache()
    return path


def customer_success_profile(candidate_id: str = "") -> SearchProfile:
    """A search the committed Revance capture can satisfy: one remote specialist role."""

    return SearchProfile(
        id="srch_test_customer_success",
        name="Customer success - remote",
        candidate_id=candidate_id,
        target_titles=["Customer Success Specialist"],
        role_family_terms=["customer", "success"],
        role_level_terms=["specialist"],
        locations=LocationIntent(states=["TX"], allow_remote=True, allow_onsite=False),
        shortlist_target=5,
        prepare_target=2,
    )


def seed_candidate(
    resume_text: str | None = None, *, with_test_profile: bool = True
) -> tuple[str, str]:
    """A candidate with an active search profile (and optionally a master resume).

    By default the profile is `customer_success_profile`, so the Revance capture yields a
    shortlisted role. `with_test_profile=False` leaves the built-in seed profile.
    """

    ctx = EngineContext.create()
    try:
        candidate_id = ensure_candidate(ctx)
        if with_test_profile:
            profile_id = ensure_search_profile(
                ctx, candidate_id, profile=customer_success_profile(), activate=True
            )
        else:
            profile_id = ensure_search_profile(ctx, candidate_id)
        if resume_text:
            DocumentService(ctx).ingest_master_resume(candidate_id, resume_text)
    finally:
        ctx.close()
    return candidate_id, profile_id
