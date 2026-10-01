"""Map verified employment and education onto Greenhouse field ids.

The ids come from the captured form (`company-name-0`, `school--0`, and the date
inputs beside them). A field this module does not recognise returns None so the
caller can treat it as an unknown question rather than as history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sabra_domain.schemas import EducationEntry, EmploymentEntry

_MONTHS = {
    "jan": "1",
    "january": "1",
    "feb": "2",
    "february": "2",
    "mar": "3",
    "march": "3",
    "apr": "4",
    "april": "4",
    "may": "5",
    "jun": "6",
    "june": "6",
    "jul": "7",
    "july": "7",
    "aug": "8",
    "august": "8",
    "sep": "9",
    "sept": "9",
    "september": "9",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}

_PRESENT = {"present", "current", "now", "today"}


@dataclass(slots=True)
class FieldPlan:
    """How one recognised history field should be treated."""

    covered: bool
    action: str  # fill | check | blank
    section: str
    value: str | None = None
    reason: str | None = None


def split_date(value: str | None) -> tuple[str | None, str | None]:
    """Return `(month, year)` from the dates candidates actually store.

    `Mar 2019`, `2019-03`, `2019-03-01` and a bare year are understood. `Present`
    is not a date. Nothing else is invented: an unparseable string yields no month.
    """

    if not value:
        return None, None
    text = value.strip()
    if text.lower() in _PRESENT:
        return None, None
    iso = re.match(r"^(\d{4})[-/](\d{1,2})", text)
    if iso:
        return str(int(iso.group(2))), iso.group(1)
    named = re.match(r"^([A-Za-z]+)\.?\s+(\d{4})", text)
    if named:
        month = _MONTHS.get(named.group(1).lower().rstrip("."))
        return month, named.group(2)
    year = re.search(r"(\d{4})", text)
    return None, year.group(1) if year else None


def plan_greenhouse_field(
    name: str,
    employment: list[EmploymentEntry],
    education: list[EducationEntry],
) -> FieldPlan | None:
    """Plan for a Greenhouse history field, or None when `name` is not one."""

    lowered = (name or "").lower()
    employment_match = _employment_slot(lowered)
    if employment_match is not None:
        index, kind = employment_match
        return _employment_plan(index, kind, employment)
    education_match = _education_slot(lowered)
    if education_match is not None:
        index, kind = education_match
        return _education_plan(index, kind, education)
    return None


def _employment_slot(name: str) -> tuple[int, str] | None:
    patterns = (
        (r"company-name-(\d+)", "employer"),
        (r"^title-(\d+)$", "title"),
        (r"start-date-month-(\d+)", "start_month"),
        (r"start-date-year-(\d+)", "start_year"),
        (r"end-date-month-(\d+)", "end_month"),
        (r"end-date-year-(\d+)", "end_year"),
        (r"current-role-(\d+)", "current"),
    )
    for pattern, kind in patterns:
        match = re.search(pattern, name)
        if match:
            return int(match.group(1)), kind
    return None


def _education_slot(name: str) -> tuple[int, str] | None:
    patterns = (
        (r"school--(\d+)", "school"),
        (r"degree--(\d+)", "degree"),
        (r"discipline--(\d+)", "discipline"),
        (r"start-month--(\d+)", "start_month"),
        (r"start-year--(\d+)", "start_year"),
        (r"end-month--(\d+)", "end_month"),
        (r"end-year--(\d+)", "end_year"),
    )
    for pattern, kind in patterns:
        match = re.search(pattern, name)
        if match:
            return int(match.group(1)), kind
    return None


def _missing(index: int, section: str) -> FieldPlan:
    human = "employment" if section == "employment_history" else "education"
    return FieldPlan(
        covered=False,
        action="blank",
        section=section,
        reason=(
            f"{human} block {index + 1} is required and the verified candidate "
            f"profile has no {human} entry for it"
        ),
    )


def _employment_plan(index: int, kind: str, employment: list[EmploymentEntry]) -> FieldPlan:
    section = "employment_history"
    if index >= len(employment):
        return _missing(index, section)
    entry = employment[index]
    start_month, start_year = split_date(entry.start_date)
    end_month, end_year = split_date(entry.end_date)
    current = bool(entry.is_current) or (entry.end_date or "").strip().lower() in _PRESENT
    values = {
        "employer": entry.employer,
        "title": entry.title,
        "start_month": start_month,
        "start_year": start_year,
        "end_month": None if current else end_month,
        "end_year": None if current else end_year,
    }
    if kind == "current":
        if current:
            return FieldPlan(covered=True, action="check", section=section, value="checked")
        return FieldPlan(covered=True, action="blank", section=section)
    if kind in {"end_month", "end_year"} and current:
        return FieldPlan(
            covered=True,
            action="blank",
            section=section,
            reason="current role; end date is not typed",
        )
    value = values.get(kind)
    if value:
        return FieldPlan(covered=True, action="fill", section=section, value=str(value))
    label = kind.replace("_", " ")
    return FieldPlan(
        covered=False,
        action="blank",
        section=section,
        reason=(
            f"{label} for employment entry {index + 1} "
            f"({entry.employer or 'unnamed employer'}) is not in the verified profile"
        ),
    )


def _education_plan(index: int, kind: str, education: list[EducationEntry]) -> FieldPlan:
    section = "education"
    if index >= len(education):
        return _missing(index, section)
    entry = education[index]
    start_month, start_year = split_date(entry.start_date)
    end_month, end_year = split_date(entry.end_date)
    values = {
        "school": entry.institution,
        "degree": entry.degree,
        "discipline": entry.field_of_study,
        "start_month": start_month,
        "start_year": start_year,
        "end_month": end_month,
        "end_year": end_year,
    }
    value = values.get(kind)
    if value:
        return FieldPlan(covered=True, action="fill", section=section, value=str(value))
    label = kind.replace("_", " ")
    return FieldPlan(
        covered=False,
        action="blank",
        section=section,
        reason=(
            f"{label} for education entry {index + 1} "
            f"({entry.institution or 'unnamed school'}) is not in the verified profile"
        ),
    )
