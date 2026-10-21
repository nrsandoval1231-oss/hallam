"""Verified answers land in the right field, and verified memory reaches the form.

Everything runs against doubles built from the real captured Greenhouse form. No
browser, no network, no employer page.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from conftest_app import build_job, build_profile
from fake_pages import FakeFormPage
from sabra_automation.base import ApplicationForm, FormField
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter, split_name
from sabra_automation.lever_adapter import LeverApplicationAdapter
from sabra_documents.master_resume import parse_master_resume
from sabra_documents.package import PackageFactory
from sabra_domain.enums import SafetyStopKind, TruthLevel
from sabra_domain.schemas import (
    AnswerRecord,
    ApplicationAnswerSchema,
    ApplicationPackageSchema,
    EducationEntry,
    EmploymentEntry,
)
from sabra_engine.documents import DocumentService


def _answer(key: str, value: str, text: str | None = None) -> ApplicationAnswerSchema:
    return ApplicationAnswerSchema(
        question_key=key,
        question_text=text,
        answer=value,
        level=TruthLevel.VERIFIED,
        requires_user_input=False,
    )


def _package(**overrides: Any) -> ApplicationPackageSchema:
    base: dict[str, Any] = dict(
        opportunity_id="opp_match",
        candidate_id="cand_match",
        job_id="job_match",
        applicant_name="JORDAN A. RIVERA",
        applicant_email="jordan.rivera@example.test",
        applicant_phone="(214) 555-0142",
        applicant_country="United States",
        tailored_resume_markdown="# JORDAN A. RIVERA\n\n## Summary\nSales leader.\n",
        employment=[
            EmploymentEntry(
                employer="Meridian Aesthetics",
                title="Regional Sales Manager",
                start_date="Mar 2019",
                end_date="Present",
                is_current=True,
            )
        ],
        education=[],
    )
    base.update(overrides)
    return ApplicationPackageSchema(**base)


def _capture(base: dict, *extra: dict) -> dict:
    """The real captured form plus the extra questions a test needs."""

    capture = copy.deepcopy(base)
    capture["captcha"] = False
    capture["fields"] = [
        f
        for f in capture["fields"]
        # Drop the unnamed controls the enumerator could not identify.
        if f.get("id") or f.get("name")
    ] + [dict(item) for item in extra]
    return capture


def _question(
    ident: str,
    label: str,
    *,
    required: bool = False,
    tag: str = "input",
    type_: str = "text",
    options: list[str] | None = None,
) -> dict:
    return {
        "id": ident,
        "tag": tag,
        "type": type_,
        "required": required,
        "label": label,
        "options": options or [],
    }


def _open(capture: dict, tmp_path) -> tuple[GreenhouseApplicationAdapter, FakeFormPage, ApplicationForm]:
    page = FakeFormPage(capture, captcha=False)
    adapter = GreenhouseApplicationAdapter(
        dry_run=True,
        page_factory=lambda _url: page,
        settings=type("S", (), {"artifacts_dir": tmp_path})(),
    )
    form = adapter.inspect(build_job())
    return adapter, page, form


# ---------------------------------------------------------------------------
# 1. answers land in the right field
# ---------------------------------------------------------------------------


def test_start_date_answer_never_touches_the_employment_block(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture, _question("question_900", "When can you start?")
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(
        answers=[_answer("start_date", "Two weeks after offer", "When can you start?")]
    )

    prepared = adapter.prepare(package, form)

    assert page.filled["#question_900"] == "Two weeks after offer"
    # The history plan owns the employment block: it typed the month of the verified job.
    assert page.filled["#start-date-month-0"] == "3"
    assert "Two weeks after offer" not in [
        value for selector, value in page.filled.items() if selector != "#question_900"
    ]
    assert prepared.filled["start-date-month-0"] == "3"


def test_education_level_answer_does_not_fill_the_degree_input(
    greenhouse_form_capture, tmp_path
) -> None:
    """`degree--0` classifies as education_level, but it belongs to the education block."""

    capture = _capture(greenhouse_form_capture)
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("education_level", "Master's degree")])

    prepared = adapter.prepare(package, form)

    assert "#degree--0" not in page.filled
    assert "education_level" not in prepared.filled
    assert "Master's degree" not in page.filled.values()


def test_a_question_is_answered_by_its_wording_when_its_key_is_unknown(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("question_901", "What is your T-shirt size?*", required=True),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(
        answers=[_answer("shirt_size", "Large", "What is your T-shirt size?")]
    )

    prepared = adapter.prepare(package, form)

    assert page.filled["#question_901"] == "Large"
    assert "question_901" in prepared.filled
    assert not any("question_901" in item for item in prepared.unfilled_required)


def test_a_yes_answer_never_selects_a_longer_option(greenhouse_form_capture, tmp_path) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question(
            "question_902",
            "Will you now or in the future require sponsorship?*",
            required=True,
            tag="select",
            type_="select",
            options=["Yes – I will require sponsorship", "No"],
        ),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("sponsorship", "Yes")])

    report = adapter.validate(package, form)

    assert not report.ok
    joined = " ".join(report.unknown_required_fields)
    assert "question_902" in joined and "no option exactly matches" in joined
    assert any(s.kind == SafetyStopKind.UNKNOWN_REQUIRED_QUESTION for s in report.stops)

    prepared = adapter.prepare(package, form)
    assert "#question_902" not in page.filled, "a substring match must not pick an option"
    assert any("question_902" in item for item in prepared.unfilled_required)
    assert "question_902" in prepared.skipped


def test_an_exact_option_is_selected_by_label_ignoring_case_and_spacing(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question(
            "question_903",
            "Will you now or in the future require sponsorship?*",
            required=True,
            tag="select",
            type_="select",
            options=["Yes – I will require sponsorship", "No"],
        ),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("sponsorship", "  no ")])

    assert adapter.validate(package, form).mapped_fields.get("question_903") == "sponsorship"
    prepared = adapter.prepare(package, form)

    assert page.filled["#question_903"] == "No", "select_option, not fill, with the label"
    assert not any("question_903" in item for item in prepared.unfilled_required)


def test_a_select_is_never_written_with_fill(greenhouse_form_capture, tmp_path) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question(
            "question_904",
            "What is your T-shirt size?",
            tag="select",
            type_="select",
            options=["Small", "Large"],
        ),
    )
    adapter, page, form = _open(capture, tmp_path)
    calls: list[tuple[str, str]] = []
    original_fill = page.fill

    def tracking_fill(selector: str, value: str) -> None:
        calls.append((selector, value))
        original_fill(selector, value)

    page.fill = tracking_fill  # type: ignore[method-assign]
    package = _package(answers=[_answer("shirt_size", "Large", "What is your T-shirt size?")])
    adapter.prepare(package, form)

    assert page.filled["#question_904"] == "Large"
    assert all(selector != "#question_904" for selector, _value in calls)


def test_lever_applies_the_same_option_rules() -> None:
    adapter = LeverApplicationAdapter(dry_run=True)
    select = FormField(
        name="cards[1][field0]",
        label="Will you require sponsorship?",
        kind="select",
        required=True,
        options=["Yes – I will require sponsorship", "No"],
        selector='[name="cards[1][field0]"]',
        question_key="sponsorship",
    )
    form = ApplicationForm(
        url="https://jobs.lever.co/x/1/apply",
        fields=[select],
        submit_selector='button[type="submit"]',
    )
    package = _package(answers=[_answer("sponsorship", "Yes")])
    report = adapter.validate(package, form)
    assert not report.ok
    assert "no option exactly matches" in " ".join(report.unknown_required_fields)

    page = FakeFormPage({"fields": [], "title": "Apply"}, captcha=False)
    filled, skipped = adapter.apply_to_page(page, package, form)
    assert '[name="cards[1][field0]"]' not in page.filled
    assert select.name in skipped
    assert select.name not in filled

    exact = _package(answers=[_answer("sponsorship", "No")])
    page = FakeFormPage({"fields": [], "title": "Apply"}, captcha=False)
    filled, _skipped = adapter.apply_to_page(page, exact, form)
    assert page.filled['[name="cards[1][field0]"]'] == "No"


def test_lever_does_not_type_answers_into_its_identity_inputs() -> None:
    adapter = LeverApplicationAdapter(dry_run=True)
    form = ApplicationForm(
        url="https://jobs.lever.co/x/1/apply",
        fields=[
            FormField(name="name", label="Full name", required=True, selector="#name"),
            FormField(
                name="org", label="Current company", required=False, selector="#org",
                question_key="current_employer",
            ),
        ],
        submit_selector='button[type="submit"]',
    )
    package = _package(answers=[_answer("current_employer", "Some Other Company")])
    page = FakeFormPage({"fields": [{"id": "name"}, {"id": "org"}], "title": "Apply"}, captcha=False)
    adapter.apply_to_page(page, package, form)
    assert page.filled["#org"] == "Meridian Aesthetics"
    assert "Some Other Company" not in page.filled.values()


# ---------------------------------------------------------------------------
# 2. verified answer memory reaches the form
# ---------------------------------------------------------------------------


def _built_package(profile, fixture_resume_text):
    master = parse_master_resume(fixture_resume_text)
    built = PackageFactory().build(
        profile=profile,
        master=master,
        job=build_job(),
        master_resume_id="resume_1",
        job_id="job_1",
        opportunity_id="opp_1",
        questions=DocumentService._default_questions(),
    )
    return built.schema


def _verified(key: str, value: str, text: str | None = None) -> AnswerRecord:
    return AnswerRecord(
        question_key=key,
        question_text=text,
        value=value,
        level=TruthLevel.VERIFIED,
        evidence=["owner_verified"],
    )


def test_the_package_carries_every_verified_memory_answer_and_nothing_else(
    fixture_resume_text,
) -> None:
    profile = build_profile("cand_memory")
    profile.answer_memory.update(
        {
            "us_citizen": _verified("us_citizen", "Yes", "Are you a U.S. citizen?"),
            "spanish": _verified("spanish", "No"),
            "prior_employer": _verified("prior_employer", "No"),
            "resume_headline": _verified("resume_headline", "Aesthetics sales leader"),
            "non_compete": AnswerRecord(
                question_key="non_compete", value="No", level=TruthLevel.INFERRED
            ),
            "salary_expectations": AnswerRecord(
                question_key="salary_expectations",
                value="$130,000",
                level=TruthLevel.INFERRED,
                sensitive=True,
            ),
        }
    )
    schema = _built_package(profile, fixture_resume_text)
    typed = {a.question_key: a for a in schema.answers if not a.requires_user_input}

    for key in ("us_citizen", "spanish", "prior_employer", "resume_headline"):
        assert typed[key].answer, key
        assert typed[key].level is TruthLevel.VERIFIED
    assert typed["us_citizen"].question_text == "Are you a U.S. citizen?"
    assert "non_compete" not in typed, "an unverified answer is never fillable"
    assert "salary_expectations" not in typed, "nor is an unverified sensitive one"
    assert not any(
        a.question_key == "non_compete" for a in schema.answers
    ), "unverified entries do not ride on the package at all"
    # It still blocks completeness, as before.
    assert "What are your salary expectations?" in schema.unresolved_questions
    # The answers are inside the approval hash.
    from sabra_domain.identity import content_hash

    assert schema.content_hash == content_hash(schema.semantic_payload())


def test_a_verified_us_citizen_answer_fills_the_citizenship_question(
    greenhouse_form_capture, fixture_resume_text, tmp_path
) -> None:
    profile = build_profile("cand_citizen")
    profile.answer_memory["us_citizen"] = _verified(
        "us_citizen", "Yes", "Are you a U.S. citizen?"
    )
    package = _built_package(profile, fixture_resume_text)
    capture = _capture(
        greenhouse_form_capture,
        _question("question_950", "Are you a U.S. citizen?*", required=True),
    )
    adapter, page, form = _open(capture, tmp_path)

    report = adapter.validate(package, form)
    citizenship = [item for item in report.unknown_required_fields if "question_950" in item]
    assert not citizenship, report.unknown_required_fields
    assert not any("citizen" in q.lower() for q in report.unanswered_questions)

    prepared = adapter.prepare(package, form)
    assert page.filled["#question_950"] == "Yes"
    assert not any("question_950" in item for item in prepared.unfilled_required)


def test_an_unverified_us_citizen_answer_leaves_the_question_unresolved_and_blocks(
    greenhouse_form_capture, fixture_resume_text, tmp_path
) -> None:
    profile = build_profile("cand_citizen_unverified")
    profile.answer_memory["us_citizen"] = AnswerRecord(
        question_key="us_citizen",
        question_text="Are you a U.S. citizen?",
        value="Yes",
        level=TruthLevel.INFERRED,
        evidence=["owner_recorded"],
    )
    package = _built_package(profile, fixture_resume_text)
    capture = _capture(
        greenhouse_form_capture,
        _question("question_951", "Are you a U.S. citizen?*", required=True),
    )
    adapter, page, form = _open(capture, tmp_path)

    report = adapter.validate(package, form)
    assert not report.ok
    assert any("citizen" in q.lower() for q in report.unanswered_questions), report.as_dict()
    assert any(s.kind == SafetyStopKind.UNKNOWN_REQUIRED_QUESTION for s in report.stops)

    prepared = adapter.prepare(package, form)
    assert "#question_951" not in page.filled, "an unverified answer is never typed"
    assert any("question_951" in item for item in prepared.unfilled_required)


def test_the_extra_keys_the_answers_page_offers_are_all_consumed(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("question_960", "Are you a U.S. citizen?*", required=True),
        _question("question_961", "Do you speak Spanish?*", required=True),
        _question("question_962", "Are you 18 or older?*", required=True),
    )
    # The captured form already asks about 18, prior employment and non-compete.
    capture["fields"] = [f for f in capture["fields"] if f.get("id") != "question_19843173004"]
    adapter, page, form = _open(capture, tmp_path)
    package = _package(
        answers=[
            _answer("us_citizen", "Yes", "Are you a U.S. citizen?"),
            _answer("spanish", "No", "Do you speak Spanish?"),
            _answer("age_eligibility", "Yes", "Are you 18 or older?"),
            _answer(
                "non_compete", "No", "Have you signed a non-compete with your current employer?"
            ),
            _answer("prior_employer", "No", "Have you previously worked at Natera?"),
        ],
    )
    adapter.prepare(package, form)

    assert page.filled["#question_960"] == "Yes"
    assert page.filled["#question_961"] == "No"
    assert page.filled["#question_962"] == "Yes"
    assert page.filled["#question_19843178004"] == "No"  # non-compete
    assert page.filled["#question_19843174004"] == "No"  # previously worked at Natera


# ---------------------------------------------------------------------------
# 5d. names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("full", "first", "last"),
    [
        ("JORDAN A. RIVERA", "JORDAN", "RIVERA"),
        ("Jordan A Rivera", "Jordan", "Rivera"),
        ("Jordan Rivera", "Jordan", "Rivera"),
        ("Mary Jo Smith", "Mary", "Jo Smith"),
        ("Cher", "Cher", None),
        ("A. J. Brown", "A.", "Brown"),
    ],
)
def test_split_name_treats_a_single_letter_as_a_middle_initial(full, first, last) -> None:
    assert split_name(full) == (first, last)


def test_structured_names_win_over_splitting_the_full_name() -> None:
    package = _package(
        applicant_name="JORDAN A. RIVERA",
        applicant_first_name="Jordan",
        applicant_last_name="Rivera-Smith",
    )
    values = GreenhouseApplicationAdapter._identity_values(package)
    assert (values["first_name"], values["last_name"]) == ("Jordan", "Rivera-Smith")

    plain = GreenhouseApplicationAdapter._identity_values(_package())
    assert (plain["first_name"], plain["last_name"]) == ("JORDAN", "RIVERA")


def test_a_profile_with_structured_names_puts_them_on_the_package_and_in_the_hash(
    fixture_resume_text,
) -> None:
    profile = build_profile("cand_names")
    without = _built_package(profile, fixture_resume_text)
    assert without.applicant_first_name is None
    assert "applicant_first_name" not in without.semantic_payload(), (
        "an unset structured name must not change the hash of a package built before it existed"
    )

    profile.identity.first_name = "Jordan"
    profile.identity.last_name = "Rivera"
    with_names = _built_package(profile, fixture_resume_text)
    assert (with_names.applicant_first_name, with_names.applicant_last_name) == (
        "Jordan",
        "Rivera",
    )
    assert with_names.content_hash != without.content_hash


def test_education_entry_is_still_typed_into_the_education_block(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(greenhouse_form_capture)
    adapter, page, form = _open(capture, tmp_path)
    package = _package(
        education=[
            EducationEntry(
                institution="University of North Texas",
                degree="Bachelor of Business Administration",
            )
        ],
        answers=[_answer("education_level", "Bachelor's")],
    )
    adapter.prepare(package, form)
    assert page.filled["#degree--0"] == "Bachelor of Business Administration"
