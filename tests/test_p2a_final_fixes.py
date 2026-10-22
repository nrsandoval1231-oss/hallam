"""Final 2A fix loop: name provenance, read-only run polling, pid reuse, research lock,
discovery cleanup and chunked grouping."""

from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sabra_api.main import app
from sabra_documents.package import _package_contact
from sabra_domain import models
from sabra_domain.errors import ValidationFailure
from sabra_domain.schemas import CandidateProfile
from sabra_engine import discovery as discovery_module
from sabra_engine import runs as runs_module
from sabra_engine.context import EngineContext
from sabra_engine.discovery import DiscoveryService
from sabra_engine.intelligence import IntelligenceService
from sabra_engine.research import ResearchService
from sabra_engine.runs import RunAborted, RunService, process_start_time
from sqlalchemy import select
from test_p2a_support import mock_http, seed_candidate, use_registry


@pytest.fixture()
def world(tmp_path, monkeypatch, fixture_resume_text):
    use_registry(tmp_path, monkeypatch)
    return seed_candidate(fixture_resume_text)


def _service(ctx, **kwargs) -> RunService:
    return RunService(ctx, discovery_http=mock_http(), research_http=mock_http(), **kwargs)


# ---------------------------------------------------------------------------
# B1: the full name is only the owner's when both parts are
# ---------------------------------------------------------------------------


def _profile(client: TestClient) -> CandidateProfile:
    return CandidateProfile.model_validate(client.get("/api/candidate").json()["profile"])


def test_changing_only_the_first_name_does_not_vouch_for_the_resume_surname(
    fixture_resume_text,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/candidate/resume",
        files={"resume": ("resume.txt", fixture_resume_text.encode(), "text/plain")},
    )
    before = _profile(client)
    assert before.identity.full_name and before.provenance["identity.full_name"].level == "INFERRED"

    client.put("/api/candidate/identity", json={"first_name": "Maria"})
    after = _profile(client)
    assert after.identity.full_name == before.identity.full_name, "the name must be left alone"
    assert after.provenance["identity.full_name"].level == "INFERRED"
    # Nothing unconfirmed reaches a form as the applicant's name.
    assert _package_contact(after)["name"] is None
    assert _package_contact(after)["first_name"] == "Maria"

    # Once the surname is the owner's too, the full name is rebuilt and verified.
    client.put("/api/candidate/identity", json={"last_name": "Lopez"})
    done = _profile(client)
    assert done.identity.full_name == "Maria Lopez"
    assert done.provenance["identity.full_name"].level == "VERIFIED"
    assert _package_contact(done)["name"] == "Maria Lopez"


def test_the_surname_alone_is_not_enough_either_and_confirming_both_rebuilds(
    fixture_resume_text,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/candidate/resume",
        files={"resume": ("resume.txt", fixture_resume_text.encode(), "text/plain")},
    )
    client.put("/api/candidate/identity", json={"last_name": "Rivera"})
    assert _package_contact(_profile(client))["name"] is None
    client.put("/api/candidate/identity", json={"first_name": "Jordan"})
    assert _package_contact(_profile(client))["name"] == "Jordan Rivera"


# ---------------------------------------------------------------------------
# S1: run polling and the "is a run active" check never take the write lock
# ---------------------------------------------------------------------------


def test_polling_runs_is_read_only_unless_there_is_something_to_reap(world) -> None:
    ctx = EngineContext.create()
    db_file = ctx.settings.database_url.split("///", 1)[1]
    try:
        run_id = RunService(ctx).create("full")
        holder = sqlite3.connect(db_file, timeout=1)
        holder.execute("BEGIN IMMEDIATE")  # someone is in the middle of a long write
        try:
            started = time.monotonic()
            assert RunService(ctx).get(run_id)["status"] == "QUEUED"
            assert RunService(ctx).list_recent(5)[0]["id"] == run_id
            with pytest.raises(ValidationFailure, match="already in progress"):
                RunService.refuse_if_active(ctx)
            assert TestClient(app).get(f"/api/runs/{run_id}").status_code == 200
            assert TestClient(app).get("/api/today").status_code == 200
            assert time.monotonic() - started < 10, "polling queued behind the write lock"
        finally:
            holder.execute("ROLLBACK")
            holder.close()
    finally:
        ctx.close()


