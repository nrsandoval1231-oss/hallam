"""Opportunity research (requirement 14).

Two rules shape this module.

**Verified facts and inferences are stored separately**, because requirement 14
demands it and because an inference that travels unlabelled becomes a fact the
moment someone quotes it.

**Nothing is invented to fill a field.** There is no "recent company developments"
value unless a source actually said something. An empty field plus a stated reason
is more useful than a plausible sentence, and this is precisely the kind of content
a model would happily make up if asked without evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sabra_domain.enums import EntityType, EventType, JobStatus
from sabra_domain.errors import ProviderError
from sabra_domain.schemas import ResearchPacketSchema
from sabra_providers.http import HttpClient

from .context import EngineContext, Repos

_META_DESCRIPTION = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_REPORTS_TO = re.compile(
    r"report(?:s|ing)?\s+(?:in)?to\s+(?:the\s+)?([A-Za-z][A-Za-z ,/&-]{2,60})", re.IGNORECASE
)


@dataclass(slots=True)
class ResearchOutcome:
    opportunity_id: str
    company_website: str | None = None
    fetched_ok: bool = False
    verified_fact_count: int = 0
    inference_count: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "company_website": self.company_website,
            "fetched_ok": self.fetched_ok,
            "verified_fact_count": self.verified_fact_count,
            "inference_count": self.inference_count,
            "notes": self.notes,
        }


class ResearchService:
    """Builds a research packet for one opportunity, deterministically.

    The company website is fetched when a domain is known, and what came back is
    recorded as a verified fact with its URL as evidence. Everything derived rather
    than read goes in `inferences`.
    """

    def __init__(self, ctx: EngineContext, *, http: HttpClient | None = None) -> None:
        self.ctx = ctx
        self._http = http
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http and self._http is not None:
            self._http.close()

    def research(self, opportunity_id: str, *, fetch_homepage: bool = True) -> ResearchOutcome:
        outcome = ResearchOutcome(opportunity_id=opportunity_id)
        with self.ctx.repos() as repos:
            opportunity = repos.opportunities.get(opportunity_id)
            jobs = repos.opportunities.list_jobs(opportunity_id)
            if not jobs:
                raise ProviderError("research", f"opportunity {opportunity_id} has no jobs")
            primary = _primary(jobs)
            company_row = repos.companies.upsert(primary.company, domain=primary.company_domain)
            registry_entry = _registry_entry(self.ctx, primary.board_token)

            verified: list[str] = []
            inferences: list[str] = []
            sources: list[str] = []

            packet = ResearchPacketSchema(
                company_description=company_row.description,
                company_website=company_row.website
                or (f"https://{primary.company_domain}" if primary.company_domain else None),
                industry=primary.department
                or (registry_entry.industry if registry_entry else None),
                role_context=_role_context(primary),
                sources=sources,
            )

            verified.append(f"posting is live on {primary.source}: {primary.source_url}")
            if primary.date_posted:
                verified.append(f"posted {primary.date_posted.isoformat()}")
            if primary.salary_min and primary.salary_max:
                verified.append(
                    f"posted compensation {primary.salary_currency or 'USD'} "
                    f"{primary.salary_min:,.0f}–{primary.salary_max:,.0f} "
                    f"({primary.salary_period or 'unknown period'})"
                )
            if primary.travel:
                verified.append(f"travel expectation: {primary.travel}")
            if primary.location:
                verified.append(f"posted location: {primary.location}")
            if len(jobs) > 1:
                verified.append(
                    f"the same opening is described by {len(jobs)} postings across "
                    f"{len({j.source for j in jobs})} source(s)"
                )

            if packet.company_website and fetch_homepage:
                description, fetched_sources, note = self._fetch_site(packet.company_website)
                if description:
                    packet.company_description = description
                    company_row.description = description
                    company_row.website = packet.company_website
                    verified.append(f"company site describes itself as: {description[:400]}")
                    sources.extend(fetched_sources)
                    outcome.fetched_ok = True
                else:
                    outcome.notes.append(note or "company site could not be read")
                    # The URL is a fact even when the page is not; the absence of a
                    # description is stated rather than filled.
                    inferences.append(
                        "no company description could be captured from the public site"
                    )

            reporting = _reporting_org(primary.description or "")
            if reporting:
                packet.likely_reporting_organization = reporting
                inferences.append(
                    f"the posting mentions reporting to {reporting!r}, which suggests the org placement"
                )

            products = _products(primary)
            if products:
                packet.products_services = products
                inferences.append(
                    "products/services inferred from terms appearing in the posting text"
                )

            intersections = _intersections(repos, opportunity.candidate_id, primary)
            if intersections:
                packet.candidate_company_intersections = intersections
                inferences.append("candidate/company intersections derived from candidate evidence")

            packet.hiring_context = _hiring_context(jobs, primary)
            if packet.hiring_context:
                inferences.append("hiring context derived from posting metadata")

            packet.verified_facts = verified
            packet.inferences = inferences
            packet.sources = sorted(set(sources))
            packet.engine = "deterministic"

            repos.research.upsert(opportunity_id, packet.model_dump(mode="json"), "deterministic")
            repos.events.append(
                EntityType.OPPORTUNITY,
                opportunity_id,
                EventType.JOB_RESEARCHED,
                {
                    "verified_facts": len(verified),
                    "inferences": len(inferences),
                    "sources": packet.sources,
                    "homepage_fetched": outcome.fetched_ok,
                },
            )
            status = JobStatus(opportunity.status)
            if status is JobStatus.SHORTLISTED:
                repos.opportunities.set_status(opportunity_id, JobStatus.RESEARCHED)

            outcome.verified_fact_count = len(verified)
            outcome.inference_count = len(inferences)
        return outcome

    def research_shortlist(self, candidate_id: str, *, limit: int = 10) -> list[ResearchOutcome]:
        with self.ctx.read_repos() as repos:
            targets = [o.id for o in repos.opportunities.shortlist(candidate_id)][:limit]
        outcomes: list[ResearchOutcome] = []
        try:
            for opportunity_id in targets:
                outcomes.append(self.research(opportunity_id))
        finally:
            self.close()
        return outcomes

    def _fetch_site(self, url: str) -> tuple[str | None, list[str], str | None]:
        client = self._http
        owns = False
        if client is None:
            client = HttpClient(
                timeout=self.ctx.settings.discovery_timeout_seconds,
                max_attempts=2,
                user_agent=self.ctx.settings.discovery_user_agent,
            )
            owns = True
        try:
            result = client.get(url)
        except ProviderError as exc:
            return None, [], f"company site fetch failed: {exc.message}"
        finally:
            if owns:
                client.close()

        html = result.text()
        description = None
        match = _META_DESCRIPTION.search(html)
        if match:
            description = re.sub(r"\s+", " ", match.group(1)).strip()
        else:
            title = _TITLE.search(html)
            if title:
                description = re.sub(r"\s+", " ", title.group(1)).strip()
        if description and len(description) < 20:
            description = None
        return description, [url], None


def _primary(jobs: list[Any]) -> Any:
    return sorted(jobs, key=lambda j: (-len(j.description or ""), j.id))[0]


def _registry_entry(ctx: EngineContext, token: str | None) -> Any:
    if not token:
        return None
    for source in ctx.registry:
        if source.entry.token == token:
            return source.entry
    return None


def _role_context(job: Any) -> str | None:
    parts: list[str] = []
    if job.department:
        parts.append(f"Department: {job.department}")
    if job.seniority:
        parts.append(f"Seniority: {job.seniority}")
    if job.employment_type:
        parts.append(f"Employment type: {job.employment_type}")
    if job.remote_status:
        parts.append(f"Arrangement: {job.remote_status}")
    return " | ".join(parts) if parts else None


def _reporting_org(description: str) -> str | None:
    match = _REPORTS_TO.search(description)
    if not match:
        return None
    candidate = match.group(1).strip(" .,;:")
    candidate = re.split(r"\b(?:and|or|who|which|that)\b", candidate, maxsplit=1)[0].strip()
    # A truncated capture is worse than none: it would read as a named role.
    if not candidate or len(candidate.split()) > 6:
        return None
    return candidate


def _products(job: Any) -> list[str]:
    """Product terms the posting itself names, in the posting's own words."""

    text = " ".join(filter(None, [job.requirements, job.preferred_qualifications, job.description]))
    text = text[:4000]
    words = re.findall(r"\b[A-Z][A-Za-z]{3,}(?:\s+[A-Z][A-Za-z]{3,})?\b", text)
    stop = {"The", "This", "You", "Our", "We", "Your", "Ability", "Experience", "Must", "Equal"}
    seen: list[str] = []
    for word in words:
        head = word.split()[0]
        if head in stop or word in seen:
            continue
        seen.append(word)
    return seen[:8]


def _intersections(repos: Repos, candidate_id: str, job: Any) -> list[str]:
    from sabra_domain.errors import NotFound

    try:
        profile = repos.candidates.active_profile(candidate_id)
    except NotFound:
        return []
    text = " ".join(
        filter(None, [job.title, job.description, job.requirements, job.company])
    ).lower()
    hits: list[str] = []
    for term in [s.name for s in profile.skills] + list(profile.industries):
        if term and len(term) > 3 and term.lower() in text and term not in hits:
            hits.append(term)
    return hits[:10]


def _hiring_context(jobs: list[Any], primary: Any) -> str | None:
    parts: list[str] = []
    if primary.date_posted:
        age = (datetime.now(UTC).date() - primary.date_posted).days
        parts.append(f"posted {age} day(s) ago")
    if len(jobs) > 1:
        parts.append(f"{len(jobs)} duplicate postings for this opening")
    return "; ".join(parts) if parts else None
