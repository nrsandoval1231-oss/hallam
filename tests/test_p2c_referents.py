"""Who a sentence is about decides which evidence may support it.

A sentence is a candidate sentence unless it is clearly about the job (it names the
company or role and nothing in it points at the candidate). The first-person-only
version of this rule let third-person and "we" sentences borrow the company's figures.
Also covered: what counts as stated experience, role-bound durations, and numbers that
must be attached to what they count.
"""

from __future__ import annotations

import test_p2c_support as sup
from sabra_documents.generate import FitNarrative, PackageGenerator
from sabra_documents.master_resume import parse_master_resume
from sabra_documents.package import PackageFactory
from sabra_documents.truth_gate import ClaimAudit, EvidenceCorpus, deterministic_check
from sabra_llm.fake import FakeProvider

RESEARCH = {"verified_facts": ["The company operates 40 clinics in 12 states and grew 25% last year."]}


def corpus(*, prof=None, master="default", research=RESEARCH, **job_kwargs) -> EvidenceCorpus:
    return EvidenceCorpus.build(
        prof or sup.profile(),
        master_text=sup.resume_text() if master == "default" else master,
        job=sup.job(**job_kwargs),
        research=research,
    )


def messages(text: str, tier: str = "letter", **kwargs) -> list[str]:
    return [v.message for v in deterministic_check(text, corpus(**kwargs), tier)]


def kinds(text: str, tier: str = "candidate", **kwargs) -> set[str]:
    return {v.kind for v in deterministic_check(text, corpus(**kwargs), tier)}


# ---------------------------------------------------------------------------
# Referents: the company tier is for sentences about the job only
# ---------------------------------------------------------------------------


def test_third_person_sentences_about_the_candidate_get_the_candidate_tier() -> None:
    for text in (
        "The candidate has managed 40 clinics across 12 states for Meridian Aesthetics.",
        "The candidate managed 40 clinics across 12 states.",
        "The candidate grew revenue 25% last year.",
        "The candidate's team covered 12 states.",
        "He ran 40 clinics.",
        "She grew the region 25%.",
        "Their territory spanned 12 states.",
    ):
        assert messages(text), text


def test_we_and_our_sentences_are_candidate_sentences() -> None:
    for text in (
        "We grew the territory 40%.",
        "We grew the territory to 40 clinics.",
        "Our team expanded into 12 states.",
        "At Meridian Aesthetics we grew the territory to 40 clinics across 12 states.",
        "That was a result for us in 12 states.",
    ):
        assert messages(text), text


def test_a_sentence_about_the_job_may_use_the_company_tier() -> None:
    assert messages("The company operates 40 clinics in 12 states.") == []
    assert messages("This role covers 12 states.") == []
    assert messages("Revance Therapeutics operates 40 clinics.") == []
    # ... but a sentence that also points at the candidate may not
    assert messages("The company operates 40 clinics and I admire that scale.")
    assert messages("The role covers 12 states, which suits the candidate.")
    # and a percentage about the company is never usable
    assert messages("The company grew 25% last year.")


def test_a_sentence_about_neither_is_checked_as_a_candidate_sentence() -> None:
    assert messages("That scale of 40 clinics is impressive.")


def test_posting_terms_are_checked_in_every_candidate_sentence() -> None:
    jb = {"requirements": "- Experience with neurotoxins and dermal fillers"}
    third = messages("The candidate brings neurotoxin and dermal filler experience.", **jb)
    assert any("neurotoxin" in m for m in third)
    first = messages("I bring neurotoxin and dermal filler experience.", **jb)
    assert any("neurotoxin" in m for m in first)
    we = messages("We sold dermal fillers.", **jb)
    assert any("dermal" in m for m in we)
    # a sentence that is about the posting may name its terms
    assert messages("The posting asks for neurotoxin and dermal filler experience.", **jb) == []


def test_the_candidate_only_tier_gives_the_company_nothing() -> None:
    assert kinds("The company operates 40 clinics in 12 states.")
    assert kinds("This role covers 12 states.")


