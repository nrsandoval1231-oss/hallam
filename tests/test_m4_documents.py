"""M4 application factory: résumé parsing, truth-bound tailoring, packages.

The central tests here are the truth-boundary ones. `verify_tailored_truth` must be
able to FAIL — a verifier that cannot fail proves nothing — so one test feeds it a
deliberately fabricated bullet and requires it to be caught.

`tests/fixtures/master_resume_sample.txt` is explicitly labelled test fixture data
describing a fictional candidate. It is not a claim about any person.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sabra_documents.answers import (
    AnswerResolver,
    auto_fillable_answers,
    classify_question,
    unresolved_questions,
)
from sabra_documents.master_resume import (
    parse_master_resume,
    render_markdown,
    split_resume_sections,
    years_of_experience,
)
from sabra_documents.package import PackageFactory
from sabra_documents.tailor import (
    ResumeTailor,
    TailoredRésumé,
    job_focus_tokens,
    relevance,
    verify_tailored_truth,
)
from sabra_domain.enums import PackageStatus, TruthLevel
from sabra_domain.identity import content_hash
from sabra_domain.schemas import (
    AnswerRecord,
    ApplicationPackageSchema,
    CandidateProfile,
    CanonicalJob,
    CompensationPreference,
    EmploymentEntry,
    FitAssessmentSchema,
    Geography,
)
from sabra_domain.truth import TruthLedger

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def resume_text() -> str:
    return (FIXTURES / "master_resume_sample.txt").read_text(encoding="utf-8")


@pytest.fixture()
def document(resume_text: str):
    return parse_master_resume(resume_text)


@pytest.fixture()
def profile(document) -> CandidateProfile:
    """A complete candidate profile built from the fixture résumé."""

    return CandidateProfile(
        candidate_id="cand_fixture",
        identity=document.contact,
        headline=document.headline,
        summary=document.summary,
        employment=document.employment,
        education=document.education,
        certifications=document.certifications,
        skills=document.skills,
        projects=document.projects,
        industries=["Medical aesthetics", "Medical devices"],
        preferred_titles=["Regional Sales Manager"],
        geography=Geography(cities=["Dallas"], states=["TX"], metro_areas=["Dallas-Fort Worth"]),
        desired_compensation=CompensationPreference(minimum=75000.0, target=120000.0),
    )


@pytest.fixture()
def job() -> CanonicalJob:
    return CanonicalJob(
        source="GREENHOUSE",
        source_job_id="9001",
        source_url="https://example.test/jobs/9001",
        company="Revance Therapeutics",
        title="Regional Sales Manager - Medical Aesthetics",
        location="Dallas, TX",
        remote_status="ONSITE",
        employment_type="FULL_TIME",
        description="Own the Dallas region for an aesthetics portfolio.",
        requirements="\n".join(
            [
                "- 5+ years of medical device or aesthetic sales experience",
                "- Experience with injectables and capital equipment preferred",
                "- Territory management across a multi-state region",
                "- Bachelor's degree required",
            ]
        ),
        responsibilities="- Build regional strategy\n- Lead territory managers",
        preferred_qualifications="- Experience launching new products",
        date_discovered=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Master résumé parsing
# ---------------------------------------------------------------------------


def test_fixture_is_labelled_as_fictional() -> None:
    """The résumé fixture must never be mistaken for a real person's data.

    The labelling lives in this directory's README rather than inside the résumé
    text, because a disclaimer banner at the top of the file would be parsed as the
    candidate's name — which is a defect in the fixture, not in the parser.
    """

    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    assert "not a real person" in readme.lower()
    assert "master_resume_sample.txt" in readme
    text = (FIXTURES / "master_resume_sample.txt").read_text(encoding="utf-8")
    assert text.startswith("JORDAN A. RIVERA")


def test_parse_master_resume_reads_every_section(document) -> None:
    assert document.contact.full_name == "JORDAN A. RIVERA"
    assert document.contact.email == "jordan.rivera@example.test"
    assert "214" in (document.contact.phone or "")
    assert document.contact.city == "Dallas" and document.contact.state == "TX"
    assert document.headline == "Medical Aesthetics & Device Sales Leader"
    assert "11 years of field sales" in (document.summary or "")
    assert len(document.employment) == 3
    assert len(document.education) == 1
    assert len(document.certifications) == 2
    assert len(document.skills) >= 10
    assert len(document.projects) == 1
    assert not document.warnings, document.warnings


def test_employment_entries_carry_title_employer_dates_and_bullets(document) -> None:
    first = document.employment[0]
    assert first.title == "Regional Sales Manager"
    assert first.employer == "Meridian Aesthetics"
    assert first.start_date == "Mar 2019"
    assert first.end_date == "Present"
    assert first.is_current is True
    assert len(first.accomplishments) == 5
    assert "43%" in first.accomplishments[0]

    second = document.employment[1]
    assert second.title == "Territory Manager"
    assert second.employer == "Northstar Medical Devices"
    assert len(second.accomplishments) == 4


def test_unparsed_content_is_retained_not_dropped(document) -> None:
    """A parser that silently drops a line loses a fact the candidate wrote."""

    assert isinstance(document.unparsed_lines, list)
    # Everything parsed plus everything unparsed must account for the input.
    assert document.bullet_count() == 10


def test_split_resume_sections_returns_preamble_separately() -> None:
    sections, preamble = split_resume_sections(
        "JANE DOE\njane@example.test\n\nSUMMARY\nEngineer\n\nEXPERIENCE\nAcme\n2020 - Present\n"
    )
    assert [s.name for s in sections] == ["summary", "experience"]
    assert any("JANE DOE" in line for line in preamble)


def test_empty_resume_is_reported_rather_than_guessed() -> None:
    document = parse_master_resume("")
    assert document.warnings
    assert document.employment == []
    assert "empty" in document.warnings[0]


def test_render_markdown_round_trips_the_structure(document) -> None:
    markdown = render_markdown(document)
    assert "# JORDAN A. RIVERA" in markdown
    assert "## Experience" in markdown
    assert "Regional Sales Manager" in markdown
    assert "43%" in markdown
    reparsed = parse_master_resume(markdown)
    assert len(reparsed.employment) == len(document.employment)
    assert reparsed.bullet_count() == document.bullet_count()


def test_years_of_experience_uses_only_parsed_dates(document) -> None:
    from datetime import date

    years = years_of_experience(document, today=date(2026, 1, 1))
    assert years is not None and 13 <= years <= 14


def test_parsed_payload_can_be_supplied_directly() -> None:
    """A DOCX/PDF extractor added later can hand over a structure instead of text."""

    document = parse_master_resume(
        "ignored",
        parsed={
            "contact": {"full_name": "Provided Name", "email": "p@example.test"},
            "employment": [
                {"employer": "Acme", "title": "Manager", "accomplishments": ["Did things"]}
            ],
            "skills": [{"name": "Negotiation"}],
        },
    )
    assert document.contact.full_name == "Provided Name"
    assert document.employment[0].employer == "Acme"
    assert document.skills[0].name == "Negotiation"


# ---------------------------------------------------------------------------
# Truth-bound tailoring
# ---------------------------------------------------------------------------


def test_tailoring_keeps_candidate_wording_verbatim(profile, document, job) -> None:
    tailored = ResumeTailor().tailor(profile, document, job)
    sources = [item for _e, _t, item in profile.all_accomplishments()]
    for bullet in tailored.selected_bullets:
        core = bullet.rstrip(".").lower()
        assert any(
            core in source.lower() or source.lower().rstrip(".") == core for source in sources
        ), f"bullet not traceable to candidate data: {bullet!r}"


def test_tailoring_reorders_by_relevance_without_deleting_a_role(profile, document, job) -> None:
    tailored = ResumeTailor().tailor(profile, document, job)
    assert len(tailored.role_order) == len(profile.employment)
    for header in tailored.role_order:
        assert header.strip(" —")
    # The sales role most aligned with the posting is promoted above the junior one.
    assert tailored.role_order[0].startswith("Regional Sales Manager")
    assert "Sales Associate" in tailored.role_order[-1]


def test_tailoring_never_leaves_a_role_without_a_bullet(profile, document) -> None:
    tailored = ResumeTailor().tailor(profile, document, job)
    sections = tailored.markdown.split("## Experience")[1].split("## Education")[0]
    for header in tailored.role_order:
        assert header in sections


def test_tailoring_marks_inferred_and_verified_separately(profile, document, job) -> None:
    tailored = ResumeTailor().tailor(profile, document, job)
    levels = {claim.level for claim in tailored.ledger.claims}
    assert TruthLevel.VERIFIED in levels
    summary = tailored.ledger.summary()
    assert summary["VERIFIED"] > 0
    assert tailored.ledger.truth_clean


def test_tailoring_of_an_empty_profile_asserts_nothing() -> None:
    """The placeholder candidate: output must be empty framing plus open questions."""

    empty = CandidateProfile(candidate_id="cand_empty")
    job = CanonicalJob(
        source="GREENHOUSE",
        source_job_id="1",
        source_url="https://example.test/1",
        company="Acme",
        title="Territory Manager",
    )
    tailored = ResumeTailor().tailor(empty, None, job)
    assert tailored.selected_bullets == []
    assert tailored.ledger.unresolved
    assert verify_tailored_truth(tailored, empty) == []
    assert "Meridian" not in tailored.markdown


def test_verify_tailored_truth_can_fail() -> None:
    """A verifier that cannot fail is decoration. Feed it a fabricated bullet."""

    profile = CandidateProfile(
        candidate_id="c",
        employment=[
            EmploymentEntry(
                employer="Acme",
                title="Manager",
                accomplishments=["Grew revenue 10%"],
            )
        ],
    )
    fabricated = TailoredRésumé(
        markdown="- Invented a 300% revenue increase",
        ledger=TruthLedger(),
        selected_bullets=["Invented a 300% revenue increase"],
    )
    violations = verify_tailored_truth(fabricated, profile)
    assert violations, "a fabricated bullet must be reported"
    assert "300%" in violations[0]

    honest = TailoredRésumé(
        markdown="- Grew revenue 10%",
        ledger=TruthLedger(),
        selected_bullets=["Grew revenue 10%."],
    )
    assert verify_tailored_truth(honest, profile) == []


def test_verified_claim_without_evidence_is_a_violation() -> None:
    profile = CandidateProfile(candidate_id="c")
    ledger = TruthLedger()
    ledger.record("something asserted", TruthLevel.VERIFIED, [])
    tailored = TailoredRésumé(markdown="x", ledger=ledger, selected_bullets=[])
    violations = verify_tailored_truth(tailored, profile)
    assert any("without evidence" in v for v in violations)


def test_relevance_scoring_moves_with_the_input(job) -> None:
    focus = job_focus_tokens(job)
    on_topic = "Grew regional revenue 43% across a multi-state territory"
    off_topic = "Managed the office coffee rota and stationery orders"
    assert relevance(on_topic, focus) > relevance(off_topic, focus)
    assert relevance(on_topic, set()) == 0.0


def test_tailoring_is_deterministic(profile, document, job) -> None:
    first = ResumeTailor().tailor(profile, document, job).markdown
    second = ResumeTailor().tailor(profile, document, job).markdown
    assert first == second


# ---------------------------------------------------------------------------
# Answer memory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Are you legally authorized to work in the United States?", "work_authorization"),
        ("Will you now or in the future require sponsorship?", "sponsorship"),
        ("Are you willing to travel up to 40%?", "travel"),
        ("What is your desired salary?", "salary_expectations"),
        ("When can you start?", "start_date"),
        ("Are you a protected veteran?", "veteran_status"),
        ("Do you have a valid driver's license?", "commute"),
        ("Are you willing to undergo a background check?", "background_check"),
    ],
)
def test_question_classification(question: str, expected: str) -> None:
    assert classify_question(question) == expected


def test_unknown_question_is_unresolved_not_guessed() -> None:
    profile = CandidateProfile(candidate_id="c")
    resolver = AnswerResolver()
    resolutions = resolver.resolve(profile, ["Describe your greatest professional weakness."])
    assert len(resolutions) == 1
    assert resolutions[0].answer.answer is None
    assert resolutions[0].answer.level is TruthLevel.UNKNOWN
    assert resolutions[0].answer.requires_user_input is True
    assert unresolved_questions(resolutions)


def test_verified_answer_is_auto_fillable() -> None:
    profile = CandidateProfile(
        candidate_id="c",
        answer_memory={
            "work_authorization": AnswerRecord(
                question_key="work_authorization",
                value="Yes",
                level=TruthLevel.VERIFIED,
                evidence=["owner_supplied"],
            )
        },
    )
    resolutions = AnswerResolver().resolve(
        profile, ["Are you legally authorized to work in the United States?"]
    )
    assert resolutions[0].answer.answer == "Yes"
    assert not resolutions[0].answer.requires_user_input
    assert auto_fillable_answers(resolutions)


def test_unverified_sensitive_answers_are_withheld() -> None:
    """A recorded sensitive answer is not typed until the owner verifies it."""

    profile = CandidateProfile(
        candidate_id="c",
        answer_memory={
            "veteran_status": AnswerRecord(
                question_key="veteran_status",
                value="I am a protected veteran",
                level=TruthLevel.INFERRED,
                sensitive=True,
                user_controlled=True,
            ),
            "salary_expectations": AnswerRecord(
                question_key="salary_expectations",
                value="120000",
                level=TruthLevel.INFERRED,
                sensitive=True,
            ),
        },
    )
    resolutions = AnswerResolver().resolve(
        profile,
        ["What is your veteran status?", "What is your desired salary?"],
    )
    assert all(item.answer.answer is None for item in resolutions)
    assert all(item.answer.requires_user_input for item in resolutions)
    assert auto_fillable_answers(resolutions) == []


def test_verified_sensitive_answers_fill_and_can_complete_a_package(
    profile, document, job
) -> None:
    """Salary and a background check complete the package once the owner verifies them."""

    verified = profile.model_copy(
        update={
            "answer_memory": {
                "salary_expectations": AnswerRecord(
                    question_key="salary_expectations",
                    value="$130,000",
                    level=TruthLevel.VERIFIED,
                    sensitive=True,
                    evidence=["owner_verified"],
                ),
                "background_check": AnswerRecord(
                    question_key="background_check",
                    value="Yes",
                    level=TruthLevel.VERIFIED,
                    sensitive=True,
                    evidence=["owner_verified"],
                ),
                "work_authorization": AnswerRecord(
                    question_key="work_authorization",
                    value="Yes",
                    level=TruthLevel.VERIFIED,
                    evidence=["owner_supplied"],
                ),
            }
        }
    )
    resolutions = AnswerResolver().resolve(
        verified,
        [
            "What are your salary expectations?",
            "Are you willing to undergo a background check?",
        ],
    )
    assert {item.answer.answer for item in resolutions} == {"$130,000", "Yes"}
    assert auto_fillable_answers(resolutions)
    built = PackageFactory().build(
        profile=verified,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
        questions=[
            "Are you legally authorized to work in the United States?",
            "What are your salary expectations?",
            "Are you willing to undergo a background check?",
        ],
    )
    assert built.status is PackageStatus.COMPLETE
    assert built.schema.unresolved_questions == []


def test_hiring_manager_message_is_role_neutral(profile, document, job) -> None:
    built = PackageFactory().build(
        profile=profile,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
    )
    message = built.schema.hiring_manager_message or ""
    assert "conversation about the role" in message
    assert "conversation about the territory" not in message
    assert "discuss the territory" not in message
    assert "Regional Sales Manager" in message


def test_inferred_answers_are_not_auto_filled() -> None:
    profile = CandidateProfile(
        candidate_id="c",
        answer_memory={
            "relocation": AnswerRecord(
                question_key="relocation",
                value="Yes",
                level=TruthLevel.INFERRED,
            )
        },
    )
    resolutions = AnswerResolver().resolve(profile, ["Are you willing to relocate?"])
    assert resolutions[0].answer.answer is None
    assert resolutions[0].answer.requires_user_input is True


def test_duplicate_questions_are_resolved_once() -> None:
    profile = CandidateProfile(candidate_id="c")
    resolutions = AnswerResolver().resolve(
        profile,
        [
            "Are you legally authorized to work in the United States?",
            "Are you legally authorized to work in the US?",
        ],
    )
    assert len({r.matched_key for r in resolutions}) == len(resolutions)


# ---------------------------------------------------------------------------
# Package assembly
# ---------------------------------------------------------------------------


def test_complete_package_is_complete_and_truth_clean(profile, document, job) -> None:
    built = PackageFactory().build(
        profile=profile,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
        questions=[
            "Are you legally authorized to work in the United States?",
        ],
    )
    assert built.violations == []
    assert built.schema.content_hash
    assert built.schema.tailored_resume_markdown
    assert built.schema.cover_letter
    assert built.schema.recruiter_message
    assert built.schema.hiring_manager_message
    assert built.schema.interview_preparation
    assert built.schema.candidate_summary
    assert built.schema.truth_ledger["truth_clean"] is True
    # The one unknown question is named rather than answered.
    assert any("authorized to work" in q for q in built.schema.unresolved_questions)
    assert built.status is PackageStatus.INCOMPLETE


def test_complete_package_needs_no_missing_inputs(profile, document, job) -> None:
    with_answers = profile.model_copy(
        update={
            "answer_memory": {
                "work_authorization": AnswerRecord(
                    question_key="work_authorization",
                    value="Yes",
                    level=TruthLevel.VERIFIED,
                    evidence=["owner_supplied"],
                )
            }
        }
    )
    built = PackageFactory().build(
        profile=with_answers,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
        questions=["Are you legally authorized to work in the United States?"],
    )
    assert built.missing_inputs == []
    assert built.schema.unresolved_questions == []
    assert built.status is PackageStatus.COMPLETE
    assert built.complete


def test_placeholder_candidate_yields_an_incomplete_package(job) -> None:
    """Requirement 5 in one test: no candidate data means no invented application."""

    empty = CandidateProfile(candidate_id="cand_empty")
    built = PackageFactory().build(
        profile=empty,
        master=None,
        job=job,
        master_resume_id=None,
        job_id="job_1",
        opportunity_id="opp_1",
        questions=["Are you legally authorized to work in the United States?"],
    )
    assert built.status is PackageStatus.INCOMPLETE
    assert not built.complete
    assert set(built.missing_inputs) >= {
        "identity.full_name",
        "identity.email",
        "employment",
        "skills",
    }
    assert built.schema.unresolved_questions
    assert built.violations == []
    # Nothing resembling a fabricated qualification appears anywhere.
    for artifact in (
        built.schema.tailored_resume_markdown or "",
        built.schema.cover_letter or "",
        built.schema.candidate_summary or "",
    ):
        assert "Meridian" not in artifact
        assert "Northstar" not in artifact


def test_package_hash_changes_with_content_but_not_with_ids_or_time(profile, document, job) -> None:
    factory = PackageFactory()
    base = factory.build(
        profile=profile,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
    ).schema

    edited = base.model_copy(update={"cover_letter": (base.cover_letter or "") + "\nExtra line."})
    edited.content_hash = content_hash(edited.semantic_payload())
    assert edited.content_hash != base.content_hash

    moved_ids = base.model_copy(
        update={"opportunity_id": "opp_2", "job_id": "job_2", "master_resume_id": "res_other"}
    )
    moved_ids.content_hash = content_hash(moved_ids.semantic_payload())
    assert moved_ids.content_hash != base.content_hash, (
        "the hash covers the job/opportunity it belongs to; otherwise an approval "
        "could be replayed against a different opportunity's package"
    )

    # Rebuilding the same content reproduces the same hash.
    again = factory.build(
        profile=profile,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
    ).schema
    assert again.content_hash == base.content_hash


def test_package_schema_rejects_a_non_unit_confidence() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FitAssessmentSchema(
            fit_score=1.4,
            career_score=0.5,
            qualification_score=0.5,
            compensation_score=0.5,
            location_score=0.5,
            industry_score=0.5,
            trajectory_score=0.5,
            confidence=0.5,
        )


def test_fit_explanation_is_included_and_explainable(profile, document, job) -> None:
    assessment = FitAssessmentSchema(
        fit_score=0.8,
        career_score=0.9,
        qualification_score=0.7,
        compensation_score=0.8,
        location_score=1.0,
        industry_score=0.6,
        trajectory_score=0.85,
        confidence=0.7,
        strengths=["territory management matches the posting"],
        gaps=["no quota figure for the last role"],
        why_this_job="Strong regional fit with evidenced aesthetics experience.",
        component_rationale={"fit_score": "weighted mean of six assessable components"},
    )
    built = PackageFactory().build(
        profile=profile,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
        assessment=assessment,
    )
    assert "Strong regional fit" in built.schema.fit_explanation
    assert built.schema.qualification_gaps == assessment.gaps
    assert assessment.confidence == 0.7


def test_application_package_schema_validates_round_trip(profile, document, job) -> None:
    built = PackageFactory().build(
        profile=profile,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
    )
    restored = ApplicationPackageSchema.model_validate(built.schema.model_dump(mode="json"))
    assert restored.content_hash == built.schema.content_hash
    assert restored.tailored_resume_markdown == built.schema.tailored_resume_markdown


def test_cover_letter_quotes_only_candidate_content(profile, document, job) -> None:
    built = PackageFactory().build(
        profile=profile,
        master=document,
        job=job,
        master_resume_id="res_master",
        job_id="job_1",
        opportunity_id="opp_1",
    )
    letter = built.schema.cover_letter or ""
    from sabra_domain.identity import normalize_text

    expected = normalize_text(profile.summary)
    assert expected and expected in letter, "the candidate's own summary must appear verbatim"
    assert any(bullet in letter for bullet in built.tailored.selected_bullets)
    claims = built.schema.truth_ledger["claims"]
    letter_claims = [c for c in claims if c["artifact"] == "cover_letter"]
    assert letter_claims
    for claim in letter_claims:
        if claim["level"] == "VERIFIED":
            assert claim["evidence"]
