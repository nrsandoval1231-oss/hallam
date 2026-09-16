"""Lever discovery and normalization.

Endpoint: `GET /v0/postings/{token}?mode=json`. Lever is generous: it supplies a
structured `salaryRange`, a `workplaceType` enum, `categories.commitment`, a
millisecond `createdAt` and `lists[]` holding the responsibilities/requirements
sections as separate headings. Everything it provides structurally is used
directly in preference to prose extraction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sabra_domain.enums import JobStatus, RemoteStatus, SourceKind
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
    parse_remote_status,
    parse_seniority,
    split_sections,
)

BOARD_URL = "https://api.lever.co/v0/postings/{token}"
JOB_URL = "https://api.lever.co/v0/postings/{token}/{job_id}"
HUMAN_URL = "https://jobs.lever.co/{token}"

_INTERVAL_TO_PERIOD = {
    "per-year-salary": "YEAR",
    "per-month-salary": "MONTH",
    "per-hour-wage": "HOUR",
    "per-year": "YEAR",
    "per-hour": "HOUR",
}

_REQUIREMENTS_HEADINGS = (
    "requirements",
    "qualifications",
    "what you'll need",
    "what you will need",
    "who you are",
    "skills",
    "experience",
    "minimum qualifications",
)
_RESPONSIBILITY_HEADINGS = (
    "responsibilities",
    "what you'll do",
    "what you will do",
    "the role",
    "duties",
)


def _is_heading(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = (text or "").strip().lower().rstrip(":").strip()
    return any(lowered == k or lowered.startswith(k) for k in keywords)


class LeverProvider(Provider):
    kind = SourceKind.LEVER

    def board_url(self) -> str:
        return HUMAN_URL.format(token=self.token)

    def discover(self, search_profile=None) -> DiscoveryBatch:
        url = BOARD_URL.format(token=self.token)
        result = self.http.get(url, params={"mode": "json"})
        payload = result.json()
        if not isinstance(payload, list):
            raise ProviderError(self.token, f"unexpected board payload from {url}")

        ref = ProviderRef(kind=self.kind, token=self.token, source_job_id=self.token, url=url)
        batch = DiscoveryBatch(ref=ref, fetches=[result.record])
        batch.total_reported = len(payload)
        for item in payload:
            if not isinstance(item, dict):
                batch.warnings.append("non-object posting skipped")
                continue
            job_id = str(item.get("id") or "").strip()
            if not job_id:
                batch.warnings.append("posting without an id skipped")
                continue
            batch.raw_jobs.append(
                RawJob(
                    ref=ProviderRef(
                        kind=self.kind,
                        token=self.token,
                        source_job_id=job_id,
                        url=item.get("hostedUrl") or self.board_url(),
                    ),
                    payload=item,
                    fetched_at=datetime.now(UTC),
                    fetch=result.record,
                    company_hint=self.display_name,
                    company_domain=self.company_domain,
                    board_display_name=self.display_name,
                )
            )
            if self.max_items and len(batch.raw_jobs) >= self.max_items:
                batch.truncated = True
                break
        return batch

    def fetch(self, job_reference: ProviderRef) -> RawJob:
        url = JOB_URL.format(token=self.token, job_id=job_reference.source_job_id)
        result = self.http.get(url, params={"mode": "json"})
        payload = result.json()
        if not isinstance(payload, dict):
            raise ProviderError(self.token, f"unexpected posting payload from {url}")
        return RawJob(
            ref=job_reference,
            payload=payload,
            fetched_at=datetime.now(UTC),
            fetch=result.record,
            company_hint=self.display_name,
            company_domain=self.company_domain,
        )

    def verify(self, ref: ProviderRef, known_hash: str | None = None) -> VerifyResult:
        url = JOB_URL.format(token=self.token, job_id=ref.source_job_id)
        try:
            result = self.http.get(url, params={"mode": "json"})
        except ProviderError as exc:
            gone = "HTTP 404" in exc.message or "HTTP 410" in exc.message
            return VerifyResult(
                available=False,
                changed=False,
                http_status=404 if gone else None,
                detail="posting closed" if gone else exc.message,
                checked_at=datetime.now(UTC),
            )
        payload = result.json()
        raw = RawJob(
            ref=ref,
            payload=payload if isinstance(payload, dict) else {},
            fetched_at=datetime.now(UTC),
            fetch=result.record,
            company_hint=self.display_name,
        )
        current = raw.source_hash()
        return VerifyResult(
            available=True,
            changed=known_hash is not None and current != known_hash,
            source_hash=current,
            http_status=result.status,
            detail="live",
            checked_at=datetime.now(UTC),
            raw=raw,
        )

    def normalize(self, raw_job: RawJob) -> Any:
        payload = raw_job.payload
        warnings: list[str] = []

        title = (payload.get("text") or "").strip()
        if not title:
            warnings.append("posting has no title")

        categories = payload.get("categories") or {}
        location_raw = categories.get("location")
        all_locations = categories.get("allLocations") or []
        if len(all_locations) > 1:
            warnings.append(f"multi-location posting: {len(all_locations)} locations")
        display_location, city, region, _ = parse_location(location_raw)

        plain = payload.get("descriptionPlain") or ""
        additional = payload.get("additionalPlain") or ""
        lists = payload.get("lists") or []

        requirements_parts: list[str] = []
        responsibility_parts: list[str] = []
        rendered_lists: list[str] = []
        for entry in lists:
            if not isinstance(entry, dict):
                continue
            heading = str(entry.get("text") or "").strip()
            body = html_to_text(entry.get("content") or "")
            if not heading and not body:
                continue
            rendered_lists.append(f"{heading}\n{body}".strip())
            if _is_heading(heading, _REQUIREMENTS_HEADINGS):
                requirements_parts.append(body)
            elif _is_heading(heading, _RESPONSIBILITY_HEADINGS):
                responsibility_parts.append(body)

        description = "\n\n".join(part for part in [plain, *rendered_lists, additional] if part)

        # Lever's own description sometimes carries explicit sections too.
        sections = split_sections(plain)
        requirements = "\n\n".join(
            requirements_parts or [sections.get("requirements") or ""]
        ).strip()
        responsibilities = "\n\n".join(
            responsibility_parts or [sections.get("responsibilities") or ""]
        ).strip()

        salary = _salary_from_range(payload.get("salaryRange"))
        if salary is None:
            salary = extract_salary(description)

        employment_type, et_warnings = parse_employment_type(
            categories.get("commitment"), description
        )
        warnings.extend(et_warnings)

        travel = extract_travel(description)

        job = CanonicalJob(
            source=self.kind,
            source_job_id=str(payload.get("id")),
            source_url=payload.get("hostedUrl") or self.board_url(),
            company=self.display_name,
            company_domain=self.company_domain,
            title=title,
            location=display_location,
            city=city,
            region=region,
            remote_status=parse_remote_status(
                structured=payload.get("workplaceType"),
                location=display_location,
                text=f"{title}\n{description[:3000]}",
            ),
            employment_type=employment_type,
            salary_min=salary[0] if salary else None,
            salary_max=salary[1] if salary else None,
            salary_currency=salary[2] if salary else None,
            salary_period=salary[3] if salary else None,
            description=description,
            requirements=requirements or None,
            preferred_qualifications=sections.get("preferred_qualifications"),
            responsibilities=responsibilities or None,
            travel=travel,
            department=categories.get("department") or categories.get("team"),
            seniority=parse_seniority(title),
            date_posted=parse_date(payload.get("createdAt")),
            date_discovered=raw_job.fetched_at,
            status=str(JobStatus.DISCOVERED),
            raw_source_hash=raw_job.source_hash(),
            board_token=self.token,
            discovered_via=f"lever:{self.token}",
            raw_payload={
                "id": payload.get("id"),
                "categories": categories,
                "country": payload.get("country"),
                "workplaceType": payload.get("workplaceType"),
                "createdAt": payload.get("createdAt"),
                "applyUrl": payload.get("applyUrl"),
                "location_count": len(all_locations),
            },
        )
        if payload.get("workplaceType") is None:
            warnings.append("no workplaceType field; arrangement inferred")
        return self._finalize(job, warnings)


def _salary_from_range(salary_range: Any) -> tuple[float, float, str, str] | None:
    """Use Lever's structured range when present and sane."""

    if not isinstance(salary_range, dict):
        return None
    low = salary_range.get("min")
    high = salary_range.get("max")
    currency = salary_range.get("currency") or "USD"
    period = _INTERVAL_TO_PERIOD.get(str(salary_range.get("interval") or "").lower())
    if low is None and high is None:
        return None
    low = float(low) if low is not None else None
    high = float(high) if high is not None else None
    if low is None:
        low = high
    if high is None:
        high = low
    if not period:
        period = "YEAR" if low >= 1000 else "UNKNOWN"
    if period == "HOUR" and not (5 <= low <= 500):
        return None
    if period == "YEAR" and low < 1000:
        return None
    if high < low:
        low, high = high, low
    return float(low), float(high), str(currency), period


# Re-exported for provider tests that assert on the enum rather than a literal.
REMOTE_FIELD = RemoteStatus.REMOTE
