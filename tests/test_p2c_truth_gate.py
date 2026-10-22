"""The truth gate's deterministic rules and claim checks (requirement 5)."""

from __future__ import annotations

import pytest
import test_p2c_support as sup
from sabra_documents.truth_gate import (
    ClaimSupport,
    EvidenceCorpus,
    deterministic_check,
    norm,
    quote_in_evidence,
    scrub_contact,
    verified_research_facts,
    verify_claims,
)
from sabra_domain.schemas import CertificationEntry

SENIOR = "Senior Regional Sales Director"


def corpus(**kwargs) -> EvidenceCorpus:
    return EvidenceCorpus.build(
        kwargs.pop("prof", sup.profile()),
        master_text=kwargs.pop("master", sup.resume_text()),
        job=kwargs.pop("jb", sup.job()),
        research=kwargs.pop("research", None),
    )


def kinds(text: str, tier: str = "candidate", **kwargs) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for violation in deterministic_check(text, corpus(**kwargs), tier):
        out.setdefault(violation.kind, []).append(violation.token)
    return out


def letter(text: str, **kwargs) -> dict[str, list[str]]:
    return kinds(text, "letter", **kwargs)


# ---------------------------------------------------------------------------
# What passes
# ---------------------------------------------------------------------------


def test_a_truthful_text_passes_every_rule() -> None:
    assert letter(sup.GOOD_LETTER_BODY) == {}
    assert kinds(sup.good_proposal().summary) == {}
    assert kinds(sup.good_proposal().headline) == {}
    assert (
        kinds("Sales leader who grew regional revenue 43% and built a team of seven territory managers.")
        == {}
    )


# ---------------------------------------------------------------------------
# Numbers (B2)
# ---------------------------------------------------------------------------


def test_numbers_must_be_in_the_candidates_evidence() -> None:
    found = kinds("I grew revenue 250% and closed $12 million with 15 reps in 2016.")
    assert set(found["number"]) >= {"250%", "$12 million"}
    assert "2016" in found["year"]
    assert "15" in found["number"]


def test_numbers_match_across_formats() -> None:
    assert kinds("I managed a $4.2 million quota and deals averaging $85K.") == {}
    assert kinds("Renewal was 96 percent across 180 clinics.") == {}
    assert kinds("I led seven managers, two new locations and four practice groups.") == {}
    assert "number" in kinds("I led nine territory managers.")


@pytest.mark.parametrize(
    "text",
    [
        "The candidate has 12 years of experience in device sales.",
        "Twelve years selling aesthetics devices.",
        "The candidate has 2 decades of sales experience.",
        "Two decades in the field.",
        "Decades of experience.",
        "15 years of field sales.",
        "A 12-year career in sales.",
    ],
)
def test_a_duration_must_not_exceed_the_years_the_evidence_states(text: str) -> None:
    # 12 months, 2nd, 12 and 2 all appear in the record, none of them as years of experience
    assert "number" in kinds(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Eleven years of field sales experience.",
        "11 years in medical aesthetics.",
        "Over a decade in field sales.",
        "Three years of growth across the region.",
    ],
)
def test_a_duration_the_evidence_supports_passes(text: str) -> None:
    assert kinds(text) == {}, text


def test_a_count_must_appear_with_the_noun_that_follows_it() -> None:
    # 12 is in the record ("12 months"), but never with representatives
    found = kinds("I managed a team of 12 representatives.")
    assert "number" in found and "12" in found["number"]
    assert "number" in kinds("I ranked 1st of 24 representatives nationally.")
    assert kinds("I ranked 2nd of 24 representatives nationally.") == {}
    assert kinds("I launched into 180 clinics in the first 12 months.") == {}
    assert "number" in kinds("I launched into 12 clinics.")
    assert "number" in kinds("I sold into 180 representatives.")


def test_tenure_from_dates_is_the_limit_when_no_years_are_stated() -> None:
    bare = sup.profile()
    bare.summary = "Field sales professional."
    for entry in bare.employment:
        entry.accomplishments = []
    found = corpus(prof=bare, master=None)
    assert found.stated_years == []
    assert found.tenure >= 14  # Jun 2012 to the present
    assert not deterministic_check("I have 9 years in sales.", found)
    assert deterministic_check("I have 40 years in sales.", found)


