"""Shared fixtures that build a real, prepared application through the services.

Building the fixture through the product's own code paths (rather than by inserting
rows) means the tests exercise the same state machine the operator does.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sabra_domain.db import Database
from sabra_domain.enums import JobStatus
from sabra_domain.schemas import (
    AnswerRecord,
    CandidateProfile,
    CanonicalJob,
    CompensationPreference,
    ContactInfo,
    EmploymentEntry,
    Geography,
    SkillEntry,
    TruthLevel,
)
from sabra_domain.settings import Settings
from sabra_engine.candidate_intake import ensure_search_profile
from sabra_engine.context import EngineContext
from sabra_engine.documents import DocumentService
from sabra_domain.repositories import CandidateRepository, JobRepository, OpportunityRepository

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixture_resume_text() -> str:
    return (FIXTURES / "master_resume_sample.txt").read_text(encoding="utf-8")


@pytest.fixture()
def greenhouse_form_capture() -> dict:
    """The real Greenhouse application form inventory captured from a live board."""

    return json.loads((FIXTURES / "greenhouse_application_form.json").read_text(encoding="utf-8"))


def build_profile(candidate_id: str, *, with_answers: bool = True) -> CandidateProfile:
    """A complete candidate profile, matching the fixture résumé.

    The values are the fixture's, not a real person's. `with_answers=False` produces
    the placeholder profile that asserts nothing.
    """

    if not with_answers:
        return CandidateProfile(candidate_id=candidate_id)
    return CandidateProfile(
        candidate_id=candidate_id,
        identity=ContactInfo(
            full_name="JORDAN A. RIVERA",
            email="jordan.rivera@example.test",
            phone="(214) 555-0142",
            city="Dallas",
            state="TX",
        ),
        headline="Medical Aesthetics & Device Sales Leader",
        summary=(
            "Commercial leader with 11 years of field sales experience across medical "
            "aesthetics and medical device categories."
        ),
        employment=[
            EmploymentEntry(
                employer="Meridian Aesthetics",
                title="Regional Sales Manager",
                start_date="Mar 2019",
                end_date="Present",
                is_current=True,
                accomplishments=[
                    "Grew regional revenue 43% over three years across a 6-state territory.",
                    "Built and led a team of 7 territory managers covering Texas, Oklahoma and Louisiana.",
                ],
            ),
            EmploymentEntry(
                employer="Northstar Medical Devices",
                title="Territory Manager",
                start_date="Jan 2015",
                end_date="Feb 2019",
                accomplishments=[
                    "Sold capital equipment into 120 clinics with an average deal size of $85,000.",
                ],
            ),
        ],
        skills=[
            SkillEntry(name="Medical device sales"),
            SkillEntry(name="Aesthetic injectables"),
            SkillEntry(name="Territory management"),
        ],
        industries=["Medical aesthetics", "Medical devices"],
        geography=Geography(cities=["Dallas"], states=["TX"], metro_areas=["Dallas-Fort Worth"]),
        desired_compensation=CompensationPreference(minimum=90000.0, target=130000.0),
        answer_memory={
            "work_authorization": AnswerRecord(
                question_key="work_authorization",
                value="Yes",
                level=TruthLevel.VERIFIED,
                evidence=["owner_supplied"],
            ),
            "veteran_status": AnswerRecord(
                question_key="veteran_status",
                value="I am a protected veteran",
                level=TruthLevel.VERIFIED,
                sensitive=True,
                user_controlled=True,
            ),
        },
    )


def build_job(**overrides) -> CanonicalJob:
    base = dict(
        source="GREENHOUSE",
        source_job_id="9001",
        source_url="https://job-boards.greenhouse.io/revance/jobs/9001",
        company="Revance Therapeutics",
        company_domain="revance.com",
        title="Regional Sales Manager - Medical Aesthetics",
        location="Dallas, TX",
        remote_status="ONSITE",
        employment_type="FULL_TIME",
        salary_min=110000.0,
        salary_max=140000.0,
        salary_period="YEAR",
        description="Own the Dallas region for an aesthetics portfolio. " * 20,
        requirements="- 5+ years of medical device or aesthetic sales experience",
        responsibilities="- Build the regional strategy",
        date_posted=datetime(2026, 8, 1, tzinfo=UTC).date(),
        board_token="revance",
    )
    base.update(overrides)
    return CanonicalJob(**base)


@pytest.fixture()
def engine(settings: Settings) -> Iterator[EngineContext]:
    # `create_schema=True`: EngineContext builds its own Database over the same URL,
    # so it must materialize the schema itself rather than assume another fixture did.
    ctx = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=str(settings.source_registry_path),
        create_schema=True,
    )
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture()
def prepared(engine: EngineContext, fixture_resume_text: str):
    """A candidate with a master résumé, one shortlisted opportunity and a package.

    Phases are run in separate sessions on purpose: a service that opens its own
    `repos()` while the caller holds one takes a second write lock, which SQLite
    refuses. See the note on `EngineContext.repos`.
    """

    from types import SimpleNamespace

    with engine.repos() as repos:
        candidate = repos.candidates.create("Test Candidate", email="jordan.rivera@example.test")
        candidate_id = candidate.id
        repos.candidates.save_profile(candidate_id, build_profile(candidate_id))

    search_id = ensure_search_profile(engine, candidate_id)

    with engine.repos() as repos:
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
        job_id = row.id
        repos.session.flush()

    documents = DocumentService(engine)
    documents.ingest_master_resume(candidate_id, fixture_resume_text)
    preparation = documents.prepare_package(opportunity_id)

    with engine.read_repos() as repos:
        application = repos.applications.for_opportunity(opportunity_id)
        package_row = repos.packages.get(preparation.package_id)
        opportunity_row = repos.opportunities.get(opportunity_id)

    return SimpleNamespace(
        ctx=engine,
        candidate_id=candidate_id,
        search_profile_id=search_id,
        opportunity_id=opportunity_id,
        job_id=job_id,
        package_id=preparation.package_id,
        application_id=application.id,
        package_hash=package_row.content_hash,
        preparation=preparation,
        opportunity=opportunity_row,
    )
