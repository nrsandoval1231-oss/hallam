"""Résumé parser robustness: nothing a candidate wrote is silently lost.

Every résumé here is invented ("Morgan Ellison" and the employers are fictional) and is
shaped after the ways a real résumé defeated the first parser: a note about time away
from work, an education block spread over several lines, a bulleted header, skills
written as a sentence, two-line role headers in either order, and wrapped bullets.
"""

from __future__ import annotations

from datetime import date

import pytest
from sabra_documents.master_resume import (
    ResumeSection,
    parse_education,
    parse_experience,
    parse_master_resume,
    parse_skills,
    render_markdown,
    years_of_experience,
)
from sabra_documents.package import PackageFactory
from sabra_documents.tailor import ResumeTailor
from sabra_domain.schemas import CandidateProfile


def _section(name: str, text: str) -> ResumeSection:
    return ResumeSection(name=name, heading=name.title(), lines=text.split("\n"))


# ---------------------------------------------------------------------------
# a. A note about time away from work becomes an employment entry
# ---------------------------------------------------------------------------

STARRED_NOTE = "*Employment gap from 2012–2021 dedicated to full-time parenting. *"
BREAK_RESUME = f"""MORGAN ELLISON
morgan.ellison@example.test

EXPERIENCE
Sales Associate | Holiday Lite Source | 2022 - 2025
- Sold seasonal lighting packages to homeowners.

{STARRED_NOTE}

Office Coordinator | Plains Dental Group | 2008 - 2012
- Scheduled patients and kept the front desk running.

EDUCATION
Bachelor of Arts, Lone Star State University, 2006
"""


def test_a_starred_gap_note_is_an_employment_entry_not_a_dropped_line() -> None:
    document = parse_master_resume(BREAK_RESUME)
    breaks = [e for e in document.employment if e.is_break]
    assert len(breaks) == 1
    entry = breaks[0]
    assert entry.employer is None
    assert entry.start_date == "2012" and entry.end_date == "2021"
    assert "full-time parenting" in (entry.title or "")
    assert entry.accomplishments == []
    assert not any("parenting" in line for line in document.unparsed_lines)
    # the roles on either side of the gap are untouched
    assert [e.employer for e in document.employment if not e.is_break] == [
        "Holiday Lite Source",
        "Plains Dental Group",
    ]
    assert document.employment[1] is entry  # kept in the order the candidate wrote it


def test_a_dash_form_career_break_line_takes_title_and_dates() -> None:
    document = parse_master_resume(
        "EXPERIENCE\nCareer break — full-time parenting / 2012 – 2021\n"
        "Office Coordinator | Plains Dental Group | 2008 - 2012\n- Kept the front desk running.\n"
    )
    entry = document.employment[0]
    assert entry.is_break is True
    assert entry.title == "Career break — full-time parenting"
    assert (entry.start_date, entry.end_date) == ("2012", "2021")
    assert entry.employer is None
    assert document.employment[1].employer == "Plains Dental Group"
    assert document.employment[1].accomplishments == ["Kept the front desk running."]


def test_a_gap_note_under_another_heading_is_still_found_and_placed_by_date() -> None:
    document = parse_master_resume(
        "EXPERIENCE\nSales Associate | Holiday Lite Source | 2022 - 2025\n- Sold lighting.\n"
        "Office Coordinator | Plains Dental Group | 2008 - 2012\n- Ran the front desk.\n\n"
        "EDUCATION\nBachelor of Arts, Lone Star State University, 2006\n"
        "Employment gap from 2012–2021 dedicated to full-time parenting.\n"
    )
    assert [e.is_break for e in document.employment] == [False, True, False]
    assert len(document.education) == 1
    assert document.education[0].institution == "Lone Star State University"
    assert "parenting" not in " ".join(document.unparsed_lines)


