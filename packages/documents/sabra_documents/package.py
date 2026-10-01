"""Application package assembly (requirements 15, 31).

Builds the reviewable artifact set and, crucially, decides whether it is
*complete*. A package is only `COMPLETE` when every generated artifact is traceable
to candidate data and no consequential question is unanswered. Anything else comes
out `INCOMPLETE` with the open questions named — because the failure mode to avoid
is a package that looks finished and asserts things the candidate never said.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sabra_domain.enums import PackageStatus, TruthLevel
from sabra_domain.identity import content_hash, normalize_text
from sabra_domain.schemas import (
    ApplicationPackageSchema,
    CandidateProfile,
    CanonicalJob,
    FitAssessmentSchema,
)
from sabra_domain.truth import Evidence, TruthLedger

from .answers import AnswerResolver, Resolution, unresolved_questions
from .master_resume import ResumeDocument
from .tailor import ResumeTailor, TailoredRésumé, verify_tailored_truth


@dataclass(slots=True)
class BuiltPackage:
    schema: ApplicationPackageSchema
    tailored: TailoredRésumé
    resolutions: list[Resolution] = field(default_factory=list)
    status: PackageStatus = PackageStatus.DRAFT
    violations: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return (
            self.status is PackageStatus.COMPLETE
            and not self.violations
            and not self.missing_inputs
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "complete": self.complete,
            "content_hash": self.schema.content_hash,
            "unresolved_questions": self.schema.unresolved_questions,
            "missing_inputs": self.missing_inputs,
            "truth_violations": self.violations,
            "truth_summary": (self.schema.truth_ledger or {}).get("summary", {}),
            "tailoring": self.tailored.as_dict(),
        }


def _provenance_verified(profile: CandidateProfile, path: str) -> bool:
    """Whether a profile path is allowed onto a form.

    Explicit INFERRED or UNKNOWN provenance (a résumé import, a placeholder)
    blocks the copy. No provenance entry means the object was supplied directly
    and its populated values are the owner's.
    """

    record = profile.provenance.get(path)
    if record is None:
        return True
    return record.level is TruthLevel.VERIFIED


def _verified_section(profile: CandidateProfile, name: str) -> list[Any]:
    if not _provenance_verified(profile, name):
        return []
    return list(getattr(profile, name) or [])


def _verified_identity(profile: CandidateProfile) -> dict[str, str | None]:
    """Contact fields that may be typed. Never the schema's default country alone."""

    identity = profile.identity

    def take(field: str) -> str | None:
        if not _provenance_verified(profile, f"identity.{field}"):
            return None
        value = getattr(identity, field, None)
        if value in (None, ""):
            return None
        return str(value)

    country = take("country")
    # ContactInfo defaults country to "USA" even when the person supplied nothing.
    # That default is not a statement. Use it only beside a verified location, or
    # when the owner set provenance on the country itself.
    if country and "identity.country" not in profile.provenance and not (
        take("city") or take("state")
    ):
        country = None
    if country in {"USA", "US"}:
        country = "United States"
    return {
        "name": take("full_name"),
        "email": take("email"),
        "phone": take("phone"),
        "city": take("city"),
        "state": take("state"),
        "postal_code": take("postal_code"),
        "country": country,
        "linkedin": take("linkedin_url"),
    }


# Answer-memory keys that may fill a blank contact field. The profile field name
# is the dict key; the answer key is what the owner records.
_CONTACT_ANSWER_KEYS = (
    ("phone", "phone"),
    ("city", "city"),
    ("state", "state_residence"),
    ("postal_code", "postal_code"),
    ("country", "country"),
    ("street_address", "street_address"),
)


def _verified_answer_value(profile: CandidateProfile, key: str) -> str | None:
    record = profile.answer_memory.get(key)
    if record is None or record.level is not TruthLevel.VERIFIED:
        return None
    if record.value in (None, ""):
        return None
    text = str(record.value).strip()
    return text or None


def _package_contact(profile: CandidateProfile) -> dict[str, str | None]:
    """Contact values copied onto a package.

    Precedence: a verified profile identity field wins when both it and a
    verified answer exist. Verified answer memory fills a field only when the
    profile value is missing. Unverified answers are never used. Street address
    has no profile field, so it comes only from a verified `street_address` answer.
    """

    identity = _verified_identity(profile)
    contact: dict[str, str | None] = {
        "name": identity.get("name"),
        "email": identity.get("email"),
        "phone": identity.get("phone"),
        "city": identity.get("city"),
        "state": identity.get("state"),
        "postal_code": identity.get("postal_code"),
        "country": identity.get("country"),
        "street_address": None,
        "linkedin": identity.get("linkedin"),
    }
    for contact_field, answer_key in _CONTACT_ANSWER_KEYS:
        if contact.get(contact_field):
            continue
        contact[contact_field] = _verified_answer_value(profile, answer_key)
    if contact.get("country") in {"USA", "US"}:
        contact["country"] = "United States"
    return contact


