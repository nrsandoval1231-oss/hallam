"""The uploaded résumé is a PDF rendered from the approved Markdown, never Markdown."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf
import pytest
from conftest_app import build_job
from fake_pages import FakeFormPage, answerable_capture
from sabra_automation.base import ApplicationForm
from sabra_automation.greenhouse_adapter import DOCUMENT_ERROR, GreenhouseApplicationAdapter
from sabra_automation.lever_adapter import LeverApplicationAdapter
from sabra_documents.render import (
    UnrenderableText,
    count_unmapped,
    render_resume_pdf,
    renderer_version,
    winansi_safe,
)
from sabra_domain.enums import SafetyStopKind
from sabra_domain.errors import SafetyStop
from sabra_domain.schemas import ApplicationPackageSchema, EmploymentEntry

RESUME = """# JORDAN A. RIVERA
_Medical Aesthetics & Device Sales Leader_
jordan.rivera@example.test | Dallas, TX | (214) 555-0142

## Summary
Commercial leader with **11 years** of field sales experience.

## Experience

**Regional Sales Manager, Meridian Aesthetics** | Mar 2019 - Present
- Grew regional revenue 43% across a 6-state region.
- Built and led a team of 7 managers.
  - Covered Texas, Oklahoma and Louisiana.

## Skills
Medical device sales, Aesthetic injectables, Account planning
"""


def _text(pdf: bytes) -> str:
    document = pymupdf.open(stream=pdf, filetype="pdf")
    return "\n".join(page.get_text() for page in document)


def test_render_is_a_pdf_and_deterministic() -> None:
    first = render_resume_pdf(RESUME, title="Jordan Rivera - Resume")
    second = render_resume_pdf(RESUME, title="Jordan Rivera - Resume")
    assert first.startswith(b"%PDF")
    assert first == second, "the same approved markdown must always yield identical bytes"
    assert render_resume_pdf(RESUME + "\n- one more\n", title="Jordan Rivera - Resume") != first


def test_rendered_text_has_no_markdown_markup() -> None:
    text = _text(render_resume_pdf(RESUME, title="Jordan Rivera - Resume"))
    assert "**" not in text
    assert not re.search(r"(^|\n)#+ ", text), text
    assert "# " not in text
    assert not re.search(r"(^|\s)_\w", text), "italic markers must not leak"
    for expected in (
        "JORDAN A. RIVERA",
        "Medical Aesthetics & Device Sales Leader",
        "jordan.rivera@example.test",
        "11 years",
        "Regional Sales Manager, Meridian Aesthetics",
        "Grew regional revenue 43%",
        "Covered Texas, Oklahoma and Louisiana.",
        "Account planning",
    ):
        assert expected in text, expected
    assert "SUMMARY" in text and "EXPERIENCE" in text  # headings are rendered as headings


def test_characters_outside_winansi_never_crash_the_render() -> None:
    nasty = (
        "# Zoë Lukasz\n\n## Summary\n"
        "Arrow → check ✓ ≥ 5 years — “quoted” • "
        "\U0001f600​ 日本\n"
        "- nonbreaking space and ‑ hyphen\n"
    )
    pdf = render_resume_pdf(nasty, title="Zoë 日本")
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    assert "Zoë" in text
    assert "->" in text and ">= 5 years" in text
    assert "—" in text  # an em dash is in WinAnsi and is kept
    assert count_unmapped(nasty) == 3  # the emoji and the two CJK characters, in the body
    assert winansi_safe("日\U0001f600").replace("?", "") == ""


def test_letters_with_an_obvious_plain_form_are_mapped_not_replaced_by_a_question_mark() -> None:
    assert winansi_safe("Łukasz Żółć") == "Lukasz Zólc"
    assert winansi_safe("đĐıŀ") == "dDil"
    # Letters WinAnsi already has are kept, not flattened.
    assert winansi_safe("Weiß œuvre Œ") == "Weiß œuvre Œ"
    assert count_unmapped("Łukasz Żółć đ") == 0


def test_a_name_or_contact_line_that_cannot_be_drawn_faithfully_raises() -> None:
    with pytest.raises(UnrenderableText) as name:
        render_resume_pdf("# 日本 Rivera\n\n## Summary\nx\n", title="t")
    assert "name" in str(name.value)
    with pytest.raises(UnrenderableText) as contact:
        render_resume_pdf(
            "# Jordan Rivera\njordan@example.test | ☎ (214) 555-0142\n\n## Summary\nx\n",
            title="t",
        )
    assert "contact" in str(contact.value)
    # The same character in the body is tolerated, and counted.
    body = "# Jordan Rivera\njordan@example.test\n\n## Summary\nCall ☎ anytime\n"
    assert render_resume_pdf(body, title="t").startswith(b"%PDF")
    assert count_unmapped(body) == 1


def test_markup_characters_in_content_are_escaped_not_interpreted() -> None:
    pdf = render_resume_pdf("## S\n- 5 < 6 & <b>not bold</b> &amp; done\n", title="t")
    text = _text(pdf)
    assert "5 < 6 & <b>not bold</b> &amp; done" in text


def test_empty_markdown_still_renders() -> None:
    assert render_resume_pdf("", title="Empty").startswith(b"%PDF")


# ---------------------------------------------------------------------------
# the adapters upload the PDF
# ---------------------------------------------------------------------------


def _package(**overrides) -> ApplicationPackageSchema:
    base = dict(
        opportunity_id="opp_pdf",
        candidate_id="cand_pdf",
        job_id="job_pdf",
        applicant_name="JORDAN A. RIVERA",
        applicant_email="jordan.rivera@example.test",
        applicant_phone="(214) 555-0142",
        applicant_country="United States",
        tailored_resume_markdown=RESUME,
        cover_letter="Dear Hiring Team,\n\nI am applying.\n\n- Selected result one\n",
        employment=[
            EmploymentEntry(
                employer="Meridian Aesthetics",
                title="Regional Sales Manager",
                start_date="Mar 2019",
                end_date="Present",
                is_current=True,
            )
        ],
    )
    base.update(overrides)
    return ApplicationPackageSchema(**base)


def _adapter(capture: dict, tmp_path, factory_calls: list | None = None):
    page = FakeFormPage(capture, captcha=False)

    def factory(_url: str):
        if factory_calls is not None:
            factory_calls.append(_url)
        return page

    adapter = GreenhouseApplicationAdapter(
        dry_run=True,
        page_factory=factory,
        settings=type("S", (), {"artifacts_dir": tmp_path})(),
    )
    return adapter, page


def test_greenhouse_uploads_a_named_pdf_and_keeps_the_markdown(
    greenhouse_form_capture, tmp_path
) -> None:
    adapter, page = _adapter(answerable_capture(greenhouse_form_capture), tmp_path)
    form = adapter.inspect(build_job())
    prepared = adapter.prepare(_package(), form)

    uploaded = Path(page.files["#resume"])
    assert uploaded.name == "Jordan-Rivera-Resume.pdf"
    assert uploaded.read_bytes().startswith(b"%PDF")
    assert uploaded.read_bytes() == render_resume_pdf(
        RESUME, title="JORDAN A. RIVERA - Resume"
    ), "the upload is the render of the approved markdown, byte for byte"
    assert "**" not in _text(uploaded.read_bytes())
    assert prepared.files["resume"] == str(uploaded)
    assert not any(str(path).endswith(".md") for path in page.files.values())
    # The owner's Markdown copy sits beside it.
    assert (uploaded.parent / "tailored_resume.md").read_text(encoding="utf-8") == RESUME
    cover = Path(page.files["#cover_letter"])
    assert cover.name == "Jordan-Rivera-Cover-Letter.pdf"
    assert cover.read_bytes().startswith(b"%PDF")


def test_the_prepared_record_carries_each_pdfs_hash_and_the_renderer_version(
    greenhouse_form_capture, tmp_path
) -> None:
    adapter, page = _adapter(answerable_capture(greenhouse_form_capture), tmp_path)
    form = adapter.inspect(build_job())
    prepared = adapter.prepare(_package(), form)

    documents = prepared.as_dict()["documents"]
    uploaded = Path(page.files["#resume"]).read_bytes()
    assert documents["resume"]["sha256"] == hashlib.sha256(uploaded).hexdigest()
    assert documents["resume"]["bytes"] == len(uploaded)
    assert documents["resume"]["file"] == "Jordan-Rivera-Resume.pdf"
    assert documents["resume"]["unmapped_characters"] == 0
    assert documents["cover_letter"]["sha256"] == hashlib.sha256(
        Path(page.files["#cover_letter"]).read_bytes()
    ).hexdigest()
    assert documents["renderer"] == renderer_version()
    assert documents["renderer"].startswith("reportlab ")


def test_a_name_with_a_polish_letter_is_uploaded_without_a_question_mark(
    greenhouse_form_capture, tmp_path
) -> None:
    resume = "# Łukasz Żółć\nlukasz@example.test\n\n## Summary\nSales.\n"
    adapter, page = _adapter(answerable_capture(greenhouse_form_capture), tmp_path)
    form = adapter.inspect(build_job())
    adapter.prepare(
        _package(applicant_name="Łukasz Żółć", tailored_resume_markdown=resume),
        form,
    )
    uploaded = Path(page.files["#resume"])
    text = _text(uploaded.read_bytes())
    assert "?" not in text
    assert "Lukasz Zólc" in text


def test_a_name_that_cannot_be_drawn_blocks_instead_of_uploading_a_mangled_one(
    greenhouse_form_capture, tmp_path
) -> None:
    calls: list = []
    adapter, page = _adapter(answerable_capture(greenhouse_form_capture), tmp_path, calls)
    form = adapter.inspect(build_job())
    before = len(calls)
    resume = "# 日本 Rivera\njordan@example.test\n\n## Summary\nSales.\n"
    with pytest.raises(SafetyStop) as excinfo:
        adapter.prepare(_package(tailored_resume_markdown=resume), form)
    assert excinfo.value.kind == SafetyStopKind.UNSUPPORTED_FILE_REQUIREMENT
    assert DOCUMENT_ERROR in excinfo.value.message
    assert len(calls) == before, "prepare must not touch the page when a document cannot be made"
    assert page.files == {} and page.filled == {}


def test_document_stem_strips_accents_before_dropping_other_characters(tmp_path) -> None:
    adapter = GreenhouseApplicationAdapter(
        dry_run=True, settings=type("S", (), {"artifacts_dir": tmp_path})()
    )
    package = _package(applicant_name="José García")
    assert adapter._document_stem(package) == "Jose-Garcia"


def test_lever_uploads_the_pdf_too(tmp_path) -> None:
    adapter = LeverApplicationAdapter(
        dry_run=True, settings=type("S", (), {"artifacts_dir": tmp_path})()
    )
    page = FakeFormPage(
        {"fields": [{"id": "name"}, {"id": "email"}, {"id": "resume", "type": "file"}]},
        captcha=False,
    )
    form = ApplicationForm(url="https://jobs.lever.co/x/1/apply", submit_selector="button")
    filled, _skipped = adapter.apply_to_page(page, _package(cover_letter=None), form)
    uploaded = Path(page.files["#resume"])
    assert uploaded.suffix == ".pdf" and uploaded.read_bytes().startswith(b"%PDF")
    assert filled["resume"] == str(uploaded)


def test_a_render_failure_blocks_the_prepare_before_the_page_is_touched(
    greenhouse_form_capture, tmp_path, monkeypatch
) -> None:
    def broken(_markdown: str, *, title: str) -> bytes:
        raise RuntimeError("reportlab is unavailable")

    monkeypatch.setattr("sabra_documents.render.render_resume_pdf", broken)
    calls: list = []
    adapter, page = _adapter(answerable_capture(greenhouse_form_capture), tmp_path, calls)
    form = adapter.inspect(build_job())
    before = len(calls)

    with pytest.raises(SafetyStop) as excinfo:
        adapter.prepare(_package(), form)
    assert excinfo.value.kind == SafetyStopKind.UNSUPPORTED_FILE_REQUIREMENT
    assert DOCUMENT_ERROR in excinfo.value.message
    assert len(calls) == before, "no page may be opened or navigated when the render failed"
    assert page.files == {}, "Markdown must never be uploaded in place of the PDF"
    assert page.filled == {}, "nothing may be typed before the documents exist"


def test_a_file_the_os_will_not_let_us_write_is_a_document_error(
    greenhouse_form_capture, tmp_path, monkeypatch
) -> None:
    """A Windows file lock on the Markdown copy must block, not escape as a traceback."""

    original = Path.write_text

    def locked(self, *args, **kwargs):
        if self.name.endswith(".md"):
            raise PermissionError("the file is locked by another process")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", locked)
    adapter, page = _adapter(answerable_capture(greenhouse_form_capture), tmp_path)
    form = adapter.inspect(build_job())
    with pytest.raises(SafetyStop) as excinfo:
        adapter.prepare(_package(), form)
    assert DOCUMENT_ERROR in excinfo.value.message
    assert "PermissionError" in excinfo.value.message
    assert page.filled == {}


def test_the_handoff_path_still_fills_when_a_document_cannot_be_made(
    greenhouse_form_capture, tmp_path, monkeypatch
) -> None:
    """`apply_to_page` is what the headed handoff calls; a partial fill helps the person."""

    def broken(_markdown: str, *, title: str) -> bytes:
        raise RuntimeError("no renderer")

    monkeypatch.setattr("sabra_documents.render.render_resume_pdf", broken)
    adapter, page = _adapter(answerable_capture(greenhouse_form_capture), tmp_path)
    form = adapter.inspect(build_job())
    filled, skipped = adapter.apply_to_page(page, _package(), form)
    assert "first_name" in filled
    assert any(item.startswith(DOCUMENT_ERROR) for item in skipped)
    # The résumé is the document that could not be made: it is never replaced by Markdown.
    # The cover letter has its own renderer and is still attached.
    assert "#resume" not in page.files
    assert not any(str(path).endswith(".md") for path in page.files.values())


def test_without_settings_documents_go_under_the_configured_artifacts_dir_not_the_cwd(
    greenhouse_form_capture,
) -> None:
    from sabra_domain.settings import get_settings

    page = FakeFormPage(answerable_capture(greenhouse_form_capture), captcha=False)
    adapter = GreenhouseApplicationAdapter(dry_run=True, page_factory=lambda _url: page)
    form = adapter.inspect(build_job())
    adapter.prepare(_package(), form)
    uploaded = Path(page.files["#resume"])
    assert uploaded.is_relative_to(get_settings().artifacts_dir)
    assert not uploaded.is_relative_to(Path("var").resolve())
