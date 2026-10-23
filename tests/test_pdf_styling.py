"""The résumé and the cover letter render in one professional, ATS-safe style."""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from conftest_app import build_job
from fake_pages import FakeFormPage, answerable_capture
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_documents.render import (
    UnrenderableText,
    extract_letterhead,
    render_cover_letter_pdf,
    render_resume_pdf,
)
from sabra_domain.schemas import ApplicationPackageSchema
from sabra_engine.execution import ExecutionService

NAME = "JORDAN A. RIVERA"
HEADLINE = "Medical Aesthetics & Device Sales Leader"
CONTACT = "jordan.rivera@example.test | (214) 555-0142 | Dallas, TX"
LEFT_MARGIN = 60.0  # 0.75 in margin plus reportlab's 6 pt frame padding
PAGE_CENTER = 306.0  # US Letter is 612 pt wide

RESUME = f"""# {NAME}
_{HEADLINE}_
{CONTACT}

## Summary
Commercial leader with **11 years** of field sales experience in medical aesthetics.

## Experience

**Regional Sales Manager — Meridian Aesthetics** | Mar 2019 - Present
Dallas, TX
- Grew regional revenue 43% across a 6-state region.
- Built and led a team of 7 managers.

**Territory Representative — Northwind Medical** | Jun 2014 - Feb 2019
- Opened 30 new accounts in the first year.

**Career break: caring for a family member** | Mar 2012 - May 2014

## Education
- B.B.A. Marketing — University of Texas at Dallas (2008 - 2012)

## Certifications
- Certified Medical Representative — CMR Institute

## Skills
Medical device sales, Aesthetic injectables, Account planning (multi-site, regional)

## Projects
- **Regional launch playbook**
  - Wrote the launch plan used by 12 reps.
"""

LETTER = """Dear Hiring Team,

I am applying for the Regional Sales Manager position at Revance Therapeutics (Dallas, TX).

Commercial leader with **11 years** of field sales experience in medical aesthetics, building teams that grow.

Selected results from my experience:
- Grew regional revenue 43% across a 6-state region.
- Built and led a team of 7 managers.

I would welcome the opportunity to discuss how my experience fits the role.

Sincerely,
JORDAN A. RIVERA"""


def _letter(markdown: str = LETTER, **overrides) -> bytes:
    args = dict(
        candidate_name=NAME,
        headline=HEADLINE,
        contact_line=CONTACT,
        company="Revance Therapeutics",
        role_title="Regional Sales Manager - Medical Aesthetics",
        dated="October 23, 2026",
        title="Jordan Rivera - Cover Letter",
    )
    args.update(overrides)
    return render_cover_letter_pdf(markdown, **args)


def _resume(markdown: str = RESUME) -> bytes:
    return render_resume_pdf(markdown, title="Jordan Rivera - Resume")


def _document(pdf: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=pdf, filetype="pdf")


def _text(pdf: bytes) -> str:
    return "\n".join(page.get_text() for page in _document(pdf))


def _lines(pdf: bytes, page: int = 0) -> list[dict]:
    """Text lines in reading order: text, bbox, fonts."""

    out: list[dict] = []
    for block in _document(pdf)[page].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                out.append(
                    {
                        "text": text,
                        "bbox": tuple(line["bbox"]),
                        "fonts": {span["font"] for span in line["spans"]},
                    }
                )
    return out


def _long_resume(roles: int) -> str:
    parts = [f"# {NAME}", f"_{HEADLINE}_", CONTACT, "", "## Experience"]
    for n in range(roles):
        parts += [
            "",
            f"**Role {n} — Employer {n}** | Jan {2000 + n} - Dec {2000 + n}",
            "Dallas, TX",
            "- Grew regional revenue and built a team that outperformed every plan.",
            "- Opened new accounts and trained the field force on the full portfolio.",
            "- Ran quarterly business reviews with the largest customers in the region.",
        ]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# both documents: bytes, determinism, no markup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("make", [_resume, _letter], ids=["resume", "cover_letter"])
