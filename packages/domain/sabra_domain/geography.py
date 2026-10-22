"""Which countries or regions a posting's location text names.

Used for REMOTE postings only. A remote role is not tied to a city, but it is usually
tied to a country ("Remote - Canada", "Remote / Australia / Singapore", "Remote - EMEA"),
and a candidate who can only work in the US cannot take those. The detector is small and
deliberately conservative: it recognises a handful of countries and regions by name and
ISO code, and reads a US state name or abbreviation as the United States. It does not
guess from city names, and text that names nothing it knows is reported as naming nothing,
which the filter treats as "open" (absence of evidence is not evidence of rejection).
"""

from __future__ import annotations

import re

from .identity import US_STATE_ABBREVIATIONS

# Canonical codes. Regions are their own codes so "Remote - EMEA" is not mistaken for any
# one country.
US = "US"

_COUNTRY_NAMES: dict[str, set[str]] = {
    "US": {
        "united states of america",
        "united states",
        "u.s.a.",
        "u.s.",
        "new england",
        "puerto rico",
    },
    "CA": {
        "canada",
        "ontario",
        "quebec",
        "british columbia",
        "alberta",
        "manitoba",
        "saskatchewan",
        "nova scotia",
        "new brunswick",
    },
    "GB": {
        "united kingdom",
        "great britain",
        "england",
        "scotland",
        "wales",
        "northern ireland",
    },
    "IE": {"ireland", "republic of ireland"},
    "DE": {"germany", "deutschland"},
    "FR": {"france"},
    "NL": {"netherlands", "the netherlands", "holland"},
    "ES": {"spain"},
    "IT": {"italy"},
    "PL": {"poland"},
    "IN": {"india"},
    "AU": {"australia", "new south wales"},
    "SG": {"singapore"},
    "MX": {"mexico"},
    "BR": {"brazil"},
    "JP": {"japan"},
    "PH": {"philippines"},
    "EMEA": {"emea"},
    "APAC": {"apac"},
    "LATAM": {"latam", "latin america"},
    "EUROPE": {"europe", "european union"},
}

# ISO-style codes, matched only when written in capitals and standing alone. "CA" (Canada
# or California), "IN" (India or Indiana) and "DE" (Germany or Delaware) are also US state
# abbreviations; the US reading wins for those, so an ambiguous string is never rejected on
# a guess.
_ISO_CODES: dict[str, str] = {
    "US": "US",
    "USA": "US",
    "UK": "GB",
    "GB": "GB",
    "IE": "IE",
    "FR": "FR",
    "NL": "NL",
    "ES": "ES",
    "IT": "IT",
    "PL": "PL",
    "AU": "AU",
    "SG": "SG",
    "MX": "MX",
    "BR": "BR",
    "JP": "JP",
    "PH": "PH",
    "CAN": "CA",
    "EU": "EUROPE",
}

_STATE_NAMES = set(US_STATE_ABBREVIATIONS)
_STATE_CODES = set(US_STATE_ABBREVIATIONS.values())

# Regions are accepted by a profile that lists them, and also cover the countries they hold.
_REGION_MEMBERS: dict[str, set[str]] = {
    "EUROPE": {"GB", "IE", "DE", "FR", "NL", "ES", "IT", "PL"},
    "EMEA": {"EUROPE", "GB", "IE", "DE", "FR", "NL", "ES", "IT", "PL"},
    "APAC": {"IN", "AU", "SG", "JP", "PH"},
    "LATAM": {"MX", "BR"},
}

_NORTH_AMERICA = "north america"

DISPLAY_NAMES = {
    "US": "the US",
    "CA": "Canada",
    "GB": "the UK",
    "IE": "Ireland",
    "DE": "Germany",
    "FR": "France",
    "NL": "the Netherlands",
    "ES": "Spain",
    "IT": "Italy",
    "PL": "Poland",
    "IN": "India",
    "AU": "Australia",
    "SG": "Singapore",
    "MX": "Mexico",
    "BR": "Brazil",
    "JP": "Japan",
    "PH": "the Philippines",
    "EMEA": "EMEA",
    "APAC": "APAC",
    "LATAM": "LATAM",
    "EUROPE": "Europe",
}


def _name_patterns() -> list[tuple[re.Pattern[str], set[str]]]:
    """Multi-letter names, longest first so "new mexico" is read as a state before
    "mexico" is read as a country and "northern ireland" before "ireland"."""

    entries: list[tuple[str, set[str]]] = [
        (_NORTH_AMERICA, {"US", "CA"}),
        ("americas", {"US", "CA", "LATAM"}),
        ("south america", {"LATAM"}),
    ]
    for name in _STATE_NAMES:
        entries.append((name, {US}))
    for code, names in _COUNTRY_NAMES.items():
        for name in names:
            entries.append((name, {code}))
    entries.sort(key=lambda entry: -len(entry[0]))
    return [
        (re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w])", re.IGNORECASE), codes)
        for name, codes in entries
    ]


