"""One-line readable summaries of event payloads, for history lists.

Event payloads are the audit record and can be large (a ranked entry, a submission
attempt). A history list needs a short description of each, never the whole payload, and
never a nested blob a page would have to render.
"""

from __future__ import annotations

from typing import Any


def summarize_payload(payload: Any, *, limit: int = 160) -> str:
    """`key: value; key: value` for the scalar fields of a payload, cut to `limit` characters."""

    if not isinstance(payload, dict):
        return _clip(str(payload), limit) if payload not in (None, "", {}, []) else ""
    parts: list[str] = []
    for key, value in payload.items():
        text = _scalar(value)
        if text:
            parts.append(f"{str(key).replace('_', ' ')}: {text}")
    return _clip("; ".join(parts), limit)


def _scalar(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _clip(" ".join(value.split()), 80)
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, str) for item in value) and len(value) <= 3:
            return _clip(", ".join(" ".join(item.split()) for item in value), 80)
        return f"{len(value)} item(s)"
    return ""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"