def test_a_pdf_is_produced_and_is_byte_identical_across_calls(make) -> None:
    first, second = make(), make()
    assert first.startswith(b"%PDF")
    assert first == second, "the same approved markdown must always yield identical bytes"


def test_a_change_to_the_content_changes_the_bytes() -> None:
    assert _resume(RESUME + "\n- one more\n") != _resume()
    assert _letter(dated="October 24, 2026") != _letter()
    assert _letter(company="Other Co") != _letter()


@pytest.mark.parametrize("make", [_resume, _letter], ids=["resume", "cover_letter"])
def test_no_markdown_markup_leaks_into_the_text(make) -> None:
    text = make() and _text(make())
    assert "**" not in text
    assert not re.search(r"(^|\n)#+ ", text), text
    assert "# " not in text
    assert "_" not in text, "italic markers must not leak"
    assert "<b>" not in text and "&amp;" not in text


# ---------------------------------------------------------------------------
# the shared letterhead
# ---------------------------------------------------------------------------


def test_both_documents_open_with_the_same_centered_letterhead() -> None:
    resume, letter = _lines(_resume()), _lines(_letter())
    for lines in (resume, letter):
        assert [line["text"] for line in lines[:3]] == [
            NAME,
            HEADLINE,
            "jordan.rivera@example.test • (214) 555-0142 • Dallas, TX",
        ]
        for line in lines[:3]:
            x0, _y0, x1, _y1 = line["bbox"]
            assert abs((x0 + x1) / 2 - PAGE_CENTER) < 2, line
    # The same text in the same place: one visual identity, not two look-alikes.
    assert [line["bbox"] for line in resume[:3]] == [line["bbox"] for line in letter[:3]]
    assert [line["fonts"] for line in resume[:3]] == [line["fonts"] for line in letter[:3]]
    assert any("Bold" in font for font in letter[0]["fonts"])


def test_the_letterhead_has_an_accent_rule_under_it() -> None:
    page = _document(_letter())[0]
    rules = [d for d in page.get_drawings() if d.get("color") or d.get("fill")]
    assert rules, "the accent rule under the header must be drawn"
    contact_bottom = _lines(_letter())[2]["bbox"][3]
    assert any(abs(d["rect"].y0 - contact_bottom) < 14 and d["rect"].width > 400 for d in rules)


# ---------------------------------------------------------------------------
# the résumé
# ---------------------------------------------------------------------------


def test_the_fixture_resume_fits_one_page_and_reads_in_order() -> None:
    pdf = _resume()
    assert len(_document(pdf)) == 1
    text = _text(pdf)
    order = [
        NAME,
        "SUMMARY",
        "EXPERIENCE",
        "Regional Sales Manager, Meridian Aesthetics",
        "Mar 2019 - Present",
        "Grew regional revenue 43%",
        "Territory Representative, Northwind Medical",
        "EDUCATION",
        "CERTIFICATIONS",
        "SKILLS",
        "PROJECTS",
    ]
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions), order


def test_a_role_line_is_title_comma_employer_in_bold_with_a_muted_meta_line_beneath() -> None:
    lines = _lines(_resume())
    texts = [line["text"] for line in lines]
    assert not any("—" in t and "Meridian" in t for t in texts), "the em dash joiner is gone"
    role = texts.index("Regional Sales Manager, Meridian Aesthetics")
    assert any("Bold" in font for font in lines[role]["fonts"])
    meta = lines[role + 1]
    assert meta["text"] == "Mar 2019 - Present · Dallas, TX"
    assert not any("Bold" in font for font in meta["fonts"])
    assert meta["bbox"][1] > lines[role]["bbox"][1] + 8, "the meta line sits beneath the role"
    # A role with no location line has just its dates beneath it.
    assert texts[texts.index("Territory Representative, Northwind Medical") + 1] == (
        "Jun 2014 - Feb 2019"
    )


