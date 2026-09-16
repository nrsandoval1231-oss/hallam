"""Intelligence service: deterministic filtering, fit assessment, ranking and the
opportunity queue (requirements 11, 12, 13).

Every stage is separable and independently re-runnable, and each one records why
it reached its decision. Re-running the pipeline is idempotent: an opportunity's
lifecycle state decides which stages apply to it, so a second pass does not
re-reject, re-qualify or re-shortlist anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sabra_domain.enums import EntityType, EventType, JobStatus
from sabra_domain.errors import NotFound
from sabra_domain.schemas import CandidateProfile, FitAssessmentSchema, SearchProfile
from sabra_scoring.features import (
    CandidateFeatures,
    IntentFeatures,
    JobFeatures,
    extract_candidate_features,
    extract_intent_features,
    extract_job_features,
)
from sabra_scoring.fit import HeuristicFitEngine
from sabra_scoring.hard_filter import HardFilter
from sabra_scoring.ranking import Ranker

from .context import EngineContext, Repos

# States the deterministic filter may still act on.
FILTERABLE = {JobStatus.DISCOVERED, JobStatus.NORMALIZED}
# States eligible for ranking and shortlist selection.
RANKABLE = {
    JobStatus.QUALIFIED,
    JobStatus.SHORTLISTED,
    JobStatus.RESEARCHED,
    JobStatus.PREPARED,
    JobStatus.AWAITING_APPROVAL,
    JobStatus.APPROVED,
}


@dataclass(slots=True)
class IntelligenceResult:
    considered: int = 0
    rejected: int = 0
    qualified: int = 0
    already_decided: int = 0
    assessed: int = 0
    ranked: int = 0
    shortlisted: int = 0
    rejection_breakdown: dict[str, int] = field(default_factory=dict)
    candidate_populated: bool = False
    top: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "rejected": self.rejected,
            "qualified": self.qualified,
            "already_decided": self.already_decided,
            "assessed": self.assessed,
            "ranked": self.ranked,
            "shortlisted": self.shortlisted,
            "rejection_breakdown": dict(
                sorted(self.rejection_breakdown.items(), key=lambda kv: -kv[1])
            ),
            "candidate_populated": self.candidate_populated,
            "top": self.top,
            "notes": self.notes,
        }


class IntelligenceService:
    def __init__(
        self,
        ctx: EngineContext,
        *,
        filter_engine: HardFilter | None = None,
        fit_engine: HeuristicFitEngine | None = None,
        ranker: Ranker | None = None,
    ) -> None:
        self.ctx = ctx
        self.filter_engine = filter_engine or HardFilter()
        self.fit_engine = fit_engine or HeuristicFitEngine()
        self.ranker = ranker or Ranker()

    def run(
        self,
        *,
        candidate_id: str,
        search_profile_id: str,
        shortlist_limit: int | None = None,
        rescore: bool = False,
    ) -> IntelligenceResult:
        result = IntelligenceResult()
        with self.ctx.repos() as repos:
            profile = self._load_candidate_profile(repos, candidate_id)
            search = repos.search_profiles.get(search_profile_id)
            candidate = extract_candidate_features(profile)
            intent = extract_intent_features(search)
            result.candidate_populated = candidate.populated
            if not candidate.populated:
                result.notes.append(
                    "candidate profile carries no evidence: fit is scored on the posting "
                    "and search intent only, and confidence is correspondingly low"
                )

            opportunities = repos.opportunities.list_for_candidate(candidate_id)
            scoped = [o for o in opportunities if (o.search_profile_id or "") == search_profile_id]
            result.considered = len(scoped)

            assessments: list[tuple[str, FitAssessmentSchema]] = []
            for opportunity in scoped:
                status = JobStatus(opportunity.status)
                if status in FILTERABLE or rescore:
                    decision = self._filter(repos, opportunity, search, intent, candidate)
                    if not decision:
                        result.already_decided += 1
                        continue
                    if decision.reasons:
                        result.rejected += 1
                        for reason in decision.reasons:
                            result.rejection_breakdown[reason] = (
                                result.rejection_breakdown.get(reason, 0) + 1
                            )
                        continue
                    result.qualified += 1
                status = JobStatus(repos.opportunities.get(opportunity.id).status)
                if status in RANKABLE:
                    assessment = self._assess(repos, opportunity, search, intent, candidate)
                    if assessment is not None:
                        result.assessed += 1
                        assessments.append((opportunity.id, assessment))

            ranked = self.ranker.rank(assessments)
            for position, entry in enumerate(ranked, start=1):
                repos.fit.upsert(
                    entry.opportunity_id,
                    candidate_id,
                    {"rank_score": entry.rank_score},
                )
                repos.opportunities.set_rank(entry.opportunity_id, entry.rank_score, position)
            result.ranked = len(ranked)

            limit = shortlist_limit or search.shortlist_target
            # Already-shortlisted opportunities stay eligible. Restricting
            # eligibility to QUALIFIED made the second run's selection empty, which
            # then looked like a demotion and tried an illegal
            # SHORTLISTED -> SHORTLISTED transition.
            eligible = {
                opportunity.id: True
                for opportunity in scoped
                if JobStatus(repos.opportunities.get(opportunity.id).status)
                in {JobStatus.QUALIFIED, JobStatus.SHORTLISTED}
            }
            currently_shortlisted = {
                opportunity.id
                for opportunity in scoped
                if JobStatus(opportunity.status) == JobStatus.SHORTLISTED
            }
            selected = self.ranker.select_shortlist(ranked, limit=limit, eligible=eligible)
            chosen = {entry.opportunity_id for entry in selected}

            for entry in selected:
                repos.opportunities.set_shortlist(
                    entry.opportunity_id,
                    True,
                    reason=entry.explanation,
                )
                current = JobStatus(repos.opportunities.get(entry.opportunity_id).status)
                if current is not JobStatus.SHORTLISTED:
                    repos.opportunities.set_status(entry.opportunity_id, JobStatus.SHORTLISTED)
                if entry.opportunity_id not in currently_shortlisted:
                    result.shortlisted += 1
                    repos.events.append(
                        EntityType.OPPORTUNITY,
                        entry.opportunity_id,
                        EventType.JOB_SHORTLISTED,
                        entry.as_dict(),
                    )
            # An opportunity that has fallen out of the shortlist is demoted, not
            # deleted: the queue is a projection of the scores, and history stays.
            for opportunity in scoped:
                if opportunity.id in currently_shortlisted and opportunity.id not in chosen:
                    repo_row = repos.opportunities.get(opportunity.id)
                    repo_row.shortlisted = False
                    repo_row.shortlist_reason = "demoted: no longer in the top shortlist"
                    repo_row.status = str(JobStatus.QUALIFIED)

            result.top = [
                {
                    "opportunity_id": entry.opportunity_id,
                    "rank_score": round(entry.rank_score, 6),
                    "title": repos.opportunities.get(entry.opportunity_id).title,
                    "company": repos.opportunities.get(entry.opportunity_id).company,
                    "location": repos.opportunities.get(entry.opportunity_id).location,
                    "components": {k: round(v, 4) for k, v in entry.components.items()},
                }
                for entry in ranked[: max(limit, 10)]
            ]
        return result

    # -- stages ---------------------------------------------------------

    def _load_candidate_profile(self, repos: Repos, candidate_id: str) -> CandidateProfile:
        try:
            return repos.candidates.active_profile(candidate_id)
        except NotFound:
            from .candidate_intake import placeholder_candidate_profile

            return placeholder_candidate_profile(candidate_id)

    def _primary_job(self, repos: Repos, opportunity: Any) -> Any:
        job_id = opportunity.primary_job_id
        if job_id:
            try:
                return repos.jobs.get(job_id)
            except NotFound:
                pass
        jobs = repos.opportunities.list_jobs(opportunity.id)
        if not jobs:
            raise NotFound("job_for_opportunity", opportunity.id)
        # Deterministic fallback: the longest description carries the most evidence.
        return sorted(jobs, key=lambda j: (-len(j.description or ""), j.id))[0]

    def _filter(
        self,
        repos: Repos,
        opportunity: Any,
        search: SearchProfile,
        intent: IntentFeatures,
        candidate: CandidateFeatures,
    ) -> Any:
        job_row = self._primary_job(repos, opportunity)
        features = extract_job_features(job_row)
        decision = self.filter_engine.evaluate(features, search, intent, candidate)
        if decision.reasons:
            record = decision.as_record(evaluated_at=datetime.now(UTC))
            repos.opportunities.set_status(
                opportunity.id, JobStatus.REJECTED, rejection=record.model_dump(mode="json")
            )
            repos.events.append(
                EntityType.OPPORTUNITY,
                opportunity.id,
                EventType.JOB_REJECTED,
                {
                    "title": opportunity.title,
                    "company": opportunity.company,
                    "reasons": decision.reasons,
                    "detail": decision.detail,
                    "checks_run": decision.checks_run,
                    "checks_skipped": decision.checks_skipped,
                },
            )
        else:
            if JobStatus(opportunity.status) != JobStatus.SHORTLISTED:
                repos.opportunities.set_status(opportunity.id, JobStatus.QUALIFIED)
            repos.events.append(
                EntityType.OPPORTUNITY,
                opportunity.id,
                EventType.JOB_SCORED,
                {
                    "stage": "hard_filter_passed",
                    "checks_run": decision.checks_run,
                    "checks_skipped": decision.checks_skipped,
                },
            )
        return decision

    def _assess(
        self,
        repos: Repos,
        opportunity: Any,
        search: SearchProfile,
        intent: IntentFeatures,
        candidate: CandidateFeatures,
    ) -> FitAssessmentSchema | None:
        job_row = self._primary_job(repos, opportunity)
        features = extract_job_features(job_row)
        assessment = self.fit_engine.assess(features, search, intent, candidate)
        repos.fit.upsert(
            opportunity.id,
            candidate.candidate_id,
            {
                "engine": assessment.engine,
                "fit_score": assessment.fit_score,
                "career_score": assessment.career_score,
                "qualification_score": assessment.qualification_score,
                "compensation_score": assessment.compensation_score,
                "location_score": assessment.location_score,
                "industry_score": assessment.industry_score,
                "trajectory_score": assessment.trajectory_score,
                "confidence": assessment.confidence,
                "strengths": assessment.strengths,
                "gaps": assessment.gaps,
                "risks": assessment.risks,
                "why_this_job": assessment.why_this_job,
                "component_rationale": assessment.component_rationale,
                "features": assessment.features,
            },
        )
        return assessment


def job_features_for(repos: Repos, opportunity: Any) -> JobFeatures:
    """Convenience accessor used by the research and document stages."""

    job_id = opportunity.primary_job_id
    if job_id:
        try:
            return extract_job_features(repos.jobs.get(job_id))
        except NotFound:
            pass
    jobs = repos.opportunities.list_jobs(opportunity.id)
    if not jobs:
        raise NotFound("job_for_opportunity", opportunity.id)
    return extract_job_features(sorted(jobs, key=lambda j: (-len(j.description or ""), j.id))[0])