def test_a_sentence_that_only_mentions_a_sabbatical_is_not_a_break() -> None:
    entries, unparsed = parse_experience(
        _section(
            "experience",
            "Sales Associate | Holiday Lite Source | 2022 - 2025\n"
            "Returned after a sabbatical to lead the seasonal team.",
        )
    )
    assert [e.is_break for e in entries] == [False]
    assert entries[0].summary and "sabbatical" in entries[0].summary
    assert unparsed == []


def test_a_break_is_not_experience_or_an_employer() -> None:
    document = parse_master_resume(BREAK_RESUME)
    # 2022-2025 and 2008-2012 only: the 9-year gap earns no professional years
    years = years_of_experience(document, today=date(2026, 1, 1))
    assert years == 7.0
    profile = CandidateProfile(
        candidate_id="c",
        employment=document.employment,
        education=document.education,
    )
    from sabra_scoring.features import extract_candidate_features

    assert extract_candidate_features(profile).years_experience == 7.0


def test_a_break_renders_as_one_line_with_no_bullets_and_round_trips() -> None:
    document = parse_master_resume(BREAK_RESUME)
    profile = CandidateProfile(
        candidate_id="c",
        employment=document.employment,
        education=document.education,
        skills=[],
    )
    from test_p2c_support import job

    tailored = ResumeTailor().tailor(profile, None, job())
    lines = tailored.markdown.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("**Employment gap"))
    assert "2012" in lines[index] and "2021" in lines[index]
    assert not lines[index + 1 :][0:1] or not lines[index + 1].startswith("- ")
    # breaks are listed after the working roles
    assert index > next(i for i, line in enumerate(lines) if "Holiday Lite Source" in line)

    markdown = render_markdown(document)
    reparsed = parse_master_resume(markdown)
    assert sum(e.is_break for e in reparsed.employment) == 1


def test_the_break_text_is_candidate_evidence_for_the_truth_gate() -> None:
    from sabra_documents.truth_gate import profile_evidence_text

    document = parse_master_resume(BREAK_RESUME)
    profile = CandidateProfile(candidate_id="c", employment=document.employment)
    assert "full-time parenting" in profile_evidence_text(profile)


def test_a_break_is_never_typed_into_an_employment_history_form() -> None:
    from test_p2c_support import job

    document = parse_master_resume(BREAK_RESUME)
    profile = CandidateProfile(
        candidate_id="c", employment=document.employment, identity=document.contact
    )
    profile.provenance["employment"] = _verified()
    built = PackageFactory().build(
        profile=profile,
        master=document,
        job=job(),
        master_resume_id="r",
        job_id="j",
        opportunity_id="o",
        assessment=None,
        questions=[],
    )
    assert [e.is_break for e in built.schema.employment] == [False, False]


def _verified():
    from sabra_domain.enums import TruthLevel
    from sabra_domain.schemas import Provenance

    return Provenance(level=TruthLevel.VERIFIED, evidence=["owner"])


# ---------------------------------------------------------------------------
# b. Education: year, minors, a bare institution, and the one-line form
# ---------------------------------------------------------------------------

EDUCATION_BLOCK = """Arizona Desert State University
Lone Star College of El Paso • Graduated 2006
Bachelor of Arts in Communications – Public Relations
Minor in Biology
Minor in Legal Reasoning"""


def test_b_a_multi_line_education_block_keeps_year_minors_and_the_bare_institution() -> None:
    entries, unparsed = parse_education(_section("education", EDUCATION_BLOCK))
    assert unparsed == []
    assert len(entries) == 2
    bare, main = entries
    assert bare.institution == "Arizona Desert State University"
    assert bare.degree is None
    assert main.institution == "Lone Star College of El Paso"
    assert main.degree == "Bachelor of Arts in Communications – Public Relations"
    assert main.end_date == "2006"
    assert main.minors == ["Biology", "Legal Reasoning"]


