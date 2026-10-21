"""Application question memory (requirement 18).

Reusable, structured answers to common questions. The rules that matter:

* an answer the candidate did not supply is **UNKNOWN**, never a guess;
* a stored answer is placed on a form only when the owner has recorded it and
  marked it **VERIFIED**;
* sensitive questions (salary, background check, voluntary disclosures, and the
  rest of `SENSITIVE_KEYS`) follow that same rule. A verified answer may fill
  them, which is what lets a package reach COMPLETE. Anything short of verified
  stays blank and blocks the package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sabra_domain.enums import TruthLevel
from sabra_domain.identity import normalize_text, token_matches_phrase
from sabra_domain.schemas import (
    AnswerRecord,
    ApplicationAnswerSchema,
    CandidateProfile,
)

# Questions that legally or ethically belong to the candidate alone. They are never
# auto-filled even when a stored value exists, because completing them on someone's
# behalf is a decision only they can make.
SENSITIVE_KEYS = frozenset(
    {
        "veteran_status",
        "disability_status",
        "gender",
        "race",
        "ethnicity",
        "hispanic_ethnicity",
        "date_of_birth",
        "criminal_history",
        "background_check",
        "salary_expectations",
    }
)

# Canonical question keys with the phrases that identify them. Kept as data so a
# new pattern is a one-line addition rather than new logic.
QUESTION_CATALOGUE: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "work_authorization",
        (
            "authorized to work",
            "work authorization",
            "legally authorized",
            "right to work",
            "eligible to work",
            "work permit",
        ),
    ),
    ("sponsorship", ("sponsorship", "require sponsorship", "visa sponsorship", "need a visa")),
    (
        "us_citizen",
        ("u.s. citizen", "us citizen", "united states citizen", "citizen of the united states"),
    ),
    (
        "spanish",
        (
            "speak spanish",
            "fluent in spanish",
            "proficient in spanish",
        ),
    ),
    ("relocation", ("willing to relocate", "relocation", "open to relocating")),
    (
        "travel",
        (
            "willing to travel",
            "travel required",
            "ability to travel",
            "travel up to",
            "comfortable travelling",
            "comfortable traveling",
        ),
    ),
    (
        "salary_expectations",
        (
            "salary expectation",
            "salary expectations",
            "desired compensation",
            "compensation expectation",
            "salary requirement",
            "expected salary",
            "desired salary",
        ),
    ),
    (
        "start_date",
        (
            "start date",
            "when can you start",
            "availability to start",
            "notice period",
            "earliest start",
        ),
    ),
    ("veteran_status", ("veteran status", "protected veteran")),
    ("disability_status", ("disability", "accommodation")),
    ("gender", ("gender", "sex ")),
    ("hispanic_ethnicity", ("hispanic", "latino", "latina", "latinx")),
    ("race", ("race", "ethnicity")),
    (
        "commute",
        (
            "willing to commute",
            "reliable transportation",
            "commute",
            "driver's license",
            "drivers license",
        ),
    ),
    ("background_check", ("background check", "drug screen", "drug test")),
    (
        "non_compete",
        ("non-compete", "noncompete", "non compete"),
    ),
    (
        "age_eligibility",
        ("18 or older", "at least 18", "over the age of 18", "are you 18"),
    ),
    (
        "prior_employer",
        ("previously worked", "previously employed", "former employee"),
    ),
    (
        "family_employed",
        ("family members", "relatives employed", "relatives currently employ"),
    ),
    (
        "street_address",
        ("street address", "permanent residence", "current address"),
    ),
    (
        "current_employer",
        ("current employer", "most recent employer", "most recent/current employer"),
    ),
    ("city", ("what city", "city do you", "current city")),
    ("state_residence", ("what state", "state do you", "current state")),
    ("postal_code", ("postal code", "zip code")),
    ("how_did_you_hear", ("how did you hear", "how you heard", "where did you hear")),
    ("education_level", ("highest level of education", "degree", "education level")),
    ("years_experience", ("years of experience", "how many years")),
    ("phone", ("phone number", "contact number", "mobile")),
    ("linkedin", ("linkedin",)),
    ("website", ("portfolio", "website", "personal site")),
)


def classify_question(question: str) -> str | None:
    """Return the canonical key for a question, or None when unrecognized."""

    if not question:
        return None
    for key, phrases in QUESTION_CATALOGUE:
        for phrase in phrases:
            if token_matches_phrase(question, phrase):
                return key
    return None


@dataclass(slots=True)
class Resolution:
    answer: ApplicationAnswerSchema
    matched_key: str | None = None

    @property
    def resolved(self) -> bool:
        return self.answer.answer not in (None, "")


def _record_to_answer(
    key: str, question: str, record: AnswerRecord | None
) -> ApplicationAnswerSchema:
    """Turn a stored record into the answer that may be placed on a form.

    Verified is the only level that fills, including sensitive questions. A value
    that was recorded but not verified is kept off the form and named as needing
    the owner, so a package cannot become COMPLETE on an unconfirmed disclosure.
    """

    sensitive = key in SENSITIVE_KEYS or bool(record and record.sensitive)
    if record is None or record.value in (None, ""):
        return ApplicationAnswerSchema(
            question_key=key,
            question_text=question,
            answer=None,
            level=TruthLevel.UNKNOWN,
            requires_user_input=True,
            sensitive=sensitive,
        )
    if record.level is not TruthLevel.VERIFIED:
        return ApplicationAnswerSchema(
            question_key=key,
            question_text=question,
            answer=None,
            level=record.level,
            evidence=list(record.evidence),
            requires_user_input=True,
            sensitive=sensitive,
        )
    return ApplicationAnswerSchema(
        question_key=key,
        question_text=question,
        answer=str(record.value),
        level=TruthLevel.VERIFIED,
        evidence=list(record.evidence) or ["candidate_profile.answer_memory"],
        requires_user_input=False,
        sensitive=sensitive,
    )


def _section_verified(profile: CandidateProfile, path: str) -> bool:
    """True when a profile section may be copied onto a form.

    An explicit provenance entry wins. With no entry, the in-memory profile is the
    owner's own object (tests and YAML load both look like this) and its populated
    fields are verified. A résumé import writes INFERRED and must not pass.
    """

    if path in profile.provenance:
        return profile.provenance[path].level is TruthLevel.VERIFIED
    return True


def verified_profile_fact(profile: CandidateProfile, key: str) -> AnswerRecord | None:
    """A recognised question answered by a verified profile field.

    Sensitive keys are not bridged. Salary, a background check, and the other
    disclosures in `SENSITIVE_KEYS` are filled only from answer memory the owner
    has verified, never from a nearby profile field that happens to look related.
    """

    if key in SENSITIVE_KEYS:
        return None
    value: str | None = None
    evidence: str | None = None
    if key == "current_employer" and _section_verified(profile, "employment"):
        value = profile.employment[0].employer if profile.employment else None
        evidence = "candidate_profile.employment"
    elif key == "city" and _section_verified(profile, "identity.city"):
        value = profile.identity.city
        evidence = "candidate_profile.identity.city"
    elif key == "state_residence" and _section_verified(profile, "identity.state"):
        value = profile.identity.state
        evidence = "candidate_profile.identity.state"
    elif key == "postal_code" and _section_verified(profile, "identity.postal_code"):
        value = profile.identity.postal_code
        evidence = "candidate_profile.identity.postal_code"
    elif key == "phone" and _section_verified(profile, "identity.phone"):
        value = profile.identity.phone
        evidence = "candidate_profile.identity.phone"
    elif key == "linkedin" and _section_verified(profile, "identity.linkedin_url"):
        value = profile.identity.linkedin_url
        evidence = "candidate_profile.identity.linkedin_url"
    elif key == "website" and _section_verified(profile, "identity.website_url"):
        value = profile.identity.website_url or profile.identity.portfolio_url
        evidence = "candidate_profile.identity.website_url"
    if not value:
        return None
    return AnswerRecord(
        question_key=key,
        value=value,
        level=TruthLevel.VERIFIED,
        evidence=[evidence or "candidate_profile"],
    )


class AnswerResolver:
    """Resolve application questions against the candidate's answer memory."""

    def resolve(
        self,
        profile: CandidateProfile,
        questions: list[str],
        *,
        extra_context: dict[str, str] | None = None,
    ) -> list[Resolution]:
        resolutions: list[Resolution] = []
        seen: set[str] = set()
        for question in questions:
            text = normalize_text(question)
            if not text:
                continue
            key = classify_question(text) or self._fallback_key(text, profile)
            if key in seen:
                continue
            seen.add(key or text.lower())
            # An explicit stored answer keyed on the exact question wins over the
            # catalogue, because the candidate supplied it for this wording.
            # A verified profile fact is used only when memory has nothing stored,
            # and never for a sensitive key.
            record = profile.answer_memory.get(key or "") or self._exact_match(text, profile)
            if record is None and key:
                record = verified_profile_fact(profile, key)
            resolutions.append(
                Resolution(
                    answer=_record_to_answer(key or text.lower(), text, record), matched_key=key
                )
            )
        return resolutions

    @staticmethod
    def _exact_match(question: str, profile: CandidateProfile) -> AnswerRecord | None:
        lowered = question.lower()
        for record in profile.answer_memory.values():
            if record.question_text and record.question_text.lower().strip() == lowered.strip():
                return record
        return None

    @staticmethod
    def _fallback_key(question: str, profile: CandidateProfile) -> str | None:
        """Match an unrecognized question against stored answer keys by token overlap."""

        question_tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
        best: tuple[float, str] | None = None
        for key in profile.answer_memory:
            key_tokens = set(re.findall(r"[a-z0-9]+", key.lower().replace("_", " ")))
            if not key_tokens:
                continue
            overlap = len(key_tokens & question_tokens) / len(key_tokens)
            if overlap >= 0.75 and (best is None or overlap > best[0]):
                best = (overlap, key)
        return best[1] if best else None


