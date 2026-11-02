"""Which countries or regions a posting's location text names, and which
cities a named US metro area covers.

Used for REMOTE postings only. A remote role is not tied to a city, but it is usually
tied to a country ("Remote - Canada", "Remote / Australia / Singapore", "Remote - EMEA"),
and a candidate who can only work in the US cannot take those. The detector is
deliberately conservative: it recognises countries and regions by name and ISO code,
reads a US state name or abbreviation as the United States, and never guesses from
city names. The country registry is broad — every country a major job board names in
remote listings — but text that names nothing it knows is still reported as naming
nothing, which the filter treats as "open" (absence of evidence is not evidence of
rejection), so "Remote", "Remote - Denver" and genuinely novel place names keep
passing a US-only profile.

The metro registry answers a different question: an ON-SITE posting in "Plano, TX" is
inside "Dallas-Fort Worth", and a search profile should not have to enumerate every
suburb to say so. Matching is phrase-based and state-guarded — a posting in "Dallas,
GA" never satisfies the Dallas-Fort Worth metro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .identity import (
    US_STATE_ABBREVIATIONS,
    normalize_location,
    normalize_text,
    token_matches_phrase,
)

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
    # South America
    "AR": {"argentina"},
    "CL": {"chile"},
    "CO": {"colombia"},
    "PE": {"peru"},
    "EC": {"ecuador"},
    "UY": {"uruguay"},
    "PY": {"paraguay"},
    "BO": {"bolivia"},
    "VE": {"venezuela"},
    "GY": {"guyana"},
    "SR": {"suriname"},
    # Central America & Caribbean
    "CR": {"costa rica"},
    "PA": {"panama"},
    "GT": {"guatemala"},
    "HN": {"honduras"},
    "SV": {"el salvador"},
    "NI": {"nicaragua"},
    "BZ": {"belize"},
    "CU": {"cuba"},
    "HT": {"haiti"},
    "JM": {"jamaica"},
    "TT": {"trinidad and tobago", "trinidad", "tobago"},
    "DO": {"dominican republic"},
    # Europe (beyond the founding members above)
    "PT": {"portugal"},
    "GR": {"greece"},
    "AT": {"austria"},
    "BE": {"belgium"},
    "CH": {"switzerland"},
    "SE": {"sweden"},
    "NO": {"norway"},
    "DK": {"denmark"},
    "FI": {"finland"},
    "IS": {"iceland"},
    "CZ": {"czech republic", "czechia", "czech"},
    "SK": {"slovakia", "slovak republic"},
    "HU": {"hungary"},
    "RO": {"romania"},
    "BG": {"bulgaria"},
    "RS": {"serbia"},
    "HR": {"croatia"},
    "SI": {"slovenia"},
    "BA": {"bosnia", "bosnia and herzegovina"},
    "ME": {"montenegro"},
    "MK": {"north macedonia", "macedonia"},
    "AL": {"albania"},
    "XK": {"kosovo"},
    "LT": {"lithuania"},
    "LV": {"latvia"},
    "EE": {"estonia"},
    "BY": {"belarus"},
    "UA": {"ukraine"},
    "MD": {"moldova"},
    "RU": {"russia", "russian federation"},
    "MT": {"malta"},
    "CY": {"cyprus"},
    "LU": {"luxembourg"},
    "LI": {"liechtenstein"},
    "AD": {"andorra"},
    "MC": {"monaco"},
    "SM": {"san marino"},
    "VA": {"vatican", "vatican city"},
    # Middle East & North Africa
    "TR": {"turkey", "turkiye"},
    "IL": {"israel"},
    "AE": {"united arab emirates", "uae"},
    "SA": {"saudi arabia"},
    "QA": {"qatar"},
    "KW": {"kuwait"},
    "BH": {"bahrain"},
    "OM": {"oman"},
    "JO": {"jordan"},
    "LB": {"lebanon"},
    "EG": {"egypt"},
    "MA": {"morocco"},
    "DZ": {"algeria"},
    "TN": {"tunisia"},
    "LY": {"libya"},
    "IQ": {"iraq"},
    "SY": {"syria"},
    "YE": {"yemen"},
    # Sub-Saharan Africa
    "NG": {"nigeria"},
    "KE": {"kenya"},
    "GH": {"ghana"},
    "ET": {"ethiopia"},
    "ZA": {"south africa"},
    "UG": {"uganda"},
    "TZ": {"tanzania"},
    "RW": {"rwanda"},
    "SN": {"senegal"},
    "CI": {"ivory coast", "cote divoire", "côte divoire"},
    "CM": {"cameroon"},
    "ZW": {"zimbabwe"},
    "ZM": {"zambia"},
    "AO": {"angola"},
    "MZ": {"mozambique"},
    "NA": {"namibia"},
    "BW": {"botswana"},
    "MG": {"madagascar"},
    "SD": {"sudan"},
    "SS": {"south sudan"},
    "CD": {"democratic republic of the congo", "drc"},
    "CG": {"republic of the congo", "congo", "congo-brazzaville"},
    "ML": {"mali"},
    "NE": {"niger"},
    "TD": {"chad"},
    "BF": {"burkina faso"},
    "GN": {"guinea"},
    "GW": {"guinea-bissau"},
    "GQ": {"equatorial guinea"},
    "ER": {"eritrea"},
    "DJ": {"djibouti"},
    "SO": {"somalia"},
    "MW": {"malawi"},
    "LS": {"lesotho"},
    "SZ": {"eswatini", "swaziland"},
    "BJ": {"benin"},
    "TG": {"togo"},
    # Asia & Pacific
    "CN": {"china"},
    "HK": {"hong kong"},
    "TW": {"taiwan"},
    "KR": {"south korea", "korea"},
    "KP": {"north korea"},
    "VN": {"vietnam", "viet nam"},
    "TH": {"thailand"},
    "ID": {"indonesia"},
    "MY": {"malaysia"},
    "PK": {"pakistan"},
    "BD": {"bangladesh"},
    "LK": {"sri lanka"},
    "NP": {"nepal"},
    "KH": {"cambodia"},
    "MM": {"myanmar", "burma"},
    "LA": {"laos"},
    "MN": {"mongolia"},
    "KZ": {"kazakhstan"},
    "UZ": {"uzbekistan"},
    "AZ": {"azerbaijan"},
    "AM": {"armenia"},
    "KG": {"kyrgyzstan"},
    "TJ": {"tajikistan"},
    "TM": {"turkmenistan"},
    "AF": {"afghanistan"},
    "NZ": {"new zealand"},
    "PG": {"papua new guinea"},
    "FJ": {"fiji"},
    "BN": {"brunei"},
    "BT": {"bhutan"},
    "MV": {"maldives"},
    "EMEA": {"emea"},
    "APAC": {"apac"},
    "LATAM": {"latam", "latin america"},
    "EUROPE": {"europe", "european union"},
    "AFRICA": {"africa"},
    "ASIA": {"asia"},
    "MIDDLE EAST": {"middle east", "mideast"},
    "GCC": {"gcc", "gulf cooperation council"},
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
    "EUROPE": {
        "GB", "IE", "DE", "FR", "NL", "ES", "IT", "PL",
        "PT", "GR", "AT", "BE", "CH", "SE", "NO", "DK", "FI", "IS",
        "CZ", "SK", "HU", "RO", "BG", "RS", "HR", "SI", "BA", "ME", "MK",
        "AL", "XK", "LT", "LV", "EE", "BY", "UA", "MD", "MT", "CY", "LU",
        "LI", "AD", "MC", "SM", "VA",
    },
    "EMEA": {
        "EUROPE",
        "GB", "IE", "DE", "FR", "NL", "ES", "IT", "PL",
        "PT", "GR", "AT", "BE", "CH", "SE", "NO", "DK", "FI", "IS",
        "CZ", "SK", "HU", "RO", "BG", "RS", "HR", "SI", "BA", "ME", "MK",
        "AL", "XK", "LT", "LV", "EE", "BY", "UA", "MD", "MT", "CY", "LU",
        "LI", "AD", "MC", "SM", "VA",
        "TR", "IL", "AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB",
        "EG", "MA", "DZ", "TN", "LY",
        "NG", "KE", "GH", "ET", "ZA", "UG", "TZ", "RW", "SN", "CI", "CM",
        "ZW", "ZM", "AO", "MZ", "NA", "BW", "MG", "SD", "SS", "CD", "CG",
        "ML", "NE", "TD", "BF", "GN", "GW", "GQ", "ER", "DJ", "SO", "MW",
        "LS", "SZ", "BJ", "TG",
    },
    "APAC": {
        "IN", "AU", "SG", "JP", "PH",
        "CN", "HK", "TW", "KR", "KP", "VN", "TH", "ID", "MY", "PK", "BD",
        "LK", "NP", "KH", "MM", "LA", "MN", "KZ", "UZ", "AZ", "AM", "KG",
        "TJ", "TM", "AF", "NZ", "PG", "FJ", "BN", "BT", "MV",
    },
    "LATAM": {"MX", "BR", "AR", "CL", "CO", "PE", "EC", "UY", "PY", "BO", "VE", "GY", "SR"},
    "AFRICA": {
        "EG", "MA", "DZ", "TN", "LY",
        "NG", "KE", "GH", "ET", "ZA", "UG", "TZ", "RW", "SN", "CI", "CM",
        "ZW", "ZM", "AO", "MZ", "NA", "BW", "MG", "SD", "SS", "CD", "CG",
        "ML", "NE", "TD", "BF", "GN", "GW", "GQ", "ER", "DJ", "SO", "MW",
        "LS", "SZ", "BJ", "TG",
    },
    "ASIA": {
        "CN", "HK", "TW", "KR", "KP", "JP", "VN", "TH", "ID", "MY", "PH",
        "IN", "SG", "PK", "BD", "LK", "NP", "KH", "MM", "LA", "MN",
        "KZ", "UZ", "AZ", "AM", "KG", "TJ", "TM", "AF", "RU",
    },
    "MIDDLE EAST": {
        "AE", "SA", "IL", "TR", "QA", "KW", "BH", "OM", "JO", "LB",
        "EG", "IQ", "SY", "YE",
    },
    "GCC": {"AE", "SA", "QA", "KW", "BH", "OM"},
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
    "AFRICA": "Africa",
    "ASIA": "Asia",
    "MIDDLE EAST": "the Middle East",
    "GCC": "the GCC",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "PE": "Peru",
    "EC": "Ecuador",
    "UY": "Uruguay",
    "PY": "Paraguay",
    "BO": "Bolivia",
    "VE": "Venezuela",
    "GY": "Guyana",
    "SR": "Suriname",
    "CR": "Costa Rica",
    "PA": "Panama",
    "GT": "Guatemala",
    "HN": "Honduras",
    "SV": "El Salvador",
    "NI": "Nicaragua",
    "BZ": "Belize",
    "CU": "Cuba",
    "HT": "Haiti",
    "JM": "Jamaica",
    "TT": "Trinidad and Tobago",
    "DO": "the Dominican Republic",
    "PT": "Portugal",
    "GR": "Greece",
    "AT": "Austria",
    "BE": "Belgium",
    "CH": "Switzerland",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "IS": "Iceland",
    "CZ": "the Czech Republic",
    "SK": "Slovakia",
    "HU": "Hungary",
    "RO": "Romania",
    "BG": "Bulgaria",
    "RS": "Serbia",
    "HR": "Croatia",
    "SI": "Slovenia",
    "BA": "Bosnia and Herzegovina",
    "ME": "Montenegro",
    "MK": "North Macedonia",
    "AL": "Albania",
    "XK": "Kosovo",
    "LT": "Lithuania",
    "LV": "Latvia",
    "EE": "Estonia",
    "BY": "Belarus",
    "UA": "Ukraine",
    "MD": "Moldova",
    "RU": "Russia",
    "MT": "Malta",
    "CY": "Cyprus",
    "LU": "Luxembourg",
    "LI": "Liechtenstein",
    "AD": "Andorra",
    "MC": "Monaco",
    "SM": "San Marino",
    "VA": "Vatican City",
    "TR": "Turkey",
    "IL": "Israel",
    "AE": "the UAE",
    "SA": "Saudi Arabia",
    "QA": "Qatar",
    "KW": "Kuwait",
    "BH": "Bahrain",
    "OM": "Oman",
    "JO": "Jordan",
    "LB": "Lebanon",
    "EG": "Egypt",
    "MA": "Morocco",
    "DZ": "Algeria",
    "TN": "Tunisia",
    "LY": "Libya",
    "IQ": "Iraq",
    "SY": "Syria",
    "YE": "Yemen",
    "NG": "Nigeria",
    "KE": "Kenya",
    "GH": "Ghana",
    "ET": "Ethiopia",
    "ZA": "South Africa",
    "UG": "Uganda",
    "TZ": "Tanzania",
    "RW": "Rwanda",
    "SN": "Senegal",
    "CI": "Ivory Coast",
    "CM": "Cameroon",
    "ZW": "Zimbabwe",
    "ZM": "Zambia",
    "AO": "Angola",
    "MZ": "Mozambique",
    "NA": "Namibia",
    "BW": "Botswana",
    "MG": "Madagascar",
    "SD": "Sudan",
    "SS": "South Sudan",
    "CD": "the DR Congo",
    "CG": "the Republic of the Congo",
    "ML": "Mali",
    "NE": "Niger",
    "TD": "Chad",
    "BF": "Burkina Faso",
    "GN": "Guinea",
    "GW": "Guinea-Bissau",
    "GQ": "Equatorial Guinea",
    "ER": "Eritrea",
    "DJ": "Djibouti",
    "SO": "Somalia",
    "MW": "Malawi",
    "LS": "Lesotho",
    "SZ": "Eswatini",
    "BJ": "Benin",
    "TG": "Togo",
    "CN": "China",
    "HK": "Hong Kong",
    "TW": "Taiwan",
    "KR": "South Korea",
    "KP": "North Korea",
    "VN": "Vietnam",
    "TH": "Thailand",
    "ID": "Indonesia",
    "MY": "Malaysia",
    "PK": "Pakistan",
    "BD": "Bangladesh",
    "LK": "Sri Lanka",
    "NP": "Nepal",
    "KH": "Cambodia",
    "MM": "Myanmar",
    "LA": "Laos",
    "MN": "Mongolia",
    "KZ": "Kazakhstan",
    "UZ": "Uzbekistan",
    "AZ": "Azerbaijan",
    "AM": "Armenia",
    "KG": "Kyrgyzstan",
    "TJ": "Tajikistan",
    "TM": "Turkmenistan",
    "AF": "Afghanistan",
    "NZ": "New Zealand",
    "PG": "Papua New Guinea",
    "FJ": "Fiji",
    "BN": "Brunei",
    "BT": "Bhutan",
    "MV": "the Maldives",
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


# ---------------------------------------------------------------------------
# US metro areas: a stated metro expands to its principal cities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetroArea:
    """One metro: the names it is known by, the states that guard it, its cities."""

    display: str
    states: frozenset[str]
    aliases: tuple[str, ...]
    cities: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_city_phrases", tuple(normalize_location(c) for c in self.cities))
        object.__setattr__(
            self, "_alias_phrases", tuple(normalize_location(a) for a in self.aliases)
        )


# Cities are the principal suburbs an ATS posting would name; the state guard
# (not list length) is what keeps same-named cities in other states out.
_METROS: tuple[MetroArea, ...] = (
    MetroArea(
        "Dallas-Fort Worth",
        frozenset({"tx"}),
        ("Dallas-Fort Worth", "Dallas/Fort Worth", "DFW", "Dallas Fort Worth",
         "Metroplex", "Dallas", "Fort Worth"),
        (
            "Dallas", "Fort Worth", "Plano", "Irving", "Arlington", "Frisco", "McKinney",
            "Garland", "Denton", "Richardson", "Carrollton", "Lewisville", "Allen",
            "Mesquite", "Grapevine", "Coppell", "Addison", "Southlake", "Keller",
            "Grand Prairie", "Euless", "Bedford", "Hurst", "Mansfield", "DeSoto",
            "Duncanville", "Lancaster", "The Colony", "Wylie", "Sachse", "Rowlett",
            "Rockwall", "Burleson", "Cleburne", "Flower Mound", "Highland Park",
            "University Park", "Farmers Branch", "Prosper", "Little Elm", "Celina",
            "Trophy Club", "North Richland Hills", "Haltom City", "Watauga", "Saginaw",
            "Benbrook", "Crowley", "Forest Hill", "Kennedale", "Everman", "Pantego",
            "Dalworthington Gardens", "Melissa", "Anna", "Roanoke", "Haslet", "Justin",
            "Double Oak", "Bartonville", "Argyle", "Corinth", "Lake Dallas",
            "Hickory Creek", "Highland Village", "Copper Canyon", "Lantana", "Northlake",
            "Westlake", "Westworth Village", "Blue Mound", "Richland Hills", "Sansom Park",
        ),
    ),
    MetroArea(
        "Houston",
        frozenset({"tx"}),
        ("Houston", "Greater Houston", "Houston Metro"),
        (
            "Houston", "Sugar Land", "Katy", "Cypress", "Spring", "Humble", "Tomball",
            "Pasadena", "Baytown", "League City", "Pearland", "Friendswood",
            "Missouri City", "Stafford", "Richmond", "Rosenberg", "Kingwood",
            "Bellaire", "Deer Park", "La Porte", "Seabrook", "Kemah", "Atascocita",
            "The Woodlands", "West University Place", "Galena Park", "Fulshear",
            "Brookshire", "Huffman", "Crosby", "Dayton", "Mont Belvieu", "Waller",
        ),
    ),
    MetroArea(
        "Austin",
        frozenset({"tx"}),
        ("Austin", "Austin Metro", "Greater Austin"),
        (
            "Austin", "Round Rock", "Cedar Park", "Georgetown", "San Marcos",
            "Pflugerville", "Leander", "Kyle", "Buda", "Lakeway", "Hutto", "Taylor",
            "Manor", "Elgin", "Dripping Springs", "West Lake Hills", "Bee Cave",
            "Anderson Mill", "Jollyville", "Lago Vista", "Liberty Hill", "Florence",
            "Jarrell", "Salado", "Bastrop", "Lockhart",
        ),
    ),
    MetroArea(
        "San Antonio",
        frozenset({"tx"}),
        ("San Antonio", "San Antonio Metro", "Greater San Antonio"),
        (
            "San Antonio", "Alamo Heights", "Helotes", "Converse", "Universal City",
            "Schertz", "Cibolo", "Boerne", "Leon Valley", "Windcrest", "Live Oak",
            "Castle Hills", "Terrell Hills", "Fair Oaks Ranch", "Bulverde", "Stone Oak",
            "Selma", "Garden Ridge", "Shavano Park", "Hollywood Park", "Hill Country Village",
        ),
    ),
    MetroArea(
        "Atlanta",
        frozenset({"ga"}),
        ("Atlanta", "Atlanta Metro", "Greater Atlanta", "ATL"),
        (
            "Atlanta", "Marietta", "Sandy Springs", "Alpharetta", "Roswell",
            "Johns Creek", "Duluth", "Decatur", "Smyrna", "Kennesaw", "Woodstock",
            "Canton", "Peachtree City", "Douglasville", "Newnan", "Stockbridge",
            "McDonough", "Tucker", "Norcross", "Suwanee", "Lawrenceville", "Snellville",
            "Lilburn", "Mableton", "East Point", "College Park", "Brookhaven",
            "Chamblee", "Dunwoody", "Vinings", "Austell", "Powder Springs", "Buckhead",
            "Union City", "Forest Park", "Morrow", "Riverdale", "Jonesboro", "Fairburn",
            "Milton", "Tyrone", "Fayetteville", "Hiram",
        ),
    ),
    MetroArea(
        "Denver",
        frozenset({"co"}),
        ("Denver", "Denver Metro", "Denver-Aurora", "Denver Aurora"),
        (
            "Denver", "Aurora", "Lakewood", "Arvada", "Westminster", "Thornton",
            "Centennial", "Broomfield", "Commerce City", "Wheat Ridge", "Golden",
            "Englewood", "Littleton", "Highlands Ranch", "Parker", "Castle Rock",
            "Brighton", "Northglenn", "Federal Heights", "Sheridan", "Edgewater",
            "Glendale", "Superior", "Louisville", "Lafayette", "Erie", "Morrison",
            "Evergreen", "Ken Caryl", "Columbine", "Watkins", "Bennett", "Strasburg",
        ),
    ),
    MetroArea(
        "Phoenix",
        frozenset({"az"}),
        ("Phoenix", "Phoenix Metro", "Phoenix Area", "Valley of the Sun"),
        (
            "Phoenix", "Mesa", "Chandler", "Scottsdale", "Glendale", "Tempe", "Peoria",
            "Surprise", "Goodyear", "Avondale", "Gilbert", "Queen Creek",
            "San Tan Valley", "Buckeye", "Cave Creek", "Carefree", "Fountain Hills",
            "Paradise Valley", "Sun City", "El Mirage", "Tolleson", "Litchfield Park",
            "Youngtown", "Guadalupe", "Apache Junction", "Anthem", "New River",
        ),
    ),
    MetroArea(
        "Chicago",
        frozenset({"il"}),
        ("Chicago", "Chicago Metro", "Chicagoland", "Chicagoland Area"),
        (
            "Chicago", "Aurora", "Naperville", "Joliet", "Elgin", "Waukegan", "Cicero",
            "Schaumburg", "Evanston", "Oak Park", "Skokie", "Des Plaines",
            "Arlington Heights", "Palatine", "Hoffman Estates", "Bolingbrook",
            "Orland Park", "Tinley Park", "Oak Lawn", "Berwyn", "Mount Prospect",
            "Wheaton", "Downers Grove", "Glenview", "Elmhurst", "Lombard",
            "Carol Stream", "Streamwood", "Bartlett", "Plainfield", "Romeoville",
            "Lockport", "Mokena", "Frankfort", "Homer Glen", "New Lenox", "Crystal Lake",
            "Algonquin", "Lake in the Hills", "McHenry", "Gurnee", "Mundelein",
            "Libertyville", "Northbrook", "Highland Park", "Deerfield", "Buffalo Grove",
            "Wheeling", "Vernon Hills", "Geneva", "St. Charles", "Batavia",
        ),
    ),
    MetroArea(
        "New York",
        frozenset({"ny", "nj", "ct"}),
        ("New York", "NYC", "New York City", "New York Metro", "Tri-State Area"),
        (
            "New York", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island",
            "Long Island City", "Astoria", "Flushing", "Jamaica", "Forest Hills",
            "Bay Ridge", "Park Slope", "Williamsburg", "Greenpoint", "Harlem",
            "Jersey City", "Hoboken", "Newark", "White Plains", "Yonkers",
            "New Rochelle", "Mount Vernon", "Stamford", "Norwalk", "Greenwich",
            "Westport", "Darien", "New Canaan", "Hempstead", "Mineola", "Garden City",
            "Long Beach", "Far Rockaway", "Bayside",
        ),
    ),
    MetroArea(
        "Boston",
        frozenset({"ma", "nh"}),
        ("Boston", "Boston Metro", "Greater Boston"),
        (
            "Boston", "Cambridge", "Somerville", "Brookline", "Quincy", "Newton",
            "Waltham", "Watertown", "Lexington", "Concord", "Bedford", "Burlington",
            "Woburn", "Malden", "Medford", "Everett", "Revere", "Chelsea", "Winthrop",
            "Arlington", "Belmont", "Needham", "Natick", "Framingham", "Wellesley",
            "Dedham", "Norwood", "Canton", "Braintree", "Milton", "Weymouth", "Peabody",
            "Danvers", "Beverly", "Salem", "Lynn", "Swampscott", "Marblehead",
            "Melrose", "Stoneham", "Wakefield", "Reading", "Wilmington", "Billerica",
            "Chelmsford", "Lowell", "Lawrence", "Haverhill", "Methuen", "Andover",
            "North Andover", "Acton", "Sudbury", "Wayland", "Weston", "Maynard",
            "Marlborough", "Southborough", "Westborough", "Hopkinton", "Ashland",
            "Holliston", "Medfield", "Walpole", "Sharon", "Foxborough", "Mansfield",
            "Nashua", "Manchester", "Londonderry", "Derry", "Salem", "Hudson",
        ),
    ),
    MetroArea(
        "Seattle",
        frozenset({"wa"}),
        ("Seattle", "Seattle Metro", "Puget Sound", "Greater Seattle"),
        (
            "Seattle", "Bellevue", "Redmond", "Kirkland", "Renton", "Everett",
            "Bothell", "Edmonds", "Shoreline", "Mercer Island", "Newcastle", "Issaquah",
            "Sammamish", "Woodinville", "Kenmore", "Lake Forest Park",
            "Mountlake Terrace", "Brier", "Lynnwood", "Mukilteo", "Mill Creek",
            "Snohomish", "Monroe", "Duvall", "Carnation", "Fall City", "Snoqualmie",
            "North Bend", "Kent", "Auburn", "Federal Way", "Des Moines", "SeaTac",
            "Tukwila", "Burien", "Normandy Park", "Vashon", "Maple Valley",
            "Covington", "Black Diamond", "Enumclaw",
        ),
    ),
    MetroArea(
        "Washington DC",
        frozenset({"dc", "md", "va"}),
        ("Washington DC", "Washington", "DC", "DC Metro", "Washington Metro",
         "National Capital Region", "DMV"),
        (
            "Washington", "Arlington", "Alexandria", "Fairfax", "Falls Church",
            "Reston", "Herndon", "Ashburn", "Leesburg", "Sterling", "McLean",
            "Bethesda", "Silver Spring", "Rockville", "Gaithersburg", "Germantown",
            "College Park", "Bowie", "Hyattsville", "Takoma Park", "Chevy Chase",
            "Potomac", "Wheaton", "Olney", "Clarksburg", "Dulles", "Chantilly",
            "Centreville", "Manassas", "Springfield", "Annandale", "Burke", "Lorton",
            "Mount Vernon", "Georgetown", "Capitol Hill", "Navy Yard", "Tysons",
            "Vienna", "Oakton", "Great Falls",
        ),
    ),
)


def _alias_key(value: str) -> str:
    text = re.sub(r"[-/,]", " ", (value or "").lower())
    return normalize_text(re.sub(r"\s+", " ", text).strip())


_METRO_BY_ALIAS: dict[str, MetroArea] = {}
for _metro in _METROS:
    for _alias in _metro.aliases + (_metro.display,):
        _METRO_BY_ALIAS.setdefault(_alias_key(_alias), _metro)

_ALL_STATE_TOKENS = frozenset(value.lower() for value in US_STATE_ABBREVIATIONS.values())


def find_metro(value: str | None) -> MetroArea | None:
    """The registry entry a profile's metro name refers to, or None if unrecognized."""

    return _METRO_BY_ALIAS.get(_alias_key(value or ""))


def known_metro_names() -> list[str]:
    """Display names of the recognized metros (for docs and diagnostics)."""

    return [metro.display for metro in _METROS]


def metro_matches_location(metro_value: str | None, location: str | None) -> bool:
    """Whether a posting location is inside the named metro.

    A recognized metro matches when the location names one of its principal
    cities or one of its own names — in both cases guarded by the metro's home
    states, so "Dallas, GA" never counts as the Dallas-Fort Worth metro. A
    location with no recognizable state is given the benefit of the doubt
    ("Plano" alone matches). An unrecognized metro value falls back to the
    legacy literal phrase check, which is strictly weaker than what a
    recognized metro gives.
    """

    normalized = normalize_location(location)
    if not normalized:
        return False
    metro = find_metro(metro_value)
    if metro is None:
        return token_matches_phrase(normalized, normalize_location(metro_value))
    tokens = set(normalized.split())
    named_states = tokens & _ALL_STATE_TOKENS
    if named_states and not (named_states & metro.states):
        return False
    if any(token_matches_phrase(normalized, alias) for alias in metro._alias_phrases):
        return True
    return any(token_matches_phrase(normalized, city) for city in metro._city_phrases)