def test_a_number_found_only_in_the_posting_is_not_supported() -> None:
    result = deterministic_check("I bring 5 clinics of experience.", corpus())
    assert [v.kind for v in result] == ["number"]
    assert "job posting" in result[0].message
    # understating the candidate's years is not a fabrication, so "5 years" is allowed
    assert not deterministic_check("I bring 5 years in this field.", corpus())


def test_a_percentage_must_be_the_candidates_own() -> None:
    assert "number" in kinds("I improved results by 120%.")
    research = {"verified_facts": ["The company grew 40% last year across 40 clinics."]}
    assert "number" in letter("I grew regional revenue 40%.", research=research)
    # even in a sentence about the company, a company percentage is not usable
    assert "number" in letter("The company grew 40% last year.", research=research)


def test_growth_words_and_rank_claims_are_quantitative_claims() -> None:
    assert "number" in kinds("I doubled revenue in the first year.")
    assert "number" in kinds("I was promoted twice and ranked first nationally.")
    assert kinds("I ranked 2nd of 24 representatives nationally.") == {}


# ---------------------------------------------------------------------------
# Job facts are not candidate facts (B1)
# ---------------------------------------------------------------------------


def test_a_headline_equal_to_the_job_title_is_rejected() -> None:
    jb = sup.job(title=SENIOR)
    found = kinds(SENIOR, jb=jb)
    assert "title" in found
    found = kinds(f"{SENIOR} with eleven years in aesthetics.", jb=jb)
    assert "title" in found or "proper_noun" in found
    assert any(w in found.get("title", []) for w in ("senior", "director")) or found


def test_the_job_title_may_only_be_named_as_the_thing_applied_to() -> None:
    jb = sup.job(title=SENIOR)
    assert letter(f"I am applying for the {SENIOR} role at Revance Therapeutics.", jb=jb) == {}
    assert letter(f"I am drawn to the {SENIOR} position.", jb=jb) == {}
    assert letter(f"My interest in the {SENIOR} opportunity is genuine.", jb=jb) == {}
    # claiming to BE that title is title inflation, even though the posting uses it
    assert letter(f"I was the {SENIOR} at Meridian Aesthetics.", jb=jb).get("title")
    assert letter(f"As {SENIOR} I led the region.", jb=jb).get("title")


def test_company_tier_supports_the_role_and_company_only() -> None:
    assert letter("I want to join Revance Therapeutics in Dallas.") == {}
    jb = sup.job(description="We compete with Allergan and Galderma.")
    assert "Allergan" in letter("I have beaten Allergan.", jb=jb)["proper_noun"]
    assert "Allergan" in kinds("Allergan is a competitor.", jb=jb)["proper_noun"]


def test_research_facts_are_company_facts_not_candidate_facts() -> None:
    research = {
        "verified_facts": ["company site describes itself as: founded in 1998 by Dana Whitfield"],
    }
    assert letter("The company was founded in 1998.", research=research) == {}
    assert letter("The company was founded by Dana Whitfield.", research=research) == {}
    # a first-person sentence may not borrow them
    assert letter("I founded a company in 1998.", research=research).get("year") == ["1998"]
    assert letter("I worked with Dana Whitfield.", research=research).get("proper_noun")
    # and the candidate-only tier accepts nothing from the company
    assert kinds("The company was founded in 1998.", research=research).get("year") == ["1998"]


def test_inferences_and_posting_facts_never_count() -> None:
    research = {
        "verified_facts": ["company site describes itself as: founded in 1998", "posted 2026-08-01"],
        "inferences": ["serves 400 clinics"],
    }
    assert verified_research_facts(research) == ["company site describes itself as: founded in 1998"]
    assert letter("The company serves 400 clinics.", research=research).get("number") == ["400"]
    assert kinds("The company serves 400 clinics.").get("number") == ["400"]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_names_must_be_in_the_evidence() -> None:
    found = kinds("I worked at Acme Biologics and then at Globex.")
    assert "Acme Biologics" in found["proper_noun"]
    assert "Globex" in found["proper_noun"]
    assert "Python" in kinds("I build tools in Python.")["proper_noun"]
    assert "Harvard University" in kinds("I studied at Harvard University.")["proper_noun"]


def test_a_sentence_initial_name_is_checked_too() -> None:
    assert "Pfizer" in kinds("Pfizer trained me on injectables.")["proper_noun"]
    assert "Pfizer" in letter("Pfizer trained me on injectables.")["proper_noun"]
    assert "Allergan" in kinds("Allergan hired me.")["proper_noun"]
    # ordinary sentence openers are not names
    assert kinds("Delivered consistent results. Grew the region. Strong relationships followed.") == {}
    assert kinds("Leading Meridian Aesthetics regional sales was rewarding.") == {}


