"""Render the package's Markdown to a PDF an employer's system can read.

The tailored résumé is stored as Markdown because that is what the owner reviews and
what the approval hash covers. An employer should never see `#` or `**`, so at submit
time the approved Markdown is rendered to a clean single-column PDF. Rendering from the
approved text means the uploaded file is bound to the approval by construction.

Properties that matter:

* Standard PDF fonts only (Helvetica), so there is nothing to embed and nothing to
  download. Characters outside WinAnsi (cp1252) are mapped to a safe equivalent or `?`;
  they never crash the render and never produce an undefined glyph.
* Deterministic: the creation date and document id are fixed (reportlab's `invariant`
  mode), so the same Markdown always yields identical bytes.
* Handles what the tailor emits: `#` / `##` headings, `**bold**` role lines, `_italic_`
  lines, `-` bullets (one nested level) and plain paragraphs.
* The résumé and the cover letter share one look: the same letterhead (name, headline,
  contact line, accent rule), the same palette and the same margins. Both are single
  column with no tables or images, so an applicant-tracking system reads them in order.
  The résumé is laid out as a résumé (section rules, "Title, Employer" role lines with
  the dates beneath); the cover letter as a left-aligned business letter.

`reportlab` is in the `automation` extra and is imported only when a PDF is rendered.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

# Characters WinAnsi lacks that have an obvious plain equivalent. Anything else that is
# not encodable is decomposed (accents dropped) or replaced with "?".
_SAFE_MAP = {
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    "​": "",
    "‌": "",
    "‍": "",
    "﻿": "",
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "―": "-",
    "−": "-",
    "→": "->",
    "←": "<-",
    "⇒": "=>",
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    "≈": "~",
    "✓": "x",
    "✔": "x",
    "●": "•",
    "▪": "•",
    "◦": "•",
    "‣": "•",
    "⁃": "-",
    "­": "",
}

# Letters with no decomposition (NFKD cannot strip a mark) that have an obvious plain
# form. Letters WinAnsi already has (ß, œ, Œ, æ, ø, ð, þ and the accented vowels) are kept
# as they are, because the font draws them correctly.
_SAFE_MAP.update(
    {
        "\u0142": "l",  # ł
        "\u0141": "L",  # Ł
        "\u0111": "d",  # đ
        "\u0110": "D",  # Đ
        "\u0131": "i",  # ı
        "\u0140": "l",  # ŀ
        "\u013f": "L",  # Ŀ
        "\u0127": "h",  # ħ
        "\u0126": "H",  # Ħ
        "\u0167": "t",  # ŧ
        "\u0166": "T",  # Ŧ
    }
)

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^(\s*)[-*•]\s+(.*\S)\s*$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])_(?!\s)(.+?)(?<!\s)_(?![\w*])")


class UnrenderableText(ValueError):
    """A name or contact line has a character that cannot be drawn faithfully.

    Raised rather than printing a mangled name: the caller turns it into a blocked
    application and nothing is uploaded.
    """


def _map_char(char: str) -> tuple[str, bool]:
    """`(replacement, unmapped)` for one character. `unmapped` means it became `?`."""

    if char in _SAFE_MAP:
        return _SAFE_MAP[char], False
    try:
        char.encode("cp1252")
    except UnicodeEncodeError:
        base = unicodedata.normalize("NFKD", char)
        base = "".join(c for c in base if not unicodedata.combining(c))
        try:
            base.encode("cp1252")
        except UnicodeEncodeError:
            return "?", True
        return base, False
    return char, False


def _winansi(text: str) -> tuple[str, int]:
    out: list[str] = []
    unmapped = 0
    for char in unicodedata.normalize("NFC", text):
        mapped, lost = _map_char(char)
        out.append(mapped)
        unmapped += int(lost)
    # Control characters other than newline/tab have no glyph.
    return "".join(c for c in "".join(out) if c in "\n\t" or ord(c) >= 32), unmapped


def winansi_safe(text: str) -> str:
    """`text` with every character mapped into WinAnsi (cp1252), never raising."""

    return _winansi(text)[0]


def count_unmapped(markdown: str) -> int:
    """How many characters in `markdown` had no safe mapping and print as `?`."""

    return _winansi(markdown or "")[1]


def renderer_version() -> str:
    import reportlab

    return f"reportlab {reportlab.Version}"


@dataclass(slots=True)
class _Block:
    kind: str  # title | heading | italic | bullet | bullet2 | text | space
    text: str = ""


def _require_faithful(kind: str, text: str) -> None:
    lost = count_unmapped(text)
    if lost:
        raise UnrenderableText(
            f"the {kind} line {text!r} has {lost} character(s) that the PDF's standard fonts "
            "cannot draw and that have no plain equivalent; the PDF would print them as '?'"
        )


def _inline(text: str) -> str:
    """Escape for reportlab's mini-markup, then turn `**x**` and `_x_` into tags."""

    safe = escape(winansi_safe(text))
    safe = _BOLD.sub(r"<b>\1</b>", safe)
    safe = _ITALIC.sub(r"<i>\1</i>", safe)
    return safe.replace("**", "")


