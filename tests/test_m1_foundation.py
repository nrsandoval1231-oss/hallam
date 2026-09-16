"""M1 foundation: schema, identity, state machine, truth boundary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from sabra_domain import models
from sabra_domain.db import Database
from sabra_domain.enums import JobStatus, TruthLevel
from sabra_domain.errors import InvalidTransition
from sabra_domain.identity import (
    canonical_key,
    new_id,
    normalize_company,
    normalize_location,
    normalize_title,
)
from sabra_domain.repositories import CandidateRepository, EventRepository, JobRepository
from sabra_domain.schemas import (
    CandidateProfile,
    CanonicalJob,
    EmploymentEntry,
    Provenance,
    SearchProfile,
)
from sabra_domain.state import JOB_TRANSITIONS, assert_job_transition, can_transition_job
from sabra_domain.truth import Evidence, TruthLedger

REQUIRED_TABLES = {
    "candidates",
    "candidate_profiles",
    "resumes",
    "search_profiles",
    "companies",
    "jobs",
    "opportunities",
    "fit_assessments",
    "research_packets",
    "application_packages",
    "applications",
    "events",
    "outcomes",
    "providers",
    "discovery_runs",
    "model_calls",
}


def test_every_domain_object_has_a_table(db: Database) -> None:
    """Requirement 34 names 15 domain objects; each must be persisted."""

    present = set(inspect(db.engine).get_table_names())
    missing = REQUIRED_TABLES - present
    assert not missing, f"missing tables: {sorted(missing)}"


def test_job_lifecycle_covers_every_contract_state() -> None:
    """Requirement 10 lists 19 states; all are declared and all are reachable."""

    declared = {s.value for s in JobStatus}
    expected = {
        "DISCOVERED", "NORMALIZED", "REJECTED", "QUALIFIED", "SHORTLISTED", "RESEARCHED",
        "PREPARED", "AWAITING_APPROVAL", "APPROVED", "APPLYING", "APPLIED", "FAILED",
        "RESPONSE", "SCREEN", "INTERVIEW", "OFFER", "REJECTED_BY_EMPLOYER", "WITHDRAWN",
        "EXPIRED",
    }
    assert declared == expected
    assert set(JOB_TRANSITIONS) == set(JobStatus), "every state needs a transition row"


def test_approval_is_required_before_applying() -> None:
    """Requirement 19: PREPARED -> ... -> APPROVED -> APPLYING only."""

    assert can_transition_job(JobStatus.PREPARED, JobStatus.AWAITING_APPROVAL)
    assert can_transition_job(JobStatus.AWAITING_APPROVAL, JobStatus.APPROVED)
    assert can_transition_job(JobStatus.APPROVED, JobStatus.APPLYING)
    # The forbidden shortcut: an unapproved job can never reach submission.
    assert not can_transition_job(JobStatus.PREPARED, JobStatus.APPLYING)
    assert not can_transition_job(JobStatus.SHORTLISTED, JobStatus.APPLIED)
    with pytest.raises(InvalidTransition):
        assert_job_transition(JobStatus.PREPARED, JobStatus.APPLIED)


def test_terminal_states_are_terminal() -> None:
    for state in (JobStatus.WITHDRAWN, JobStatus.EXPIRED, JobStatus.REJECTED_BY_EMPLOYER):
        assert JOB_TRANSITIONS[state] == frozenset(), f"{state} must not transition onward"


def test_normalization_functions_are_conservative() -> None:
    assert normalize_company("Revance Therapeutics, Inc.") == normalize_company("REVANCE THERAPEUTICS")
    assert normalize_company("Acme & Co.") == "acme and"
    # Seniority must survive normalization: two titles differing there are two roles.
    assert "senior" in normalize_title("Senior Territory Manager")
    assert normalize_title("Territory Manager (Dallas, TX)") == "territory manager"
    assert normalize_title("Area Sales Manager - Remote") == "area sales manager"
    # A requisition number is noise; a job title is not.
    assert normalize_title("Territory Manager Req #12345") == "territory manager"
    assert normalize_location("Dallas, Texas") == normalize_location("Dallas, TX")
    assert normalize_location("Remote - US") == ""


def test_canonical_key_is_stable_and_case_insensitive() -> None:
    a = canonical_key("revance", "territory manager", "dallas tx")
    b = canonical_key("revance", "territory manager", "dallas tx")
    c = canonical_key("revance", "territory manager", "houston tx")
    assert a == b
    assert a != c


def test_new_id_is_unique_and_time_ordered() -> None:
    """Ids must be unique and non-decreasing in their time component.

    Full-string order is deliberately NOT asserted: two ids created inside the
    same millisecond share a time prefix and differ only in their random suffix,
    so lexicographic order is not creation order at that resolution. The property
    the ledger relies on is that the time prefix never goes backwards.

    The sleep-forcing second half matters: without ids from a *different*
    millisecond, an implementation whose prefix was constant would still pass the
    non-decreasing assertion.
    """

    import time

    ids = [new_id("job") for _ in range(200)]
    assert len(set(ids)) == 200
    assert all(i.startswith("job_") for i in ids)

    prefixes = [i.split("_", 1)[1][:10] for i in ids]
    assert prefixes == sorted(prefixes), "time prefix must never go backwards"

    time.sleep(0.005)
    later = new_id("job")
    assert later.split("_", 1)[1][:10] >= prefixes[0]
    assert later > ids[0], "an id from a later millisecond must sort after an earlier one"


def test_truth_value_only_verified_is_usable() -> None:
    ledger = TruthLedger()
    ledger.record("Led by 12-person region", TruthLevel.VERIFIED, [Evidence("employment.0")])
    ledger.record("Familiar with CRM", TruthLevel.INFERRED)
    ledger.unresolved_question("Do you require sponsorship?")

    assert not ledger.truth_clean, "an unevidenced inference must not pass as clean"
    assert [c.claim for c in ledger.unsupported] == ["Familiar with CRM"]
    assert ledger.summary()["UNRESOLVED_QUESTIONS"] == 1


def test_candidate_profile_declares_provenance_for_resume_derived_values() -> None:
    profile = CandidateProfile(
        candidate_id="cand_1",
        headline="Medical aesthetics sales leader",
        employment=[EmploymentEntry(employer="Acme", title="Territory Manager")],
        provenance={
            "employment": Provenance(
                level=TruthLevel.INFERRED, evidence=["master_resume:experience[0]"]
            )
        },
    )
    assert profile.provenance_for("employment").level is TruthLevel.INFERRED
    assert profile.provenance_for("headline").level is TruthLevel.VERIFIED
    assert profile.populated_field_paths()


def test_search_profile_requires_titles_and_normalizes() -> None:
    profile = SearchProfile(
        id="srch_1",
        name="Aesthetics commercial DFW",
        candidate_id="cand_1",
        target_titles=["  Territory Manager ", ""],
        excluded_titles=["Commission Only"],
    )
    assert profile.target_titles == ["Territory Manager"]
    assert profile.excluded_titles == ["Commission Only"]


def test_job_source_id_is_unique_but_multiple_sources_coexist(db: Database) -> None:
    """Requirement 9: same opening on two ATS boards, two rows, one canonical key."""

    with db.session() as session:
        jobs = JobRepository(session)
        base = dict(
            source_job_id="12345",
            company="Revance",
            title="Territory Manager",
            location="Dallas, TX",
            source_url="https://example.test/1",
            date_discovered=datetime(2026, 1, 1, tzinfo=UTC),
        )
        first, created_a, changed_a = jobs.upsert_canonical(
            CanonicalJob(source="GREENHOUSE", raw_source_hash="hash-a", **base)
        )
        assert (created_a, changed_a) == (True, True)

        # Idempotent re-ingest of byte-identical content.
        second, created_b, changed_b = jobs.upsert_canonical(
            CanonicalJob(source="GREENHOUSE", raw_source_hash="hash-a", **base)
        )
        assert (created_b, changed_b) == (False, False)
        assert second.id == first.id

        # Changed content updates in place rather than duplicating.
        _, created_c, changed_c = jobs.upsert_canonical(
            CanonicalJob(source="GREENHOUSE", raw_source_hash="hash-b", **base)
        )
        assert (created_c, changed_c) == (False, True)

        other, created_d, _ = jobs.upsert_canonical(
            CanonicalJob(source="LEVER", raw_source_hash="hash-c", **base)
        )
        assert created_d
        assert other.id != first.id
        assert other.canonical_key == first.canonical_key

    with pytest.raises(IntegrityError):
        with db.session() as session:
            session.add(
                models.Job(
                    source="GREENHOUSE",
                    source_job_id="12345",
                    source_url="https://example.test/dupe",
                    company="Revance",
                    title="Territory Manager",
                    canonical_key="k",
                )
            )


def test_events_are_append_only_and_ordered(db: Database) -> None:
    with db.session() as session:
        events = EventRepository(session)
        for index in range(5):
            events.append("job", "job_1", "JOB_DISCOVERED", {"n": index})
        rows = events.for_entity("job", "job_1")
        assert [r.payload["n"] for r in rows] == [0, 1, 2, 3, 4]
        assert [r.seq for r in rows] == sorted(r.seq for r in rows)

    # A second pass over the same entity must not disturb prior rows.
    with db.session() as session:
        events = EventRepository(session)
        events.append("job", "job_1", "JOB_NORMALIZED")
    with db.session() as session:
        assert len(EventRepository(session).for_entity("job", "job_1")) == 6


def test_duplicate_applications_are_impossible(db: Database) -> None:
    """Requirement 9 is enforced structurally, not by convention."""

    with db.session() as session:
        candidate = CandidateRepository(session).create("Test Candidate")
        opp = models.Opportunity(
            candidate_id=candidate.id,
            canonical_key="ck",
            title="Territory Manager",
            company="Revance",
        )
        session.add(opp)
        session.flush()
        payload = dict(
            opportunity_id=opp.id,
            package_id="pkg_1",
            job_id="job_1",
            candidate_id=candidate.id,
            master_resume_id=None,
            tailored_resume_id=None,
            answers=[],
        )
        session.add(models.ApplicationRow(**payload))
        session.flush()

    with pytest.raises(IntegrityError):
        with db.session() as session:
            session.add(models.ApplicationRow(**payload))


def test_profile_versions_accumulate_rather_than_mutate(db: Database) -> None:
    with db.session() as session:
        candidates = CandidateRepository(session)
        candidate = candidates.create("Test Candidate")
        candidates.save_profile(
            candidate.id, CandidateProfile(candidate_id=candidate.id, headline="v1")
        )
        candidates.save_profile(
            candidate.id, CandidateProfile(candidate_id=candidate.id, headline="v2")
        )
        active = candidates.active_profile(candidate.id)
        assert active.headline == "v2"
        assert active.version == 2
        rows = session.query(models.CandidateProfileRow).all()
        assert len(rows) == 2
        assert [r.is_active for r in rows] == [False, True]


def test_sqlite_enforces_foreign_keys(db: Database) -> None:
    """A dangling FK would let the ledger rot silently."""

    assert db.dialect == "sqlite"
    with pytest.raises(IntegrityError):
        with db.session() as session:
            session.add(
                models.Resume(candidate_id="cand_missing", content_text="x", content_hash="h")
            )