def test_a_dead_run_is_still_reaped_by_a_poll(world) -> None:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    ctx = EngineContext.create()
    try:
        run_id = RunService(ctx).create("full")
        with ctx.repos() as repos:
            row = repos.pipeline_runs.get(run_id)
            row.status = "RUNNING"
            row.progress = {"owner": {"pid": finished.pid, "host": socket.gethostname()}}
        assert RunService(ctx).get(run_id)["status"] == "FAILED"
        assert RunService._reap_dead(ctx) == 0
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# S2: a reused process id is not the run's owner
# ---------------------------------------------------------------------------


def _row(owner: dict, age: timedelta = timedelta(seconds=5)):
    now = datetime.now(UTC)
    return SimpleNamespace(progress={"owner": owner}, updated_at=now - age, created_at=now - age)


@pytest.mark.skipif(
    process_start_time(os.getpid()) is None, reason="no process start time on this platform"
)
def test_a_pid_that_now_belongs_to_another_process_is_dead() -> None:
    host = socket.gethostname()
    stamp = process_start_time(os.getpid())
    assert stamp == process_start_time(os.getpid())
    # This very process, with the creation time it really has: alive.
    assert runs_module._is_dead(_row({"pid": os.getpid(), "host": host, "started": stamp})) is False
    # Same pid, a different creation time: the id was reused, the owner is gone.
    assert (
        runs_module._is_dead(_row({"pid": os.getpid(), "host": host, "started": stamp + 10_000}))
        is True
    )
    # A record without a creation time falls back to the pid alone.
    assert runs_module._is_dead(_row({"pid": os.getpid(), "host": host})) is False
    assert runs_module._owner()["started"] == stamp


def test_a_new_run_records_its_process_creation_time(world) -> None:
    stamp = process_start_time(os.getpid())
    ctx = EngineContext.create()
    try:
        run_id = RunService(ctx).create("full")
        owner = RunService(ctx).get(run_id)["progress"]["owner"]
        assert owner["pid"] == os.getpid() and owner["host"] == socket.gethostname()
        assert owner.get("started") == stamp
    finally:
        ctx.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API signatures")
def test_the_windows_calls_declare_their_signatures() -> None:
    kernel32 = runs_module._win_kernel32()
    for name in ("OpenProcess", "GetExitCodeProcess", "CloseHandle", "GetProcessTimes"):
        assert getattr(kernel32, name).argtypes, name
        assert getattr(kernel32, name).restype is not None, name
    assert runs_module.pid_alive(os.getpid()) is True
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert runs_module.pid_alive(finished.pid) is False


# ---------------------------------------------------------------------------
# S3: research never fetches while the write scope is open
# ---------------------------------------------------------------------------


def test_a_changed_site_is_not_fetched_inside_the_write_transaction(world, monkeypatch) -> None:
    candidate_id, profile_id = world
    ctx = EngineContext.create()
    try:
        _service(ctx).run_sync("discover", False)
        IntelligenceService(ctx).run(candidate_id=candidate_id, search_profile_id=profile_id)
        # The prefetch saw one site; by the time the packet is built the company's site is
        # another (revance.com), so the in-scope fetch would be a second, locked one.
        monkeypatch.setattr(
            ResearchService, "_website_for", lambda self, opportunity_id: "https://other.example.com"
        )
        requests: list[str] = []
        summary = ResearchService(ctx, http=mock_http(requests=requests)).research_fresh(
            candidate_id
        )
        assert summary["researched"] == 1 and summary["failed"] == 0
        assert requests == ["https://other.example.com"], requests
        with ctx.read_repos() as repos:
            opportunity = repos.opportunities.shortlist(candidate_id)[0]
            packet = repos.research.for_opportunity(opportunity.id).payload
        assert packet["company_website"] == "https://revance.com"
        assert "no company description could be captured" in " ".join(packet["inferences"])
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# S4: an abort never leaves a run row RUNNING
# ---------------------------------------------------------------------------