def _blocks(markdown: str) -> list[_Block]:
    blocks: list[_Block] = []
    seen_title = False
    for raw in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            blocks.append(_Block("space"))
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if level == 1 and not seen_title:
                seen_title = True
                blocks.append(_Block("title", heading.group(2)))
            else:
                blocks.append(_Block("heading", heading.group(2)))
            continue
        bullet = _BULLET.match(line)
        if bullet:
            nested = len(bullet.group(1).replace("\t", "    ")) >= 2
            blocks.append(_Block("bullet2" if nested else "bullet", bullet.group(2)))
            continue
        stripped = line.strip()
        if re.fullmatch(r"_[^_].*[^_]_", stripped):
            blocks.append(_Block("italic", stripped[1:-1]))
            continue
        blocks.append(_Block("text", stripped))
    return blocks


# ---------------------------------------------------------------------------
# shared look: one palette, one letterhead, one set of margins for both documents
# ---------------------------------------------------------------------------

_INK = "#1f2a2e"
_MUTED = "#5b6a70"
_RULE = "#9fb3ab"
_ACCENT = "#2f5d50"

_SIDE_MARGIN_IN = 0.75
_TOP_MARGIN_IN = 0.6
_BOTTOM_MARGIN_IN = 0.55

_ROLE = re.compile(r"^\*\*(?P<head>[^*].*?)\*\*\s*(?:\|\s*(?P<dates>.*?))?\s*$")
_BOLD_ONLY = re.compile(r"^\*\*(?P<text>[^*].*?)\*\*$")
# The tailor writes a career break as an ordinary `**title** | dates` line (its own flag is
# not carried into the Markdown), so only a line whose title starts "Career break" is styled
# as a break; any other role line is drawn as a role, with or without bullets.
_CAREER_BREAK = re.compile(r"^career\s+break\b", re.IGNORECASE)
_POSTSCRIPT = re.compile(r"^p\.?\s?s\b", re.IGNORECASE)
_GREETING = re.compile(r"^(?:dear|hello|hi|greetings|to whom it may concern)\b", re.IGNORECASE)
_CLOSING = re.compile(
    r"^(?:sincerely|sincerely yours|yours sincerely|yours truly|best regards|kind regards|"
    r"warm regards|warmest regards|regards|best wishes|best|respectfully|"
    r"respectfully yours|cordially|with appreciation|thank you|thanks)\s*[,.!]?$",
    re.IGNORECASE,
)


def _plain(text: str) -> str:
    """Escape caller-supplied text (name, company...) with no Markdown interpretation."""

    return escape(winansi_safe(text or ""))


def _contact_display(line: str) -> str:
    """`a | b | c` as `a • b • c`: a separator a person reads and a parser tolerates."""

    parts = [part for part in re.split(r"\s*\|\s*", line.strip()) if part]
    return " • ".join(parts)


def _split_items(text: str) -> list[str]:
    """Split a skills line on commas, semicolons and bullets that are not inside brackets."""

    items: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and char in ",;•":
            items.append("".join(current))
            current = []
            continue
        current.append(char)
    items.append("".join(current))
    return [item.strip() for item in items if item.strip()]


