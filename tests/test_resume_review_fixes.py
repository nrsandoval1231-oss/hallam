"""Regression tests for the review of the résumé-parser and dialog work.

Everything here is invented ("Morgan Ellison" and the employers are fictional).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from p2b_support import render
from sabra_api.main import _truth_sections, applications_page
from sabra_documents.master_resume import (
    ResumeSection,
    parse_experience,
    parse_master_resume,
)
from sabra_documents.truth_gate import (
    EvidenceCorpus,
    deterministic_check,
    profile_evidence_text,
)
from sabra_domain.identity import content_hash
from sabra_domain.schemas import (
    ApplicationPackageSchema,
    CandidateProfile,
    EducationEntry,
    EmploymentEntry,
)
from test_resume_parser_robustness import BREAK_RESUME

FIXTURES = Path(__file__).parent / "fixtures"


def _section(text: str, name: str = "experience") -> ResumeSection:
    return ResumeSection(name=name, heading=name.title(), lines=text.split("\n"))


def _experience(text: str):
    return parse_experience(_section(text))


def _warnings(experience_text: str) -> list[str]:
    return parse_master_resume(f"EXPERIENCE\n{experience_text}\n\nSKILLS\nExcel\n").warnings


# ---------------------------------------------------------------------------
# B1: a break phrase at the front of a job title or a skill is not a break
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "employer", "title"),
    [
        ("Medical Leave Coordinator | Baylor Health | 2015 - 2020", "Baylor Health", "Medical Leave Coordinator"),
        (
            "Sabbatical Program Manager | Lone Star University | 2010 - 2015",
            "Lone Star University",
            "Sabbatical Program Manager",
        ),
        ("Sabbatical — Baylor Health | 2015 - 2020", "Baylor Health", "Sabbatical"),
    ],
)
def test_b1_a_role_that_starts_with_a_break_phrase_is_a_role(
    line: str, employer: str, title: str
) -> None:
    entries, unparsed = _experience(f"{line}\n- Processed the claims.\n")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.is_break is False
    assert (entry.title, entry.employer) == (title, employer)
    assert entry.accomplishments == ["Processed the claims."]
    assert unparsed == []


def test_b1_a_skill_line_that_starts_with_a_break_phrase_stays_a_skill() -> None:
    document = parse_master_resume(
        "EXPERIENCE\nCashier | Plains Grocery | 2019 - 2021\n- Ran the register.\n\n"
        "SKILLS\nFamily leave administration, FMLA, Payroll\n"
    )
    assert [s.name for s in document.skills] == ["Family leave administration", "FMLA", "Payroll"]
    assert not any(e.is_break for e in document.employment)


def test_b1_an_undated_phrase_outside_experience_is_left_where_it_is() -> None:
    document = parse_master_resume(
        "EXPERIENCE\nCashier | Plains Grocery | 2019 - 2021\n- Ran the register.\n\n"
        "EDUCATION\nSabbatical studies at the Lone Star Institute\n"
    )
    assert not any(e.is_break for e in document.employment)
    assert any("Sabbatical studies" in line for line in document.unparsed_lines) or any(
        "Sabbatical studies" in (e.institution or "") for e in document.education
    )


def test_b1_a_prefixed_note_outside_experience_is_still_a_break() -> None:
    document = parse_master_resume(
        "EXPERIENCE\nCashier | Plains Grocery | 2019 - 2021\n- Ran the register.\n\n"
        "EDUCATION\nBachelor of Arts, Lone Star State University, 2006\n"
        "Note: career break for caregiving\n"
    )
    assert [e.is_break for e in document.employment] == [False, True]
    assert len(document.education) == 1


def test_b1_the_candidate_page_labels_a_break() -> None:
    profile = CandidateProfile(
        candidate_id="c",
        employment=[
            EmploymentEntry(title="Full-time parenting", is_break=True, start_date="2012"),
            EmploymentEntry(title="Career break — caregiving", is_break=True),
            EmploymentEntry(title="Cashier", employer="Plains Grocery"),
        ],
    )
    rows = next(s["rows"] for s in _truth_sections(profile) if s["title"] == "Employment")
    assert [r["label"] for r in rows] == [
        "Full-time parenting (career break)",
        "Career break — caregiving",
        "Cashier at Plains Grocery",
    ]


# ---------------------------------------------------------------------------
# B2: a career break is not tenure and its duration is not experience
# ---------------------------------------------------------------------------


def _corpus(text: str, master: str | None = None) -> EvidenceCorpus:
    document = parse_master_resume(text)
    profile = CandidateProfile(
        candidate_id="c", employment=document.employment, education=document.education
    )
    return EvidenceCorpus.build(profile, master_text=master)


def _kinds(text: str, corpus: EvidenceCorpus) -> list[str]:
    return [v.kind for v in deterministic_check(text, corpus)]


def test_b2_tenure_is_the_union_of_work_and_17_years_is_rejected() -> None:
    corpus = _corpus(BREAK_RESUME, master=BREAK_RESUME)
    assert corpus.tenure == 7  # 2008-2012 and 2022-2025; the 2012-2021 break earns nothing
    assert corpus.years_limit == 7
    assert "number" in _kinds("I bring 17 years of experience in sales.", corpus)
    assert "number" in _kinds("I bring 9 years of experience in sales.", corpus)
    assert "number" not in _kinds("I bring 6 years of experience in sales.", corpus)


def test_b2_back_to_back_and_overlapping_roles_still_form_one_stretch() -> None:
    profile = CandidateProfile(
        candidate_id="c",
        employment=[
            EmploymentEntry(employer="A", title="T", start_date="Jan 2010", end_date="Dec 2014"),
            EmploymentEntry(employer="B", title="T", start_date="Jan 2015", end_date="Dec 2019"),
            EmploymentEntry(employer="C", title="T", start_date="2018", end_date="2022"),
        ],
    )
    assert EvidenceCorpus.build(profile).tenure == 12  # Jan 2010 - Dec 2022 is 12.9 years, rounded down


def test_b2_a_duration_inside_the_break_note_does_not_lift_the_cap() -> None:
    text = "EXPERIENCE\nCareer break: 10 years away from my career 2014 - 2024\n"
    corpus = _corpus(text, master=text)
    assert corpus.stated_years == []
    assert corpus.years_limit == 0
    assert "number" in _kinds("I bring 10 years of experience.", corpus)


def test_b2_a_break_is_evidence_text_labelled_as_a_break_not_a_role() -> None:
    document = parse_master_resume(BREAK_RESUME)
    profile = CandidateProfile(candidate_id="c", employment=document.employment)
    text = profile_evidence_text(profile)
    lines = text.split("\n")
    assert any(line.startswith("Career break:") and "parenting" in line for line in lines)
    assert not any(line.startswith("Role:") and "parenting" in line for line in lines)
    assert sum(line.startswith("Role:") for line in lines) == 2


# ---------------------------------------------------------------------------
# S1: title / employer order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "employer"),
    [
        ("Data Scientist", "Principal Financial Group"),
        ("Server", "Executive Car Wash"),
        ("Sous Chef", "The Driver Group"),
        ("Barista", "Coach"),
        ("Sales Manager", "Smith & Associates"),
        ("Cashier", "Acme Lead"),
        ("Owner", "Acme Consulting"),
    ],
)
def test_s1_title_first_lines_keep_their_order(title: str, employer: str) -> None:
    entries, _ = _experience(f"{title} | {employer} | 2015 - 2020")
    assert (entries[0].title, entries[0].employer) == (title, employer)
    assert not any("order guessed" in w for w in _warnings(f"{title} | {employer} | 2015 - 2020"))


@pytest.mark.parametrize(
    ("line", "title", "employer"),
    [
        ("Acme Consulting — Owner | 2015 - 2020", "Owner", "Acme Consulting"),
        ("Owner — Acme Consulting | 2015 - 2020", "Owner", "Acme Consulting"),
        ("Acme Sales Group | Account Executive | 2015 - 2020", "Account Executive", "Acme Sales Group"),
        ("Smith & Associates — Paralegal | 2015 - 2020", "Paralegal", "Smith & Associates"),
    ],
)
def test_s1_a_clear_title_after_an_untitled_employer_is_swapped(
    line: str, title: str, employer: str
) -> None:
    entries, _ = _experience(line)
    assert (entries[0].title, entries[0].employer) == (title, employer)


def test_s1_the_second_part_is_never_the_title_when_it_ends_like_a_company() -> None:
    entries, _ = _experience("Plains Dental — Regional Director Group | 2015 - 2020")
    assert (entries[0].title, entries[0].employer) == ("Plains Dental", "Regional Director Group")


@pytest.mark.parametrize(
    "text",
    [
        "Plains Dental | Scheduler | 2015 - 2020",
        "Scheduler | Plains Dental | 2015 - 2020",
        "Holiday Lite Source — Showroom Support\nLubbock, TX • 2022 – 2025",
    ],
)
def test_s1_a_guessed_order_is_reported(text: str) -> None:
    assert any("title/employer order guessed for" in w for w in _warnings(text))


def test_s1_a_certain_order_is_not_reported() -> None:
    assert not any(
        "order guessed" in w
        for w in _warnings("Holiday Lite Source — Sales Associate\nLubbock, TX • 2022 – 2025")
    )


# ---------------------------------------------------------------------------
# S2: only a bullet that plainly continues is joined
# ---------------------------------------------------------------------------


def test_s2_a_stacked_header_after_a_bullet_without_a_period_is_a_new_role() -> None:
    entries, unparsed = _experience(
        "Cashier | Plains | 2019 - 2021\n• Ran the register\n"
        "Acme Retail\nSales Associate\n2015 - 2019\n• Sold stuff"
    )
    assert unparsed == []
    assert entries[0].accomplishments == ["Ran the register"]
    assert (entries[1].employer, entries[1].title) == ("Acme Retail", "Sales Associate")
    assert (entries[1].start_date, entries[1].end_date) == ("2015", "2019")
    assert entries[1].accomplishments == ["Sold stuff"]


def test_s2_a_capitalised_line_after_a_short_bullet_is_not_merged() -> None:
    entries, unparsed = _experience(
        "Cashier | Plains | 2019 - 2021\n• Ran the register\nPromoted to shift lead in 2020"
    )
    assert entries[0].accomplishments == ["Ran the register"]
    assert unparsed == ["Promoted to shift lead in 2020"]


@pytest.mark.parametrize(
    ("bullet", "continuation", "joined"),
    [
        ("Ran the register and", "Managed the opening shift.", "Ran the register and Managed the opening shift."),
        ("Ran the register,", "Managed the opening shift.", "Ran the register, Managed the opening shift."),
        ("Ran the register", "and managed the opening shift.", "Ran the register and managed the opening shift."),
        (
            "Managed vendor relationships and negotiated annual purchasing terms",
            "Managed the budget.",
            "Managed vendor relationships and negotiated annual purchasing terms Managed the budget.",
        ),
    ],
)
def test_s2_bullets_that_plainly_continue_are_joined(
    bullet: str, continuation: str, joined: str
) -> None:
    entries, unparsed = _experience(
        f"Cashier | Plains | 2019 - 2021\n• {bullet}\n{continuation}\n"
    )
    assert entries[0].accomplishments == [joined]
    assert unparsed == []


# ---------------------------------------------------------------------------
# S3: header block
# ---------------------------------------------------------------------------

TAIL = "\n\nSKILLS\nExcel\n"


def _contact(text: str):
    document = parse_master_resume(text + TAIL)
    return document, document.contact


def test_s3_a_full_state_name_is_a_location_not_the_headline() -> None:
    document, contact = _contact("Morgan Ellison\nDallas, Texas • morgan@example.test\nSales Leader")
    assert document.headline == "Sales Leader"
    assert (contact.city, contact.state) == ("Dallas", "Texas")
    assert document.unparsed_lines == []


def test_s3_a_job_title_with_a_comma_is_a_headline_not_a_place() -> None:
    document, contact = _contact("Morgan Ellison\nDirector, IT\nDallas, TX | morgan@example.test")
    assert document.headline == "Director, IT"
    assert (contact.city, contact.state) == ("Dallas", "TX")


def test_s3_a_title_with_a_comma_and_no_location_leaves_the_city_empty() -> None:
    document, contact = _contact("Morgan Ellison\nDirector, IT\nmorgan@example.test")
    assert document.headline == "Director, IT"
    assert contact.city is None and contact.state is None


def test_s3_real_state_codes_and_provinces_still_work() -> None:
    _, contact = _contact("Morgan Ellison\nmorgan@example.test | Austin, TX 78701")
    assert (contact.city, contact.state, contact.postal_code) == ("Austin", "TX", "78701")
    _, contact = _contact("Morgan Ellison\nmorgan@example.test | Calgary, AB")
    assert (contact.city, contact.state) == ("Calgary", "AB")


# ---------------------------------------------------------------------------
# S5: a package stored before the new fields keeps the hash its approval is bound to
# ---------------------------------------------------------------------------

STORED_HASH = "3934635ff9876076bfe891f21b5334f58b1afeab95575b84edf7c72892849035"


def _stored_package() -> ApplicationPackageSchema:
    raw = (FIXTURES / "stored_package_main_3934635f.json").read_text(encoding="utf-8")
    return ApplicationPackageSchema.model_validate_json(raw)


def test_s5_a_package_stored_by_main_rehashes_to_its_approved_hash() -> None:
    """The fixture is a package JSON produced by main's code, before `is_break` and `minors`
    existed. Its recorded hash is the literal below; the branch must recompute exactly it."""

    stored = _stored_package()
    assert json.loads((FIXTURES / "stored_package_main_3934635f.json").read_text("utf-8"))[
        "content_hash"
    ] == STORED_HASH
    assert stored.content_hash == STORED_HASH
    assert content_hash(stored.semantic_payload()) == STORED_HASH


def test_s5_using_the_new_fields_does_change_the_hash() -> None:
    stored = _stored_package()
    with_break = stored.model_copy(
        update={"employment": [*stored.employment, EmploymentEntry(title="Career break", is_break=True)]}
    )
    flipped = stored.model_copy(
        update={
            "employment": [
                stored.employment[0].model_copy(update={"is_break": True}),
                *stored.employment[1:],
            ]
        }
    )
    minors = stored.model_copy(
        update={
            "education": [
                stored.education[0].model_copy(update={"minors": ["Biology"]}),
                *stored.education[1:],
            ]
        }
    )
    other = {content_hash(p.semantic_payload()) for p in (with_break, flipped, minors)}
    assert STORED_HASH not in other and len(other) == 3
    assert EducationEntry().minors == [] and EmploymentEntry().is_break is False


# ---------------------------------------------------------------------------
# S4: the dialog survives a stale close event
# ---------------------------------------------------------------------------


def test_s4_a_stale_close_event_does_not_cancel_the_next_question() -> None:
    probe = "document.querySelector('#probe').textContent"
    out = render(
        applications_page(),
        {"packages": []},
        "const first=ask('One?'); document.querySelector('#ask-ok').onclick(); await first;"
        "const second=ask('Two?'); let state='pending'; second.then(v=>{state='resolved:'+v});"
        "await new Promise(r=>setImmediate(r)); await new Promise(r=>setImmediate(r));"
        f"{probe}=state;"
        "document.querySelector('#ask-ok').onclick(); await second;",
        dialog="manual",
    )
    assert out["nodes"]["#probe"]["textContent"] == "pending"
    assert [s["text"] for s in out["shown"]] == ["One?", "Two?"]


def test_s4_the_fallback_without_show_modal_closes_by_removing_the_attribute() -> None:
    html = applications_page()
    assert "dialog.removeAttribute('open')" in html
    assert "if(!dialog.open)finish(" in html


# ---------------------------------------------------------------------------
# A location-and-dates line listing several places; a sentence under a role header
# ---------------------------------------------------------------------------

MULTI_CITY_HEADER = "Restaurant & Hospitality — Various Roles (Server, Shift Lead)"


@pytest.mark.parametrize(
    ("line", "location"),
    [
        ("Tempe, AZ; Honolulu, HI; El Paso, TX • 2001 – 2012", "Tempe, AZ; Honolulu, HI; El Paso, TX"),
        ("Tempe, AZ / Honolulu, HI • 2001 – 2012", "Tempe, AZ / Honolulu, HI"),
        ("Tempe, AZ | Honolulu, HI | 2001 – 2012", "Tempe, AZ | Honolulu, HI"),
        ("Tempe, AZ and Honolulu, HI • 2001 – 2012", "Tempe, AZ and Honolulu, HI"),
        ("2001 – 2012 • Tempe, AZ; Honolulu, HI; El Paso, TX", "Tempe, AZ; Honolulu, HI; El Paso, TX"),
    ],
)
def test_several_cities_and_dates_form_the_role_location_line(line: str, location: str) -> None:
    entries, unparsed = _experience(f"{MULTI_CITY_HEADER}\n{line}\n- Served guests.\n")
    assert unparsed == []
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.employer, entry.title) == ("Restaurant & Hospitality", "Various Roles (Server, Shift Lead)")
    assert entry.location == location
    assert (entry.start_date, entry.end_date) == ("2001", "2012")
    assert entry.accomplishments == ["Served guests."]


def test_a_parenthesised_title_suffix_is_part_of_the_title_in_either_order() -> None:
    for header in (
        "Restaurant & Hospitality — Various Roles (Server, Shift Lead)",
        "Various Roles (Server, Shift Lead) — Restaurant & Hospitality",
        "Restaurant & Hospitality | Server (Part-time)",
    ):
        entries, _ = _experience(f"{header}\nTempe, AZ • 2001 – 2012\n")
        assert entries[0].title in {"Various Roles (Server, Shift Lead)", "Server (Part-time)"}
        assert entries[0].employer == "Restaurant & Hospitality"
    assert not any("order guessed" in w for w in _warnings(f"{MULTI_CITY_HEADER}\nTempe, AZ • 2001 – 2012"))


def test_a_line_that_is_not_a_place_list_is_still_not_a_location_line() -> None:
    entries, _ = _experience(
        "Sales Associate — Holiday Lite Source\nTerritory Manager | Northstar | 2015 - 2019\n"
    )
    assert len(entries) == 2
    assert entries[0].start_date is None and entries[0].location is None


SUMMARY_SENTENCE = (
    "Office manager for three firms sharing one building: Alpha Law, Beta Partners, "
    "and Gamma Advisors"
)


@pytest.mark.parametrize(
    "header",
    [
        "Office Manager | Alpha Law | 2015 - 2020",
        "Office Manager — Alpha Law\nAmarillo, TX • 2015 – 2020",
    ],
)
def test_a_sentence_under_a_role_header_before_any_bullet_is_the_summary(header: str) -> None:
    entries, unparsed = _experience(f"{header}\n{SUMMARY_SENTENCE}\n- Ran the front desk.\n")
    assert unparsed == []
    assert entries[0].summary == SUMMARY_SENTENCE
    assert entries[0].accomplishments == ["Ran the front desk."]


def test_a_short_sentence_with_a_full_stop_is_also_the_summary() -> None:
    entries, unparsed = _experience("Office Manager | Alpha Law | 2015 - 2020\nRan the office.\n")
    assert entries[0].summary == "Ran the office." and unparsed == []


def test_a_sentence_after_bullets_still_goes_to_the_unread_list() -> None:
    entries, unparsed = _experience(
        f"Office Manager | Alpha Law | 2015 - 2020\n{SUMMARY_SENTENCE}\n"
        "- Ran the front desk.\nA stray sentence after the bullets.\n"
    )
    assert entries[0].summary == SUMMARY_SENTENCE
    assert entries[0].accomplishments == ["Ran the front desk."]
    assert unparsed == ["A stray sentence after the bullets."]


def test_a_second_role_header_is_not_swallowed_as_a_summary() -> None:
    entries, _ = _experience(
        "Office Manager | Alpha Law | 2015 - 2020\n"
        "Senior Regional Sales and Operations Manager for West Texas — Acme Retail Group\n"
        "Lubbock, TX • 2010 – 2015\n"
    )
    assert len(entries) == 2 and entries[0].summary is None


def test_the_whole_owner_layout_parses_without_loss() -> None:
    document = parse_master_resume(
        "Morgan Ellison\nmorgan@example.test\n\nEXPERIENCE\n"
        f"{MULTI_CITY_HEADER}\nTempe, AZ; Honolulu, HI; El Paso, TX • 2001 – 2012\n"
        "- Served guests and led shifts.\n\n"
        f"Office Manager | Alpha Law | 2015 - 2020\n{SUMMARY_SENTENCE}\n- Ran the front desk.\n\n"
        "SKILLS\nExcel\n"
    )
    assert document.unparsed_lines == []
    assert [e.location for e in document.employment] == ["Tempe, AZ; Honolulu, HI; El Paso, TX", None]
    assert document.employment[1].summary == SUMMARY_SENTENCE
    assert document.warnings == []
