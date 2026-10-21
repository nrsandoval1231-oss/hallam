"""Matching verified answers to form fields, and writing them kind-aware.

The rules are all about not guessing:

* Only a VERIFIED answer is ever typed. `requires_user_input=False` is not enough: the
  schema defaults it to False for an answer whose level is UNKNOWN or INFERRED.
* A verified answer is matched to a field by the field's question key, or by the
  field's wording against the answer's recorded question text. Nothing is matched by
  position or by "first field with that key".
* A *key* match comes from a phrase catalogue, and a phrase also appears in questions it
  does not answer ("If you are not a U.S. citizen, what is your visa type?"). So a key
  match may be typed only into a yes/no control with a yes/no answer, or when the
  field's wording is the one recorded with the answer. Otherwise the field is
  unresolved and blocks.
* A `<select>` is set only when exactly one option equals the answer, ignoring case and
  whitespace. There is no substring fallback: "Yes" is not "Yes - I will require
  sponsorship".
* A field is written through `write_value`, which knows what kind of control it is.
  A `page.fill` on a `<select>`, a checkbox or a radio group throws or does the wrong
  thing, so nothing else writes to a page.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from sabra_documents.answers import norm_question_text
from sabra_domain.enums import TruthLevel
from sabra_domain.schemas import ApplicationAnswerSchema, ApplicationPackageSchema

from .base import FormField

_AFFIRMATIVE = frozenset({"yes", "y", "true", "checked", "check", "on"})
_NEGATIVE = frozenset({"no", "n", "false", "unchecked", "off"})
_YES_NO = _AFFIRMATIVE | _NEGATIVE
# An option list longer than this was cut by the page reader; see ENUMERATE_FIELDS_JS.
OPTION_READ_LIMIT = 1000


def _collapse(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def norm_text(value: Any) -> str:
    """Question wording for comparison; see `sabra_documents.answers.norm_question_text`."""

    return norm_question_text(value)


def exact_option(options: list[str], value: str) -> str | None:
    """The first option equal to `value` (case and whitespace aside), else None."""

    target = _collapse(value)
    if not target:
        return None
    for option in options:
        if _collapse(option) == target:
            return option
    return None


def option_problem(field: FormField, value: str) -> str | None:
    """Why `value` cannot be written to this field, or None when it can.

    Only controls with a fixed set of choices can fail: a select with no exactly equal
    option, a radio (its choices are separate controls this adapter does not group), and
    a checkbox answered with something that is neither yes nor no.
    """

    kind = (field.kind or "text").lower()
    if kind == "select":
        if not field.options:
            return "the choices of this drop-down could not be read, so no option can be chosen"
        if exact_option(field.options, value) is None:
            shown = "; ".join(option.strip() for option in field.options[:6])
            cut = (
                f" (the page reader stopped at {OPTION_READ_LIMIT} options, so the list may be cut)"
                if len(field.options) >= OPTION_READ_LIMIT
                else ""
            )
            return (
                f"no option exactly matches the verified answer {value!r} "
                f"(options: {shown}{'; ...' if len(field.options) > 6 else ''}){cut}"
            )
        return None
    if kind == "radio":
        return "radio choices are not filled automatically; a person must choose"
    if kind == "checkbox" and _collapse(value) not in _YES_NO:
        return f"a checkbox needs a yes or no answer, not {value!r}"
    return None


def write_value(page: Any, field: FormField, selector: str, value: str) -> bool:
    """Write `value` to the control the way its kind requires.

    Returns True when the field now holds the answer, False when it was not written
    (the caller must treat that field as unfilled). A select is set by its exact option
    label; a checkbox is checked for an affirmative answer and left alone for a negative
    one; a radio is never written here.
    """

    if option_problem(field, value) is not None:
        return False
    kind = (field.kind or "text").lower()
    if kind == "select":
        page.select_option(selector, exact_option(field.options, value))
        return True
    if kind == "checkbox":
        if _collapse(value) in _AFFIRMATIVE:
            page.check(selector)
        return True
    if kind == "file":
        return False
    page.fill(selector, value)
    return True


def is_typable(answer: ApplicationAnswerSchema) -> bool:
    """Whether an answer may be typed: VERIFIED, non-empty, not waiting on the owner."""

    return bool(
        answer.answer
        and str(answer.answer).strip()
        and answer.level is TruthLevel.VERIFIED
        and not answer.requires_user_input
    )


def verified_answers(package: ApplicationPackageSchema) -> list[ApplicationAnswerSchema]:
    return [answer for answer in package.answers if is_typable(answer)]


@dataclass(slots=True)
class AnswerMatch:
    """The outcome of looking for the verified answer to one form question."""

    answer: ApplicationAnswerSchema | None = None
    # "wording" (the field's text equals the answer's recorded question text) or "key".
    via: str | None = None
    # Why a key match was refused, when it was.
    refused: str | None = None


def match_answer(
    package: ApplicationPackageSchema,
    field: FormField,
    *,
    question_key: str | None,
    key_is_trusted: bool = False,
) -> AnswerMatch:
    """Find the verified package answer for a form field.

    By wording first (the owner recorded the exact question, so that is the strongest
    evidence), then by question key, which is allowed only for a yes/no answer in a
    select or checkbox. A refused key match carries the reason. `key_is_trusted` is for a
    field recognised by its own id or autocomplete attribute as holding that key (a
    `city` input): there the key is not a guess from wording and any answer may be typed.
    """

    usable = verified_answers(package)
    wanted = norm_text(field.label)
    if wanted:
        for answer in usable:
            if answer.question_text and norm_text(answer.question_text) == wanted:
                return AnswerMatch(answer, "wording")
    if question_key:
        for answer in usable:
            if answer.question_key != question_key:
                continue
            kind = (field.kind or "text").lower()
            if key_is_trusted or (
                _collapse(answer.answer) in _YES_NO and kind in {"select", "checkbox"}
            ):
                return AnswerMatch(answer, "key")
            return AnswerMatch(
                refused=(
                    f"the form's question {(field.label or field.name)!r} only resembles "
                    f"{question_key!r}; a verified answer is typed into a free-text field only "
                    "when the question wording was recorded with it (record the answer again "
                    "with the exact question wording and verify it)"
                    if kind not in {"select", "checkbox"}
                    else f"the answer for {question_key!r} is not yes or no, so it is not "
                    "chosen from this drop-down by key; record it with the exact question "
                    "wording"
                )
            )
    return AnswerMatch()