def test_section_headers_are_uppercase_bold_with_a_rule() -> None:
    document = _document(_resume())
    lines = _lines(_resume())
    header = next(line for line in lines if line["text"] == "EXPERIENCE")
    assert any("Bold" in font for font in header["fonts"])
    assert header["bbox"][0] == pytest.approx(LEFT_MARGIN, abs=1)
    thin_rules = [d for d in document[0].get_drawings() if d["rect"].width > 400]
    assert len(thin_rules) >= 7, "one accent rule under the header and one under each section"


def test_skills_are_one_bullet_joined_paragraph_and_brackets_keep_their_commas() -> None:
    text = _text(_resume())
    assert (
        "Medical device sales  •  Aesthetic injectables  •  "
        "Account planning (multi-site, regional)"
    ) in text.replace("\n", " ") or (
        "Medical device sales • Aesthetic injectables • Account planning (multi-site, regional)"
        in re.sub(r"\s+", " ", text)
    )


def test_a_career_break_is_one_muted_line_and_bullets_are_tight_bullets() -> None:
    lines = _lines(_resume())
    texts = [line["text"] for line in lines]
    brk = next(line for line in lines if line["text"].startswith("Career break"))
    assert "Mar 2012 - May 2014" in brk["text"]
    assert not any("Bold" in font for font in brk["fonts"])
    assert any(t.startswith("• ") for t in texts), "bullets are drawn with a bullet character"
    assert "Regional launch playbook" in texts
    assert "• Wrote the launch plan used by 12 reps." in texts


def test_education_and_certifications_read_as_plain_lines() -> None:
    texts = [line["text"] for line in _lines(_resume())]
    assert "B.B.A. Marketing — University of Texas at Dallas (2008 - 2012)" in texts
    assert "Certified Medical Representative — CMR Institute" in texts


def test_a_long_resume_flows_to_two_pages_without_error() -> None:
    pdf = _resume(_long_resume(12))
    assert pdf.startswith(b"%PDF")
    assert len(_document(pdf)) == 2
    assert "Role 11, Employer 11" in _text(pdf)
    # A role's heading is never stranded at the foot of the first page.
    first_page = _lines(pdf, 0)
    assert not first_page[-1]["text"].startswith("Jan "), first_page[-1]


# ---------------------------------------------------------------------------
# the cover letter
# ---------------------------------------------------------------------------


def test_the_letter_reads_letterhead_date_recipient_greeting_body_closing_name() -> None:
    texts = [line["text"] for line in _lines(_letter())]
    assert texts[0] == NAME
    order = [
        "October 23, 2026",
        "Hiring Team",
        "Revance Therapeutics",
        "Regional Sales Manager - Medical Aesthetics",
        "Dear Hiring Team,",
        "I am applying for the Regional Sales Manager position",
        "Selected results from my experience:",
        "Grew regional revenue 43% across a 6-state region.",
        "I would welcome the opportunity",
        "Sincerely,",
    ]
    positions = []
    for item in order:
        positions.append(next(n for n, text in enumerate(texts) if text.startswith(item)))
    assert positions == sorted(positions) and len(set(positions)) == len(positions), texts
    assert texts[-1] == NAME, "the candidate's name closes the letter"
    assert texts.count("Dear Hiring Team,") == 1
    assert texts.count("Sincerely,") == 1
    assert texts.count(NAME) == 2, "the letterhead and the signature, no more"
    assert len(_document(_letter())) == 1


def test_no_body_line_is_centered_only_the_three_letterhead_lines() -> None:
    lines = _lines(_letter())
    for line in lines[:3]:
        x0, _y0, x1, _y1 = line["bbox"]
        assert abs((x0 + x1) / 2 - PAGE_CENTER) < 2
    bullet_mark = LEFT_MARGIN + 6
    bullet_text = LEFT_MARGIN + 18
    for line in lines[3:]:
        x0 = line["bbox"][0]
        assert any(
            abs(x0 - expected) < 1.5 for expected in (LEFT_MARGIN, bullet_mark, bullet_text)
        ), (line["text"], x0)
    paragraph = next(line for line in lines if line["text"].startswith("Commercial leader"))
    assert paragraph["bbox"][0] == pytest.approx(LEFT_MARGIN, abs=1)


