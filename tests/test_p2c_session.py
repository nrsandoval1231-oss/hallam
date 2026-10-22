"""prepare_package never holds the database while a model runs.

SQLite write scopes take the write lock for their whole duration, so a model call made
inside one would block every other writer. The call happens with no session open, and a
short write scope afterwards refuses to save if the world moved in the meantime.
"""

from __future__ import annotations

import pytest
import test_p2c_support as sup
from conftest_app import build_profile
from sabra_domain.models import ApplicationPackageRow, ModelCallRow, Resume
from sabra_domain.settings import Settings
from sabra_engine.context import EngineContext
from sabra_engine.documents import DocumentService, PreparationConflict
from sabra_llm.base import NullProvider
from sabra_llm.fake import FakeProvider
from sqlalchemy import func, select
from test_p2c_service import make_engine, script_good, seed


class HookedProvider(FakeProvider):
    """A FakeProvider that runs a callback inside its first model call."""

    def __init__(self) -> None:
        super().__init__()
        self.hook = None
        self.fired = False

    def _structured(self, system, user, schema, *, purpose):
        if self.hook is not None and not self.fired:
            self.fired = True
            self.hook()
        return super()._structured(system, user, schema, purpose=purpose)


@pytest.fixture()
def world(settings: Settings, fixture_resume_text: str):
    fake = HookedProvider()
    ctx = next(make_engine(settings, fake))
    try:
        yield seed(ctx, fixture_resume_text), fake
    finally:
        ctx.close()


def count(ctx: EngineContext, model) -> int:
    with ctx.read_repos() as repos:
        return repos.session.execute(select(func.count()).select_from(model)).scalar_one()


def test_no_session_is_open_while_the_model_runs(world) -> None:
    w, fake = world
    script_good(fake, w.candidate_id)
    observed: dict[str, object] = {}

    def probe() -> None:
        pool = w.ctx.db.engine.pool
        observed["checked_out"] = pool.checkedout()
        # a writer gets in at once: nothing is holding the write lock
        with w.ctx.repos() as repos:
            repos.candidates.create("Probe Writer", email="probe@example.test")
        observed["wrote"] = True

    fake.hook = probe
    w.service.prepare_package(w.opportunity_id)

    assert observed == {"checked_out": 0, "wrote": True}
    assert count(w.ctx, ModelCallRow) == 6


def test_the_audits_and_regenerations_also_run_without_a_session(world) -> None:
    w, fake = world
    script_good(fake, w.candidate_id)
    depths: list[int] = []
    original = fake._structured

    def watching(system, user, schema, *, purpose):
        depths.append(w.ctx.db.engine.pool.checkedout())
        return original(system, user, schema, purpose=purpose)

    fake._structured = watching  # type: ignore[method-assign]
    w.service.prepare_package(w.opportunity_id)
    assert len(depths) == 6 and set(depths) == {0}


def moved_by_reingesting_the_master(w) -> None:
    w.service.ingest_master_resume(w.candidate_id, sup.resume_text() + "\n- Added during a run.\n")


def moved_by_a_new_profile_version(w) -> None:
    with w.ctx.repos() as repos:
        profile = build_profile(w.candidate_id)
        profile.headline = "A different headline"
        repos.candidates.save_profile(w.candidate_id, profile)


def moved_by_a_status_change(w) -> None:
    with w.ctx.repos() as repos:
        repos.opportunities.get(w.opportunity_id).status = "REJECTED"


def moved_by_another_package(w) -> None:
    other = EngineContext.create(
        settings=w.ctx.settings,
        database_url=w.ctx.settings.database_url,
        registry_path=str(w.ctx.settings.source_registry_path),
        create_schema=False,
        llm=NullProvider(),
    )
    try:
        DocumentService(other).prepare_package(w.opportunity_id)
    finally:
        other.close()


def moved_by_an_edited_job(w) -> None:
    with w.ctx.repos() as repos:
        job = repos.jobs.get(repos.opportunities.get(w.opportunity_id).primary_job_id)
        job.description = "An edited description of a different job."


@pytest.mark.parametrize(
    ("mover", "reason"),
    [
        (moved_by_reingesting_the_master, "master résumé changed"),
        (moved_by_a_new_profile_version, "candidate profile changed"),
        (moved_by_a_status_change, "status changed"),
        (moved_by_another_package, "newer package revision"),
        (moved_by_an_edited_job, "job posting changed"),
    ],
)
def test_a_package_built_against_stale_state_is_refused_and_not_saved(world, mover, reason) -> None:
    w, fake = world
    script_good(fake, w.candidate_id)
    before_packages = count(w.ctx, ApplicationPackageRow)
    before_resumes = count(w.ctx, Resume)
    fake.hook = lambda: mover(w)

    with pytest.raises(PreparationConflict) as caught:
        w.service.prepare_package(w.opportunity_id)

    assert reason in str(caught.value)
    assert "not saved" in str(caught.value)
    extra_packages = 1 if mover is moved_by_another_package else 0
    assert count(w.ctx, ApplicationPackageRow) == before_packages + extra_packages
    # the master-résumé mover adds one master version; nothing else may add a résumé row
    extra_resumes = {moved_by_reingesting_the_master: 1, moved_by_another_package: 1}.get(mover, 0)
    assert count(w.ctx, Resume) == before_resumes + extra_resumes
    # what the model cost is still on record
    assert count(w.ctx, ModelCallRow) == 6


def test_an_untouched_run_still_saves(world) -> None:
    w, fake = world
    script_good(fake, w.candidate_id)
    result = w.service.prepare_package(w.opportunity_id)
    assert result.package_id and result.application_id
    assert count(w.ctx, ApplicationPackageRow) == 1


def test_a_failing_build_still_records_the_calls_and_saves_nothing(world, monkeypatch) -> None:
    w, fake = world
    script_good(fake, w.candidate_id)

    # the model ran (the hook fires in its first call), then the build fails afterwards
    original = w.service.factory.build

    def failing_build(**kwargs):
        built = original(**kwargs)
        raise RuntimeError(f"build exploded after {len(built.schema.cover_letter or '')} chars")

    monkeypatch.setattr(w.service.factory, "build", failing_build)
    with pytest.raises(RuntimeError, match="build exploded"):
        w.service.prepare_package(w.opportunity_id)
    assert count(w.ctx, ApplicationPackageRow) == 0
    assert count(w.ctx, ModelCallRow) == 6