def _styles(scale: float = 1.0) -> dict[str, Any]:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle

    ink = colors.HexColor(_INK)
    muted = colors.HexColor(_MUTED)
    accent = colors.HexColor(_ACCENT)
    base, bold = "Helvetica", "Helvetica-Bold"

    def size(value: float) -> float:
        return round(value * scale, 2)

    return {
        "name": ParagraphStyle(
            "name", fontName=bold, fontSize=20, leading=24, textColor=ink,
            alignment=TA_CENTER, spaceAfter=2,
        ),
        "headline": ParagraphStyle(
            "headline", fontName=base, fontSize=10, leading=13, textColor=accent,
            alignment=TA_CENTER, spaceAfter=3,
        ),
        "contact": ParagraphStyle(
            "contact", fontName=base, fontSize=9.5, leading=12, textColor=muted,
            alignment=TA_CENTER, spaceAfter=6,
        ),
        "section": ParagraphStyle(
            "section", fontName=bold, fontSize=10.5, leading=13, textColor=accent,
            spaceBefore=7, spaceAfter=1, keepWithNext=1,
        ),
        "body": ParagraphStyle(
            "body", fontName=base, fontSize=9.8, leading=12.8, textColor=ink,
            alignment=TA_LEFT, spaceAfter=2,
        ),
        # A summary paragraph: a little air between paragraphs and before a role's bullets.
        "para": ParagraphStyle(
            "para", fontName=base, fontSize=9.8, leading=12.8, textColor=ink,
            alignment=TA_LEFT, spaceAfter=4,
        ),
        "role": ParagraphStyle(
            "role", fontName=bold, fontSize=10.3, leading=13, textColor=ink,
            spaceBefore=5, keepWithNext=1,
        ),
        "meta": ParagraphStyle(
            "meta", fontName=base, fontSize=9.3, leading=11.5, textColor=muted,
            spaceAfter=1, keepWithNext=1,
        ),
        "bullet": ParagraphStyle(
            "bullet", fontName=base, fontSize=9.8, leading=12.8, textColor=ink,
            leftIndent=13, bulletIndent=2, bulletFontName=base, bulletFontSize=9.8,
            spaceAfter=0.6,
        ),
        "bullet2": ParagraphStyle(
            "bullet2", fontName=base, fontSize=9.8, leading=12.8, textColor=ink,
            leftIndent=26, bulletIndent=15, bulletFontName=base, bulletFontSize=9.8,
            spaceAfter=0.6,
        ),
        "break": ParagraphStyle(
            "break", fontName=base, fontSize=9.8, leading=12.8, textColor=muted, spaceBefore=5,
        ),
        "italic": ParagraphStyle(
            "italic", fontName="Helvetica-Oblique", fontSize=9.8, leading=12.8, textColor=ink,
            spaceAfter=2,
        ),
        # The cover letter: left-aligned business-letter text, a little larger and looser.
        "letter": ParagraphStyle(
            "letter", fontName=base, fontSize=size(10), leading=size(14), textColor=ink,
            alignment=TA_LEFT, spaceAfter=size(10),
        ),
        "letter_tight": ParagraphStyle(
            "letter_tight", fontName=base, fontSize=size(10), leading=size(14), textColor=ink,
            alignment=TA_LEFT, spaceAfter=0,
        ),
        "letter_bullet": ParagraphStyle(
            "letter_bullet", fontName=base, fontSize=size(10), leading=size(14), textColor=ink,
            alignment=TA_LEFT, leftIndent=18, bulletIndent=6, bulletFontName=base,
            bulletFontSize=size(10), spaceAfter=size(3),
        ),
        "letter_bullet2": ParagraphStyle(
            "letter_bullet2", fontName=base, fontSize=size(10), leading=size(14), textColor=ink,
            alignment=TA_LEFT, leftIndent=34, bulletIndent=22, bulletFontName=base,
            bulletFontSize=size(10), spaceAfter=size(3),
        ),
    }


def _rule(*, thickness: float, color: str, space_after: float = 4.0) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import HRFlowable

    rule = HRFlowable(
        width="100%", thickness=thickness, color=colors.HexColor(color), spaceAfter=space_after
    )
    rule.keepWithNext = 1
    return rule


def _letterhead(
    styles: dict[str, Any],
    *,
    name: str,
    headline: str,
    contact_lines: list[str],
) -> list[Any]:
    """The header both documents share: name, headline, contact line, accent rule.

    The arguments are already escaped for reportlab's mini-markup.
    """

    from reportlab.platypus import Paragraph

    items: list[Any] = []
    if name:
        items.append(Paragraph(name, styles["name"]))
    if headline:
        items.append(Paragraph(headline, styles["headline"]))
    for line in contact_lines:
        items.append(Paragraph(line, styles["contact"]))
    if not items:
        return []
    rule = _rule(thickness=1.2, color=_ACCENT, space_after=2)
    rule.keepWithNext = 0
    items.append(rule)
    return items