def test_the_narrative_borrowing_a_research_figure_does_not_ship() -> None:
    narrative = FitNarrative(
        why_this_job="The candidate has managed 40 clinics across 12 states for Meridian "
        "Aesthetics. That scale matches this regional role.",
        gap_analysis="The posting asks for product-launch experience; the record shows one "
        "injectable portfolio launch, so be ready to discuss it openly.",
    )
    labelled_job_fact = ClaimAudit(
        claims=[
            sup.claim(
                "job_fact", "managed 40 clinics across 12 states", "operates 40 clinics in 12 states"
            ),
            sup.claim("job_fact", "scale matches this regional role", "Regional Sales Manager"),
            sup.claim("gap", "the record shows one injectable portfolio launch"),
        ]
    )
    fake = sup.script_all_good(FakeProvider())
    fake.clear("fit_narrative").script("fit_narrative", narrative, narrative)
    fake.clear("audit:narrative").script("audit:narrative", labelled_job_fact, labelled_job_fact)
    built = PackageFactory().build(
        profile=sup.profile(),
        master=sup.document(),
        job=sup.job(),
        master_resume_id="r",
        job_id="j",
        opportunity_id="o",
        assessment=sup.assessment(),
        questions=[],
        research={"verified_facts": ["The company operates 40 clinics in 12 states."]},
        generator=PackageGenerator(fake),
    )
    assert "40 clinics" not in built.schema.fit_explanation
    record = built.schema.truth_ledger["generation"]["truth_gate"]["narrative"]
    assert record["fell_back"] is True
    assert any("40" in v for v in record["violations"])


def test_a_fabricated_we_sentence_in_a_letter_does_not_ship() -> None:
    from sabra_documents.generate import CoverLetterDraft

    body = (
        "I am applying for the Regional Sales Manager - Medical Aesthetics role at Revance "
        "Therapeutics.\n\n"
        "At Meridian Aesthetics we grew the territory to 40 clinics across 12 states, and I built "
        "and led a team of 7 territory managers.\n\n"
        "I would welcome a conversation about the role."
    )
    audit = ClaimAudit(
        claims=[
            *sup.good_letter_audit().claims,
            sup.claim(
                "job_fact",
                "grew the territory to 40 clinics across 12 states",
                "operates 40 clinics in 12 states",
            ),
        ]
    )
    fake = sup.script_all_good(FakeProvider())
    fake.clear("cover_letter").script(
        "cover_letter", CoverLetterDraft(body=body, claims=[]), CoverLetterDraft(body=body, claims=[])
    )
    fake.clear("audit:cover_letter").script("audit:cover_letter", audit, audit)
    built = PackageFactory().build(
        profile=sup.profile(),
        master=sup.document(),
        job=sup.job(),
        master_resume_id="r",
        job_id="j",
        opportunity_id="o",
        assessment=sup.assessment(),
        questions=[],
        research={"verified_facts": ["The company operates 40 clinics in 12 states."]},
        generator=PackageGenerator(fake),
    )
    assert "40 clinics" not in built.schema.cover_letter


# ---------------------------------------------------------------------------
# Years of experience
# ---------------------------------------------------------------------------


def test_only_experience_phrases_raise_the_years_cap() -> None:
    prof = sup.profile()
    prof.employment[0].accomplishments.append("Secured a 15-year exclusive supply agreement.")
    found = corpus(prof=prof, master=None)
    assert found.stated_years and max(found.stated_years) == 11  # from the summary only
    assert "number" in kinds("Field sales leader with 15 years in aesthetics.", prof=prof)
    assert kinds("Field sales leader with 11 years in aesthetics.", prof=prof) == set()


def test_a_growth_period_does_not_cap_a_longer_career() -> None:
    prof = sup.profile()
    prof.summary = "Commercial leader in medical aesthetics."
    prof.employment = [e for e in prof.employment if e.employer == "Meridian Aesthetics"]
    found = corpus(prof=prof, master="Grew regional revenue 43% over three years.")
    assert found.stated_years == []
    assert found.tenure >= 7
    assert not deterministic_check("Seven years in aesthetics sales.", found, "candidate")
    assert deterministic_check("Twelve years in aesthetics sales.", found, "candidate")


def test_stated_years_never_outrun_the_employment_dates() -> None:
    prof = sup.profile()
    prof.summary = "Commercial leader with 30 years of experience in medical aesthetics."
    found = corpus(prof=prof, master=None)
    assert found.years_limit == found.tenure
    assert "number" in kinds("Thirty years in aesthetics sales.", prof=prof, master=None)
    assert kinds("Fourteen years in aesthetics sales.", prof=prof, master=None) == set()


