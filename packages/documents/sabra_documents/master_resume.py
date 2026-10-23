"""Master résumé ingestion and rendering (requirement 16).

The master résumé is one canonical document per candidate. It is *evidence* for the
candidate model, never itself edited: every tailored résumé is generated from
`CandidateProfile + MasterResume + Job`, which is what prevents résumé drift.

Parsing is deterministic and deliberately conservative. Anything it cannot
structurally read is preserved in `unparsed_lines` rather than dropped, so a
tailoring run can never silently lose a fact the candidate wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sabra_domain.schemas import (
    CertificationEntry,
    ContactInfo,
    EducationEntry,
    EmploymentEntry,
    ProjectEntry,
    SkillEntry,
)

# Canonical section names, in the order a résumé normally presents them.
SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "summary": (
        "summary",
        "professional summary",
        "profile",
        "objective",
        "about",
        "executive summary",
        "career summary",
        "professional profile",
    ),
    "experience": (
        "experience",
        "professional experience",
        "work experience",
        "employment",
        "employment history",
        "work history",
        "career history",
        "relevant experience",
    ),
    "education": ("education", "academic background", "academics", "education and training"),
    "skills": (
        "skills",
        "core skills",
        "key skills",
        "technical skills",
        "core competencies",
        "competencies",
        "areas of expertise",
        "skills and expertise",
        "expertise",
    ),
    "certifications": (
        "certifications",
        "certificates",
        "licenses",
        "licenses and certifications",
        "certifications and licenses",
        "credentials",
        "training and certifications",
    ),
    "projects": ("projects", "selected projects", "portfolio", "key projects"),
    "awards": ("awards", "honors", "honours", "awards and honors", "recognition"),
    "publications": ("publications", "papers", "patents"),
    "volunteer": ("volunteer", "volunteer experience", "community involvement"),
    "languages": ("languages",),
}

_ALIAS_LOOKUP: dict[str, str] = {
    alias: canonical for canonical, aliases in SECTION_ALIASES.items() for alias in aliases
}

_BULLET = re.compile(r"^\s*(?:[-*\u2022\u25cf\u00b7\u25aa]|\d+[.)])\s+(.*)$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Allows a leading "(" so "(214) 555-0142" is captured whole rather than from "214".
_PHONE = re.compile(r"(?:\+?\d|\(\d)[\d\s().-]{7,}\d")
_URL = re.compile(r"(?:https?://|www\.)[^\s,;)]+")
_LINKEDIN = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/[^\s,;)]+", re.IGNORECASE)

# Markdown emphasis and heading markers, stripped before classification so the
# parser can read its own `render_markdown` output.
_MD_MARKERS = re.compile(r"^(?:#{1,6}\s*)|[*_]{1,2}")

# "Territory Manager | Acme Medical | Jan 2021 - Present"
_DATE_RANGE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4})"
    r"\s*(?:-|–|—|to)\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4}|present|current|now)",
    re.IGNORECASE,
)
_HEADING_LINE = re.compile(r"^[A-Z][A-Za-z /&'\-]{2,60}$")
_DEGREE = re.compile(
    r"\b(bachelor|master|associate|doctorate|phd|mba|b\.?s\.?|b\.?a\.?|m\.?s\.?|m\.?a\.?|"
    r"d\.?d\.?s\.?|b\.?s\.?n\.?|certificate|diploma)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ResumeSection:
    name: str
    heading: str
    lines: list[str] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(self.lines).strip()


@dataclass(slots=True)
class ResumeDocument:
    """A parsed résumé, plus an honest record of what could not be parsed."""

    raw_text: str
    contact: ContactInfo = field(default_factory=ContactInfo)
    headline: str | None = None
    summary: str | None = None
    employment: list[EmploymentEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    certifications: list[CertificationEntry] = field(default_factory=list)
    skills: list[SkillEntry] = field(default_factory=list)
    projects: list[ProjectEntry] = field(default_factory=list)
    sections: list[ResumeSection] = field(default_factory=list)
    unparsed_lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def section(self, name: str) -> ResumeSection | None:
        for entry in self.sections:
            if entry.name == name:
                return entry
        return None

    def bullet_count(self) -> int:
        return sum(len(e.accomplishments) for e in self.employment)

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "summary": self.summary,
            "employment_entries": len(self.employment),
            "bullets": self.bullet_count(),
            "education_entries": len(self.education),
            "certifications": len(self.certifications),
            "skills": len(self.skills),
            "projects": len(self.projects),
            "sections_found": [s.name for s in self.sections],
            "unparsed_line_count": len(self.unparsed_lines),
            "unparsed_lines": list(self.unparsed_lines),
            "warnings": self.warnings,
        }


def _clean_line(line: str) -> str:
    return line.replace("\xa0", " ").rstrip()


def _strip_markdown(line: str) -> str:
    """Remove heading and emphasis markers so markdown round-trips through the parser."""

    stripped = _MD_MARKERS.sub("", line.strip())
    return stripped.replace("**", "").replace("__", "").strip()


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return False
    if _BULLET.match(stripped):
        return False
    if stripped.endswith((".", ",", ";", ":")) and ":" not in stripped:
        return False
    plain = _strip_markdown(stripped)
    candidate = plain.rstrip(":").strip().lower()
    if candidate in _ALIAS_LOOKUP:
        return True
    # ALL-CAPS headings with no lowercase letters are the other common style.
    letters = [c for c in plain if c.isalpha()]
    if len(letters) >= 3 and all(c.isupper() for c in letters) and len(plain.split()) <= 5:
        return candidate in _ALIAS_LOOKUP or candidate.title() in {a.title() for a in _ALIAS_LOOKUP}
    return False


def split_resume_sections(text: str) -> tuple[list[ResumeSection], list[str]]:
    """Split résumé text into canonical sections.

    Returns `(sections, preamble_lines)`. Lines appearing before the first
    recognized heading are the preamble — usually contact details — and are
    returned rather than discarded.
    """

    sections: list[ResumeSection] = []
    preamble: list[str] = []
    current: ResumeSection | None = None
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _clean_line(raw)
        if _looks_like_heading(line):
            plain = _strip_markdown(line)
            canonical = _ALIAS_LOOKUP.get(plain.rstrip(":").strip().lower())
            if canonical:
                current = ResumeSection(name=canonical, heading=plain)
                sections.append(current)
                continue
        if current is None:
            preamble.append(line)
        else:
            current.lines.append(line)
    return sections, preamble


# Visual separators résumés put between inline fields ("Lubbock, TX • 2022 - 2025").
_SEP_SPLIT = re.compile(r"\s*[•●·▪|]\s*")
_EDGE_JUNK = " \t•●·▪|,;/-–—"
_PRESENT = {"present", "current", "now"}

_STREET_ADDRESS = re.compile(
    r"^(?P<street>\d+[^,]*),\s*"
    r"(?:(?:[^,]*\b(?:apt|apartment|suite|ste|unit)\b[^,]*|#[^,]*),\s*)?"
    r"(?P<city>[A-Za-z][A-Za-z .'-]*),\s*(?P<state>[A-Z]{2})\.?\s*"
    r"(?P<zip>\d{5}(?:-\d{4})?)?$"
)
_STATE_NAMES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas",
    "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
    "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
    "District of Columbia",
)
# A two-letter "state" is only a state when it is one: "Director, IT" is a job title, not a
# city in a state called IT.
_STATE_CODES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
        "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
        "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
        "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "AB", "BC", "MB", "NB", "NL",
        "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
    }
)
_STATE_NAME_ALTERNATION = "|".join(
    re.escape(n) for n in sorted(_STATE_NAMES, key=len, reverse=True)
)
_CITY_STATE = re.compile(
    r"^(?P<city>[A-Z][A-Za-z .'-]{1,30}),\s*(?P<state>[A-Z]{2}|"
    + _STATE_NAME_ALTERNATION
    + r")\.?(?:\s+(?P<zip>\d{5}(?:-\d{4})?))?$"
)


def _match_location(segment: str, pattern: re.Pattern[str]) -> re.Match[str] | None:
    """`pattern` matched against `segment`, with a two-letter state checked against real codes."""

    found = pattern.match(segment)
    if found is None:
        return None
    state = found.group("state")
    if len(state) == 2 and state not in _STATE_CODES:
        return None
    return found


_ROLE_SEP = re.compile(r"\s[|–—]\s")

# A standalone note about time away from work. Anchored at the start of the line so a
# sentence that merely mentions a sabbatical is not turned into an entry.
_BREAK_NOTE = re.compile(
    r"^(?:note\s*[:\-–—]\s*)?(?:"
    r"employment\s+gap|gap\s+in\s+(?:employment|work)|"
    r"career\s+(?:break|gap|pause|hiatus)|"
    r"break\s+(?:from|in)\s+(?:work|employment|my\s+career|career)|"
    r"time\s+(?:away|off)\s+from\s+(?:work|employment)|sabbatical|"
    r"stay[\s-]at[\s-]home\s+(?:parent|mom|mother|dad|father)|"
    r"full[\s-]time\s+(?:parent|parenting)|"
    r"(?:parental|family|medical)\s+leave"
    r")\b",
    re.IGNORECASE,
)

# Words that, straight after "medical leave" or "sabbatical", make the phrase part of a job
# title or a department ("Medical Leave Coordinator", "Family leave administration").
_WORK_WORDS = frozenset(
    {
        "program", "programs", "administration", "administrator", "claims", "policy", "policies",
        "benefits", "specialist", "coordinator", "manager", "services", "department", "team",
        "analyst", "compliance", "processing", "management",
    }
)
# What a real gap is spent on. A part of a "Career break — …" line that says this is the note.
_BREAK_ACTIVITY = re.compile(
    r"\b(parent\w*|caregiv\w*|famil\w*|child\w*|kids?|travel\w*|relocat\w*|study\w*|"
    r"educat\w*|recover\w*|illness|military|raising|personal|health\s+reasons)\b",
    re.IGNORECASE,
)
_COMPANY_SUFFIX = frozenset(
    {
        "inc", "llc", "ltd", "lp", "llp", "group", "co", "corp", "corporation", "company",
        "bank", "hospital", "university", "partners", "associates", "incorporated",
    }
)

_TITLE_NOUNS = frozenset(
    {
        "manager", "director", "associate", "representative", "rep", "specialist", "coordinator",
        "assistant", "executive", "consultant", "engineer", "analyst", "officer", "lead",
        "supervisor", "administrator", "technician", "clerk", "cashier", "agent", "owner",
        "founder", "president", "vp", "intern", "advisor", "nurse", "teacher", "instructor",
        "developer", "designer", "recruiter", "buyer", "planner", "accountant", "bookkeeper",
        "receptionist", "driver", "operator", "stylist", "esthetician", "trainer", "coach",
        "strategist", "principal", "barista", "server", "chef", "cook", "scientist", "waiter",
        "waitress", "bartender", "host", "hostess", "mechanic", "electrician", "paralegal",
        "attorney", "lawyer", "counselor", "therapist", "pharmacist", "physician", "dentist",
        "hygienist", "teller", "dispatcher", "programmer", "architect", "writer", "editor",
        "producer", "estimator", "inspector", "installer", "salesperson", "representatives",
    }
)

_INSTITUTION_WORD = re.compile(
    r"\b(university|univ\.?|college|institute|school|academy|polytechnic|seminary|conservatory|state)\b",
    re.IGNORECASE,
)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_GRAD_YEAR = re.compile(
    r"^(?:(?:graduated|graduation|grad\.?|completed|class\s+of|expected(?:\s+graduation)?|"
    r"anticipated)\s*(?:in|:)?\s*)?"
    rf"((?:{_MONTH}\s+)?\d{{4}})$",
    re.IGNORECASE,
)
_MINOR = re.compile(r"^minors?\b\s*(?:in\b|:|-|–)?\s*(.+)$", re.IGNORECASE)
_HONORS = re.compile(r"\b(cum laude|honou?rs?|dean'?s list|gpa|scholar\w*)\b", re.IGNORECASE)
_LEADING_SKILL_PREFIX = re.compile(
    r"^(?:proficien(?:t|cy)|skilled|experienced|fluent|expert|advanced|knowledgeable|familiar|"
    r"adept)\s+(?:in|with|at|using)\s+",
    re.IGNORECASE,
)


def _strip_edges(text: str) -> str:
    return text.strip(_EDGE_JUNK)


def _split_top_level(text: str, separators: str) -> list[str]:
    """Split on any separator character that is not inside parentheses or brackets."""

    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and char in separators:
            parts.append("".join(buffer))
            buffer = []
            continue
        buffer.append(char)
    parts.append("".join(buffer))
    return [p.strip() for p in parts if p.strip()]


def _first_match_by_line(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """First match, searched line by line so a number ending one line cannot join the next."""

    for line in text.split("\n"):
        found = pattern.search(line)
        if found:
            return found
    return None


def _contact_segment_kind(segment: str) -> str:
    if _EMAIL.search(segment) or _PHONE.search(segment) or _URL.search(segment):
        return "contact"
    if _LINKEDIN.search(segment):
        return "contact"
    if _match_location(segment, _STREET_ADDRESS):
        return "address"
    if _match_location(segment, _CITY_STATE):
        return "location"
    return "text"


def parse_contact(preamble: list[str], full_text: str) -> tuple[ContactInfo, str | None, list[str]]:
    """Extract contact details and a likely headline from the preamble.

    Each preamble line is read segment by segment (bullets and pipes are separators, so
    "• (806) 555-0173 •" is a phone number and nothing else). A street address or a
    "City, ST zip" segment is contact detail, never a headline.
    """

    joined = "\n".join(preamble)
    unparsed: list[str] = []
    email = _first_match_by_line(_EMAIL, joined) or _first_match_by_line(_EMAIL, full_text)
    phone = _first_match_by_line(_PHONE, joined) or _first_match_by_line(_PHONE, full_text)
    linkedin = _LINKEDIN.search(full_text)
    urls = [u for u in _URL.findall(full_text) if not (linkedin and u in linkedin.group(0))]

    city: str | None = None
    state: str | None = None
    postal: str | None = None
    candidate_lines: list[str] = []
    address_lines: set[int] = set()
    for index, raw in enumerate(preamble):
        stripped = raw.strip()
        if not stripped:
            continue
        segments = [s for s in (_strip_edges(p) for p in _SEP_SPLIT.split(stripped)) if s]
        kinds = [_contact_segment_kind(s) for s in segments]
        for segment, kind in zip(segments, kinds, strict=True):
            if kind in {"address", "location"} and city is None:
                found = _match_location(
                    segment, _STREET_ADDRESS if kind == "address" else _CITY_STATE
                )
                if found:
                    city = found.group("city").strip()
                    state = found.group("state")
                    postal = found.group("zip")
            if kind == "address":
                address_lines.add(index)
        if all(kind != "text" for kind in kinds):
            continue  # the whole line is contact detail
        if any(kind != "text" for kind in kinds):
            leftover = [s for s, k in zip(segments, kinds, strict=True) if k == "text"]
            candidate_lines.append(" | ".join(leftover))
        else:
            candidate_lines.append(stripped)

    if city is None:
        masked = "\n".join(line for i, line in enumerate(preamble) if i not in address_lines)
        masked = _LINKEDIN.sub(" ", masked)
        masked = _URL.sub(" ", masked)
        masked = _EMAIL.sub(" ", masked)
        masked = _PHONE.sub(" ", masked)
        pattern = re.compile(
            r"\b([A-Z][a-zA-Z .'-]{2,30}),\s*([A-Z]{2}|" + _STATE_NAME_ALTERNATION + r")\b"
        )
        for candidate_line in masked.split("\n"):
            for found_loc in pattern.finditer(candidate_line):
                if len(found_loc.group(2)) == 2 and found_loc.group(2) not in _STATE_CODES:
                    continue
                city = found_loc.group(1).strip()
                state = found_loc.group(2).strip()
                break
            if city is not None:
                break

    name: str | None = None
    headline: str | None = None
    for line in candidate_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if name is None and len(stripped.split()) <= 5 and not _BULLET.match(stripped):
            name = _strip_markdown(stripped)
            continue
        if headline is None and len(stripped) <= 120 and not _BULLET.match(stripped):
            headline = _strip_markdown(stripped)
            continue
        unparsed.append(stripped)

    contact = ContactInfo(
        full_name=name,
        email=email.group(0) if email else None,
        phone=phone.group(0).strip() if phone else None,
        linkedin_url=linkedin.group(0) if linkedin else None,
        website_url=urls[0] if urls else None,
        city=city,
        state=state,
        postal_code=postal,
    )
    return contact, headline, unparsed


def _plain_title_score(text: str) -> int:
    words = [w.strip(".,()").lower() for w in text.split()]
    if not words:
        return 0
    if words[-1] in _TITLE_NOUNS:
        return 2
    return 1 if any(w in _TITLE_NOUNS for w in words) else 0


def _title_score(text: str) -> int:
    """2 when the last word is a job-title noun, 1 when any word is, else 0.

    A parenthesised suffix is part of the title and may carry the noun:
    "Server (Part-time)" and "Various Roles (Server, Shift Lead)" both score 2.
    """

    base = re.sub(r"\([^)]*\)", " ", text)
    score = _plain_title_score(base)
    for inner in re.findall(r"\(([^)]*)\)", text):
        score = max(score, *(_plain_title_score(item) for item in inner.split(",")))
    return score


def _ends_with_company_suffix(text: str) -> bool:
    words = [w.strip(".,()&").lower() for w in text.split()]
    return bool(words) and words[-1] in _COMPANY_SUFFIX


def _order_title_employer(first: str, second: str) -> tuple[str, str]:
    """Return (title, employer). Title-first is the default. The two are swapped only when
    the second part clearly is a job title (it ends in a title noun), the first has no title
    word at all, and the second does not end like a company name:
    "Holiday Lite Source — Sales Associate". Anything less certain keeps the order written;
    `parse_master_resume` then warns that the order was guessed."""

    if (
        _title_score(second) == 2
        and _title_score(first) == 0
        and not _ends_with_company_suffix(second)
    ):
        return second, first
    return first, second


def _is_title_case(text: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'.&-]*", text) if len(w) > 1]
    return bool(words) and all(w[0].isupper() for w in words)


def looks_like_break_note(line: str) -> bool:
    """Whether a stored line (markdown, bullet or "Career break:" prefix and all) is a gap note."""

    plain = _strip_markdown(line.strip().lstrip("-*• ").strip())
    return bool(plain) and _is_break_note(plain, in_experience=True)


def _is_break_note(plain: str, *, in_experience: bool = True) -> bool:
    """Whether `plain` is a standalone note about time away from work.

    The line must start with the phrase, and the phrase must not be the front of a job title
    or department ("Medical Leave Coordinator", "Sabbatical Program Manager", "Family leave
    administration"). A "Phrase — Employer | dates" line is a role, not a note. Outside the
    experience section a note needs a date range or a "Note:" prefix, so a skill or a school
    that happens to start with the phrase is never pulled out of its section.
    """

    match = _BREAK_NOTE.match(plain)
    if match is None:
        return False
    noted = bool(re.match(r"note\s*[:\-–—]", plain, re.IGNORECASE))
    if not in_experience and not noted and not _DATE_RANGE.search(plain):
        return False
    following = re.match(r"[\s,]*([A-Za-z][A-Za-z-]*)", plain[match.end() :])
    if following and following.group(1).lower() in (_TITLE_NOUNS | _WORK_WORDS):
        return False
    if _ROLE_SEP.search(plain):
        for part in _header_parts(_DATE_RANGE.sub(" ", plain))[1:]:
            if _BREAK_ACTIVITY.search(part):
                continue
            if _title_score(part) > 0 or _ends_with_company_suffix(part) or _is_title_case(part):
                return False
    return True


def _header_parts(cleaned: str, *, hyphen: bool = False) -> list[str]:
    """Split a role header on its separators; `hyphen` also accepts a spaced " - "."""

    pattern = r"\s*(?:\||–|—|@| at |\s-\s)\s*" if hyphen else r"\s*(?:\||–|—|@| at )\s*"
    parts = [p.strip(" |,-–—\t") for p in re.split(pattern, cleaned)]
    return [p for p in parts if p]


def _split_title_employer_dates(line: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (title, employer, start, end) from an experience heading line."""

    cleaned = _strip_markdown(line.strip().lstrip("-*• ").strip())
    start = end = None
    match = _DATE_RANGE.search(cleaned)
    if match:
        start = match.group(1).strip()
        end = match.group(2).strip()
        cleaned = (cleaned[: match.start()] + cleaned[match.end() :]).strip(" |,-–—\t")

    parts = _header_parts(cleaned)
    title = parts[0] if parts else None
    employer = parts[1] if len(parts) > 1 else None
    # Some résumés put the employer first ("Acme Medical — Territory Manager").
    if employer and title and _DEGREE.search(employer) and not _DEGREE.search(title):
        title, employer = employer, title
    elif employer and title:
        title, employer = _order_title_employer(title, employer)
    return title, employer, start, end


def _is_current(end: str | None) -> bool:
    return bool(end and end.lower() in _PRESENT)


_LOCATION_JOIN = re.compile(r"\s*(?:;|/|\||\band\b)\s*")


def _is_city_list(text: str) -> bool:
    """One or more "City, ST" places (or "Remote") joined by ";", "/", "|" or "and"."""

    if len(text) > 160:
        return False
    segments = [s.strip(" ,") for s in _LOCATION_JOIN.split(text) if s.strip(" ,")]
    return bool(segments) and all(
        _match_location(s, _CITY_STATE) is not None
        or re.fullmatch(r"(?:remote|hybrid|virtual)", s, re.IGNORECASE) is not None
        for s in segments
    )


def _looks_like_location(text: str) -> bool:
    if not text:
        return True
    if _is_city_list(text):
        return True
    if len(text) > 50 or len(text.split()) > 6 or _ROLE_SEP.search(text):
        return False
    return "," in text or bool(re.search(r"\b(remote|hybrid|virtual)\b", text, re.IGNORECASE))


def _date_line_parts(line: str | None) -> tuple[str, str, str | None] | None:
    """(start, end, location) when `line` is only dates and an optional location."""

    if line is None or _BULLET.match(line):
        return None
    plain = _strip_markdown(line.strip())
    match = _DATE_RANGE.search(plain)
    if not match:
        return None
    # Bullets between fields become spaces; a pipe stays, because it may join several places.
    rest = re.sub(
        r"\s*[•●·▪]\s*", " ", plain[: match.start()] + " " + plain[match.end() :]
    )
    location = _strip_edges(re.sub(r"\s+", " ", rest))
    if not _looks_like_location(location):
        return None
    return match.group(1).strip(), match.group(2).strip(), location or None


def _break_entry(text: str) -> EmploymentEntry:
    """An employment entry for a stated gap. The title is the candidate's own wording."""

    cleaned = _strip_markdown(text.strip()).strip()
    start = end = None
    title = cleaned
    match = _DATE_RANGE.search(cleaned)
    if match:
        start = match.group(1).strip()
        end = match.group(2).strip()
        if not cleaned[match.end() :].strip(" )].,;|/-–—"):
            head = cleaned[: match.start()].rstrip(" ([|/,;:-–—")
            head = re.sub(r"\s+(?:from|in|during|between|since)$", "", head, flags=re.IGNORECASE)
            title = head.strip() or cleaned
    return EmploymentEntry(
        employer=None,
        title=title,
        start_date=start,
        end_date=end,
        is_current=_is_current(end),
        is_break=True,
    )


_OPEN_WORDS = frozenset(
    {
        "and", "or", "the", "of", "to", "for", "with", "in", "across", "a", "an", "by", "on",
        "at", "as", "from", "into", "over", "per", "than", "while", "&",
    }
)


def _ends_open(text: str) -> bool:
    """Whether a bullet stops where a sentence cannot: on a comma, a dash, "&" or a connector."""

    trimmed = text.rstrip()
    if trimmed.endswith((",", "-", "&", "/", "(", ";")):
        return True
    words = trimmed.split()
    return bool(words) and words[-1].strip(".,;:()").lower() in _OPEN_WORDS


def _short_plain_line(line: str | None) -> bool:
    """A short line with no dates, separators or bullet: a possible part of a stacked header."""

    if line is None or _BULLET.match(line):
        return False
    plain = _strip_markdown(line.strip())
    return (
        0 < len(plain) <= 60
        and len(plain.split()) <= 8
        and not plain.endswith((".", "!", "?", ":", ";"))
        and not _DATE_RANGE.search(plain)
        and not _ROLE_SEP.search(plain)
    )


def _join_wrapped(previous: str, continuation: str) -> str:
    if previous.endswith("-") and not previous.endswith(" -"):
        return previous + continuation.strip()
    return previous.rstrip() + " " + continuation.strip()


def parse_experience(section: ResumeSection) -> tuple[list[EmploymentEntry], list[str]]:
    entries: list[EmploymentEntry] = []
    unparsed: list[str] = []
    current: EmploymentEntry | None = None
    in_bullet = False  # the previous line was a bullet, or a wrapped part of one
    lines = [line for line in section.lines if line.strip()]
    position = 0
    while position < len(lines):
        line = lines[position]
        position += 1
        stripped = line.strip()
        following = lines[position] if position < len(lines) else None

        bullet = _BULLET.match(line)
        if bullet:
            if current is None or current.is_break:
                unparsed.append(stripped)
                in_bullet = False
                continue
            current.accomplishments.append(bullet.group(1).strip())
            in_bullet = True
            continue

        plain = _strip_markdown(stripped.lstrip("-*• ").strip())

        # A note about time away from work is an employment entry, never dropped.
        if _is_break_note(plain, in_experience=True):
            current = _break_entry(plain)
            entries.append(current)
            in_bullet = False
            continue

        has_dates = bool(_DATE_RANGE.search(plain))
        has_sep = bool(_ROLE_SEP.search(plain))
        next_dates = None if has_dates else _date_line_parts(following)

        # Stacked header: employer and title on two short lines, then "Location • dates".
        after_following = lines[position + 1] if position + 1 < len(lines) else None
        if (
            not has_dates
            and not has_sep
            and _short_plain_line(stripped)
            and _short_plain_line(following)
            and _date_line_parts(after_following) is not None
        ):
            title, employer = _order_title_employer(plain, _strip_markdown(following.strip()))
            start, end, location = _date_line_parts(after_following)  # type: ignore[misc]
            current = EmploymentEntry(
                employer=employer,
                title=title,
                start_date=start,
                end_date=end,
                is_current=_is_current(end),
                location=location,
            )
            entries.append(current)
            position += 2
            in_bullet = False
            continue

        # A wrapped bullet: join it to the bullet above unless it is plainly a new header.
        if in_bullet and current is not None and current.accomplishments:
            previous = current.accomplishments[-1]
            unfinished = not previous.rstrip().endswith((".", "!", "?", ":"))
            lowercase_start = plain[:1].islower()
            header_like = (
                has_dates
                or (has_sep and next_dates is not None)
                or (has_sep and len(plain.split()) <= 8)
            ) and not lowercase_start
            # Join only when the bullet plainly continues: the next line starts lowercase,
            # the bullet stops on a connector or comma, or it is long enough to have wrapped.
            # A short complete-looking bullet followed by a capitalised line is a new thing.
            wraps = (
                lowercase_start
                or _ends_open(previous)
                or (unfinished and len(previous) >= 60)
            )
            if wraps and not header_like:
                current.accomplishments[-1] = _join_wrapped(previous, stripped)
                continue

        in_bullet = False

        # Two-line header: "Employer - Title" (either order), then "Location • dates".
        if (
            (has_sep or re.search(r"\s-\s", plain))
            and not has_dates
            and next_dates is not None
        ):
            parts = _header_parts(plain, hyphen=True)
            if len(parts) >= 2:
                title, employer = _order_title_employer(parts[0], parts[1])
                start, end, location = next_dates
                current = EmploymentEntry(
                    employer=employer,
                    title=title,
                    start_date=start,
                    end_date=end,
                    is_current=_is_current(end),
                    location=location,
                    summary=" | ".join(parts[2:]) or None,
                )
                entries.append(current)
                position += 1
                continue

        # A plain sentence straight under a role header, before any bullet, describes the
        # role. (After bullets it is not a wrap, so it goes to the unread list below.)
        if (
            not has_dates
            and current is not None
            and not current.is_break
            and not current.summary
            and not current.accomplishments
            and (plain.endswith(".") or (len(plain.split()) > 8 and not has_sep))
        ):
            current.summary = stripped
            continue

        # A non-bullet line with dates or a separator starts a new role.
        if has_dates or has_sep:
            title, employer, start, end = _split_title_employer_dates(stripped)
            current = EmploymentEntry(
                employer=employer,
                title=title,
                start_date=start,
                end_date=end,
                is_current=_is_current(end),
            )
            entries.append(current)
            continue
        if (
            current is not None
            and not current.is_break
            and not current.summary
            and not current.accomplishments
        ):
            current.summary = stripped
            continue
        unparsed.append(stripped)
    return entries, unparsed


def _parse_education_line(text: str) -> dict[str, Any] | None:
    """Classify one education line into degree / institution / year / minors / honors.

    Returns None when nothing on the line is understood. Segments are separated by
    commas, bullets, pipes, semicolons and spaced em dashes, but never by an en dash or
    anything inside parentheses, so "Communications – Public Relations" stays whole.
    """

    plain = _strip_markdown(text.strip())
    parsed: dict[str, Any] = {
        "degree": None,
        "institution": None,
        "start": None,
        "end": None,
        "minors": [],
        "honors": [],
        "others": [],
    }
    range_match = _DATE_RANGE.search(plain)
    if range_match:
        parsed["start"] = range_match.group(1).strip()
        parsed["end"] = range_match.group(2).strip()
        before = plain[: range_match.start()].rstrip(" (")
        after = plain[range_match.end() :].lstrip(" )")
        plain = f"{before} {after}".strip()
    # "(2006)" after an institution is a graduation year, not part of the name.
    plain = re.sub(
        rf"\s*\(\s*((?:{_MONTH}\s+)?\d{{4}})\s*\)", r"|\1", plain, flags=re.IGNORECASE
    )
    segments = _split_top_level(plain.replace(" — ", "|"), "|•·;,")
    for segment in segments:
        trimmed = segment.strip(" .")
        minor = _MINOR.match(trimmed)
        year = _GRAD_YEAR.match(trimmed)
        if minor:
            parsed["minors"].extend(
                m.strip() for m in re.split(r"\s*(?:,|;|\band\b|&)\s*", minor.group(1)) if m.strip()
            )
        elif year:
            parsed["end"] = parsed["end"] or year.group(1)
        elif _DEGREE.search(trimmed) and parsed["degree"] is None:
            parsed["degree"] = trimmed
        elif _INSTITUTION_WORD.search(trimmed) and parsed["institution"] is None:
            parsed["institution"] = trimmed
        elif _HONORS.search(trimmed):
            parsed["honors"].append(trimmed)
        else:
            parsed["others"].append(trimmed)
    if parsed["degree"] and parsed["institution"] is None and parsed["others"]:
        parsed["institution"] = parsed["others"].pop(0)
    understood = (
        parsed["degree"]
        or parsed["institution"]
        or parsed["minors"]
        or parsed["honors"]
        or parsed["end"]
    )
    return parsed if understood else None


def parse_education(section: ResumeSection) -> tuple[list[EducationEntry], list[str]]:
    entries: list[EducationEntry] = []
    unparsed: list[str] = []
    current: EducationEntry | None = None
    for line in section.lines:
        text = _BULLET.sub(r"\1", line).strip()
        if not text:
            continue
        parsed = _parse_education_line(text)
        if parsed is None:
            unparsed.append(text)
            continue
        unparsed.extend(parsed["others"])
        degree, institution = parsed["degree"], parsed["institution"]
        if degree is None and institution is None:
            # A year, minors or honors line belongs to the entry above it.
            if current is None:
                unparsed.append(text)
                continue
            current.minors.extend(parsed["minors"])
            current.honors.extend(parsed["honors"])
            if parsed["end"]:
                if current.end_date is None:
                    current.end_date = parsed["end"]
                else:
                    unparsed.append(text)
            continue
        if (
            current is not None
            and degree is not None
            and institution is None
            and current.degree is None
        ):
            current.degree = degree  # the degree line for the institution just above
            target = current
        elif (
            current is not None
            and institution is not None
            and degree is None
            and current.institution is None
        ):
            current.institution = institution  # the institution line for the degree above
            target = current
        else:
            target = EducationEntry(institution=institution, degree=degree)
            entries.append(target)
            current = target
        if parsed["start"] and target.start_date is None:
            target.start_date = parsed["start"]
        if parsed["end"] and target.end_date is None:
            target.end_date = parsed["end"]
        target.minors.extend(parsed["minors"])
        target.honors.extend(parsed["honors"])
    return entries, unparsed


def _skill_items(text: str, *, split_and: bool) -> list[str]:
    items: list[str] = []
    for chunk in _split_top_level(text, ",;|•●·▪"):
        chunk = re.sub(r"^(?:and|&)\s+", "", chunk.strip(), flags=re.IGNORECASE)
        pieces = re.split(r"\s+and\s+", chunk) if split_and else [chunk]
        items.extend(p.strip(" .") for p in pieces if p.strip(" ."))
    return items


def parse_skills(section: ResumeSection) -> tuple[list[SkillEntry], list[str]]:
    skills: list[SkillEntry] = []
    unparsed: list[str] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, category: str | None = None) -> None:
        key = (name.lower(), (category or "").lower())
        if key not in seen:
            seen.add(key)
            skills.append(SkillEntry(name=name, category=category))

    for line in section.lines:
        stripped = _BULLET.sub(r"\1", line).strip()
        if not stripped:
            continue
        category: str | None = None
        body = stripped
        head, colon, rest = stripped.partition(":")
        if colon and rest.strip() and len(head) <= 40 and len(head.split()) <= 4:
            category, body = head.strip(), rest.strip()
        lead = _LEADING_SKILL_PREFIX.match(body)
        if lead:
            body = body[lead.end() :]
        items = _skill_items(body, split_and=bool(lead))
        if category:
            items = [
                part.strip()
                for item in items
                for part in (item.split("/") if "/" in item and "(" not in item else [item])
                if part.strip()
            ]
        if len(items) == 1 and len(items[0]) >= 60:
            unparsed.append(stripped)
            continue
        for name in items:
            if len(name) < 60:
                add(name, category)
            else:
                unparsed.append(name)
    return skills, unparsed