def _build_pdf(story: list[Any], *, title: str) -> tuple[bytes, int]:
    """`(bytes, page count)`. Fixed creation date and id, so the bytes are repeatable."""

    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=_SIDE_MARGIN_IN * inch,
        rightMargin=_SIDE_MARGIN_IN * inch,
        topMargin=_TOP_MARGIN_IN * inch,
        bottomMargin=_BOTTOM_MARGIN_IN * inch,
        title=winansi_safe(title),
        author="",
        subject="",
        creator="",
        invariant=1,
        pageCompression=0,
    )
    document.build(story)
    return buffer.getvalue(), int(document.page)


@dataclass(slots=True, frozen=True)
class Letterhead:
    """The name, headline and contact line at the top of a résumé's Markdown."""

    name: str = ""
    headline: str = ""
    contact_line: str = ""


def extract_letterhead(markdown: str) -> Letterhead:
    """Read the header of a tailored résumé, so the cover letter can repeat it exactly."""

    name = headline = ""
    contacts: list[str] = []
    for block in _blocks(markdown or ""):
        if block.kind == "heading" or block.kind in {"bullet", "bullet2"}:
            break
        if block.kind == "title" and not name:
            name = _plain_markdown(block.text)
        elif block.kind == "italic" and not headline:
            headline = _plain_markdown(block.text)
        elif block.kind == "text":
            contacts.append(_plain_markdown(block.text))
    return Letterhead(name=name, headline=headline, contact_line=" | ".join(contacts))


def _plain_markdown(text: str) -> str:
    return _BOLD.sub(r"\1", text).replace("**", "").strip()


# ---------------------------------------------------------------------------
# the résumé
# ---------------------------------------------------------------------------


_CITY = r"[A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,3}"
_LOCATION_LINE = re.compile(
    rf"^(?P<place>{_CITY}, [A-Z]{{2}}|Remote)(?:\s*[•·|]\s*(?P<dates>\S.*?))?\s*$"
)


def _location_line(text: str) -> tuple[str, str] | None:
    """`(place, dates)` for a strict `City, ST`, `City, ST • dates` or bare `Remote` line.

    Anything else (a sentence of the role's summary, "Remote-first payments team...") is
    not a location and is drawn as an ordinary paragraph.
    """

    match = _LOCATION_LINE.match(text.strip())
    if match is None:
        return None
    return match.group("place"), (match.group("dates") or "").strip()


def _role_paragraphs(head: str, dates: str, location: str, styles: dict[str, Any]) -> list[Any]:
    from reportlab.platypus import Paragraph

    title_part, _sep, employer_part = head.partition(" — ")
    role = ", ".join(part.strip() for part in (title_part, employer_part) if part.strip())
    items: list[Any] = [Paragraph(_inline(role), styles["role"])]
    meta = " · ".join(bit.strip() for bit in (dates, location) if bit and bit.strip())
    if meta:
        items.append(Paragraph(_inline(meta), styles["meta"]))
    return items


