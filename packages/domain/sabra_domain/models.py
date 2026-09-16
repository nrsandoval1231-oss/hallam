"""SQLAlchemy models — the 15 domain objects required by PRD requirement 34.

Schema notes that are load-bearing:

* `opportunities.primary_job_id` is a plain string, not a foreign key, because the
  jobs <-> opportunities relationship is genuinely mutual and SQLite cannot emit
  the ALTER TABLE a cyclic FK would need. Integrity is maintained by the service
  layer, and `jobs.opportunity_id` (a real FK) is the authoritative edge.
* `applications.opportunity_id` is UNIQUE. Requirement 9 says duplicate
  applications must not be possible; a constraint enforces that, a comment does
  not.
* `events` is append-only with an autoincrement `seq` so ordering is deterministic
  even when two rows share a timestamp.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .identity import new_id, sha256_text
from .types import JsonBlob, UtcDateTime, utcnow


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Candidate aggregate
# ---------------------------------------------------------------------------


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cand"))
    display_name: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(256), index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(256))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    profiles: Mapped[list[CandidateProfileRow]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    resumes: Mapped[list[Resume]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    search_profiles: Mapped[list[SearchProfileRow]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class CandidateProfileRow(Base, TimestampMixin):
    """Versioned structured candidate model. Never mutated in place."""

    __tablename__ = "candidate_profiles"
    __table_args__ = (UniqueConstraint("candidate_id", "version", name="uq_profile_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cprof"))
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonBlob, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(64), default="owner", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    candidate: Mapped[Candidate] = relationship(back_populates="profiles")


class Resume(Base, TimestampMixin):
    """Master and tailored résumés.

    Requirement 16: exactly one master is authoritative (`is_master`), and
    tailored résumés record the master they derive from so drift is detectable.
    """

    __tablename__ = "resumes"
    __table_args__ = (
        UniqueConstraint("candidate_id", "version", name="uq_resume_version"),
        Index("ix_resumes_master", "candidate_id", "is_master"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("res"))
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    label: Mapped[str | None] = mapped_column(String(256))
    is_master: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_format: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(512))
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    parsed: Mapped[dict[str, Any]] = mapped_column(JsonBlob, nullable=False, default=dict)
    derived_from_resume_id: Mapped[str | None] = mapped_column(String(64))
    opportunity_id: Mapped[str | None] = mapped_column(String(64), index=True)

    candidate: Mapped[Candidate] = relationship(back_populates="resumes")


class SearchProfileRow(Base, TimestampMixin):
    __tablename__ = "search_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("srch"))
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonBlob, nullable=False, default=dict)

    candidate: Mapped[Candidate] = relationship(back_populates="search_profiles")


# ---------------------------------------------------------------------------
# Company and job
# ---------------------------------------------------------------------------


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("co"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    domain: Mapped[str | None] = mapped_column(String(256))
    industry: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(512))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonBlob, default=dict)

    __table_args__ = (UniqueConstraint("normalized_name", name="uq_company_normalized"),)


class ProviderRow(Base, TimestampMixin):
    """Source registry entry (requirement 7 / 35)."""

    __tablename__ = "providers"
    __table_args__ = (UniqueConstraint("kind", "token", name="uq_provider_kind_token"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("src"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(128))
    locator: Mapped[str | None] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    last_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    health: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)


class Opportunity(Base, TimestampMixin):
    """One real-world opening, however many source postings describe it."""

    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "search_profile_id", "canonical_key", name="uq_opportunity_key"
        ),
        Index("ix_opportunity_status", "candidate_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("opp"))
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    # Empty string, never NULL: SQLite (and the SQL standard) treat NULLs as
    # distinct in a UNIQUE constraint, so a nullable column here would silently
    # allow two opportunities for the same posting when no search profile is set.
    search_profile_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    canonical_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str] = mapped_column(String(256), nullable=False)
    location: Mapped[str | None] = mapped_column(String(256))
    primary_job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_ids: Mapped[list[Any]] = mapped_column(JsonBlob, default=list)
    status: Mapped[str] = mapped_column(String(32), default="NORMALIZED", nullable=False)
    rejection: Mapped[dict[str, Any] | None] = mapped_column(JsonBlob)
    rank_score: Mapped[float | None] = mapped_column(Float)
    rank_position: Mapped[int | None] = mapped_column(Integer)
    shortlisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shortlist_reason: Mapped[str | None] = mapped_column(Text)

    jobs: Mapped[list[Job]] = relationship(
        back_populates="opportunity", foreign_keys="Job.opportunity_id"
    )
    fit_assessment: Mapped[FitAssessmentRow | None] = relationship(
        back_populates="opportunity", uselist=False, cascade="all, delete-orphan"
    )


class Job(Base, TimestampMixin):
    """Canonical job record (requirement 8). One row per source posting."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_job_source_id"),
        Index("ix_jobs_canonical_key", "canonical_key"),
        Index("ix_jobs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("job"))
    opportunity_id: Mapped[str | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    canonical_key: Mapped[str] = mapped_column(String(64), nullable=False)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_job_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    board_token: Mapped[str | None] = mapped_column(String(128))

    company: Mapped[str] = mapped_column(String(256), nullable=False)
    company_domain: Mapped[str | None] = mapped_column(String(256))
    company_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    location: Mapped[str | None] = mapped_column(String(256))
    city: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(128))
    remote_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    employment_type: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(8))
    salary_period: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    preferred_qualifications: Mapped[str | None] = mapped_column(Text)
    responsibilities: Mapped[str | None] = mapped_column(Text)
    travel: Mapped[str | None] = mapped_column(String(256))
    department: Mapped[str | None] = mapped_column(String(256))
    seniority: Mapped[str | None] = mapped_column(String(64))
    date_posted: Mapped[date | None] = mapped_column(Date)
    date_discovered: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_verified: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(32), default="DISCOVERED", nullable=False)
    rejection: Mapped[dict[str, Any] | None] = mapped_column(JsonBlob)
    raw_source_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JsonBlob, default=dict)
    normalization_warnings: Mapped[list[Any]] = mapped_column(JsonBlob, default=list)
    discovered_via: Mapped[str | None] = mapped_column(String(128))

    opportunity: Mapped[Opportunity | None] = relationship(
        back_populates="jobs", foreign_keys=[opportunity_id]
    )