def parse_certifications(section: ResumeSection) -> tuple[list[CertificationEntry], list[str]]:
    entries: list[CertificationEntry] = []
    unparsed: list[str] = []
    for line in section.lines:
        stripped = _BULLET.sub(r"\1", line).strip()
        if not stripped:
            continue
        if len(stripped) > 120:
            unparsed.append(stripped)
            continue
        parts = [p.strip() for p in re.split(r"\s*[|–—]\s*|\s+-\s+", stripped) if p.strip()]
        entries.append(
            CertificationEntry(
                name=parts[0],
                issuer=parts[1] if len(parts) > 1 else None,
                issued=parts[2] if len(parts) > 2 else None,
            )
        )
    return entries, unparsed


def parse_projects(section: ResumeSection) -> tuple[list[ProjectEntry], list[str]]:
    entries: list[ProjectEntry] = []
    unparsed: list[str] = []
    current: ProjectEntry | None = None
    for line in section.lines:
        stripped = line.strip()
        if not stripped:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            if current is None:
                unparsed.append(stripped)
            else:
                current.outcomes.append(bullet.group(1).strip())
            continue
        name = stripped
        url = None
        match = _URL.search(stripped)
        if match:
            url = match.group(0)
            name = (stripped[: match.start()] + stripped[match.end() :]).strip(" |,-–—")
        current = ProjectEntry(name=name or stripped, url=url)
        entries.append(current)
    return entries, unparsed


