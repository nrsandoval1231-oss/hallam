"""Wording the owner recorded reaches the package, and location values are typed only
into location inputs.

Packages are built through `PackageFactory` from real answer memory, and the form is the
real captured Greenhouse form, so these tests cover the path the owner actually takes.
"""

from __future__ import annotations

from conftest_app import build_profile
from sabra_domain.enums import TruthLevel
from sabra_domain.schemas import AnswerRecord
from test_answers_reach_form import _built_package, _capture, _open, _question

NATERA_SPONSORSHIP = "Do you now or will you in the future require sponsorship?"
SPONSORSHIP_FIELD = "question_19843177004"


def _memory(key: str, value: str, wording: str | None, level=TruthLevel.VERIFIED) -> AnswerRecord:
    return AnswerRecord(
        question_key=key,
        question_text=wording,
        value=value,
        level=level,
        evidence=["owner_verified"] if level is TruthLevel.VERIFIED else ["owner_recorded"],
    )


# ---------------------------------------------------------------------------
# 1. recorded wording for the seven default questions
# ---------------------------------------------------------------------------


def test_sponsorship_recorded_with_the_boards_wording_fills_the_real_natera_field(
    greenhouse_form_capture, fixture_resume_text, tmp_path
) -> None:
    profile = build_profile("cand_wording")
    profile.answer_memory["sponsorship"] = _memory("sponsorship", "No", NATERA_SPONSORSHIP)
    package = _built_package(profile, fixture_resume_text)

    # Both wordings ride on the package: our default for the resolver, the board's for the form.
    sponsorship = [a for a in package.answers if a.question_key == "sponsorship"]
    assert len(sponsorship) == 2
    assert {a.question_text for a in sponsorship} == {
        NATERA_SPONSORSHIP,
        "Will you now or in the future require sponsorship for employment visa status?",
    }
    assert all(a.answer == "No" and a.level is TruthLevel.VERIFIED for a in sponsorship)

    adapter, page, form = _open(_capture(greenhouse_form_capture), tmp_path)
    report = adapter.validate(package, form)
    assert not any(SPONSORSHIP_FIELD in item for item in report.unknown_required_fields)
    assert not any("sponsorship" in q.lower() for q in report.unanswered_questions)

    prepared = adapter.prepare(package, form)
    assert page.filled[f"#{SPONSORSHIP_FIELD}"] == "No"
    assert not any(SPONSORSHIP_FIELD in item for item in prepared.unfilled_required)


def test_sponsorship_recorded_without_wording_stays_unresolved_with_the_reason(
    greenhouse_form_capture, fixture_resume_text, tmp_path
) -> None:
    profile = build_profile("cand_no_wording")
    profile.answer_memory["sponsorship"] = _memory("sponsorship", "No", None)
    package = _built_package(profile, fixture_resume_text)
    assert len([a for a in package.answers if a.question_key == "sponsorship"]) == 1

    adapter, page, form = _open(_capture(greenhouse_form_capture), tmp_path)
    report = adapter.validate(package, form)
    refused = [item for item in report.unknown_required_fields if SPONSORSHIP_FIELD in item]
    assert refused and "exact question wording" in refused[0]
    prepared = adapter.prepare(package, form)
    assert f"#{SPONSORSHIP_FIELD}" not in page.filled
    assert any(SPONSORSHIP_FIELD in item for item in prepared.unfilled_required)


def test_the_same_wording_is_not_duplicated_and_unverified_wording_is_never_added(
    fixture_resume_text,
) -> None:
    default_wording = (
        "Will you now or in the future require sponsorship for employment visa status?"
    )
    profile = build_profile("cand_same")
    profile.answer_memory["sponsorship"] = _memory("sponsorship", "No", default_wording + "*")
    assert len(
        [a for a in _built_package(profile, fixture_resume_text).answers
         if a.question_key == "sponsorship"]
    ) == 1

    unverified = build_profile("cand_unverified")
    unverified.answer_memory["sponsorship"] = _memory(
        "sponsorship", "No", NATERA_SPONSORSHIP, level=TruthLevel.INFERRED
    )
    package = _built_package(unverified, fixture_resume_text)
    assert not [
        a for a in package.answers if a.question_text == NATERA_SPONSORSHIP
    ], "a recorded but unverified answer never rides on a package"