@pytest.mark.parametrize(
    "line",
    [
        "Lone Star College of El Paso | Graduated 2006",
        "Lone Star College of El Paso • Class of 2006",
        "Lone Star College of El Paso, 2006",
    ],
)
def test_b_graduation_year_forms(line: str) -> None:
    entries, unparsed = parse_education(
        _section("education", f"{line}\nBachelor of Arts in History")
    )
    assert unparsed == []
    assert len(entries) == 1
    assert entries[0].end_date == "2006"
    assert entries[0].degree == "Bachelor of Arts in History"


def test_b_a_year_on_its_own_line_belongs_to_the_entry_above() -> None:
    entries, unparsed = parse_education(
        _section("education", "Lone Star College of El Paso\nBachelor of Arts in History\n2006")
    )
    assert unparsed == []
    assert entries[0].end_date == "2006"


def test_b_the_one_line_form_parses_degree_institution_and_year() -> None:
    entries, unparsed = parse_education(
        _section(
            "education",
            "Bachelor of Arts in Communications (Public Relations), "
            "Lone Star College at El Paso, 2006",
        )
    )
    assert unparsed == []
    assert len(entries) == 1
    assert entries[0].degree == "Bachelor of Arts in Communications (Public Relations)"
    assert entries[0].institution == "Lone Star College at El Paso"
    assert entries[0].end_date == "2006"


def test_b_a_date_range_and_minors_survive_a_markdown_round_trip() -> None:
    document = parse_master_resume(f"EDUCATION\n{EDUCATION_BLOCK}\n")
    markdown = render_markdown(document)
    reparsed = parse_master_resume(markdown)
    assert [(e.institution, e.degree, e.end_date, e.minors) for e in reparsed.education] == [
        (e.institution, e.degree, e.end_date, e.minors) for e in document.education
    ]


def test_b_minors_reach_the_tailored_education_line() -> None:
    from test_p2c_support import job

    document = parse_master_resume(f"EDUCATION\n{EDUCATION_BLOCK}\n")
    profile = CandidateProfile(candidate_id="c", education=document.education)
    tailored = ResumeTailor().tailor(profile, None, job())
    assert "Minor in Biology; Minor in Legal Reasoning" in tailored.markdown
    assert "(2006)" in tailored.markdown
    assert "Arizona Desert State University" in tailored.markdown


# ---------------------------------------------------------------------------
# c. Header block with bullets as separators
# ---------------------------------------------------------------------------

HEADER_RESUME = """Morgan Ellison
1428 Juniper Ridge Dr, Amarillo, TX 79109
• (806) 555-0173 •
morgan.ellison@example.test

SUMMARY
Dependable operations professional.

SKILLS
Excel, Scheduling
"""


def test_c_a_bulleted_header_block_is_contact_detail_only() -> None:
    document = parse_master_resume(HEADER_RESUME)
    contact = document.contact
    assert contact.full_name == "Morgan Ellison"
    assert contact.phone == "(806) 555-0173"
    assert contact.email == "morgan.ellison@example.test"
    assert (contact.city, contact.state, contact.postal_code) == ("Amarillo", "TX", "79109")
    # the street address is not a headline and the phone line is not a skill or summary
    assert document.headline is None
    assert document.summary == "Dependable operations professional."
    assert [s.name for s in document.skills] == ["Excel", "Scheduling"]
    assert document.unparsed_lines == []
    assert document.warnings == ["no employment entries could be parsed"]


def test_c_a_header_on_one_line_with_bullets_still_extracts_every_field() -> None:
    document = parse_master_resume(
        "Morgan Ellison\nAmarillo, TX • (806) 555-0173 • morgan.ellison@example.test\n"
        "Operations Coordinator\n\nSKILLS\nExcel\n"
    )
    assert document.contact.phone == "(806) 555-0173"
    assert document.contact.city == "Amarillo"
    assert document.headline == "Operations Coordinator"


# ---------------------------------------------------------------------------
# d. Skills
# ---------------------------------------------------------------------------


