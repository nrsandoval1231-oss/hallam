"""Find out what a careers URL is, and whether its board is live (`sabra sources probe`).

Two properties matter here and are enforced in code, not by convention:

* **SSRF-safe.** The only hosts ever contacted are the three public ATS API hosts, and the
  request URL is built from a fixed template plus a token that passed a strict pattern. A
  pasted URL is read for its host and path only; it is never fetched. Redirects are not
  followed.
* **One attempt.** A probe runs inside a dashboard request, so it makes a single request
  with the discovery timeout and no retries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from sabra_domain.enums import SourceKind
from sabra_domain.errors import ProviderError, ValidationFailure
from sabra_domain.schemas import CanonicalJob, SearchProfile
from sabra_domain.settings import Settings

from .http import HttpClient
from .normalize import normalize_text

# The API hosts a probe may contact. Anything else is refused before a socket opens.
ALLOWED_API_HOSTS = frozenset({"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com"})
GREENHOUSE_BOARD_INFO_URL = "https://boards-api.greenhouse.io/v1/boards/{token}"

PROBE_KINDS = (SourceKind.GREENHOUSE, SourceKind.LEVER, SourceKind.ASHBY)
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
MAX_SAMPLE_TITLES = 5


@dataclass(frozen=True, slots=True)
class BoardLocator:
    kind: SourceKind
    token: str


def _clean_token(value: str) -> str:
    token = value.strip()
    if not _TOKEN.match(token):
        raise ValidationFailure(
            f"{value!r} is not a valid board token (letters, digits, '-', '_' and '.' only)"
        )
    return token


def _first_segment(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    return parts[0] if parts else None


def parse_kind(value: str | None) -> SourceKind | None:
    if value is None or not str(value).strip():
        return None
    try:
        kind = SourceKind(str(value).strip().upper())
    except ValueError as exc:
        raise ValidationFailure(
            f"unknown kind {value!r}; use one of {', '.join(str(k) for k in PROBE_KINDS)}"
        ) from exc
    if kind not in PROBE_KINDS:
        raise ValidationFailure(
            f"kind {kind} cannot be probed; use one of {', '.join(str(k) for k in PROBE_KINDS)}"
        )
    return kind


def parse_locator(url_or_token: str, kind: str | SourceKind | None = None) -> BoardLocator:
    """Work out the ATS and board token from a careers URL or a bare token.

    A URL on a known careers host names its own kind. A bare token cannot, so `kind`
    is required. A `kind` that contradicts the URL is an error rather than a guess.
    """

    text = (url_or_token or "").strip()
    if not text:
        raise ValidationFailure("enter a careers URL or a board token")
    wanted = parse_kind(kind.value if isinstance(kind, SourceKind) else kind)

    looks_like_url = "/" in text or "?" in text or "://" in text
    if not looks_like_url:
        if wanted is None:
            raise ValidationFailure(
                f"{text!r} is a bare token; say which service it is on with --kind "
                f"({', '.join(str(k) for k in PROBE_KINDS)})"
            )
        return BoardLocator(wanted, _clean_token(text))

    try:
        parts = urlsplit(text if "://" in text else f"https://{text}")
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise ValidationFailure(f"{text!r} is not a valid URL ({exc})") from exc
    if parts.scheme not in {"http", "https"}:
        raise ValidationFailure(f"unsupported URL scheme in {text!r}")
    path = parts.path

    detected: BoardLocator | None = None
    if host in {"job-boards.greenhouse.io", "boards.greenhouse.io"}:
        if path.rstrip("/").lower() == "/embed/job_board":
            token = (parse_qs(parts.query).get("for") or [""])[0]
        else:
            token = _first_segment(path) or ""
        if token and token.lower() != "embed":
            detected = BoardLocator(SourceKind.GREENHOUSE, _clean_token(token))
    elif host == "jobs.eu.lever.co":
        raise ValidationFailure(
            "EU Lever boards (jobs.eu.lever.co) are served from api.eu.lever.co, which this "
            "version does not read; this board cannot be added yet"
        )
    elif host == "jobs.lever.co":
        token = _first_segment(path)
        if token:
            detected = BoardLocator(SourceKind.LEVER, _clean_token(token))
    elif host == "jobs.ashbyhq.com":
        token = _first_segment(path)
        if token:
            detected = BoardLocator(SourceKind.ASHBY, _clean_token(token))
    elif host == "api.ashbyhq.com":
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) >= 3 and segments[:2] == ["posting-api", "job-board"]:
            detected = BoardLocator(SourceKind.ASHBY, _clean_token(segments[2]))

    if detected is None:
        raise ValidationFailure(
            f"could not find a board in {text!r}. Paste a Greenhouse (job-boards.greenhouse.io/"
            "<name>), Lever (jobs.lever.co/<name>) or Ashby (jobs.ashbyhq.com/<name>) "
            "careers URL, or a bare token with --kind"
        )
    if wanted is not None and wanted is not detected.kind:
        raise ValidationFailure(
            f"{text!r} is a {detected.kind} board, but --kind {wanted} was given"
        )
    return detected


def api_url(locator: BoardLocator) -> str:
    """The public API URL a probe reads, checked against the host allowlist."""

    from .ashby import BOARD_URL as ASHBY_URL
    from .greenhouse import BOARD_URL as GREENHOUSE_URL
    from .lever import BOARD_URL as LEVER_URL

    template = {
        SourceKind.GREENHOUSE: GREENHOUSE_URL,
        SourceKind.LEVER: LEVER_URL,
        SourceKind.ASHBY: ASHBY_URL,
    }[locator.kind]
    url = template.format(token=_clean_token(locator.token))
    assert_safe_api_url(url)
    return url


def assert_safe_api_url(url: str) -> None:
    host = (urlsplit(url).hostname or "").lower()
    if urlsplit(url).scheme != "https" or host not in ALLOWED_API_HOSTS:
        raise ValidationFailure(f"refusing to contact {host or url!r}: not a known job-board API")


def build_probe_http(settings: Settings) -> HttpClient:
    """One attempt, the discovery timeout, and no redirects."""

    client = httpx.Client(
        timeout=settings.discovery_timeout_seconds,
        follow_redirects=False,
        headers={
            "User-Agent": settings.discovery_user_agent,
            "Accept": "application/json",
        },
    )
    return HttpClient(
        timeout=settings.discovery_timeout_seconds,
        max_attempts=1,
        user_agent=settings.discovery_user_agent,
        client=client,
    )


@dataclass(slots=True)
class ProbeResult:
    kind: SourceKind
    token: str
    api_url: str
    live: bool
    posting_count: int = 0
    local_or_remote_count: int = 0
    sample_titles: list[str] = field(default_factory=list)
    error: str | None = None
    profile_name: str | None = None
    profile_has_cities: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "token": self.token,
            "api_url": self.api_url,
            "live": self.live,
            "posting_count": self.posting_count,
            "local_or_remote_count": self.local_or_remote_count,
            "sample_titles": list(self.sample_titles),
            "error": self.error,
            "profile_name": self.profile_name,
            "profile_has_cities": self.profile_has_cities,
        }


def _tokens(text: str | None) -> set[str]:
    return set(normalize_text(text or "").lower().replace("-", " ").split())


def _title_matches(title: str, profile: SearchProfile | None) -> bool:
    """Whether a title fits the profile's declared titles, or its family and level terms."""

    if profile is None:
        return False
    title_tokens = _tokens(title)
    for declared in [*profile.target_titles, *profile.acceptable_titles]:
        wanted = _tokens(declared)
        if wanted and wanted <= title_tokens:
            return True
    family = {t for term in profile.role_family_terms for t in _tokens(term)}
    level = {t for term in profile.role_level_terms for t in _tokens(term)}
    return bool(family & title_tokens) and bool(level & title_tokens)


