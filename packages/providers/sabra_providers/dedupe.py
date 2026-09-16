"""Deterministic deduplication (requirement 9).

One real-world opening must become one canonical opportunity even when several
sources advertise it. The rules below use only signals named in the contract —
company, title, location, source ids, canonical URL, description similarity,
posting date — and are deliberately conservative: merging two genuinely distinct
openings is worse than leaving a near-duplicate visible, because a bad merge
silently deletes an opportunity the candidate never sees.

The grouping is order-independent: records are sorted before grouping and the
first matching group in that fixed order wins, so the same input always produces
the same output.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from sabra_domain.identity import (
    canonical_key,
    normalize_company,
    normalize_location,
    normalize_text,
    normalize_title,
)
from sabra_domain.schemas import CanonicalJob

DEFAULT_TITLE_THRESHOLD = 0.92
DEFAULT_DESCRIPTION_THRESHOLD = 0.75

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "the", "of", "for", "to", "in", "on", "at", "with", "as",
        "is", "are", "be", "you", "your", "we", "our", "this", "that", "will", "role",
        "job", "position", "opportunity", "team", "company",
    }
)
_TOKEN = re.compile(r"[a-z0-9]+")


def token_set(text: str | None) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN.findall((text or "").lower())
        if token not in _STOPWORDS and len(token) > 1
    )


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_similarity(left: str | None, right: str | None) -> float:
    """Token-set Jaccard plus sequence ratio.

    Jaccard alone misses word order; the sequence ratio alone misses reordering.
    Taking the max of the two matches how recruiters actually restate a title.
    """

    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    tokens_a, tokens_b = token_set(a), token_set(b)
    return max(jaccard(tokens_a, tokens_b), SequenceMatcher(None, a, b).ratio())


@dataclass(slots=True)
class JobKey:
    """The dedup-relevant projection of a job posting."""

    source: str
    source_job_id: str
    company_norm: str
    title_norm: str
    location_norm: str
    remote_status: str
    source_url: str
    title: str
    description_excerpt: str = ""
    date_posted: str | None = None

    @classmethod
    def from_canonical(cls, job: CanonicalJob) -> JobKey:
        return cls(
            source=str(job.source),
            source_job_id=job.source_job_id,
            company_norm=normalize_company(job.company),
            title_norm=normalize_title(job.title),
            location_norm=normalize_location(job.location),
            remote_status=str(job.remote_status),
            source_url=job.source_url,
            title=job.title,
            description_excerpt=(job.description or "")[:4000],
            date_posted=job.date_posted.isoformat() if job.date_posted else None,
        )

    @classmethod
    def from_row(cls, row: Any) -> JobKey:
        return cls(
            source=str(row.source),
            source_job_id=str(row.source_job_id),
            company_norm=normalize_company(row.company),
            title_norm=normalize_title(row.title),
            location_norm=normalize_location(row.location),
            remote_status=str(row.remote_status),
            source_url=str(row.source_url),
            title=str(row.title),
            description_excerpt=(row.description or "")[:4000],
            date_posted=row.date_posted.isoformat() if row.date_posted else None,
        )

    def group_key(self) -> str:
        return canonical_key(self.company_norm, self.title_norm, self.location_norm)

    def as_source_entry(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_job_id": self.source_job_id,
            "url": self.source_url,
            "date_posted": self.date_posted,
        }


@dataclass(slots=True)
class MergeDecision:
    merged: bool
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OpportunityGroup:
    """One canonical opportunity and the postings that describe it."""

    canonical_key: str
    members: list[JobKey] = field(default_factory=list)
    merge_reasons: list[str] = field(default_factory=list)

    @property
    def primary(self) -> JobKey:
        return self.members[0]

    @property
    def title(self) -> str:
        return self.primary.title

    @property
    def company_norm(self) -> str:
        return self.primary.company_norm

    @property
    def location_norm(self) -> str:
        return self.primary.location_norm

    @property
    def source_count(self) -> int:
        return len(self.members)

    def add(self, key: JobKey, reason: str) -> None:
        self.members.append(key)
        self.merge_reasons.append(reason)


def location_compatible(left: str, right: str) -> bool:
    """Whether two normalized locations can describe the same opening.

    Compatible when identical, when either is unknown, or when one is a token
    subset of the other (a board that lists "dallas tx" versus the same opening
    advertised with an extra site). "Dallas, TX" and "Houston, TX" are NOT
    compatible: they are two different openings.
    """

    if left == right:
        return True
    if not left or not right:
        return True
    tokens_left, tokens_right = set(left.split()), set(right.split())
    return tokens_left <= tokens_right or tokens_right <= tokens_left


def company_compatible(left: str, right: str) -> bool:
    """Whether two normalized company names can be the same employer.

    Needed because sources declare the same employer differently: Greenhouse
    reports `"Revance "` while a registry label or another board reports
    "Revance Therapeutics". Compatibility requires one name's tokens to be a
    *prefix* of the other's in order — not merely a subset — so "acme health" and
    "health acme" stay distinct, and a bare generic token cannot pull two
    employers together.
    """

    if left == right:
        return True
    if not left or not right:
        return False
    tokens_left, tokens_right = left.split(), right.split()
    shorter, longer = (
        (tokens_left, tokens_right) if len(tokens_left) <= len(tokens_right) else (tokens_right, tokens_left)
    )
    if len(shorter) > len(longer):
        return False
    return longer[: len(shorter)] == shorter


class Deduplicator:
    def __init__(
        self,
        *,
        title_threshold: float = DEFAULT_TITLE_THRESHOLD,
        description_threshold: float = DEFAULT_DESCRIPTION_THRESHOLD,
    ) -> None:
        self.title_threshold = title_threshold
        self.description_threshold = description_threshold

    def should_merge(
        self, candidate: JobKey, incumbent: JobKey, *, same_source: bool = False
    ) -> MergeDecision:
        """Decide whether `candidate` is the same real-world opening as `incumbent`."""

        if not company_compatible(candidate.company_norm, incumbent.company_norm):
            return MergeDecision(False, "different_company")
        if same_source and candidate.source_job_id == incumbent.source_job_id:
            return MergeDecision(True, "same_source_id", {"source_job_id": candidate.source_job_id})

        titles_equal = candidate.title_norm == incumbent.title_norm
        locations_ok = location_compatible(candidate.location_norm, incumbent.location_norm)

        # An identical canonical URL is conclusive: it is literally the same page.
        if candidate.source_url and candidate.source_url == incumbent.source_url:
            return MergeDecision(True, "identical_source_url", {"url": candidate.source_url})

        if titles_equal and locations_ok:
            description_similarity = jaccard(
                token_set(candidate.description_excerpt),
                token_set(incumbent.description_excerpt),
            )
            # An identical title in an identical location is not by itself proof
            # of one opening. Measured on real boards: a pair of "Clinical Field
            # Specialist" postings in San Diego described *different territories*
            # and were two distinct jobs. Merging them would hide an opportunity
            # and, because an application is unique per opportunity, would block a
            # second application. So the ad copy has to corroborate, unless one
            # side has no copy at all.
            has_both = bool(candidate.description_excerpt and incumbent.description_excerpt)
            if not has_both or description_similarity >= self.description_threshold:
                reason = (
                    "exact_title_location"
                    if candidate.location_norm == incumbent.location_norm
                    else "title_match_compatible_location"
                )
                return MergeDecision(
                    True,
                    reason,
                    {
                        "title_norm": candidate.title_norm,
                        "location_left": candidate.location_norm,
                        "location_right": incumbent.location_norm,
                        "description_similarity": round(description_similarity, 4),
                    },
                )
            return MergeDecision(
                False,
                "same_title_different_description",
                {"description_similarity": round(description_similarity, 4)},
            )

        if locations_ok:
            similarity = title_similarity(candidate.title, incumbent.title)
            if similarity >= self.title_threshold:
                description_similarity = jaccard(
                    token_set(candidate.description_excerpt),
                    token_set(incumbent.description_excerpt),
                )
                # A near-identical title is not enough on its own: two districts
                # can post the same title with different scope. Require the ad
                # copy to corroborate, unless one side has no copy at all.
                if description_similarity >= self.description_threshold or not (
                    candidate.description_excerpt and incumbent.description_excerpt
                ):
                    return MergeDecision(
                        True,
                        "near_title_match",
                        {
                            "title_similarity": round(similarity, 4),
                            "description_similarity": round(description_similarity, 4),
                        },
                    )
                return MergeDecision(
                    False,
                    "near_title_different_description",
                    {
                        "title_similarity": round(similarity, 4),
                        "description_similarity": round(description_similarity, 4),
                    },
                )
        return MergeDecision(False, "distinct_locations" if not locations_ok else "distinct_titles")


def group_records(records: Iterable[JobKey], dedup: Deduplicator | None = None) -> list[OpportunityGroup]:
    """Group postings into canonical opportunities deterministically.

    Ordering is by normalized company, title, location then source id, so the
    representative posting of a group does not depend on iteration order or on
    which source happened to be ingested first.
    """

    dedup = dedup or Deduplicator()
    ordered = sorted(
        records,
        key=lambda k: (
            k.company_norm,
            k.title_norm,
            k.location_norm,
            k.source,
            k.source_job_id,
        ),
    )
    groups: list[OpportunityGroup] = []
    for record in ordered:
        target: OpportunityGroup | None = None
        for group in groups:
            decision = dedup.should_merge(record, group.primary)
            if decision.merged:
                target = group
                target.add(record, decision.reason)
                break
        if target is None:
            groups.append(
                OpportunityGroup(canonical_key=record.group_key(), members=[record])
            )
    return groups


def duplicate_report(groups: Iterable[OpportunityGroup]) -> dict[str, Any]:
    """Counts for the pilot report: how many postings collapsed into how many openings."""

    groups = list(groups)
    total_postings = sum(g.source_count for g in groups)
    multi = [g for g in groups if g.source_count > 1]
    return {
        "canonical_opportunities": len(groups),
        "postings_ingested": total_postings,
        "duplicates_removed": total_postings - len(groups),
        "multi_source_opportunities": len(multi),
        "cross_source_pairs": sum(
            1
            for g in multi
            if len({m.source for m in g.members}) > 1
        ),
    }


def normalize_excerpt(text: str | None, limit: int = 4000) -> str:
    return normalize_text(text)[:limit]
