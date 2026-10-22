"""Ashby discovery and normalization.

Endpoint: `GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true`.
One request returns the whole board: `{"apiVersion", "jobs": [...]}`. There is no
company field (the registry's display name stands in), no single-posting endpoint
(so `fetch` and `verify` re-read the board), and compensation is a list of
structured components rather than prose.

Work arrangement: Ashby sends both `workplaceType` (Remote / Hybrid / OnSite) and a
boolean `isRemote`. On real boards `isRemote` is true for hybrid roles too, so the
specific `workplaceType` wins and `isRemote` decides only when `workplaceType` is
absent.

Ashby has no application adapter in this product: an approved Ashby role is handed to
the person in a headed browser (see `ExecutionService`), with `applyUrl` as the page.
"""

from __future__ import annotations

import re
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
HUMAN_URL = "https://jobs.ashbyhq.com/{token}"

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_INTERVAL_PERIODS = (("YEAR", "YEAR"), ("MONTH", "MONTH"), ("HOUR", "HOUR"))


class AshbyProvider(Provider):
    kind = SourceKind.ASHBY

    def board_url(self) -> str:
        return HUMAN_URL.format(token=self.token)

    # -- reading the board ----------------------------------------------

    def _read_board(self) -> tuple[list[dict[str, Any]], Any]:
        """The board's postings (as sent) and the fetch result. Raises `ProviderError`."""

        url = BOARD_URL.format(token=self.token)
        result = self.http.get(url, params={"includeCompensation": "true"})
        payload = result.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ProviderError(self.token, f"unexpected board payload from {url}", retryable=False)
        return [item for item in payload["jobs"] if isinstance(item, dict)], result

    def _raw(self, item: dict[str, Any], record: Any) -> RawJob:
        return RawJob(
            ref=ProviderRef(
                kind=self.kind,
                token=self.token,
                source_job_id=str(item.get("id") or "").strip(),
                url=self._posting_url(item),
            ),
            payload=item,
            fetched_at=datetime.now(UTC),
            fetch=record,
            company_hint=self.display_name,
            company_domain=self.company_domain,
            board_display_name=self.display_name,
        )

    def _posting_url(self, item: dict[str, Any]) -> str:
        """The posting's own page. A posting without `jobUrl` gets the URL Ashby builds
        from the board token and id, never the board's index page (a manual handoff would
        open that and the person could not tell which role was meant)."""

        url = item.get("jobUrl")
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            return url
        return f"{self.board_url()}/{str(item.get('id') or '').strip()}"

    @staticmethod
    def _is_live(item: dict[str, Any], source_job_id: str) -> bool:
        return str(item.get("id") or "") == source_job_id and item.get("isListed") is not False

    # -- discovery ------------------------------------------------------

    def discover(self, search_profile=None) -> DiscoveryBatch:
        url = BOARD_URL.format(token=self.token)
        items, result = self._read_board()
        ref = ProviderRef(kind=self.kind, token=self.token, source_job_id=self.token, url=url)
        batch = DiscoveryBatch(ref=ref, fetches=[result.record])
        listed = 0
        for item in items:
            if item.get("isListed") is False:
                batch.warnings.append("unlisted posting skipped")
                continue
            if not str(item.get("id") or "").strip():
                batch.warnings.append("posting without an id skipped")
                continue
            listed += 1
            if self.max_items and len(batch.raw_jobs) >= self.max_items:
                batch.truncated = True
                continue
            batch.raw_jobs.append(self._raw(item, result.record))
        # The true size of the board, so a capped run is visible as truncated.
        batch.total_reported = listed
        return batch

    def fetch(self, job_reference: ProviderRef) -> RawJob:
        """Ashby has no single-posting endpoint: find the posting on the board."""

        items, result = self._read_board()
        for item in items:
            if self._is_live(item, job_reference.source_job_id):
                return self._raw(item, result.record)
        raise ProviderError(
            self.token,
            f"posting {job_reference.source_job_id} is not on the {self.token} board",
            retryable=False,
        )

    def verify(self, ref: ProviderRef, known_hash: str | None = None) -> VerifyResult:
        """Gone only when the board answered and the posting is not on it.

        A posting the board still returns with `isListed: false` counts as gone too: an
        unlisted posting is not open for applications.

        A board that cannot be read (timeout, 5xx, garbage) is "could not check" and
        carries no HTTP status, so it never expires a job. A 404 / 410 for the whole
        board means the board itself is gone.
        """

        checked = datetime.now(UTC)
        try:
            items, result = self._read_board()
        except ProviderError as exc:
            board_gone = "HTTP 404" in exc.message or "HTTP 410" in exc.message
            return VerifyResult(
                available=False,
                changed=False,
                http_status=404 if board_gone else None,
                detail=(
                    "posting closed: the job board no longer exists" if board_gone else exc.message
                ),
                checked_at=checked,
            )
        for item in items:
            if self._is_live(item, ref.source_job_id):
                raw = self._raw(item, result.record)
                current = raw.source_hash()
                return VerifyResult(
                    available=True,
                    changed=known_hash is not None and current != known_hash,
                    source_hash=current,
                    http_status=result.status,
                    detail="live",
                    checked_at=checked,
                    raw=raw,
                )
        return VerifyResult(
            available=False,
            changed=False,
            http_status=404,
            detail="posting closed: it is no longer on the board",
            checked_at=checked,
        )

    # -- normalization --------------------------------------------------

    def normalize(self, raw_job: RawJob) -> Any:
        payload = raw_job.payload
        warnings: list[str] = []
        title = (payload.get("title") or "").strip()
        if not title:
            warnings.append("posting has no title")

        description = (payload.get("descriptionPlain") or "").strip()
        if not description:
            description = html_to_text(payload.get("descriptionHtml") or "")
        sections = split_sections(description)

        display_location, city, region, country, all_locations = _locations(payload)
        if len(all_locations) > 1:
            warnings.append(f"multi-location posting: {display_location!r}")

        remote = _remote_status(payload, display_location, f"{title}\n{description[:3000]}")

        salary = salary_from_compensation(payload.get("compensation"))
        if salary is None:
            salary = extract_salary(description)

        commitment = payload.get("employmentType")
        employment_type, et_warnings = parse_employment_type(
            _CAMEL.sub(" ", commitment) if isinstance(commitment, str) else None, description
        )
        warnings.extend(et_warnings)

        job = CanonicalJob(
            source=self.kind,
            source_job_id=str(payload.get("id")),
            source_url=self._posting_url(payload),
            company=self.display_name,
            company_domain=self.company_domain,
            title=title,
            location=display_location,
            city=city,
            region=region,
            country=country,
            remote_status=remote,
            employment_type=employment_type,
            salary_min=salary[0] if salary else None,
            salary_max=salary[1] if salary else None,
            salary_currency=salary[2] if salary else None,
            salary_period=salary[3] if salary else None,
            description=description,
            requirements=sections.get("requirements"),
            preferred_qualifications=sections.get("preferred_qualifications"),
            responsibilities=sections.get("responsibilities"),
            travel=extract_travel(description),
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
                "jobUrl": payload.get("jobUrl"),
                "applyUrl": payload.get("applyUrl"),
                "team": payload.get("team"),
                "workplaceType": payload.get("workplaceType"),
                "isRemote": payload.get("isRemote"),
                "employmentType": payload.get("employmentType"),
                "publishedAt": payload.get("publishedAt"),
                "all_locations": all_locations,
            },
        )
        return self._finalize(job, warnings)