def norm_question_text(value: object) -> str:
    """Question wording for comparison: NFKC, casefolded, whitespace collapsed.

    Only the decoration a form adds around a question is stripped (a trailing `*`, `?`
    or `:` and the spaces beside them). Symbols that change the meaning stay: "C++",
    "C#" and "C" are different questions, as are "18+" and "18".
    """

    import unicodedata

    text = " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()
    while text and text[-1] in "*?: ":
        text = text[:-1]
    return text


def verified_memory_answers(
    profile: CandidateProfile, *, resolved: dict[str, str | None] | None = None
) -> list[ApplicationAnswerSchema]:
    """Every VERIFIED answer-memory entry, as a package answer the adapters may type.

    A package carries the whole of the owner's verified memory, not only the handful of
    default questions, because a real application form asks board-specific questions
    (citizenship, language, prior employment, address) that only memory can answer. The
    package hash covers these answers, so each stays bound to the approval.

    Only verified entries are included. A recorded but unverified answer, sensitive or
    not, is never a fillable answer: it surfaces only as an unresolved question when a
    form asks for it. Order is by key so the same memory always produces the same package
    content.

    `resolved` maps each key the resolver already answered for this package to the
    wording the resolver used (our default question). That answer carries OUR wording, so
    a memory entry for the same key is skipped only when it has no wording of its own or
    its wording is the same; when the owner recorded the board's own wording it is emitted
    as well, under the same key, so a form that asks it that way can be answered.
    """

    resolved = resolved or {}
    answers: list[ApplicationAnswerSchema] = []
    for key in sorted(profile.answer_memory):
        record = profile.answer_memory[key]
        if key in resolved:
            own = norm_question_text(record.question_text)
            if not own or own == norm_question_text(resolved[key]):
                continue
        if record.level is not TruthLevel.VERIFIED or record.value in (None, ""):
            continue
        text = str(record.value)
        if not text.strip():
            continue
        answers.append(
            ApplicationAnswerSchema(
                question_key=key,
                question_text=record.question_text,
                answer=text,
                level=TruthLevel.VERIFIED,
                evidence=list(record.evidence) or ["candidate_profile.answer_memory"],
                requires_user_input=False,
                sensitive=key in SENSITIVE_KEYS or record.sensitive,
            )
        )
    return answers


def unresolved_questions(resolutions: list[Resolution]) -> list[str]:
    """Questions that need a human. These block a complete package, by design."""

    return [
        (r.answer.question_text or r.answer.question_key)
        for r in resolutions
        if r.answer.requires_user_input
    ]


def auto_fillable_answers(resolutions: list[Resolution]) -> list[ApplicationAnswerSchema]:
    """Only the answers that may be placed into a form without human review."""

    return [r.answer for r in resolutions if not r.answer.requires_user_input and r.answer.answer]