def test_d_a_proficient_in_sentence_becomes_one_skill_each() -> None:
    skills, unparsed = parse_skills(
        _section("skills", "Proficient in Microsoft Office, Google Workspace, and QuickBooks")
    )
    assert [s.name for s in skills] == ["Microsoft Office", "Google Workspace", "QuickBooks"]
    assert unparsed == []


def test_d_proficient_in_two_items_joined_by_and() -> None:
    skills, _ = parse_skills(_section("skills", "Proficient in Microsoft Office and QuickBooks"))
    assert [s.name for s in skills] == ["Microsoft Office", "QuickBooks"]


@pytest.mark.parametrize(
    "line",
    [
        "Excel • Scheduling • Inventory Control",
        "Excel | Scheduling | Inventory Control",
        "Excel; Scheduling; Inventory Control",
        "Excel, Scheduling, Inventory Control",
        "• Excel • Scheduling • Inventory Control",
    ],
)
def test_d_separator_styles_all_split(line: str) -> None:
    skills, unparsed = parse_skills(_section("skills", line))
    assert [s.name for s in skills] == ["Excel", "Scheduling", "Inventory Control"]
    assert unparsed == []


def test_d_a_name_with_and_inside_stays_one_skill() -> None:
    skills, _ = parse_skills(_section("skills", "Training and Development, Excel"))
    assert [s.name for s in skills] == ["Training and Development", "Excel"]


def test_d_commas_inside_parentheses_do_not_split_and_duplicates_collapse() -> None:
    skills, _ = parse_skills(_section("skills", "CRM (Salesforce, HubSpot), Excel, excel"))
    assert [s.name for s in skills] == ["CRM (Salesforce, HubSpot)", "Excel"]


def test_d_a_category_label_is_kept() -> None:
    skills, _ = parse_skills(_section("skills", "Software: Excel, QuickBooks"))
    assert [(s.name, s.category) for s in skills] == [
        ("Excel", "Software"),
        ("QuickBooks", "Software"),
    ]


# ---------------------------------------------------------------------------
# e. Two-line role headers, either order
# ---------------------------------------------------------------------------

BULLETS = "• Sold seasonal lighting packages to homeowners.\n"


@pytest.mark.parametrize(
    "header",
    [
        "Holiday Lite Source — Sales Associate",
        "Sales Associate — Holiday Lite Source",
        "**Sales Associate — Holiday Lite Source**",
        "Sales Associate | Holiday Lite Source",
    ],
)
def test_e_two_line_headers_parse_in_either_order(header: str) -> None:
    entries, unparsed = parse_experience(
        _section("experience", f"{header}\nLubbock, TX • 2022 – 2025\n{BULLETS}")
    )
    assert unparsed == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.employer == "Holiday Lite Source"
    assert entry.title == "Sales Associate"
    assert entry.location == "Lubbock, TX"
    assert (entry.start_date, entry.end_date) == ("2022", "2025")
    assert entry.accomplishments == ["Sold seasonal lighting packages to homeowners."]


def test_e_dates_first_then_location_and_a_current_role() -> None:
    entries, _ = parse_experience(
        _section(
            "experience",
            "Holiday Lite Source — Sales Associate\nJan 2022 – Present | Lubbock, TX\n",
        )
    )
    assert entries[0].location == "Lubbock, TX"
    assert entries[0].is_current is True


def test_e_a_dates_only_second_line_has_no_location() -> None:
    entries, _ = parse_experience(
        _section("experience", "Holiday Lite Source — Sales Associate\n2022 – 2025\n")
    )
    assert entries[0].location is None
    assert entries[0].start_date == "2022"


def test_e_consecutive_one_line_roles_are_not_merged_into_a_two_line_header() -> None:
    entries, _ = parse_experience(
        _section(
            "experience",
            "Sales Associate — Holiday Lite Source\n"
            "Cashier | Plains Grocery | 2019 - 2021\n",
        )
    )
    assert len(entries) == 2
    assert entries[0].start_date is None
    assert entries[1].employer == "Plains Grocery"