def render_resume_pdf(markdown: str, *, title: str) -> bytes:
    """Render résumé Markdown to PDF bytes. Identical input gives identical bytes."""

    from reportlab.platypus import Paragraph

    styles = _styles()
    blocks = _blocks(markdown or "")
    # The name and the contact lines must come out exactly as written: a mangled name or
    # phone number on a résumé is worse than no upload. Anywhere else a '?' is tolerated
    # (and counted by `count_unmapped`).
    header_zone_check = True
    for block in blocks:
        if block.kind == "heading":
            header_zone_check = False
        if block.kind == "title":
            _require_faithful("name", block.text)
        elif header_zone_check and block.kind == "text":
            _require_faithful("contact", block.text)

    story: list[Any] = []
    header: dict[str, Any] = {"name": "", "headline": "", "contacts": []}
    state = {"header_open": True, "section": ""}
    skills: list[str] = []

    def close_header() -> None:
        if state["header_open"]:
            state["header_open"] = False
            story.extend(
                _letterhead(
                    styles,
                    name=header["name"],
                    headline=header["headline"],
                    contact_lines=header["contacts"],
                )
            )

    def flush_skills() -> None:
        if skills:
            story.append(Paragraph("  •  ".join(_inline(item) for item in skills), styles["body"]))
            skills.clear()

    index = 0
    while index < len(blocks):
        block = blocks[index]
        index += 1
        if block.kind == "space":
            continue
        if block.kind == "heading":
            close_header()
            flush_skills()
            state["section"] = block.text.strip().lower()
            story.append(Paragraph(_inline(block.text.upper()), styles["section"]))
            story.append(_rule(thickness=0.8, color=_RULE))
            continue
        if state["header_open"]:
            if block.kind == "title":
                header["name"] = _inline(block.text)
                continue
            if block.kind == "italic" and not header["headline"]:
                header["headline"] = _inline(block.text)
                continue
            if block.kind == "text":
                header["contacts"].append(_inline(_contact_display(block.text)))
                continue
            close_header()
        section = state["section"]
        if section == "skills" and block.kind in {"text", "bullet", "bullet2"}:
            skills.extend(_split_items(block.text))
            continue
        if block.kind in {"bullet", "bullet2"}:
            if section == "projects" and block.kind == "bullet" and _BOLD_ONLY.match(block.text):
                story.append(Paragraph(_inline(block.text), styles["role"]))
            elif section in {"education", "certifications"}:
                story.append(Paragraph(_inline(block.text), styles["body"]))
            else:
                story.append(
                    Paragraph(_inline(block.text), styles[block.kind], bulletText="•")
                )
            continue
        if block.kind == "text":
            role = _ROLE.match(block.text)
            if role is not None:
                head = role.group("head").strip()
                dates = (role.group("dates") or "").strip()
                following = blocks[index] if index < len(blocks) else None
                if _CAREER_BREAK.match(head) and (
                    following is None or following.kind not in {"bullet", "bullet2"}
                ):
                    line = f"{head} ({dates})" if dates else head
                    story.append(Paragraph(_inline(line), styles["break"]))
                    continue
                location = ""
                if (
                    following is not None
                    and following.kind == "text"
                    and _ROLE.match(following.text) is None
                ):
                    found = _location_line(following.text)
                    if found is not None:
                        location = found[0]
                        dates = dates or found[1]
                        index += 1
                story.extend(_role_paragraphs(head, dates, location, styles))
                continue
            story.append(Paragraph(_inline(block.text), styles["para"]))
            continue
        if block.kind == "italic":
            story.append(Paragraph(_inline(block.text), styles["italic"]))
            continue
        # A second `# ` heading or any other leftover: plain text, never raw markup.
        story.append(Paragraph(_inline(block.text), styles["body"]))
    flush_skills()
    close_header()
    if not story:
        story.append(Paragraph("", styles["body"]))
    return _build_pdf(story, title=title)[0]


# ---------------------------------------------------------------------------
# the cover letter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Letter:
    greeting: str
    items: list[tuple[str, str]]  # ("para" | "bullet" | "bullet2", text)
    closing: str
    after_signature: list[str]


def _same_name(line: str, name: str) -> bool:
    def key(value: str) -> str:
        return re.sub(r"\s+", " ", winansi_safe(_plain_markdown(value))).strip().casefold()

    return bool(name) and key(line) == key(name)


def _find_closing(lines: list[str]) -> int | None:
    """The closing line, searched from the end.

    A closing is a closing-looking line with at most two short lines after it (the
    signature). A body line such as "Thank you." in the middle of the letter is not one.
    """

    for n in range(len(lines) - 1, -1, -1):
        if not _CLOSING.match(_plain_markdown(lines[n].strip())):
            continue
        after = [line.strip() for line in lines[n + 1:] if line.strip()]
        if len(after) <= 2 and all(len(line) <= 60 for line in after):
            return n
    return None


