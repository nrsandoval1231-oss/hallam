"""Discovery at the scale of a hundred boards: caps, time budgets, isolation, `--only-new`.

HTTP is a canned transport or `ScriptedHttp`. Time budgets are tested with a response that
trickles in, so the assertions are about elapsed wall-clock time (generously bounded) and
never about the network.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from p3_support import FIXTURES, ScriptedHttp, ashby_board
from sabra_api.main import app
from sabra_domain.enums import DiscoveryRunStatus
from sabra_domain.errors import ProviderError
from sabra_domain.settings import reset_settings_cache
from sabra_engine.candidate_intake import ensure_candidate, ensure_search_profile
from sabra_engine.cli import app as cli_app
from sabra_engine.context import EngineContext
from sabra_engine.discovery import DiscoveryService
from sabra_engine.runs import RunService
from sabra_providers.http import HttpClient
from typer.testing import CliRunner

REGISTRY = """\
version: 1
sources:
  - id: greenhouse-revance
    kind: GREENHOUSE
    token: revance
    display_name: Revance Therapeutics
    max_items: 2
  - id: ashby-siepe
    kind: ASHBY
    token: siepe
    display_name: Siepe
  - id: lever-elfbeauty
    kind: LEVER
    token: elfbeauty
    display_name: e.l.f. Beauty
"""


def greenhouse_board() -> dict:
    return json.loads((FIXTURES / "greenhouse_revance_board.json").read_text(encoding="utf-8"))


def lever_board() -> list:
    return json.loads((FIXTURES / "lever_elfbeauty_board.json").read_text(encoding="utf-8"))


def healthy_http(**overrides) -> ScriptedHttp:
    rules = {
        "boards/revance/jobs": greenhouse_board(),
        "job-board/siepe": ashby_board(),
        "postings/elfbeauty": lever_board(),
    }
    rules.update(overrides)
    return ScriptedHttp(rules)


@pytest.fixture()
def world(settings):
    settings.source_registry_path.write_text(REGISTRY, encoding="utf-8")
    ctx = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=str(settings.source_registry_path),
    )
    try:
        candidate_id = ensure_candidate(ctx, display_name="Tester", create_placeholder=True)
        profile_id = ensure_search_profile(ctx, candidate_id)
        ctx.sync_source_registry()
        yield ctx, candidate_id, profile_id
    finally:
        ctx.close()


def discover(world, http, **kwargs):
    ctx, candidate_id, profile_id = world
    return DiscoveryService(ctx).run(
        candidate_id=candidate_id, search_profile_id=profile_id, http=http, **kwargs
    )


def provider_rows(ctx) -> dict[str, object]:
    with ctx.read_repos() as repos:
        return {row.token: row for row in repos.providers.list_all()}


# ---------------------------------------------------------------------------
# per-source max_items
# ---------------------------------------------------------------------------


def test_a_registry_max_items_caps_that_source_for_every_provider_kind(world, settings) -> None:
    result = discover(world, healthy_http())
    by_id = {o.source_id: o for o in result.sources}
    capped = by_id["greenhouse-revance"]
    assert capped.ok and capped.jobs_seen == 2 and capped.truncated
    assert capped.total_reported and capped.total_reported > 2
    assert by_id["ashby-siepe"].jobs_seen == 10 and not by_id["ashby-siepe"].truncated

    # A cap on each kind of entry, each honoured by its provider.
    text = (
        "version: 1\nsources:\n"
        "  - {id: g, kind: GREENHOUSE, token: revance, display_name: G, max_items: 3}\n"
        "  - {id: l, kind: LEVER, token: elfbeauty, display_name: L, max_items: 4}\n"
        "  - {id: a, kind: ASHBY, token: siepe, display_name: A, max_items: 5}\n"
    )
    settings.source_registry_path.write_text(text, encoding="utf-8")
    ctx = world[0]
    ctx.reload_registry()
    outcomes = {o.source_id: o for o in discover(world, healthy_http()).sources}
    assert {k: v.jobs_seen for k, v in outcomes.items()} == {"g": 3, "l": 4, "a": 5}
    assert all(o.truncated for o in outcomes.values())


def test_the_runs_cap_and_the_entrys_cap_the_tighter_wins(world) -> None:
    by_id = {o.source_id: o for o in discover(world, healthy_http(), max_items_per_source=1).sources}
    assert {o.jobs_seen for o in by_id.values()} == {1}
    by_id = {o.source_id: o for o in discover(world, healthy_http(), max_items_per_source=50).sources}
    assert by_id["greenhouse-revance"].jobs_seen == 2, "the entry's own cap still applies"
    assert by_id["ashby-siepe"].jobs_seen == 10


# ---------------------------------------------------------------------------
# per-source time budget
# ---------------------------------------------------------------------------


class Trickle(httpx.SyncByteStream):
    """A response body that keeps arriving, slowly: each chunk resets httpx's read timer."""

    def __init__(self, chunks: int, pause: float) -> None:
        self.chunks, self.pause = chunks, pause

    def __iter__(self):
        for _ in range(self.chunks):
            time.sleep(self.pause)
            yield b" "


