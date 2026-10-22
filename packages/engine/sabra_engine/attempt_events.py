"""Reading one application's event log as a sequence of attempts.

An application can be tried more than once (BLOCKED or FAILED, then Apply again).
Every try begins with APPLICATION_STARTED, so "has this attempt been left unresolved?"
is a question about the events after the *last* start, in order. Reading the whole log
as a set would let an old UNCONFIRMED event poison a clean retry, or hide a new one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sabra_domain.enums import EventType

# `source` values written by `_finish_handoff` for a person's own report.
REPORT_SOURCES = frozenset({"user_report", "user_confirmation"})
# HANDED_TO_HUMAN states in which no thread is waiting but a person can report.
WAITING_STATES = frozenset(
    {"needs_report", "browser_closed", "error", "server_stopped", "interrupted"}
)


def current_attempt(events: Sequence[Any]) -> list[Any]:
    """Events after the most recent APPLICATION_STARTED (all of them if there is none)."""

    start = -1
    for index, event in enumerate(events):
        if event.event_type == EventType.APPLICATION_STARTED.value:
            start = index
    return list(events[start + 1 :])


def manual_info(events: Sequence[Any]) -> dict[str, Any] | None:
    """The manual-handoff facts of the latest attempt, or None when it was not manual.

    A manual handoff is an approved application on a source with no adapter: the posting
    is opened for the person and nothing is typed. The attempt says so in its
    APPLICATION_STARTED event, and names the saved files in its HANDED_TO_HUMAN event.
    """

    started = None
    for index, event in enumerate(events):
        if event.event_type == EventType.APPLICATION_STARTED.value:
            started = index
    if started is None or not (events[started].payload or {}).get("manual"):
        return None
    info: dict[str, Any] = {"documents": {}, "url": (events[started].payload or {}).get("url")}
    for event in events[started + 1 :]:
        payload = event.payload or {}
        if (
            event.event_type == EventType.HANDED_TO_HUMAN.value
            and payload.get("manual")
            and payload.get("documents")
        ):
            info["documents"] = dict(payload["documents"])
    return info


def attempt_has_unconfirmed(events: Sequence[Any]) -> bool:
    return any(
        e.event_type == EventType.APPLICATION_UNCONFIRMED.value for e in current_attempt(events)
    )


def unresolved_unconfirmed(events: Sequence[Any]) -> bool:
    """An UNCONFIRMED click in this attempt that no person has reported on since."""

    attempt = current_attempt(events)
    last = None
    for index, event in enumerate(attempt):
        if event.event_type == EventType.APPLICATION_UNCONFIRMED.value:
            last = index
    if last is None:
        return False
    return not any(
        (event.payload or {}).get("source") in REPORT_SOURCES for event in attempt[last + 1 :]
    )


def waiting_for_person(events: Sequence[Any]) -> bool:
    """Whether the attempt has stopped and is waiting for a person's report.

    True only when the latest event of the attempt says so: an unconfirmed click, or a
    handoff that ended without an outcome. A run that is still going (or whose browser
    is still open) leaves a different event last, and a direct report is then refused.
    """

    attempt = current_attempt(events)
    if not attempt:
        return False
    last = attempt[-1]
    if last.event_type == EventType.APPLICATION_UNCONFIRMED.value:
        return True
    return (
        last.event_type == EventType.HANDED_TO_HUMAN.value
        and (last.payload or {}).get("state") in WAITING_STATES
    )


def submission_evidence(application: Any, events: Sequence[Any]) -> str | None:
    """Why this application may have reached the employer, or None when it cannot have.

    Used wherever an owner may rebuild or give up on a BLOCKED or FAILED application:
    that is only safe when nothing was submitted. Evidence is any of a recorded
    submission time, an APPLICATION_SUBMITTED event, or an UNCONFIRMED click in the
    latest attempt that no person has reported on since.
    """

    if getattr(application, "submission_time", None) is not None:
        return "already submitted"
    if any(e.event_type == EventType.APPLICATION_SUBMITTED.value for e in events):
        return "already submitted"
    if unresolved_unconfirmed(events):
        return (
            "a submit click is unconfirmed and may have reached the employer; "
            "report its outcome first"
        )
    return None
