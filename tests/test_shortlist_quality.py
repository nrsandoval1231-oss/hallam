"""Shortlist quality: title level check, remote geography, per-company cap, pay, rescore.

Pilot evidence these pin down: an administrative-assistant search shortlisted nine
"Customer Success Manager" roles from one company, several of them "Remote - Canada",
while the genuine administrative roles in Dallas ranked 17th to 61st.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest_app import build_job
from fastapi.testclient import TestClient
from sabra_api.main import app
from sabra_domain import models
from sabra_domain.db import Database
from sabra_domain.enums import ApplicationStatus, JobStatus, RejectionReason
from sabra_domain.errors import InvalidTransition
from sabra_domain.repositories import EventRepository, SearchProfileRepository
from sabra_domain.schemas import (
    CompensationPreference,
    LocationIntent,
    SearchProfile,
)
from sabra_domain.settings import Settings
from sabra_domain.state import assert_job_transition
from sabra_engine.candidate_intake import seed_search_profile
from sabra_engine.context import EngineContext
from sabra_engine.intelligence import IntelligenceService
from sabra_engine.runs import RunService
from sabra_scoring.geography import detect_regions, normalize_countries
from sabra_scoring.ranking import RankedOpportunity, Ranker
from sqlalchemy import select
from test_m3_intelligence import _seed, assess, evaluate, make_job, make_search
from test_p2a_support import mock_http, seed_candidate, use_registry

EXAMPLE = Path(__file__).parents[1] / "config" / "search_profile.example.yaml"

# ---------------------------------------------------------------------------
# 1. Title relevance needs a level word for a fragment of a declared title
# ---------------------------------------------------------------------------


def admin_search(**overrides) -> SearchProfile:
    base = dict(
        id="srch_admin",
        name="Admin search",
        candidate_id="cand_test",
        target_titles=[
            "Administrative Assistant",
            "Office Manager",
            "Executive Assistant",
            "Customer Success Coordinator",
        ],
        role_family_terms=["customer success", "office", "administrative"],
        role_level_terms=["assistant", "coordinator", "specialist", "clerk", "associate", "support"],
        locations=LocationIntent(cities=["Dallas"], states=["TX"]),
        compensation=CompensationPreference(minimum=55000.0, target=70000.0),
        max_age_days=365,
    )
    base.update(overrides)
    return SearchProfile(**base)


def title_decision(title: str, search: SearchProfile | None = None):
    return evaluate(
        make_job(title=title, location="Dallas, TX", remote_status="ONSITE"),
        search or admin_search(),
    )


@pytest.mark.parametrize(
    "title",
    [
        "Customer Success Manager",
        "Senior Customer Success Manager",
        "Customer Success Manager, Enterprise",
    ],
)
def test_a_fragment_of_a_declared_title_without_a_level_word_is_rejected(title: str) -> None:
    decision = title_decision(title)
    assert RejectionReason.OUTSIDE_SEARCH_INTENT.value in decision.reasons


@pytest.mark.parametrize(
    "title",
    [
        "Customer Success Coordinator",
        "Customer Success Associate",
        "Customer Success Specialist",
        "Office Manager",
        "Executive Assistant",
        "Administrative Assistant II",
        "Regional Office Manager",
    ],
)
def test_declared_titles_and_fragments_with_a_level_word_are_accepted(title: str) -> None:
    decision = title_decision(title)
    assert RejectionReason.OUTSIDE_SEARCH_INTENT.value not in decision.reasons, decision.detail


def test_a_profile_with_no_level_terms_keeps_the_plain_ngram_rule() -> None:
    search = admin_search(role_level_terms=[], role_family_terms=[])
    decision = title_decision("Customer Success Manager", search)
    assert RejectionReason.OUTSIDE_SEARCH_INTENT.value not in decision.reasons


# ---------------------------------------------------------------------------
# 2. Remote postings are checked against the accepted countries
# ---------------------------------------------------------------------------


def remote_decision(location: str | None, search: SearchProfile | None = None):
    return evaluate(
        make_job(location=location, remote_status="REMOTE"), search or make_search()
    )


@pytest.mark.parametrize(
    "location",
    [
        "Remote - Canada",
        "Remote, Canada",
        "Remote / Australia / Singapore",
        "Remote - EMEA",
        "Remote - APAC",
        "Remote - LATAM",
        "Remote - UK",
        "Remote - United Kingdom",
        "Remote - India",
        "Remote - Germany",
        "Remote - Ontario",
        "Remote - Mexico",
        "REMOTE - CANADA",
        "Remote (Poland)",
    ],
)
def test_remote_outside_the_accepted_country_is_rejected(location: str) -> None:
    decision = remote_decision(location)
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in decision.reasons, location
    assert "remote outside" in decision.detail[RejectionReason.PROHIBITED_GEOGRAPHY.value]


@pytest.mark.parametrize(
    "location",
    [
        "Remote - US",
        "Remote, United States",
        "US Remote",
        "Remote - USA",
        "Remote - U.S.",
        "Remote - Denver",
        "Remote",
        "Remote - Texas",
        "Remote - New Mexico",
        "Remote - Indiana",
        "Remote - Denver, CO",
        "Remote - US or Canada",
        "Remote - North America",
        "Remote - Worldwide",
        "Remote - New South Wales, United States",
        None,
    ],
)
def test_remote_in_the_us_or_naming_no_country_is_kept(location: str | None) -> None:
    decision = remote_decision(location)
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in decision.reasons, location


def test_a_profile_that_lists_a_country_accepts_it() -> None:
    search = make_search(
        locations=LocationIntent(cities=["Dallas"], states=["TX"], countries=["US", "Canada"])
    )
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in remote_decision(
        "Remote - Canada", search
    ).reasons
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision(
        "Remote - EMEA", search
    ).reasons


def test_a_region_in_the_profile_covers_its_countries() -> None:
    search = make_search(locations=LocationIntent(countries=["US", "EMEA"]))
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in remote_decision(
        "Remote - Germany", search
    ).reasons
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in remote_decision(
        "Remote - EMEA", search
    ).reasons
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision(
        "Remote - India", search
    ).reasons


def test_an_empty_country_list_turns_the_check_off() -> None:
    search = make_search(locations=LocationIntent(countries=[]))
    assert remote_decision("Remote - Canada", search).passed


def test_relocation_does_not_exempt_a_remote_role_from_the_country_check() -> None:
    search = make_search(
        locations=LocationIntent(cities=["Dallas"], states=["TX"], allow_relocation=True)
    )
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision(
        "Remote - Canada", search
    ).reasons


def test_non_remote_postings_keep_the_city_and_state_logic() -> None:
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in evaluate(
        make_job(location="Toronto, Canada", remote_status="ONSITE")
    ).reasons
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in evaluate(
        make_job(location="Dallas, TX", remote_status="ONSITE")
    ).reasons
    # Hybrid in another country is judged by the old rules, not the country list.
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in evaluate(
        make_job(location="Toronto, Canada", remote_status="HYBRID")
    ).reasons


def test_country_detector_reads_names_codes_and_states() -> None:
    assert detect_regions("Remote - Canada") == {"CA"}
    assert detect_regions("Remote / Australia / Singapore") == {"AU", "SG"}
    assert detect_regions("Remote - EMEA") == {"EMEA"}
    assert detect_regions("Dallas, TX") == {"US"}
    assert detect_regions("San Francisco, CA") == {"US"}
    assert detect_regions("Remote - New Mexico") == {"US"}
    assert detect_regions("Remote - Northern Ireland") == {"GB"}
    assert detect_regions("Remote - Denver") == set()
    assert detect_regions(None) == set()
    assert normalize_countries(["United States", "usa", "US"]) == {"US"}
    assert normalize_countries(["CA"]) == {"CA"}


# ---------------------------------------------------------------------------
# 3. The shortlist holds at most `max_per_company` roles from one company
# ---------------------------------------------------------------------------


def ranked_rows(spec: list[tuple[str, float]]) -> list[RankedOpportunity]:
    return sorted(
        (RankedOpportunity(opportunity_id=i, rank_score=s) for i, s in spec),
        key=lambda r: (-r.rank_score, r.opportunity_id),
    )


def test_ten_rows_from_one_company_and_five_from_others_select_seven() -> None:
    spec = [(f"s{i:02d}", 0.95 - i * 0.01) for i in range(10)]
    spec += [(f"o{i}", 0.5 - i * 0.01) for i in range(5)]
    companies = {f"s{i:02d}": "Samsara Inc." for i in range(10)}
    companies.update({f"o{i}": f"Other {i}" for i in range(5)})
    stats: dict[str, int] = {}
    selected = Ranker().select_shortlist(
        ranked_rows(spec), limit=10, companies=companies, max_per_company=2, stats=stats
    )
    chosen = [r.opportunity_id for r in selected]
    assert chosen == ["s00", "s01", "o0", "o1", "o2", "o3", "o4"]
    assert stats["skipped_for_diversity"] == 8


def test_freed_slots_go_to_the_next_best_roles_from_other_companies() -> None:
    spec = [
        ("a1", 0.90),
        ("b1", 0.85),
        ("a2", 0.80),
        ("b2", 0.75),
        ("a3", 0.70),
        ("b3", 0.65),
        ("c1", 0.60),
        ("d1", 0.55),
    ]
    companies = {"a1": "A", "a2": "A", "a3": "A", "b1": "B", "b2": "B", "b3": "B", "c1": "C", "d1": "D"}
    ranker = Ranker()
    stats: dict[str, int] = {}
    selected = ranker.select_shortlist(
        ranked_rows(spec), limit=5, companies=companies, max_per_company=2, stats=stats
    )
    assert [r.opportunity_id for r in selected] == ["a1", "b1", "a2", "b2", "c1"]
    assert stats["skipped_for_diversity"] == 2
    # Without a cap the top five are taken as before.
    plain = ranker.select_shortlist(ranked_rows(spec), limit=5)
    assert [r.opportunity_id for r in plain] == ["a1", "b1", "a2", "b2", "a3"]


def test_company_names_are_compared_after_normalisation() -> None:
    spec = [("x1", 0.9), ("x2", 0.8), ("x3", 0.7), ("y1", 0.6)]
    companies = {"x1": "Samsara", "x2": "Samsara, Inc.", "x3": "SAMSARA INC", "y1": "Other"}
    chosen = Ranker().select_shortlist(
        ranked_rows(spec), limit=3, companies=companies, max_per_company=2
    )
    assert [r.opportunity_id for r in chosen] == ["x1", "x2", "y1"]


def test_diversity_selection_is_deterministic_on_ties_and_skips_ineligible_rows() -> None:
    spec = [("t3", 0.5), ("t1", 0.5), ("t2", 0.5), ("u1", 0.4)]
    companies = {"t1": "T", "t2": "T", "t3": "T", "u1": "U"}
    ranker = Ranker()
    ranked = ranked_rows(spec)
    chosen = ranker.select_shortlist(ranked, limit=3, companies=companies, max_per_company=2)
    assert [r.opportunity_id for r in chosen] == ["t1", "t2", "u1"]
    eligible = {"t1": False}
    chosen = ranker.select_shortlist(
        ranked, limit=3, eligible=eligible, companies=companies, max_per_company=2
    )
    assert [r.opportunity_id for r in chosen] == ["t2", "t3", "u1"]


def _four_roles(company: str = "Samsara") -> list:
    return [
        make_job(source_job_id="1", title="Territory Manager", location="Dallas, TX", company=company),
        make_job(source_job_id="2", title="Regional Sales Manager", location="Plano, TX", company=company),
        make_job(source_job_id="3", title="Area Sales Manager", location="Dallas, TX", company=company),
        make_job(
            source_job_id="4", title="Business Development Manager", location="Plano, TX", company=company
        ),
        make_job(source_job_id="5", title="Territory Manager", location="Plano, TX", company="Revance"),
    ]


@pytest.fixture()
def ctx(db: Database, settings: Settings):
    context = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=None,
        create_schema=False,
    )
    try:
        yield context
    finally:
        context.close()


def test_the_service_caps_roles_per_company_and_records_the_skips(
    db: Database, ctx: EngineContext
) -> None:
    candidate_id, search_id = _seed(db, _four_roles())
    result = IntelligenceService(ctx).run(
        candidate_id=candidate_id, search_profile_id=search_id, shortlist_limit=10
    )
    assert result.qualified == 5
    assert result.shortlisted == 3  # two Samsara roles and the one Revance role
    assert result.skipped_for_diversity == 2
    assert result.summary()["skipped_for_diversity"] == 2
    with db.read_session() as session:
        rows = list(session.execute(select(models.Opportunity)).scalars())
        samsara = [o for o in rows if o.company == "Samsara"]
        assert sum(1 for o in samsara if o.shortlisted) == 2
        assert sum(1 for o in samsara if o.status == "QUALIFIED") == 2


def test_the_cap_is_a_search_profile_field_with_a_default() -> None:
    assert make_search().max_per_company == 2
    assert make_search(max_per_company=1).max_per_company == 1
    with pytest.raises(ValueError):
        make_search(max_per_company=0)
    assert make_search().locations.countries == ["US"]
    old = make_search().model_dump(mode="json")
    old.pop("max_per_company")
    old["locations"].pop("countries")
    restored = SearchProfile.model_validate(old)
    assert restored.max_per_company == 2 and restored.locations.countries == ["US"]


# ---------------------------------------------------------------------------
# 4. Pay far above the target earns no extra credit and is flagged
# ---------------------------------------------------------------------------

SENIOR_RISK = "pay suggests a more senior role than the search targets"


def pay_search() -> SearchProfile:
    return admin_search(compensation=CompensationPreference(minimum=55000.0, target=70000.0))


def test_pay_far_above_the_target_is_capped_and_noted_as_a_risk() -> None:
    far_above = assess(make_job(salary_min=134000.0, salary_max=174000.0), pay_search())
    assert far_above.compensation_score == 1.0
    assert SENIOR_RISK in far_above.risks

    just_meets = assess(make_job(salary_min=60000.0, salary_max=70000.0), pay_search())
    assert just_meets.compensation_score == 1.0
    assert SENIOR_RISK not in just_meets.risks


def test_pay_above_the_target_never_beats_pay_at_the_target() -> None:
    at_target = assess(make_job(salary_min=65000.0, salary_max=70000.0), pay_search())
    way_above = assess(make_job(salary_min=150000.0, salary_max=200000.0), pay_search())
    assert at_target.compensation_score == way_above.compensation_score == 1.0


def test_the_senior_pay_note_needs_the_minimum_above_one_point_six_times_the_target() -> None:
    # 98,000 / 70,000 = 1.4: well paid, not a signal of a different job.
    assert SENIOR_RISK not in assess(
        make_job(salary_min=98000.0, salary_max=174000.0), pay_search()
    ).risks
    # 112,001 / 70,000 is just over 1.6.
    assert SENIOR_RISK in assess(
        make_job(salary_min=112001.0, salary_max=130000.0), pay_search()
    ).risks


def test_below_target_pay_still_rises_with_pay_and_is_not_rejected_for_being_high() -> None:
    lower = assess(make_job(salary_min=50000.0, salary_max=56000.0), pay_search())
    higher = assess(make_job(salary_min=55000.0, salary_max=65000.0), pay_search())
    assert 0.0 <= lower.compensation_score < higher.compensation_score < 1.0
    decision = evaluate(make_job(salary_min=134000.0, salary_max=174000.0), pay_search())
    assert RejectionReason.BELOW_MINIMUM_COMPENSATION.value not in decision.reasons


# ---------------------------------------------------------------------------
# `sabra rank --rescore`: re-filter and re-rank rows decided in an earlier pass
# ---------------------------------------------------------------------------


def _replace_search(db: Database, **changes) -> None:
    with db.session() as session:
        repo = SearchProfileRepository(session)
        row = session.execute(select(models.SearchProfileRow)).scalars().one()
        profile = SearchProfile.model_validate(row.payload)
        repo.upsert(profile.model_copy(update=changes))


def _states(db: Database) -> dict[str, tuple[str, bool]]:
    with db.read_session() as session:
        return {
            o.title: (o.status, o.shortlisted)
            for o in session.execute(select(models.Opportunity)).scalars()
        }


def test_rescore_brings_back_a_rejected_role_that_now_passes(
    db: Database, ctx: EngineContext
) -> None:
    jobs = [make_job(source_job_id="1", title="Territory Manager", location="Dallas, TX")]
    candidate_id, search_id = _seed(db, jobs)
    _replace_search(db, excluded_titles=["Territory"])
    service = IntelligenceService(ctx)
    first = service.run(candidate_id=candidate_id, search_profile_id=search_id)
    assert first.rejected == 1
    assert _states(db)["Territory Manager"] == ("REJECTED", False)

    _replace_search(db, excluded_titles=[])
    plain = service.run(candidate_id=candidate_id, search_profile_id=search_id)
    assert plain.qualified == 0 and _states(db)["Territory Manager"][0] == "REJECTED"

    rescored = service.run(candidate_id=candidate_id, search_profile_id=search_id, rescore=True)
    assert rescored.reinstated == 1 and rescored.qualified == 1 and rescored.shortlisted == 1
    assert _states(db)["Territory Manager"] == ("SHORTLISTED", True)
    with db.read_session() as session:
        opportunity = session.execute(select(models.Opportunity)).scalars().one()
        job = session.execute(select(models.Job)).scalars().one()
        assert opportunity.rejection is None and job.rejection is None
        assert job.status == JobStatus.SHORTLISTED.value
        events = EventRepository(session).recent(limit=100)
        passed = [
            e
            for e in events
            if e.event_type == "JOB_SCORED" and e.payload.get("stage") == "hard_filter_passed"
        ]
        assert any(e.payload.get("reinstated_from") == ["EXCLUDED_TITLE"] for e in passed)


def test_rescore_rejects_a_qualified_and_a_shortlisted_role_that_now_fail(
    db: Database, ctx: EngineContext
) -> None:
    jobs = [
        make_job(source_job_id="1", title="Territory Manager", location="Dallas, TX", company="A"),
        make_job(source_job_id="2", title="Area Sales Manager", location="Dallas, TX", company="B"),
        make_job(source_job_id="3", title="Regional Sales Manager", location="Dallas, TX", company="B"),
        make_job(source_job_id="4", title="Business Development Manager", location="Dallas, TX", company="B"),
    ]
    candidate_id, search_id = _seed(db, jobs)
    service = IntelligenceService(ctx)
    service.run(candidate_id=candidate_id, search_profile_id=search_id, shortlist_limit=2)
    before = _states(db)
    assert sorted(s for s, _ in before.values()) == ["QUALIFIED", "QUALIFIED", "SHORTLISTED", "SHORTLISTED"]

    _replace_search(db, excluded_titles=["Manager"])
    result = service.run(
        candidate_id=candidate_id, search_profile_id=search_id, shortlist_limit=2, rescore=True
    )
    assert result.rejected == 4
    assert set(_states(db).values()) == {("REJECTED", False)}
    with db.read_session() as session:
        events = [
            e for e in EventRepository(session).recent(limit=200) if e.event_type == "JOB_REJECTED"
        ]
        assert len(events) == 4
        assert all("EXCLUDED_TITLE" in e.payload["reasons"] for e in events)


def test_a_repeated_rescore_does_not_rewrite_rejections(
    db: Database, ctx: EngineContext
) -> None:
    jobs = [make_job(source_job_id="1", title="Senior Software Engineer", location="Dallas, TX")]
    candidate_id, search_id = _seed(db, jobs)
    service = IntelligenceService(ctx)
    service.run(candidate_id=candidate_id, search_profile_id=search_id)
    again = service.run(candidate_id=candidate_id, search_profile_id=search_id, rescore=True)
    assert again.rejected == 0 and again.already_decided == 1
    with db.read_session() as session:
        events = [
            e for e in EventRepository(session).recent(limit=50) if e.event_type == "JOB_REJECTED"
        ]
        assert len(events) == 1


def test_rescore_never_reverses_a_human_decision(db: Database, ctx: EngineContext) -> None:
    jobs = [
        make_job(source_job_id="1", title="Territory Manager", location="Dallas, TX", company="A"),
        make_job(source_job_id="2", title="Area Sales Manager", location="Dallas, TX", company="B"),
    ]
    candidate_id, search_id = _seed(db, jobs)
    with db.session() as session:
        rows = {o.title: o for o in session.execute(select(models.Opportunity)).scalars()}
        rows["Territory Manager"].status = JobStatus.REJECTED.value
        rows["Territory Manager"].rejection = {"reasons": ["OWNER_PASSED"], "detail": {}}
        rows["Area Sales Manager"].status = JobStatus.REJECTED.value
        rows["Area Sales Manager"].rejection = {"reasons": ["DECLINED_BY_CANDIDATE"], "detail": {}}
    result = IntelligenceService(ctx).run(
        candidate_id=candidate_id, search_profile_id=search_id, rescore=True
    )
    assert result.reinstated == 0
    assert set(_states(db).values()) == {("REJECTED", False)}


def test_rescore_leaves_a_role_with_a_package_awaiting_approval_alone(prepared) -> None:
    ctx = prepared.ctx
    with ctx.repos() as repos:
        search = repos.search_profiles.get(prepared.search_profile_id)
        # The role would now be filtered out, so a rescore that touched it would reject it.
        repos.search_profiles.upsert(search.model_copy(update={"excluded_titles": ["Regional"]}))
        assert repos.opportunities.get(prepared.opportunity_id).status == "AWAITING_APPROVAL"

    def state() -> tuple:
        with ctx.read_repos() as repos:
            opportunity = repos.opportunities.get(prepared.opportunity_id)
            application = repos.applications.for_opportunity(prepared.opportunity_id)
            package = repos.packages.latest_for_opportunity(prepared.opportunity_id)
            return (
                opportunity.status,
                opportunity.shortlisted,
                opportunity.rejection,
                application.status,
                package.status,
            )

    before = state()
    assert before[3] == ApplicationStatus.AWAITING_APPROVAL.value
    result = IntelligenceService(ctx).run(
        candidate_id=prepared.candidate_id,
        search_profile_id=prepared.search_profile_id,
        rescore=True,
    )
    assert result.rejected == 0
    assert state() == before


def test_rescore_leaves_a_shortlisted_role_that_has_an_application_alone(prepared) -> None:
    """Even if the opportunity row still reads SHORTLISTED, an application protects it."""

    ctx = prepared.ctx
    with ctx.repos() as repos:
        search = repos.search_profiles.get(prepared.search_profile_id)
        repos.search_profiles.upsert(search.model_copy(update={"excluded_titles": ["Regional"]}))
        repos.opportunities.get(prepared.opportunity_id).status = JobStatus.SHORTLISTED.value
        assert repos.applications.for_opportunity(prepared.opportunity_id) is not None
    result = IntelligenceService(ctx).run(
        candidate_id=prepared.candidate_id,
        search_profile_id=prepared.search_profile_id,
        rescore=True,
    )
    assert result.rejected == 0
    with ctx.read_repos() as repos:
        opportunity = repos.opportunities.get(prepared.opportunity_id)
        assert opportunity.status == JobStatus.SHORTLISTED.value
        assert opportunity.shortlisted is True
        assert (
            repos.applications.for_opportunity(prepared.opportunity_id).status
            == ApplicationStatus.AWAITING_APPROVAL.value
        )


# ---------------------------------------------------------------------------
# Review fixes: country list values, location phrases, level vocabulary, guards
# ---------------------------------------------------------------------------


def with_countries(*countries: str) -> SearchProfile:
    return make_search(locations=LocationIntent(countries=list(countries)))


@pytest.mark.parametrize("word", ["Worldwide", "Anywhere", "Global", "Any", "All", "worldwide"])
def test_worldwide_style_values_turn_the_country_check_off(word: str) -> None:
    search = with_countries(word)
    for location in ("Remote - Canada", "Remote - US", "Remote / Australia / Singapore", "Remote"):
        assert remote_decision(location, search).passed, (word, location)


def test_can_means_canada_and_americas_and_eu_are_regions() -> None:
    canada = with_countries("CAN")
    assert remote_decision("Remote - Canada", canada).passed
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision(
        "Remote - US", canada
    ).reasons

    americas = with_countries("Americas")
    for location in ("Remote - US", "Remote - Canada", "Remote - Mexico", "Remote - Brazil"):
        assert remote_decision(location, americas).passed, location
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision(
        "Remote - Germany", americas
    ).reasons

    eu = with_countries("EU")
    assert remote_decision("Remote - Germany", eu).passed
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision("Remote - US", eu).reasons


@pytest.mark.parametrize("value", ["Narnia", "Dallas", "Remote-ish"])
def test_an_unrecognised_country_is_refused_with_a_clear_message(value: str) -> None:
    with pytest.raises(ValueError, match="unrecognised country or region"):
        LocationIntent(countries=[value])
    # Known values are kept and blanks are dropped.
    assert LocationIntent(countries=["US", "  ", "Canada"]).countries == ["US", "Canada"]


def test_the_searches_api_refuses_an_unrecognised_country_with_a_422() -> None:
    _, profile_id = seed_candidate()
    client = TestClient(app)
    response = client.put(
        f"/api/searches/{profile_id}",
        json={"name": "X", "locations": {"countries": ["Narnia"]}},
    )
    assert response.status_code == 422
    assert any(e["field"] == "locations.countries" for e in response.json()["errors"])
    ok = client.put(
        f"/api/searches/{profile_id}",
        json={"name": "X", "locations": {"countries": ["Worldwide"]}},
    )
    assert ok.status_code == 200


@pytest.mark.parametrize(
    "location",
    [
        "Remote - New England",
        "Remote (Not available in Canada)",
        "Remote - US, excluding Canada",
        "Remote except Canada and Mexico",
        "Remote - Puerto Rico",
        "Remote ON SITE",
        "REMOTE - ON SITE",
    ],
)
def test_phrases_that_do_not_name_a_foreign_place_are_kept(location: str) -> None:
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value not in remote_decision(location).reasons


@pytest.mark.parametrize(
    "location",
    [
        "Remote - CAN",
        "Remote, Toronto, ON",
        "Remote (Vancouver, BC)",
        "Remote - EU",
        "Remote - England",
    ],
)
def test_more_foreign_forms_are_rejected(location: str) -> None:
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision(location).reasons


def test_a_region_on_the_posting_covers_the_countries_a_profile_lists() -> None:
    germany = with_countries("Germany")
    assert remote_decision("Remote - EMEA", germany).passed
    assert remote_decision("Remote - Europe", germany).passed
    # A US-only profile still rejects EMEA, and a European one still rejects APAC.
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision("Remote - EMEA").reasons
    assert RejectionReason.PROHIBITED_GEOGRAPHY.value in remote_decision(
        "Remote - APAC", germany
    ).reasons


def test_the_state_table_has_a_public_name() -> None:
    from sabra_domain.identity import US_STATE_ABBREVIATIONS

    assert US_STATE_ABBREVIATIONS["texas"] == "TX"


def test_front_desk_agent_is_accepted_by_the_example_profile_and_leader_by_the_seed() -> None:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data.update(id="srch_example", candidate_id="cand_test")
    example = SearchProfile.model_validate(data)
    assert "agent" in example.role_level_terms
    decision = evaluate(
        make_job(title="Front Desk Agent", location="Dallas, TX", remote_status="ONSITE"), example
    )
    assert RejectionReason.OUTSIDE_SEARCH_INTENT.value not in decision.reasons, decision.detail

    seed = seed_search_profile("cand_test")
    assert "leader" in seed.role_level_terms
    decision = evaluate(
        make_job(title="Regional Sales Leader", location="Dallas, TX", remote_status="ONSITE"),
        seed,
    )
    assert RejectionReason.OUTSIDE_SEARCH_INTENT.value not in decision.reasons, decision.detail


def test_rejected_to_qualified_needs_the_explicit_rescore_edge() -> None:
    with pytest.raises(InvalidTransition):
        assert_job_transition(JobStatus.REJECTED, JobStatus.QUALIFIED)
    assert_job_transition(JobStatus.REJECTED, JobStatus.QUALIFIED, revoke=True)
    # The edge opens nothing else from REJECTED.
    with pytest.raises(InvalidTransition):
        assert_job_transition(JobStatus.REJECTED, JobStatus.SHORTLISTED, revoke=True)


def test_rescore_does_not_reinstate_a_rejected_role_that_has_an_application(prepared) -> None:
    ctx = prepared.ctx
    with ctx.repos() as repos:
        opportunity = repos.opportunities.get(prepared.opportunity_id)
        opportunity.status = JobStatus.REJECTED.value
        opportunity.shortlisted = False
        opportunity.rejection = {"reasons": ["EXCLUDED_TITLE"], "detail": {}}
        for job in repos.opportunities.list_jobs(prepared.opportunity_id):
            job.status = JobStatus.REJECTED.value
        # No excluded title is left in the search, so the filter now passes this role.
        assert repos.applications.for_opportunity(prepared.opportunity_id) is not None
    result = IntelligenceService(ctx).run(
        candidate_id=prepared.candidate_id,
        search_profile_id=prepared.search_profile_id,
        rescore=True,
    )
    assert result.reinstated == 0 and result.qualified == 0
    with ctx.read_repos() as repos:
        opportunity = repos.opportunities.get(prepared.opportunity_id)
        assert opportunity.status == JobStatus.REJECTED.value
        assert (opportunity.rejection or {}).get("reasons") == ["EXCLUDED_TITLE"]
        assert (
            repos.applications.for_opportunity(prepared.opportunity_id).status
            == ApplicationStatus.AWAITING_APPROVAL.value
        )


def test_a_role_with_an_application_survives_demotion_when_it_ranks_below_the_shortlist(
    prepared,
) -> None:
    """Ten better rows push the prepared one out of the top ten; it must not be demoted."""

    ctx = prepared.ctx
    with ctx.repos() as repos:
        # A thin posting, so it scores below the ten full ones.
        thin = repos.jobs.get(prepared.job_id)
        thin.salary_min = thin.salary_max = None
        thin.description = "Short."
        opportunity = repos.opportunities.get(prepared.opportunity_id)
        opportunity.status = JobStatus.SHORTLISTED.value  # SHORTLISTED, but with an application
        for index in range(10):
            row, _, _ = repos.jobs.upsert_canonical(
                build_job(
                    source_job_id=f"strong-{index}",
                    source_url=f"https://example.test/{index}",
                    company=f"Strong Co {index}",
                    title="Territory Manager",
                ),
                raw_payload={},
            )
            row.status = JobStatus.NORMALIZED.value
            created = repos.opportunities.create(
                candidate_id=prepared.candidate_id,
                search_profile_id=prepared.search_profile_id,
                canonical_key=row.canonical_key,
                title=row.title,
                company=row.company,
                location=row.location,
                primary_job_id=row.id,
                source_ids=[
                    {"source": row.source, "source_job_id": row.source_job_id, "url": row.source_url}
                ],
            )
            row.opportunity_id = created.id
    result = IntelligenceService(ctx).run(
        candidate_id=prepared.candidate_id,
        search_profile_id=prepared.search_profile_id,
        rescore=True,
    )
    assert result.shortlisted == 10
    with ctx.read_repos() as repos:
        opportunity = repos.opportunities.get(prepared.opportunity_id)
        assert opportunity.rank_position and opportunity.rank_position > 10, (
            "the prepared role must rank outside the top ten for this to exercise demotion"
        )
        assert opportunity.status == JobStatus.SHORTLISTED.value
        assert opportunity.shortlisted is True
        assert (
            repos.applications.for_opportunity(prepared.opportunity_id).status
            == ApplicationStatus.AWAITING_APPROVAL.value
        )


def test_a_rejected_role_rejected_again_for_new_reasons_updates_its_postings(
    db: Database, ctx: EngineContext
) -> None:
    jobs = [make_job(source_job_id="1", title="Territory Manager", location="Dallas, TX")]
    candidate_id, search_id = _seed(db, jobs)
    _replace_search(db, excluded_titles=["Territory"])
    service = IntelligenceService(ctx)
    service.run(candidate_id=candidate_id, search_profile_id=search_id)
    _replace_search(db, compensation=CompensationPreference(minimum=500000.0, target=600000.0))
    service.run(candidate_id=candidate_id, search_profile_id=search_id, rescore=True)
    with db.read_session() as session:
        opportunity = session.execute(select(models.Opportunity)).scalars().one()
        job = session.execute(select(models.Job)).scalars().one()
        assert set(opportunity.rejection["reasons"]) == {
            "EXCLUDED_TITLE",
            "BELOW_MINIMUM_COMPENSATION",
        }
        assert job.rejection == opportunity.rejection


def test_top_respects_the_per_company_cap(db: Database, ctx: EngineContext) -> None:
    candidate_id, search_id = _seed(db, _four_roles())
    result = IntelligenceService(ctx).run(
        candidate_id=candidate_id, search_profile_id=search_id, shortlist_limit=10
    )
    companies = [item["company"] for item in result.top]
    assert companies.count("Samsara") == 2 and len(companies) == 3


def test_the_rank_stage_reports_diversity_and_reinstated_counts(tmp_path, monkeypatch) -> None:
    use_registry(tmp_path, monkeypatch)
    seed_candidate()
    context = EngineContext.create()
    try:
        record = RunService(
            context, discovery_http=mock_http(), research_http=mock_http()
        ).run_sync("rank", False)
    finally:
        context.close()
    stage = record["progress"]["stages"]["rank"]
    assert stage["skipped_for_diversity"] == 0 and stage["reinstated"] == 0


def test_a_missing_target_does_not_crash_the_compensation_note() -> None:
    """With only a minimum set, a posted range above it used to raise a TypeError."""

    search = admin_search(compensation=CompensationPreference(minimum=55000.0, target=None))
    meets = assess(make_job(salary_min=60000.0, salary_max=70000.0), search)
    assert "55,000 minimum" in meets.component_rationale["compensation_score"]
    below = assess(make_job(salary_min=30000.0, salary_max=40000.0), search)
    assert "below" in below.component_rationale["compensation_score"]
    assert SENIOR_RISK not in meets.risks  # no target, so no senior-pay comparison
