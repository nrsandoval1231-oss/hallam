"""Intelligence service: deterministic filtering, fit assessment, ranking and the
opportunity queue (requirements 11, 12, 13).

Every stage is separable and independently re-runnable, and each one records why
it reached its decision. Re-running the pipeline is idempotent: an opportunity's
lifecycle state decides which stages apply to it, so a second pass does not
re-reject, re-qualify or re-shortlist anything.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sabra_domain.enums import (
    ApplicationStatus,
    EntityType,
    EventType,
    JobStatus,
    PackageStatus,
    RejectionReason,
)
from sabra_domain.errors import NotFound, ValidationFailure
from sabra_domain.schemas import CandidateProfile, FitAssessmentSchema, SearchProfile
from sabra_domain.state import can_transition_job
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


# Opportunities filtered, assessed and written per transaction.
DEFAULT_BATCH_SIZE = 50
# Past the filter already: a rescore that still passes it leaves the state alone.
BEYOND_QUALIFIED = {
    JobStatus.SHORTLISTED,
    JobStatus.RESEARCHED,
    JobStatus.PREPARED,
    JobStatus.AWAITING_APPROVAL,
    JobStatus.APPROVED,
}

ProgressCallback = Callable[[dict[str, Any]], None]


def _notify(progress: ProgressCallback | None, payload: dict[str, Any]) -> None:
    """Report progress. A progress sink that fails must never fail the pass."""

    if progress is None:
        return
    try:
        progress(payload)
    except Exception:  # noqa: BLE001 - observability only
        return


def _passed_by_owner(opportunity: Any) -> bool:
    rejection = opportunity.rejection or {}
    return RejectionReason.OWNER_PASSED.value in (rejection.get("reasons") or [])


# Rejection reasons the hard filter writes. A rejection with any other reason (the owner's
# Pass, a declined package) is a human decision and is never reversed by a rescore.
FILTER_REASONS = frozenset(r.value for r in RejectionReason) - {RejectionReason.OWNER_PASSED.value}
# States a rescore may re-filter. Past these (a prepared package, an approval, a
# submission) the opportunity is left exactly as it is: rejecting it would orphan the
# package or application, and the owner already acted on it.
REFILTERABLE = {JobStatus.QUALIFIED, JobStatus.SHORTLISTED, JobStatus.RESEARCHED}


def _may_refilter(repos: Repos, opportunity: Any, status: JobStatus) -> bool:
    """Whether a rescore may re-run the hard filter on an opportunity already decided.

    Allowed for QUALIFIED, SHORTLISTED and RESEARCHED rows that have no application, and
    for REJECTED rows whose rejection came from the filter itself. A row that reached
    PREPARED, AWAITING_APPROVAL or beyond, or that has an application, is left alone.
    """

    if status is JobStatus.REJECTED:
        reasons = set((opportunity.rejection or {}).get("reasons") or [])
        if not reasons or not reasons <= FILTER_REASONS:
            return False
    elif status not in REFILTERABLE:
        return False
    return repos.applications.for_opportunity(opportunity.id) is None


@dataclass(slots=True)
class IntelligenceResult:
    considered: int = 0
    rejected: int = 0
    qualified: int = 0
    already_decided: int = 0
    assessed: int = 0
    ranked: int = 0
    shortlisted: int = 0
    # Opportunities passed over because their company already held `max_per_company` slots.
    skipped_for_diversity: int = 0
    # Previously rejected opportunities a rescore found to pass the current rules.
    reinstated: int = 0
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
            "skipped_for_diversity": self.skipped_for_diversity,
            "reinstated": self.reinstated,
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
        progress: ProgressCallback | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> IntelligenceResult:
        """Filter, assess, rank and shortlist.

        The pass commits in batches of `batch_size` opportunities rather than holding one
        write transaction for the whole queue: a thousand-posting pass would otherwise
        keep the SQLite write lock for minutes and lock the dashboard out. Every stage is
        state-driven and idempotent, so a pass that stops part-way is simply run again.
        `progress` receives a small dict after each batch.
        """

        result = IntelligenceResult()
        batch_size = max(1, batch_size)
        _notify(progress, {"stage": "load"})

        with self.ctx.read_repos() as repos:
            profile = self._load_candidate_profile(repos, candidate_id)
            search = repos.search_profiles.get(search_profile_id)
            candidate = extract_candidate_features(profile)
            intent = extract_intent_features(search)
            scoped_ids = [
                o.id
                for o in repos.opportunities.list_for_candidate(candidate_id)
                if (o.search_profile_id or "") == search_profile_id
            ]
        result.candidate_populated = candidate.populated
        if not candidate.populated:
            result.notes.append(
                "candidate profile carries no evidence: fit is scored on the posting "
                "and search intent only, and confidence is correspondingly low"
            )
        result.considered = len(scoped_ids)

        assessments: list[tuple[str, FitAssessmentSchema]] = []
        for start in range(0, len(scoped_ids), batch_size):
            chunk = scoped_ids[start : start + batch_size]
            with self.ctx.repos() as repos:
                for opportunity_id in chunk:
                    opportunity = repos.opportunities.get(opportunity_id)
                    if _passed_by_owner(opportunity):
                        # An owner's Pass is a decision, not a score: no later pass,
                        # rescore included, brings the role back.
                        result.already_decided += 1
                        continue
                    status = JobStatus(opportunity.status)
                    if status in FILTERABLE or (
                        rescore and _may_refilter(repos, opportunity, status)
                    ):
                        decision = self._filter(repos, opportunity, search, intent, candidate)
                        if not decision:
                            result.already_decided += 1
                            continue
                        if decision.reinstated:
                            result.reinstated += 1
                        if decision.reasons:
                            result.rejected += 1
                            for reason in decision.reasons:
                                result.rejection_breakdown[reason] = (
                                    result.rejection_breakdown.get(reason, 0) + 1
                                )
                            continue
                        result.qualified += 1
                    status = JobStatus(repos.opportunities.get(opportunity_id).status)
                    if status in RANKABLE:
                        assessment = self._assess(repos, opportunity, search, intent, candidate)
                        if assessment is not None:
                            result.assessed += 1
                            assessments.append((opportunity_id, assessment))
            _notify(
                progress,
                {
                    "stage": "assess",
                    "done": min(start + batch_size, len(scoped_ids)),
                    "total": len(scoped_ids),
                    "rejected": result.rejected,
                    "qualified": result.qualified,
                    "assessed": result.assessed,
                },
            )

        ranked = self.ranker.rank(assessments)
        for start in range(0, len(ranked), batch_size):
            with self.ctx.repos() as repos:
                for position, entry in enumerate(
                    ranked[start : start + batch_size], start=start + 1
                ):
                    repos.fit.upsert(
                        entry.opportunity_id,
                        candidate_id,
                        {"rank_score": entry.rank_score},
                    )
                    repos.opportunities.set_rank(entry.opportunity_id, entry.rank_score, position)
            _notify(
                progress,
                {"stage": "rank", "done": min(start + batch_size, len(ranked)), "total": len(ranked)},
            )
        result.ranked = len(ranked)

        limit = shortlist_limit or search.shortlist_target
        with self.ctx.repos() as repos:
            # Every ranked opportunity competes for a slot, including ones already shortlisted
            # or researched (research moves SHORTLISTED to RESEARCHED) and ones whose package
            # is prepared or awaiting approval: they keep their place and their state.
            # Restricting the pool to QUALIFIED made a second run's selection empty, which
            # then looked like a demotion. Only a QUALIFIED opportunity changes state here;
            # moving an advanced one back to SHORTLISTED is an illegal transition (a rank
            # after preparing a package used to fail on exactly that).
            statuses = {
                opportunity_id: JobStatus(repos.opportunities.get(opportunity_id).status)
                for opportunity_id in scoped_ids
            }
            currently_shortlisted = {
                opportunity_id
                for opportunity_id, status in statuses.items()
                if status in {JobStatus.SHORTLISTED, JobStatus.RESEARCHED}
            }
            # The assess batches committed minutes ago; an opportunity may since have been
            # passed, expired or sent to the employer. Only rows still QUALIFIED or already
            # past it may take a slot, and an owner's Pass is never reversed. Filtering
            # before selection means such a row neither consumes a slot nor fails the pass.
            def may_take_a_slot(opportunity_id: str) -> bool:
                status = statuses[opportunity_id]
                if status is not JobStatus.QUALIFIED and status not in BEYOND_QUALIFIED:
                    return False
                return not _passed_by_owner(repos.opportunities.get(opportunity_id))

            ranked = [entry for entry in ranked if may_take_a_slot(entry.opportunity_id)]
            companies = {
                entry.opportunity_id: repos.opportunities.get(entry.opportunity_id).company
                for entry in ranked
            }
            stats: dict[str, int] = {}
            selected = self.ranker.select_shortlist(
                ranked,
                limit=limit,
                companies=companies,
                max_per_company=search.max_per_company,
                stats=stats,
            )
            result.skipped_for_diversity = stats.get("skipped_for_diversity", 0)
            chosen = {entry.opportunity_id for entry in selected}

            for entry in selected:
                repos.opportunities.set_shortlist(
                    entry.opportunity_id,
                    True,
                    reason=entry.explanation,
                )
                if statuses[entry.opportunity_id] in BEYOND_QUALIFIED:
                    continue
                repos.opportunities.set_status(entry.opportunity_id, JobStatus.SHORTLISTED)
                result.shortlisted += 1
                repos.events.append(
                    EntityType.OPPORTUNITY,
                    entry.opportunity_id,
                    EventType.JOB_SHORTLISTED,
                    entry.as_dict(),
                )
            # An opportunity that has fallen out of the shortlist is demoted, not
            # deleted: the queue is a projection of the scores, and history stays.
            for opportunity_id in scoped_ids:
                if opportunity_id in currently_shortlisted and opportunity_id not in chosen:
                    if repos.applications.for_opportunity(opportunity_id) is not None:
                        # A package or application exists: dropping the role out of the
                        # shortlist would strand it, so it keeps its place and its state.
                        continue
                    repos.opportunities.set_shortlist(
                        opportunity_id, False, reason="demoted: no longer in the top shortlist"
                    )
                    # Through the graph, so the postings follow the opportunity.
                    repos.opportunities.set_status(opportunity_id, JobStatus.QUALIFIED)
                    repos.events.append(
                        EntityType.OPPORTUNITY,
                        opportunity_id,
                        EventType.JOB_SCORED,
                        {
                            "stage": "demoted_from_shortlist",
                            "from": statuses[opportunity_id].value,
                            "reason": "no longer in the top shortlist",
                        },
                    )

            result.top = [
                {
                    "opportunity_id": entry.opportunity_id,
                    "rank_score": round(entry.rank_score, 6),
                    "title": row.title,
                    "company": row.company,
                    "location": row.location,
                    "components": {k: round(v, 4) for k, v in entry.components.items()},
                }
                # The same per-company cap as the shortlist, so the table the CLI prints
                # matches what was actually shortlisted.
                for entry in self.ranker.select_shortlist(
                    ranked,
                    limit=max(limit, 10),
                    companies=companies,
                    max_per_company=search.max_per_company,
                )
                for row in (repos.opportunities.get(entry.opportunity_id),)
            ]
        _notify(progress, {"stage": "shortlist", "shortlisted": result.shortlisted})
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
        current = JobStatus(opportunity.status)
        if decision.reasons:
            if current is JobStatus.REJECTED and set(decision.reasons) == set(
                (opportunity.rejection or {}).get("reasons") or []
            ):
                # Rejected before, for the same reasons: nothing to record again.
                return None
            record = decision.as_record(evaluated_at=datetime.now(UTC))
            repos.opportunities.set_status(
                opportunity.id, JobStatus.REJECTED, rejection=record.model_dump(mode="json")
            )
            # Rejection and the shortlist flag are separate columns. A rescore that
            # newly rejects a shortlisted opportunity used to leave `shortlisted`
            # true, so the queue still showed a role the filter had just removed.
            repos.opportunities.set_shortlist(
                opportunity.id,
                False,
                reason="removed from shortlist: " + ", ".join(decision.reasons),
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
            payload: dict[str, Any] = {
                "stage": "hard_filter_passed",
                "checks_run": decision.checks_run,
                "checks_skipped": decision.checks_skipped,
            }
            if current is JobStatus.REJECTED:
                # A rule change now lets through a role an earlier pass rejected. Back
                # through the graph (the explicit rescore edge), with the old reasons kept
                # in the event so history shows what changed.
                payload["reinstated_from"] = (opportunity.rejection or {}).get("reasons") or []
                repos.opportunities.set_status(opportunity.id, JobStatus.QUALIFIED, revoke=True)
                opportunity.rejection = None
                decision.reinstated = True
            elif current not in BEYOND_QUALIFIED:
                repos.opportunities.set_status(opportunity.id, JobStatus.QUALIFIED)
            repos.events.append(
                EntityType.OPPORTUNITY, opportunity.id, EventType.JOB_SCORED, payload
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


def pass_opportunity(
    ctx: EngineContext, opportunity_id: str, *, decided_by: str = "owner"
) -> dict[str, Any]:
    """The owner passes on an opportunity (PRD section 29, "Pass").

    The opportunity and its postings move to REJECTED through the job transition graph
    with reason OWNER_PASSED, and a later ranking pass never brings it back. A prepared
    package that was waiting for approval is superseded and its application withdrawn.
    An application past AWAITING_APPROVAL (approved, applying, applied, blocked, failed)
    is refused: the owner already acted on it, and passing silently would orphan an
    approval or a submission.
    """

    with ctx.repos() as repos:
        opportunity = repos.opportunities.get(opportunity_id)
        application = repos.applications.for_opportunity(opportunity_id)
        if application is not None and application.status not in {
            ApplicationStatus.DRAFT.value,
            ApplicationStatus.AWAITING_APPROVAL.value,
            ApplicationStatus.WITHDRAWN.value,
        }:
            raise ValidationFailure(
                f"cannot pass on this opportunity: its application is {application.status}, "
                "past awaiting approval. Use the application's own controls instead"
            )
        current = JobStatus(opportunity.status)
        if current is JobStatus.REJECTED and _passed_by_owner(opportunity):
            return {
                "opportunity_id": opportunity_id,
                "status": opportunity.status,
                "already_passed": True,
            }
        if current is JobStatus.EXPIRED:
            raise ValidationFailure(
                "cannot pass on this opportunity: the posting has expired, so it is already "
                "off your list"
            )
        if current is not JobStatus.REJECTED and not can_transition_job(
            current, JobStatus.REJECTED
        ):
            raise ValidationFailure(
                f"cannot pass on this opportunity: it is {current.value}, and a role in that "
                "state cannot be passed on"
            )

        # A role the filter already rejected keeps its reasons; the pass is added to them.
        earlier = opportunity.rejection or {}
        reasons = [r for r in (earlier.get("reasons") or []) if r != RejectionReason.OWNER_PASSED]
        rejection = {
            "reasons": [*reasons, RejectionReason.OWNER_PASSED.value],
            "detail": {**(earlier.get("detail") or {}), "decided_by": decided_by},
        }
        if earlier.get("evaluated_at"):
            rejection["evaluated_at"] = earlier["evaluated_at"]
        package = repos.packages.latest_for_opportunity(opportunity_id)
        if package is not None and package.status not in {
            PackageStatus.SUPERSEDED.value,
            PackageStatus.APPROVED.value,
        }:
            repos.packages.set_status(package.id, PackageStatus.SUPERSEDED)
            repos.events.append(
                EntityType.PACKAGE,
                package.id,
                EventType.PACKAGE_REJECTED,
                {"reason": RejectionReason.OWNER_PASSED.value, "decided_by": decided_by},
                actor=decided_by,
            )
        if application is not None and application.status != ApplicationStatus.WITHDRAWN.value:
            repos.applications.set_status(application.id, ApplicationStatus.WITHDRAWN)
            repos.events.append(
                EntityType.APPLICATION,
                application.id,
                EventType.REJECTED,
                {"reason": RejectionReason.OWNER_PASSED.value, "decided_by": decided_by},
                actor=decided_by,
            )
        repos.opportunities.set_status(opportunity_id, JobStatus.REJECTED, rejection=rejection)
        repos.opportunities.set_shortlist(opportunity_id, False, reason="passed by the owner")
        repos.events.append(
            EntityType.OPPORTUNITY,
            opportunity_id,
            EventType.JOB_REJECTED,
            {
                "title": opportunity.title,
                "company": opportunity.company,
                "reasons": [RejectionReason.OWNER_PASSED.value],
                "decided_by": decided_by,
            },
            actor=decided_by,
        )
        return {
            "opportunity_id": opportunity_id,
            "status": opportunity.status,
            "already_passed": False,
        }