def parse_master_resume(text: str, *, parsed: dict[str, Any] | None = None) -> ResumeDocument:
    """Parse a master résumé.

    `parsed` allows a caller to supply a pre-parsed structure (for example from a
    DOCX/PDF extractor added later); the document is then trusted as-is rather than
    re-derived from text.
    """

    document = ResumeDocument(raw_text=text or "")
    if parsed:
        document.contact = ContactInfo.model_validate(parsed.get("contact") or {})
        document.headline = parsed.get("headline")
        document.summary = parsed.get("summary")
        document.employment = [
            EmploymentEntry.model_validate(e) for e in parsed.get("employment") or []
        ]
        document.education = [
            EducationEntry.model_validate(e) for e in parsed.get("education") or []
        ]
        document.certifications = [
            CertificationEntry.model_validate(c) for c in parsed.get("certifications") or []
        ]
        document.skills = [SkillEntry.model_validate(s) for s in parsed.get("skills") or []]
        document.projects = [ProjectEntry.model_validate(p) for p in parsed.get("projects") or []]
        return document

    if not text or not text.strip():
        document.warnings.append("résumé text is empty")
        return document

    sections, preamble = split_resume_sections(text)
    document.sections = sections
    if not sections:
        document.warnings.append("no recognized section headings found in the résumé")

    contact, headline, contact_unparsed = parse_contact(preamble, text)
    document.contact = contact
    document.headline = headline
    document.unparsed_lines.extend(contact_unparsed)

    stray_breaks: list[EmploymentEntry] = []
    for section in sections:
        if section.name != "experience":
            # A note about time away from work can sit under any heading; pull it out
            # before it is misread as a skill, a school or a summary sentence.
            kept: list[str] = []
            for raw_line in section.lines:
                plain = _strip_markdown(raw_line.strip())
                if (
                    plain
                    and not _BULLET.match(raw_line)
                    and _is_break_note(plain, in_experience=False)
                ):
                    stray_breaks.append(_break_entry(plain))
                else:
                    kept.append(raw_line)
            if len(kept) != len(section.lines):
                section = ResumeSection(name=section.name, heading=section.heading, lines=kept)
        if section.name == "summary":
            document.summary = section.text()
        elif section.name == "experience":
            entries, unparsed = parse_experience(section)
            document.employment = entries
            document.unparsed_lines.extend(unparsed)
        elif section.name == "education":
            entries, unparsed = parse_education(section)
            document.education = entries
            document.unparsed_lines.extend(unparsed)
        elif section.name == "skills":
            entries, unparsed = parse_skills(section)
            document.skills = entries
            document.unparsed_lines.extend(unparsed)
        elif section.name == "certifications":
            entries, unparsed = parse_certifications(section)
            document.certifications = entries
            document.unparsed_lines.extend(unparsed)
        elif section.name == "projects":
            entries, unparsed = parse_projects(section)
            document.projects = entries
            document.unparsed_lines.extend(unparsed)

    for stray in stray_breaks:
        _insert_chronologically(document.employment, stray)

    if not any(not e.is_break for e in document.employment):
        document.warnings.append("no employment entries could be parsed")
    if not document.skills:
        document.warnings.append("no skills could be parsed")
    if not document.contact.email:
        document.warnings.append("no email address found")
    document.unparsed_lines = [line for line in document.unparsed_lines if line.strip()]
    for entry in document.employment:
        label = entry.title or entry.employer or "an unnamed entry"
        if not entry.is_break and not entry.employer:
            document.warnings.append(f"Employment entry '{label}' has no employer")
        if (
            not entry.is_break
            and entry.title
            and entry.employer
            and _title_score(entry.title) == 0
            and _title_score(entry.employer) == 0
        ):
            document.warnings.append(
                f"title/employer order guessed for '{entry.title} / {entry.employer}'"
            )
        if not (entry.start_date or entry.end_date):
            document.warnings.append(f"Employment entry '{label}' has no dates")
    if document.unparsed_lines:
        count = len(document.unparsed_lines)
        document.warnings.append(
            f"{count} line{'s' if count != 1 else ''} of the résumé "
            f"{'were' if count != 1 else 'was'} not understood"
        )
    return document


