"""Hardening round for the model-assisted generation path.

Each test here closes a hole found by probing the first version: facts about the job
accepted as facts about the candidate, numbers matched as a bag of values, an audit
that trusted the model's own labels, the candidate's pay floor reaching a prompt, and
the deterministic path drifting. The model is always a `FakeProvider`.
"""

from __future__ import annotations

import httpx
import pytest
import test_p2c_support as sup
from sabra_documents import generate as gen_module
from sabra_documents.generate import (
    CoverLetterDraft,
    FitNarrative,
    PackageGenerator,
    heuristic_context,
    narrative_prompt,
)
from sabra_documents.master_resume import parse_master_resume
from sabra_documents.package import PackageFactory
from sabra_documents.truth_gate import ClaimAudit
from sabra_domain.schemas import FitAssessmentSchema
from sabra_domain.settings import Settings
from sabra_llm.base import NullProvider
from sabra_llm.errors import LLMError
from sabra_llm.fake import FakeProvider
from sabra_llm.openai_compatible import OpenAICompatibleProvider

SENIOR = "Senior Regional Sales Director"


def build(fake=None, *, prior=None, research=None, prof=None, jb=None, assess="default", master=None):
    factory = PackageFactory()
    generator = PackageGenerator(fake, prior=prior) if fake is not None else None
    built = factory.build(
        profile=prof or sup.profile(),
        master=master or sup.document(),
        job=jb or sup.job(),
        master_resume_id="resume_1",
        job_id="job_1",
        opportunity_id="opp_1",
        assessment=sup.assessment() if assess == "default" else assess,
        questions=[],
        research=research,
        generator=generator,
    )
    return built, generator


def gate_record(built, name):
    return built.schema.truth_ledger["generation"]["truth_gate"][name]


def letter_with(sentence: str) -> CoverLetterDraft:
    body = (
        "I am applying for the Regional Sales Manager - Medical Aesthetics role at Revance "
        f"Therapeutics.\n\n{sentence}\n\nI would welcome a conversation about the role."
    )
    return CoverLetterDraft(body=body, claims=[])


# ---------------------------------------------------------------------------
# The deterministic path does not drift (S8)
# ---------------------------------------------------------------------------

# Measured on tests/fixtures/master_resume_sample.txt. Re-pinned twice, each time for one
# reviewed reason, and the rendered markdown differs by nothing else:
#   1. the template "Directly relevant experience: ..." line left the tailored resume and
#      "My background covers ..." left the cover letter
#      (5ec7d03c / 3934635f / 235f4f25 -> 13d8af98 / 56216c28 / e36843bb);
#   2. the parser now reads the graduation year from the fixture's education line, so the
#      only further change is "(2012)" on the education line and end_date "2012" in the
#      education payload (-> the values below).
GOLDEN_HASH_NO_ASSESSMENT = "6cec870f6e374fc84415a176478e82a753613a44c48b3a157fa16f174b5c3a57"
GOLDEN_HASH_WITH_ASSESSMENT = "02216b1d44df93b251492f0b205a0e2464b79da73392724a7b780bbd1e8890ed"
GOLDEN_HASH_SENIOR_DIRECTOR = "e1cadc17e040dfd2d1a54bbe0e28d05645d350b013dcf91a7ca537938e4f354a"


def golden_build(generator=None, *, assessment=None, **job_overrides):
    return PackageFactory().build(
        profile=sup.profile(),
        master=sup.document(),
        job=sup.job(**job_overrides),
        master_resume_id="r",
        job_id="j",
        opportunity_id="o",
        assessment=assessment,
        questions=[],
        generator=generator,
    )


def test_the_no_model_path_hashes_exactly_as_before_the_model_work() -> None:
    assert golden_build().schema.content_hash == GOLDEN_HASH_NO_ASSESSMENT
    assert golden_build(assessment=sup.assessment()).schema.content_hash == GOLDEN_HASH_WITH_ASSESSMENT
    variant = golden_build(assessment=sup.assessment(), title="Senior Director", requirements=None)
    assert variant.schema.content_hash == GOLDEN_HASH_SENIOR_DIRECTOR
    # a generator that cannot or does not run leaves the output byte-identical
    for provider in (NullProvider(), FakeProvider().script("tailoring", LLMError("down"))):
        built = golden_build(PackageGenerator(provider), assessment=sup.assessment())
        assert built.schema.content_hash == GOLDEN_HASH_WITH_ASSESSMENT


