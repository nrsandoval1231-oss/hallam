"""Small formatting helpers shared by the API and the services that feed pages."""

from __future__ import annotations

from typing import Any


def compensation_text(job: Any) -> str:
    """The posted pay as a person would read it, or "not listed"."""

    if job is None:
        return "not listed"
    low, high = job.salary_min, job.salary_max
    if not low and not high:
        return "not listed"
    currency = job.salary_currency or "USD"
    symbol = "$" if currency == "USD" else f"{currency} "

    def money(value: float) -> str:
        return f"{symbol}{value:,.0f}"

    if low and high and low != high:
        text = f"{money(low)} to {money(high)}"
    elif low and high:
        text = money(low)
    elif low:
        text = f"from {money(low)}"
    else:
        text = f"up to {money(high)}"
    suffix = {"YEAR": "a year", "MONTH": "a month", "HOUR": "an hour"}.get(
        str(job.salary_period or "").upper(), ""
    )
    return f"{text} {suffix}".strip()