def trickling_client(chunks: int = 40, pause: float = 0.05, **kwargs) -> HttpClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Trickle(chunks, pause))

    return HttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=kwargs.pop("max_attempts", 3),
        backoff_seconds=kwargs.pop("backoff_seconds", 0.0),
    )


def test_a_response_that_trickles_in_is_cut_off_at_the_deadline() -> None:
    client = trickling_client()  # would take 2 s to arrive
    client.deadline = time.monotonic() + 0.3
    started = time.monotonic()
    with pytest.raises(ProviderError):
        client.get("https://example.test/board")
    assert time.monotonic() - started < 1.2
    assert client.calls and client.calls[-1].status == 0, "the failed read is recorded"


def test_retries_and_waits_stay_inside_the_budget() -> None:
    attempts: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(time.monotonic())
        return httpx.Response(503)

    client = HttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=5,
        backoff_seconds=30.0,
    )
    client.deadline = time.monotonic() + 0.4
    started = time.monotonic()
    with pytest.raises(ProviderError, match="time budget"):
        client.get("https://example.test/board")
    assert time.monotonic() - started < 2.0, "it did not sleep through a 30 s backoff"
    assert len(attempts) == 1


def test_an_expired_deadline_makes_no_request_at_all() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, json={})

    client = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    client.deadline = time.monotonic() - 1
    with pytest.raises(ProviderError, match="time budget"):
        client.get("https://example.test/board")
    assert attempts == []


def test_without_a_deadline_the_client_behaves_as_before() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})

    client = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.deadline is None
    result = client.get("https://example.test/board")
    assert result.status == 200 and result.json() == {"jobs": []}


def test_the_budget_defaults_to_twice_the_request_timeout(settings) -> None:
    assert settings.discovery_source_budget_seconds == 0
    assert settings.source_budget_seconds == settings.discovery_timeout_seconds * 2
    tuned = settings.model_copy(update={"discovery_source_budget_seconds": 7.5})
    assert tuned.source_budget_seconds == 7.5


def test_one_slow_board_cannot_stall_the_run_and_does_not_fail_it(world) -> None:
    ctx = world[0]
    ctx.settings.discovery_source_budget_seconds = 0.4

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "job-board/siepe" in url:
            return httpx.Response(200, stream=Trickle(60, 0.05))  # 3 s if left alone
        if "boards/revance" in url:
            return httpx.Response(200, json=greenhouse_board())
        return httpx.Response(200, json=lever_board())

    client = HttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=3,
        backoff_seconds=0.0,
    )
    started = time.monotonic()
    result = discover(world, client)
    assert time.monotonic() - started < 2.5, "the run did not wait for the slow board"
    by_id = {o.source_id: o for o in result.sources}
    assert not by_id["ashby-siepe"].ok
    assert by_id["greenhouse-revance"].ok and by_id["lever-elfbeauty"].ok
    assert result.status is DiscoveryRunStatus.PARTIAL
    assert client.deadline is None, "the budget is cleared between sources"

    row = provider_rows(ctx)["siepe"]
    assert row.health == "DEGRADED" and row.last_error
    assert provider_rows(ctx)["revance"].health == "HEALTHY"