def test_bullets_in_the_letter_are_drawn_as_bullets() -> None:
    texts = [line["text"] for line in _lines(_letter())]
    assert texts.count("•") == 2
    assert "Built and led a team of 7 managers." in texts


def test_paragraphs_are_separated_by_a_blank_line() -> None:
    lines = _lines(_letter())
    by_text = {line["text"]: line["bbox"] for line in lines}
    greeting = by_text["Dear Hiring Team,"]
    first = next(line["bbox"] for line in lines if line["text"].startswith("I am applying"))
    gap = first[1] - greeting[3]
    assert gap > 6, "a visible space, not a run-on"


def test_a_greeting_and_closing_already_in_the_markdown_are_not_duplicated() -> None:
    texts = [line["text"] for line in _lines(_letter())]
    assert texts.count("Dear Hiring Team,") == 1 and texts.count("Sincerely,") == 1
    bare = "I am applying for the role.\n\nI would welcome a conversation.\n"
    bare_texts = [line["text"] for line in _lines(_letter(bare))]
    assert bare_texts.count("Dear Hiring Team,") == 1
    assert bare_texts.count("Sincerely,") == 1
    assert bare_texts[-1] == NAME
    named = "Dear Ms. Alvarez,\n\nHello.\n\nBest regards,\nJordan A. Rivera\n"
    named_texts = [line["text"] for line in _lines(_letter(named))]
    assert "Dear Ms. Alvarez," in named_texts and "Dear Hiring Team," not in named_texts
    assert "Best regards," in named_texts and "Sincerely," not in named_texts
    assert named_texts.count(NAME) == 2 and "Jordan A. Rivera" not in named_texts


def test_without_a_date_there_is_no_date_line_and_the_renderer_never_reads_the_clock() -> None:
    texts = [line["text"] for line in _lines(_letter(dated=None))]
    assert texts[3] == "Hiring Team"
    assert not any(re.search(r"\b20\d\d\b", t) for t in texts[3:])


def test_the_recipient_block_skips_what_is_unknown() -> None:
    texts = [line["text"] for line in _lines(_letter(company="", role_title=""))]
    assert "Hiring Team" in texts and "Revance Therapeutics" not in texts


def _body_size(pdf: bytes) -> float:
    for block in _document(pdf)[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].startswith("Commercial leader"):
                    return round(span["size"], 2)
    raise AssertionError("body paragraph not found")


_FILLER = "Commercial leader with deep field sales experience in aesthetics. " * 6


def _filler_letter(paragraphs: int) -> str:
    return "Dear Hiring Team,\n\n" + "\n\n".join([_FILLER] * paragraphs) + "\n\nSincerely,\nX"


def test_a_long_letter_steps_down_to_stay_on_one_page() -> None:
    results = {n: _letter(_filler_letter(n)) for n in range(2, 14)}
    full = [
        n for n, pdf in results.items() if _body_size(pdf) == 10.0 and len(_document(pdf)) == 1
    ]
    stepped = [
        n
        for n, pdf in results.items()
        if _body_size(pdf) < 10.0 and len(_document(pdf)) == 1
    ]
    assert full, "a short letter keeps the full 10 pt type"
    assert stepped, "some letter is two pages at 10 pt and one page in smaller type"
    assert max(full) < min(stepped)
    assert min(_body_size(results[n]) for n in stepped) >= 8.5


def test_a_letter_that_cannot_fit_even_smaller_keeps_full_size_type_on_two_pages() -> None:
    pdf = _letter(_filler_letter(40))
    assert len(_document(pdf)) >= 2
    assert _body_size(pdf) == 10.0, "no 8.5 pt type: the full-size letter runs to two pages"


def test_markup_characters_in_a_letter_are_escaped_not_interpreted() -> None:
    text = _text(_letter("Dear Hiring Team,\n\n5 < 6 & <b>not bold</b> &amp; done\n"))
    assert "5 < 6 & <b>not bold</b> &amp; done" in text