def test_no_template_skills_sentence_in_the_résumé_or_the_letter() -> None:
    schema = golden_build(assessment=sup.assessment()).schema
    assert "Directly relevant experience" not in schema.tailored_resume_markdown
    assert "My background covers" not in (schema.cover_letter or "")
    ledger_text = str(schema.truth_ledger)
    assert "alignment sentence" not in ledger_text
    # the Skills section still carries the terms, so nothing was lost
    assert "## Skills" in schema.tailored_resume_markdown
    assert "Territory management" in schema.tailored_resume_markdown


# ---------------------------------------------------------------------------
# B1: the job's facts are not the candidate's facts
# ---------------------------------------------------------------------------


def test_a_headline_and_summary_copied_from_the_job_title_do_not_ship() -> None:
    jb = sup.job(title=SENIOR)
    proposal = sup.good_proposal().model_copy(
        update={
            "headline": SENIOR,
            "summary": f"{SENIOR} with 11 years across medical aesthetics, who grew regional "
            "revenue 43% and led a team of 7 territory managers.",
        }
    )
    audit = ClaimAudit(
        claims=[
            sup.claim("candidate_fact", SENIOR, SENIOR),  # job-title "evidence"
            sup.claim("candidate_fact", "11 years across medical aesthetics", sup.SUMMARY_QUOTE),
            sup.claim("candidate_fact", "grew regional revenue 43%", "Grew regional revenue 43%"),
            sup.claim("candidate_fact", "led a team of 7 territory managers", sup.TEAM.rstrip(".")),
        ]
    )
    fake = sup.script_all_good(FakeProvider())
    fake.clear("tailoring").script("tailoring", proposal, proposal)
    fake.clear("audit:tailoring").script("audit:tailoring", audit, audit)
    built, _ = build(fake, jb=jb)

    assert SENIOR not in built.schema.tailored_resume_markdown
    assert "_Medical Aesthetics & Device Sales Leader_" in built.schema.tailored_resume_markdown
    record = gate_record(built, "tailoring")
    assert record["fell_back"] is True and record["regenerated"] is True
    assert any("title" in v or "Senior" in v for v in record["violations"])
    # the check never reached the audit: the cheap layers rejected it
    assert fake.calls_for("audit:tailoring") == []


def test_a_company_percentage_cannot_become_a_claim_about_the_candidate() -> None:
    research = {"verified_facts": ["The company grew 40% last year across 40 clinics."]}
    letter = letter_with("I grew regional revenue 40% at Meridian Aesthetics.")
    audit = ClaimAudit(
        claims=[
            *sup.good_letter_audit().claims,
            sup.claim(
                "candidate_fact", "grew regional revenue 40%", "grew 40% last year"
            ),
        ]
    )
    fake = sup.script_all_good(FakeProvider())
    fake.clear("cover_letter").script("cover_letter", letter, letter)
    fake.clear("audit:cover_letter").script("audit:cover_letter", audit, audit)
    built, _ = build(fake, research=research)
    assert "40%" not in built.schema.cover_letter
    record = gate_record(built, "cover_letter")
    assert record["fell_back"] is True
    assert any("40%" in v for v in record["violations"])


def test_an_audit_quote_for_a_candidate_fact_must_be_in_the_candidates_record() -> None:
    research = {"verified_facts": ["The company grew 40% last year across 40 clinics."]}
    fake = sup.script_all_good(FakeProvider())
    # the letter is clean, but the auditor "supports" a candidate claim from the research
    audit = ClaimAudit(
        claims=[
            *sup.good_letter_audit().claims[1:],
            sup.claim("candidate_fact", "grew revenue 40%", "grew 40% last year"),
        ]
    )
    fake.clear("audit:cover_letter").script("audit:cover_letter", audit, sup.good_letter_audit())
    fake.script("cover_letter", sup.good_letter())
    built, _ = build(fake, research=research)
    record = gate_record(built, "cover_letter")
    assert record["regenerated"] is True
    assert any("not in the candidate's record" in v for v in record["violations"])