# ---------------------------------------------------------------------------
# f. Continuation lines, unparsed lines and warnings
# ---------------------------------------------------------------------------


def test_f_a_wrapped_bullet_joins_the_bullet_above() -> None:
    entries, unparsed = parse_experience(
        _section(
            "experience",
            "Sales Associate | Holiday Lite Source | 2022 - 2025\n"
            "• Sold seasonal lighting packages to homeowners across the\n"
            "South Plains region and trained new hires\n"
            "to close.\n"
            "• Kept the display floor stocked.\n",
        )
    )
    assert unparsed == []
    assert entries[0].accomplishments == [
        "Sold seasonal lighting packages to homeowners across the South Plains region "
        "and trained new hires to close.",
        "Kept the display floor stocked.",
    ]


def test_f_a_role_header_after_an_unterminated_bullet_is_not_swallowed() -> None:
    entries, _ = parse_experience(
        _section(
            "experience",
            "Sales Associate | Holiday Lite Source | 2022 - 2025\n"
            "• Sold seasonal lighting packages\n"
            "Cashier | Plains Grocery | 2019 - 2021\n"
            "• Ran the register.\n",
        )
    )
    assert [e.employer for e in entries] == ["Holiday Lite Source", "Plains Grocery"]
    assert entries[0].accomplishments == ["Sold seasonal lighting packages"]


def test_f_a_complete_bullet_followed_by_a_capitalised_sentence_is_not_joined() -> None:
    entries, unparsed = parse_experience(
        _section(
            "experience",
            "Sales Associate | Holiday Lite Source | 2022 - 2025\n"
            "• Sold seasonal lighting packages.\n"
            "Promoted twice in three years.\n",
        )
    )
    assert entries[0].accomplishments == ["Sold seasonal lighting packages."]
    assert "Promoted twice in three years." in (unparsed + [entries[0].summary or ""])


def test_f_unparsed_lines_and_warnings_are_exposed() -> None:
    document = parse_master_resume(
        "Morgan Ellison\nmorgan.ellison@example.test\n\n"
        "EXPERIENCE\n• A bullet with no role above it.\n"
        "Sales Associate\n"
        "Cashier | Plains Grocery\n- Ran the register.\n\n"
        "SKILLS\nExcel\n"
    )
    assert "• A bullet with no role above it." in document.unparsed_lines or any(
        "no role above it" in line for line in document.unparsed_lines
    )
    assert any("not understood" in w for w in document.warnings)
    assert any("'Cashier' has no dates" in w for w in document.warnings)
    assert document.as_dict()["unparsed_lines"] == document.unparsed_lines


def test_f_an_entry_with_no_employer_is_flagged() -> None:
    document = parse_master_resume(
        "EXPERIENCE\nSales Associate | 2022 - 2025\n- Sold things.\n\nSKILLS\nExcel\nSales\n"
    )
    assert any("has no employer" in w for w in document.warnings)


def test_a_clean_resume_has_no_warnings() -> None:
    document = parse_master_resume(HEADER_RESUME.replace("SKILLS", "EXPERIENCE\nCashier | Plains Grocery | 2019 - 2021\n- Ran the register.\n\nSKILLS"))
    assert document.warnings == []
    assert document.unparsed_lines == []


