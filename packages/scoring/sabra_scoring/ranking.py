"""Opportunity ranking and shortlist selection (requirement 13).

`rank = fit x desirability x confidence x probability of a meaningful outcome`,
combined as a weighted geometric mean rather than a raw product: a raw product
lets a single low component collapse a genuinely strong opportunity to near zero,
while a geometric mean keeps every factor binding without being brittle.

V1 deliberately uses explicit weighted logic. The features behind every factor are
stored on the assessment so a learned ranker can replace this later.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sabra_domain.identity import normalize_company
from sabra_domain.schemas import FitAssessmentSchema

EPSILON = 0.05


@dataclass(slots=True)
class RankingWeights:
    fit: float = 0.40
    desirability: float = 0.25
    confidence: float = 0.20
    outcome_probability: float = 0.15

    def as_dict(self) -> dict[str, float]:
        return {
            "fit": self.fit,
            "desirability": self.desirability,
            "confidence": self.confidence,
            "outcome_probability": self.outcome_probability,
        }


@dataclass(slots=True)
class RankedOpportunity:
    opportunity_id: str
    rank_score: float
    components: dict[str, float] = field(default_factory=dict)
    explanation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "rank_score": round(self.rank_score, 6),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "explanation": self.explanation,
        }


def desirability_score(assessment: FitAssessmentSchema) -> float:
    """How attractive the opportunity is in itself, independent of the candidate.

    Compensation and recency dominate: a well-paid role posted this week is more
    desirable than an identically-scored one from three months ago, and industry
    fit is a smaller modifier.
    """

    features = assessment.features or {}
    job = features.get("job") or {}
    compensation = _available(assessment, "compensation_score")
    industry = _available(assessment, "industry_score")
    days = job.get("days_since_posted")
    recency = 0.5 if days is None else max(0.0, 1.0 - min(days, 120) / 120.0)
    remote_bonus = 1.0 if job.get("remote_friendly") else 0.85
    return min(
        1.0,
        (0.45 * compensation + 0.35 * recency + 0.20 * industry) * remote_bonus,
    )


def outcome_probability(assessment: FitAssessmentSchema) -> float:
    """Rough probability the application produces a meaningful outcome.

    Heuristic and named as such: it is a function of evidenced qualification and
    career alignment, scaled by how much evidence existed. It is not a claim about
    the employer's behaviour, and it is preserved as a feature so real outcomes can
    eventually calibrate or replace it.
    """

    qualification = _available(assessment, "qualification_score")
    career = _available(assessment, "career_score")
    return min(1.0, (0.55 * qualification + 0.45 * career) * (0.5 + 0.5 * assessment.confidence))


def _available(assessment: FitAssessmentSchema, name: str) -> float:
    """Component score, defaulting to a neutral prior when it could not be assessed.

    The default is explicit and mid-range: substituting 0 would rank a posting
    with an unstated salary as though it paid nothing, and substituting 1 would
    rank it as though it paid best.
    """

    components = (assessment.features or {}).get("components") or {}
    entry = components.get(name)
    if not isinstance(entry, dict):
        return 0.5
    if not entry.get("available"):
        return 0.5
    return float(entry.get("score") or 0.0)


class Ranker:
    def __init__(self, weights: RankingWeights | None = None) -> None:
        self.weights = weights or RankingWeights()

    def score(self, opportunity_id: str, assessment: FitAssessmentSchema) -> RankedOpportunity:
        components = {
            "fit": assessment.fit_score,
            "desirability": desirability_score(assessment),
            "confidence": assessment.confidence,
            "outcome_probability": outcome_probability(assessment),
        }
        weights = self.weights
        weighted = (
            weights.fit * _log(components["fit"])
            + weights.desirability * _log(components["desirability"])
            + weights.confidence * _log(components["confidence"])
            + weights.outcome_probability * _log(components["outcome_probability"])
        )
        rank = _exp(weighted)
        explanation = (
            f"fit {components['fit']:.2f} x desirability {components['desirability']:.2f} "
            f"x confidence {components['confidence']:.2f} "
            f"x outcome probability {components['outcome_probability']:.2f}"
        )
        return RankedOpportunity(
            opportunity_id=opportunity_id,
            rank_score=rank,
            components=components,
            explanation=explanation,
        )

    def rank(
        self, assessments: Iterable[tuple[str, FitAssessmentSchema]]
    ) -> list[RankedOpportunity]:
        return sorted(
            (self.score(opportunity_id, assessment) for opportunity_id, assessment in assessments),
            key=lambda r: (-r.rank_score, r.opportunity_id),
        )

    def select_shortlist(
        self,
        ranked: list[RankedOpportunity],
        *,
        limit: int,
        eligible: dict[str, bool] | None = None,
        companies: dict[str, str] | None = None,
        max_per_company: int | None = None,
        stats: dict[str, int] | None = None,
    ) -> list[RankedOpportunity]:
        """Choose the top `limit` opportunities.

        Ties break on opportunity id so selection is deterministic across runs;
        `eligible` lets the caller exclude opportunities that are already in a
        later lifecycle state. When `max_per_company` and `companies` (opportunity id
        to company name) are given, no normalised company contributes more than that
        many, and the freed slots go to the next-best opportunities of other companies.

        `stats`, when given, receives `skipped_for_diversity`: how many eligible
        opportunities ranked inside the selection window (above the last one chosen)
        but were left out because their company already held `max_per_company` slots.
        """

        size = max(0, limit)
        pool = [r for r in ranked if (eligible or {}).get(r.opportunity_id, True)]
        skipped = 0
        if not max_per_company or max_per_company < 1 or not companies:
            selected = pool[:size]
        else:
            selected = []
            taken: dict[str, int] = {}
            for entry in pool:
                if len(selected) >= size:
                    break
                # An opportunity with no known company cannot be grouped, so it is never capped.
                key = normalize_company(companies.get(entry.opportunity_id)) or entry.opportunity_id
                if taken.get(key, 0) >= max_per_company:
                    skipped += 1
                    continue
                taken[key] = taken.get(key, 0) + 1
                selected.append(entry)
        if stats is not None:
            stats["skipped_for_diversity"] = skipped
        return selected


def _log(value: float) -> float:
    import math

    return math.log(max(EPSILON, min(1.0, value)))


def _exp(value: float) -> float:
    import math

    return math.exp(value)