def _mentions_local_or_remote(job: CanonicalJob, profile: SearchProfile | None) -> bool:
    haystack = f"{normalize_text(job.location or '')} {normalize_text(job.title)}".lower()
    if job.remote_status == "REMOTE" or re.search(r"(?<!\w)remote(?!\w)", haystack):
        return True
    if profile is None:
        return False
    cities = [normalize_text(c).lower() for c in profile.locations.cities if c.strip()]
    # Whole words only: "Plano" must not match "Planogram", nor "Austin" "Austine".
    return any(re.search(rf"(?<!\w){re.escape(city)}(?!\w)", haystack) for city in cities)


def probe_board(
    locator: BoardLocator,
    *,
    http: HttpClient,
    settings: Settings,
    profile: SearchProfile | None = None,
) -> ProbeResult:
    """Read the board once and summarise it. A dead board is a result, not an exception."""

    from .registry import PROVIDER_TYPES

    url = api_url(locator)
    result = ProbeResult(
        kind=locator.kind,
        token=locator.token,
        api_url=url,
        live=False,
        profile_name=profile.name if profile else None,
        profile_has_cities=bool(profile and profile.locations.cities),
    )
    provider = PROVIDER_TYPES[locator.kind](locator.token, http=http, settings=settings)
    try:
        batch = provider.discover()
    except ProviderError as exc:
        result.error = exc.message
        return result
    except Exception as exc:  # noqa: BLE001 - a malformed board is a dead probe, not a crash
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    result.live = True
    for raw in batch.raw_jobs:
        try:
            job = provider.normalize(raw).job
        except Exception:  # noqa: BLE001 - one odd posting must not hide the board
            continue
        result.posting_count += 1
        if _mentions_local_or_remote(job, profile):
            result.local_or_remote_count += 1
        if len(result.sample_titles) < MAX_SAMPLE_TITLES and _title_matches(job.title, profile):
            result.sample_titles.append(job.title)
    return result


def fetch_board_name(locator: BoardLocator, *, http: HttpClient) -> str | None:
    """The board's own display name, where the API provides one (Greenhouse only)."""

    if locator.kind is not SourceKind.GREENHOUSE:
        return None
    url = GREENHOUSE_BOARD_INFO_URL.format(token=_clean_token(locator.token))
    assert_safe_api_url(url)
    try:
        payload = http.get(url).json()
    except ProviderError:
        return None
    name = payload.get("name") if isinstance(payload, dict) else None
    return (name.strip() or None) if isinstance(name, str) else None