# ---------------------------------------------------------------------------
# unrenderable text still stops both documents
# ---------------------------------------------------------------------------


def test_a_name_that_cannot_be_drawn_raises_in_the_resume() -> None:
    with pytest.raises(UnrenderableText) as excinfo:
        _resume("# 日本 Rivera\n\n## Summary\nx\n")
    assert "name" in str(excinfo.value)


def test_a_name_that_cannot_be_drawn_raises_in_the_cover_letter() -> None:
    with pytest.raises(UnrenderableText) as excinfo:
        _letter(candidate_name="日本 Rivera")
    assert "name" in str(excinfo.value)
    with pytest.raises(UnrenderableText) as contact:
        _letter(contact_line="jordan@example.test | ☎ (214) 555-0142")
    assert "contact" in str(contact.value)


def test_characters_with_a_plain_equivalent_are_mapped_in_the_letter() -> None:
    pdf = _letter(candidate_name="Łukasz Żółć", markdown="Dear Hiring Team,\n\nArrow → done ✓\n")
    text = _text(pdf)
    assert "Lukasz Zólc" in text and "->" in text and "?" not in text


# ---------------------------------------------------------------------------
# header extraction
# ---------------------------------------------------------------------------


def test_extract_letterhead_reads_the_resume_header() -> None:
    head = extract_letterhead(RESUME)
    assert (head.name, head.headline, head.contact_line) == (NAME, HEADLINE, CONTACT)
    assert extract_letterhead("") == extract_letterhead("## Summary\nx\n")


# ---------------------------------------------------------------------------
# the adapter and the engine feed the letter
# ---------------------------------------------------------------------------


def _package(**overrides) -> ApplicationPackageSchema:
    base = dict(
        opportunity_id="opp_style",
        candidate_id="cand_style",
        job_id="job_style",
        applicant_name=NAME,
        applicant_email="jordan.rivera@example.test",
        applicant_phone="(214) 555-0142",
        applicant_country="United States",
        tailored_resume_markdown=RESUME,
        cover_letter=LETTER,
    )
    base.update(overrides)
    return ApplicationPackageSchema(**base)


def _adapter(tmp_path, capture):
    page = FakeFormPage(answerable_capture(capture), captcha=False)
    adapter = GreenhouseApplicationAdapter(
        dry_run=True,
        page_factory=lambda _url: page,
        settings=type("S", (), {"artifacts_dir": tmp_path})(),
    )
    return adapter, page


def test_the_adapter_renders_the_letter_with_the_job_and_the_prepared_date(
    greenhouse_form_capture, tmp_path
) -> None:
    adapter, _page = _adapter(tmp_path, greenhouse_form_capture)
    documents = adapter.prepare_documents(
        _package(), job=build_job(), dated="October 23, 2026"
    )
    letter = documents["cover_letter"].read_bytes()
    assert letter == _letter(
        title=f"{NAME} - Cover Letter",
        role_title="Regional Sales Manager - Medical Aesthetics",
    ), "the upload is the render of the approved markdown, byte for byte"
    texts = [line["text"] for line in _lines(letter)]
    assert "October 23, 2026" in texts and "Revance Therapeutics" in texts
    # The résumé keeps its own renderer.
    assert documents["resume"].read_bytes() == render_resume_pdf(
        RESUME, title=f"{NAME} - Resume"
    )


def test_re_rendering_the_same_package_is_byte_identical(
    greenhouse_form_capture, tmp_path
) -> None:
    adapter, _page = _adapter(tmp_path, greenhouse_form_capture)
    package = _package()
    first = adapter.prepare_documents(package, job=build_job(), dated="October 23, 2026")
    first_bytes = {k: v.read_bytes() for k, v in first.items()}
    second = adapter.prepare_documents(package, job=build_job(), dated="October 23, 2026")
    assert {k: v.read_bytes() for k, v in second.items()} == first_bytes


