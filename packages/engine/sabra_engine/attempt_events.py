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