def test_a_duration_is_capped_by_the_role_it_names() -> None:
    # Meridian Aesthetics runs Mar 2019 to the present: about eight years
    assert "number" in kinds("Led device sales for 11 years at Meridian Aesthetics.")
    assert "number" in kinds("Led device sales for 11 years at Meridian.")
    assert kinds("Led device sales for 7 years at Meridian Aesthetics.") == set()
    assert kinds("Led device sales for 4 years at Northstar Medical Devices.") == set()
    assert "number" in kinds("Sold for 9 years at Northstar Medical Devices.")
    # a sentence that names no employer is capped by the whole career
    assert kinds("Eleven years in device sales.") == set()


# ---------------------------------------------------------------------------
# Numbers must be attached to what they count
# ---------------------------------------------------------------------------


def test_a_number_borrowed_from_elsewhere_in_the_record_is_rejected() -> None:
    for text in (
        "Managed a team of 12 in Texas.",
        "Managed 12 clinics across Texas.",
        "Managed 12 clinics in Dallas.",
        "Ran a $12 book at Meridian.",
        "Sold into 7 clinics.",
    ):
        assert "number" in kinds(text), text


def test_a_number_with_its_noun_before_or_after_it_is_accepted() -> None:
    assert kinds("Managed a team of 7 in Texas.") == set()
    assert kinds("Built a group of 7 territory managers.") == set()
    assert kinds("Led 7 territory managers.") == set()
    assert kinds("Sold into 120 clinics.") == set()
    assert kinds("Maintained 96% contract renewal.") == set()
    assert kinds("Average deal size of $85,000.") == set()
    assert "number" in kinds("Average deal size of $85.")
    assert "number" in kinds("Average deal size of $12.")


def test_a_second_number_in_a_sentence_cannot_borrow_the_first_ones_noun() -> None:
    master = sup.resume_text().replace(
        "Trained clinic staff on device operation and treatment protocols.",
        "Managed 3 clinics and 12 staff.",
    )
    assert kinds("Managed 3 clinics and 12 staff.", master=master) == set()
    assert "number" in kinds("Managed 12 clinics.", master=master)
    assert "number" in kinds("Managed 3 staff.", master=master)


def test_ordinals_are_matched_exactly() -> None:
    assert kinds("Ranked 2nd of 24 representatives nationally.") == set()
    assert "number" in kinds("Ranked 1st of 24 representatives nationally.")
    assert "number" in kinds("Ranked 3rd of 24 representatives nationally.")


# ---------------------------------------------------------------------------
# Ordinary words
# ---------------------------------------------------------------------------


def test_names_ending_like_ordinary_words_are_still_names() -> None:
    assert "proper_noun" in kinds("Fidelity trained me on injectables.")
    assert "proper_noun" in kinds("Pfizer trained me on injectables.")


def test_ordinary_sentence_starters_are_not_names() -> None:
    for text in (
        "None worth raising.",
        "Oversaw a team of 7 territory managers.",
        "Field sales leader with 11 years across medical aesthetics.",
        "Commercial leader focused on consultative selling.",
        "Responsible for the Dallas region.",
    ):
        assert "proper_noun" not in kinds(text), text


def test_the_master_text_parser_still_feeds_the_corpus() -> None:
    doc = parse_master_resume(sup.resume_text())
    assert doc.employment and corpus().tenure >= 14


# ---------------------------------------------------------------------------
# A job sentence that describes a person is a claim about the candidate


def test_a_job_sentence_describing_a_person_cannot_borrow_company_figures() -> None:
    """"Someone who ran 40 clinics" names nobody but means the candidate."""

    for text in (
        "This role needs someone who ran 40 clinics, which matches the record.",
        "Your team will gain a veteran who managed 40 clinics.",
        "Revance Therapeutics will gain a leader who ran 40 clinics.",
        "The posting's 40 clinics match a background of running 40 clinics.",
    ):
        assert any("40" in m for m in messages(text, company="Revance Therapeutics")), text


def test_a_plain_job_sentence_still_uses_the_company_tier() -> None:
    assert messages("The company operates 40 clinics in 12 states.", company="Revance Therapeutics") == []


def test_tenure_rounds_down_so_a_partial_year_never_supports_the_next_whole_year() -> None:
    from sabra_documents.truth_gate import _tenure_years

    prof = sup.profile()
    entry = prof.employment[0]
    entry.start_date = "2019-03"
    entry.end_date = "2026-10"
    for other in prof.employment[1:]:
        other.start_date = "2020-01"
        other.end_date = "2021-01"
    assert _tenure_years(prof) == 7