def test_the_report_carries_hashes_for_both_documents(greenhouse_form_capture, tmp_path) -> None:
    import hashlib

    adapter, page = _adapter(tmp_path, greenhouse_form_capture)
    form = adapter.inspect(build_job())
    adapter.prepare_documents(_package(), job=build_job(), dated="October 23, 2026")
    prepared = adapter.prepare(_package(), form)
    documents = prepared.as_dict()["documents"]
    for key, field in (("resume", "#resume"), ("cover_letter", "#cover_letter")):
        data = Path(page.files[field]).read_bytes()
        assert documents[key]["sha256"] == hashlib.sha256(data).hexdigest()
        assert documents[key]["bytes"] == len(data)
    assert documents["cover_letter"]["unmapped_characters"] == 0


def test_without_a_job_the_letter_has_no_company_and_no_date(
    greenhouse_form_capture, tmp_path
) -> None:
    adapter, _page = _adapter(tmp_path, greenhouse_form_capture)
    documents = adapter.prepare_documents(_package())
    texts = [line["text"] for line in _lines(documents["cover_letter"].read_bytes())]
    assert "Hiring Team" in texts and "Revance Therapeutics" not in texts
    assert not any("2026" in t for t in texts)


def test_a_name_that_cannot_be_drawn_blocks_the_letter_too(
    greenhouse_form_capture, tmp_path
) -> None:
    from sabra_automation.greenhouse_adapter import DOCUMENT_ERROR
    from sabra_domain.errors import SafetyStop

    adapter, _page = _adapter(tmp_path, greenhouse_form_capture)
    package = _package(
        applicant_name="日本 Rivera", tailored_resume_markdown=None, cover_letter=LETTER
    )
    with pytest.raises(SafetyStop) as excinfo:
        adapter.prepare_documents(package, job=build_job(), dated=None)
    assert DOCUMENT_ERROR in excinfo.value.message
    assert "cover letter" in excinfo.value.message


def test_the_engine_dates_the_letter_from_the_stored_package_not_the_clock() -> None:
    service = ExecutionService.__new__(ExecutionService)
    # The date line is the host-local calendar day the package was created, so the test
    # builds its timestamp already in local time: it holds in any host timezone.
    created = datetime(2026, 10, 23, 15, 0).astimezone()

    class _Repos:
        applications = SimpleNamespace(get=lambda _id: SimpleNamespace(package_id="pkg_1"))
        packages = SimpleNamespace(get=lambda _id: SimpleNamespace(created_at=created))

    @contextmanager
    def read_repos():
        yield _Repos()

    service.ctx = SimpleNamespace(read_repos=read_repos)
    assert service._package_date("app_1") == "October 23, 2026"

    @contextmanager
    def broken():
        raise RuntimeError("database is gone")
        yield  # pragma: no cover

    service.ctx = SimpleNamespace(read_repos=broken)
    assert service._package_date("app_1") is None


# ---------------------------------------------------------------------------
# review fixes: opportunity-keyed context, unmapped recipients, closing, location
# ---------------------------------------------------------------------------


def test_a_package_never_gets_another_opportunitys_company(
    greenhouse_form_capture, tmp_path
) -> None:
    adapter, _page = _adapter(tmp_path, greenhouse_form_capture)
    job_a = build_job(company="Alpha Corp", title="Alpha Role")
    adapter.prepare_documents(_package(opportunity_id="opp_a"), job=job_a, dated="October 1, 2026")
    letter_b = adapter.prepare_documents(_package(opportunity_id="opp_b"))["cover_letter"]
    texts = [line["text"] for line in _lines(letter_b.read_bytes())]
    assert "Alpha Corp" not in texts and "Alpha Role" not in texts
    assert not any("2026" in t for t in texts), "nor its date"
    # A date alone for B does not borrow A's company either.
    letter_b = adapter.prepare_documents(
        _package(opportunity_id="opp_b"), dated="October 2, 2026"
    )["cover_letter"]
    texts = [line["text"] for line in _lines(letter_b.read_bytes())]
    assert "October 2, 2026" in texts and "Alpha Corp" not in texts
    # And A still has its own.
    letter_a = adapter.prepare_documents(_package(opportunity_id="opp_a"))["cover_letter"]
    assert "Alpha Corp" in [line["text"] for line in _lines(letter_a.read_bytes())]


