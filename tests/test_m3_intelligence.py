"""M3 intelligence: hard filtering, fit assessment, ranking, shortlisting.

The filter tests pin the rule that matters most: **absence of evidence is not
evidence of rejection**. A posting with no stated salary, no stated location or no
stated arrangement must survive deterministic screening, because rejecting it would
silently delete an opportunity the candidate may want.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sabra_domain import models
from sabra_domain.db import Database
from sabra_domain.enums import JobStatus, RejectionReason
from sabra_domain.repositories import (
    CandidateRepository,
    EventRepository,
    JobRepository,
    OpportunityRepository,
    SearchProfileRepository,
)
from sabra_domain.schemas import (
    CandidateProfile,
    CanonicalJob,
    CertificationEntry,
    CompensationPreference,
    EmploymentEntry,
    LocationIntent,
    SearchProfile,
    SkillEntry,
)
from sabra_domain.settings import Settings
from sabra_engine.context import EngineContext
from sabra_engine.intelligence import IntelligenceService
from sabra_scoring.features import (
    extract_candidate_features,
    extract_intent_features,
    extract_job_features,
)
from sabra_scoring.fit import FitWeights, HeuristicFitEngine
from sabra_scoring.hard_filter import HardFilter
from sabra_scoring.ranking import Ranker, desirability_score, outcome_probability
from sqlalchemy import select

TODAY = date(2026, 9, 16)


def make_search(**overrides) -> SearchProfile:
    base = dict(
        id="srch_test",
        name="Test search",
        candidate_id="cand_test",
        target_titles=["Territory Manager", "Regional Sales Manager"],
        acceptable_titles=["Area Sales Manager"],
        excluded_titles=["Intern", "Machine Operator"],
        role_family_terms=["sales", "account", "business development"],
        role_level_terms=["manager", "director", "executive", "representative"],
        industries=["Medical aesthetics", "Medical devices"],
        locations=LocationIntent(
            cities=["Dallas", "Plano"], states=["TX"], metro_areas=["Dallas-Fort Worth"]
        ),
        compensation=CompensationPreference(minimum=75000.0, target=100000.0),
        travel_max_percent=40,
        max_age_days=120,
        shortlist_target=10,
    )
    base.update(overrides)
    return SearchProfile(**base)


def make_job(**overrides) -> models.Job:
    base = dict(
        source="GREENHOUSE",
        source_job_id="1",
        source_url="https://example.test/1",
        company="Revance",
        title="Territory Manager",
        normalized_title="territory manager",
        location="Dallas, TX",
        remote_status="ONSITE",
        employment_type="FULL_TIME",
        description="Own the Dallas territory selling aesthetic devices to clinics. " * 20,
        requirements="- 3+ years of medical device sales experience\n- Valid driver's license",
        preferred_qualifications="- Experience with injectables",
        responsibilities="- Own the territory plan",
        salary_min=90000.0,
        salary_max=110000.0,
        salary_currency="USD",
        salary_period="YEAR",
        travel="up to 30% travel",
        department="Sales",
        seniority="MANAGER",
        date_posted=TODAY - timedelta(days=10),
    )
    base.update(overrides)
    return models.Job(canonical_key="ck", **base)


def evaluate(job: models.Job, search: SearchProfile | None = None, candidate=None):
    search = search or make_search()
    intent = extract_intent_features(search)
    candidate_features = extract_candidate_features(
        candidate or CandidateProfile(candidate_id="cand_test")
    )
    return HardFilter().evaluate(
        extract_job_features(job, today=TODAY), search, intent, candidate_features, today=TODAY
    )


# ---------------------------------------------------------------------------
# Hard filter
# ---------------------------------------------------------------------------


def test_a_clean_posting_passes() -> None:
    decision = evaluate(make_job())
    assert decision.passed, decision.detail


def test_below_minimum_compensation_is_rejected() -> None:
    decision = evaluate(make_job(salary_min=45000.0, salary_max=55000.0))
    assert RejectionReason.BELOW_MINIMUM_COMPENSATION.value in decision.reasons


def test_unknown_compensation_is_not_a_rejection() -> None:
    """Absence of evidence is not evidence of rejection."""

    decision = evaluate(make_job(salary_min=None, salary_max=None, salary_period=None))
    assert RejectionReason.BELOW_MINIMUM_COMPENSATION.value not in decision.reasons
    assert "compensation" in decision.checks_skipped


def test_hourly_compensation_is_not_compared_with_an_annual_floor() -> None:
    """$35/hour must not be read as below a $75,000 floor."""

    decision = evaluate(make_job(salary_min=35.0, salary_max=45.0, salary_period="HOUR"))
    assert RejectionReason.BELOW_MINIMUM_COMPENSATION.value not in decision.reasons


def test_prohibited_geography_is_rejected_but_unknown_location_is_not() -> None:
    out_of_area = evaluate(make_job(location="Seattle, WA", remote_status="ONSITE"))
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in out_of_area.reasons

    unknown = evaluate(make_job(location=None, remote_status="UNKNOWN"))
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in unknown.reasons
    assert "geography" in unknown.checks_skipped


def test_remote_and_hybrid_arrangements_are_accepted() -> None:
    for status in ("REMOTE", "HYBRID"):
        decision = evaluate(make_job(location="Seattle, WA", remote_status=status))
        assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in decision.reasons


def test_texas_spelled_out_matches_a_tx_profile() -> None:
    """Postings say "Texas" as often as "TX"; a raw token comparison missed it."""

    decision = evaluate(make_job(location="Texas", remote_status="ONSITE"))
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in decision.reasons


def test_excluded_title_matches_on_word_boundaries() -> None:
    assert (
        RejectionReason.EXCLUDED_TITLE.value in evaluate(make_job(title="Machine Operator")).reasons
    )
    # "Intern" must not reject "Internal Sales Manager".
    decision = evaluate(make_job(title="Internal Sales Manager"))
    assert RejectionReason.EXCLUDED_TITLE.value not in decision.reasons


def test_commission_only_is_rejected_unless_the_profile_accepts_it() -> None:
    job = make_job(employment_type="COMMISSION_ONLY")
    assert RejectionReason.INCOMPATIBLE_EMPLOYMENT_TYPE.value in evaluate(job).reasons
    accepting = make_search(allow_commission_only=True)
    assert (
        RejectionReason.INCOMPATIBLE_EMPLOYMENT_TYPE.value not in evaluate(job, accepting).reasons
    )


def test_excessive_travel_is_rejected() -> None:
    assert (
        RejectionReason.EXCESSIVE_TRAVEL.value
        in evaluate(make_job(travel="up to 75% travel")).reasons
    )
    assert "travel" not in evaluate(make_job(travel=None)).reasons


def test_stale_posting_is_rejected_as_unavailable() -> None:
    old = make_job(date_posted=TODAY - timedelta(days=400))
    assert RejectionReason.ROLE_UNAVAILABLE.value in evaluate(old).reasons


def test_role_family_plus_level_is_required_together() -> None:
    """A family word alone is not a role, and a level word alone is not either."""

    assert evaluate(make_job(title="Regional Account Director")).passed
    assert (
        RejectionReason.OUTSIDE_SEARCH_INTENT.value
        in evaluate(make_job(title="Sales Operations Analyst")).reasons
    )  # family but no declared level term
    assert (
        RejectionReason.OUTSIDE_SEARCH_INTENT.value
        in evaluate(make_job(title="Senior Software Engineer")).reasons
    )


def test_multi_word_role_family_matches_as_a_phrase_only() -> None:
    """Splitting "business development" into tokens admitted an HR role."""

    assert evaluate(make_job(title="Business Development Manager")).passed
    assert (
        RejectionReason.OUTSIDE_SEARCH_INTENT.value
        in evaluate(make_job(title="Director, People Business Partner")).reasons
    )


def test_mandatory_qualification_only_rejects_with_candidate_evidence() -> None:
    search = make_search(minimum_qualifications=["valid driver's license"])
    # No candidate evidence at all: silence is not a failure.
    assert (
        RejectionReason.MISSING_MANDATORY_QUALIFICATION.value
        not in evaluate(make_job(), search).reasons
    )

    candidate = CandidateProfile(
        candidate_id="cand_test",
        employment=[EmploymentEntry(employer="Acme", title="Territory Manager")],
        skills=[SkillEntry(name="Medical device sales")],
    )
    decision = evaluate(make_job(), search, candidate)
    assert RejectionReason.MISSING_MANDATORY_QUALIFICATION.value in decision.reasons

    qualified = CandidateProfile(
        candidate_id="cand_test",
        employment=[EmploymentEntry(employer="Acme", title="Territory Manager")],
        skills=[SkillEntry(name="Driver's license")],
    )
    assert (
        RejectionReason.MISSING_MANDATORY_QUALIFICATION.value
        not in evaluate(make_job(), search, qualified).reasons
    )


def test_every_check_is_reported_exactly_once_as_run_or_skipped() -> None:
    decision = evaluate(make_job())
    assert decision.checks_run
    # No duplicates, and a skipped check is always one that was scheduled.
    assert len(decision.checks_run) == len(set(decision.checks_run))
    assert set(decision.checks_skipped) <= set(decision.checks_run)
    # Every declared check appears: an unlisted check would be one nobody can audit.
    assert "title_relevance" in decision.checks_run
    assert "compensation" in decision.checks_run


# ---------------------------------------------------------------------------
# Fit assessment
# ---------------------------------------------------------------------------


def assess(job: models.Job, search: SearchProfile | None = None, candidate=None):
    search = search or make_search()
    return HeuristicFitEngine().assess(
        extract_job_features(job, today=TODAY),
        search,
        extract_intent_features(search),
        extract_candidate_features(candidate or CandidateProfile(candidate_id="cand_test")),
    )


def test_fit_scores_are_bounded_and_every_component_is_explained() -> None:
    assessment = assess(make_job())
    for field in (
        "fit_score",
        "career_score",
        "qualification_score",
        "compensation_score",
        "location_score",
        "industry_score",
        "trajectory_score",
    ):
        assert 0.0 <= getattr(assessment, field) <= 1.0
    assert set(assessment.component_rationale) == {
        "qualification_score",
        "career_score",
        "compensation_score",
        "location_score",
        "industry_score",
        "trajectory_score",
    }
    assert all(rationale for rationale in assessment.component_rationale.values())
    assert assessment.why_this_job and "scores" in assessment.why_this_job


def test_unassessable_components_are_excluded_rather_than_guessed() -> None:
    assessment = assess(make_job())
    components = assessment.features["components"]
    # No candidate data: qualification and trajectory cannot be assessed.
    assert components["qualification_score"]["available"] is False
    assert components["trajectory_score"]["available"] is False
    assert components["location_score"]["available"] is True
    assert assessment.features["available_weight"] < 1.0
    assert any("qualification" in gap for gap in assessment.gaps)


def test_confidence_falls_when_the_posting_is_thin() -> None:
    rich = assess(make_job())
    thin = assess(
        make_job(
            description="Apply now.",
            requirements=None,
            responsibilities=None,
            department=None,
            salary_min=None,
            salary_max=None,
            salary_period=None,
        )
    )
    assert thin.confidence < rich.confidence
    assert thin.features["posting_completeness"] < rich.features["posting_completeness"]


def test_qualification_score_responds_to_candidate_evidence() -> None:
    """Assert the score MOVES with its input, not merely that it exists."""

    job = make_job(
        requirements="- 3+ years of medical device sales experience\n- Experience with injectables"
    )
    empty = assess(job, candidate=CandidateProfile(candidate_id="c"))
    partial = assess(
        job,
        candidate=CandidateProfile(
            candidate_id="c",
            employment=[
                EmploymentEntry(
                    employer="Acme",
                    title="Territory Manager",
                    accomplishments=["Grew medical device sales 20%"],
                )
            ],
        ),
    )
    assert partial.qualification_score > empty.qualification_score
    assert partial.features["components"]["qualification_score"]["details"]["covered"] >= 1


def test_candidate_skills_count_as_evidence() -> None:
    """A profile that is largely a skills list must still score qualification.

    Asserted as a property of `evidence_tokens` rather than through the one skill
    that exposed it: skills and certifications were extracted into their own sets
    and never merged into the evidence the scorers read.
    """

    profile = CandidateProfile(
        candidate_id="c",
        skills=[SkillEntry(name="Medical device sales"), SkillEntry(name="Injectables")],
        certifications=[CertificationEntry(name="CPR")],
        industries=["Medical aesthetics"],
    )
    features = extract_candidate_features(profile)
    assert features.populated
    for token in ("injectables", "medical", "device", "sales", "cpr"):
        assert token in features.evidence_tokens, f"{token!r} missing from evidence"


def test_location_score_prefers_the_candidates_own_metro() -> None:
    search = make_search()
    dallas = assess(make_job(location="Dallas, TX"), search)
    houston = assess(make_job(location="Houston, TX"), search)
    remote = assess(make_job(location="Remote", remote_status="REMOTE"), search)
    assert dallas.location_score > houston.location_score
    assert remote.location_score == 1.0
    assert dallas.features["components"]["location_score"]["details"]["tier"] == "preferred_city"
    assert houston.features["components"]["location_score"]["details"]["tier"] == "accepted_state"


def test_weights_are_honoured() -> None:
    location_only = HeuristicFitEngine(
        FitWeights(
            qualification=0.0,
            career=0.0,
            compensation=0.0,
            location=1.0,
            industry=0.0,
            trajectory=0.0,
        )
    ).assess(
        extract_job_features(make_job(), today=TODAY),
        make_search(),
        extract_intent_features(make_search()),
        extract_candidate_features(CandidateProfile(candidate_id="c")),
    )
    assert location_only.fit_score == location_only.location_score


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_ranking_is_deterministic_and_ordered() -> None:
    strong = assess(make_job())
    weak = assess(make_job(title="Sales Operations Analyst", location="Seattle, WA"))
    ranker = Ranker()
    first = ranker.rank([("opp_b", weak), ("opp_a", strong)])
    second = ranker.rank([("opp_a", strong), ("opp_b", weak)])
    assert [r.opportunity_id for r in first] == [r.opportunity_id for r in second]
    assert first[0].opportunity_id == "opp_a"
    assert first[0].rank_score > first[1].rank_score


def test_ranking_explains_itself() -> None:
    entry = Ranker().score("opp_a", assess(make_job()))
    assert set(entry.components) == {"fit", "desirability", "confidence", "outcome_probability"}
    assert "fit" in entry.explanation and "confidence" in entry.explanation


def test_a_single_weak_factor_does_not_zero_the_rank() -> None:
    """A raw product would collapse to ~0; the geometric mean keeps it informative."""

    entry = Ranker().score("opp_a", assess(make_job()))
    assert entry.rank_score > 0.05


def test_desirability_and_outcome_probability_are_bounded() -> None:
    for job in (make_job(), make_job(salary_min=None, salary_max=None, salary_period=None)):
        assessment = assess(job)
        assert 0.0 <= desirability_score(assessment) <= 1.0
        assert 0.0 <= outcome_probability(assessment) <= 1.0


def test_shortlist_selection_respects_the_limit_and_eligibility() -> None:
    ranker = Ranker()
    ranked = ranker.rank(
        [
            (f"opp_{i}", assess(make_job(source_job_id=str(i), salary_min=90000 + i * 1000)))
            for i in range(20)
        ]
    )
    selected = ranker.select_shortlist(ranked, limit=10)
    assert len(selected) == 10

    eligible = {r.opportunity_id: r.opportunity_id != ranked[0].opportunity_id for r in ranked}
    filtered = ranker.select_shortlist(ranked, limit=10, eligible=eligible)
    assert ranked[0].opportunity_id not in {r.opportunity_id for r in filtered}


# ---------------------------------------------------------------------------
# Service-level integration
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine_db(db: Database, settings: Settings) -> Database:
    return db


def _seed(engine_db: Database, jobs: list[models.Job], *, with_candidate: bool = False):
    with engine_db.session() as session:
        candidates = CandidateRepository(session)
        candidate = candidates.create("Test Candidate")
        profile = CandidateProfile(
            candidate_id=candidate.id,
            headline="Aesthetics sales leader" if with_candidate else None,
            employment=[EmploymentEntry(employer="Acme", title="Territory Manager")]
            if with_candidate
            else [],
        )
        candidates.save_profile(candidate.id, profile)
        search = make_search(candidate_id=candidate.id)
        SearchProfileRepository(session).upsert(search)

        job_repo = JobRepository(session)
        opp_repo = OpportunityRepository(session)
        for job in jobs:
            row, _, _ = job_repo.upsert_canonical(
                CanonicalJob(
                    source=job.source,
                    source_job_id=job.source_job_id,
                    source_url=job.source_url,
                    company=job.company,
                    title=job.title,
                    location=job.location,
                    remote_status=job.remote_status,
                    employment_type=job.employment_type,
                    salary_min=job.salary_min,
                    salary_max=job.salary_max,
                    salary_period=job.salary_period,
                    description=job.description,
                    requirements=job.requirements,
                    responsibilities=job.responsibilities,
                    travel=job.travel,
                    department=job.department,
                    seniority=job.seniority,
                    date_posted=job.date_posted,
                    date_discovered=datetime.now(UTC),
                    raw_source_hash=f"hash-{job.source_job_id}",
                )
            )
            row.status = str(JobStatus.NORMALIZED)
            opp = opp_repo.create(
                candidate_id=candidate.id,
                search_profile_id=search.id,
                canonical_key=row.canonical_key,
                title=row.title,
                company=row.company,
                location=row.location,
                primary_job_id=row.id,
                source_ids=[
                    {
                        "source": row.source,
                        "source_job_id": row.source_job_id,
                        "url": row.source_url,
                    }
                ],
            )
            row.opportunity_id = opp.id
        session.flush()
        return candidate.id, search.id


def test_intelligence_service_filters_ranks_and_shortlists(
    engine_db: Database, settings: Settings
) -> None:
    jobs = [
        make_job(source_job_id="1", title="Territory Manager", location="Dallas, TX"),
        make_job(source_job_id="2", title="Regional Sales Manager", location="Plano, TX"),
        make_job(source_job_id="3", title="Senior Software Engineer", location="Dallas, TX"),
        make_job(source_job_id="4", title="Business Development Manager", location="Seattle, WA"),
        make_job(
            source_job_id="5",
            title="Area Sales Manager",
            location="Dallas, TX",
            salary_min=60000.0,
            salary_max=70000.0,
        ),
    ]
    candidate_id, search_id = _seed(engine_db, jobs)
    ctx = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=None,
        create_schema=False,
    )
    try:
        result = IntelligenceService(ctx).run(
            candidate_id=candidate_id, search_profile_id=search_id, shortlist_limit=2
        )
    finally:
        ctx.close()

    assert result.considered == 5
    assert result.rejected == 3  # engineer, Seattle, below floor
    assert result.qualified == 2
    assert result.shortlisted == 2
    assert result.rejection_breakdown["OUTSIDE_SEARCH_INTENT"] >= 1
    assert result.rejection_breakdown["PROHIBITED_GEOGRAPHY"] == 1
    assert result.rejection_breakdown["BELOW_MINIMUM_COMPENSATION"] == 1

    with engine_db.read_session() as session:
        statuses = {
            o.title: o.status for o in session.execute(select(models.Opportunity)).scalars()
        }
        assert statuses["Senior Software Engineer"] == "REJECTED"
        assert statuses["Territory Manager"] == "SHORTLISTED"
        assert statuses["Regional Sales Manager"] == "SHORTLISTED"
        assert statuses["Business Development Manager"] == "REJECTED"

        rejected = (
            session.execute(
                select(models.Opportunity).where(models.Opportunity.status == "REJECTED")
            )
            .scalars()
            .first()
        )
        assert rejected.rejection and rejected.rejection["reasons"]


def test_intelligence_service_is_idempotent(engine_db: Database, settings: Settings) -> None:
    """Re-running must not re-reject, re-qualify or re-shortlist anything."""

    jobs = [
        make_job(source_job_id="1", title="Territory Manager"),
        make_job(source_job_id="2", title="Senior Software Engineer"),
    ]
    candidate_id, search_id = _seed(engine_db, jobs)
    ctx = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=None,
        create_schema=False,
    )
    try:
        service = IntelligenceService(ctx)
        first = service.run(
            candidate_id=candidate_id, search_profile_id=search_id, shortlist_limit=10
        )
        second = service.run(
            candidate_id=candidate_id, search_profile_id=search_id, shortlist_limit=10
        )
    finally:
        ctx.close()

    assert (first.rejected, first.shortlisted) == (1, 1)
    assert (second.rejected, second.shortlisted) == (0, 0)
    assert second.ranked == first.ranked == 1

    with engine_db.read_session() as session:
        events = EventRepository(session).recent(limit=500)
        shortlist_events = [e for e in events if e.event_type == "JOB_SHORTLISTED"]
        reject_events = [e for e in events if e.event_type == "JOB_REJECTED"]
        assert len(shortlist_events) == 1
        assert len(reject_events) == 1


def test_rejection_is_audited_with_reasons(engine_db: Database, settings: Settings) -> None:
    jobs = [make_job(source_job_id="1", title="Senior Software Engineer")]
    candidate_id, search_id = _seed(engine_db, jobs)
    ctx = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=None,
        create_schema=False,
    )
    try:
        IntelligenceService(ctx).run(candidate_id=candidate_id, search_profile_id=search_id)
    finally:
        ctx.close()

    with engine_db.read_session() as session:
        events = [
            e for e in EventRepository(session).recent(limit=100) if e.event_type == "JOB_REJECTED"
        ]
        assert len(events) == 1
        payload = events[0].payload
        assert payload["reasons"]
        assert payload["detail"]
        assert "title_relevance" in payload["checks_run"]


def test_rescore_drops_a_newly_rejected_opportunity_from_the_shortlist(
    engine_db: Database, settings: Settings
) -> None:
    """`rank --rescore` must not leave a role on the shortlist after the filter rejects it."""

    jobs = [make_job(source_job_id="1", title="Territory Manager", location="Dallas, TX")]
    candidate_id, search_id = _seed(engine_db, jobs)
    ctx = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=None,
        create_schema=False,
    )
    try:
        IntelligenceService(ctx).run(
            candidate_id=candidate_id, search_profile_id=search_id, shortlist_limit=5
        )
        with ctx.repos() as repos:
            opportunity = repos.opportunities.list_for_candidate(candidate_id)[0]
            assert opportunity.shortlisted is True
            assert opportunity.status == JobStatus.SHORTLISTED.value
            job = repos.jobs.get(opportunity.primary_job_id)
            job.salary_min = 40000.0
            job.salary_max = 50000.0
        IntelligenceService(ctx).run(
            candidate_id=candidate_id,
            search_profile_id=search_id,
            shortlist_limit=5,
            rescore=True,
        )
    finally:
        ctx.close()

    with engine_db.read_session() as session:
        opportunity = session.execute(select(models.Opportunity)).scalars().one()
        assert opportunity.status == JobStatus.REJECTED.value
        assert opportunity.shortlisted is False