def test_the_recorded_wording_is_inside_the_approval_hash(fixture_resume_text) -> None:
    without = build_profile("cand_hash")
    without.answer_memory["sponsorship"] = _memory("sponsorship", "No", None)
    with_wording = build_profile("cand_hash")
    with_wording.answer_memory["sponsorship"] = _memory("sponsorship", "No", NATERA_SPONSORSHIP)
    first = _built_package(without, fixture_resume_text)
    second = _built_package(with_wording, fixture_resume_text)
    assert first.content_hash != second.content_hash


# ---------------------------------------------------------------------------
# 3. location values are typed only into location inputs
# ---------------------------------------------------------------------------


def _located_package(**overrides):
    from test_answers_reach_form import _package

    fields = dict(applicant_city="Dallas", applicant_state="TX", applicant_postal_code="75201")
    fields.update(overrides)
    return _package(**fields)


def test_a_work_location_question_does_not_get_the_home_city(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("question_work_city", "What city would you like to work in?*", required=True),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _located_package()

    report = adapter.validate(package, form)
    refused = [item for item in report.unknown_required_fields if "question_work_city" in item]
    assert refused and "exact question wording" in refused[0]
    prepared = adapter.prepare(package, form)
    assert "#question_work_city" not in page.filled
    assert any("question_work_city" in item for item in prepared.unfilled_required)


def test_the_home_city_is_typed_into_a_city_input_recognised_by_its_id(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("candidate-location", "Location (City)*", required=True),
        _question("zip", "ZIP*", required=True),
    )
    # An input that declares itself by autocomplete rather than id.
    state = _question("question_state_field", "Region*", required=True)
    state["autocomplete"] = "address-level1"
    capture["fields"].append(state)
    adapter, page, form = _open(capture, tmp_path)
    package = _located_package()

    report = adapter.validate(package, form)
    for name in ("candidate-location", "zip", "question_state_field"):
        assert not any(name in item for item in report.unknown_required_fields), name
    adapter.prepare(package, form)
    assert page.filled["#candidate-location"] == "Dallas"
    assert page.filled["#zip"] == "75201"
    assert page.filled["#question_state_field"] == "TX"


def test_a_city_question_is_typed_when_its_wording_was_recorded(
    greenhouse_form_capture, tmp_path
) -> None:
    from test_answers_reach_form import _answer

    capture = _capture(greenhouse_form_capture)
    adapter, page, form = _open(capture, tmp_path)
    package = _located_package(
        answers=[_answer("city", "Dallas", "What city do you currently live in?")]
    )
    adapter.prepare(package, form)
    assert page.filled["#question_19843180004"] == "Dallas"


def test_a_verified_city_answer_is_typed_into_a_city_input_without_recorded_wording(
    greenhouse_form_capture, tmp_path
) -> None:
    from test_answers_reach_form import _answer

    capture = _capture(
        greenhouse_form_capture, _question("candidate-location", "Location*", required=True)
    )
    adapter, page, form = _open(capture, tmp_path)
    adapter.prepare(_located_package(applicant_city=None, answers=[_answer("city", "Austin")]), form)
    assert page.filled["#candidate-location"] == "Austin"


def test_the_employer_bridge_is_guarded_the_same_way(greenhouse_form_capture, tmp_path) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("question_prior", "Who was your most recent employer before this one?"),
        _question("employer", "Employer"),
    )
    adapter, page, form = _open(capture, tmp_path)
    adapter.prepare(_located_package(), form)
    assert page.filled["#employer"] == "Meridian Aesthetics"
    assert "#question_prior" not in page.filled
