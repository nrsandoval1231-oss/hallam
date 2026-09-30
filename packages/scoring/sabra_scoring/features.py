"""Feature extraction (requirements 13 and 25).

The ranking heuristic is allowed to be simple, but the *features* behind it must
be preserved from day one so that outcome learning can later replace or augment
the heuristic without re-deriving history. Everything computed here is stored on
the fit assessment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sabra_domain.identity import normalize_location, normalize_text, normalize_title
from sabra_domain.schemas import CandidateProfile, SearchProfile

# Unicode-aware so evidence matching works for postings in any script.
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")

# Seniority ranks used for trajectory comparison. Higher means further up.
SENIORITY_RANK = {
    "INTERN": 0,
    "ENTRY": 1,
    "UNKNOWN": 2,
    "SENIOR": 3,
    "MANAGER": 4,
    "DIRECTOR": 5,
}


def tokens(text: str | None) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def token_set(text: str | None) -> set[str]:
    """Comparable tokens. Single characters are dropped: an apostrophe split turns
    "Driver's license" into a stray "s" that then counts as evidence."""

    return {token for token in tokens(text) if len(token) > 1}


@dataclass(slots=True)
class JobFeatures:
    """Everything the filter, scorer and future learner need about a posting."""

    job_id: str
    title: str
    normalized_title: str
    company: str
    location: str | None
    location_tokens: list[str] = field(default_factory=list)
    remote_status: str = "UNKNOWN"
    employment_type: str = "UNKNOWN"
    salary_min: float | None = None
    salary_max: float | None = None
    salary_period: str | None = None
    travel_percent: int | None = None
    seniority: str | None = None
    seniority_rank: int = 2
    department: str | None = None
    description_chars: int = 0
    description_excerpt: str = ""
    requirement_items: list[str] = field(default_factory=list)
    preferred_items: list[str] = field(default_factory=list)
    responsibility_items: list[str] = field(default_factory=list)
    description_tokens: set[str] = field(default_factory=set)
    days_since_posted: int | None = None
    title_tokens: list[str] = field(default_factory=list)
    title_ngrams: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "remote_status": self.remote_status,
            "employment_type": self.employment_type,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "salary_period": self.salary_period,
            "travel_percent": self.travel_percent,
            "seniority": self.seniority,
            "seniority_rank": self.seniority_rank,
            "department": self.department,
            "description_chars": self.description_chars,
            "requirement_count": len(self.requirement_items),
            "preferred_count": len(self.preferred_items),
            "responsibility_count": len(self.responsibility_items),
            "days_since_posted": self.days_since_posted,
            "remote_friendly": self.remote_status in {"REMOTE", "HYBRID"},
        }


def ngrams(words: list[str], min_size: int = 2, max_size: int = 3) -> set[str]:
    """Contiguous n-grams, used to derive title relevance from the profile itself
    rather than from a hand-written vocabulary of job families."""

    out: set[str] = set()
    for size in range(min_size, max_size + 1):
        for start in range(len(words) - size + 1):
            out.add(" ".join(words[start : start + size]))
    return out


def extract_job_features(job: Any, *, today: date | None = None) -> JobFeatures:
    """Build features from a persisted `Job` row or a `CanonicalJob`."""

    def get(name: str, default: Any = None) -> Any:
        return getattr(job, name, default)

    title = get("title") or ""
    normalized = normalize_title(title)
    words = normalized.split()
    description = get("description") or ""

    from sabra_providers.normalize import to_bullets, travel_percent

    requirements = to_bullets(get("requirements"))
    preferred = to_bullets(get("preferred_qualifications"))
    responsibilities = to_bullets(get("responsibilities"))

    posted = get("date_posted")
    days_since: int | None = None
    if posted:
        reference = today or date.today()
        days_since = max(0, (reference - posted).days)

    seniority = get("seniority")
    from sabra_providers.normalize import parse_seniority

    if not seniority:
        seniority = parse_seniority(title)

    return JobFeatures(
        job_id=str(get("id") or ""),
        title=title,
        normalized_title=normalized,
        company=get("company") or "",
        location=get("location"),
        location_tokens=sorted(token_set(get("location"))),
        remote_status=str(get("remote_status") or "UNKNOWN"),
        employment_type=str(get("employment_type") or "UNKNOWN"),
        salary_min=get("salary_min"),
        salary_max=get("salary_max"),
        salary_period=get("salary_period"),
        travel_percent=travel_percent(get("travel")),
        seniority=seniority,
        seniority_rank=SENIORITY_RANK.get(seniority or "UNKNOWN", 2),
        department=get("department"),
        description_chars=len(description),
        description_excerpt=description[:6000],
        requirement_items=requirements,
        preferred_items=preferred,
        responsibility_items=responsibilities,
        description_tokens=token_set(description),
        days_since_posted=days_since,
        title_tokens=words,
        title_ngrams=ngrams(words),
    )


@dataclass(slots=True)
class CandidateFeatures:
    """The candidate's evidenced capabilities, as comparable tokens."""

    candidate_id: str
    populated: bool = False
    title_ngrams: set[str] = field(default_factory=set)
    skill_tokens: set[str] = field(default_factory=set)
    industry_tokens: set[str] = field(default_factory=set)
    accomplishment_tokens: set[str] = field(default_factory=set)
    certification_tokens: set[str] = field(default_factory=set)
    evidence_tokens: set[str] = field(default_factory=set)
    highest_seniority_rank: int = 2
    has_management_experience: bool = False
    years_experience: float | None = None
    stated_titles: list[str] = field(default_factory=list)
    stated_industries: list[str] = field(default_factory=list)
    stated_skills: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "populated": self.populated,
            "evidence_token_count": len(self.evidence_tokens),
            "highest_seniority_rank": self.highest_seniority_rank,
            "has_management_experience": self.has_management_experience,
            "years_experience": self.years_experience,
            "stated_titles": self.stated_titles,
            "stated_industries": self.stated_industries,
            "stated_skill_count": len(self.stated_skills),
        }