def test_a_company_the_fonts_cannot_draw_is_left_out_and_counted(
    greenhouse_form_capture, tmp_path
) -> None:
    pdf = _letter(company="株式会社メルカリ", role_title="Regional Sales Manager 🚀")
    text = _text(pdf)
    assert "?" not in text
    assert "Hiring Team" in text and "Regional Sales Manager" not in text.split("Dear")[0]
    # A part that is drawable stays.
    assert "Regional Sales Manager - Medical Aesthetics" in _text(
        _letter(company="株式会社メルカリ")
    )
    adapter, _page = _adapter(tmp_path, greenhouse_form_capture)
    job = build_job(company="株式会社メルカリ", title="Regional Sales Manager 🚀")
    adapter.prepare_documents(_package(), job=job, dated=None)
    form = adapter.inspect(job)
    report = adapter.prepare(_package(), form).as_dict()["documents"]
    assert report["cover_letter"]["unmapped_characters"] == 8 + 1


def test_the_headline_is_counted_toward_the_letters_unmapped_characters(
    greenhouse_form_capture, tmp_path
) -> None:
    resume = RESUME.replace(HEADLINE, "Sales Leader 日本")
    adapter, _page = _adapter(tmp_path, greenhouse_form_capture)
    form = adapter.inspect(build_job())
    report = adapter.prepare(_package(tailored_resume_markdown=resume), form)
    counts = report.as_dict()["documents"]
    assert counts["resume"]["unmapped_characters"] == 2
    assert counts["cover_letter"]["unmapped_characters"] == 2


def test_a_thank_you_line_in_the_body_is_not_the_closing() -> None:
    letter = (
        "Dear Hiring Team,\n\nI am applying.\n\nThank you.\n\n"
        "I would welcome a conversation.\n\nSincerely,\nJordan A. Rivera\n"
    )
    texts = [line["text"] for line in _lines(_letter(letter))]
    assert texts.index("Thank you.") < texts.index("I would welcome a conversation.")
    assert texts.index("I would welcome a conversation.") < texts.index("Sincerely,")
    assert texts.count("Sincerely,") == 1 and texts[-1] == NAME
    assert texts.count("Thank you.") == 1


def test_a_closing_with_a_long_tail_after_it_is_body_text() -> None:
    body = "Dear Hiring Team,\n\nThank you,\n\n" + "\n".join(["A line of the letter."] * 3)
    texts = [line["text"] for line in _lines(_letter(body))]
    assert "Sincerely," in texts, "the standard closing is added after the whole body"


def test_only_the_candidates_own_name_is_printed_as_the_signature() -> None:
    letter = "Dear Hiring Team,\n\nHello.\n\nSincerely,\nJ. Rivera\n"
    texts = [line["text"] for line in _lines(_letter(letter))]
    assert "J. Rivera" not in texts and texts[-1] == NAME


@pytest.mark.parametrize(
    "line", ["Remote-first payments team that builds cards.", "Led the Dallas, Texas region team"]
)
def test_a_role_summary_is_never_mistaken_for_a_location(line: str) -> None:
    markdown = (
        f"# {NAME}\n\n## Experience\n\n"
        f"**Engineer — Acme** | 2020 - Present\n{line}\n- Built things.\n"
    )
    lines = _lines(_resume(markdown))
    texts = [t["text"] for t in lines]
    assert "2020 - Present" in texts, "the dates line stays dates only"
    assert line in texts, "the summary is its own paragraph"
    summary = next(t for t in lines if t["text"] == line)
    assert not any("Bold" in font for font in summary["fonts"])