def test_each_source_gets_its_own_budget(world) -> None:
    ctx = world[0]
    ctx.settings.discovery_source_budget_seconds = 0.9

    def handler(request: httpx.Request) -> httpx.Response:
        # Every board takes about 0.35 s: slower than a third of the run's total, but each
        # is inside its own budget.
        time.sleep(0.35)
        url = str(request.url)
        if "job-board/siepe" in url:
            return httpx.Response(200, json=ashby_board())
        if "boards/revance" in url:
            return httpx.Response(200, json=greenhouse_board())
        return httpx.Response(200, json=lever_board())

    client = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = discover(world, client)
    assert result.sources_ok == 3 and result.status is DiscoveryRunStatus.COMPLETED


# ---------------------------------------------------------------------------
# a failed board is recorded and does not fail the run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        500,
        404,
        "<html>not a board</html>",
        {"unexpected": "shape"},
        ProviderError("http", "GET x failed: ReadTimeout: slow", retryable=True),
        RuntimeError("boom"),
    ],
)
def test_a_failing_ashby_board_is_recorded_on_its_row_and_the_run_continues(
    world, failure
) -> None:
    ctx = world[0]
    result = discover(world, healthy_http(**{"job-board/siepe": failure}))
    by_id = {o.source_id: o for o in result.sources}
    assert not by_id["ashby-siepe"].ok and by_id["ashby-siepe"].error
    assert by_id["greenhouse-revance"].ok and by_id["lever-elfbeauty"].ok
    assert result.status is DiscoveryRunStatus.PARTIAL
    assert result.jobs_new > 0, "the healthy boards were still ingested"

    row = provider_rows(ctx)["siepe"]
    assert row.health == "DEGRADED" and row.consecutive_failures == 1 and row.last_error
    assert row.last_success_at is None
    # Failures accumulate to FAILING, and one good read heals the row.
    discover(world, healthy_http(**{"job-board/siepe": failure}))
    discover(world, healthy_http(**{"job-board/siepe": failure}))
    assert provider_rows(ctx)["siepe"].health == "FAILING"
    discover(world, healthy_http())
    healed = provider_rows(ctx)["siepe"]
    assert healed.health == "HEALTHY" and healed.last_error is None and healed.last_success_at


def test_a_failed_board_does_not_touch_what_was_already_stored(world) -> None:
    from sabra_domain import models

    ctx = world[0]
    discover(world, healthy_http())
    with ctx.read_repos() as repos:
        before = sorted(j.id for j in repos.session.query(models.Job).all())
    assert before
    discover(world, healthy_http(**{"job-board/siepe": 500}))
    with ctx.read_repos() as repos:
        assert sorted(j.id for j in repos.session.query(models.Job).all()) == before


# ---------------------------------------------------------------------------
# --only-new
# ---------------------------------------------------------------------------


def age(ctx, token: str, hours: float) -> None:
    with ctx.repos() as repos:
        row = next(r for r in repos.providers.list_all() if r.token == token)
        row.last_success_at = datetime.now(UTC) - timedelta(hours=hours)


def test_only_new_skips_boards_read_recently(world) -> None:
    ctx = world[0]
    first = discover(world, healthy_http())
    assert first.sources_ok == 3 and first.summary()["sources_skipped_fresh"] == 0

    http = healthy_http()
    second = discover(world, http, only_new_hours=20)
    assert http.requests == [], "no board was read"
    assert second.sources == [] and sorted(second.sources_skipped_fresh) == sorted(
        ["greenhouse-revance", "ashby-siepe", "lever-elfbeauty"]
    )
    assert second.status is DiscoveryRunStatus.COMPLETED, "nothing due is a finished run"
    assert second.summary()["sources_skipped_fresh"] == 3 and second.summary()["sources_total"] == 0

    # One board goes stale and is read; the other two are still skipped.
    age(ctx, "siepe", 25)
    http = healthy_http()
    third = discover(world, http, only_new_hours=20)
    assert [o.source_id for o in third.sources] == ["ashby-siepe"]
    assert len(http.requests) == 1 and "job-board/siepe" in http.requests[0][0]
    assert len(third.sources_skipped_fresh) == 2

    # Without the option every board is read, as before.
    full = discover(world, healthy_http())
    assert full.sources_ok == 3 and full.sources_skipped_fresh == []


