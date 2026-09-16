"""Structured payload contracts.

These are the shapes persisted in JSON columns and exchanged across package
boundaries. Requirements 4, 6, 8 and 12 each name fields explicitly; those names
are preserved verbatim here so the code and the contract cannot drift.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import EmploymentType, RemoteStatus, SourceKind, TruthLevel

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """Where a value came from and how strongly it is supported."""

    model_config = ConfigDict(extra="forbid")

    level: TruthLevel = TruthLevel.VERIFIED
    evidence: list[str] = Field(default_factory=list)
    note: str | None = None


# ---------------------------------------------------------------------------
# Candidate (requirements 4, 18)
# ---------------------------------------------------------------------------


class ContactInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str = "USA"
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    website_url: str | None = None


class EmploymentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    employer: str | None = None
    title: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
    location: str | None = None
    remote_status: RemoteStatus | None = None
    summary: str | None = None
    accomplishments: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    people_managed: int | None = None
    quota_attainment: str | None = None
    technologies: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    honors: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CertificationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    issuer: str | None = None
    issued: str | None = None
    expires: str | None = None
    credential_id: str | None = None
    evidence: list[str] = Field(default_factory=list)


class SkillEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    category: str | None = None
    years: float | None = None
    evidence: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: str | None = None
    description: str | None = None
    url: str | None = None
    outcomes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CompensationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employer: str | None = None
    title: str | None = None
    base: float | None = None
    bonus: float | None = None
    commission: float | None = None
    ote: float | None = None
    currency: str = "USD"
    period: Literal["YEAR", "MONTH", "HOUR"] = "YEAR"
    year: int | None = None


class CompensationPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum: float | None = None
    target: float | None = None
    currency: str = "USD"
    period: Literal["YEAR", "MONTH", "HOUR"] = "YEAR"
    accepts_commission: bool | None = None
    notes: str | None = None


class ManagementExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    people_managed_max: int | None = None
    years_managing: float | None = None
    functions_managed: list[str] = Field(default_factory=list)
    has_management_experience: bool | None = None


class Geography(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cities: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    metro_areas: list[str] = Field(default_factory=list)
    country: str = "USA"
    willing_to_relocate: bool | None = None
    relocation_notes: str | None = None


class TravelTolerance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_percent: int | None = None
    willing_to_travel: bool | None = None
    notes: str | None = None


class WorkAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    requires_sponsorship: bool | None = None
    notes: str | None = None


class HardConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    value: str | bool | float | int | None = None
    detail: str | None = None


class SoftPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    value: str | bool | float | int | None = None
    weight: float = 0.5
    detail: str | None = None


class AnswerRecord(BaseModel):
    """Reusable answer to a common application question (requirement 18).

    `sensitive` and `user_controlled` answers are never auto-filled into a form:
    the system may propose text but the human decides.
    """

    model_config = ConfigDict(extra="forbid")

    question_key: str
    question_text: str | None = None
    value: Any = None
    level: TruthLevel = TruthLevel.UNKNOWN
    sensitive: bool = False
    user_controlled: bool = False
    evidence: list[str] = Field(default_factory=list)
    note: str | None = None

    @property
    def auto_fillable(self) -> bool:
        return (
            self.level is TruthLevel.VERIFIED
            and not self.sensitive
            and not self.user_controlled
            and self.value not in (None, "")
        )


class CandidateProfile(BaseModel):
    """Canonical candidate model (requirement 4).

    The résumé is evidence for this model, never the model itself: resume-derived
    values carry `Provenance(level=INFERRED, evidence=[...])` so downstream
    tailoring can distinguish what the owner asserted from what the system read.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    version: int = 1
    identity: ContactInfo = Field(default_factory=ContactInfo)
    headline: str | None = None
    summary: str | None = None
    employment: list[EmploymentEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    skills: list[SkillEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    management_experience: ManagementExperience = Field(default_factory=ManagementExperience)
    technical_capabilities: list[str] = Field(default_factory=list)
    compensation_history: list[CompensationRecord] = Field(default_factory=list)
    desired_compensation: CompensationPreference = Field(default_factory=CompensationPreference)
    preferred_titles: list[str] = Field(default_factory=list)
    acceptable_titles: list[str] = Field(default_factory=list)
    excluded_titles: list[str] = Field(default_factory=list)
    preferred_industries: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    geography: Geography = Field(default_factory=Geography)
    relocation_willingness: bool | None = None
    work_arrangement: dict[str, bool] = Field(
        default_factory=lambda: {"remote": True, "hybrid": True, "onsite": True}
    )
    travel_tolerance: TravelTolerance = Field(default_factory=TravelTolerance)
    seniority_preference: list[str] = Field(default_factory=list)
    employment_types: list[EmploymentType] = Field(default_factory=lambda: [EmploymentType.FULL_TIME])
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    soft_preferences: list[SoftPreference] = Field(default_factory=list)
    answer_memory: dict[str, AnswerRecord] = Field(default_factory=dict)
    provenance: dict[str, Provenance] = Field(default_factory=dict)

    # -- derived helpers -------------------------------------------------

    def provenance_for(self, field_path: str) -> Provenance:
        """Support level for a field.

        Absent an explicit entry, a populated owner-supplied field is VERIFIED and
        an empty one is UNKNOWN. Absence of evidence is treated as absence of
        support rather than as support.
        """

        if field_path in self.provenance:
            return self.provenance[field_path]
        return Provenance(level=TruthLevel.VERIFIED)

    def is_empty(self, field_path: str) -> bool:
        value: Any = self
        for part in field_path.split("."):
            value = (
                value.get(part) if isinstance(value, dict) else getattr(value, part, None)
            )
            if value is None:
                return True
        if isinstance(value, (list, dict, str)):
            return len(value) == 0
        return False

    def populated_field_paths(self, prefix: str = "") -> list[str]:
        """Every non-empty leaf field, used by documents to bound generation."""

        out: list[str] = []
        for name, value in self.model_dump(mode="json").items():
            path = f"{prefix}{name}" if not prefix else f"{prefix}.{name}"
            if isinstance(value, dict):
                nested = CandidateProfile.model_fields.get(name)
                if nested is not None and not isinstance(value, dict):
                    out.append(path)
                    continue
                for key, sub in value.items():
                    sub_path = f"{path}.{key}"
                    if sub in (None, "", [], {}):
                        continue
                    out.append(sub_path)
            elif isinstance(value, list):
                if value:
                    out.append(path)
            elif value not in (None, ""):
                out.append(path)
        return out

    def all_accomplishments(self) -> list[tuple[str, str, str]]:
        """(employer, title, accomplishment) triples — the only permitted source of
        résumé bullets and quantified claims."""

        out: list[tuple[str, str, str]] = []
        for entry in self.employment:
            for item in entry.accomplishments:
                out.append((entry.employer or "", entry.title or "", item))
        return out


# ---------------------------------------------------------------------------
# Search intent (requirement 6)
# ---------------------------------------------------------------------------


class LocationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cities: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    metro_areas: list[str] = Field(default_factory=list)
    allow_remote: bool = True
    allow_hybrid: bool = True
    allow_onsite: bool = True
    allow_relocation: bool = False


class SearchProfile(BaseModel):
    """Canonical search intent (requirement 6). Users may hold several."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    candidate_id: str
    is_active: bool = True
    target_titles: list[str] = Field(default_factory=list)
    acceptable_titles: list[str] = Field(default_factory=list)
    excluded_titles: list[str] = Field(default_factory=list)
    # Role families the candidate accepts, and the level markers that make one of
    # them a job title. A posting title passes intent screening when it contains a
    # target n-gram, or a family term together with a level term. This is declared
    # by the profile rather than hard-coded, because "is this really a field sales
    # role?" is the candidate's judgement, not the program's.
    role_family_terms: list[str] = Field(default_factory=list)
    role_level_terms: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    locations: LocationIntent = Field(default_factory=LocationIntent)
    compensation: CompensationPreference = Field(default_factory=CompensationPreference)
    travel_max_percent: int | None = None
    employment_types: list[EmploymentType] = Field(
        default_factory=lambda: [EmploymentType.FULL_TIME]
    )
    excluded_employment_types: list[EmploymentType] = Field(default_factory=list)
    seniority_levels: list[str] = Field(default_factory=list)
    excluded_seniority_levels: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    minimum_qualifications: list[str] = Field(default_factory=list)
    allow_commission_only: bool = False
    max_age_days: int | None = 90
    source_filter: list[SourceKind] = Field(default_factory=list)
    shortlist_target: int = 10
    prepare_target: int = 3
    created_at: datetime | None = None

    @field_validator("excluded_titles", "target_titles", mode="after")
    @classmethod
    def _strip(cls, value: list[str]) -> list[str]:
        return [v.strip() for v in value if v and v.strip()]


# ---------------------------------------------------------------------------
# Canonical job (requirement 8)
# ---------------------------------------------------------------------------


class CanonicalJob(BaseModel):
    """The normalized opening every provider produces (requirement 8).

    Field names are exactly those named in the contract. `raw_source_hash` binds
    the normalized record to the bytes it came from.
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    source: SourceKind
    source_job_id: str
    source_url: str
    company: str
    company_domain: str | None = None
    title: str
    location: str | None = None
    remote_status: RemoteStatus = RemoteStatus.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    description: str | None = None
    requirements: str | None = None
    preferred_qualifications: str | None = None
    responsibilities: str | None = None
    travel: str | None = None
    department: str | None = None
    date_posted: date | None = None
    date_discovered: datetime | None = None
    last_verified: datetime | None = None
    status: str = "DISCOVERED"
    raw_source_hash: str = ""
    city: str | None = None
    region: str | None = None
    country: str | None = None
    board_token: str | None = None
    seniority: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    discovered_via: str | None = None

    def canonical_key(self) -> str:
        from .identity import canonical_key, normalize_company, normalize_location, normalize_title

        return canonical_key(
            normalize_company(self.company),
            normalize_title(self.title),
            normalize_location(self.location),
        )

    @field_validator("company", "title", "location", "city", "region", "department", mode="after")
    @classmethod
    def _strip_surrounding_whitespace(cls, value: str | None) -> str | None:
        """Trim source text.

        ATS payloads carry values like `"Revance "` with a trailing space; left
        alone that single space changes the canonical key and stops the same
        employer's posting on two boards from being recognized as one opening.
        """

        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class NormalizationResult(BaseModel):
    """Outcome of normalizing one raw posting, including what could not be read."""

    model_config = ConfigDict(extra="forbid")

    job: CanonicalJob
    warnings: list[str] = Field(default_factory=list)
    field_confidence: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fit assessment (requirement 12)
# ---------------------------------------------------------------------------


class FitAssessmentSchema(BaseModel):
    """Structured, explainable assessment (requirement 12).

    Every sub-score carries its own rationale so no magic number is ever
    presented without a reason.
    """

    model_config = ConfigDict(extra="forbid")

    fit_score: float
    career_score: float
    qualification_score: float
    compensation_score: float
    location_score: float
    industry_score: float
    trajectory_score: float
    confidence: float
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    why_this_job: str = ""
    component_rationale: dict[str, str] = Field(default_factory=dict)
    engine: str = "heuristic"
    features: dict[str, Any] = Field(default_factory=dict)
    rank_score: float | None = None

    @field_validator(
        "fit_score", "career_score", "qualification_score", "compensation_score",
        "location_score", "industry_score", "trajectory_score", "confidence",
    )
    @classmethod
    def _unit_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"score out of range [0,1]: {value}")
        return value


class RejectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasons: list[str]
    detail: dict[str, str] = Field(default_factory=dict)
    evaluated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Research (requirement 14)
# ---------------------------------------------------------------------------


class ResearchPacketSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_description: str | None = None
    company_website: str | None = None
    industry: str | None = None
    products_services: list[str] = Field(default_factory=list)
    role_context: str | None = None
    likely_reporting_organization: str | None = None
    recent_developments: list[str] = Field(default_factory=list)
    hiring_context: str | None = None
    candidate_company_intersections: list[str] = Field(default_factory=list)
    verified_facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    engine: str = "deterministic"


# ---------------------------------------------------------------------------
# Application package (requirements 15, 16, 18, 31)
# ---------------------------------------------------------------------------


class ApplicationAnswerSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_key: str
    question_text: str | None = None
    answer: str | None = None
    level: TruthLevel = TruthLevel.UNKNOWN
    evidence: list[str] = Field(default_factory=list)
    requires_user_input: bool = False
    sensitive: bool = False


class ApplicationPackageSchema(BaseModel):
    """The reviewable artifact set (requirement 15).

    `content_hash` is computed over `semantic_payload()` — content only, never
    timestamps or ids — so an approval bound to it cannot be replayed against
    edited content.
    """

    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    candidate_id: str
    job_id: str
    master_resume_id: str | None = None
    tailored_resume_markdown: str | None = None
    cover_letter: str | None = None
    candidate_summary: str | None = None
    fit_explanation: str | None = None
    qualification_gaps: list[str] = Field(default_factory=list)
    answers: list[ApplicationAnswerSchema] = Field(default_factory=list)
    recruiter_message: str | None = None
    hiring_manager_message: str | None = None
    interview_preparation: str | None = None
    unresolved_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    truth_ledger: dict[str, Any] = Field(default_factory=dict)
    engine: str = "deterministic"
    content_hash: str = ""

    def semantic_payload(self) -> dict[str, Any]:
        """Content that defines the package identity. Excludes volatile metadata."""

        return {
            "opportunity_id": self.opportunity_id,
            "candidate_id": self.candidate_id,
            "job_id": self.job_id,
            "master_resume_id": self.master_resume_id,
            "tailored_resume_markdown": self.tailored_resume_markdown,
            "cover_letter": self.cover_letter,
            "candidate_summary": self.candidate_summary,
            "fit_explanation": self.fit_explanation,
            "qualification_gaps": self.qualification_gaps,
            "answers": [a.model_dump(mode="json") for a in self.answers],
            "recruiter_message": self.recruiter_message,
            "hiring_manager_message": self.hiring_manager_message,
            "interview_preparation": self.interview_preparation,
        }


class ApprovalRecord(BaseModel):
    """An approval bound to one immutable package revision (requirement 19)."""

    model_config = ConfigDict(extra="forbid")

    package_id: str
    package_content_hash: str
    approved_by: str
    approved_at: datetime
    decision: str = "APPROVED"
    scope_note: str | None = None
    acknowledged_unresolved: list[str] = Field(default_factory=list)


class ApplicationLedgerSchema(BaseModel):
    """The durable application record (requirement 22)."""

    model_config = ConfigDict(extra="forbid")

    application_id: str
    job: dict[str, Any]
    candidate: dict[str, Any]
    application_package: dict[str, Any]
    resume_version: str | None = None
    answers: list[dict[str, Any]] = Field(default_factory=list)
    submission_time: datetime | None = None
    submission_method: str | None = None
    status: str
    external_confirmation: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    outcomes: list[dict[str, Any]] = Field(default_factory=list)


class LearningRecord(BaseModel):
    """Training evidence preserved from day one (requirement 25)."""

    model_config = ConfigDict(extra="forbid")

    application_id: str
    job_features: dict[str, Any] = Field(default_factory=dict)
    candidate_features: dict[str, Any] = Field(default_factory=dict)
    strategy: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    outcome: str | None = None
    outcome_recorded_at: datetime | None = None


class SourceRegistryEntry(BaseModel):
    """One discovery source. `token` is the board identifier on the ATS."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: SourceKind
    token: str
    display_name: str
    industry: str | None = None
    enabled: bool = True
    locator: str | None = None
    notes: str | None = None
    discovered_job_count: int | None = None