def test_name_matching_keeps_capitalisation() -> None:
    plain = sup.profile()
    plain.summary = "Hit every sales target, reached the summit of the league and kept the square."
    found = {v.token for v in deterministic_check("I worked at Target and Summit and Square.", corpus(prof=plain, master=None))}
    assert {"Target", "Summit", "Square"} <= found
    # the same words in ordinary lower case are fine
    assert kinds("I hit every sales target.", prof=plain, master=None) == {}


def test_acronyms_must_be_in_the_evidence() -> None:
    assert "PMP" in kinds("I hold a PMP and an MBA.")["acronym"]
    assert kinds("I run CRM reporting in Salesforce.") == {}
    assert kinds("I work in the US.") == {}


def test_sentence_initial_words_and_greetings_are_not_names() -> None:
    assert kinds("Dear Hiring Team,\nThank you for considering this. Sincerely, Best regards") == {}
    assert kinds("Texas and Oklahoma were both in my territory.") == {}


# ---------------------------------------------------------------------------
# Titles, certifications, degrees, dates, terms
# ---------------------------------------------------------------------------


def test_title_inflation_is_caught() -> None:
    found = kinds("As Senior Director of Sales Operations I led the region.")
    assert "title" in found
    assert "title" in kinds("As the head of sales I led the region.")
    assert "title" in kinds("I was a vice president of sales.")
    assert kinds("As Regional Sales Manager I led the region.") == {}
    assert letter("I am applying for the Regional Sales Manager - Medical Aesthetics role.") == {}
    senior_posting = sup.job(title="Senior Regional Sales Manager")
    assert letter("I am applying for the Senior Regional Sales Manager role.", jb=senior_posting) == {}


def test_certified_or_licensed_requires_a_matching_certification() -> None:
    assert kinds("I am a certified project manager.").get("certification")
    assert kinds("I am a Certified Aesthetic Consultant.") == {}
    assert kinds("I am a licensed physician.").get("certification")
    no_certs = sup.profile()
    no_certs.certifications = []
    assert kinds("I am a Certified Aesthetic Consultant.", prof=no_certs).get("certification")


def test_degrees_need_a_matching_education_entry() -> None:
    assert kinds("I earned a master's degree in business.").get("degree")
    assert kinds("I hold a PhD in chemistry.").get("degree")
    assert kinds("I earned a bachelor's degree in business.") == {}


def test_invented_dates_are_caught() -> None:
    found = kinds("I joined the company in March 2016 and left in 2021.")
    assert "date" in found and "mar 2016" in found["date"]
    assert "2021" in found["year"]
    assert kinds("I have led the territory since March 2019.") == {}
    assert kinds("I held the role from Jan 2015 to Feb 2019.") == {}


def test_requirement_terms_the_candidate_never_mentions_are_caught() -> None:
    jb = sup.job(requirements="- Experience with biologics and cold-chain logistics required")
    found = kinds("I have deep experience with biologics.", jb=jb)
    assert "skill" in found and "biologics" in found["skill"]
    assert kinds("I have deep experience with injectables and capital equipment.") == {}
    assert "skill" not in kinds("I would welcome a thoughtful conversation.", jb=jb)


def test_requirement_terms_fall_back_to_the_description_when_requirements_are_empty() -> None:
    jb = sup.job(
        requirements=None,
        preferred_qualifications=None,
        description="You will sell biologics into hospital networks.",
        responsibilities="- Cover cold-chain logistics",
    )
    assert "biologics" in kinds("I have deep experience with biologics.", jb=jb).get("skill", [])


# ---------------------------------------------------------------------------
# Quotes and claims
# ---------------------------------------------------------------------------


def test_quotes_must_really_be_in_the_evidence() -> None:
    gate_corpus = corpus()
    assert quote_in_evidence("grew REGIONAL revenue   43% over three years", gate_corpus)
    assert quote_in_evidence("- Grew regional revenue 43% over three years", gate_corpus)
    assert quote_in_evidence("Grew regional revenue 43% ... 6-state territory", gate_corpus)
    assert not quote_in_evidence("Grew regional revenue 80% over three years", gate_corpus)
    assert not quote_in_evidence("", gate_corpus)
    assert not quote_in_evidence(None, gate_corpus)
    assert not quote_in_evidence("43%", gate_corpus)