class FitAssessmentRow(Base, TimestampMixin):
    """Explainable fit assessment (requirement 12). One row per opportunity."""

    __tablename__ = "fit_assessments"
    __table_args__ = (UniqueConstraint("opportunity_id", name="uq_fit_opportunity"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("fit"))
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    engine: Mapped[str] = mapped_column(String(32), default="heuristic", nullable=False)
    fit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    career_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    qualification_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    compensation_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    location_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    industry_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trajectory_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rank_score: Mapped[float | None] = mapped_column(Float)
    strengths: Mapped[list[Any]] = mapped_column(JsonBlob, default=list)
    gaps: Mapped[list[Any]] = mapped_column(JsonBlob, default=list)
    risks: Mapped[list[Any]] = mapped_column(JsonBlob, default=list)
    why_this_job: Mapped[str] = mapped_column(Text, default="")
    component_rationale: Mapped[dict[str, Any]] = mapped_column(JsonBlob, default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(JsonBlob, default=dict)

    opportunity: Mapped[Opportunity] = relationship(back_populates="fit_assessment")


class ResearchPacketRow(Base, TimestampMixin):
    __tablename__ = "research_packets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rsch"))
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JsonBlob, nullable=False, default=dict)
    engine: Mapped[str] = mapped_column(String(32), default="deterministic", nullable=False)


# ---------------------------------------------------------------------------
# Application aggregate
# ---------------------------------------------------------------------------


class ApplicationPackageRow(Base, TimestampMixin):
    """Reviewable package (requirements 15, 16, 31). Immutable once approved.

    `content_hash` binds approvals to exact content; editing a package after
    approval produces a new hash and therefore a new revision that must be
    approved again.
    """

    __tablename__ = "application_packages"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "revision", name="uq_package_revision"),
        Index("ix_package_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pkg"))
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    master_resume_id: Mapped[str | None] = mapped_column(String(64))
    tailored_resume_id: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonBlob, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    truth_clean: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    unresolved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ApplicationRow(Base, TimestampMixin):
    """Durable application record (requirement 22) and duplicate guard (req 9)."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("opportunity_id", name="uq_application_opportunity"),
        Index("ix_application_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("app"))
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    package_id: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    master_resume_id: Mapped[str | None] = mapped_column(String(64))
    tailored_resume_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    answers: Mapped[list[Any]] = mapped_column(JsonBlob, default=list)
    submission_method: Mapped[str | None] = mapped_column(String(32))
    submission_time: Mapped[datetime | None] = mapped_column(UtcDateTime)
    external_confirmation: Mapped[dict[str, Any]] = mapped_column(JsonBlob, default=dict)
    approval: Mapped[dict[str, Any] | None] = mapped_column(JsonBlob)
    approved_package_hash: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    blocked_reason: Mapped[str | None] = mapped_column(String(64))


class EventRow(Base):
    """Append-only event log (requirement 23). Never updated, never deleted."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_entity", "entity_type", "entity_id", "seq"),
        Index("ix_events_type", "event_type"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: new_id("evt"))
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonBlob, nullable=False, default=dict)
    actor: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)


class OutcomeRow(Base, TimestampMixin):
    """Outcome capture, manual-first (requirement 24)."""

    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("out"))
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonBlob, default=dict)


class DiscoveryRunRow(Base, TimestampMixin):
    """Observability record for one restartable discovery run (requirement 38)."""

    __tablename__ = "discovery_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    candidate_id: Mapped[str | None] = mapped_column(String(64), index=True)
    search_profile_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", nullable=False)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    sources_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_ok: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_unchanged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    per_source: Mapped[dict[str, Any]] = mapped_column(JsonBlob, default=dict)
    errors: Mapped[list[Any]] = mapped_column(JsonBlob, default=list)


class ModelCallRow(Base):
    """Audit trail for the reasoning layer (requirement 37).

    Prompts and responses are hashed, not stored: the record proves what was asked
    and answered without retaining candidate PII in a second place.
    """

    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("llm"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    prompt_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)


def prompt_fingerprint(text: str) -> str:
    return sha256_text(text)