_PATTERNS = _name_patterns()
_CODE = re.compile(r"(?<![\w.])([A-Z]{2,3})(?![\w])")
# Canadian province codes are only read after a comma ("Toronto, ON"): on their own they
# collide with ordinary words in capitals ("ON SITE").
_PROVINCE_CODE = re.compile(r"(?:,|\()\s*(?:ON|BC|AB|QC)(?![\w])")
_SPACES = re.compile(r"\s+")
# "Remote (Not available in Canada)", "excluding Canada": the place after these words is
# one the role is NOT open to, so it must not count as naming it.
_EXCLUSION = re.compile(
    r"\b(?:not\s+available\s+in|not\s+open\s+to|not\s+eligible\s+in|excluding|"
    r"except(?:\s+for)?|other\s+than)\b[^)\]]*",
    re.IGNORECASE,
)

# Profile values that mean "no country restriction".
ANYWHERE = "*"
_ANYWHERE_WORDS = frozenset(
    {"worldwide", "anywhere", "global", "globally", "any", "all", "everywhere"}
)
# A bare code in a profile means the country: "CA" is Canada here, though in a posting's
# "Denver, CA" it reads as the state.
_PROFILE_CODES = {**_ISO_CODES, "CA": "CA", "IN": "IN", "DE": "DE"}


def detect_regions(location: str | None) -> set[str]:
    """Canonical country or region codes the location text names; empty when it names none."""

    if not location:
        return set()
    text = _SPACES.sub(" ", location).strip()
    text = _EXCLUSION.sub(" ", text)
    found: set[str] = set()

    # Names first, consuming what they match so a shorter name inside a longer one
    # ("mexico" in "new mexico", "england" in "new england") cannot match again.
    for pattern, codes in _PATTERNS:

        def consume(match: re.Match[str], codes: set[str] = codes) -> str:
            found.update(codes)
            return " " * len(match.group(0))

        text = pattern.sub(consume, text)

    if _PROVINCE_CODE.search(text):
        found.add("CA")
    # Capitalised codes standing alone: "US", "UK", "Dallas, TX".
    for match in _CODE.finditer(text):
        token = match.group(1)
        if token in _STATE_CODES:
            found.add(US)
        elif token in _ISO_CODES:
            found.add(_ISO_CODES[token])
    return found


def resolve_country(value: str | None) -> set[str] | None:
    """The codes one `countries` entry from a profile stands for.

    `{ANYWHERE}` for "Worldwide" and its synonyms, a set of codes for a recognised country
    or region, and `None` for text this module does not understand, so a typo is reported
    instead of silently matching nothing.
    """

    text = (value or "").strip()
    if not text:
        return set()
    lower = text.lower()
    if lower in _ANYWHERE_WORDS:
        return {ANYWHERE}
    if lower in {"americas", "the americas"}:
        return {"US", "CA", "LATAM"}
    if len(text) <= 3 and text.upper() in _PROFILE_CODES:
        return {_PROFILE_CODES[text.upper()]}
    return detect_regions(text) or None


def _expand(codes: set[str]) -> set[str]:
    """`codes` plus the countries any region among them holds."""

    expanded = set(codes)
    changed = True
    while changed:
        changed = False
        for region, members in _REGION_MEMBERS.items():
            if region in expanded and not members <= expanded:
                expanded |= members
                changed = True
    return expanded


def normalize_countries(values: list[str]) -> set[str]:
    """The codes a profile's `countries` list stands for, with regions expanded.

    "United States", "USA" and "us" all mean the same thing. An empty set means no
    restriction: an empty list, or any entry that says Worldwide/Anywhere/Global.
    """

    accepted: set[str] = set()
    for value in values:
        codes = resolve_country(value)
        if codes is None:
            continue
        if ANYWHERE in codes:
            return set()
        accepted |= codes
    return _expand(accepted)


def remote_region_conflict(location: str | None, accepted: set[str]) -> tuple[bool, list[str]]:
    """Whether a remote posting's location names only places outside `accepted`.

    Returns `(conflict, named)`. No conflict when the text names nothing recognisable, or
    when at least one named place is accepted ("Remote - US or Canada" for a US profile).
    A region on the posting side stands for the countries it holds, so "Remote - EMEA" is
    acceptable to a profile that lists a European country.
    """

    named = detect_regions(location)
    if not named or not accepted:
        return False, sorted(named)
    return not (_expand(named) & accepted), sorted(named)


def describe(codes: list[str]) -> str:
    return ", ".join(DISPLAY_NAMES.get(code, code) for code in codes)
