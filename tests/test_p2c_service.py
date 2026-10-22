"""DocumentService with a model: audit rows, reuse, research, and the default path.

The model is always a `FakeProvider`. The service is exercised through the same
code path the operator uses, against a throwaway database.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
import test_p2c_support as sup
from conftest_app import build_job, build_profile
from sabra_documents.generate import (
    CoverLetterDraft,
    RoleSelection,
    TailoringProposal,
    index_roles,
)
from sabra_documents.truth_gate import AuditedClaim, ClaimAudit
from sabra_domain.enums import JobStatus
from sabra_domain.models import ModelCallRow
from sabra_domain.settings import Settings
from sabra_engine.candidate_intake import ensure_search_profile
from sabra_engine.context import EngineContext
from sabra_engine.documents import DocumentService
from sabra_llm.base import NullProvider
from sabra_llm.errors import LLMError
from sabra_llm.fake import FakeProvider
from sqlalchemy import select


def make_engine(settings: Settings, llm) -> Iterator[EngineContext]:
    ctx = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=str(settings.source_registry_path),
        create_schema=True,
        llm=llm,
    )
    try:
        yield ctx
    finally:
        ctx.close()


def seed(ctx: EngineContext, resume_text: str):
    with ctx.repos() as repos:
        candidate = repos.candidates.create("Test Candidate", email="jordan.rivera@example.test")
        candidate_id = candidate.id
        repos.candidates.save_profile(candidate_id, build_profile(candidate_id))
    search_id = ensure_search_profile(ctx, candidate_id)
    with ctx.repos() as repos:
        row, _, _ = repos.jobs.upsert_canonical(build_job(), raw_payload={})
        row.status = str(JobStatus.NORMALIZED)
        opportunity = repos.opportunities.create(
            candidate_id=candidate_id,
            search_profile_id=search_id,
            canonical_key=row.canonical_key,
            title=row.title,
            company=row.company,
            location=row.location,
            primary_job_id=row.id,
            source_ids=[
                {"source": row.source, "source_job_id": row.source_job_id, "url": row.source_url}
            ],
        )
        row.opportunity_id = opportunity.id
        opportunity.status = str(JobStatus.SHORTLISTED)
        opportunity.shortlisted = True
        opportunity.rank_score = 0.71
        opportunity_id = opportunity.id
        repos.session.flush()
        repos.fit.upsert(
            opportunity_id,
            candidate_id,
            {
                "fit_score": 0.8,
                "career_score": 0.8,
                "qualification_score": 0.8,
                "compensation_score": 0.5,
                "location_score": 1.0,
                "industry_score": 1.0,
                "trajectory_score": 0.7,
                "confidence": 0.7,
                "strengths": ["Direct device sales background"],
                "gaps": ["No product-launch evidence beyond one portfolio"],
                "risks": [],
                "why_this_job": "A close match on territory and category.",
                "component_rationale": {},
                "engine": "heuristic",
                "features": {},
                "rank_score": 0.71,
            },
        )
    service = DocumentService(ctx)
    service.ingest_master_resume(candidate_id, resume_text)
    return SimpleNamespace(
        ctx=ctx, service=service, candidate_id=candidate_id, opportunity_id=opportunity_id
    )


@pytest.fixture()
def fake() -> FakeProvider:
    return FakeProvider()


@pytest.fixture()
def world(settings: Settings, fake: FakeProvider, fixture_resume_text: str):
    (ctx,) = list(_one(make_engine(settings, fake)))
    yield seed(ctx, fixture_resume_text)
    ctx.close()


def _one(generator: Iterator[EngineContext]) -> Iterator[EngineContext]:
    yield next(generator)


def script_good(fake: FakeProvider, candidate_id: str) -> None:
    """The scripted answers for the seeded profile (two roles, three bullets)."""

    profile = build_profile(candidate_id)
    roles = index_roles(profile)
    proposal = TailoringProposal(
        headline="Medical aesthetics and device sales leader",
        summary=(
            "Field sales leader with 11 years across medical aesthetics and medical devices, "
            "who grew regional revenue 43%."
        ),
        roles=[RoleSelection(role_id=r.role_id, bullet_ids=[b for b, _ in r.bullets]) for r in roles],
        skills=["Territory management", "Aesthetic injectables"],
        strengths=["Direct experience selling aesthetic injectables."],
        gaps=[],
    )
    fake.script("tailoring", proposal)
    fake.script("audit:tailoring", sup.good_tailoring_audit())
    fake.script("cover_letter", sup.good_letter())
    fake.script("audit:cover_letter", sup.good_letter_audit())
    fake.script("fit_narrative", sup.good_narrative())
    fake.script("audit:narrative", sup.good_narrative_audit())


def calls(ctx: EngineContext) -> list[ModelCallRow]:
    with ctx.read_repos() as repos:
        return list(
            repos.session.execute(select(ModelCallRow).order_by(ModelCallRow.occurred_at)).scalars()
        )


def latest_payload(ctx: EngineContext, opportunity_id: str) -> dict:
    with ctx.read_repos() as repos:
        return dict(repos.packages.latest_for_opportunity(opportunity_id).payload)


def test_model_calls_are_audited_in_the_database(world, fake) -> None:
    script_good(fake, world.candidate_id)
    result = world.service.prepare_package(world.opportunity_id)

    rows = calls(world.ctx)
    assert len(rows) == 6
    assert {r.purpose for r in rows} == {
        "tailoring",
        "audit:tailoring",
        "cover_letter",
        "audit:cover_letter",
        "fit_narrative",
        "audit:narrative",
    }
    for row in rows:
        assert row.ok is True and row.provider == "fake" and row.model == "fake-1"
        assert row.entity_type == "opportunity" and row.entity_id == world.opportunity_id
        assert len(row.prompt_hash) == 64 and len(row.response_hash) == 64
        assert row.latency_ms is not None

    payload = latest_payload(world.ctx, world.opportunity_id)
    assert payload["engine"] == "model:fake/fake-1"
    assert payload["truth_ledger"]["generation"]["generated_by"]["cover_letter"] == "model:fake/fake-1"
    # tokens and cache reads travel with the package (the table predates those columns)
    assert payload["truth_ledger"]["generation"]["calls"][0]["input_tokens"] == 100
    assert "Gap analysis:" in payload["fit_explanation"]
    assert result.violations == []


def test_a_failed_call_still_writes_its_row(world, fake) -> None:
    fake.script("tailoring", LLMError("provider down", retryable=True))
    result = world.service.prepare_package(world.opportunity_id)

    rows = calls(world.ctx)
    assert len(rows) == 1
    assert rows[0].ok is False and "provider down" in rows[0].error
    payload = latest_payload(world.ctx, world.opportunity_id)
    assert any(w.startswith("model unavailable:") for w in payload["warnings"])
    assert payload["engine"] == "deterministic"
    assert result.status is not None


def test_unchanged_inputs_do_not_call_the_model_again(world, fake) -> None:
    script_good(fake, world.candidate_id)
    first = world.service.prepare_package(world.opportunity_id)
    assert len(calls(world.ctx)) == 6

    second = world.service.prepare_package(world.opportunity_id)  # nothing left scripted
    assert len(calls(world.ctx)) == 6
    assert len(fake.calls) == 6
    assert second.content_hash == first.content_hash
    payload = latest_payload(world.ctx, world.opportunity_id)
    assert payload["truth_ledger"]["generation"]["reused"] is True


def test_changed_inputs_regenerate(world, fake) -> None:
    script_good(fake, world.candidate_id)
    world.service.prepare_package(world.opportunity_id)

    with world.ctx.repos() as repos:
        job = repos.jobs.get(repos.opportunities.get(world.opportunity_id).primary_job_id)
        job.responsibilities = "- Build the regional strategy\n- Hire and coach reps"
    script_good(fake, world.candidate_id)
    world.service.prepare_package(world.opportunity_id)
    assert len(calls(world.ctx)) == 12


def test_without_a_model_the_service_is_untouched(settings: Settings, fixture_resume_text) -> None:
    (ctx,) = list(_one(make_engine(settings, NullProvider())))
    try:
        world = seed(ctx, fixture_resume_text)
        result = world.service.prepare_package(world.opportunity_id)
        assert calls(ctx) == []
        payload = latest_payload(ctx, world.opportunity_id)
        assert payload["engine"] == "deterministic"
        assert "generation" not in payload["truth_ledger"]
        assert not any("model" in w.lower() for w in payload["warnings"])
        assert result.violations == []
    finally:
        ctx.close()


def test_stored_research_reaches_the_cover_letter_under_the_same_gate(world, fake) -> None:
    with world.ctx.repos() as repos:
        repos.research.upsert(
            world.opportunity_id,
            {
                "verified_facts": ["company site describes itself as: founded in 1998"],
                "inferences": ["probably serves about 400 clinics"],
            },
            "deterministic",
        )
    script_good(fake, world.candidate_id)
    fake.clear("cover_letter").clear("audit:cover_letter")
    letter = CoverLetterDraft(
        body=sup.GOOD_LETTER_BODY.replace(
            "I would welcome",
            "Revance has been in aesthetics since 1998.\n\nI would welcome",
        ),
        claims=sup.good_letter().claims,
    )
    fake.script("cover_letter", letter)
    fake.script(
        "audit:cover_letter",
        ClaimAudit(
            claims=[
                *sup.good_letter_audit().claims,
                sup.claim(
                    "job_fact",
                    "Revance has been in aesthetics since 1998",
                    "founded in 1998",
                ),
            ]
        ),
    )
    world.service.prepare_package(world.opportunity_id)

    prompt = fake.calls_for("cover_letter")[0]["user"]
    assert "founded in 1998" in prompt and "400 clinics" not in prompt
    payload = latest_payload(world.ctx, world.opportunity_id)
    assert "since 1998" in payload["cover_letter"]


def test_the_package_status_does_not_depend_on_which_path_produced_the_text(world, fake) -> None:
    unsupported = ClaimAudit(
        claims=[AuditedClaim(claim="x", kind="candidate_fact", supported=False)]
    )
    fake.script("tailoring", sup.good_proposal(), sup.good_proposal())
    fake.script("audit:tailoring", unsupported, unsupported)
    fake.script("cover_letter", sup.good_letter(), sup.good_letter())
    fake.script("audit:cover_letter", unsupported, unsupported)
    fake.script("fit_narrative", sup.good_narrative(), sup.good_narrative())
    fake.script("audit:narrative", unsupported, unsupported)
    result = world.service.prepare_package(world.opportunity_id)
    payload = latest_payload(world.ctx, world.opportunity_id)
    assert payload["engine"] == "deterministic"
    assert result.violations == []
    assert len(calls(world.ctx)) == 12