def test_an_interrupted_discovery_finishes_its_run_row_as_failed(world) -> None:
    candidate_id, profile_id = world
    ctx = EngineContext.create()

    def interrupt(_payload: dict) -> None:
        raise KeyboardInterrupt

    try:
        with pytest.raises(KeyboardInterrupt):
            DiscoveryService(ctx).run(
                candidate_id=candidate_id,
                search_profile_id=profile_id,
                http=mock_http(),
                progress=interrupt,
            )
        with ctx.read_repos() as repos:
            row = repos.session.execute(select(models.DiscoveryRunRow)).scalars().one()
            assert row.status == "FAILED" and row.finished_at is not None
            assert "KeyboardInterrupt" in row.errors[0]
    finally:
        ctx.close()


def test_a_run_aborted_on_its_very_first_progress_write_returns_quietly(world, monkeypatch) -> None:
    ctx = EngineContext.create()
    try:
        service = _service(ctx)
        run_id = service.create("full")

        def reaped_then_raise(self) -> None:
            with self.ctx.repos() as repos:
                row = repos.pipeline_runs.get(run_id)
                row.status = "FAILED"
                row.error = "abandoned: reaped"
                row.finished_at = datetime.now(UTC)
            raise RunAborted("FAILED")

        monkeypatch.setattr(RunService, "_flush", reaped_then_raise)
        record = service.execute(run_id)  # must not raise
        assert record["status"] == "FAILED" and record["error"] == "abandoned: reaped"
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# S5: a never-started run is not the baseline for "new"
# ---------------------------------------------------------------------------


def test_a_reaped_queued_run_is_not_the_previous_run(world) -> None:
    ctx = EngineContext.create()
    try:
        _service(ctx).run_sync("full", False)  # finds the role
        time.sleep(0.01)
        _service(ctx).run_sync("rank", False)  # nothing new
        client = TestClient(app)
        assert client.get("/api/today").json()["counts"]["new_high_fit"] == 0
        time.sleep(0.01)
        with ctx.repos() as repos:
            ghost = repos.pipeline_runs.create(
                kind="full", prepare=False, candidate_id=world[0], search_profile_id=world[1]
            )
            ghost.status = "FAILED"  # reaped before it ever started
            ghost.finished_at = datetime.now(UTC)
            assert ghost.started_at is None
        assert client.get("/api/today").json()["counts"]["new_high_fit"] == 0
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# S6: grouping commits in chunks
# ---------------------------------------------------------------------------


def test_grouping_commits_per_batch_and_gives_the_same_result(world, monkeypatch) -> None:
    monkeypatch.setattr(discovery_module, "GROUP_BATCH_SIZE", 4)
    candidate_id, profile_id = world
    ctx = EngineContext.create()
    watcher = EngineContext.create()
    seen: dict[str, int] = {"calls": 0}
    original = DiscoveryService._materialize_group

    def spy(self, repos, group, cand, prof):
        original(self, repos, group, cand, prof)
        seen["calls"] += 1
        if seen["calls"] == 6:  # second batch, two groups in
            with watcher.read_repos() as other:
                seen["visible"] = other.session.query(models.Opportunity).count()

    monkeypatch.setattr(DiscoveryService, "_materialize_group", spy)
    try:
        result = DiscoveryService(ctx).run(
            candidate_id=candidate_id, search_profile_id=profile_id, http=mock_http()
        )
        # The first batch of four groups was already committed while the second was open.
        assert seen["visible"] == 4
        assert result.opportunities_regrouped == 15 and result.canonical_opportunities == 15
        assert result.postings_ingested == 15 and result.duplicates_removed == 0
        # Re-running is idempotent: no duplicate opportunities.
        DiscoveryService(ctx).run(
            candidate_id=candidate_id, search_profile_id=profile_id, http=mock_http()
        )
        with ctx.read_repos() as repos:
            assert repos.session.query(models.Opportunity).count() == 15
            jobs = repos.session.execute(select(models.Job)).scalars().all()
            assert all(job.opportunity_id for job in jobs)
    finally:
        watcher.close()
        ctx.close()
