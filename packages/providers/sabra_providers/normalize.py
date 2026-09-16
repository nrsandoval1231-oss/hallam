"""Deterministic normalization of raw ATS payloads into canonical fields.

Everything here is pure: input in, canonical values out, no I/O and no models.
Every function that infers rather than reads records what it inferred so the
result can be audited (requirement 8: preserve provenance).

The rules were written against captured real payloads, not guesses:
`tests/fixtures/greenhouse_revance_board.json` and
`tests/fixtures/lever_elfbeauty_board.json`. Greenhouse sends `content` as
HTML-*escaped* HTML and Lever sends `descriptionPlain` plus HTML `lists`, which is
why `html_to_text` must unescape before stripping tags — stripping first would
leave literal `&lt;p&gt;` in the description.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from typing import Any

from sabra_domain.enums import EmploymentType, RemoteStatus
from sabra_domain.identity import normalize_text

# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_BLOCK_TAGS = re.compile(
    r"</?(?:p|div|br|li|ul|ol|tr|table|h[1-6]|section|article)\b[^>]*>", re.IGNORECASE
)
_TAG = re.compile(r"<[^>]+>")
_BULLET = re.compile(r"^\s*(?:[-*\u2022\u25cf\u00b7]|\d+[.)])\s+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)


def html_to_text(raw: str | None) -> str:
    """Convert (possibly HTML-escaped) markup into readable plain text.

    Entity decoding happens first because Greenhouse escapes its HTML; a tag
    strip before decoding leaves visible `&lt;p&gt;` noise in every description.
    Lists keep their bullet character so downstream section splitting and
    requirement extraction still see item boundaries.
    """

    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = _BLOCK_TAGS.sub("\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = _TRAILING_WS.sub("", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def to_bullets(text: str | None) -> list[str]:
    """Extract list items from normalized text, falling back to sentences."""

    if not text:
        return []
    items: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _BULLET.match(stripped):
            items.append(_BULLET.sub("", stripped).strip())
    if items:
        return [i for i in items if len(i) > 3]
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.strip()) > 3]


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

# Order matters: "Preferred Qualifications" must be tested before
# "Qualifications", or every preferred section is misread as a requirement.
SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "preferred_qualifications",
        re.compile(
            r"^(?:preferred|desired|nice[- ]to[- ]have|bonus|plus(?:es)?|a plus|"
            r"preferred qualifications?|preferred skills?|what sets you apart|"
            r"additional qualifications?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "benefits",
        re.compile(
            r"^(?:benefits?|perks?|what we offer|we offer|compensation and benefits|"
            r"why (?:join|work)|our benefits|total rewards)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "requirements",
        re.compile(
            r"^(?:minimum |basic |required |key |core )?(?:qualifications?|requirements?|"
            r"skills?(?: and experience)?|education(?: and experience)?|experience|"
            r"what you(?:'| wi)ll need|who you are|must[- ]haves?|you have|"
            r"we are looking for|desired experience|essential qualifications)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "responsibilities",
        re.compile(
            r"^(?:responsibilit(?:y|ies)|essential (?:duties|functions)|duties|"
            r"what you(?:'| wi)ll do|the role|key accountabilities|accountabilities|"
            r"job summary|position summary|position overview|about the role|role summary|"
            r"your responsibilities|how you(?:'| wi)ll make an impact)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "travel",
        re.compile(r"^travel(?: requirements?| expectations?)?$", re.IGNORECASE),
    ),
    (
        "compensation",
        re.compile(
            r"^(?:compensation|salary|pay range|pay transparency|compensation range|"
            r"total compensation)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "about",
        re.compile(
            r"^(?:about (?:us|the company|the team|our company)|company overview|"
            r"who we are|our company|company description|about )\b",
            re.IGNORECASE,
        ),
    ),
]

_HEADING_MAX_WORDS = 9
_HEADING_MAX_CHARS = 70


def _heading_kind(line: str) -> str | None:
    """Classify a line as a section heading, or None when it is body text."""

    candidate = line.strip().rstrip(":").strip()
    if not candidate or len(candidate) > _HEADING_MAX_CHARS:
        return None
    if len(candidate.split()) > _HEADING_MAX_WORDS:
        return None
    if candidate.endswith((".", ",", ";")) and not candidate.endswith("..."):
        return None
    for kind, pattern in SECTION_PATTERNS:
        if pattern.match(candidate):
            return kind
    return None


def split_sections(text: str | None) -> dict[str, str]:
    """Split a description into canonical sections.

    Returns only the sections actually present. Unrecognized prose stays in
    `body`/`description` rather than being forced into a bucket, because a wrong
    bucket is worse than no bucket: it would make the qualification scorer read
    benefits copy as requirements.
    """

    if not text:
        return {}
    sections: dict[str, list[str]] = {}
    current = "body"
    for line in text.split("\n"):
        kind = _heading_kind(line)
        if kind is not None:
            current = kind
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    out: dict[str, str] = {}
    for key, lines in sections.items():
        joined = normalize_text("\n".join(lines))
        joined = "\n".join(line for line in "\n".join(lines).split("\n") if line.strip()).strip()
        if joined:
            out[key] = joined
    return out


# ---------------------------------------------------------------------------
# Compensation
# ---------------------------------------------------------------------------

_MONEY = r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kK])?"
_PERIOD_WORDS = {
    "hour": "HOUR",
    "hourly": "HOUR",
    "/hr": "HOUR",
    "/hour": "HOUR",
    "per hour": "HOUR",
    "year": "YEAR",
    "yearly": "YEAR",
    "annual": "YEAR",
    "annually": "YEAR",
    "per year": "YEAR",
    "/yr": "YEAR",
    "month": "MONTH",
    "monthly": "MONTH",
    "per month": "MONTH",
}
_SALARY_CONTEXT = re.compile(
    r"(salary|compensation|pay range|base pay|base salary|ote|on[- ]target earnings|"
    r"total compensation|pay\s*\$|starting at|hourly rate|per hour|hourly|range)",
    re.IGNORECASE,
)
_ANNUAL_MIN = 20_000.0
_HOURLY_MAX = 500.0


def _to_amount(number: str, suffix: str | None) -> float:
    value = float(number.replace(",", ""))
    if suffix and suffix.lower() == "k":
        value *= 1000
    return value


def detect_salary_period(context: str) -> str | None:
    lowered = context.lower()
    for token, period in _PERIOD_WORDS.items():
        if token in lowered:
            return period
    return None


def extract_salary(text: str | None) -> tuple[float, float, str, str] | None:
    """Extract an annualized-or-hourly salary range from text.

    Returns `(min, max, currency, period)` or None. Guessing is refused: a
    matched number is only accepted when salary context appears nearby, when the
    magnitude is plausible for the period, and when a range's two bounds are
    plausible together. An hourly figure is never recorded as an annual one —
    that is exactly the comparison error the hard filter must not make.
    """

    if not text:
        return None
    flat = normalize_text(text)
    matches = list(re.finditer(_MONEY, flat))
    if not matches:
        return None

    period = detect_salary_period(flat)

    def plausible(value: float) -> bool:
        if value <= 0:
            return False
        if period == "HOUR":
            return 5 <= value <= _HOURLY_MAX
        if value < _ANNUAL_MIN:
            return False
        return value <= 5_000_000

    accepted: list[float] = []
    for match in matches:
        window = flat[max(0, match.start() - 80) : match.end() + 80]
        if not _SALARY_CONTEXT.search(window):
            continue
        value = _to_amount(match.group(1), match.group(2))
        suffix_amount = match.group(2)
        # A bare small number ("3 openings", "$50 gift card") is not salary — but
        # the rule only holds for annualised pay. An hourly rate is legitimately a
        # small bare number, so the magnitude guard must be period-aware.
        if suffix_amount is None and value < 1000 and period != "HOUR":
            continue
        if not plausible(value):
            continue
        accepted.append(value)

    if not accepted:
        return None

    values = sorted(set(accepted))
    low, high = values[0], values[-1]
    inferred_period = period or ("YEAR" if low >= _ANNUAL_MIN else "UNKNOWN")
    if high / low > 20:
        # Two unrelated numbers captured by one regex is not a range.
        return None
    return low, high, "USD", inferred_period


def salary_from_metadata(pairs: list[tuple[str, str]] | None) -> tuple[float, float, str, str] | None:
    """Read a pay range from structured metadata fields when a board provides one."""

    if not pairs:
        return None
    for name, value in pairs:
        label = (name or "").lower()
        if any(
            key in label
            for key in ("salary", "compensation", "pay range", "ote", "pay range", "rate")
        ):
            found = extract_salary(value)
            if found:
                return found
            # Structured fields may hold a bare number.
            digits = re.findall(r"\d[\d,]*(?:\.\d+)?", value or "")
            if digits:
                amounts = sorted(float(d.replace(",", "")) for d in digits)
                period = detect_salary_period(value or "") or "YEAR"
                if amounts and (
                    (period == "HOUR" and amounts[0] >= 5) or amounts[0] >= _ANNUAL_MIN
                ):
                    return amounts[0], amounts[-1], "USD", period
    return None


# ---------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------

_TRAVEL_PATTERNS = [
    re.compile(r"(?:up to\s*)?(\d{1,3})\s*%\s*(?:of\s*)?travel", re.IGNORECASE),
    re.compile(r"travel[: ]+(?:up to\s*)?(\d{1,3})\s*%", re.IGNORECASE),
    re.compile(r"travel\s*(?:of|is|:)\s*(?:up to\s*)?(\d{1,3})\s*%", re.IGNORECASE),
]


def extract_travel(text: str | None) -> str | None:
    """Return the travel expectation as written, e.g. "up to 40% travel"."""

    if not text:
        return None
    for pattern in _TRAVEL_PATTERNS:
        match = pattern.search(text)
        if match:
            percent = int(match.group(1))
            if 0 <= percent <= 100:
                return f"up to {percent}% travel"
    if re.search(r"\bno travel\b", text, re.IGNORECASE):
        return "no travel"
    if re.search(r"\b(?:extensive|significant|frequent)\s+travel\b", text, re.IGNORECASE):
        return "extensive travel"
    return None


def travel_percent(value: str | None) -> int | None:
    if not value:
        return None
    if value == "no travel":
        return 0
    match = re.search(r"(\d{1,3})\s*%", value)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Employment type and work arrangement
# ---------------------------------------------------------------------------

_COMMISSION_ONLY = re.compile(
    r"(commission[- ]only|100%\s*commission|commission[- ]based\s+only|"
    r"straight\s+commission|1099\s+commission)",
    re.IGNORECASE,
)
_COMMITMENT_MAP = {
    "full time": EmploymentType.FULL_TIME,
    "fulltime": EmploymentType.FULL_TIME,
    "regular": EmploymentType.FULL_TIME,
    "permanent": EmploymentType.FULL_TIME,
    "part time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "contractor": EmploymentType.CONTRACT,
    "contract to hire": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "temp": EmploymentType.TEMPORARY,
    "internship": EmploymentType.INTERNSHIP,
    "intern": EmploymentType.INTERNSHIP,
    "commission only": EmploymentType.COMMISSION_ONLY,
}


def _commitment_key(value: str) -> str:
    """Normalize a structured commitment value for lookup.

    Sources spell the same concept differently — Lever sends "Full Time",
    schema.org (and therefore most company career pages) sends "FULL_TIME" — so
    separators are collapsed before lookup rather than one spelling being treated
    as unrecognized.
    """

    return normalize_text(value.replace("_", " ").replace("-", " ")).lower()


def parse_employment_type(
    structured: str | None, text: str | None = None
) -> tuple[EmploymentType, list[str]]:
    """Prefer the ATS field; fall back to explicit prose. Returns warnings."""

    warnings: list[str] = []
    if _COMMISSION_ONLY.search(text or ""):
        return EmploymentType.COMMISSION_ONLY, warnings
    if structured:
        key = _commitment_key(structured)
        if key in _COMMITMENT_MAP:
            return _COMMITMENT_MAP[key], warnings
    lowered = (text or "").lower()
    if re.search(r"\b1099\b", lowered) or re.search(r"\bcontract(?:or)?\b", lowered):
        if re.search(r"\b(?:this|the) (?:is a )?(?:1099|contract)\b", lowered):
            return EmploymentType.CONTRACT, warnings
    if re.search(r"\bfull[- ]time\b", lowered):
        return EmploymentType.FULL_TIME, warnings
    if re.search(r"\bpart[- ]time\b", lowered):
        return EmploymentType.PART_TIME, warnings
    if re.search(r"\bintern(?:ship)?\b", lowered):
        return EmploymentType.INTERNSHIP, warnings
    if structured:
        warnings.append(f"unrecognized employment commitment: {structured!r}")
    return EmploymentType.UNKNOWN, warnings


_REMOTE_MARKERS = re.compile(r"\b(remote|work from home|wfh|virtual|telecommut\w*)\b", re.IGNORECASE)
_HYBRID_MARKERS = re.compile(r"\bhybrid\b", re.IGNORECASE)


def parse_remote_status(
    *, structured: str | None = None, location: str | None = None, text: str | None = None
) -> RemoteStatus:
    """Determine work arrangement.

    Precedence: the ATS's own field, then the location string, then the title or
    body. A concrete city with no remote marker is read as ONSITE, which is the
    conventional meaning of a posted city; with no signal at all the answer is
    UNKNOWN rather than a default.
    """

    if structured:
        key = normalize_text(structured).lower()
        if "remote" in key:
            return RemoteStatus.REMOTE
        if "hybrid" in key:
            return RemoteStatus.HYBRID
        if "on-site" in key or "onsite" in key or "in-person" in key:
            return RemoteStatus.ONSITE
    if location:
        if _HYBRID_MARKERS.search(location):
            return RemoteStatus.HYBRID
        if _REMOTE_MARKERS.search(location):
            return RemoteStatus.REMOTE
        if re.search(r"[,]", location) or re.search(r"[A-Za-z]{2}\b", location):
            return RemoteStatus.ONSITE
    if text:
        if _HYBRID_MARKERS.search(text[:2000]):
            return RemoteStatus.HYBRID
        if _REMOTE_MARKERS.search(text[:2000]):
            return RemoteStatus.REMOTE
    return RemoteStatus.UNKNOWN


_MULTI_LOCATION_SPLIT = re.compile(r"\s*(?:/|\||;|\bor\b|\band\b)\s*", re.IGNORECASE)
_CITY_REGION = re.compile(r"^([^,]+?)\s*,\s*([A-Za-z]{2}|[A-Za-z .]+?)\s*$")


def parse_location(location: str | None) -> tuple[str | None, str | None, str | None, list[str]]:
    """Return `(primary display location, city, region, all locations)`.

    Multi-location strings are split so a two-city posting is visible to the
    geography filter instead of being hidden inside one opaque string.
    """

    if not location:
        return None, None, None, []
    cleaned = normalize_text(location)
    if not cleaned:
        return None, None, None, []
    parts = [normalize_text(p) for p in _MULTI_LOCATION_SPLIT.split(cleaned) if normalize_text(p)]
    parts = [p for p in parts if p]
    primary = parts[0] if parts else cleaned
    city = region = None
    match = _CITY_REGION.match(primary)
    if match:
        city = normalize_text(match.group(1))
        region = normalize_text(match.group(2)).upper()
    elif primary.lower() in {"remote", "united states", "usa", "us"}:
        city = None
    else:
        city = primary
    return cleaned, city, region, parts


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def parse_date(value: Any) -> date | None:
    """Parse a date from ISO text, epoch seconds or epoch milliseconds."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e11:  # milliseconds
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=__import__("datetime").UTC).date()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e11:
            number /= 1000.0
        from datetime import UTC

        return datetime.fromtimestamp(number, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        day = parse_date(value)
        if day is None:
            return None
        from datetime import UTC, time

        return datetime.combine(day, time.min, tzinfo=UTC)
    from datetime import UTC

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Seniority
# ---------------------------------------------------------------------------

_SENIORITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("INTERN", re.compile(r"\b(?:intern|internship)\b", re.IGNORECASE)),
    ("DIRECTOR", re.compile(r"\b(?:director|head of|vp|vice president|chief)\b", re.IGNORECASE)),
    # SENIOR is checked before MANAGER because it modifies the title rather than
    # naming the org level: "Senior Territory Manager" is a senior individual
    # contributor, and the word "Manager" in a sales job family is not rank.
    ("SENIOR", re.compile(r"^(?:senior|sr\.?|principal|staff)\b", re.IGNORECASE)),
    ("MANAGER", re.compile(r"\b(?:manager|supervisor|lead)\b", re.IGNORECASE)),
    ("ENTRY", re.compile(r"\b(?:entry[- ]level|associate|junior|jr\.?|graduate)\b", re.IGNORECASE)),
]


def parse_seniority(title: str | None) -> str | None:
    """Best-effort seniority label, or None when the title does not declare one.

    Ordering is deliberate and documented: DIRECTOR wins outright; SENIOR is
    matched as a leading modifier before MANAGER; an executive title with no
    level word yields None rather than a guess.
    """

    if not title:
        return None
    cleaned = title.strip()
    for label, pattern in _SENIORITY_PATTERNS:
        if pattern.search(cleaned):
            return label
    return None
