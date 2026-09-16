"""Greenhouse discovery and normalization.

Endpoint: `GET /v1/boards/{token}/jobs?content=true`. Board payloads are HTML
*escaped*, carry `location.name` (sometimes a multi-city string, sometimes
"Remote") and an optional `metadata` list of `{name, value}` pairs where pay
ranges appear when a board publishes them. There is no structured remote flag,
so the arrangement is read from the location string.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sabra_domain.enums import JobStatus, SourceKind
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
    salary_from_metadata,
    split_sections,
    travel_percent,
)

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
JOB_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"
HUMAN_URL = "https://job-boards.greenhouse.io/{token}"


class GreenhouseProvider(Provider):
    kind = SourceKind.GREENHOUSE

    def board_url(self) -> str:
        return HUMAN_URL.format(token=self.token)

    # -- discovery ------------------------------------------------------

    def discover(self, search_profile=None) -> DiscoveryBatch:
        url = BOARD_URL.format(token=self.token)
        result = self.http.get(url, params={"content": "true"})
        payload = result.json()
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise ProviderError(
                self.token, f"unexpected board payload from {url}", retryable=False
            )

        ref = ProviderRef(
            kind=self.kind,
            token=self.token,
            source_job_id=self.token,
            url=url,
        )
        batch = DiscoveryBatch(ref=ref, fetches=[result.record])
        jobs = payload.get("jobs") or []
        # Greenhouse reports the true board size in `meta.total`; recording it
        # lets the run distinguish "this is all there is" from "we captured a
        # subset", which is what makes truncation detectable instead of silent.
        reported = (payload.get("meta") or {}).get("total")
        batch.total_reported = int(reported) if isinstance(reported, int) else len(jobs)
        for item in jobs:
            job_id = str(item.get("id") or "").strip()
            if not job_id:
                batch.warnings.append("board item without an id skipped")
                continue
            batch.raw_jobs.append(
                RawJob(
                    ref=ProviderRef(
                        kind=self.kind,
                        token=self.token,
                        source_job_id=job_id,
                        url=item.get("absolute_url") or self.board_url(),
                    ),
                    payload=item,
                    fetched_at=datetime.now(UTC),
                    fetch=result.record,
                    company_hint=item.get("company_name") or self.display_name,
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
        result = self.http.get(url)
        payload = result.json()
        if not isinstance(payload, dict):
            raise ProviderError(self.token, f"unexpected job payload from {url}")
        return RawJob(
            ref=job_reference,
            payload=payload,
            fetched_at=datetime.now(UTC),
            fetch=result.record,
            company_hint=payload.get("company_name") or self.display_name,
            company_domain=self.company_domain,
        )

    def verify(self, ref: ProviderRef, known_hash: str | None = None) -> VerifyResult:
        """Use the single-job endpoint: it is one request and 404s when closed."""

        url = JOB_URL.format(token=self.token, job_id=ref.source_job_id)
        try:
            result = self.http.get(url)
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

    # -- normalization --------------------------------------------------

    def normalize(self, raw_job: RawJob) -> Any:
        payload = raw_job.payload
        warnings: list[str] = []
        title = (payload.get("title") or "").strip()
        if not title:
            warnings.append("posting has no title")

        content_html = payload.get("content") or ""
        content_text = html_to_text(content_html)
        sections = split_sections(content_text)

        location_raw = (payload.get("location") or {}).get("name")
        display_location, city, region, all_locations = parse_location(location_raw)
        if len(all_locations) > 1:
            warnings.append(f"multi-location posting: {display_location!r}")

        salary = salary_from_metadata(_metadata_pairs(payload.get("metadata")))
        if salary is None:
            salary = extract_salary(content_text)
        if salary is None:
            # Pay ranges are commonly published only in the office-level metadata.
            salary = extract_salary(display_location)

        employment_type, et_warnings = parse_employment_type(
            payload.get("employment_type") or payload.get("commitment"), content_text
        )
        warnings.extend(et_warnings)

        body_parts = [sections.get(key) for key in ("body",)]
        description = "\n\n".join(part for part in body_parts if part) or content_text
        # The full ad text stays in `description`; the sectioned views are the
        # derived slices, so nothing is lost if the splitter misreads a heading.
        description = content_text or description

        travel = extract_travel(content_text)
        if travel is None and travel_percent(sections.get("travel")) is not None:
            travel = f"up to {travel_percent(sections.get('travel'))}% travel"

        departments = [d.get("name") for d in (payload.get("departments") or []) if d.get("name")]
        offices = [o.get("name") for o in (payload.get("offices") or []) if o.get("name")]

        job = CanonicalJob(
            source=self.kind,
            source_job_id=str(payload.get("id")),
            source_url=payload.get("absolute_url") or self.board_url(),
            company=payload.get("company_name") or self.display_name,
            company_domain=self.company_domain,
            title=title,
            location=display_location,
            city=city,
            region=region,
            remote_status=parse_remote_status(
                location=display_location, text=f"{title}\n{content_text}"
            ),
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
            department=departments[0] if departments else None,
            seniority=parse_seniority(title),
            date_posted=parse_date(payload.get("first_published")) or parse_date(
                payload.get("updated_at")
            ),
            date_discovered=raw_job.fetched_at,
            status=str(JobStatus.DISCOVERED),
            raw_source_hash=raw_job.source_hash(),
            board_token=self.token,
            discovered_via=f"greenhouse:{self.token}",
            raw_payload={
                "id": payload.get("id"),
                "internal_job_id": payload.get("internal_job_id"),
                "requisition_id": payload.get("requisition_id"),
                "departments": departments,
                "offices": offices,
                "all_locations": all_locations,
                "application_deadline": payload.get("application_deadline"),
                "updated_at": payload.get("updated_at"),
                "first_published": payload.get("first_published"),
            },
        )
        return self._finalize(job, warnings)


def _metadata_pairs(metadata: Any) -> list[tuple[str, str]]:
    """Greenhouse metadata is a list of `{name, value}` or null."""

    if not isinstance(metadata, list):
        return []
    pairs: list[tuple[str, str]] = []
    for entry in metadata:
        if isinstance(entry, dict) and entry.get("name") is not None:
            pairs.append((str(entry.get("name")), str(entry.get("value") or "")))
    return pairs