def _parse_letter(markdown: str, *, candidate_name: str) -> _Letter:
    lines = [
        raw.rstrip() for raw in (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    closing_at = _find_closing(lines)
    body_lines = lines if closing_at is None else lines[:closing_at]
    closing = "Sincerely," if closing_at is None else _plain_markdown(lines[closing_at].strip())
    if closing_at is not None and not closing.endswith((",", ".")):
        closing += ","
    trailing = [] if closing_at is None else [line.strip() for line in lines[closing_at + 1 :]]

    # A leading greeting line is the letter's own; otherwise the standard one is used.
    greeting = "Dear Hiring Team,"
    for n, line in enumerate(body_lines):
        if not line.strip():
            continue
        plain = _plain_markdown(line.strip())
        if _GREETING.match(plain):
            greeting = plain
            body_lines = body_lines[n + 1 :]
        break
    # A bare name line at the end with no closing in front of it is the signature again.
    if closing_at is None:
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        if body_lines and _same_name(body_lines[-1], candidate_name):
            body_lines.pop()

    items: list[tuple[str, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            items.append(("para", " ".join(buffer)))
            buffer.clear()

    for line in body_lines:
        if not line.strip():
            flush()
            continue
        bullet = _BULLET.match(line)
        if bullet:
            flush()
            nested = len(bullet.group(1).replace("\t", "    ")) >= 2
            items.append(("bullet2" if nested else "bullet", bullet.group(2)))
            continue
        heading = _HEADING.match(line)
        buffer.append(heading.group(2) if heading else line.strip())
    flush()

    # The signature printed is always the candidate's own name, never a different name
    # the Markdown happened to carry. Anything beyond that first line stays.
    after = [line for line in trailing if line]
    if candidate_name and after and len(after[0]) <= 60 and not _POSTSCRIPT.match(after[0]):
        after = after[1:]
    return _Letter(greeting=greeting, items=items, closing=closing, after_signature=after)


def render_cover_letter_pdf(
    markdown: str,
    *,
    candidate_name: str,
    headline: str,
    contact_line: str,
    company: str,
    role_title: str,
    dated: str | None,
    title: str,
) -> bytes:
    """Render a cover letter to PDF bytes. Identical input gives identical bytes.

    The letterhead matches the résumé's. Below it is a left-aligned business letter: the
    date (only if `dated` is given: the renderer never reads the clock), a recipient block,
    the greeting, the body, the closing and the candidate's name. A greeting or closing
    already in the Markdown is used once, not repeated; the signature is always the
    candidate's own name. The type steps down (to 85 percent at most) if the letter would
    otherwise run past one page; if even that does not fit, the full-size letter runs to
    two pages. A company or role title with a character the fonts cannot draw is omitted.
    """

    from reportlab.platypus import Paragraph, Spacer

    # The name and the contact line are printed exactly as given or not at all.
    _require_faithful("name", candidate_name or "")
    _require_faithful("contact", contact_line or "")
    letter = _parse_letter(markdown or "", candidate_name=candidate_name or "")
    # An employer's name that the standard fonts cannot draw is left out rather than
    # printed as "????"; the caller counts it with `count_unmapped`.
    recipient_parts = [
        part.strip()
        for part in (company, role_title)
        if part and part.strip() and not count_unmapped(part)
    ]

    def story_at(scale: float) -> list[Any]:
        styles = _styles(scale)
        story: list[Any] = _letterhead(
            styles,
            name=_plain(candidate_name),
            headline=_plain(headline),
            contact_lines=[_plain(_contact_display(contact_line))] if contact_line else [],
        )
        story.append(Spacer(1, 12 * scale))
        if dated:
            story.append(Paragraph(_plain(dated), styles["letter"]))
        recipient = ["Hiring Team", *recipient_parts]
        for position, part in enumerate(recipient):
            style = styles["letter_tight"] if position < len(recipient) - 1 else styles["letter"]
            story.append(Paragraph(_plain(part.strip()), style))
        story.append(Paragraph(_plain(letter.greeting), styles["letter"]))
        previous = "para"
        for kind, text in letter.items:
            if previous in {"bullet", "bullet2"} and kind == "para":
                story.append(Spacer(1, 7 * scale))
            if kind == "para":
                story.append(Paragraph(_inline(text), styles["letter"]))
            else:
                story.append(
                    Paragraph(
                        _inline(text),
                        styles["letter_bullet" if kind == "bullet" else "letter_bullet2"],
                        bulletText="•",
                    )
                )
            previous = kind
        if previous in {"bullet", "bullet2"}:
            story.append(Spacer(1, 7 * scale))
        story.append(Paragraph(_plain(letter.closing), styles["letter_tight"]))
        story.append(Spacer(1, 18 * scale))
        if candidate_name:
            story.append(Paragraph(_plain(candidate_name), styles["letter_tight"]))
        for extra in letter.after_signature:
            story.append(Paragraph(_inline(extra), styles["letter_tight"]))
        return story

    full_size, pages = _build_pdf(story_at(1.0), title=title)
    if pages <= 1:
        return full_size
    for scale in (0.95, 0.9, 0.85):
        pdf, pages = _build_pdf(story_at(scale), title=title)
        if pages <= 1:
            return pdf
    # Nothing smaller fits: keep the full-size letter on two pages rather than 8.5 pt type.
    return full_size


__all__ = [
    "Letterhead",
    "UnrenderableText",
    "count_unmapped",
    "extract_letterhead",
    "render_cover_letter_pdf",
    "render_resume_pdf",
    "renderer_version",
    "winansi_safe",
]
