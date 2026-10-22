"""Shared builders for the model-assisted generation tests (no tests of its own).

Everything here is fixture data about a fictional candidate (see
`tests/fixtures/README.md`). No test in the p2c suite may reach the network: every
model is a `FakeProvider`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sabra_documents.generate import (
    CoverLetterDraft,
    FitNarrative,
    RoleSelection,
    TailoringProposal,
    index_roles,
)
from sabra_documents.master_resume import parse_master_resume
from sabra_documents.truth_gate import AuditedClaim, ClaimAudit, ClaimSupport
from sabra_domain.schemas import CandidateProfile, CanonicalJob, FitAssessmentSchema
from sabra_llm.fake import FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"

REVENUE = "Grew regional revenue 43% over three years across a 6-state territory."
TEAM = "Built and led a team of 7 territory managers covering Texas, Oklahoma and Louisiana."
CLINICS = "Sold capital equipment into 120 clinics with an average deal size of $85,000."
SUMMARY_QUOTE = "Commercial leader with 11 years of field sales experience across medical aesthetics"


def resume_text() -> str:
    return (FIXTURES / "master_resume_sample.txt").read_text(encoding="utf-8")


def document():
    return parse_master_resume(resume_text())


def profile() -> CandidateProfile:
    doc = document()
    return CandidateProfile(
        candidate_id="cand_fixture",
        identity=doc.contact,
        headline=doc.headline,
        summary=doc.summary,
        employment=doc.employment,
        education=doc.education,
        certifications=doc.certifications,
        skills=doc.skills,
        projects=doc.projects,
        industries=["Medical aesthetics", "Medical devices"],
    )


def job(**overrides) -> CanonicalJob:
    base = dict(
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
    base.update(overrides)
    return CanonicalJob(**base)


def assessment() -> FitAssessmentSchema:
    return FitAssessmentSchema(
        fit_score=0.8,
        career_score=0.8,
        qualification_score=0.8,
        compensation_score=0.5,
        location_score=1.0,
        industry_score=1.0,
        trajectory_score=0.7,
        confidence=0.7,
        strengths=["Direct device sales background"],
        gaps=["No product-launch evidence beyond one portfolio"],
        why_this_job="A close match on territory and category.",
        engine="heuristic",
    )


# -- model outputs ---------------------------------------------------------


def good_proposal(prof: CandidateProfile | None = None) -> TailoringProposal:
    prof = prof or profile()
    roles = index_roles(prof)
    selections = []
    for role in roles:
        ids = [bid for bid, _text in role.bullets[:2]]
        selections.append(RoleSelection(role_id=role.role_id, bullet_ids=ids))
    return TailoringProposal(
        headline="Medical aesthetics and device sales leader",
        summary=(
            "Field sales leader with 11 years across medical aesthetics and medical devices, "
            "who grew regional revenue 43% and led a team of 7 territory managers."
        ),
        roles=selections,
        skills=["Aesthetic injectables", "Capital equipment sales", "Territory management"],
        strengths=["Direct experience selling aesthetic injectables and capital equipment."],
        gaps=["The record shows one portfolio launch and no wider launch programme."],
    )


GOOD_LETTER_BODY = (
    "I am applying for the Regional Sales Manager - Medical Aesthetics role at Revance "
    "Therapeutics. My field sales background in medical aesthetics and medical devices maps "
    "directly onto it.\n\n"
    "At Meridian Aesthetics I grew regional revenue 43% over three years across a 6-state "
    "territory and built and led a team of 7 territory managers. At Northstar Medical Devices "
    "I sold capital equipment into 120 clinics.\n\n"
    "I would welcome a conversation about the role."
)


def good_letter() -> CoverLetterDraft:
    return CoverLetterDraft(
        body=GOOD_LETTER_BODY,
        claims=[
            ClaimSupport(
                claim="Grew regional revenue 43% over three years", excerpt=REVENUE.rstrip(".")
            ),
            ClaimSupport(claim="Led a team of 7 territory managers", excerpt=TEAM.rstrip(".")),
            ClaimSupport(
                claim="Sold capital equipment into 120 clinics",
                excerpt="Sold capital equipment into 120 clinics",
            ),
        ],
    )


def good_narrative() -> FitNarrative:
    return FitNarrative(
        why_this_job=(
            "The role is a regional aesthetics sales position in Dallas, which matches the "
            "candidate's territory and category. The candidate already leads a regional team."
        ),
        gap_analysis=(
            "The posting asks for product-launch experience; the record shows one injectable "
            "portfolio launch, so be ready to discuss it openly."
        ),
    )


def audit_for(*pairs: tuple[str, str]) -> ClaimAudit:
    """An audit that supports each (claim, quote) pair."""

    return ClaimAudit(
        claims=[
            AuditedClaim(claim=claim, kind="candidate_fact", supported=True, evidence=quote)
            for claim, quote in pairs
        ]
    )


def claim(kind: str, text: str, quote: str | None = None, supported: bool = True) -> AuditedClaim:
    return AuditedClaim(claim=text, kind=kind, supported=supported, evidence=quote)


def good_tailoring_audit() -> ClaimAudit:
    return ClaimAudit(
        claims=[
            claim(
                "candidate_fact",
                "11 years of field sales experience across medical aesthetics and medical devices",
                SUMMARY_QUOTE,
            ),
            claim("candidate_fact", "grew regional revenue 43%", "Grew regional revenue 43% over three years"),
            claim("candidate_fact", "led a team of 7 territory managers", TEAM.rstrip(".")),
            claim("candidate_fact", "sells aesthetic injectables", "Aesthetic injectables"),
            claim("candidate_fact", "sold capital equipment", "Sold capital equipment into 120 clinics"),
            claim("gap", "one portfolio launch is shown and no wider launch programme"),
        ]
    )


def good_letter_audit() -> ClaimAudit:
    return ClaimAudit(
        claims=[
            claim(
                "job_fact",
                "the role is Regional Sales Manager - Medical Aesthetics at Revance Therapeutics",
                "Regional Sales Manager - Medical Aesthetics",
            ),
            claim(
                "candidate_fact",
                "field sales background in medical aesthetics and medical devices",
                SUMMARY_QUOTE,
            ),
            claim(
                "candidate_fact",
                "grew regional revenue 43% over three years across a 6-state territory",
                REVENUE.rstrip("."),
            ),
            claim("candidate_fact", "built and led a team of 7 territory managers", TEAM.rstrip(".")),
            claim(
                "candidate_fact",
                "sold capital equipment into 120 clinics",
                "Sold capital equipment into 120 clinics",
            ),
            claim("framing", "would welcome a conversation about the role"),
        ]
    )


def good_narrative_audit() -> ClaimAudit:
    return ClaimAudit(
        claims=[
            claim("job_fact", "the role is a regional aesthetics sales position in Dallas", "Own the Dallas region for an aesthetics portfolio"),
            claim("candidate_fact", "the candidate's territory and category match", "Territory management"),
            claim("candidate_fact", "the candidate already leads a regional team", TEAM.rstrip(".")),
            claim("job_fact", "the posting describes owning the Dallas region", "Own the Dallas region"),
            claim("candidate_fact", "the record shows an injectable portfolio launch", "Launched a new injectable portfolio into 180 clinics"),
        ]
    )


def script_all_good(fake: FakeProvider, prof: CandidateProfile | None = None) -> FakeProvider:
    fake.script("tailoring", good_proposal(prof))
    fake.script("audit:tailoring", good_tailoring_audit())
    fake.script("cover_letter", good_letter())
    fake.script("audit:cover_letter", good_letter_audit())
    fake.script("fit_narrative", good_narrative())
    fake.script("audit:narrative", good_narrative_audit())
    return fake