@pytest.mark.parametrize(
    ("line", "meta"),
    [
        ("Dallas, TX", "2020 - Present · Dallas, TX"),
        ("Remote", "2020 - Present · Remote"),
        ("Dallas, TX • 2020 - Present", "2020 - Present · Dallas, TX"),
    ],
)
def test_a_strict_location_line_joins_the_muted_meta_line(line: str, meta: str) -> None:
    dates = " | 2020 - Present" if "•" not in line else ""
    markdown = (
        f"# {NAME}\n\n## Experience\n\n**Engineer — Acme**{dates}\n{line}\n- Built things.\n"
    )
    texts = [t["text"] for t in _lines(_resume(markdown))]
    assert meta in texts


def test_summary_paragraphs_have_air_between_them() -> None:
    markdown = f"# {NAME}\n\n## Summary\nFirst paragraph.\nSecond paragraph.\n"
    lines = {t["text"]: t["bbox"] for t in _lines(_resume(markdown))}
    gap = lines["Second paragraph."][1] - lines["First paragraph."][3]
    assert gap > 2


def test_a_role_summary_has_air_before_its_bullets() -> None:
    markdown = (
        f"# {NAME}\n\n## Experience\n\n**Engineer — Acme** | 2020 - Present\n"
        "Led the payments platform.\n- Built things.\n"
    )
    lines = {t["text"]: t["bbox"] for t in _lines(_resume(markdown))}
    bullet = next(b for text, b in lines.items() if text.startswith("• Built"))
    gap = bullet[1] - lines["Led the payments platform."][3]
    assert gap > 2


def test_only_career_break_prefixed_lines_are_styled_as_breaks() -> None:
    markdown = (
        f"# {NAME}\n\n## Experience\n\n**Raised a family** | 2012 - 2014\n"
        "\n**Career break** | 2010 - 2012\n"
    )
    lines = {t["text"]: t for t in _lines(_resume(markdown))}
    assert any("Bold" in font for font in lines["Raised a family"]["fonts"])
    assert not any("Bold" in font for font in lines["Career break (2010 - 2012)"]["fonts"])


# ---------------------------------------------------------------------------
# review fixes: handoff path, plain-text recipient, a failing renderer
# ---------------------------------------------------------------------------


def test_the_handoff_fill_renders_the_same_letter_as_prepare_documents(
    greenhouse_form_capture, tmp_path
) -> None:
    adapter, page = _adapter(tmp_path, greenhouse_form_capture)
    job = build_job()
    documents = adapter.prepare_documents(_package(), job=job, dated="October 23, 2026")
    expected = documents["cover_letter"].read_bytes()
    form = adapter.inspect(job)
    # The headed handoff calls apply_to_page with no documents: it re-renders.
    adapter.apply_to_page(page, _package(), form)
    assert Path(page.files["#cover_letter"]).read_bytes() == expected
    texts = [line["text"] for line in _lines(expected)]
    assert "October 23, 2026" in texts and "Revance Therapeutics" in texts


def test_company_and_title_reach_the_pdf_as_plain_escaped_text() -> None:
    pdf = _letter(company="A&B <b>Co</b> _x_\x07", role_title="Role **1** &amp; <i>2</i>")
    text = _text(pdf)
    assert "A&B <b>Co</b> _x_" in text
    assert "Role **1** &amp; <i>2</i>" in text
    assert "\x07" not in text


def test_a_failing_cover_letter_render_blocks_prepare_with_nothing_typed(
    greenhouse_form_capture, tmp_path, monkeypatch
) -> None:
    from sabra_automation.greenhouse_adapter import DOCUMENT_ERROR
    from sabra_domain.errors import SafetyStop

    def boom(*_args, **_kwargs) -> bytes:
        raise RuntimeError("letter renderer failed")

    monkeypatch.setattr("sabra_documents.render.render_cover_letter_pdf", boom)
    adapter, page = _adapter(tmp_path, greenhouse_form_capture)
    form = adapter.inspect(build_job())
    with pytest.raises(SafetyStop) as excinfo:
        adapter.prepare(_package(), form)
    assert DOCUMENT_ERROR in excinfo.value.message
    assert "cover letter" in excinfo.value.message
    assert page.files == {} and page.filled == {}
