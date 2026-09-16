"""Typed repositories.

Every write path in the product goes through one of these, so the invariants the
schema cannot express (status transitions, provenance, truth levels) have exactly
one home. Repositories never call providers, models or the network.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from . import models
from .enums import (
    ApplicationStatus,
    EntityType,
    EventType,
    JobStatus,
    PackageStatus,
    SourceHealth,
)
from .errors import NotFound
from .identity import content_hash, normalize_company, sha256_text
from .schemas import (
    ApplicationPackageSchema,
    CandidateProfile,
    CanonicalJob,
    SearchProfile,
)
from .state import assert_application_transition, assert_job_transition
from .types import utcnow


class EventRepository:
    """Append-only writer. There is deliberately no update or delete."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(
        self,
        entity_type: EntityType | str,
        entity_id: str,
        event_type: EventType | str,
        payload: dict[str, Any] | None = None,
        *,
        actor: str = "system",
        occurred_at: datetime | None = None,
    ) -> models.EventRow:
        row = models.EventRow(
            entity_type=str(entity_type),
            entity_id=entity_id,
            event_type=str(event_type),
            payload=payload or {},
            actor=actor,
            occurred_at=occurred_at or utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def for_entity(
        self, entity_type: EntityType | str, entity_id: str, limit: int | None = None
    ) -> list[models.EventRow]:
        stmt: Select = (
            select(models.EventRow)
            .where(
                models.EventRow.entity_type == str(entity_type),
                models.EventRow.entity_id == entity_id,
            )
            .order_by(models.EventRow.seq)
        )
        if limit:
            stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).scalars())

    def recent(self, limit: int = 50) -> list[models.EventRow]:
        stmt = select(models.EventRow).order_by(models.EventRow.seq.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars())

    def count_by_type(self) -> dict[str, int]:
        stmt = select(models.EventRow.event_type, func.count()).group_by(models.EventRow.event_type)
        return {row[0]: row[1] for row in self.session.execute(stmt)}


class CandidateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        display_name: str | None,
        *,
        email: str | None = None,
        phone: str | None = None,
        location: str | None = None,
        notes: str | None = None,
    ) -> models.Candidate:
        row = models.Candidate(
            display_name=display_name, email=email, phone=phone, location=location, notes=notes
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, candidate_id: str) -> models.Candidate:
        row = self.session.get(models.Candidate, candidate_id)
        if row is None:
            raise NotFound("candidate", candidate_id)
        return row

    def find_by_email(self, email: str) -> models.Candidate | None:
        stmt = select(models.Candidate).where(models.Candidate.email == email)
        return self.session.execute(stmt).scalars().first()

    def primary(self) -> models.Candidate | None:
        stmt = select(models.Candidate).where(models.Candidate.is_primary.is_(True)).order_by(
            models.Candidate.created_at
        )
        return self.session.execute(stmt).scalars().first()

    def list_all(self) -> list[models.Candidate]:
        return list(self.session.execute(select(models.Candidate)).scalars())

    # -- profiles -------------------------------------------------------

    def save_profile(
        self, candidate_id: str, profile: CandidateProfile, *, source: str = "owner"
    ) -> models.CandidateProfileRow:
        """Persist a new profile version and deactivate prior ones."""

        self.get(candidate_id)
        payload = profile.model_dump(mode="json")
        latest = self.latest_profile_row(candidate_id)
        version = (latest.version + 1) if latest else 1
        profile.version = version
        payload["version"] = version
        for existing in self.session.execute(
            select(models.CandidateProfileRow).where(
                models.CandidateProfileRow.candidate_id == candidate_id,
                models.CandidateProfileRow.is_active.is_(True),
            )
        ).scalars():
            existing.is_active = False
        row = models.CandidateProfileRow(
            candidate_id=candidate_id,
            version=version,
            is_active=True,
            payload=payload,
            source=source,
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def latest_profile_row(self, candidate_id: str) -> models.CandidateProfileRow | None:
        stmt = (
            select(models.CandidateProfileRow)
            .where(models.CandidateProfileRow.candidate_id == candidate_id)
            .order_by(models.CandidateProfileRow.version.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def active_profile(self, candidate_id: str) -> CandidateProfile:
        row = self.session.execute(
            select(models.CandidateProfileRow).where(
                models.CandidateProfileRow.candidate_id == candidate_id,
                models.CandidateProfileRow.is_active.is_(True),
            )
        ).scalars().first()
        if row is None:
            raise NotFound("candidate_profile", candidate_id)
        return CandidateProfile.model_validate(row.payload)


class ResumeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        candidate_id: str,
        content_text: str,
        *,
        is_master: bool,
        label: str | None = None,
        source_format: str = "text",
        source_path: str | None = None,
        parsed: dict[str, Any] | None = None,
        derived_from_resume_id: str | None = None,
        opportunity_id: str | None = None,
    ) -> models.Resume:
        if is_master:
            for existing in self.session.execute(
                select(models.Resume).where(
                    models.Resume.candidate_id == candidate_id,
                    models.Resume.is_master.is_(True),
                )
            ).scalars():
                existing.is_master = False
        max_version = self.session.execute(
            select(func.max(models.Resume.version)).where(models.Resume.candidate_id == candidate_id)
        ).scalar()
        row = models.Resume(
            candidate_id=candidate_id,
            version=(max_version or 0) + 1,
            label=label,
            is_master=is_master,
            source_format=source_format,
            source_path=source_path,
            content_text=content_text,
            content_hash=sha256_text(content_text),
            parsed=parsed or {},
            derived_from_resume_id=derived_from_resume_id,
            opportunity_id=opportunity_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def master(self, candidate_id: str) -> models.Resume | None:
        stmt = (
            select(models.Resume)
            .where(models.Resume.candidate_id == candidate_id, models.Resume.is_master.is_(True))
            .order_by(models.Resume.version.desc())
        )
        return self.session.execute(stmt).scalars().first()

    def require_master(self, candidate_id: str) -> models.Resume:
        row = self.master(candidate_id)
        if row is None:
            raise NotFound("master_resume", candidate_id)
        return row

    def get(self, resume_id: str) -> models.Resume:
        row = self.session.get(models.Resume, resume_id)
        if row is None:
            raise NotFound("resume", resume_id)
        return row

    def list_for_candidate(self, candidate_id: str) -> list[models.Resume]:
        stmt = (
            select(models.Resume)
            .where(models.Resume.candidate_id == candidate_id)
            .order_by(models.Resume.version.desc())
        )
        return list(self.session.execute(stmt).scalars())


class SearchProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, profile: SearchProfile) -> models.SearchProfileRow:
        row = models.SearchProfileRow(
            id=profile.id,
            candidate_id=profile.candidate_id,
            name=profile.name,
            is_active=profile.is_active,
            payload=profile.model_dump(mode="json"),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def upsert(self, profile: SearchProfile) -> models.SearchProfileRow:
        row = self.session.get(models.SearchProfileRow, profile.id)
        if row is None:
            return self.create(profile)
        row.name = profile.name
        row.is_active = profile.is_active
        row.payload = profile.model_dump(mode="json")
        self.session.flush()
        return row

    def get(self, profile_id: str) -> SearchProfile:
        row = self.session.get(models.SearchProfileRow, profile_id)
        if row is None:
            raise NotFound("search_profile", profile_id)
        return SearchProfile.model_validate(row.payload)

    def active(self, candidate_id: str) -> list[SearchProfile]:
        stmt = (
            select(models.SearchProfileRow)
            .where(
                models.SearchProfileRow.candidate_id == candidate_id,
                models.SearchProfileRow.is_active.is_(True),
            )
            .order_by(models.SearchProfileRow.created_at)
        )
        return [SearchProfile.model_validate(r.payload) for r in self.session.execute(stmt).scalars()]

    def list_for_candidate(self, candidate_id: str) -> list[models.SearchProfileRow]:
        stmt = select(models.SearchProfileRow).where(
            models.SearchProfileRow.candidate_id == candidate_id
        )
        return list(self.session.execute(stmt).scalars())


class CompanyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, name: str, **fields: Any) -> models.Company:
        normalized = normalize_company(name)
        row = self.session.execute(
            select(models.Company).where(models.Company.normalized_name == normalized)
        ).scalars().first()
        if row is None:
            row = models.Company(name=name, normalized_name=normalized, **fields)
            self.session.add(row)
        else:
            for key, value in fields.items():
                if value not in (None, ""):
                    setattr(row, key, value)
        self.session.flush()
        return row

    def list_all(self) -> list[models.Company]:
        return list(self.session.execute(select(models.Company)).scalars())


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, job_id: str) -> models.Job:
        row = self.session.get(models.Job, job_id)
        if row is None:
            raise NotFound("job", job_id)
        return row

    def find_by_source_id(self, source: str, source_job_id: str) -> models.Job | None:
        stmt = select(models.Job).where(
            models.Job.source == str(source), models.Job.source_job_id == source_job_id
        )
        return self.session.execute(stmt).scalars().first()

    def upsert_canonical(
        self, job: CanonicalJob, *, raw_payload: dict[str, Any] | None = None
    ) -> tuple[models.Job, bool, bool]:
        """Insert or refresh a job from its canonical form.

        Returns `(row, created, content_changed)`. Re-ingesting identical source
        content must be a no-op so discovery is idempotent (requirement 38) — the
        `raw_source_hash` is the comparison basis, and only `last_verified` moves
        when nothing changed.
        """

        from .identity import normalize_title

        now = utcnow()
        existing = self.find_by_source_id(str(job.source), job.source_job_id)
        fields = {
            "source_url": job.source_url,
            "company": job.company,
            "company_domain": job.company_domain,
            "board_token": job.board_token,
            "title": job.title,
            "normalized_title": normalize_title(job.title),
            "location": job.location,
            "city": job.city,
            "region": job.region,
            "country": job.country,
            "remote_status": str(job.remote_status),
            "employment_type": str(job.employment_type),
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "salary_currency": job.salary_currency,
            "salary_period": job.salary_period,
            "description": job.description,
            "requirements": job.requirements,
            "preferred_qualifications": job.preferred_qualifications,
            "responsibilities": job.responsibilities,
            "travel": job.travel,
            "department": job.department,
            "seniority": job.seniority,
            "date_posted": job.date_posted,
            "raw_payload": raw_payload if raw_payload is not None else job.raw_payload,
            "canonical_key": job.canonical_key(),
        }
        if existing is None:
            row = models.Job(
                source=str(job.source),
                source_job_id=job.source_job_id,
                status=str(JobStatus.DISCOVERED),
                raw_source_hash=job.raw_source_hash,
                date_discovered=job.date_discovered or now,
                last_verified=now,
                discovered_via=job.discovered_via,
                **fields,
            )
            self.session.add(row)
            self.session.flush()
            return row, True, True

        changed = existing.raw_source_hash != job.raw_source_hash
        if changed:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.raw_source_hash = job.raw_source_hash
            existing.last_verified = now
            if existing.status in (str(JobStatus.EXPIRED),):
                existing.status = str(JobStatus.NORMALIZED)
        else:
            existing.last_verified = now
        self.session.flush()
        return existing, False, changed

    def by_canonical_key(self, canonical_key: str) -> list[models.Job]:
        stmt = select(models.Job).where(models.Job.canonical_key == canonical_key).order_by(
            models.Job.created_at
        )
        return list(self.session.execute(stmt).scalars())

    def set_status(
        self,
        job_id: str,
        target: JobStatus,
        *,
        rejection: dict[str, Any] | None = None,
    ) -> models.Job:
        row = self.get(job_id)
        assert_job_transition(JobStatus(row.status), target)
        row.status = str(target)
        if rejection is not None:
            row.rejection = rejection
        self.session.flush()
        return row

    def list_for_opportunity(self, opportunity_id: str) -> list[models.Job]:
        stmt = select(models.Job).where(models.Job.opportunity_id == opportunity_id)
        return list(self.session.execute(stmt).scalars())

    def count_by_status(self) -> dict[str, int]:
        stmt = select(models.Job.status, func.count()).group_by(models.Job.status)
        return {row[0]: row[1] for row in self.session.execute(stmt)}


class OpportunityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, opportunity_id: str) -> models.Opportunity:
        row = self.session.get(models.Opportunity, opportunity_id)
        if row is None:
            raise NotFound("opportunity", opportunity_id)
        return row

    def find(
        self, candidate_id: str, search_profile_id: str, canonical_key: str
    ) -> models.Opportunity | None:
        stmt = select(models.Opportunity).where(
            models.Opportunity.candidate_id == candidate_id,
            models.Opportunity.search_profile_id == search_profile_id,
            models.Opportunity.canonical_key == canonical_key,
        )
        return self.session.execute(stmt).scalars().first()

    def create(
        self,
        *,
        candidate_id: str,
        search_profile_id: str,
        canonical_key: str,
        title: str,
        company: str,
        location: str | None,
        primary_job_id: str,
        source_ids: Sequence[dict[str, Any]],
    ) -> models.Opportunity:
        row = models.Opportunity(
            candidate_id=candidate_id,
            search_profile_id=search_profile_id,
            canonical_key=canonical_key,
            title=title,
            company=company,
            location=location,
            primary_job_id=primary_job_id,
            source_count=len(source_ids),
            source_ids=list(source_ids),
            status=str(JobStatus.NORMALIZED),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def attach_source(
        self, opportunity: models.Opportunity, source_entry: dict[str, Any]
    ) -> models.Opportunity:
        existing = list(opportunity.source_ids or [])
        key = (source_entry.get("source"), source_entry.get("source_job_id"))
        if all((e.get("source"), e.get("source_job_id")) != key for e in existing):
            existing.append(source_entry)
        opportunity.source_ids = existing
        opportunity.source_count = len(existing)
        self.session.flush()
        return opportunity

    def set_status(
        self, opportunity_id: str, target: JobStatus, *, rejection: dict[str, Any] | None = None
    ) -> models.Opportunity:
        row = self.get(opportunity_id)
        assert_job_transition(JobStatus(row.status), target)
        row.status = str(target)
        if rejection is not None:
            row.rejection = rejection
        for job in self.list_jobs(opportunity_id):
            if job.status != str(target) and JobStatus(job.status) not in (
                JobStatus.EXPIRED,
                JobStatus.REJECTED_BY_EMPLOYER,
                JobStatus.WITHDRAWN,
            ):
                job.status = str(target)
                job.rejection = rejection
        self.session.flush()
        return row

    def set_rank(self, opportunity_id: str, rank_score: float, position: int | None) -> None:
        row = self.get(opportunity_id)
        row.rank_score = rank_score
        row.rank_position = position
        self.session.flush()

    def set_shortlist(self, opportunity_id: str, shortlisted: bool, reason: str | None = None) -> None:
        row = self.get(opportunity_id)
        row.shortlisted = shortlisted
        row.shortlist_reason = reason
        self.session.flush()

    def list_jobs(self, opportunity_id: str) -> list[models.Job]:
        stmt = select(models.Job).where(models.Job.opportunity_id == opportunity_id)
        return list(self.session.execute(stmt).scalars())

    def list_for_candidate(
        self, candidate_id: str, statuses: Iterable[JobStatus] | None = None
    ) -> list[models.Opportunity]:
        stmt = select(models.Opportunity).where(models.Opportunity.candidate_id == candidate_id)
        if statuses is not None:
            stmt = stmt.where(models.Opportunity.status.in_([str(s) for s in statuses]))
        return list(self.session.execute(stmt).scalars())

    def ranked(self, candidate_id: str) -> list[models.Opportunity]:
        stmt = (
            select(models.Opportunity)
            .where(
                models.Opportunity.candidate_id == candidate_id,
                models.Opportunity.rank_score.is_not(None),
            )
            .order_by(models.Opportunity.rank_score.desc())
        )
        return list(self.session.execute(stmt).scalars())

    def shortlist(self, candidate_id: str) -> list[models.Opportunity]:
        stmt = (
            select(models.Opportunity)
            .where(
                models.Opportunity.candidate_id == candidate_id,
                models.Opportunity.shortlisted.is_(True),
            )
            .order_by(models.Opportunity.rank_score.desc())
        )
        return list(self.session.execute(stmt).scalars())

    def count_by_status(self, candidate_id: str | None = None) -> dict[str, int]:
        stmt = select(models.Opportunity.status, func.count()).group_by(models.Opportunity.status)
        if candidate_id:
            stmt = stmt.where(models.Opportunity.candidate_id == candidate_id)
        return {row[0]: row[1] for row in self.session.execute(stmt)}


class FitAssessmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, opportunity_id: str, candidate_id: str, payload: dict[str, Any]) -> models.FitAssessmentRow:
        row = self.session.execute(
            select(models.FitAssessmentRow).where(
                models.FitAssessmentRow.opportunity_id == opportunity_id
            )
        ).scalars().first()
        if row is None:
            row = models.FitAssessmentRow(opportunity_id=opportunity_id, candidate_id=candidate_id)
            self.session.add(row)
        for key, value in payload.items():
            setattr(row, key, value)
        self.session.flush()
        return row

    def for_opportunity(self, opportunity_id: str) -> models.FitAssessmentRow | None:
        return self.session.execute(
            select(models.FitAssessmentRow).where(
                models.FitAssessmentRow.opportunity_id == opportunity_id
            )
        ).scalars().first()


class ResearchRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, opportunity_id: str, payload: dict[str, Any], engine: str) -> models.ResearchPacketRow:
        row = self.session.execute(
            select(models.ResearchPacketRow).where(
                models.ResearchPacketRow.opportunity_id == opportunity_id
            )
        ).scalars().first()
        if row is None:
            row = models.ResearchPacketRow(opportunity_id=opportunity_id)
            self.session.add(row)
        row.payload = payload
        row.engine = engine
        self.session.flush()
        return row

    def for_opportunity(self, opportunity_id: str) -> models.ResearchPacketRow | None:
        return self.session.execute(
            select(models.ResearchPacketRow).where(
                models.ResearchPacketRow.opportunity_id == opportunity_id
            )
        ).scalars().first()


class PackageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        package: ApplicationPackageSchema,
        *,
        status: PackageStatus = PackageStatus.DRAFT,
        truth_clean: bool = True,
    ) -> models.ApplicationPackageRow:
        max_revision = self.session.execute(
            select(func.max(models.ApplicationPackageRow.revision)).where(
                models.ApplicationPackageRow.opportunity_id == package.opportunity_id
            )
        ).scalar()
        payload = package.model_dump(mode="json")
        row = models.ApplicationPackageRow(
            opportunity_id=package.opportunity_id,
            job_id=package.job_id,
            candidate_id=package.candidate_id,
            master_resume_id=package.master_resume_id,
            revision=(max_revision or 0) + 1,
            status=str(status),
            payload=payload,
            content_hash=package.content_hash or content_hash(package.semantic_payload()),
            truth_clean=truth_clean,
            unresolved_count=len(package.unresolved_questions),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, package_id: str) -> models.ApplicationPackageRow:
        row = self.session.get(models.ApplicationPackageRow, package_id)
        if row is None:
            raise NotFound("application_package", package_id)
        return row

    def latest_for_opportunity(self, opportunity_id: str) -> models.ApplicationPackageRow | None:
        stmt = (
            select(models.ApplicationPackageRow)
            .where(models.ApplicationPackageRow.opportunity_id == opportunity_id)
            .order_by(models.ApplicationPackageRow.revision.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def set_status(self, package_id: str, status: PackageStatus) -> models.ApplicationPackageRow:
        row = self.get(package_id)
        row.status = str(status)
        self.session.flush()
        return row

    def schema(self, row: models.ApplicationPackageRow) -> ApplicationPackageSchema:
        return ApplicationPackageSchema.model_validate(row.payload)

    def list_pending_approval(self, candidate_id: str | None = None) -> list[models.ApplicationPackageRow]:
        stmt = select(models.ApplicationPackageRow).where(
            models.ApplicationPackageRow.status == str(PackageStatus.AWAITING_APPROVAL)
        )
        if candidate_id:
            stmt = stmt.where(models.ApplicationPackageRow.candidate_id == candidate_id)
        return list(self.session.execute(stmt).scalars())

    def list_all(self, candidate_id: str | None = None) -> list[models.ApplicationPackageRow]:
        stmt = select(models.ApplicationPackageRow).order_by(
            models.ApplicationPackageRow.created_at.desc()
        )
        if candidate_id:
            stmt = stmt.where(models.ApplicationPackageRow.candidate_id == candidate_id)
        return list(self.session.execute(stmt).scalars())


class ApplicationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        opportunity_id: str,
        package_id: str,
        job_id: str,
        candidate_id: str,
        master_resume_id: str | None,
        tailored_resume_id: str | None,
        answers: list[dict[str, Any]],
    ) -> models.ApplicationRow:
        row = models.ApplicationRow(
            opportunity_id=opportunity_id,
            package_id=package_id,
            job_id=job_id,
            candidate_id=candidate_id,
            master_resume_id=master_resume_id,
            tailored_resume_id=tailored_resume_id,
            answers=answers,
            status=str(ApplicationStatus.DRAFT),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, application_id: str) -> models.ApplicationRow:
        row = self.session.get(models.ApplicationRow, application_id)
        if row is None:
            raise NotFound("application", application_id)
        return row

    def for_opportunity(self, opportunity_id: str) -> models.ApplicationRow | None:
        stmt = select(models.ApplicationRow).where(
            models.ApplicationRow.opportunity_id == opportunity_id
        )
        return self.session.execute(stmt).scalars().first()

    def set_status(
        self, application_id: str, target: ApplicationStatus, **fields: Any
    ) -> models.ApplicationRow:
        row = self.get(application_id)
        assert_application_transition(ApplicationStatus(row.status), target)
        row.status = str(target)
        for key, value in fields.items():
            setattr(row, key, value)
        self.session.flush()
        return row

    def list_for_candidate(self, candidate_id: str) -> list[models.ApplicationRow]:
        stmt = (
            select(models.ApplicationRow)
            .where(models.ApplicationRow.candidate_id == candidate_id)
            .order_by(models.ApplicationRow.created_at.desc())
        )
        return list(self.session.execute(stmt).scalars())

    def count_by_status(self, candidate_id: str | None = None) -> dict[str, int]:
        stmt = select(models.ApplicationRow.status, func.count()).group_by(
            models.ApplicationRow.status
        )
        if candidate_id:
            stmt = stmt.where(models.ApplicationRow.candidate_id == candidate_id)
        return {row[0]: row[1] for row in self.session.execute(stmt)}


class OutcomeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        *,
        application_id: str,
        kind: str,
        opportunity_id: str | None = None,
        notes: str | None = None,
        source: str = "manual",
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> models.OutcomeRow:
        row = models.OutcomeRow(
            application_id=application_id,
            opportunity_id=opportunity_id,
            kind=kind,
            notes=notes,
            source=source,
            payload=payload or {},
            occurred_at=occurred_at or utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def for_application(self, application_id: str) -> list[models.OutcomeRow]:
        stmt = (
            select(models.OutcomeRow)
            .where(models.OutcomeRow.application_id == application_id)
            .order_by(models.OutcomeRow.occurred_at)
        )
        return list(self.session.execute(stmt).scalars())

    def list_all(self, candidate_id: str | None = None) -> list[models.OutcomeRow]:
        stmt = select(models.OutcomeRow).order_by(models.OutcomeRow.occurred_at.desc())
        if candidate_id:
            stmt = stmt.join(
                models.ApplicationRow,
                models.ApplicationRow.id == models.OutcomeRow.application_id,
            ).where(models.ApplicationRow.candidate_id == candidate_id)
        return list(self.session.execute(stmt).scalars())


class ProviderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        *,
        kind: str,
        token: str,
        display_name: str,
        industry: str | None = None,
        locator: str | None = None,
        enabled: bool = True,
        notes: str | None = None,
    ) -> models.ProviderRow:
        row = self.session.execute(
            select(models.ProviderRow).where(
                models.ProviderRow.kind == kind, models.ProviderRow.token == token
            )
        ).scalars().first()
        if row is None:
            row = models.ProviderRow(
                kind=kind, token=token, display_name=display_name, industry=industry,
                locator=locator, enabled=enabled, notes=notes,
            )
            self.session.add(row)
        else:
            row.display_name = display_name
            row.industry = industry or row.industry
            row.locator = locator or row.locator
            row.enabled = enabled
            if notes is not None:
                row.notes = notes
        self.session.flush()
        return row

    def record_success(self, provider_id: str, discovered: int) -> models.ProviderRow:
        row = self.session.get(models.ProviderRow, provider_id)
        if row is None:
            raise NotFound("provider", provider_id)
        row.last_attempt_at = utcnow()
        row.last_success_at = row.last_attempt_at
        row.last_error = None
        row.consecutive_failures = 0
        row.total_discovered += discovered
        row.health = str(SourceHealth.HEALTHY)
        self.session.flush()
        return row

    def record_failure(self, provider_id: str, error: str) -> models.ProviderRow:
        row = self.session.get(models.ProviderRow, provider_id)
        if row is None:
            raise NotFound("provider", provider_id)
        row.last_attempt_at = utcnow()
        row.last_error = error[:2000]
        row.consecutive_failures += 1
        row.health = str(
            SourceHealth.FAILING if row.consecutive_failures >= 3 else SourceHealth.DEGRADED
        )
        self.session.flush()
        return row

    def list_all(self, *, enabled_only: bool = False) -> list[models.ProviderRow]:
        stmt = select(models.ProviderRow).order_by(models.ProviderRow.kind, models.ProviderRow.token)
        if enabled_only:
            stmt = stmt.where(models.ProviderRow.enabled.is_(True))
        return list(self.session.execute(stmt).scalars())


class DiscoveryRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(
        self, *, candidate_id: str | None, search_profile_id: str | None, sources_total: int
    ) -> models.DiscoveryRunRow:
        row = models.DiscoveryRunRow(
            candidate_id=candidate_id,
            search_profile_id=search_profile_id,
            sources_total=sources_total,
            status="RUNNING",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, run_id: str) -> models.DiscoveryRunRow:
        row = self.session.get(models.DiscoveryRunRow, run_id)
        if row is None:
            raise NotFound("discovery_run", run_id)
        return row

    def finish(self, run_id: str, **fields: Any) -> models.DiscoveryRunRow:
        row = self.get(run_id)
        for key, value in fields.items():
            setattr(row, key, value)
        row.finished_at = utcnow()
        self.session.flush()
        return row

    def latest(self) -> models.DiscoveryRunRow | None:
        stmt = (
            select(models.DiscoveryRunRow)
            .order_by(models.DiscoveryRunRow.started_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()


class ModelCallRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        provider: str,
        model: str | None,
        purpose: str,
        ok: bool,
        prompt_hash: str = "",
        response_hash: str = "",
        entity_type: str | None = None,
        entity_id: str | None = None,
        error: str | None = None,
        latency_ms: int | None = None,
    ) -> models.ModelCallRow:
        row = models.ModelCallRow(
            provider=provider,
            model=model,
            purpose=purpose,
            ok=ok,
            prompt_hash=prompt_hash,
            response_hash=response_hash,
            entity_type=entity_type,
            entity_id=entity_id,
            error=error,
            latency_ms=latency_ms,
        )
        self.session.add(row)
        self.session.flush()
        return row