# ---------------------------------------------------------------------------
# B2: numbers are not a bag of values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "I bring 12 years of experience in device sales.",
        "Twelve years selling aesthetics devices shaped how I lead.",
        "I led a team of 12 representatives at Meridian Aesthetics.",
        "Two decades in this field taught me to listen.",
    ],
)
def test_borrowed_numbers_do_not_ship_in_a_letter(sentence: str) -> None:
    fake = sup.script_all_good(FakeProvider())
    fake.clear("cover_letter").script("cover_letter", letter_with(sentence), letter_with(sentence))
    built, _ = build(fake)
    assert sentence not in built.schema.cover_letter
    assert gate_record(built, "cover_letter")["fell_back"] is True


# ---------------------------------------------------------------------------
# B3: pay never reaches a model
# ---------------------------------------------------------------------------


def comp_assessment() -> FitAssessmentSchema:
    return FitAssessmentSchema(
        fit_score=0.8,
        career_score=0.8,
        qualification_score=0.8,
        compensation_score=0.9,
        location_score=1.0,
        industry_score=1.0,
        trajectory_score=0.7,
        confidence=0.7,
        strengths=[
            "posted pay: top of range 150,000 meets the 140,000 target",
            "territory and category match the candidate's background",
        ],
        gaps=[
            "compensation: minimum 130,000 is above the posted midpoint",
            "requirement not evidenced: experience with biologics",
        ],
        why_this_job="Strong match; pay clears the 140,000 target.",
        component_rationale={
            "compensation_score": "top of range 150,000 meets the 140,000 target",
            "qualification_score": "territory and category match the candidate's background",
        },
        engine="heuristic",
    )


def test_compensation_text_never_reaches_a_prompt() -> None:
    assessment = comp_assessment()
    strengths, gaps = heuristic_context(assessment)
    assert strengths == ["territory and category match the candidate's background"]
    assert gaps == ["requirement not evidenced: experience with biologics"]

    fake = sup.script_all_good(FakeProvider())
    build(fake, assess=assessment)
    assert fake.calls
    for call in fake.calls:
        for text in (call["system"], call["user"]):
            for secret in ("150,000", "140,000", "130,000", "midpoint", "posted pay"):
                assert secret not in text, (call["purpose"], secret)
    assert "biologics" in fake.calls_for("fit_narrative")[0]["user"]
    assert "130,000" not in narrative_prompt(sup.job(), assessment, [])


def test_the_candidates_own_pay_preferences_are_not_in_the_evidence() -> None:
    from sabra_domain.schemas import CompensationPreference

    prof = sup.profile()
    prof.desired_compensation = CompensationPreference(minimum=91234.0, target=131313.0)
    fake = sup.script_all_good(FakeProvider())
    build(fake, prof=prof)
    for call in fake.calls:
        assert "91234" not in call["system"] + call["user"]
        assert "131313" not in call["system"] + call["user"]


# ---------------------------------------------------------------------------
# S1/S2: names
# ---------------------------------------------------------------------------


def test_a_sentence_initial_fabricated_employer_does_not_ship() -> None:
    sentence = "Pfizer trained me on injectables before I joined Meridian Aesthetics."
    fake = sup.script_all_good(FakeProvider())
    fake.clear("cover_letter").script("cover_letter", letter_with(sentence), letter_with(sentence))
    built, _ = build(fake)
    assert "Pfizer" not in built.schema.cover_letter
    assert any("Pfizer" in v for v in gate_record(built, "cover_letter")["violations"])


# ---------------------------------------------------------------------------
# S3: the audit does not trust the model's own labels
# ---------------------------------------------------------------------------


def run_letter_audit(audit: ClaimAudit, letter: CoverLetterDraft | None = None):
    fake = sup.script_all_good(FakeProvider())
    fake.clear("audit:cover_letter").script("audit:cover_letter", audit, sup.good_letter_audit())
    fake.clear("cover_letter").script("cover_letter", letter or sup.good_letter(), sup.good_letter())
    built, _ = build(fake)
    return gate_record(built, "cover_letter")


