"""Explainable fit assessment (requirement 12).

Scores must be explainable, so nothing here returns a bare number: every
component carries a rationale, components that cannot be assessed from available
evidence are marked unavailable and excluded from the weighting (rather than
being filled in with a neutral guess), and `confidence` reports how much of the
assessment actually rested on data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sabra_domain.enums import RemoteStatus
from sabra_domain.identity import normalize_location, normalize_text
from sabra_domain.schemas import (
    CandidateProfile,
    FitAssessmentSchema,
    SearchProfile,
)

from .features import (
    CandidateFeatures,
    IntentFeatures,
    JobFeatures,
    annualized_salary,
)

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "the", "of", "for", "to", "in", "on", "at", "with", "as", "is",
        "are", "be", "or", "you", "your", "we", "our", "this", "that", "will", "must",
        "have", "has", "2", "3", "4", "5", "years", "year", "experience", "ability",
        "strong", "excellent", "including", "etc", "plus",
    }
)


@dataclass(slots=True)
class FitWeights:
    """Weights for the composite fit score. Sum should be 1.0."""

    qualification: float = 0.32
    career: float = 0.18
    compensation: float = 0.16
    location: float = 0.14
    industry: float = 0.10
    trajectory: float = 0.10

    def as_dict(self) -> dict[str, float]:
        return {
            "qualification": self.qualification,
            "career": self.career,
            "compensation": self.compensation,
            "location": self.location,
            "industry": self.industry,
            "trajectory": self.trajectory,
        }


@dataclass(slots=True)
class Component:
    """One assessed dimension."""

    name: str
    score: float
    available: bool
    rationale: str
    details: dict[str, Any] = field(default_factory=dict)


def _content_tokens(text: str | None) -> set[str]:
    from .features import token_set

    return {t for t in token_set(text) if t not in _STOPWORDS and len(t) > 1}


class HeuristicFitEngine:
    """Deterministic, weighted, fully explainable scorer.

    Chosen over a learned model deliberately (requirement 13): V1 needs ranking
    that can be argued with, and the underlying features are preserved on every
    assessment so a learned ranker can replace this later without losing history.
    """

    def __init__(self, weights: FitWeights | None = None) -> None:
        self.weights = weights or FitWeights()

    # -- components -----------------------------------------------------

    def _qualification(
        self, job: JobFeatures, candidate: CandidateFeatures
    ) -> Component:
        items = job.requirement_items or job.preferred_items
        source = "requirements" if job.requirement_items else "preferred qualifications"
        if not items:
            return Component(
                "qualification_score",
                0.0,
                False,
                "the posting lists no requirements or preferred qualifications to assess",
                {"items": 0},
            )
        if not candidate.populated:
            return Component(
                "qualification_score",
                0.0,
                False,
                (
                    f"the posting lists {len(items)} requirement items but no candidate "
                    "evidence exists yet to compare against"
                ),
                {"items": len(items), "covered": 0},
            )

        covered: list[str] = []
        partial: list[str] = []
        uncovered: list[str] = []
        for item in items:
            tokens = _content_tokens(item)
            if not tokens:
                continue
            hit = len(tokens & candidate.evidence_tokens) / len(tokens)
            if hit >= 0.6:
                covered.append(item)
            elif hit >= 0.25:
                partial.append(item)
            else:
                uncovered.append(item)

        total = len(covered) + len(partial) + len(uncovered)
        if total == 0:
            return Component(
                "qualification_score", 0.0, False, "no assessable requirement content", {}
            )
        score = (len(covered) + 0.4 * len(partial)) / total
        return Component(
            "qualification_score",
            round(score, 4),
            True,
            (
                f"{len(covered)} of {total} {source} items are supported by candidate "
                f"evidence ({len(partial)} partial)"
            ),
            {
                "source": source,
                "items": total,
                "covered": len(covered),
                "partial": len(partial),
                "uncovered": len(uncovered),
                "examples_uncovered": uncovered[:3],
            },
        )

    def _career(self, job: JobFeatures, intent: IntentFeatures) -> Component:
        if not intent.title_ngrams:
            return Component(
                "career_score", 0.0, False, "the search profile names no target titles", {}
            )
        title_tokens = set(job.title_tokens)
        exact = [
            t
            for t in intent.target_titles
            if set(normalize_text(t).lower().split()) <= title_tokens
        ]
        if exact:
            strength, note = 1.0, f"title matches target role {exact[0]!r} exactly"
        else:
            matched = [g for g in intent.title_ngrams if g in job.normalized_title]
            strength = 0.75 if matched else 0.0
            note = (
                f"title matches the acceptable role family via {sorted(matched)[:2]}"
                if matched
                else "title does not match a target or acceptable role"
            )
        return Component(
            "career_score",
            strength,
            True,
            note,
            {"target_match": bool(exact), "matched_ngrams": sorted(matched) if not exact else []},
        )

    def _compensation(
        self, job: JobFeatures, search: SearchProfile
    ) -> Component:
        low, high = annualized_salary(job)
        floor = search.compensation.minimum
        target = search.compensation.target
        if high is None:
            posted = (
                "posted hourly, which cannot be compared with an annual target"
                if job.salary_period == "HOUR"
                else "no compensation is posted"
            )
            return Component("compensation_score", 0.0, False, posted, {"posted": bool(job.salary_min)})
        if floor is None and target is None:
            return Component(
                "compensation_score",
                0.5,
                False,
                "no compensation target is set in the search profile",
                {"posted_high": high},
            )
        reference = target or floor or 0.0
        if reference <= 0:
            return Component("compensation_score", 0.5, False, "no target set", {})
        ratio = high / reference
        score = min(1.0, max(0.0, (ratio - 0.6) / 0.6))
        if target and high >= target:
            note = f"top of range {high:,.0f} meets the {target:,.0f} target"
        elif floor and high >= floor:
            note = f"top of range {high:,.0f} clears the {floor:,.0f} minimum but not the {target:,.0f} target"
        else:
            note = f"top of range {high:,.0f} is below both floor and target"
        return Component(
            "compensation_score",
            round(score, 4),
            True,
            note,
            {"posted_low": low, "posted_high": high, "reference": reference, "period": job.salary_period},
        )

    def _location(self, job: JobFeatures, search: SearchProfile) -> Component:
        if not job.location and job.remote_status == RemoteStatus.UNKNOWN:
            return Component("location_score", 0.0, False, "no location or arrangement stated", {})
        if job.remote_status == RemoteStatus.REMOTE:
            if search.locations.allow_remote:
                note = "remote, which the profile accepts"
                score = 1.0
            else:
                note = "remote, which the profile does not accept"
                score = 0.0
        elif job.remote_status == RemoteStatus.HYBRID:
            score = 0.9 if search.locations.allow_hybrid else 0.1
            note = f"hybrid in {job.location}" if job.location else "hybrid"
        else:
            # Tiered so a Dallas posting outranks a Houston one for a Dallas
            # candidate. Scoring every accepted location equally made geography a
            # yes/no flag and left the ranking unable to prefer the candidate's own
            # metro — which is the whole point of stating it.
            normalized = normalize_location(job.location)
            tokens = set(normalized.split())
            preferred_cities = {
                token
                for city in search.locations.cities
                for token in normalize_location(city).split()
            }
            preferred_states = {
                token
                for state in search.locations.states
                for token in normalize_location(state).split()
            }
            metro_hit = any(
                set(normalize_location(metro).split()) <= tokens
                for metro in search.locations.metro_areas
                if metro
            )
            city_hit = bool(preferred_cities & tokens)
            state_hit = bool(preferred_states & tokens)
            if city_hit:
                score, tier = 1.0, "preferred_city"
                note = f"on-site in {job.location}, one of the candidate's preferred cities"
            elif metro_hit:
                score, tier = 0.95, "preferred_metro"
                note = f"on-site in {job.location}, inside the preferred metro area"
            elif state_hit:
                score, tier = 0.6, "accepted_state"
                note = (
                    f"on-site in {job.location}, in an accepted state but not the preferred metro"
                )
            else:
                score, tier = 0.25, "outside_preference"
                note = f"on-site in {job.location}, outside the preferred cities"
            return Component(
                "location_score",
                round(score, 4),
                True,
                note,
                {"remote_status": job.remote_status, "tier": tier},
            )
        return Component("location_score", round(score, 4), True, note, {"remote_status": job.remote_status})

    def _industry(self, job: JobFeatures, intent: IntentFeatures) -> Component:
        if not intent.industry_tokens:
            return Component("industry_score", 0.0, False, "no preferred industries set", {})
        job_tokens = set(job.description_tokens) | set(job.title_tokens) | _content_tokens(job.department)
        hits = intent.industry_tokens & job_tokens
        score = len(hits) / len(intent.industry_tokens)
        return Component(
            "industry_score",
            round(min(1.0, score), 4),
            True,
            (
                f"posting mentions {len(hits)} of {len(intent.industry_tokens)} preferred industry terms "
                f"({sorted(hits)[:4]})"
                if hits
                else "posting mentions none of the preferred industry terms"
            ),
            {"hits": sorted(hits)},
        )

    def _trajectory(self, job: JobFeatures, candidate: CandidateFeatures) -> Component:
        if not candidate.populated or not candidate.stated_titles:
            return Component(
                "trajectory_score", 0.0, False, "no candidate employment history to compare", {}
            )
        delta = job.seniority_rank - candidate.highest_seniority_rank
        if delta >= 1:
            score, note = 1.0, "a step up from the candidate's most senior role"
        elif delta == 0:
            score, note = 0.85, "level with the candidate's most senior role"
        elif delta == -1:
            score, note = 0.5, "one level below the candidate's most senior role"
        else:
            score, note = 0.2, "materially below the candidate's most senior role"
        return Component(
            "trajectory_score",
            score,
            True,
            note,
            {"job_rank": job.seniority_rank, "candidate_rank": candidate.highest_seniority_rank},
        )

    # -- assembly -------------------------------------------------------

    def assess(
        self,
        job: JobFeatures,
        search: SearchProfile,
        intent: IntentFeatures,
        candidate: CandidateFeatures,
    ) -> FitAssessmentSchema:
        components = [
            self._qualification(job, candidate),
            self._career(job, intent),
            self._compensation(job, search),
            self._location(job, search),
            self._industry(job, intent),
            self._trajectory(job, candidate),
        ]

        weights = {
            "qualification_score": self.weights.qualification,
            "career_score": self.weights.career,
            "compensation_score": self.weights.compensation,
            "location_score": self.weights.location,
            "industry_score": self.weights.industry,
            "trajectory_score": self.weights.trajectory,
        }
        available = [c for c in components if c.available]
        available_weight = sum(weights[c.name] for c in available)
        if available_weight > 0:
            fit = sum(weights[c.name] * c.score for c in available) / available_weight
        else:
            fit = 0.0

        by_name = {c.name: c for c in components}
        posting_completeness = _posting_completeness(job)
        confidence = round(
            min(1.0, (available_weight / sum(weights.values())) * posting_completeness), 4
        )

        strengths, gaps, risks = _narrate(components, job, available)
        why = _why_this_job(job, by_name, strengths, gaps, fit)

        return FitAssessmentSchema(
            fit_score=round(min(1.0, max(0.0, fit)), 4),
            career_score=by_name["career_score"].score,
            qualification_score=by_name["qualification_score"].score,
            compensation_score=by_name["compensation_score"].score,
            location_score=by_name["location_score"].score,
            industry_score=by_name["industry_score"].score,
            trajectory_score=by_name["trajectory_score"].score,
            confidence=confidence,
            strengths=strengths,
            gaps=gaps,
            risks=risks,
            why_this_job=why,
            component_rationale={c.name: c.rationale for c in components},
            engine="heuristic",
            features={
                "job": job.as_dict(),
                "candidate": candidate.as_dict(),
                "components": {
                    c.name: {"score": c.score, "available": c.available, "details": c.details}
                    for c in components
                },
                "weights": self.weights.as_dict(),
                "available_weight": round(available_weight, 4),
                "posting_completeness": round(posting_completeness, 4),
                "assessed_at_index": None,
            },
        )


def _posting_completeness(job: JobFeatures) -> float:
    """How much of a posting was actually known, 0..1.

    Confidence must fall when the advert is thin, because a confident score built
    on a three-line description is exactly the "magic number" the contract forbids.
    """

    checks = [
        bool(job.description_chars > 600),
        bool(job.requirement_items),
        bool(job.location or job.remote_status != "UNKNOWN"),
        job.salary_min is not None,
        bool(job.department),
        bool(job.responsibility_items),
    ]
    return sum(1 for c in checks if c) / len(checks)


def _narrate(
    components: list[Component], job: JobFeatures, available: list[Component]
) -> tuple[list[str], list[str], list[str]]:
    strengths: list[str] = []
    gaps: list[str] = []
    risks: list[str] = []

    for component in components:
        if not component.available:
            gaps.append(f"{component.name.replace('_score', '')}: {component.rationale}")
            continue
        if component.score >= 0.75:
            strengths.append(component.rationale)
        elif component.score <= 0.4:
            gaps.append(component.rationale)

    qualification = next((c for c in components if c.name == "qualification_score"), None)
    if qualification and qualification.available and qualification.details.get("uncovered"):
        for item in qualification.details["examples_uncovered"]:
            gaps.append(f"requirement not evidenced: {item[:160]}")

    if job.days_since_posted is not None and job.days_since_posted > 60:
        risks.append(f"posted {job.days_since_posted} days ago; may already be filled")
    if job.travel_percent is not None and job.travel_percent >= 40:
        risks.append(f"travel expectation is {job.travel_percent}%")
    if job.salary_period == "HOUR":
        risks.append("compensation is posted hourly, so total pay is uncertain")
    if not available:
        risks.append("no component could be assessed from the available evidence")

    return strengths, gaps, risks


def _why_this_job(
    job: JobFeatures,
    by_name: dict[str, Component],
    strengths: list[str],
    gaps: list[str],
    fit: float,
) -> str:
    lead = (
        f"{job.title} at {job.company}"
        + (f" ({job.location})" if job.location else "")
    )
    top = sorted(
        (c for c in by_name.values() if c.available),
        key=lambda c: -c.score,
    )[:2]
    parts = [f"{lead} scores {fit:.2f} overall."]
    if top:
        parts.append("Strongest on " + " and ".join(c.rationale for c in top) + ".")
    if gaps:
        parts.append(f"Primary gap: {gaps[0]}")
    return " ".join(parts)


def assess_profile_ready(profile: CandidateProfile | None) -> bool:
    """Whether a candidate profile carries anything to reason about."""

    if profile is None:
        return False
    return bool(
        profile.employment or profile.skills or profile.summary or profile.headline
    )
