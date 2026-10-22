"""HTTP access with retries, timeouts and a fetch record.

Every network read in the product goes through here, so a provider failure is
always a recorded, bounded event rather than an exception that escapes into the
discovery run. Providers are synchronous: a discovery run walks sources in
sequence from the CLI. The FastAPI app serves stored results and package
preparation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sabra_domain.errors import ProviderError
from sabra_domain.identity import sha256_bytes


@dataclass(slots=True)
class FetchRecord:
    """Provenance for one HTTP read: what was requested, what came back."""

    url: str
    status: int
    bytes_read: int
    content_hash: str
    elapsed_ms: int
    attempts: int
    from_cache: bool = False
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "bytes": self.bytes_read,
            "content_hash": self.content_hash,
            "elapsed_ms": self.elapsed_ms,
            "attempts": self.attempts,
            "error": self.error,
        }


@dataclass(slots=True)
class FetchResult:
    """A response body plus the record of how it was obtained."""

    url: str
    status: int
    body: bytes
    record: FetchRecord

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        try:
            return json.loads(self.text())
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "http", f"{self.url} returned non-JSON body: {exc}", retryable=False
            ) from exc


class HttpClient:
    """Bounded-retry JSON/text reader.

    Retries only idempotent GETs, only on transport errors and 5xx/429, and never
    more than `max_attempts`; every failure raises `ProviderError` so a caller can
    record it against the source instead of aborting the whole run.
    """

    RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        user_agent: str = "SabraJobEngine/0.1",
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.backoff_seconds = backoff_seconds
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            },
        )
        self.calls: list[FetchRecord] = []
        # `time.monotonic()` moment by which the current source must be done. Set by the
        # discovery run for each source, so one slow board cannot stall the run: every
        # attempt, every wait between attempts and every chunk read is bounded by it.
        self.deadline: float | None = None

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _remaining(self) -> float | None:
        return None if self.deadline is None else self.deadline - time.monotonic()

    def _over_budget(self, url: str, started: float, attempt: int) -> ProviderError:
        record = self._record(url, 0, b"", started, attempt, "source time budget exceeded")
        self.calls.append(record)
        return ProviderError(
            "http", f"GET {url} exceeded the source time budget", retryable=True
        )

    def _pause(self, seconds: float, url: str, started: float, attempt: int) -> None:
        """Wait between attempts, but never past the source's deadline."""

        remaining = self._remaining()
        if remaining is not None and remaining <= seconds:
            raise self._over_budget(url, started, attempt)
        time.sleep(seconds)

    def _fetch_once(self, url: str, params: dict[str, Any] | None) -> tuple[int, bytes]:
        """One request. Without a deadline this is a plain GET; with one, the body is read
        in chunks so a response that trickles in cannot outlast the budget."""

        remaining = self._remaining()
        if remaining is None:
            response = self._client.get(url, params=params)
            return response.status_code, response.content
        timeout = httpx.Timeout(max(0.05, min(self.timeout, remaining)))
        with self._client.stream("GET", url, params=params, timeout=timeout) as response:
            chunks: list[bytes] = []
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                if (self._remaining() or 0.0) <= 0:
                    raise httpx.ReadTimeout("the source time budget ran out mid-response")
            return response.status_code, b"".join(chunks)

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> FetchResult:
        last_error: str | None = None
        started = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            remaining = self._remaining()
            if remaining is not None and remaining <= 0:
                raise self._over_budget(url, started, attempt)
            try:
                status_code, body = self._fetch_once(url, params)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.max_attempts:
                    self._pause(self.backoff_seconds * attempt, url, started, attempt)
                    continue
                record = self._record(url, 0, b"", started, attempt, last_error)
                self.calls.append(record)
                raise ProviderError(
                    "http", f"GET {url} failed: {last_error}", retryable=True
                ) from exc

            if status_code in self.RETRY_STATUSES and attempt < self.max_attempts:
                last_error = f"HTTP {status_code}"
                self._pause(self.backoff_seconds * attempt, url, started, attempt)
                continue

            record = self._record(url, status_code, body, started, attempt, None)
            self.calls.append(record)
            if status_code >= 400:
                raise ProviderError(
                    "http",
                    f"GET {url} returned HTTP {status_code}",
                    retryable=status_code in self.RETRY_STATUSES,
                )
            return FetchResult(url=url, status=status_code, body=body, record=record)

        raise ProviderError("http", f"GET {url} exhausted retries: {last_error}", retryable=True)

    @staticmethod
    def _record(
        url: str, status: int, body: bytes, started: float, attempts: int, error: str | None
    ) -> FetchRecord:
        return FetchRecord(
            url=url,
            status=status,
            bytes_read=len(body),
            content_hash=sha256_bytes(body),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
            error=error,
        )
