"""The provider interface (requirement 35) and its shared value objects.

Contract: `discover(search_profile)`, `fetch(job_reference)`,
`normalize(raw_job)`, `verify(job)`. Provider-specific shape handling lives here
and nowhere else; everything upstream of `normalize` deals in `CanonicalJob`.

Providers are synchronous and side-effect-free beyond HTTP reads. A provider that
raises is a *source* failure, not a run failure — the caller records it and
continues with the remaining sources (requirement 38).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sabra_domain.enums import SourceKind
from sabra_domain.identity import sha256_text, stable_json
from sabra_domain.schemas import CanonicalJob, NormalizationResult, SearchProfile
from sabra_domain.settings import Settings, get_settings

from .http import FetchRecord, HttpClient


@dataclass(slots=True)
class ProviderRef:
    """Stable pointer to one posting inside one source."""

    kind: SourceKind
    token: str
    source_job_id: str
    url: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source": str(self.kind),
            "board_token": self.token,
            "source_job_id": self.source_job_id,
            "url": self.url,
        }


@dataclass(slots=True)
class RawJob:
    """A posting exactly as the source sent it, plus how we got it."""

    ref: ProviderRef
    payload: dict[str, Any]
    fetched_at: datetime
    fetch: FetchRecord | None = None
    company_hint: str | None = None
    company_domain: str | None = None
    board_display_name: str | None = None

    def source_hash(self) -> str:
        """Hash of the source content. The deduplication/idempotency basis.

        Computed over a canonical rendering of the payload so key order from the
        JSON parser cannot change the hash.
        """

        return sha256_text(stable_json(self.payload))


@dataclass(slots=True)
class DiscoveryBatch:
    """Result of one provider's discovery pass."""

    ref: ProviderRef
    raw_jobs: list[RawJob] = field(default_factory=list)
    fetches: list[FetchRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    total_reported: int | None = None

    @property
    def count(self) -> int:
        return len(self.raw_jobs)


@dataclass(slots=True)
class VerifyResult:
    """Whether a posting is still live, and what changed if anything."""

    available: bool
    changed: bool
    source_hash: str | None = None
    http_status: int | None = None
    detail: str | None = None
    checked_at: datetime | None = None
    raw: RawJob | None = None


class Provider(ABC):
    """Common discovery interface (requirement 35)."""

    kind: SourceKind

    def __init__(
        self,
        token: str,
        *,
        display_name: str | None = None,
        company_domain: str | None = None,
        http: HttpClient | None = None,
        settings: Settings | None = None,
        max_items: int | None = None,
    ) -> None:
        self.token = token
        self.display_name = display_name or token
        self.company_domain = company_domain
        self.settings = settings or get_settings()
        self.max_items = max_items
        self._owns_http = http is None
        self.http = http or HttpClient(
            timeout=self.settings.discovery_timeout_seconds,
            user_agent=self.settings.discovery_user_agent,
        )

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> Provider:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- interface ------------------------------------------------------

    @abstractmethod
    def board_url(self) -> str:
        """The human-facing board this provider reads."""

    @abstractmethod
    def discover(self, search_profile: SearchProfile | None = None) -> DiscoveryBatch:
        """List postings. Must not raise for an empty board."""

    @abstractmethod
    def fetch(self, job_reference: ProviderRef) -> RawJob:
        """Retrieve one posting by reference."""

    @abstractmethod
    def normalize(self, raw_job: RawJob) -> NormalizationResult:
        """Map a raw posting onto the canonical `Job` (requirement 8)."""

    def verify(self, ref: ProviderRef, known_hash: str | None = None) -> VerifyResult:
        """Confirm a posting is still live and whether its content changed.

        Default implementation re-fetches and compares hashes; providers with a
        cheaper single-item endpoint override it.
        """

        from datetime import UTC

        try:
            raw = self.fetch(ref)
        except Exception as exc:  # a dead posting is an outcome, not a crash
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return VerifyResult(
                available=False,
                changed=False,
                http_status=status,
                detail=f"{type(exc).__name__}: {exc}",
                checked_at=datetime.now(UTC),
            )
        current = raw.source_hash()
        return VerifyResult(
            available=True,
            changed=known_hash is not None and current != known_hash,
            source_hash=current,
            detail="fetched",
            checked_at=datetime.now(UTC),
            raw=raw,
        )

    # -- helpers --------------------------------------------------------

    def _finalize(self, job: CanonicalJob, warnings: list[str]) -> NormalizationResult:
        """Fill provenance fields every canonical job must carry."""

        from datetime import UTC

        now = datetime.now(UTC)
        job.last_verified = now
        if job.date_discovered is None:
            job.date_discovered = now
        if job.company_domain is None and self.company_domain:
            job.company_domain = self.company_domain
        if job.board_token is None:
            job.board_token = self.token
        return NormalizationResult(job=job, warnings=warnings)