def test_a_candidate_quote_cannot_come_from_the_job_or_the_research() -> None:
    research = {"verified_facts": ["the company grew 40% last year across 40 clinics"]}
    gate_corpus = corpus(research=research, jb=sup.job(title=SENIOR))
    assert not quote_in_evidence(SENIOR, gate_corpus)
    assert not quote_in_evidence("grew 40% last year", gate_corpus)
    assert not quote_in_evidence("Experience with injectables and capital equipment preferred", gate_corpus)
    # ... but it is a perfectly good quote for a statement about the job
    assert quote_in_evidence(SENIOR, gate_corpus, "job")
    assert quote_in_evidence("grew 40% last year", gate_corpus, "job")
    assert quote_in_evidence("Experience with injectables and capital equipment preferred", gate_corpus, "job")
    assert not quote_in_evidence("Grew regional revenue 43% over three years", gate_corpus, "job")


def test_claims_are_checked_against_their_own_excerpt() -> None:
    gate_corpus = corpus()
    ok = ClaimSupport(
        claim="Grew regional revenue 43%", excerpt="Grew regional revenue 43% over three years"
    )
    wrong_number = ClaimSupport(
        claim="Grew regional revenue 80%", excerpt="Grew regional revenue 43% over three years"
    )
    invented = ClaimSupport(claim="Won an award", excerpt="Won the President's Club award")
    flagged = ClaimSupport(claim="Won an award", unsupported=True)
    assert verify_claims([ok], gate_corpus) == []
    assert len(verify_claims([wrong_number, invented, flagged], gate_corpus)) == 3


def test_a_draft_claim_about_the_candidate_cannot_cite_research() -> None:
    research = {"verified_facts": ["the company grew 40% last year across 40 clinics"]}
    gate_corpus = corpus(research=research)
    borrowed = ClaimSupport(claim="I grew revenue 40%", excerpt="grew 40% last year")
    about_company = ClaimSupport(
        claim="The company grew 40%", excerpt="grew 40% last year", source="job"
    )
    assert len(verify_claims([borrowed], gate_corpus)) == 1
    assert verify_claims([about_company], gate_corpus) == []


# ---------------------------------------------------------------------------
# Contact scrubbing (S6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("Call +1 214 555 0142 today", ["214", "555", "0142", "+1"]),
        ("Call 214.555.0142 today", ["214.555"]),
        ("Call (214) 555-0142 today", ["555-0142"]),
        ("Call 2145550142 today", ["2145550142"]),
        ("Call +44 20 7946 0958 today", ["7946", "+44"]),
        ("jordan@ex.co.uk", ["ex.co.uk"]),
        ("see https://github.com/jr/project for code", ["github.com", "jr/project"]),
        ("see github.com/jr", ["github.com"]),
        ("linkedin.com/in/x", ["linkedin"]),
        ("portfolio at www.jrivera.dev/work", ["jrivera"]),
        ("Lives at 123 Main St, Dallas TX 75201 now", ["123 Main", "75201"]),
        ("1200 Elm Street Apt 4B, Plano, TX 75074", ["1200 Elm", "75074"]),
    ],
)
def test_contact_details_are_scrubbed(raw: str, gone: list[str]) -> None:
    scrubbed = scrub_contact(raw)
    for fragment in gone:
        assert fragment not in scrubbed, (raw, scrubbed)
    assert "[contact removed]" in scrubbed


def test_scrubbing_leaves_dates_and_figures_alone() -> None:
    text = "Mar 2019 - Present. Grew revenue 43% across 180 clinics from 2015 - 2019 for $85,000."
    assert scrub_contact(text) == text


def test_contact_details_are_scrubbed_from_the_corpus() -> None:
    gate_corpus = corpus()
    assert "555-0142" not in gate_corpus.candidate_text
    assert "example.test" not in gate_corpus.candidate_text
    assert "Meridian Aesthetics" in gate_corpus.candidate_text


def test_norm_is_case_whitespace_quote_dash_and_month_insensitive() -> None:
    assert norm("  March 2019 – “Present” ") == 'mar 2019 - "present"'


def test_certifications_in_the_profile_can_name_a_licence() -> None:
    licensed = sup.profile()
    licensed.certifications = [CertificationEntry(name="Texas Real Estate License")]
    found = EvidenceCorpus.build(licensed, master_text=None, job=sup.job())
    assert not [
        v
        for v in deterministic_check("I hold a Texas Real Estate License.", found)
        if v.kind == "certification"
    ]