def test_a_job_fact_needs_a_quote_from_the_job_information() -> None:
    claims = [c for c in sup.good_letter_audit().claims if c.kind != "job_fact"]
    claims.append(sup.claim("job_fact", "the role is at Revance Therapeutics"))  # no quote
    record = run_letter_audit(ClaimAudit(claims=claims))
    assert record["regenerated"] is True
    assert any("not in the job information" in v for v in record["violations"])

    claims[-1] = sup.claim("job_fact", "the role is at Revance", "Revance in the Gulf of Mexico")
    record = run_letter_audit(ClaimAudit(claims=claims))
    assert any("not in the job information" in v for v in record["violations"])


def test_a_framing_label_cannot_cover_a_sentence_with_figures() -> None:
    claims = [c for c in sup.good_letter_audit().claims if "43%" not in c.claim]
    claims.append(sup.claim("framing", "grew regional revenue 43% over three years across a 6-state territory"))
    record = run_letter_audit(ClaimAudit(claims=claims))
    assert record["regenerated"] is True
    assert any("framing" in v or "no factual claim covers" in v for v in record["violations"])


def test_every_sentence_must_be_covered_by_some_claim() -> None:
    claims = [c for c in sup.good_letter_audit().claims if "120" not in c.claim]
    record = run_letter_audit(ClaimAudit(claims=claims))
    assert record["regenerated"] is True
    assert any("120" in v and "no factual claim covers" in v for v in record["violations"])

    # a sentence with no figures still has to be touched by a claim
    claims = [c for c in sup.good_letter_audit().claims if c.kind != "candidate_fact" or "field sales" not in c.claim]
    claims = [c for c in claims if c.kind != "job_fact"]
    record = run_letter_audit(ClaimAudit(claims=claims))
    assert any("no claim covers the sentence" in v for v in record["violations"])


def test_an_empty_audit_is_never_acceptable_even_for_a_short_text() -> None:
    fake = sup.script_all_good(FakeProvider())
    fake.clear("fit_narrative").script(
        "fit_narrative",
        FitNarrative(why_this_job="It fits well.", gap_analysis="Nothing to raise."),
        FitNarrative(why_this_job="It fits well.", gap_analysis="Nothing to raise."),
    )
    fake.clear("audit:narrative").script("audit:narrative", ClaimAudit(claims=[]), ClaimAudit(claims=[]))
    built, _ = build(fake)
    record = gate_record(built, "narrative")
    assert record["fell_back"] is True
    assert any("no claims" in v for v in record["violations"])
    assert "It fits well." not in built.schema.fit_explanation


def test_probe_a_twelve_years_narrative_with_an_empty_audit_does_not_ship() -> None:
    fake = sup.script_all_good(FakeProvider())
    bad = FitNarrative(why_this_job="Twelve years selling. Fits well.", gap_analysis="None worth raising.")
    fake.clear("fit_narrative").script("fit_narrative", bad, bad)
    fake.clear("audit:narrative").script("audit:narrative", ClaimAudit(claims=[]), ClaimAudit(claims=[]))
    built, _ = build(fake)
    assert "Twelve years" not in built.schema.fit_explanation


# ---------------------------------------------------------------------------
# S5: the call budget is real
# ---------------------------------------------------------------------------


def test_the_thirteenth_call_is_refused() -> None:
    generator = PackageGenerator(sup.script_all_good(FakeProvider()))
    generator._calls = gen_module.MAX_CALLS_PER_BUILD
    with pytest.raises(LLMError, match="budget"):
        generator._call("s", "u", FitNarrative, "fit_narrative", None)


def test_audits_count_against_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen_module, "MAX_CALLS_PER_BUILD", 3)
    fake = sup.script_all_good(FakeProvider())
    built, _ = build(fake)
    # tailoring, its audit, then the letter: the letter's audit is the fourth call
    assert len(fake.calls) == 3
    assert any(w.startswith("model unavailable:") and "budget" in w for w in built.schema.warnings)
    generated_by = built.schema.truth_ledger["generation"]["generated_by"]
    assert generated_by["tailored_resume"].startswith("model:")
    assert generated_by["cover_letter"] == "template"  # its audit was refused, so it never ships
    assert generated_by["fit_explanation"] == "template"


# ---------------------------------------------------------------------------
# Reuse is keyed on everything the output depends on
# ---------------------------------------------------------------------------


def first_record():
    built, _ = build(sup.script_all_good(FakeProvider()))
    return built.schema.truth_ledger["generation"]