def test_a_full_real_shaped_resume_loses_nothing() -> None:
    text = f"""Morgan Ellison
1428 Juniper Ridge Dr, Amarillo, TX 79109
• (806) 555-0173 •
morgan.ellison@example.test

PROFESSIONAL SUMMARY
Dependable operations professional with a decade of retail and office experience.

EXPERIENCE
Holiday Lite Source — Sales Associate
Lubbock, TX • 2022 – 2025
• Sold seasonal lighting packages to homeowners across the
South Plains region.
• Trained eight seasonal hires.

{STARRED_NOTE}

Plains Dental Group — Office Coordinator
Amarillo, TX • 2008 – 2012
• Scheduled patients and kept the front desk running.

EDUCATION
{EDUCATION_BLOCK}

SKILLS
Proficient in Microsoft Office, Google Workspace, and QuickBooks
"""
    document = parse_master_resume(text)
    assert document.unparsed_lines == []
    assert document.warnings == []
    assert [e.is_break for e in document.employment] == [False, True, False]
    assert document.employment[0].location == "Lubbock, TX"
    assert document.employment[0].accomplishments[0].endswith("South Plains region.")
    assert len(document.education) == 2
    assert [s.name for s in document.skills] == [
        "Microsoft Office",
        "Google Workspace",
        "QuickBooks",
    ]


# ---------------------------------------------------------------------------
# f (continued). The upload result and the Candidate page show what was not understood
# ---------------------------------------------------------------------------

UPLOAD_RESUME = """Morgan Ellison
morgan.ellison@example.test

EXPERIENCE
Holiday Lite Source — Sales Associate
Lubbock, TX • 2022 – 2025
• Sold seasonal lighting packages.
Plains Dental Group | Office Coordinator
• Kept the front desk running.
• A stray bullet.

SKILLS
Excel
Refrigerated warehouse inventory rotation and cold-chain compliance reporting for regional grocers
"""


def test_the_upload_result_and_the_candidate_api_expose_unparsed_lines_and_warnings() -> None:
    from fastapi.testclient import TestClient
    from sabra_api.main import app

    client = TestClient(app)
    response = client.post(
        "/api/candidate/resume",
        files={"resume": ("resume.txt", UPLOAD_RESUME.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200
    body = response.json()
    assert any("Refrigerated warehouse" in line for line in body["unparsed_lines"])
    assert any("'Office Coordinator' has no dates" in w for w in body["warnings"])
    assert any("not understood" in w for w in body["warnings"])

    candidate = client.get("/api/candidate").json()
    stored = next(r for r in candidate["resumes"] if r["id"] == body["resume_id"])
    assert stored["unparsed_lines"] == body["unparsed_lines"]
    assert stored["warnings"] == body["warnings"]


def test_the_candidate_page_lists_unparsed_lines_under_the_resume_versions() -> None:
    from p2b_support import EVIL, assert_inert, render
    from sabra_api.main import candidate_page

    payload = {
        "sections": [],
        "identity": {},
        "identity_levels": {},
        "resumes": [
            {
                "id": "res_1",
                "version": 2,
                "label": "resume.txt",
                "is_master": True,
                "source_format": "text",
                "content_hash": "ab" * 32,
                "unparsed_lines": [EVIL, "Second stray line"],
                "warnings": ["Employment entry 'Cashier' has no dates"],
            },
            {
                "id": "res_0",
                "version": 1,
                "label": "old.txt",
                "is_master": False,
                "source_format": "text",
                "content_hash": "cd" * 32,
                "unparsed_lines": [],
                "warnings": [],
            },
        ],
    }
    html = render(candidate_page(), payload)["nodes"]["#resumes"]["innerHTML"]
    assert "These lines were not understood:" in html
    assert "Second stray line" in html
    assert "Employment entry &#39;Cashier&#39; has no dates" in html
    assert_inert(html, EVIL)
    # a clean version adds nothing to the list of unread lines
    assert html.count("These lines were not understood:") == 1


def test_a_candidate_with_nothing_unparsed_shows_no_warning_block() -> None:
    from p2b_support import render
    from sabra_api.main import candidate_page

    payload = {
        "sections": [],
        "identity": {},
        "identity_levels": {},
        "resumes": [
            {
                "id": "r",
                "version": 1,
                "label": "x",
                "is_master": True,
                "source_format": "text",
                "content_hash": "ab" * 32,
                "unparsed_lines": [],
                "warnings": [],
            }
        ],
    }
    html = render(candidate_page(), payload)["nodes"]["#resumes"]["innerHTML"]
    assert "not understood" not in html