def _insert_chronologically(entries: list[EmploymentEntry], entry: EmploymentEntry) -> None:
    """Place a stray career break where its dates fall in a newest-first employment list."""

    def year(value: str | None) -> int | None:
        found = re.search(r"(\d{4})", str(value or ""))
        return int(found.group(1)) if found else None

    mine = year(entry.start_date)
    if mine is not None:
        for index, other in enumerate(entries):
            theirs = year(other.start_date)
            if theirs is not None and theirs <= mine:
                entries.insert(index, entry)
                return
    entries.append(entry)


def render_markdown(document: ResumeDocument) -> str:
    """Render a structured résumé back to markdown.

    Used for master-résumé round-tripping and for displaying parsed truth in the UI;
    tailored résumés are rendered by `sabra_documents.tailor`, which attaches the
    provenance ledger.
    """

    lines: list[str] = []
    contact = document.contact
    if contact.full_name:
        lines.append(f"# {contact.full_name}")
    headline = document.headline
    if headline:
        lines.append(f"_{headline}_")
    contact_bits = [
        part
        for part in (
            contact.email,
            contact.phone,
            ", ".join(p for p in (contact.city, contact.state) if p) or None,
            contact.linkedin_url,
            contact.website_url,
        )
        if part
    ]
    if contact_bits:
        lines.append(" | ".join(contact_bits))

    def add_heading(title: str) -> None:
        lines.append("")
        lines.append(f"## {title}")

    if document.summary:
        add_heading("Summary")
        lines.append(document.summary.strip())
    if document.employment:
        add_heading("Experience")
        for entry in document.employment:
            header = " — ".join(p for p in (entry.title, entry.employer) if p)
            dates = " - ".join(p for p in (entry.start_date, entry.end_date) if p)
            lines.append("")
            lines.append(f"**{header}**" + (f" | {dates}" if dates else ""))
            if entry.location:
                lines.append(f"_{entry.location}_")
            if entry.summary:
                lines.append(entry.summary)
            for item in entry.accomplishments:
                lines.append(f"- {item}")
    if document.education:
        add_heading("Education")
        for entry in document.education:
            line = " — ".join(p for p in (entry.degree, entry.institution) if p)
            dates = " - ".join(p for p in (entry.start_date, entry.end_date) if p)
            minors = "".join(f"; Minor in {m}" for m in entry.minors)
            lines.append(f"- {line}" + (f" ({dates})" if dates else "") + minors)
    if document.certifications:
        add_heading("Certifications")
        for entry in document.certifications:
            line = " — ".join(p for p in (entry.name, entry.issuer) if p)
            lines.append(f"- {line}")
    if document.skills:
        add_heading("Skills")
        grouped: dict[str, list[str]] = {}
        for skill in document.skills:
            grouped.setdefault(skill.category or "", []).append(skill.name)
        for category, names in grouped.items():
            prefix = f"**{category}:** " if category else ""
            lines.append(f"- {prefix}{', '.join(names)}")
    if document.projects:
        add_heading("Projects")
        for project in document.projects:
            lines.append(f"- **{project.name}**" + (f" ({project.url})" if project.url else ""))
            for outcome in project.outcomes:
                lines.append(f"  - {outcome}")
    return "\n".join(lines).strip() + "\n"


def years_of_experience(document: ResumeDocument, *, today: date | None = None) -> float | None:
    """Total professional years, computed only from parsed date ranges.

    Dates arrive in several shapes ("Mar 2019", "2019", "03/2019"), so the year is
    extracted from anywhere in the string rather than from its first four characters.
    """

    reference_year = (today or date.today()).year

    def year_of(value: str | None) -> int | None:
        if not value:
            return None
        match = re.search(r"(\d{4})", str(value))
        return int(match.group(1)) if match else None

    total = 0.0
    found = False
    for entry in document.employment:
        if entry.is_break:
            continue  # time away from work is not professional experience
        start_year = year_of(entry.start_date)
        if start_year is None:
            continue
        end_text = str(entry.end_date or "").lower()
        if not end_text or end_text in {"present", "current", "now"}:
            end_year = reference_year
        else:
            end_year = year_of(entry.end_date) or reference_year
        total += max(0.0, end_year - start_year)
        found = True
    return total if found else None