def _remote_status(payload: dict[str, Any], location: str | None, text: str) -> RemoteStatus:
    workplace = payload.get("workplaceType")
    if isinstance(workplace, str) and workplace.strip():
        status = parse_remote_status(structured=workplace)
        if status is not RemoteStatus.UNKNOWN:
            return status
    if payload.get("isRemote") is True:
        return RemoteStatus.REMOTE
    return parse_remote_status(location=location, text=text)


def _address_parts(address: Any) -> tuple[str | None, str | None, str | None]:
    postal = address.get("postalAddress") if isinstance(address, dict) else None
    if not isinstance(postal, dict):
        return None, None, None
    return (
        postal.get("addressLocality") or None,
        postal.get("addressRegion") or None,
        postal.get("addressCountry") or None,
    )


def _locations(
    payload: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None, list[str]]:
    """`(display, city, region, country, all locations)`.

    The display string is the posting's `location`, followed by any secondary
    locations so the geography filter can see every office (a Houston role that is
    also open in Dallas must not look Houston-only). When `location` is empty the
    structured address supplies "locality, region".
    """

    locality, addr_region, country = _address_parts(payload.get("address"))
    primary = (payload.get("location") or "").strip()
    if not primary and locality:
        primary = ", ".join(part for part in (locality, addr_region) if part)
    names = [primary] if primary else []
    for extra in payload.get("secondaryLocations") or []:
        name = ((extra.get("location") if isinstance(extra, dict) else None) or "").strip()
        if name and name not in names:
            names.append(name)
    if not names:
        return None, None, None, country, []
    display, city, region, parts = parse_location(" / ".join(names))
    # The structured address fills what the free-text location did not say.
    if primary and primary.lower() != "remote":
        city = city or locality
        region = region or addr_region
    return display, city, region, country, parts


def salary_from_compensation(compensation: Any) -> tuple[float, float, str, str] | None:
    """Pay range from Ashby's structured `summaryComponents`.

    Only `Salary` components count (equity, bonus and commission are not base pay). The
    period is recorded as published (YEAR, MONTH or HOUR) and is never annualised
    (assumption A3). Other intervals, or a component without numbers, are ignored. When
    several salary components share a period the widest range is returned.
    """

    if not isinstance(compensation, dict):
        return None
    components = compensation.get("summaryComponents")
    best: dict[str, tuple[float, float, str]] = {}
    for component in components if isinstance(components, list) else []:
        if not isinstance(component, dict):
            continue
        if str(component.get("compensationType") or "").lower() != "salary":
            continue
        interval = str(component.get("interval") or "").upper()
        period = next((value for key, value in _INTERVAL_PERIODS if key in interval), None)
        raw_low, raw_high = component.get("minValue"), component.get("maxValue")
        known = [v for v in (raw_low, raw_high) if isinstance(v, (int, float))]
        if period is None or not known:
            continue
        low, high = float(min(known)), float(max(known))
        if low <= 0:
            continue
        currency = str(component.get("currencyCode") or "USD")
        if period in best:
            old_low, old_high, old_currency = best[period]
            if old_currency != currency:
                continue
            low, high = min(low, old_low), max(high, old_high)
        best[period] = (low, high, currency)
    for period in ("YEAR", "HOUR", "MONTH"):
        if period in best:
            low, high, currency = best[period]
            return low, high, currency, period
    return None
