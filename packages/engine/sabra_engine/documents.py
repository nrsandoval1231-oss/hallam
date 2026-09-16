"""Document service: master résumé ingestion and application-package preparation.

This is the M4 orchestration layer. It owns the two invariants the rest of the
product depends on:

* exactly one master résumé per candidate, and every tailored résumé derives from
  it rather than from a previous tailored résumé (requirement 16 — no drift); and
* a package is never presented as ready when it is not, so an incomplete package is
  stored as INCOMPLETE with its open questions rather than quietly filled in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sabra_documents.master_resume import parse_master_resume
from sabra_documents.package import BuiltPackage, PackageFactory
from sabra_domain.enums import EntityType, EventType, JobStatus, PackageStatus
from sabra_domain.errors import NotFound, ValidationFailure
from sabra_domain.identity import content_hash
from sabra_domain.schemas import CandidateProfile, FitAssessmentSchema

from .context import EngineContext, Repos


@dataclass(slots=True)
class PreparationResult:
    opportunity_id: str
    job_id: str
    package_id: str
    application_id: str
    status: PackageStatus
    complete: bool
    content_hash: str
    unresolved_questions: list[str]
    missing_inputs: list[str]
    violations: list[str]
    tailored_resume_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "job_id": self.job_id,
            "package_id": self.package_id,
            "application_id": self.application_id,
            "status": str(self.status),
            "complete": self.complete,
            "content_hash": self.content_hash,
            "unresolved_questions": self.unresolved_questions,
            "missing_inputs": self.missing_inputs,
            "truth_violations": self.violations,
            "tailored_resume_id": self.tailored_resume_id,
        }


class DocumentService:
    def __init__(self, ctx: EngineContext, *, factory: PackageFactory | None = None) -> None:
        self.ctx = ctx
        self.factory = factory or PackageFactory()

    # -- master résumé --------------------------------------------------

    def ingest_master_resume(
        self,
        candidate_id: str,
        text: str,
        *,
        label: str | None = "Master résumé",
        source_format: str = "text",
        source_path: str | None = None,
        parsed: dict[str, Any] | None = None,
        promote_identity: bool = True,
    ) -> str:
        """Store the master résumé and return its id.

        The master is authoritative and immutable per version: re-ingesting stores a
        new version rather than mutating the old one, so history is preserved.
        """

        document = parse_master_resume(text, parsed=parsed)
        with self.ctx.repos() as repos:
            repos.candidates.get(candidate_id)
            row = repos.resumes.add(
                candidate_id,
                text,
                is_master=True,
                label=label,
                source_format=source_format,
                source_path=source_path,
                parsed={
                    "headline": document.headline,
                    "summary": document.summary,
                    "employment": [e.model_dump(mode="json") for e in document.employment],
                    "education": [e.model_dump(mode="json") for e in document.education],
                    "certifications": [c.model_dump(mode="json") for c in document.certifications],
                    "skills": [s.model_dump(mode="json") for s in document.skills],
                    "projects": [p.model_dump(mode="json") for p in document.projects],
                    "contact": document.contact.model_dump(mode="json"),
                    "unparsed_line_count": len(document.unparsed_lines),
                    "warnings": document.warnings,
                },
            )
            repos.events.append(
                EntityType.RESUME,
                row.id,
                EventType.RESUME_INGESTED,
                {
                    "candidate_id": candidate_id,
                    "version": row.version,
                    "content_hash": row.content_hash,
                    "employment_entries": len(document.employment),
                    "skills": len(document.skills),
                    "warnings": document.warnings,
                },
            )
            if promote_identity:
                self._merge_resume_into_profile(repos, candidate_id, document)
            return row.id

    def _merge_resume_into_profile(self, repos: Repos, candidate_id: str, document) -> None:
        """Fold résumé-derived values into the profile as INFERRED.

        Résumé content is evidence *for* the candidate model, never a statement by
        the candidate, so it enters the profile marked INFERRED. A value the owner
        already supplied is never overwritten.
        """

        from sabra_domain.enums import TruthLevel
        from sabra_domain.schemas import Provenance

        try:
            profile = repos.candidates.active_profile(candidate_id)
        except NotFound:
            profile = CandidateProfile(candidate_id=candidate_id)

        inferred = Provenance(
            level=TruthLevel.INFERRED,
            evidence=["master_resume"],
            note="read from the master résumé, not stated by the candidate",
        )

        def fill(attr: str, value: Any, replace_empty_only: bool = True) -> None:
            if value in (None, "", [], {}):
                return
            current = getattr(profile, attr)
            current_provenance = profile.provenance.get(attr)
            inferred_value = (
                current_provenance is not None and current_provenance.level is TruthLevel.INFERRED
            )
            if replace_empty_only and current not in (None, "", [], {}) and not inferred_value:
                return
            setattr(profile, attr, value)
            profile.provenance[attr] = inferred

        fill("headline", document.headline)
        fill("summary", document.summary)
        fill("employment", document.employment)
        fill("education", document.education)
        fill("certifications", document.certifications)
        fill("skills", document.skills)
        fill("projects", document.projects)
        for attr, value in (
            ("full_name", document.contact.full_name),
            ("email", document.contact.email),
            ("phone", document.contact.phone),
            ("city", document.contact.city),
            ("state", document.contact.state),
            ("linkedin_url", document.contact.linkedin_url),
            ("website_url", document.contact.website_url),
        ):
            if value:
                provenance = profile.provenance.get(f"identity.{attr}")
                inferred_value = provenance is not None and provenance.level is TruthLevel.INFERRED
                if not getattr(profile.identity, attr) or inferred_value:
                    setattr(profile.identity, attr, value)
                    profile.provenance[f"identity.{attr}"] = inferred

        repos.candidates.save_profile(candidate_id, profile, source="master_resume")

    def attach_resume_to_opportunity(self, opportunity_id: str, resume_id: str) -> None:
        """Record the master résumé version used for an opportunity's packages.

        Stored on the package/application (`master_resume_id`), not on the
        opportunity: an opportunity can be re-prepared after the master is updated,
        and the ledger must show which version each application actually used.
        """

        with self.ctx.repos() as repos:
            for package_row in repos.packages.list_all():
                if package_row.opportunity_id == opportunity_id:
                    package_row.master_resume_id = resume_id
            repos.session.flush()

    # -- packages -------------------------------------------------------

    def prepare_package(
        self,
        opportunity_id: str,
        *,
        questions: list[str] | None = None,
        include_cover_letter: bool = True,
    ) -> PreparationResult:
        """Generate, persist and queue one application package for approval."""

        with self.ctx.repos() as repos:
            opportunity = repos.opportunities.get(opportunity_id)
            candidate_id = opportunity.candidate_id
            profile = self._profile(repos, candidate_id)
            master_row = repos.resumes.master(candidate_id)
            if master_row is None:
                raise ValidationFailure(
                    "no master résumé is stored for this candidate; "
                    "an application package cannot be prepared without one"
                )
            master_document = parse_master_resume(
                master_row.content_text, parsed=master_row.parsed or None
            )
            job_row = self._primary_job(repos, opportunity)
            assessment = self._assessment(repos, opportunity_id)
            research = repos.research.for_opportunity(opportunity_id)

            built = self.factory.build(
                profile=profile,
                master=master_document,
                job=job_row,
                master_resume_id=master_row.id,
                job_id=job_row.id,
                opportunity_id=opportunity_id,
                assessment=assessment,
                questions=questions if questions is not None else self._default_questions(),
                research=(research.payload if research else None),
            )

            tailored_row = repos.resumes.add(
                candidate_id,
                built.schema.tailored_resume_markdown or "",
                is_master=False,
                label=f"Tailored — {job_row.title[:80]}",
                source_format="markdown",
                derived_from_resume_id=master_row.id,
                opportunity_id=opportunity_id,
            )

            package_row = repos.packages.create(
                built.schema,
                status=built.status,
                truth_clean=built.violations == [],
            )
            # Recorded on the package row, not inside the payload: the payload's hash
            # is the approval scope, so it must not gain fields after hashing.
            package_row.tailored_resume_id = tailored_row.id
            repos.session.flush()
            application_row = self._ensure_application(
                repos, opportunity, package_row, master_row, tailored_row, built
            )

            status = JobStatus(opportunity.status)
            if status is JobStatus.SHORTLISTED:
                repos.opportunities.set_status(opportunity_id, JobStatus.PREPARED)
            repos.opportunities.set_status(opportunity_id, JobStatus.AWAITING_APPROVAL)

            repos.events.append(
                EntityType.PACKAGE,
                package_row.id,
                EventType.PACKAGE_GENERATED,
                {
                    "opportunity_id": opportunity_id,
                    "job_id": job_row.id,
                    "revision": package_row.revision,
                    "content_hash": package_row.content_hash,
                    "status": str(built.status),
                    "complete": built.complete,
                    "unresolved_questions": built.schema.unresolved_questions,
                    "missing_inputs": built.missing_inputs,
                    "truth_violations": built.violations,
                    "engine": built.schema.engine,
                },
            )
            repos.events.append(
                EntityType.APPLICATION,
                application_row.id,
                EventType.APPLICATION_STARTED,
                {"stage": "awaiting_approval", "package_id": package_row.id},
            )

            return PreparationResult(
                opportunity_id=opportunity_id,
                job_id=job_row.id,
                package_id=package_row.id,
                application_id=application_row.id,
                status=built.status,
                complete=built.complete,
                content_hash=package_row.content_hash,
                unresolved_questions=list(built.schema.unresolved_questions),
                missing_inputs=list(built.missing_inputs),
                violations=list(built.violations),
                tailored_resume_id=tailored_row.id,
            )

    def prepare_top(
        self,
        candidate_id: str,
        *,
        limit: int = 3,
        search_profile_id: str | None = None,
        questions: list[str] | None = None,
    ) -> list[PreparationResult]:
        """Prepare packages for the highest-ranked shortlisted opportunities."""

        results: list[PreparationResult] = []
        with self.ctx.read_repos() as repos:
            shortlist = repos.opportunities.shortlist(candidate_id)
            if search_profile_id:
                shortlist = [
                    o for o in shortlist if (o.search_profile_id or "") == search_profile_id
                ]
            targets = [o.id for o in shortlist[: max(0, limit)]]
        for opportunity_id in targets:
            results.append(self.prepare_package(opportunity_id, questions=questions))
        return results

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _default_questions() -> list[str]:
        """The questions the product always asks of the candidate's memory.

        These are the ones every board asks; board-specific questions are added when
        an adapter inspects a real application form.
        """

        return [
            "Are you legally authorized to work in the United States?",
            "Will you now or in the future require sponsorship for employment visa status?",
            "What are your salary expectations?",
            "When can you start?",
            "Are you willing to undergo a background check?",
            "Are you willing to relocate?",
            "Are you able to travel as required by this role?",
        ]

    def _profile(self, repos: Repos, candidate_id: str) -> CandidateProfile:
        try:
            return repos.candidates.active_profile(candidate_id)
        except NotFound:
            from .candidate_intake import placeholder_candidate_profile

            return placeholder_candidate_profile(candidate_id)

    @staticmethod
    def _primary_job(repos: Repos, opportunity: Any) -> Any:
        if opportunity.primary_job_id:
            try:
                return repos.jobs.get(opportunity.primary_job_id)
            except NotFound:
                pass
        jobs = repos.opportunities.list_jobs(opportunity.id)
        if not jobs:
            raise NotFound("job_for_opportunity", opportunity.id)
        return sorted(jobs, key=lambda j: (-len(j.description or ""), j.id))[0]

    @staticmethod
    def _assessment(repos: Repos, opportunity_id: str) -> FitAssessmentSchema | None:
        row = repos.fit.for_opportunity(opportunity_id)
        if row is None:
            return None
        return FitAssessmentSchema(
            fit_score=row.fit_score,
            career_score=row.career_score,
            qualification_score=row.qualification_score,
            compensation_score=row.compensation_score,
            location_score=row.location_score,
            industry_score=row.industry_score,
            trajectory_score=row.trajectory_score,
            confidence=row.confidence,
            strengths=list(row.strengths or []),
            gaps=list(row.gaps or []),
            risks=list(row.risks or []),
            why_this_job=row.why_this_job or "",
            component_rationale=dict(row.component_rationale or {}),
            engine=row.engine,
            features=dict(row.features or {}),
            rank_score=row.rank_score,
        )

    @staticmethod
    def _ensure_application(
        repos: Repos,
        opportunity: Any,
        package_row: Any,
        master_row: Any,
        tailored_row: Any,
        built: BuiltPackage,
    ) -> Any:
        """One application per opportunity (requirement 9).

        Re-preparing a package updates the existing application's package reference
        rather than creating a second one, because the unique constraint on
        `applications.opportunity_id` is what makes duplicate applications impossible.
        """

        existing = repos.applications.for_opportunity(opportunity.id)
        answers = [a.model_dump(mode="json") for a in built.schema.answers]
        if existing is not None:
            if existing.status in {"APPLIED", "APPLYING"}:
                repos.events.append(
                    EntityType.APPLICATION,
                    existing.id,
                    EventType.PACKAGE_GENERATED,
                    {
                        "note": "package regenerated after submission; existing application kept",
                        "package_id": package_row.id,
                    },
                )
                return existing
            existing.package_id = package_row.id
            existing.master_resume_id = master_row.id
            existing.tailored_resume_id = tailored_row.id
            existing.answers = answers
            repos.session.flush()
            return existing

        row = repos.applications.create(
            opportunity_id=opportunity.id,
            package_id=package_row.id,
            job_id=package_row.job_id,
            candidate_id=opportunity.candidate_id,
            master_resume_id=master_row.id,
            tailored_resume_id=tailored_row.id,
            answers=answers,
        )
        from sabra_domain.enums import ApplicationStatus

        repos.applications.set_status(row.id, ApplicationStatus.AWAITING_APPROVAL)
        return repos.applications.get(row.id)


def package_summary(package_row: Any) -> dict[str, Any]:
    payload = dict(package_row.payload or {})
    return {
        "package_id": package_row.id,
        "opportunity_id": package_row.opportunity_id,
        "revision": package_row.revision,
        "status": package_row.status,
        "content_hash": package_row.content_hash,
        "truth_clean": package_row.truth_clean,
        "unresolved_count": package_row.unresolved_count,
        "warnings": payload.get("warnings", []),
        "qualification_gaps": payload.get("qualification_gaps", []),
        "answers": payload.get("answers", []),
        "generated_at": package_row.created_at.astimezone(UTC).isoformat()
        if package_row.created_at
        else None,
    }


def package_content_hash(schema: Any) -> str:
    return content_hash(schema.semantic_payload()) if schema else ""


__all__ = [
    "DocumentService",
    "PreparationResult",
    "package_summary",
    "package_content_hash",
    "datetime",
]