def test_only_new_retries_a_board_that_never_succeeded_or_last_failed(world) -> None:
    ctx = world[0]
    discover(world, healthy_http(**{"job-board/siepe": 500}))
    assert provider_rows(ctx)["siepe"].last_success_at is None
    http = healthy_http()
    again = discover(world, http, only_new_hours=20)
    assert [o.source_id for o in again.sources] == ["ashby-siepe"], "a failed board is retried"
    assert sorted(again.sources_skipped_fresh) == ["greenhouse-revance", "lever-elfbeauty"]


def test_only_new_still_runs_the_group_step_only_when_something_was_read(world) -> None:
    stages: list[str] = []
    discover(world, healthy_http(), progress=lambda p: stages.append(p.get("stage", "")))
    assert "group" in stages
    stages.clear()
    discover(world, healthy_http(), only_new_hours=20, progress=lambda p: stages.append(p.get("stage", "")))
    assert "group" not in stages and "sources" in stages


def test_a_skipped_run_is_recorded_as_a_finished_run(world) -> None:
    from sabra_domain import models

    ctx = world[0]
    discover(world, healthy_http())
    skipped = discover(world, healthy_http(), only_new_hours=20)
    with ctx.read_repos() as repos:
        row = repos.session.get(models.DiscoveryRunRow, skipped.run_id)
        assert row.status == "COMPLETED" and row.sources_total == 0 and row.finished_at


# ---------------------------------------------------------------------------
# the command line, `sabra status` and the run service
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_world(tmp_path: Path, monkeypatch):
    path = tmp_path / "cli-sources.yaml"
    path.write_text(REGISTRY, encoding="utf-8")
    monkeypatch.setenv("SABRA_SOURCE_REGISTRY_PATH", str(path))
    reset_settings_cache()
    http = healthy_http()
    monkeypatch.setattr("sabra_engine.discovery.HttpClient", lambda **_kw: http)
    monkeypatch.setattr("sabra_engine.runs.build_discovery_http", lambda _ctx: http)
    ctx = EngineContext.create()
    try:
        ensure_search_profile(ctx, ensure_candidate(ctx, create_placeholder=True))
        ctx.sync_source_registry()
    finally:
        ctx.close()
    yield http
    reset_settings_cache()