def _closing_line(profile: CandidateProfile) -> str:
    name = profile.identity.full_name or "the candidate"
    return f"Sincerely,\n{name}"


class PackageFactory:
    """Assembles one application package from profile + master résumé + job."""

    def __init__(
        self,
        *,
        tailor: ResumeTailor | None = None,
        resolver: AnswerResolver | None = None,
        include_cover_letter: bool = True,
    ) -> None:
        self.tailor = tailor or ResumeTailor()
        self.resolver = resolver or AnswerResolver()
        self.include_cover_letter = include_cover_letter

    def build(
        self,
        *,
        profile: CandidateProfile,
        master: ResumeDocument | None,
        job: CanonicalJob | Any,
        master_resume_id: str | None,
        job_id: str,
        opportunity_id: str,
        assessment: FitAssessmentSchema | None = None,
        questions: list[str] | None = None,
        research: dict[str, Any] | None = None,
    ) -> BuiltPackage:
        ledger = TruthLedger()
        tailored = self.tailor.tailor(profile, master, job, assessment)

        # The tailored résumé's ledger is folded into the package ledger so the audit
        # trail covers the whole artifact set, not just the cover letter.
        for claim in tailored.ledger.claims:
            ledger.claims.append(claim)
        for question in tailored.ledger.unresolved:
            ledger.unresolved_question(question)

        violations = verify_tailored_truth(tailored, profile)
        missing: list[str] = []
        if not profile.identity.full_name:
            missing.append("identity.full_name")
        if not profile.identity.email:
            missing.append("identity.email")
        if not profile.employment:
            missing.append("employment")
        if not profile.skills:
            missing.append("skills")

        resolutions = self.resolver.resolve(profile, questions or [])
        for question in unresolved_questions(resolutions):
            ledger.unresolved_question(question)

        cover_letter = None
        if self.include_cover_letter:
            cover_letter = self._cover_letter(profile, job, tailored, ledger)

        answers = [r.answer for r in resolutions]
        gaps = list(assessment.gaps) if assessment else []
        warnings = list(tailored.ledger.unresolved)
        if violations:
            warnings.append(
                f"{len(violations)} generated line(s) could not be traced to candidate data"
            )
        if missing:
            warnings.append("candidate profile is missing required fields: " + ", ".join(missing))

        identity = _package_contact(profile)
        schema = ApplicationPackageSchema(
            opportunity_id=opportunity_id,
            candidate_id=profile.candidate_id,
            job_id=job_id,
            master_resume_id=master_resume_id,
            tailored_resume_markdown=tailored.markdown,
            cover_letter=cover_letter,
            candidate_summary=self._candidate_summary(profile, job, tailored),
            fit_explanation=self._fit_explanation(assessment, job),
            qualification_gaps=gaps,
            answers=answers,
            applicant_name=identity.get("name"),
            applicant_email=identity.get("email"),
            applicant_phone=identity.get("phone"),
            applicant_city=identity.get("city"),
            applicant_state=identity.get("state"),
            applicant_postal_code=identity.get("postal_code"),
            applicant_country=identity.get("country"),
            applicant_street_address=identity.get("street_address"),
            applicant_linkedin=identity.get("linkedin"),
            employment=_verified_section(profile, "employment"),
            education=_verified_section(profile, "education"),
            recruiter_message=self._recruiter_message(profile, job, tailored),
            hiring_manager_message=self._hiring_manager_message(profile, job, tailored, assessment),
            interview_preparation=self._interview_prep(job, assessment, profile),
            unresolved_questions=list(ledger.unresolved),
            warnings=warnings,
            truth_ledger=ledger.as_dict(),
            engine="deterministic",
        )
        # The hash covers content only — never ids or timestamps — so an approval
        # bound to it cannot be replayed against edited content.
        schema.content_hash = content_hash(schema.semantic_payload())

        status = PackageStatus.COMPLETE
        if violations or missing or ledger.unresolved:
            status = PackageStatus.INCOMPLETE

        return BuiltPackage(
            schema=schema,
            tailored=tailored,
            resolutions=resolutions,
            status=status,
            violations=violations,
            missing_inputs=missing,
        )

    # -- artifacts ------------------------------------------------------

    def _cover_letter(
        self,
        profile: CandidateProfile,
        job: CanonicalJob | Any,
        tailored: TailoredRésumé,
        ledger: TruthLedger,
    ) -> str | None:
        """A cover letter built only from fixed framing plus candidate content.

        Framing sentences are recorded as INFERRED with the job field they derive
        from; every substantive sentence is either a candidate-authored line quoted
        verbatim or a list of the candidate's own terms. Nothing is paraphrased, so
        no claim can be introduced by the generator.
        """

        title = normalize_text(getattr(job, "title", "") or "the advertised role")
        company = normalize_text(getattr(job, "company", "") or "your organisation")
        location = normalize_text(getattr(job, "location", "") or "")

        lines: list[str] = ["Dear Hiring Team,", ""]
        opening = f"I am applying for the {title} position at {company}"
        if location:
            opening += f" ({location})"
        opening += "."
        lines.append(opening)
        ledger.record(
            f"cover letter opening: {opening}",
            TruthLevel.INFERRED,
            [Evidence("job.title"), Evidence("job.company")],
            artifact="cover_letter",
        )

        if profile.summary:
            lines.append("")
            lines.append(normalize_text(profile.summary))
            ledger.record(
                "cover letter body (candidate's own summary)",
                TruthLevel.VERIFIED,
                [Evidence("candidate_profile.summary")],
                artifact="cover_letter",
            )

        highlights = tailored.selected_bullets[:3]
        if highlights:
            lines.append("")
            lines.append("Selected results from my experience:")
            for bullet in highlights:
                lines.append(f"- {bullet}")
                ledger.record(
                    f"cover letter bullet: {bullet}",
                    TruthLevel.VERIFIED,
                    [Evidence("candidate_profile.accomplishments")],
                    artifact="cover_letter",
                )

        if tailored.emphasis_terms:
            lines.append("")
            lines.append("My background covers " + ", ".join(tailored.emphasis_terms[:6]) + ".")
            ledger.record(
                "cover letter alignment sentence",
                TruthLevel.INFERRED,
                [Evidence("candidate_profile.skills")],
                artifact="cover_letter",
            )

        lines.append("")
        lines.append(
            f"I would welcome the opportunity to discuss how my experience fits the {title} role."
        )
        lines.append("")
        lines.append(_closing_line(profile))
        if not profile.summary and not highlights:
            ledger.unresolved_question(
                "Cover letter has no candidate-authored content to quote; the letter is "
                "framing only and is not ready to send"
            )
        return "\n".join(lines)

    def _candidate_summary(
        self, profile: CandidateProfile, job: CanonicalJob | Any, tailored: TailoredRésumé
    ) -> str:
        parts: list[str] = []
        if profile.headline:
            parts.append(normalize_text(profile.headline))
        roles = [
            f"{e.title} at {e.employer}" for e in profile.employment[:3] if e.title and e.employer
        ]
        if roles:
            parts.append("Recent roles: " + "; ".join(roles) + ".")
        if tailored.emphasis_terms:
            parts.append("Relevant strengths: " + ", ".join(tailored.emphasis_terms[:8]) + ".")
        if not parts:
            return (
                "No candidate summary is available yet: the profile carries no headline, "
                "employment history or skills to summarise."
            )
        return " ".join(parts)

    def _fit_explanation(
        self, assessment: FitAssessmentSchema | None, job: CanonicalJob | Any
    ) -> str:
        if assessment is None:
            return "No fit assessment has been produced for this opportunity yet."
        lines = [assessment.why_this_job]
        if assessment.strengths:
            lines.append("Strengths: " + "; ".join(assessment.strengths[:4]))
        if assessment.gaps:
            lines.append("Gaps: " + "; ".join(assessment.gaps[:4]))
        lines.append(f"Confidence: {assessment.confidence:.2f} (engine: {assessment.engine}).")
        return "\n".join(lines)

    def _recruiter_message(
        self, profile: CandidateProfile, job: CanonicalJob | Any, tailored: TailoredRésumé
    ) -> str:
        name = profile.identity.full_name or "[your name]"
        title = normalize_text(getattr(job, "title", "") or "the role")
        company = normalize_text(getattr(job, "company", "") or "your team")
        return (
            f"Hello, I'm {name}. I've applied for the {title} role at {company} and wanted to "
            "introduce myself directly. My background includes "
            + (
                ", ".join(tailored.emphasis_terms[:5])
                if tailored.emphasis_terms
                else "the experience summarised in my application"
            )
            + ". I'd be glad to answer any questions."
        )

    def _hiring_manager_message(
        self,
        profile: CandidateProfile,
        job: CanonicalJob | Any,
        tailored: TailoredRésumé,
        assessment: FitAssessmentSchema | None,
    ) -> str:
        title = normalize_text(getattr(job, "title", "") or "the role")
        lead = tailored.selected_bullets[:2]
        body = " ".join(lead) if lead else ""
        return (
            f"Regarding the {title} opening: "
            + (body + " " if body else "")
            + "I'd welcome a conversation about the role and what success looks like in the first year."
        )

    def _interview_prep(
        self,
        job: CanonicalJob | Any,
        assessment: FitAssessmentSchema | None,
        profile: CandidateProfile,
    ) -> str:
        """Prompts derived from the posting — questions to prepare, not claims."""

        lines: list[str] = ["Prepare for these areas (derived from the posting):"]
        requirements = getattr(job, "requirements", None)
        from sabra_providers.normalize import to_bullets

        for item in to_bullets(requirements)[:6]:
            lines.append(f"- {item}")
        if assessment and assessment.gaps:
            lines.append("")
            lines.append("Gaps to address proactively:")
            for gap in assessment.gaps[:4]:
                lines.append(f"- {gap}")
        if not profile.employment:
            lines.append("")
            lines.append(
                "- No candidate history is available, so no role-specific talking points "
                "can be prepared yet."
            )
        return "\n".join(lines)
