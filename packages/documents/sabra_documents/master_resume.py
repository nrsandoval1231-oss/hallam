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


def parse_contact(preamble: list[str], full_text: str) -> tuple[ContactInfo, str | None, list[str]]:
    """Extract contact details and a likely headline from the preamble.

    Location is searched across the whole preamble with emails, phones and URLs
    masked out first: candidates routinely put "Dallas, TX" on the same line as
    their email, and skipping any line that contains contact details lost it.
    """

    joined = "\n".join(preamble)
    unparsed: list[str] = []
    email = _EMAIL.search(joined) or _EMAIL.search(full_text)
    phone = _PHONE.search(joined) or _PHONE.search(full_text)
    linkedin = _LINKEDIN.search(full_text)
    urls = [u for u in _URL.findall(full_text) if not (linkedin and u in linkedin.group(0))]

    masked = _LINKEDIN.sub(" ", joined)
    masked = _URL.sub(" ", masked)
    masked = _EMAIL.sub(" ", masked)
    masked = _PHONE.sub(" ", masked)
    location: str | None = None
    for candidate_line in masked.split("\n"):
        match = re.search(
            r"\b([A-Z][a-zA-Z .'-]{2,30},\s*(?:[A-Z]{2}|[A-Z][a-z]{3,}))\b", candidate_line
        )
        if match:
            location = match.group(1).strip()
            break
    if location is None:
        match = re.search(r"\b([A-Z][a-zA-Z .'-]{2,30},\s*(?:[A-Z]{2}|[A-Z][a-z]{3,}))\b", masked)
        if match:
            location = match.group(1).strip()

    name: str | None = None
    headline: str | None = None
    for line in preamble:
        stripped = line.strip()
        if not stripped:
            continue
        if _EMAIL.search(stripped) or _PHONE.search(stripped) or _URL.search(stripped):
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
        city=location.split(",")[0].strip() if location else None,
        state=location.split(",")[1].strip() if location and "," in location else None,
    )
    return contact, headline, unparsed


def _split_title_employer_dates(line: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (title, employer, start, end) from an experience heading line."""

    cleaned = _strip_markdown(line.strip().lstrip("-*\u2022 ").strip())
    start = end = None
    match = _DATE_RANGE.search(cleaned)
    if match:
        start = match.group(1).strip()
        end = match.group(2).strip()
        cleaned = (cleaned[: match.start()] + cleaned[match.end() :]).strip(" |,-–—\t")

    parts = [p.strip(" |,-–—\t") for p in re.split(r"\s*(?:\||–|—|@| at )\s*", cleaned)]
    parts = [p for p in parts if p]
    title = parts[0] if parts else None
    employer = parts[1] if len(parts) > 1 else None
    # Some résumés put the employer first ("Acme Medical — Territory Manager").
    if employer and title and _DEGREE.search(employer) and not _DEGREE.search(title):
        title, employer = employer, title
    return title, employer, start, end


def parse_experience(section: ResumeSection) -> tuple[list[EmploymentEntry], list[str]]:
    entries: list[EmploymentEntry] = []
    unparsed: list[str] = []
    current: EmploymentEntry | None = None
    for line in section.lines:
        stripped = line.strip()
        if not stripped:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            if current is None:
                unparsed.append(stripped)
                continue
            current.accomplishments.append(bullet.group(1).strip())
            continue
        # A non-bullet line with dates or a separator starts a new role.
        if _DATE_RANGE.search(stripped) or re.search(r"\s[|–—]\s", stripped):
            title, employer, start, end = _split_title_employer_dates(stripped)
            current = EmploymentEntry(
                employer=employer,
                title=title,
                start_date=start,
                end_date=end,
                is_current=bool(end and end.lower() in {"present", "current", "now"}),
            )
            entries.append(current)
            continue
        if current is not None and not current.summary:
            current.summary = stripped
            continue
        unparsed.append(stripped)
    return entries, unparsed


def parse_education(section: ResumeSection) -> tuple[list[EducationEntry], list[str]]:
    entries: list[EducationEntry] = []
    unparsed: list[str] = []
    for line in section.lines:
        stripped = _BULLET.sub(r"\1", line).strip()
        if not stripped:
            continue
        if not _DEGREE.search(stripped):
            unparsed.append(stripped)
            continue
        parts = [p.strip() for p in re.split(r"\s*[|,–—]\s*", stripped) if p.strip()]
        degree = next((p for p in parts if _DEGREE.search(p)), None)
        institution = next((p for p in parts if p is not degree), None)
        dates = _DATE_RANGE.search(stripped)
        entries.append(
            EducationEntry(
                institution=institution,
                degree=degree,
                start_date=dates.group(1) if dates else None,
                end_date=dates.group(2) if dates else None,
            )
        )
    return entries, unparsed


def parse_skills(section: ResumeSection) -> tuple[list[SkillEntry], list[str]]:
    skills: list[SkillEntry] = []
    unparsed: list[str] = []
    for line in section.lines:
        stripped = _BULLET.sub(r"\1", line).strip()
        if not stripped:
            continue
        if ":" in stripped and len(stripped) > 40:
            category, values = stripped.split(":", 1)
            for value in re.split(r"[,;|/]", values):
                name = value.strip()
                if name:
                    skills.append(SkillEntry(name=name, category=category.strip()))
            continue
        if re.search(r"[,;|]", stripped):
            for value in re.split(r"[,;|]", stripped):
                name = value.strip(" .")
                if name and len(name) < 60:
                    skills.append(SkillEntry(name=name))
            continue
        if len(stripped) < 60:
            skills.append(SkillEntry(name=stripped.strip(" .")))
        else:
            unparsed.append(stripped)
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

    for section in sections:
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

    if not document.employment:
        document.warnings.append("no employment entries could be parsed")
    if not document.skills:
        document.warnings.append("no skills could be parsed")
    if not document.contact.email:
        document.warnings.append("no email address found")
    document.unparsed_lines = [line for line in document.unparsed_lines if line.strip()]
    return document


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
            lines.append(f"- {line}" + (f" ({dates})" if dates else ""))
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
