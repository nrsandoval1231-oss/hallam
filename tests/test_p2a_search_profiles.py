"""Search profiles from a file and from the browser; the active profile is never re-seeded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sabra_api.main import app
from sabra_domain import models
from sabra_domain.errors import ConfigurationError
from sabra_domain.schemas import SearchProfile
from sabra_engine.candidate_intake import (
    SEED_SEARCH_PROFILE_ID,
    ensure_candidate,
    ensure_search_profile,
    load_search_profile,
)
from sabra_engine.cli import app as cli_app
from sabra_engine.context import EngineContext
from test_p2a_support import customer_success_profile, seed_candidate
from typer.testing import CliRunner

EXAMPLE = Path(__file__).parents[1] / "config" / "search_profile.example.yaml"
# The example has no hard-coded id: one is derived from its name.
EXAMPLE_ID = load_search_profile(EXAMPLE).id


def _ctx() -> EngineContext:
    return EngineContext.create()


def _events(event_type: str) -> list[models.EventRow]:
    ctx = _ctx()
    try:
        with ctx.read_repos() as repos:
            return [e for e in repos.events.recent(500) if e.event_type == event_type]
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# the loader and the documented example
# ---------------------------------------------------------------------------


def test_the_example_profile_loads_and_is_generic_admin_with_a_70000_target() -> None:
    profile = load_search_profile(EXAMPLE)
    assert profile.compensation.target == 70000
    assert "Administrative Assistant" in profile.target_titles
    assert "Dallas" in profile.locations.cities
    # Not the medical-aesthetics seed.
    text = EXAMPLE.read_text(encoding="utf-8").lower()
    assert "aesthetic" not in text and "skincare" not in text


def test_the_example_profile_documents_every_schema_field() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    for name in SearchProfile.model_fields:
        assert f"{name}:" in text, f"{name} is not documented in the example"
    from sabra_domain.schemas import CompensationPreference, LocationIntent

    for name in list(LocationIntent.model_fields) + list(CompensationPreference.model_fields):
        assert f"{name}:" in text, f"{name} is not documented in the example"


def test_the_loader_reads_json_too_and_fills_id_and_candidate(tmp_path: Path) -> None:
    path = tmp_path / "mine.json"
    path.write_text(json.dumps({"name": "My search", "target_titles": ["Clerk"]}), encoding="utf-8")
    profile = load_search_profile(path)
    assert profile.id == "srch_my_search"
    assert profile.candidate_id == ""
    assert profile.target_titles == ["Clerk"]


def test_a_blank_yaml_list_means_the_default_but_null_age_means_no_limit(tmp_path: Path) -> None:
    path = tmp_path / "blank.yaml"
    path.write_text("name: Blank\nexcluded_titles:\nmax_age_days:\n", encoding="utf-8")
    profile = load_search_profile(path)
    assert profile.excluded_titles == []
    assert profile.max_age_days is None


def test_the_loader_names_the_bad_field(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump({"name": "Bad", "compensation": {"minimum": "lots"}, "nonsense": 1}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as raised:
        load_search_profile(path)
    assert "compensation.minimum" in str(raised.value)
    assert "nonsense" in str(raised.value)
    with pytest.raises(ConfigurationError):
        load_search_profile(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# the active profile
# ---------------------------------------------------------------------------


def test_a_stored_profile_is_never_overwritten_or_reseeded() -> None:
    ctx = _ctx()
    try:
        candidate_id = ensure_candidate(ctx)
        first = ensure_search_profile(ctx, candidate_id)
        assert first == SEED_SEARCH_PROFILE_ID
        with ctx.repos() as repos:
            edited = repos.search_profiles.get(first)
            edited.name = "Edited by a person"
            edited.target_titles = ["Clerk"]
            repos.search_profiles.upsert(edited)
        again = ensure_search_profile(ctx, candidate_id)
        assert again == first
        with ctx.read_repos() as repos:
            stored = repos.search_profiles.get(first)
            assert stored.name == "Edited by a person"
            assert stored.target_titles == ["Clerk"]
            assert len(repos.search_profiles.list_for_candidate(candidate_id)) == 1
    finally:
        ctx.close()


def test_the_active_profile_is_returned_instead_of_the_seed() -> None:
    candidate_id, profile_id = seed_candidate()
    assert profile_id == "srch_test_customer_success"
    ctx = _ctx()
    try:
        assert ensure_search_profile(ctx, candidate_id) == profile_id
        with ctx.read_repos() as repos:
            ids = [r.id for r in repos.search_profiles.list_for_candidate(candidate_id)]
        assert SEED_SEARCH_PROFILE_ID not in ids
    finally:
        ctx.close()


def test_activate_keeps_exactly_one_profile_active_and_the_payload_agrees() -> None:
    candidate_id, first = seed_candidate()
    ctx = _ctx()
    try:
        other = customer_success_profile()
        other.id = "srch_other"
        other.name = "Other"
        assert ensure_search_profile(ctx, candidate_id, profile=other) == "srch_other"
        with ctx.read_repos() as repos:
            assert repos.search_profiles.active_one(candidate_id).id == first
        with ctx.repos() as repos:
            repos.search_profiles.activate(candidate_id, "srch_other")
        with ctx.read_repos() as repos:
            assert repos.search_profiles.active_one(candidate_id).id == "srch_other"
            rows = repos.search_profiles.list_for_candidate(candidate_id)
            assert [r.is_active for r in rows].count(True) == 1
            for row in rows:
                assert row.payload["is_active"] == row.is_active
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_init_with_a_search_profile_activates_it_and_keeps_it_on_rerun(tmp_path: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(cli_app, ["init", "--search-profile", str(EXAMPLE), "--json"])
    assert first.exit_code == 0, first.output
    payload = json.loads(first.output)
    assert payload["search_profile_id"] == EXAMPLE_ID
    again = runner.invoke(cli_app, ["init", "--json"])
    assert json.loads(again.output)["search_profile_id"] == EXAMPLE_ID
    listed = json.loads(runner.invoke(cli_app, ["searches", "list", "--json"]).output)
    assert [r["id"] for r in listed] == [EXAMPLE_ID]
    assert listed[0]["active"] is True


def test_searches_load_show_list_activate(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(cli_app, ["init", "--json"])  # seeds the built-in profile
    loaded = runner.invoke(cli_app, ["searches", "load", str(EXAMPLE), "--json"])
    assert loaded.exit_code == 0, loaded.output
    assert json.loads(loaded.output)["active"] is True

    listed = json.loads(runner.invoke(cli_app, ["searches", "list", "--json"]).output)
    assert {r["id"] for r in listed} == {SEED_SEARCH_PROFILE_ID, EXAMPLE_ID}
    assert [r["id"] for r in listed if r["active"]] == [EXAMPLE_ID]

    shown = json.loads(runner.invoke(cli_app, ["searches", "show", "--json"]).output)
    assert shown["id"] == EXAMPLE_ID
    other = json.loads(
        runner.invoke(cli_app, ["searches", "show", SEED_SEARCH_PROFILE_ID, "--json"]).output
    )
    assert other["id"] == SEED_SEARCH_PROFILE_ID

    activated = runner.invoke(cli_app, ["searches", "activate", SEED_SEARCH_PROFILE_ID, "--json"])
    assert activated.exit_code == 0, activated.output
    listed = json.loads(runner.invoke(cli_app, ["searches", "list", "--json"]).output)
    assert [r["id"] for r in listed if r["active"]] == [SEED_SEARCH_PROFILE_ID]
    assert len(_events("SEARCH_PROFILE_ACTIVATED")) >= 2


def test_loading_a_different_search_with_the_same_id_needs_replace(tmp_path: Path) -> None:
    runner = CliRunner()
    path = tmp_path / "s.yaml"
    path.write_text("name: Same\ntarget_titles: [Clerk]\n", encoding="utf-8")
    assert runner.invoke(cli_app, ["searches", "load", str(path)]).exit_code == 0
    # The very same file again is harmless.
    assert runner.invoke(cli_app, ["searches", "load", str(path)]).exit_code == 0

    # An edited file with the same derived id would silently replace the stored search.
    path.write_text("name: Same\ntarget_titles: [Clerk, Teller]\n", encoding="utf-8")
    refused = runner.invoke(cli_app, ["searches", "load", str(path)])
    assert refused.exit_code != 0
    assert "--replace" in str(refused.exception)
    listed = json.loads(runner.invoke(cli_app, ["searches", "list", "--json"]).output)
    assert listed[0]["target_titles"] == ["Clerk"], "a refused load must change nothing"

    replaced = runner.invoke(cli_app, ["searches", "load", str(path), "--replace"])
    assert replaced.exit_code == 0, replaced.output
    assert "Replaced existing search srch_same (was 'Same')" in replaced.output
    listed = json.loads(runner.invoke(cli_app, ["searches", "list", "--json"]).output)
    assert len(listed) == 1
    assert listed[0]["target_titles"] == ["Clerk", "Teller"]
    assert _events("SEARCH_PROFILE_UPDATED")


def test_a_different_name_under_the_same_id_is_refused_too(tmp_path: Path) -> None:
    runner = CliRunner()
    first = tmp_path / "a.yaml"
    first.write_text("id: srch_shared\nname: Original\n", encoding="utf-8")
    runner.invoke(cli_app, ["searches", "load", str(first)])
    second = tmp_path / "b.yaml"
    second.write_text("id: srch_shared\nname: Somebody else's search\n", encoding="utf-8")
    refused = runner.invoke(cli_app, ["searches", "load", str(second)])
    assert refused.exit_code != 0 and "Original" in str(refused.exception)
    via_init = runner.invoke(cli_app, ["init", "--search-profile", str(second), "--json"])
    assert via_init.exit_code != 0
    ok = runner.invoke(cli_app, ["init", "--search-profile", str(second), "--replace", "--json"])
    assert ok.exit_code == 0, ok.output
    assert "was 'Original'" in json.loads(ok.output)["replaced"]


def test_json_profiles_may_start_with_a_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"name": "Bom search"}).encode())
    assert load_search_profile(path).name == "Bom search"


def test_the_example_file_carries_no_hard_coded_id() -> None:
    assert yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")).get("id") is None


def test_discover_and_rank_use_the_active_profile_unless_told_otherwise(monkeypatch) -> None:
    from sabra_engine import discovery as discovery_module
    from sabra_engine import intelligence as intelligence_module

    candidate_id, active_id = seed_candidate()
    seen: dict[str, str] = {}

    def fake_discover(self, **kwargs):
        seen["discover"] = kwargs["search_profile_id"]
        raise RuntimeError("stop here; the profile id is what is under test")

    def fake_rank(self, **kwargs):
        seen["rank"] = kwargs["search_profile_id"]
        raise RuntimeError("stop here; the profile id is what is under test")

    monkeypatch.setattr(discovery_module.DiscoveryService, "run", fake_discover)
    monkeypatch.setattr(intelligence_module.IntelligenceService, "run", fake_rank)
    runner = CliRunner()
    runner.invoke(cli_app, ["discover"])
    runner.invoke(cli_app, ["rank"])
    assert seen == {"discover": active_id, "rank": active_id}
    runner.invoke(cli_app, ["discover", "--search-profile-id", "srch_explicit"])
    runner.invoke(cli_app, ["rank", "--search-profile-id", "srch_explicit"])
    assert seen == {"discover": "srch_explicit", "rank": "srch_explicit"}


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------


def test_searches_api_lists_and_shows_profiles() -> None:
    candidate_id, active_id = seed_candidate()
    client = TestClient(app)
    listed = client.get("/api/searches").json()
    assert listed["active_id"] == active_id
    assert [s["id"] for s in listed["searches"]] == [active_id]
    detail = client.get(f"/api/searches/{active_id}").json()
    assert detail["active"] is True
    assert detail["profile"]["name"] == "Customer success - remote"
    assert client.get("/api/searches/srch_nope").status_code == 404


def test_put_replaces_validates_and_records_an_event() -> None:
    _, profile_id = seed_candidate()
    client = TestClient(app)
    body = client.get(f"/api/searches/{profile_id}").json()["profile"]
    body["name"] = "Renamed"
    body["target_titles"] = ["Clerk", "Teller"]
    body["is_active"] = False  # not the caller's to change here
    body["candidate_id"] = "cand_someone_else"
    response = client.put(f"/api/searches/{profile_id}", json=body)
    assert response.status_code == 200, response.text
    assert "name" in response.json()["changed_fields"]
    stored = client.get(f"/api/searches/{profile_id}").json()
    assert stored["profile"]["name"] == "Renamed"
    assert stored["profile"]["target_titles"] == ["Clerk", "Teller"]
    assert stored["active"] is True
    assert stored["profile"]["candidate_id"] != "cand_someone_else"
    events = _events("SEARCH_PROFILE_UPDATED")
    assert events and events[-1].entity_id == profile_id
    assert events[-1].payload["name"] == "Renamed"


def test_put_is_a_full_replace_so_a_missing_field_returns_to_its_default() -> None:
    _, profile_id = seed_candidate()
    client = TestClient(app)
    response = client.put(f"/api/searches/{profile_id}", json={"name": "Bare"})
    assert response.status_code == 200
    stored = client.get(f"/api/searches/{profile_id}").json()["profile"]
    assert stored["target_titles"] == []
    assert stored["shortlist_target"] == 10


def test_put_with_a_bad_body_is_422_with_field_errors_and_changes_nothing() -> None:
    _, profile_id = seed_candidate()
    client = TestClient(app)
    response = client.put(
        f"/api/searches/{profile_id}",
        json={"name": "X", "compensation": {"minimum": "lots"}, "shortlist_target": "many"},
    )
    assert response.status_code == 422
    fields = {e["field"] for e in response.json()["errors"]}
    assert {"compensation.minimum", "shortlist_target"} <= fields
    assert client.get(f"/api/searches/{profile_id}").json()["profile"]["name"] != "X"
    mismatch = client.put(f"/api/searches/{profile_id}", json={"id": "srch_other", "name": "X"})
    assert mismatch.status_code == 422
    assert client.put("/api/searches/srch_nope", json={"name": "X"}).status_code == 404


def test_post_creates_and_activate_switches() -> None:
    _, first = seed_candidate()
    client = TestClient(app)
    created = client.post("/api/searches", json={"name": "Second", "target_titles": ["Clerk"]})
    assert created.status_code == 201, created.text
    new_id = created.json()["profile"]["id"]
    assert created.json()["active"] is False
    assert client.get("/api/searches").json()["active_id"] == first

    bad = client.post("/api/searches", json={"target_titles": ["Clerk"]})
    assert bad.status_code == 422
    assert any(e["field"] == "name" for e in bad.json()["errors"])
    duplicate = client.post("/api/searches", json={"id": new_id, "name": "Dup"})
    assert duplicate.status_code == 409

    activated = client.post(f"/api/searches/{new_id}/activate")
    assert activated.status_code == 200
    assert client.get("/api/searches").json()["active_id"] == new_id
    assert client.post("/api/searches/srch_nope/activate").status_code == 404
    assert _events("SEARCH_PROFILE_ACTIVATED")


def test_creating_the_first_search_makes_it_active() -> None:
    client = TestClient(app)
    created = client.post("/api/searches", json={"name": "First"})
    assert created.status_code == 201
    assert created.json()["active"] is True
    assert client.get("/api/searches").json()["active_id"] == created.json()["profile"]["id"]
