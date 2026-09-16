"""Direct company career pages (Tier 1, requirement 7).

Reads a configured careers URL and extracts `schema.org/JobPosting` nodes from
JSON-LD. JSON-LD is the deterministic path: it is documented, structured, and
stable across page redesigns, unlike scraping rendered markup.

Pages that embed no JSON-LD are reported as a source limitation rather than
guessed at — a provider that invents fields from prose would poison the canonical
job record.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sabra_domain.enums import EmploymentType, JobStatus, RemoteStatus, SourceKind
from sabra_domain.errors import ProviderError
from sabra_domain.schemas import CanonicalJob

from .base import DiscoveryBatch, Provider, ProviderRef, RawJob, VerifyResult
from .normalize import (
    extract_salary,
    extract_travel,
    html_to_text,
    parse_date,
    parse_employment_type,
    parse_location,
    parse_seniority,
    split_sections,
)

_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

_EMPLOYMENT_MAP = {
    "full_time": EmploymentType.FULL_TIME,
    "full-time": EmploymentType.FULL_TIME,
    "part_time": EmploymentType.PART_TIME,
    "part-time": EmploymentType.PART_TIME,
    "contractor": EmploymentType.CONTRACT,
    "contract": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "intern": EmploymentType.INTERNSHIP,
    "internship": EmploymentType.INTERNSHIP,
}


def extract_json_ld_blocks(page_html: str) -> list[Any]:
    """Return every JSON-LD document on the page.

    A malformed block is skipped rather than failing the page: one bad script tag
    is common and must not hide the valid postings beside it.
    """

    blocks: list[Any] = []
    for match in _LD_JSON.finditer(page_html or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            # Some sites emit HTML comments or trailing commas inside the block.
            cleaned = re.sub(r"^\s*<!--|-->\s*$", "", raw).strip()
            try:
                blocks.append(json.loads(cleaned))
            except json.JSONDecodeError:
                continue
    return blocks


def _walk_for_job_postings(node: Any) -> list[dict[str, Any]]:
    """Find `JobPosting` nodes anywhere in the JSON-LD graph."""

    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(str(t).lower() == "jobposting" for t in types if t):
            found.append(node)
        for value in node.values():
            found.extend(_walk_for_job_postings(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_for_job_postings(item))
    return found


class DirectCareersProvider(Provider):
    """A single company careers page as a discovery source."""

    kind = SourceKind.DIRECT_CAREERS

    def __init__(self, token: str, *, careers_url: str, **kwargs: Any) -> None:
        super().__init__(token, **kwargs)
        self.careers_url = careers_url

    def board_url(self) -> str:
        return self.careers_url

    def discover(self, search_profile=None) -> DiscoveryBatch:
        result = self.http.get(self.careers_url)
        page = result.text()
        blocks = extract_json_ld_blocks(page)
        postings: list[dict[str, Any]] = []
        for block in blocks:
            postings.extend(_walk_for_job_postings(block))

        ref = ProviderRef(
            kind=self.kind, token=self.token, source_job_id=self.token, url=self.careers_url
        )
        batch = DiscoveryBatch(ref=ref, fetches=[result.record])
        batch.total_reported = len(postings)
        if not postings:
            batch.warnings.append(
                f"{self.careers_url} embeds no schema.org JobPosting JSON-LD; "
                "source yields nothing capturable"
            )
            return batch

        for posting in postings:
            job_id = _posting_id(posting)
            if not job_id:
                batch.warnings.append("JobPosting without an identifier skipped")
                continue
            batch.raw_jobs.append(
                RawJob(
                    ref=ProviderRef(
                        kind=self.kind,
                        token=self.token,
                        source_job_id=job_id,
                        url=_posting_url(posting) or self.careers_url,
                    ),
                    payload=posting,
                    fetched_at=datetime.now(UTC),
                    fetch=result.record,
                    company_hint=self.display_name,
                    company_domain=self.company_domain,
                )
            )
            if self.max_items and len(batch.raw_jobs) >= self.max_items:
                batch.truncated = True
                break
        return batch

    def fetch(self, job_reference: ProviderRef) -> RawJob:
        """Re-read the page and locate the posting by identifier."""

        result = self.http.get(self.careers_url)
        page = result.text()
        postings: list[dict[str, Any]] = []
        for block in extract_json_ld_blocks(page):
            postings.extend(_walk_for_job_postings(block))
        for posting in postings:
            if _posting_id(posting) == job_reference.source_job_id:
                return RawJob(
                    ref=job_reference,
                    payload=posting,
                    fetched_at=datetime.now(UTC),
                    fetch=result.record,
                    company_hint=self.display_name,
                    company_domain=self.company_domain,
                )
        raise ProviderError(
            self.token, f"posting {job_reference.source_job_id} no longer on {self.careers_url}"
        )

    def verify(self, ref: ProviderRef, known_hash: str | None = None) -> VerifyResult:
        try:
            raw = self.fetch(ref)
        except ProviderError as exc:
            return VerifyResult(
                available=False,
                changed=False,
                detail=exc.message,
                checked_at=datetime.now(UTC),
            )
        current = raw.source_hash()
        return VerifyResult(
            available=True,
            changed=known_hash is not None and current != known_hash,
            source_hash=current,
            detail="live",
            checked_at=datetime.now(UTC),
            raw=raw,
        )

    def normalize(self, raw_job: RawJob) -> Any:
        posting = raw_job.payload
        warnings: list[str] = []

        title = str(posting.get("title") or "").strip()
        if not title:
            warnings.append("JobPosting has no title")

        org = posting.get("hiringOrganization") or {}
        company = self.display_name
        company_domain = self.company_domain
        if isinstance(org, dict):
            company = str(org.get("name") or company)
            if not company_domain and org.get("sameAs"):
                company_domain = str(org.get("sameAs"))

        location_raw, remote_flag = _location_fields(posting)
        display_location, city, region, _ = parse_location(location_raw)

        description_html = str(posting.get("description") or "")
        description = html_to_text(description_html)
        sections = split_sections(description)

        employment_type, et_warnings = parse_employment_type(
            _first(posting.get("employmentType")), description
        )
        warnings.extend(et_warnings)

        salary = _salary_from_base_salary(posting.get("baseSalary")) or extract_salary(description)

        remote_status = remote_flag
        if remote_status is RemoteStatus.UNKNOWN:
            from .normalize import parse_remote_status

            remote_status = parse_remote_status(
                structured=str(posting.get("jobLocationType") or ""),
                location=display_location,
                text=f"{title}\n{description[:2000]}",
            )

        travel = extract_travel(description)
        if travel is None:
            travel_requirements = posting.get("jobBenefits")
            travel = extract_travel(
                travel_requirements if isinstance(travel_requirements, str) else None
            )

        job = CanonicalJob(
            source=self.kind,
            source_job_id=_posting_id(posting) or self.token,
            source_url=_posting_url(posting) or self.careers_url,
            company=company,
            company_domain=company_domain,
            title=title,
            location=display_location,
            city=city,
            region=region,
            remote_status=remote_status,
            employment_type=employment_type,
            salary_min=salary[0] if salary else None,
            salary_max=salary[1] if salary else None,
            salary_currency=salary[2] if salary else None,
            salary_period=salary[3] if salary else None,
            description=description,
            requirements=sections.get("requirements"),
            preferred_qualifications=sections.get("preferred_qualifications"),
            responsibilities=sections.get("responsibilities"),
            travel=travel,
            department=_first(posting.get("occupationalCategory"))
            if not isinstance(posting.get("occupationalCategory"), dict)
            else None,
            seniority=parse_seniority(title),
            date_posted=parse_date(posting.get("datePosted")),
            date_discovered=raw_job.fetched_at,
            status=str(JobStatus.DISCOVERED),
            raw_source_hash=raw_job.source_hash(),
            board_token=self.token,
            discovered_via=f"direct:{self.token}",
            raw_payload={
                "identifier": posting.get("identifier"),
                "datePosted": posting.get("datePosted"),
                "validThrough": posting.get("validThrough"),
                "industry": posting.get("industry"),
                "jobLocationType": posting.get("jobLocationType"),
            },
        )
        if not description:
            warnings.append("JobPosting description was empty")
        return self._finalize(job, warnings)


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        return str(value[0]) if value else None
    if value is None:
        return None
    return str(value)


def _posting_id(posting: dict[str, Any]) -> str | None:
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        value = identifier.get("value") or identifier.get("@id")
        if value:
            return str(value)
    elif isinstance(identifier, str) and identifier:
        return identifier
    if posting.get("url"):
        return str(posting.get("url"))
    return None


def _posting_url(posting: dict[str, Any]) -> str | None:
    url = posting.get("url")
    if isinstance(url, str) and url:
        return url
    return None


def _location_fields(posting: dict[str, Any]) -> tuple[str | None, RemoteStatus]:
    """Read location from `jobLocation` (single or list) and `jobLocationType`."""

    from .normalize import parse_remote_status

    locations = posting.get("jobLocation")
    entries = locations if isinstance(locations, list) else [locations] if locations else []
    parts: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address") or {}
        if isinstance(address, dict):
            locality = address.get("addressLocality")
            region = address.get("addressRegion")
            country = address.get("addressCountry")
            country_name = country.get("name") if isinstance(country, dict) else country
            if locality or region:
                parts.append(", ".join(str(p) for p in (locality, region) if p))
            elif country_name:
                parts.append(str(country_name))
        elif entry.get("name"):
            parts.append(str(entry.get("name")))

    location_type = str(posting.get("jobLocationType") or "")
    remote = RemoteStatus.UNKNOWN
    if "telecommute" in location_type.lower() or "remote" in location_type.lower():
        remote = RemoteStatus.REMOTE
    if not parts and remote is RemoteStatus.UNKNOWN:
        return None, RemoteStatus.UNKNOWN
    joined = " or ".join(parts) if parts else None
    if remote is RemoteStatus.UNKNOWN:
        remote = parse_remote_status(location=joined)
    return joined, remote


def _salary_from_base_salary(base_salary: Any) -> tuple[float, float, str, str] | None:
    """Read `baseSalary` per the schema.org shape, including QuantitativeValue."""

    if not isinstance(base_salary, dict):
        return None
    currency = str(base_salary.get("currency") or "USD")
    value = base_salary.get("value")
    unit = None
    if isinstance(value, dict):
        unit = value.get("unitText")
        low = value.get("minValue", value.get("value"))
        high = value.get("maxValue", value.get("value"))
    else:
        low = high = value
    if low is None and high is None:
        return None
    try:
        low = float(low) if low is not None else None
        high = float(high) if high is not None else None
    except (TypeError, ValueError):
        return None
    if low is None:
        low = high
    if high is None:
        high = low
    if low is None or high is None:
        return None
    period = "UNKNOWN"
    unit_text = str(unit or "").upper()
    if "HOUR" in unit_text:
        period = "HOUR"
    elif "MONTH" in unit_text:
        period = "MONTH"
    elif "YEAR" in unit_text or "ANNUAL" in unit_text or low >= 1000:
        period = "YEAR"
    if high < low:
        low, high = high, low
    return float(low), float(high), currency, period