def extract_candidate_features(profile: CandidateProfile) -> CandidateFeatures:
    """Everything here derives from candidate-supplied or résumé evidence only.

    Nothing is imputed: an empty profile produces empty features, which is what
    makes a low-confidence score honest rather than a guess dressed as data.
    """

    from sabra_providers.normalize import parse_seniority

    title_ngrams: set[str] = set()
    titles: list[str] = []
    highest = 2
    evidence: set[str] = set()

    for entry in profile.employment:
        if not entry.title:
            continue
        titles.append(entry.title)
        normalized = normalize_title(entry.title)
        title_ngrams |= ngrams(normalized.split())
        rank = SENIORITY_RANK.get(parse_seniority(entry.title) or "UNKNOWN", 2)
        highest = max(highest, rank)
        if entry.employer:
            evidence |= token_set(entry.employer)
        if entry.products:
            evidence |= token_set(" ".join(entry.products))
        if entry.quota_attainment:
            evidence |= token_set(entry.quota_attainment)

    accomplishment_tokens: set[str] = set()
    for _employer, _title, item in profile.all_accomplishments():
        accomplishment_tokens |= token_set(item)

    skill_tokens = token_set(" ".join(s.name for s in profile.skills))
    category_tokens = token_set(" ".join(s.category or "" for s in profile.skills))
    skill_tokens |= category_tokens

    certification_tokens = token_set(
        " ".join(f"{c.name} {c.issuer or ''}" for c in profile.certifications)
    )
    industry_tokens = token_set(" ".join(profile.industries + profile.preferred_industries))

    # Composed from every evidenced source. Skills and certifications were
    # previously extracted but omitted from `evidence`, so a candidate whose profile
    # is largely a skills list scored zero qualification coverage against postings
    # that plainly asked for those skills.
    evidence |= token_set(profile.summary) | token_set(profile.headline)
    evidence |= token_set(" ".join(profile.technical_capabilities))
    evidence |= skill_tokens | certification_tokens | industry_tokens
    evidence |= accomplishment_tokens
    evidence |= token_set(" ".join(titles))
    for entry in profile.employment:
        evidence |= token_set(entry.summary)
    for project in profile.projects:
        evidence |= token_set(project.name) | token_set(project.description)
        evidence |= token_set(" ".join(project.outcomes))

    total_years: float | None = None
    starts = [e.start_date for e in profile.employment if e.start_date]
    if starts:
        years = []
        for entry in profile.employment:
            try:
                start_year = int(str(entry.start_date)[:4])
            except (TypeError, ValueError):
                continue
            end_year = None
            if entry.end_date:
                try:
                    end_year = int(str(entry.end_date)[:4])
                except (TypeError, ValueError):
                    end_year = None
            years.append(max(0, (end_year or date.today().year) - start_year))
        if years:
            total_years = float(sum(years))

    management = profile.management_experience
    has_management = bool(
        management.has_management_experience
        or (management.people_managed_max or 0) > 0
        or (management.years_managing or 0) > 0
    )

    populated = bool(
        profile.employment
        or profile.skills
        or profile.summary
        or profile.headline
        or profile.industries
    )

    return CandidateFeatures(
        candidate_id=profile.candidate_id,
        populated=populated,
        title_ngrams=title_ngrams,
        skill_tokens=skill_tokens,
        industry_tokens=industry_tokens,
        accomplishment_tokens=accomplishment_tokens,
        certification_tokens=certification_tokens,
        evidence_tokens=evidence,
        highest_seniority_rank=highest,
        has_management_experience=has_management,
        years_experience=total_years,
        stated_titles=titles,
        stated_industries=profile.industries,
        stated_skills=[s.name for s in profile.skills],
    )


