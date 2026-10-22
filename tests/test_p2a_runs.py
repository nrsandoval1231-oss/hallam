"""Pipeline runs: discover, rank, research the shortlist, optionally prepare."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sabra_api.main import app
from sabra_domain import models
from sabra_domain.errors import ValidationFailure
from sabra_engine.cli import app as cli_app
from sabra_engine.context import EngineContext
from sabra_engine.intelligence import IntelligenceService
from sabra_engine.research import ResearchService
from sabra_engine.runs import STALE_AFTER, RunService
from sqlalchemy import select
from test_p2a_support import mock_http, seed_candidate, use_registry
from typer.testing import CliRunner


@pytest.fixture()
def world(tmp_path, monkeypatch, fixture_resume_text):
    """A one-source registry, a candidate with a resume and a search the capture satisfies."""

    use_registry(tmp_path, monkeypatch)
    candidate_id, profile_id = seed_candidate(fixture_resume_text)
    return candidate_id, profile_id


def _service(ctx, *, discovery=None, research=None, on_update=None) -> RunService:
    return RunService(
        ctx,
        discovery_http=discovery or mock_http(),
        research_http=research or mock_http(),
        on_update=on_update,
    )


def _opportunities() -> list[models.Opportunity]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            return list(repos.session.execute(select(models.Opportunity)).scalars())
    finally:
        ctx.close()


def _event_types(entity_id: str) -> list[str]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            return [
                e.event_type
                for e in reversed(repos.events.recent(1000))
                if e.entity_id == entity_id
            ]
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# the whole loop
# ---------------------------------------------------------------------------


def test_a_full_run_discovers_ranks_researches_and_records_everything(world) -> None:
    ctx = EngineContext.create()
    try:
        record = _service(ctx).run_sync("full", False)
    finally:
        ctx.close()
    assert record["status"] == "COMPLETED", record
    progress = record["progress"]
    stages = progress["stages"]
    assert list(stages) == ["discover", "rank", "research"]
    assert stages["discover"]["jobs_seen"] == 15 and stages["discover"]["sources_ok"] == 1
    assert stages["rank"]["qualified"] == 1 and stages["rank"]["shortlisted"] == 1
    assert stages["research"]["researched"] == 1 and stages["research"]["failed"] == 0
    # The per-source outcome is on the record.
    assert progress["sources"][0]["source_id"] == "greenhouse-revance"
    assert progress["sources"][0]["ok"] is True
    assert "1/1 sources" in progress["summary"]
    assert record["started_at"] and record["finished_at"]

    types = _event_types(record["id"])
    assert types == ["PIPELINE_RUN_STARTED", "PIPELINE_RUN_COMPLETED"]

    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            shortlisted = repos.opportunities.shortlist(record["candidate_id"])
            assert len(shortlisted) == 1
            assert shortlisted[0].status == "RESEARCHED"
            packet = repos.research.for_opportunity(shortlisted[0].id)
            assert packet is not None
            assert "Revance makes aesthetic" in packet.payload["company_description"]
    finally:
        ctx.close()


def test_progress_is_committed_as_the_run_goes(world) -> None:
    snapshots: list[dict] = []
    ctx = EngineContext.create()
    watcher = EngineContext.create()
    try:

        def on_update(progress: dict) -> None:
            # A second connection sees what the run has committed so far.
            with watcher.read_repos() as repos:
                row = repos.pipeline_runs.list_recent(1)[0]
                snapshots.append({"status": row.status, "stages": list(row.progress["stages"])})

        _service(ctx, on_update=on_update).run_sync("full", False)
    finally:
        watcher.close()
        ctx.close()
    assert snapshots[0]["status"] == "RUNNING"
    seen_stage_sets = {tuple(s["stages"]) for s in snapshots}
    assert ("discover",) in seen_stage_sets
    assert ("discover", "rank") in seen_stage_sets
    assert ("discover", "rank", "research") in seen_stage_sets
    assert snapshots[-1]["status"] == "COMPLETED"


def test_kinds_run_only_their_stages(world) -> None:
    ctx = EngineContext.create()
    try:
        discover = _service(ctx).run_sync("discover", True)
        assert list(discover["progress"]["stages"]) == ["discover"]
        assert discover["prepare"] is False  # nothing to prepare after a discover-only run
        rank = _service(ctx).run_sync("rank", False)
        assert list(rank["progress"]["stages"]) == ["rank", "research"]
        with pytest.raises(ValidationFailure):
            _service(ctx).create("everything")
    finally:
        ctx.close()


def test_a_failing_source_does_not_fail_the_run_but_every_source_failing_does(world) -> None:
    ctx = EngineContext.create()
    try:
        record = _service(ctx, discovery=mock_http(board="fail")).run_sync("full", False)
    finally:
        ctx.close()
    assert record["status"] == "FAILED"
    assert "every discovery source failed" in record["error"]
    # The rest of the loop still ran on what is stored.
    assert "rank" in record["progress"]["stages"]
    assert record["progress"]["sources"][0]["ok"] is False
    assert _event_types(record["id"])[-1] == "PIPELINE_RUN_FAILED"


def test_no_enabled_source_is_a_warning_not_a_failure(tmp_path, monkeypatch, world) -> None:
    use_registry(
        tmp_path,
        monkeypatch,
        yaml_text="version: 1\nsources:\n  - id: off\n    kind: GREENHOUSE\n    token: x\n"
        "    display_name: Off\n    enabled: false\n",
    )
    ctx = EngineContext.create()
    try:
        record = _service(ctx).run_sync("discover", False)
    finally:
        ctx.close()
    assert record["status"] == "COMPLETED"
    assert any("no discovery sources" in w for w in record["progress"]["warnings"])


# ---------------------------------------------------------------------------
# one at a time, and dead runs
# ---------------------------------------------------------------------------


def test_a_second_run_is_refused_while_one_is_active(world) -> None:
    ctx = EngineContext.create()
    try:
        first = RunService(ctx).create("full")
        with pytest.raises(ValidationFailure, match="already in progress"):
            RunService(ctx).create("rank")
        _service(ctx).execute(first)
        # Finished: the next one may start.
        assert RunService(ctx).create("rank")
    finally:
        ctx.close()


def test_a_run_that_stopped_updating_is_reaped_and_does_not_block_forever(world) -> None:
    ctx = EngineContext.create()
    try:
        dead = RunService(ctx).create("full")
        with ctx.repos() as repos:
            row = repos.pipeline_runs.get(dead)
            row.status = "RUNNING"
            row.updated_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=1)
        # `onupdate` would overwrite the stale heartbeat; put it back with a raw UPDATE.
        with ctx.repos() as repos:
            repos.session.execute(
                models.PipelineRunRow.__table__.update()
                .where(models.PipelineRunRow.id == dead)
                .values(
                    updated_at=datetime.now(UTC).replace(tzinfo=None) - STALE_AFTER * 2,
                    progress={},  # no recorded owner: only the clock can say it is dead
                )
            )
        replacement = RunService(ctx).create("full")
        assert replacement != dead
        reaped = RunService(ctx).get(dead)
        assert reaped["status"] == "FAILED"
        assert "abandoned" in reaped["error"]
        assert "PIPELINE_RUN_FAILED" in _event_types(dead)
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


def _shortlisted_ids(candidate_id: str) -> list[str]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            return [o.id for o in repos.opportunities.shortlist(candidate_id)]
    finally:
        ctx.close()


def test_research_skips_a_fresh_packet_and_refreshes_a_stale_one(world) -> None:
    candidate_id, profile_id = world
    ctx = EngineContext.create()
    try:
        _service(ctx).run_sync("rank", False)  # discovers nothing yet; rank is empty
        _service(ctx).run_sync("discover", False)
        IntelligenceService(ctx).run(candidate_id=candidate_id, search_profile_id=profile_id)
        requests: list[str] = []
        http = mock_http(requests=requests)
        first = ResearchService(ctx, http=http).research_fresh(candidate_id)
        assert (first["researched"], first["skipped_fresh"]) == (1, 0)
        fetched = len(requests)
        second = ResearchService(ctx, http=http).research_fresh(candidate_id)
        assert (second["researched"], second["skipped_fresh"]) == (0, 1)
        assert len(requests) == fetched, "a fresh packet must not be fetched again"

        opportunity_id = _shortlisted_ids(candidate_id)[0]
        with ctx.repos() as repos:
            repos.session.execute(
                models.ResearchPacketRow.__table__.update()
                .where(models.ResearchPacketRow.opportunity_id == opportunity_id)
                .values(updated_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8))
            )
        third = ResearchService(ctx, http=http).research_fresh(candidate_id)
        assert (third["researched"], third["skipped_fresh"]) == (1, 0)
        assert len(requests) > fetched
    finally:
        ctx.close()


def test_one_research_failure_never_fails_the_run(world, monkeypatch) -> None:
    def broken_site(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    ctx = EngineContext.create()
    try:
        record = _service(ctx, research=mock_http(site=broken_site)).run_sync("full", False)
        assert record["status"] == "COMPLETED"
        # The page could not be read; the packet still exists and says so.
        assert record["progress"]["stages"]["research"]["researched"] == 1
        with ctx.read_repos() as repos:
            row = repos.research.for_opportunity(_shortlisted_ids(record["candidate_id"])[0])
            assert "no company description could be captured" in " ".join(
                row.payload["inferences"]
            )

        # An exception inside research (not just a failed fetch) is counted, not raised.
        def explode(self, opportunity_id, **kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(ResearchService, "research", explode)
        with ctx.repos() as repos:
            repos.session.execute(models.ResearchPacketRow.__table__.delete())
        summary = ResearchService(ctx, http=mock_http()).research_fresh(record["candidate_id"])
        assert summary["failed"] == 1 and summary["researched"] == 0
        assert "kaboom" in summary["errors"][0]
    finally:
        ctx.close()


def test_the_company_site_is_fetched_without_holding_the_database_write_lock(
    world, tmp_path
) -> None:
    ctx = EngineContext.create()
    db_path = ctx.settings.database_url.split("///", 1)[1]
    locked: list[bool] = []

    def site(request: httpx.Request) -> httpx.Response:
        # While the "slow" site is being read, another writer must be able to write.
        conn = sqlite3.connect(db_path, timeout=0.3)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
            locked.append(False)
        except sqlite3.OperationalError:
            locked.append(True)
        finally:
            conn.close()
        return httpx.Response(200, text="<title>A long enough company title</title>")

    try:
        _service(ctx).run_sync("discover", False)
        IntelligenceService(ctx).run(
            candidate_id=world[0], search_profile_id=world[1]
        )
        summary = ResearchService(ctx, http=mock_http(site=site)).research_fresh(world[0])
    finally:
        ctx.close()
    assert summary["researched"] == 1
    assert locked == [False]


def test_ranking_twice_after_research_keeps_the_shortlist_stable(world) -> None:
    candidate_id, profile_id = world
    ctx = EngineContext.create()
    try:
        _service(ctx).run_sync("full", False)
        again = IntelligenceService(ctx).run(candidate_id=candidate_id, search_profile_id=profile_id)
        assert again.shortlisted == 0
        with ctx.read_repos() as repos:
            rows = repos.opportunities.shortlist(candidate_id)
        assert len(rows) == 1 and rows[0].status == "RESEARCHED"
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# ranking in batches
# ---------------------------------------------------------------------------


def test_ranking_commits_in_batches_so_the_dashboard_is_not_locked_out(world) -> None:
    candidate_id, profile_id = world
    ctx = EngineContext.create()
    watcher = EngineContext.create()
    try:
        _service(ctx).run_sync("discover", False)
        batches: list[dict] = []

        def progress(payload: dict) -> None:
            if payload["stage"] == "assess":
                with watcher.read_repos() as repos:
                    rejected = (
                        repos.session.query(models.Opportunity)
                        .filter(models.Opportunity.status == "REJECTED")
                        .count()
                    )
                batches.append({"done": payload["done"], "visible_rejected": rejected})

        result = IntelligenceService(ctx).run(
            candidate_id=candidate_id,
            search_profile_id=profile_id,
            progress=progress,
            batch_size=4,
        )
    finally:
        watcher.close()
        ctx.close()
    assert [b["done"] for b in batches] == [4, 8, 12, 15]
    # Rejections from the first batch were already committed and visible to another
    # connection before the pass finished.
    assert batches[0]["visible_rejected"] > 0
    assert batches[0]["visible_rejected"] < result.rejected
    assert result.considered == 15 and result.qualified == 1


def test_a_progress_callback_that_raises_does_not_break_the_pass(world) -> None:
    candidate_id, profile_id = world
    ctx = EngineContext.create()
    try:
        _service(ctx).run_sync("discover", False)

        def boom(_payload: dict) -> None:
            raise RuntimeError("sink down")

        result = IntelligenceService(ctx).run(
            candidate_id=candidate_id, search_profile_id=profile_id, progress=boom
        )
        assert result.shortlisted == 1
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# preparing
# ---------------------------------------------------------------------------


def test_prepare_builds_a_package_for_the_top_role_once(world) -> None:
    ctx = EngineContext.create()
    try:
        first = _service(ctx).run_sync("full", True)
        assert first["status"] == "COMPLETED", first
        prepare = first["progress"]["stages"]["prepare"]
        assert (prepare["prepared"], prepare["failed"], prepare["skipped_existing"]) == (1, 0, 0)
        with ctx.read_repos() as repos:
            opportunity = repos.opportunities.shortlist(first["candidate_id"])[0]
            assert opportunity.status == "AWAITING_APPROVAL"
            application = repos.applications.for_opportunity(opportunity.id)
            assert application is not None and application.status == "AWAITING_APPROVAL"

        # The next run leaves the package alone instead of writing revision 2.
        second = _service(ctx).run_sync("full", True)
        assert second["status"] == "COMPLETED", second["error"]
        again = second["progress"]["stages"]["prepare"]
        assert (again["prepared"], again["skipped_existing"]) == (0, 1)
        with ctx.read_repos() as repos:
            assert repos.packages.latest_for_opportunity(opportunity.id).revision == 1
    finally:
        ctx.close()


def test_prepare_without_a_resume_warns_and_does_not_fail(tmp_path, monkeypatch) -> None:
    use_registry(tmp_path, monkeypatch)
    seed_candidate()  # no resume
    ctx = EngineContext.create()
    try:
        record = _service(ctx).run_sync("full", True)
    finally:
        ctx.close()
    assert record["status"] == "COMPLETED"
    assert record["progress"]["stages"]["prepare"]["status"] == "skipped"
    assert any("master resume" in w for w in record["progress"]["warnings"])


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------


def _wait_for(client: TestClient, run_id: str, timeout: float = 30.0) -> dict:
    assert RunService.wait(run_id, timeout), "the background run did not finish"
    return client.get(f"/api/runs/{run_id}").json()


def test_post_runs_starts_a_background_run_that_can_be_polled(world, monkeypatch) -> None:
    monkeypatch.setattr("sabra_engine.runs.build_discovery_http", lambda _ctx: mock_http())
    monkeypatch.setattr("sabra_engine.runs.build_research_http", lambda _ctx: mock_http())
    client = TestClient(app)
    started = client.post("/api/runs", json={"kind": "full", "prepare": True})
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    assert started.json()["status"] == "QUEUED"
    finished = _wait_for(client, run_id)
    assert finished["status"] == "COMPLETED", finished
    assert finished["progress"]["stages"]["prepare"]["prepared"] == 1

    listed = client.get("/api/runs?limit=5").json()["runs"]
    assert listed[0]["id"] == run_id
    assert client.get("/api/runs/prun_nope").status_code == 404
    assert client.post("/api/runs", json={"kind": "everything"}).status_code == 422


def test_a_second_post_is_409_while_a_run_is_in_flight(world, monkeypatch) -> None:
    release = threading.Event()
    entered = threading.Event()

    def slow_board(request: httpx.Request) -> httpx.Response:
        entered.set()
        release.wait(20)
        return httpx.Response(200, json={"jobs": []})

    blocking = httpx.Client(transport=httpx.MockTransport(slow_board))
    from sabra_providers.http import HttpClient

    monkeypatch.setattr(
        "sabra_engine.runs.build_discovery_http",
        lambda _ctx: HttpClient(client=blocking, max_attempts=1),
    )
    monkeypatch.setattr("sabra_engine.runs.build_research_http", lambda _ctx: mock_http())
    client = TestClient(app)
    first = client.post("/api/runs", json={"kind": "discover"})
    assert first.status_code == 202
    try:
        assert entered.wait(20), "the run never reached the source"
        # Progress is readable while it is still running.
        running = client.get(f"/api/runs/{first.json()['id']}").json()
        assert running["status"] == "RUNNING"
        second = client.post("/api/runs", json={"kind": "full"})
        assert second.status_code == 409
        assert "already in progress" in second.json()["detail"]
    finally:
        release.set()
    assert _wait_for(client, first.json()["id"])["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_sabra_run_prints_json_and_a_table(world, monkeypatch) -> None:
    monkeypatch.setattr("sabra_engine.runs.build_discovery_http", lambda _ctx: mock_http())
    monkeypatch.setattr("sabra_engine.runs.build_research_http", lambda _ctx: mock_http())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["run", "--kind", "rank", "--json"])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["status"] == "COMPLETED" and record["kind"] == "rank"

    table = runner.invoke(cli_app, ["run", "--kind", "full", "--prepare"])
    assert table.exit_code == 0, table.output
    assert "discover" in table.output and "Sources" in table.output
    assert "greenhouse-revance" in table.output

    bad = runner.invoke(cli_app, ["run", "--kind", "everything"])
    assert bad.exit_code != 0


def test_sabra_rank_researches_the_shortlist(world, monkeypatch) -> None:
    monkeypatch.setattr("sabra_engine.runs.build_discovery_http", lambda _ctx: mock_http())
    requests: list[str] = []
    monkeypatch.setattr(
        "sabra_engine.runs.build_research_http", lambda _ctx: mock_http(requests=requests)
    )
    runner = CliRunner()
    runner.invoke(cli_app, ["run", "--kind", "discover", "--json"])
    ranked = runner.invoke(cli_app, ["rank", "--json"])
    assert ranked.exit_code == 0, ranked.output
    payload = json.loads(ranked.output)
    assert payload["research"]["researched"] == 1
    assert any("revance.com" in url for url in requests)
    skipped = json.loads(runner.invoke(cli_app, ["rank", "--json"]).output)
    assert skipped["research"]["skipped_fresh"] == 1
    off = json.loads(runner.invoke(cli_app, ["rank", "--no-research", "--json"]).output)
    assert "research" not in off


def test_stopping_the_server_marks_its_running_run_failed(world, monkeypatch) -> None:
    from sabra_providers.http import HttpClient

    release = threading.Event()
    entered = threading.Event()

    def slow_board(request: httpx.Request) -> httpx.Response:
        entered.set()
        release.wait(20)
        return httpx.Response(200, json={"jobs": []})

    monkeypatch.setattr(
        "sabra_engine.runs.build_discovery_http",
        lambda _ctx: HttpClient(
            client=httpx.Client(transport=httpx.MockTransport(slow_board)), max_attempts=1
        ),
    )
    client = TestClient(app)
    run_id = client.post("/api/runs", json={"kind": "discover"}).json()["id"]
    try:
        assert entered.wait(20)
        assert RunService.record_server_stopped() == 1
        stopped = client.get(f"/api/runs/{run_id}").json()
        assert stopped["status"] == "FAILED"
        assert "dashboard stopped" in stopped["error"]
    finally:
        release.set()
        RunService.wait(run_id, 20)