def test_reuse_restores_the_verified_claims() -> None:
    record = first_record()
    assert record["verified_claims"]["cover_letter"]
    again, _ = build(FakeProvider(), prior=record)
    assert again.schema.truth_ledger["generation"]["verified_claims"]["cover_letter"] == (
        record["verified_claims"]["cover_letter"]
    )
    assert any(
        c["evidence"][0]["excerpt"]
        for c in again.schema.truth_ledger["claims"]
        if c["artifact"] == "cover_letter"
    )


@pytest.mark.parametrize(
    "change",
    ["research", "master", "assessment", "profile", "job"],
)
def test_reuse_is_invalidated_when_any_input_changes(change: str) -> None:
    record = first_record()
    kwargs: dict = {}
    if change == "research":
        kwargs["research"] = {"verified_facts": ["company site describes itself as: founded in 1998"]}
    elif change == "master":
        kwargs["master"] = parse_master_resume(sup.resume_text() + "\n- Added a new bullet.\n")
    elif change == "assessment":
        kwargs["assess"] = sup.assessment().model_copy(update={"gaps": ["a different gap"]})
    elif change == "profile":
        prof = sup.profile()
        prof.skills = prof.skills[:-1]
        kwargs["prof"] = prof
    else:
        kwargs["jb"] = sup.job(description="A different description of the region.")
    fake = sup.script_all_good(FakeProvider())
    build(fake, prior=record, **kwargs)
    assert fake.calls_for("tailoring"), f"changing {change} must regenerate"


def test_an_unusable_prior_is_ignored_not_trusted() -> None:
    record = first_record()
    record["reusable"] = False
    fake = sup.script_all_good(FakeProvider())
    build(fake, prior=record)
    assert fake.calls_for("tailoring")


# ---------------------------------------------------------------------------
# Failed assembly leaves nothing behind
# ---------------------------------------------------------------------------


def test_a_failed_assembly_clears_its_claims_and_stored_artifact(monkeypatch) -> None:
    monkeypatch.setattr(gen_module, "verify_tailored_truth", lambda *a, **k: ["forced failure"])
    built, _ = build(sup.script_all_good(FakeProvider()))
    generation = built.schema.truth_ledger["generation"]
    assert generation["truth_gate"]["tailoring"]["fell_back"] is True
    assert "tailoring" not in generation["artifacts"]
    assert "tailoring" not in generation["verified_claims"]
    assert generation["generated_by"]["tailored_resume"] == "template"


# ---------------------------------------------------------------------------
# Provider nits
# ---------------------------------------------------------------------------


def test_plain_completion_records_a_prompt_hash(tmp_path) -> None:
    from types import SimpleNamespace

    from sabra_llm.anthropic_provider import AnthropicProvider

    reply = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="hi")],
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0
        ),
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: reply))
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'p.db'}",
        data_dir=tmp_path,
        artifacts_dir=tmp_path / "a",
        evidence_dir=tmp_path / "e",
        automation_screenshot_dir=tmp_path / "s",
        source_registry_path=tmp_path / "r.yaml",
        candidate_dir=tmp_path / "c",
    )
    llm = AnthropicProvider(settings, client=client)
    seen = []
    llm.call_sink = seen.append
    llm.complete("hello", system="sys", purpose="unit")
    assert len(seen[0].prompt_hash) == 64


def test_openai_compatible_error_bodies_are_redacted(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'p.db'}",
        data_dir=tmp_path,
        artifacts_dir=tmp_path / "a",
        evidence_dir=tmp_path / "e",
        automation_screenshot_dir=tmp_path / "s",
        source_registry_path=tmp_path / "r.yaml",
        candidate_dir=tmp_path / "c",
        llm_provider="openai",
        llm_model="m",
        llm_api_key="sk-live-abcdef123456",
        llm_base_url="http://llm.test/v1",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="bad key sk-live-abcdef123456 and sk-other-9999")
        )
    )
    llm = OpenAICompatibleProvider(settings, client=client)
    with pytest.raises(LLMError) as caught:
        llm.complete("hi")
    assert "sk-live-abcdef123456" not in str(caught.value)
    assert "sk-other-9999" not in str(caught.value)
    assert "[redacted]" in str(caught.value)
