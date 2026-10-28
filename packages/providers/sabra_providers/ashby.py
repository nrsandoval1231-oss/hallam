"""Ashby discovery and normalization.

Endpoint: `GET https://api.ashbyhq.com/posting-api/job-board/{token}`. Board
payloads carry structured fields the other Tier-1 boards lack: a boolean
`isRemote`, a `workplaceType` (`Remote`/`Hybrid`/`OnSite`), an `employmentType`
enum (`FullTime`, `PartTime`, `Contract`, `Intern`), and a postal `address`.
Some boards also publish a `compensation` block whose
`compensationTierSummary` is free text; when present it is treated like any
other salary string and parsed, never trusted as structured numbers.
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

BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"
JOB_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}/{job_id}"
HUMAN_URL = "https://jobs.ashbyhq.com/{token}"


class AshbyProvider(Provider):
    kind = SourceKind.ASHBY

    def board_url(self) -> str:
        return HUMAN_URL.format(token=self.token)

    # -- discovery ------------------------------------------------------

    def discover(self, search_profile=None) -> DiscoveryBatch:
        url = BOARD_URL.format(token=self.token)
        result = self.http.get(url)
        payload = result.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ProviderError(self.token, f"unexpected board payload from {url}", retryable=False)

        ref = ProviderRef(
            kind=self.kind,
            token=self.token,
            source_job_id=self.token,
            url=url,
        )
        batch = DiscoveryBatch(ref=ref, fetches=[result.record])
        for item in payload["jobs"]:
            # Ashby keeps unlisted postings on the board; only listed ones are
            # openings a candidate could actually pursue.
            if item.get("isListed") is False:
                continue
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
                        url=item.get("jobUrl") or self.board_url(),
                    ),
                    payload=item,
                    fetched_at=datetime.now(UTC),
                    fetch=result.record,
                    company_hint=item.get("hiringOrganizationName") or self.display_name,
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
            company_hint=payload.get("hiringOrganizationName") or self.display_name,
            company_domain=self.company_domain,
        )

    def verify(self, ref: ProviderRef, known_hash: str | None = None) -> VerifyResult:
        """The single-job endpoint 404s when the posting is gone or unlisted."""

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
        if isinstance(payload, dict) and payload.get("isListed") is False:
            return VerifyResult(
                available=False,
                changed=False,
                http_status=result.status,
                detail="posting unlisted",
                checked_at=datetime.now(UTC),
            )
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

        content_html = payload.get("descriptionHtml") or ""
        content_text = html_to_text(content_html)
        sections = split_sections(content_text)

        location_raw = payload.get("location")
        display_location, city, region, all_locations = parse_location(location_raw)
        if len(all_locations) > 1:
            warnings.append(f"multi-location posting: {display_location!r}")

        salary = extract_salary(_compensation_summary(payload.get("compensation")))
        if salary is None:
            salary = extract_salary(content_text)

        employment_type, et_warnings = parse_employment_type(payload.get("employmentType"), content_text)
        warnings.extend(et_warnings)

        remote_status = _remote_status(payload, display_location, f"{title}\n{content_text}")

        travel = extract_travel(content_text)

        job = CanonicalJob(
            source=self.kind,
            source_job_id=str(payload.get("id")),
            source_url=payload.get("jobUrl") or self.board_url(),
            company=payload.get("hiringOrganizationName") or self.display_name,
            company_domain=self.company_domain,
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
            description=content_text or None,
            requirements=sections.get("requirements"),
            preferred_qualifications=sections.get("preferred_qualifications"),
            responsibilities=sections.get("responsibilities"),
            travel=travel,
            department=payload.get("department") or payload.get("team"),
            seniority=parse_seniority(title),
            date_posted=parse_date(payload.get("publishedAt")),
            date_discovered=raw_job.fetched_at,
            status=str(JobStatus.DISCOVERED),
            raw_source_hash=raw_job.source_hash(),
            board_token=self.token,
            discovered_via=f"ashby:{self.token}",
            raw_payload={
                "id": payload.get("id"),
                "department": payload.get("department"),
                "team": payload.get("team"),
                "employment_type": payload.get("employmentType"),
                "workplace_type": payload.get("workplaceType"),
                "is_remote": payload.get("isRemote"),
                "apply_url": payload.get("applyUrl"),
                "address": payload.get("address"),
                "published_at": payload.get("publishedAt"),
                "updated_at": payload.get("updatedAt"),
            },
        )
        return self._finalize(job, warnings)


def _compensation_summary(compensation: Any) -> str | None:
    """Ashby's compensation block is employer free text in `compensationTierSummary`.

    Returns the summary string when present; numeric fields, when a board
    publishes them, are surfaced as text for the same parser everything else
    uses so no source gets a bespoke salary code path.
    """

    if not isinstance(compensation, dict):
        return None
    summary = compensation.get("compensationTierSummary")
    if summary:
        return str(summary)
    parts = [compensation.get(key) for key in ("min", "max", "currency") if compensation.get(key)]
    return " ".join(str(p) for p in parts) or None


def _remote_status(payload: dict[str, Any], location: str | None, text: str) -> RemoteStatus:
    """Prefer Ashby's structured flags; prose is the fallback."""

    workplace = str(payload.get("workplaceType") or "").strip().lower()
    if workplace == "remote" or payload.get("isRemote") is True:
        return RemoteStatus.REMOTE
    if workplace == "hybrid":
        return RemoteStatus.HYBRID
    if workplace == "onsite":
        return RemoteStatus.ONSITE
    return parse_remote_status(location=location, text=text)
