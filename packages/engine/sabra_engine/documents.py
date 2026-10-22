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

from sabra_documents.generate import PackageGenerator
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


class PreparationConflict(ValidationFailure):
    """The state a package was built against changed before it could be saved."""


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
        """Generate, persist and queue one application package for approval.

        Three phases, so a slow model call never holds the database:

        1. a read scope gathers everything the package is built from, plus a snapshot of
           the state it was built against;
        2. the package is built (including every model call and the truth gate) with NO
           session open;
        3. a short write scope re-reads that state. If it moved (status, a newer package
           revision, another profile version or master résumé, an edited job) nothing is
           saved and `PreparationConflict` says so. Otherwise it persists the package, the
           `model_calls` rows and the events.
        """

        # -- 1. read ----------------------------------------------------------------
        with self.ctx.read_repos() as repos:
            opportunity = repos.opportunities.get(opportunity_id)
            self._refuse_if_already_approved(repos, opportunity)
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
            # The stored research packet (persisted when the opportunity was ranked)
            # reaches the package. Its VERIFIED facts count as evidence for the
            # model-written company sentence; its inferences never do.
            research = repos.research.for_opportunity(opportunity_id)
            research_payload = dict(research.payload) if research and research.payload else None
            prior = self._prior_generation(repos, opportunity_id)
            seen = self._snapshot(repos, opportunity, profile, master_row, job_row)
            master_row_id = master_row.id
            job_row_id = job_row.id

        # -- 2. build: no session is open while the model runs ------------------------------
        generator = self._make_generator(prior)
        try:
            built = self.factory.build(
                profile=profile,
                master=master_document,
                job=job_row,
                master_resume_id=master_row_id,
                job_id=job_row_id,
                opportunity_id=opportunity_id,
                assessment=assessment,
                questions=questions if questions is not None else self._default_questions(),
                research=research_payload,
                generator=generator,
            )
        except BaseException:
            with self.ctx.repos() as repos:
                self._persist_model_calls(repos, generator)
            raise

        # -- 3. persist, if the world is still the one the package was built for ----------------
        conflict: str | None = None
        result: PreparationResult | None = None
        with self.ctx.repos() as repos:
            # What the model cost is recorded whether or not the package is kept.
            self._persist_model_calls(repos, generator)
            conflict = self._conflict(repos, opportunity_id, candidate_id, seen)
            if conflict is None:
                result = self._persist_package(
                    repos, opportunity_id, built, master_row_id, job_row_id
                )
        if conflict is not None or result is None:
            raise PreparationConflict(
                f"the package for {opportunity_id} was not saved: {conflict}. Nothing changed; "
                "prepare it again to build against the current state"
            )
        return result

    # -- prepare_package helpers -------------------------------------------------

    @staticmethod
    def _job_digest(job_row: Any) -> str:
        return content_hash(
            [
                getattr(job_row, name, None) or ""
                for name in (
                    "raw_source_hash",
                    "title",
                    "company",
                    "location",
                    "description",
                    "requirements",
                    "preferred_qualifications",
                    "responsibilities",
                )
            ]
        )

    def _snapshot(
        self, repos: Repos, opportunity: Any, profile: CandidateProfile, master_row: Any, job_row: Any
    ) -> dict[str, Any]:
        """The state a package is built against; compared again just before it is saved."""

        latest = repos.packages.latest_for_opportunity(opportunity.id)
        application = repos.applications.for_opportunity(opportunity.id)
        return {
            "opportunity_status": str(opportunity.status),
            "application_status": str(application.status) if application else None,
            "latest_package": (latest.id, latest.revision) if latest else None,
            "profile_version": profile.version,
            "master_resume": (master_row.id, master_row.content_hash),
            "job": (job_row.id, self._job_digest(job_row)),
            # A re-rank or a research refresh during generation would leave a package
            # built on stale inputs; both are part of the state the package is bound to.
            "research": self._research_digest(repos, opportunity.id),
            "assessment": self._assessment_digest(repos, opportunity.id),
        }

    @staticmethod
    def _research_digest(repos: Repos, opportunity_id: str) -> str | None:
        research = repos.research.for_opportunity(opportunity_id)
        if research is None or not research.payload:
            return None
        return content_hash(dict(research.payload))

    def _assessment_digest(self, repos: Repos, opportunity_id: str) -> str | None:
        assessment = self._assessment(repos, opportunity_id)
        if assessment is None:
            return None
        return content_hash(assessment.model_dump(mode="json"))

    def _conflict(
        self, repos: Repos, opportunity_id: str, candidate_id: str, seen: dict[str, Any]
    ) -> str | None:
        """Why the package can no longer be saved, or None if the state is unchanged."""

        opportunity = repos.opportunities.get(opportunity_id)
        try:
            self._refuse_if_already_approved(repos, opportunity)
        except ValidationFailure as exc:
            return f"the application was approved or moved on while the package was generating ({exc})"
        master_row = repos.resumes.master(candidate_id)
        if master_row is None:
            return "the master résumé was removed while the package was generating"
        job_row = self._primary_job(repos, opportunity)
        now = self._snapshot(repos, opportunity, self._profile(repos, candidate_id), master_row, job_row)
        labels = {
            "opportunity_status": "the opportunity's status changed",
            "application_status": "the application's status changed",
            "latest_package": "a newer package revision was saved",
            "profile_version": "the candidate profile changed",
            "master_resume": "the master résumé changed",
            "research": "the research packet changed",
            "assessment": "the fit assessment changed",
            "job": "the job posting changed",
        }
        moved = [labels[key] for key in labels if now[key] != seen[key]]
        return "; ".join(moved) if moved else None

    def _persist_package(
        self,
        repos: Repos,
        opportunity_id: str,
        built: BuiltPackage,
        master_row_id: str,
        job_row_id: str,
    ) -> PreparationResult:
        opportunity = repos.opportunities.get(opportunity_id)
        candidate_id = opportunity.candidate_id
        master_row = repos.resumes.get(master_row_id)
        job_row = repos.jobs.get(job_row_id)

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

    @staticmethod
    def _refuse_if_already_approved(repos: Repos, opportunity: Any) -> None:
        """An approved application is not prepared again.

        A new package would get a new content hash and silently detach the
        approval the owner already gave. Later submission states are the same
        refusal: the package they approved is the one that may be submitted.
        """

        from sabra_domain.enums import ApplicationStatus

        existing = repos.applications.for_opportunity(opportunity.id)
        locked_application = {
            ApplicationStatus.APPROVED.value,
            ApplicationStatus.APPLYING.value,
            ApplicationStatus.APPLIED.value,
        }
        locked_job = {JobStatus.APPROVED, JobStatus.APPLYING, JobStatus.APPLIED}
        if existing is not None and existing.status in {
            ApplicationStatus.BLOCKED.value,
            ApplicationStatus.FAILED.value,
        }:
            raise ValidationFailure(
                f"already approved; this application is {existing.status} and was not prepared "
                "again. Use Refresh package (or `sabra packages refresh`) to rebuild it, "
                "which revokes the old approval so you can approve the new package"
            )
        if (existing is not None and existing.status in locked_application) or JobStatus(
            opportunity.status
        ) in locked_job:
            raise ValidationFailure(
                "already approved; this application was not prepared again "
                "because a new package would break the approval hash"
            )

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _prior_generation(repos: Repos, opportunity_id: str) -> dict[str, Any] | None:
        """The stored generation record of the latest package, for reuse on unchanged inputs."""

        latest = repos.packages.latest_for_opportunity(opportunity_id)
        if latest is None:
            return None
        record = ((latest.payload or {}).get("truth_ledger") or {}).get("generation")
        return dict(record) if isinstance(record, dict) else None

    def _make_generator(self, prior: dict[str, Any] | None) -> PackageGenerator | None:
        """A generator over the configured model, or None when there is no model.

        With no model configured the deterministic path runs untouched.
        """

        llm = self.ctx.llm
        if not llm.available or llm.name == "none":
            return None
        return PackageGenerator(llm, prior=prior)

    @staticmethod
    def _persist_model_calls(repos: Repos, generator: PackageGenerator | None) -> None:
        """Write one `model_calls` row per call the generator made.

        Token counts are passed on only if the repository accepts them; the table
        predates them. They are always kept in the package's `generation` ledger.
        """

        if generator is None or not generator.records:
            return
        import inspect

        accepted = set(inspect.signature(repos.model_calls.record).parameters)
        for call in generator.records:
            kwargs: dict[str, Any] = {
                "provider": call.provider,
                "model": call.model,
                "purpose": call.purpose,
                "ok": call.ok,
                "prompt_hash": call.prompt_hash,
                "response_hash": call.response_hash,
                "entity_type": call.entity_type,
                "entity_id": call.entity_id,
                "error": call.error,
                "latency_ms": call.latency_ms,
            }
            for name in ("input_tokens", "output_tokens", "cache_read_tokens"):
                if name in accepted:
                    kwargs[name] = getattr(call, name)
            repos.model_calls.record(**kwargs)

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