def test_cli_discover_only_new(cli_world) -> None:
    http = cli_world
    runner = CliRunner()
    first = runner.invoke(cli_app, ["discover", "--json"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["sources_ok"] == 3
    requests_after_first = len(http.requests)

    second = runner.invoke(cli_app, ["discover", "--only-new", "--json"])
    assert second.exit_code == 0, second.output
    summary = json.loads(second.output)
    assert summary["sources_skipped_fresh"] == 3 and summary["sources_total"] == 0
    assert summary["status"] == "COMPLETED"
    assert len(http.requests) == requests_after_first

    # A window of a tiny fraction of an hour makes everything stale again.
    third = runner.invoke(cli_app, ["discover", "--only-new", "--only-new-hours", "0.000001", "--json"])
    assert json.loads(third.output)["sources_skipped_fresh"] == 0
    assert len(http.requests) > requests_after_first


def test_cli_only_new_hours_needs_the_flag_and_a_positive_number(cli_world) -> None:
    runner = CliRunner()
    assert runner.invoke(cli_app, ["discover", "--only-new-hours", "5"]).exit_code != 0
    assert runner.invoke(cli_app, ["discover", "--only-new", "--only-new-hours", "0"]).exit_code != 0


def test_cli_run_only_new(cli_world) -> None:
    runner = CliRunner()
    assert runner.invoke(cli_app, ["run", "--kind", "discover", "--json"]).exit_code == 0
    again = runner.invoke(cli_app, ["run", "--kind", "discover", "--only-new", "--json"])
    assert again.exit_code == 0, again.output
    record = json.loads(again.output)
    assert record["status"] == "COMPLETED"
    stage = record["progress"]["stages"]["discover"]
    assert stage["sources_skipped_fresh"] == 3 and stage["sources_total"] == 0
    assert any("were read within the last 20 hours" in w for w in record["progress"]["warnings"])


def test_the_dashboard_run_accepts_only_new(cli_world) -> None:
    client = TestClient(app)
    first = client.post("/api/runs", json={"kind": "discover"})
    assert first.status_code == 202, first.text
    assert RunService.wait(first.json()["id"], 30)
    second = client.post("/api/runs", json={"kind": "discover", "only_new": True})
    assert second.status_code == 202, second.text
    assert RunService.wait(second.json()["id"], 30)
    record = client.get(f"/api/runs/{second.json()['id']}").json()
    assert record["status"] == "COMPLETED"
    assert record["progress"]["stages"]["discover"]["sources_skipped_fresh"] == 3


def test_status_shows_each_sources_last_success(cli_world) -> None:
    runner = CliRunner()
    before = json.loads(runner.invoke(cli_app, ["status", "--json"]).output)
    assert {s["token"]: s["last_success_at"] for s in before["sources"]} == {
        "revance": None,
        "siepe": None,
        "elfbeauty": None,
    }
    runner.invoke(cli_app, ["discover", "--json"])
    after = json.loads(runner.invoke(cli_app, ["status", "--json"]).output)
    assert all(s["last_success_at"] for s in after["sources"])
    assert all(s["last_attempt_at"] for s in after["sources"])
    assert {s["health"] for s in after["sources"]} == {"HEALTHY"}


def test_a_run_with_some_failed_boards_warns_on_the_record_and_on_today(cli_world) -> None:
    http = cli_world
    http.rules["job-board/siepe"] = 500
    runner = CliRunner()
    result = runner.invoke(cli_app, ["run", "--kind", "discover", "--json"])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["status"] == "COMPLETED"
    warnings = record["progress"]["warnings"]
    assert "1 of 3 boards failed: ashby-siepe" in warnings

    today = TestClient(app).get("/api/today").json()
    assert "1 of 3 boards failed: ashby-siepe" in today["last_run"]["progress"]["warnings"]


def test_no_board_warning_when_all_or_none_failed(cli_world) -> None:
    http = cli_world
    runner = CliRunner()
    clean = json.loads(runner.invoke(cli_app, ["run", "--kind", "discover", "--json"]).output)
    assert not any("boards failed" in w for w in clean["progress"]["warnings"])
    for key in list(http.rules):
        http.rules[key] = 500
    broken = json.loads(runner.invoke(cli_app, ["run", "--kind", "discover", "--json"]).output)
    assert broken["status"] == "FAILED"
    assert not any("boards failed" in w for w in broken["progress"]["warnings"])


def test_the_board_warning_names_ten_and_counts_the_rest(world) -> None:
    ctx = world[0]
    rows = "".join(
        f"  - {{id: g{i}, kind: GREENHOUSE, token: t{i}, display_name: G{i}}}\n" for i in range(14)
    )
    ctx.settings.source_registry_path.write_text(
        REGISTRY + rows, encoding="utf-8"
    )
    ctx.reload_registry()
    ctx.sync_source_registry()
    http = healthy_http()
    for i in range(14):
        http.rules[f"boards/t{i}/jobs"] = 500
    service = RunService(ctx, discovery_http=http)
    record = service.run_sync("discover")
    warning = next(w for w in record["progress"]["warnings"] if "boards failed" in w)
    assert warning.startswith("14 of 17 boards failed: ") and warning.endswith("and 4 more")