@dataclass(slots=True)
class IntentFeatures:
    """The search profile reduced to comparable tokens."""

    title_ngrams: set[str] = field(default_factory=set)
    target_titles: list[str] = field(default_factory=list)
    acceptable_titles: list[str] = field(default_factory=list)
    target_ngrams: set[str] = field(default_factory=set)
    industry_tokens: set[str] = field(default_factory=set)
    excluded_title_patterns: list[str] = field(default_factory=list)
    excluded_company_tokens: set[str] = field(default_factory=set)
    excluded_keywords: list[str] = field(default_factory=list)
    role_family_tokens: set[str] = field(default_factory=set)
    role_family_phrases: set[str] = field(default_factory=set)
    role_level_tokens: set[str] = field(default_factory=set)
    location_tokens: set[str] = field(default_factory=set)
    city_tokens: set[str] = field(default_factory=set)
    state_tokens: set[str] = field(default_factory=set)


# Function words that must not appear inside a title n-gram. Without this,
# "Director of Sales" contributes the two-word gram "director of", which then
# matches "Associate Director of Bioinformatics" — measured on live data, an
# unrelated role reached the shortlist by that route.
ROLE_STOPWORDS = frozenset(
    {
        "of",
        "and",
        "the",
        "for",
        "a",
        "an",
        "in",
        "to",
        "with",
        "or",
        "on",
        "at",
        "our",
        "your",
        "i",
        "ii",
        "iii",
    }
)


def role_ngrams(words: list[str], min_size: int = 2, max_size: int = 3) -> set[str]:
    """Title n-grams with function words removed from consideration."""

    return {
        gram
        for gram in ngrams(words, min_size, max_size)
        if not (set(gram.split()) & ROLE_STOPWORDS)
    }


def extract_intent_features(profile: SearchProfile) -> IntentFeatures:
    targets = list(profile.target_titles)
    accepted = list(profile.acceptable_titles)
    all_titles = targets + accepted
    target_ngrams: set[str] = set()
    for title in targets:
        target_ngrams |= role_ngrams(normalize_title(title).split())
    combined_ngrams: set[str] = set()
    for title in all_titles:
        combined_ngrams |= role_ngrams(normalize_title(title).split())

    return IntentFeatures(
        title_ngrams=combined_ngrams,
        target_titles=targets,
        acceptable_titles=accepted,
        target_ngrams=target_ngrams,
        industry_tokens=token_set(" ".join(profile.industries)),
        excluded_title_patterns=[normalize_text(t).lower() for t in profile.excluded_titles],
        excluded_company_tokens=token_set(" ".join(profile.excluded_companies)),
        excluded_keywords=[normalize_text(k).lower() for k in profile.excluded_keywords],
        role_family_tokens=token_set(
            " ".join(t for t in profile.role_family_terms if " " not in t.strip())
        ),
        # Multi-word families must match as contiguous phrases. Splitting them into
        # tokens made "business development" contribute the bare token "business",
        # which then admitted "Director, People Business Partner" — an HR role — to
        # the shortlist.
        role_family_phrases={
            normalize_text(t).lower() for t in profile.role_family_terms if " " in t.strip()
        },
        role_level_tokens=token_set(" ".join(profile.role_level_terms)),
        location_tokens=token_set(
            " ".join(
                profile.locations.cities + profile.locations.states + profile.locations.metro_areas
            )
        ),
        # Normalized so a posting that says "Texas" matches a profile that says
        # "TX"; comparing raw tokens silently failed that match.
        city_tokens=token_set(" ".join(normalize_location(c) for c in profile.locations.cities)),
        state_tokens=token_set(" ".join(normalize_location(s) for s in profile.locations.states)),
    )


def annualized_salary(features: JobFeatures) -> tuple[float | None, float | None]:
    """Return (min, max) as annual figures, or (None, None) when not comparable.

    An hourly rate is NOT scaled up: a made-up annual equivalent would be presented
    as a fact about the posting. Not knowing is the honest answer, and the filter
    treats it as unknown rather than as a failure.
    """

    if features.salary_period == "HOUR":
        return None, None
    if features.salary_period == "MONTH":
        low = features.salary_min * 12 if features.salary_min else None
        high = features.salary_max * 12 if features.salary_max else None
        return low, high
    if features.salary_period in (None, "YEAR"):
        return features.salary_min, features.salary_max
    return None, None
