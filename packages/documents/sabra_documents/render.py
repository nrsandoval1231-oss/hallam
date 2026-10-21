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

`reportlab` is in the `automation` extra and is imported only when a PDF is rendered.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
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


def render_resume_pdf(markdown: str, *, title: str) -> bytes:
    """Render résumé Markdown to PDF bytes. Identical input gives identical bytes."""

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    base = ParagraphStyle(
        "base", fontName="Helvetica", fontSize=10, leading=13, textColor=colors.black
    )
    styles = {
        "title": ParagraphStyle(
            "title", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22,
            alignment=TA_CENTER, spaceAfter=2,
        ),
        "italic": ParagraphStyle(
            "italic", parent=base, fontName="Helvetica-Oblique", alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "heading": ParagraphStyle(
            "heading", parent=base, fontName="Helvetica-Bold", fontSize=11.5, leading=14,
            spaceBefore=10, spaceAfter=2,
        ),
        "text": ParagraphStyle("text", parent=base, spaceAfter=3),
        "contact": ParagraphStyle("contact", parent=base, alignment=TA_CENTER, spaceAfter=2),
        "bullet": ParagraphStyle(
            "bullet", parent=base, leftIndent=16, bulletIndent=5, spaceAfter=2
        ),
        "bullet2": ParagraphStyle(
            "bullet2", parent=base, leftIndent=30, bulletIndent=19, spaceAfter=2
        ),
    }

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
    story: list = []
    previous = "space"
    header_zone = True  # name, headline and contact lines above the first section
    for block in blocks:
        if block.kind == "space":
            previous = "space"
            continue
        if block.kind == "title":
            story.append(Paragraph(_inline(block.text), styles["title"]))
        elif block.kind == "heading":
            header_zone = False
            story.append(Paragraph(_inline(block.text.upper()), styles["heading"]))
            story.append(
                HRFlowable(width="100%", thickness=0.6, color=colors.black, spaceAfter=4)
            )
        elif block.kind in {"bullet", "bullet2"}:
            story.append(
                Paragraph(_inline(block.text), styles[block.kind], bulletText="•")
            )
        elif block.kind == "italic":
            story.append(Paragraph(_inline(block.text), styles["italic"]))
        else:
            if previous == "space" and story and not header_zone:
                story.append(Spacer(1, 3))
            story.append(Paragraph(_inline(block.text), styles["contact" if header_zone else "text"]))
        previous = block.kind
    if not story:
        story.append(Paragraph("", styles["text"]))

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=winansi_safe(title),
        author="",
        subject="",
        creator="",
        invariant=1,
        pageCompression=0,
    )
    document.build(story)
    return buffer.getvalue()


__all__ = [
    "UnrenderableText",
    "count_unmapped",
    "render_resume_pdf",
    "renderer_version",
    "winansi_safe",
]
