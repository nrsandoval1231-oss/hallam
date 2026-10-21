"""Guards on which verified answer may be typed into which question.

A phrase catalogue classifies questions, and a phrase also appears in questions it does
not answer. These tests pin the rules that keep a verified answer out of those.
"""

from __future__ import annotations

from sabra_automation.answer_match import OPTION_READ_LIMIT, norm_text, option_problem
from sabra_automation.base import FormField
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_documents.answers import SENSITIVE_KEYS, classify_question
from sabra_domain.enums import SafetyStopKind, TruthLevel
from sabra_domain.schemas import ApplicationAnswerSchema
from test_answers_reach_form import _answer, _capture, _open, _package, _question

# ---------------------------------------------------------------------------
# 4. wording comparison keeps the symbols that change a question's meaning
# ---------------------------------------------------------------------------


def test_norm_text_keeps_symbols_and_strips_only_form_decoration() -> None:
    assert norm_text("C++") != norm_text("C#") != norm_text("C")
    assert norm_text("C++") != norm_text("C")
    assert norm_text("Are you 18+?") != norm_text("Are you 18?")
    assert norm_text("Salary in £") != norm_text("Salary in €") != norm_text("Salary in")
    assert norm_text("Growth %") != norm_text("Growth")
    # Decoration around the question is not part of it.
    assert norm_text("  Are you 18 or older?* ") == norm_text("are   you 18 OR older")
    assert norm_text("Postal Code *") == norm_text("postal code:")
    assert norm_text("Ｃ＋＋") == norm_text("C++"), "NFKC folds full-width forms"


def test_questions_that_differ_only_by_a_symbol_get_their_own_answers(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("question_c_plus", "Years of C++?*"),
        _question("question_c_sharp", "Years of C#?*"),
        _question("question_c", "Years of C?*"),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(
        answers=[
            _answer("years_c_sharp", "7", "Years of C#?"),
            _answer("years_c", "2", "Years of C?"),
        ]
    )
    adapter.prepare(package, form)
    assert page.filled["#question_c_sharp"] == "7"
    assert page.filled["#question_c"] == "2"
    assert "#question_c_plus" not in page.filled


def test_age_18_plus_is_not_the_age_18_question(greenhouse_form_capture, tmp_path) -> None:
    capture = _capture(greenhouse_form_capture, _question("question_age", "Are you 18+?*"))
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("shirt_size", "Large", "Are you 18?")])
    adapter.prepare(package, form)
    assert "#question_age" not in page.filled


# ---------------------------------------------------------------------------
# 5. a catalogue key alone does not put an answer into a free-text field
# ---------------------------------------------------------------------------


def test_visa_type_question_does_not_get_the_citizenship_answer(
    greenhouse_form_capture, tmp_path
) -> None:
    label = "If you are not a U.S. citizen, what is your visa type?*"
    assert classify_question(label) == "us_citizen", "the catalogue does match it by phrase"
    capture = _capture(greenhouse_form_capture, _question("question_visa", label, required=True))
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("us_citizen", "Yes", "Are you a U.S. citizen?")])

    report = adapter.validate(package, form)
    assert not report.ok
    assert any("question_visa" in item for item in report.unknown_required_fields)
    assert any(s.kind == SafetyStopKind.UNKNOWN_REQUIRED_QUESTION for s in report.stops)

    prepared = adapter.prepare(package, form)
    assert "#question_visa" not in page.filled
    assert any("question_visa" in item for item in prepared.unfilled_required)


def test_a_spouses_citizenship_question_does_not_get_the_owners_answer(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("question_spouse", "Is your spouse a US citizen?*", required=True),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("us_citizen", "Yes", "Are you a U.S. citizen?")])
    assert not adapter.validate(package, form).ok
    adapter.prepare(package, form)
    assert "#question_spouse" not in page.filled


def test_spanish_speaking_customers_is_not_the_do_you_speak_spanish_question(
    greenhouse_form_capture, tmp_path
) -> None:
    label = "Are you comfortable working with Spanish-speaking customers?*"
    assert classify_question(label) is None
    capture = _capture(greenhouse_form_capture, _question("question_es", label, required=True))
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("spanish", "No", "Do you speak Spanish?")])

    report = adapter.validate(package, form)
    assert any("question_es" in item for item in report.unknown_required_fields)
    adapter.prepare(package, form)
    assert "#question_es" not in page.filled
    # The question that really is about speaking Spanish still classifies.
    assert classify_question("Do you speak Spanish?") == "spanish"


def test_hispanic_latino_is_its_own_question_not_race(greenhouse_form_capture, tmp_path) -> None:
    assert classify_question("Are you Hispanic/Latino?") == "hispanic_ethnicity"
    assert classify_question("Race") == "race"
    assert "hispanic_ethnicity" in SENSITIVE_KEYS

    capture = _capture(greenhouse_form_capture)
    field = next(f for f in capture["fields"] if f.get("id") == "hispanic_ethnicity")
    assert field["label"] == "Are you Hispanic/Latino?", "the real fixture asks it"
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("race", "Asian"), _answer("ethnicity", "Not specified")])
    adapter.prepare(package, form)
    assert "#hispanic_ethnicity" not in page.filled
    assert "Asian" not in page.filled.values()


