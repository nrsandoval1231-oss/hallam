"""Application question memory (requirement 18).

Reusable, structured answers to common questions. The two rules that matter:

* an answer the candidate did not supply is **UNKNOWN**, never a guess; and
* sensitive or voluntary disclosures remain explicitly user-controlled, so the
  system will surface them for the human and never auto-fill them, however
  complete the stored value looks.
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
    ("race", ("race", "ethnicity", "hispanic")),
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
    if record is None or record.value in (None, ""):
        return ApplicationAnswerSchema(
            question_key=key,
            question_text=question,
            answer=None,
            level=TruthLevel.UNKNOWN,
            requires_user_input=True,
            sensitive=key in SENSITIVE_KEYS,
        )
    sensitive = key in SENSITIVE_KEYS or record.sensitive
    if sensitive or record.user_controlled:
        # Deliberately withheld rather than filled. The value exists, but supplying
        # it is the candidate's decision to make per application.
        return ApplicationAnswerSchema(
            question_key=key,
            question_text=question,
            answer=None,
            level=record.level,
            evidence=list(record.evidence),
            requires_user_input=True,
            sensitive=True,
        )
    if record.level is not TruthLevel.VERIFIED:
        return ApplicationAnswerSchema(
            question_key=key,
            question_text=question,
            answer=None,
            level=record.level,
            evidence=list(record.evidence),
            requires_user_input=True,
            sensitive=False,
        )
    return ApplicationAnswerSchema(
        question_key=key,
        question_text=question,
        answer=str(record.value),
        level=TruthLevel.VERIFIED,
        evidence=list(record.evidence) or ["candidate_profile.answer_memory"],
        requires_user_input=False,
        sensitive=False,
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
            record = profile.answer_memory.get(key or "") or self._exact_match(text, profile)
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
