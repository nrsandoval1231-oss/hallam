"""Deterministic hard filtering (requirement 11).

Runs before any expensive analysis, and every decision it makes is explainable
from stored fields.

The governing rule is **absence of evidence is not evidence of rejection**: a
posting is only rejected on a fact that is actually known about it. An unstated
salary is not a low salary, an unstated location is not a prohibited location, and
a blank candidate profile does not fail a mandatory qualification. Unknowns are
left for the scorer to express as low confidence, because rejecting them here
would silently delete opportunities the candidate may well want.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sabra_domain.enums import EmploymentType, RejectionReason, RemoteStatus
from sabra_domain.identity import normalize_location, normalize_text, token_matches_phrase
from sabra_domain.schemas import RejectionRecord, SearchProfile

from .features import (
    CandidateFeatures,
    IntentFeatures,
    JobFeatures,
    annualized_salary,
    token_set,
)


@dataclass(slots=True)
class FilterDecision:
    """Outcome of deterministic screening for one posting."""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    detail: dict[str, str] = field(default_factory=dict)
    checks_run: list[str] = field(default_factory=list)
    checks_skipped: list[str] = field(default_factory=list)

    def as_record(self, evaluated_at: Any = None) -> RejectionRecord:
        return RejectionRecord(reasons=self.reasons, detail=self.detail, evaluated_at=evaluated_at)


def _phrase_in(haystack: str, phrase: str) -> bool:
    """Word-boundary phrase match, so 'Intern' does not match 'Internal'."""

    return token_matches_phrase(haystack, phrase)


class HardFilter:
    """Configurable deterministic screen.

    The thresholds come from the search profile wherever the profile expresses
    one, so the filter cannot drift away from the candidate's stated intent.
    """

    def __init__(self, *, max_posting_age_days: int | None = None) -> None:
        self.max_posting_age_days = max_posting_age_days

    def evaluate(
        self,
        job: JobFeatures,
        search: SearchProfile,
        intent: IntentFeatures,
        candidate: CandidateFeatures,
        *,
        today: date | None = None,
    ) -> FilterDecision:
        reasons: list[str] = []
        detail: dict[str, str] = {}
        run: list[str] = []
        skipped: list[str] = []

        def reject(reason: RejectionReason, message: str) -> None:
            if reason.value not in reasons:
                reasons.append(reason.value)
            detail[reason.value] = message

        title_lower = job.title.lower()

        # 1. Posting availability / staleness --------------------------------
        max_age = self.max_posting_age_days
        if max_age is None:
            max_age = search.max_age_days
        if max_age is not None and job.days_since_posted is not None:
            run.append("posting_age")
            if job.days_since_posted > max_age:
                reject(
                    RejectionReason.ROLE_UNAVAILABLE,
                    f"posted {job.days_since_posted} days ago (limit {max_age})",
                )
        else:
            skipped.append("posting_age")

        # 2. Excluded titles -------------------------------------------------
        run.append("excluded_title")
        for pattern in intent.excluded_title_patterns:
            if pattern and _phrase_in(title_lower, pattern):
                reject(RejectionReason.EXCLUDED_TITLE, f"title contains excluded term {pattern!r}")
                break

        # 3. Excluded seniority ---------------------------------------------
        run.append("seniority")
        excluded_seniority = {s.upper() for s in search.excluded_seniority_levels}
        if job.seniority and job.seniority.upper() in excluded_seniority:
            reject(
                RejectionReason.OUTSIDE_SEARCH_INTENT,
                f"seniority {job.seniority} is excluded by the search profile",
            )

        # 4. Title relevance (search intent) ---------------------------------
        run.append("title_relevance")
        if not _title_relevant(job, intent):
            reject(
                RejectionReason.OUTSIDE_SEARCH_INTENT,
                "title matches no target title, n-gram or declared role family",
            )

        # 5. Excluded companies ----------------------------------------------
        run.append("excluded_company")
        company_tokens = token_set(job.company)
        if intent.excluded_company_tokens & company_tokens:
            overlap = sorted(intent.excluded_company_tokens & company_tokens)
            reject(RejectionReason.EXCLUDED_COMPANY, f"company matches excluded terms {overlap}")

        # 6. Excluded keywords (commission-only, MLM, ...) --------------------
        run.append("excluded_keywords")
        for keyword in intent.excluded_keywords:
            if not keyword:
                continue
            if _phrase_in(title_lower, keyword):
                reject(RejectionReason.OUTSIDE_SEARCH_INTENT, f"title contains {keyword!r}")
                break
            # Body text is only consulted for phrases that are unambiguous: an
            # ad that *offers* commission is not the same as one paid only in
            # commission, and the employment-type check below owns that call.
            if keyword in {"mlm", "multi-level marketing", "unpaid"} and _phrase_in(
                job.description_excerpt, keyword
            ):
                reject(RejectionReason.OUTSIDE_SEARCH_INTENT, f"posting mentions {keyword!r}")
                break

        # 7. Employment type -------------------------------------------------
        run.append("employment_type")
        if job.employment_type != EmploymentType.UNKNOWN:
            excluded = {str(t) for t in search.excluded_employment_types}
            if job.employment_type in excluded:
                reject(
                    RejectionReason.INCOMPATIBLE_EMPLOYMENT_TYPE,
                    f"employment type {job.employment_type} is excluded",
                )
            elif (
                job.employment_type == str(EmploymentType.COMMISSION_ONLY)
                and not search.allow_commission_only
            ):
                reject(
                    RejectionReason.INCOMPATIBLE_EMPLOYMENT_TYPE,
                    "commission-only role and the profile does not accept commission-only",
                )
        elif search.employment_types:
            skipped.append("employment_type")

        # 8. Compensation ----------------------------------------------------
        run.append("compensation")
        low, high = annualized_salary(job)
        floor = search.compensation.minimum
        if floor is not None and high is not None:
            if high < floor:
                reject(
                    RejectionReason.BELOW_MINIMUM_COMPENSATION,
                    f"top of posted range {high:,.0f} is below the {floor:,.0f} minimum",
                )
        else:
            skipped.append("compensation")

        # 9. Geography -------------------------------------------------------
        run.append("geography")
        if not search.locations.allow_relocation and not (
            search.locations.allow_remote and job.remote_status == RemoteStatus.REMOTE
        ):
            if job.location:
                if not _location_allowed(job, intent, search):
                    reject(
                        RejectionReason.PROHIBITED_GEOGRAPHY,
                        f"{job.location!r} is outside the accepted locations and relocation is not accepted",
                    )
            else:
                skipped.append("geography")

        # 10. Travel ---------------------------------------------------------
        run.append("travel")
        if job.travel_percent is not None and search.travel_max_percent is not None:
            if job.travel_percent > search.travel_max_percent:
                reject(
                    RejectionReason.EXCESSIVE_TRAVEL,
                    f"requires up to {job.travel_percent}% travel (limit {search.travel_max_percent}%)",
                )
        else:
            skipped.append("travel")

        # 11. Mandatory qualifications ---------------------------------------
        run.append("mandatory_qualification")
        if search.minimum_qualifications:
            missing = _missing_mandatory_qualifications(job, search, candidate)
            for item in missing:
                reject(
                    RejectionReason.MISSING_MANDATORY_QUALIFICATION,
                    f"required qualification not evidenced by the candidate: {item!r}",
                )
        else:
            skipped.append("mandatory_qualification")

        return FilterDecision(
            passed=not reasons,
            reasons=reasons,
            detail=detail,
            checks_run=run,
            checks_skipped=skipped,
        )


def _title_relevant(job: JobFeatures, intent: IntentFeatures) -> bool:
    """Whether a posting's title falls inside the stated search intent.

    Three ways to qualify, all derived from the profile:

    1. a target or acceptable title's tokens are all present in the posting title;
    2. the posting title contains a contiguous n-gram drawn from those titles;
    3. the posting title contains one of the profile's declared role-family terms
       AND one of its declared level terms.

    Rule 3 exists because real titles do not reuse the profile's wording. Measured
    against live boards, an n-gram-only rule rejected "Regional Account Director -
    Texas", "Clinical Sales Exec" and "Regional Vice President I, Sales" — all
    genuine field-sales roles — while the family+level rule admits them and still
    rejects "Senior Software Engineer" and "Clinical Field Specialist".

    The vocabulary lives in the search profile rather than here: whether a title is
    really a sales role is the candidate's judgement to make, and a hard-coded list
    in the scorer would silently redefine their intent.
    """

    title_tokens = set(job.title_tokens)
    if not intent.title_ngrams and not intent.role_family_tokens:
        return True

    for declared in list(intent.target_titles) + list(intent.acceptable_titles):
        declared_tokens = set(normalize_text(declared).lower().split())
        if declared_tokens and declared_tokens <= title_tokens:
            return True

    title_phrase = " ".join(job.title_tokens)
    if any(
        re.search(rf"(?:^|\s){re.escape(gram)}(?:\s|$)", title_phrase)
        for gram in intent.title_ngrams
    ):
        return True

    if intent.role_family_tokens and intent.role_level_tokens:
        level_hit = bool(intent.role_level_tokens & title_tokens)
        family_hit = bool(intent.role_family_tokens & title_tokens) or any(
            token_matches_phrase(job.title, phrase) for phrase in intent.role_family_phrases
        )
        if family_hit and level_hit:
            return True

    return False


def _location_allowed(job: JobFeatures, intent: IntentFeatures, search: SearchProfile) -> bool:
    """Whether a known posting location intersects the accepted geography."""

    if job.remote_status in {RemoteStatus.REMOTE, RemoteStatus.HYBRID}:
        if job.remote_status == RemoteStatus.REMOTE and search.locations.allow_remote:
            return True
        if job.remote_status == RemoteStatus.HYBRID and search.locations.allow_hybrid:
            return True
    # Normalized on both sides: a posting that says "Texas" must match a profile
    # that says "TX", and one that says "Dallas, Texas" must match "Dallas".
    normalized_job = normalize_location(job.location)
    tokens = set(normalized_job.split())
    if not tokens:
        return False
    if intent.city_tokens & tokens or intent.state_tokens & tokens:
        return True
    # Metro areas are multi-word, so compare as phrases.
    for metro in search.locations.metro_areas:
        metro_tokens = set(normalize_location(metro).split())
        if metro_tokens and metro_tokens <= tokens:
            return True
    return False


def _missing_mandatory_qualifications(
    job: JobFeatures, search: SearchProfile, candidate: CandidateFeatures
) -> list[str]:
    """Mandatory qualifications the posting demands and the candidate cannot show.

    Only evaluated when the candidate profile carries evidence at all: an empty
    profile proves nothing, and inferring a failure from silence would reject
    every posting for a candidate who has not filled anything in yet.
    """

    if not candidate.populated:
        return []
    missing: list[str] = []
    requirement_text = " ".join(job.requirement_items).lower()
    if not requirement_text:
        return []
    for qualification in search.minimum_qualifications:
        phrase = normalize_text(qualification).lower()
        if not phrase or not _phrase_in(requirement_text, phrase):
            continue
        qualification_tokens = token_set(phrase) - {
            "required",
            "must",
            "have",
            "with",
            "and",
            "the",
            "a",
            "an",
            "of",
        }
        if not qualification_tokens:
            continue
        # Coverage, not exact superset. A job that asks for a "valid driver's
        # license" is satisfied by a candidate whose skill says "Driver's license";
        # requiring every token to appear made "valid" an unmet requirement and
        # rejected qualified candidates on wording alone.
        coverage = len(qualification_tokens & candidate.evidence_tokens) / len(qualification_tokens)
        if coverage < 0.6:
            missing.append(qualification)
    return missing