def test_a_key_match_is_typed_into_a_yes_no_drop_down(greenhouse_form_capture, tmp_path) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question(
            "question_cit",
            "Are you a citizen of the United States?*",
            required=True,
            tag="select",
            type_="select",
            options=["Yes", "No"],
        ),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("us_citizen", "yes")])
    assert adapter.validate(package, form).mapped_fields.get("question_cit") == "us_citizen"
    adapter.prepare(package, form)
    assert page.filled["#question_cit"] == "Yes"


def test_a_free_text_answer_is_typed_when_the_recorded_wording_matches(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("question_visa2", "If you are not a U.S. citizen, what is your visa type?*"),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(
        answers=[
            _answer(
                "visa_type", "N/A", "If you are not a U.S. citizen, what is your visa type?"
            )
        ]
    )
    adapter.prepare(package, form)
    assert page.filled["#question_visa2"] == "N/A"


def test_a_non_yes_no_answer_is_not_chosen_from_a_drop_down_by_key(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question(
            "question_sal",
            "What is your desired salary?*",
            required=True,
            tag="select",
            type_="select",
            options=["$100,000", "$130,000"],
        ),
    )
    adapter, page, form = _open(capture, tmp_path)
    package = _package(answers=[_answer("salary_expectations", "$130,000")])
    report = adapter.validate(package, form)
    assert not report.ok
    assert any("exact question wording" in item for item in report.unknown_required_fields)
    adapter.prepare(package, form)
    assert "#question_sal" not in page.filled


# ---------------------------------------------------------------------------
# 6. long option lists
# ---------------------------------------------------------------------------


def test_a_country_in_a_300_option_list_is_found(greenhouse_form_capture, tmp_path) -> None:
    options = [f"Country {index:03d}" for index in range(300)]
    options[250] = "United States"
    capture = _capture(greenhouse_form_capture)
    country = next(f for f in capture["fields"] if f.get("id") == "country")
    country.update(tag="select", type="select", options=options)
    adapter, page, form = _open(capture, tmp_path)
    package = _package()

    mapped = adapter.validate(package, form).mapped_fields
    assert mapped.get("country") == "identity"
    adapter.prepare(package, form)
    assert page.filled["#country"] == "United States"


def test_the_page_reader_no_longer_cuts_option_lists_at_eighty() -> None:
    from sabra_automation.greenhouse_adapter import ENUMERATE_FIELDS_JS

    assert ".slice(0, 80)" not in ENUMERATE_FIELDS_JS
    assert f".slice(0, {OPTION_READ_LIMIT})" in ENUMERATE_FIELDS_JS


def test_a_list_that_hit_the_read_limit_says_it_may_be_cut() -> None:
    options = [f"Option {index}" for index in range(OPTION_READ_LIMIT)]
    field = FormField(name="x", kind="select", options=options)
    assert "may be cut" in (option_problem(field, "United States") or "")
    short = FormField(name="x", kind="select", options=options[:10])
    assert "may be cut" not in (option_problem(short, "United States") or "")


# ---------------------------------------------------------------------------
# 7. "verified" is checked, not assumed
# ---------------------------------------------------------------------------


def test_an_inferred_answer_is_never_typed_even_with_requires_user_input_false(
    greenhouse_form_capture, tmp_path
) -> None:
    capture = _capture(
        greenhouse_form_capture,
        _question("question_t", "What is your T-shirt size?*", required=True),
    )
    adapter, page, form = _open(capture, tmp_path)
    inferred = ApplicationAnswerSchema(
        question_key="shirt_size",
        question_text="What is your T-shirt size?",
        answer="Large",
        level=TruthLevel.INFERRED,
        requires_user_input=False,
    )
    unknown = inferred.model_copy(update={"level": TruthLevel.UNKNOWN})
    for answer in (inferred, unknown):
        page.filled.clear()
        package = _package(answers=[answer])
        report = adapter.validate(package, form)
        assert not report.ok
        prepared = adapter.prepare(package, form)
        assert "#question_t" not in page.filled
        assert any("question_t" in item for item in prepared.unfilled_required)

    verified = inferred.model_copy(update={"level": TruthLevel.VERIFIED})
    adapter.prepare(_package(answers=[verified]), form)
    assert page.filled["#question_t"] == "Large"


def test_an_inferred_identity_answer_is_not_used() -> None:
    package = _package(
        applicant_email=None,
        applicant_phone=None,
        answers=[
            ApplicationAnswerSchema(
                question_key="email",
                answer="guess@example.test",
                level=TruthLevel.INFERRED,
                requires_user_input=False,
            ),
            ApplicationAnswerSchema(
                question_key="phone",
                answer="(214) 555-0000",
                level=TruthLevel.VERIFIED,
                requires_user_input=False,
            ),
        ],
    )
    values = GreenhouseApplicationAdapter._identity_values(package)
    assert "email" not in values
    assert values["phone"] == "(214) 555-0000"


def test_a_memory_answer_does_not_override_the_structured_package_name() -> None:
    package = _package(
        applicant_first_name="Jordan",
        applicant_last_name="Rivera-Smith",
        answers=[
            _answer("first_name", "Someone"),
            _answer("last_name", "Else"),
            _answer("preferred_name", "JR"),
        ],
    )
    values = GreenhouseApplicationAdapter._identity_values(package)
    assert (values["first_name"], values["last_name"]) == ("Jordan", "Rivera-Smith")
    assert values["preferred_name"] == "JR"

    # Without a structured name, a verified memory answer is the owner's explicit choice.
    split_only = _package(answers=[_answer("first_name", "Jordan")])
    assert GreenhouseApplicationAdapter._identity_values(split_only)["first_name"] == "Jordan"
