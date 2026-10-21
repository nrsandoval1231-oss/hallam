"""Background human handoffs, one per application.

The dashboard must not hold an HTTP request open for the minutes a person needs to
solve a challenge, so the headed browser runs in a thread of its own. This module
is the registry of those threads, keyed by application id. It knows nothing about
the database: the thread's target does the recording.

At most one handoff may be active per application. A person's report reaches the
running thread through its `HandoffControl`; a handoff that has already ended (the
browser was closed, or the server restarted) has no thread to wake, and the caller
records the report directly.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sabra_automation.handoff import HandoffControl
from sabra_domain.errors import ValidationFailure

# States a record moves through. `active` is "the thread is alive".
STATE_STARTING = "starting"
STATE_OPEN = "open"
STATE_NEEDS_REPORT = "needs_report"


@dataclass(slots=True)
class HandoffRecord:
    application_id: str
    url: str
    control: HandoffControl
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    state: str = STATE_STARTING
    outcome: str | None = None
    detail: str | None = None
    thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def as_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "url": self.url,
            "active": self.active,
            "state": self.state,
            "outcome": self.outcome,
            "detail": self.detail,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "timeout_seconds": self.control.timeout_seconds,
        }


class HandoffManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, HandoffRecord] = {}
        self._runs: set[str] = set()

    def begin_run(self, application_id: str) -> None:
        """Mark a submission run (claim, headless inspection, fill, click) as in flight.

        While it is registered no outcome may be reported for the application: the run is
        still deciding what happened, and a report would release the claim under it.
        """

        with self._lock:
            if application_id in self._runs:
                raise ValidationFailure(
                    f"a submission is already in progress for application {application_id}"
                )
            self._runs.add(application_id)

    def end_run(self, application_id: str) -> None:
        with self._lock:
            self._runs.discard(application_id)

    def run_in_progress(self, application_id: str) -> bool:
        with self._lock:
            return application_id in self._runs

    def running_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._runs)

    def active_ids(self) -> list[str]:
        with self._lock:
            return [key for key, record in self._records.items() if record.active]

    def start(
        self,
        application_id: str,
        *,
        url: str,
        timeout_seconds: float,
        target: Callable[[HandoffRecord], None],
    ) -> HandoffRecord:
        """Run `target(record)` in a thread. Refuses a second handoff while one is active."""

        with self._lock:
            existing = self._records.get(application_id)
            if existing is not None and existing.active:
                raise ValidationFailure(
                    f"a human handoff is already active for application {application_id}"
                )
            record = HandoffRecord(
                application_id=application_id,
                url=url,
                control=HandoffControl(timeout_seconds),
            )

            def runner() -> None:
                try:
                    target(record)
                finally:
                    record.finished_at = datetime.now(UTC)

            thread = threading.Thread(
                target=runner, name=f"sabra-handoff-{application_id}", daemon=True
            )
            record.thread = thread
            self._records[application_id] = record
            thread.start()
            return record

    def get(self, application_id: str) -> HandoffRecord | None:
        with self._lock:
            return self._records.get(application_id)

    def active(self, application_id: str) -> bool:
        record = self.get(application_id)
        return record is not None and record.active

    def report(self, application_id: str, outcome: str, *, wait_seconds: float = 20.0) -> bool:
        """Deliver an outcome to a running handoff and wait for it to finish recording.

        Returns False when no handoff is running for the application, in which case
        the caller records the outcome itself.
        """

        record = self.get(application_id)
        if record is None or not record.active:
            return False
        record.control.report(outcome)
        thread = record.thread
        if thread is not None:
            thread.join(wait_seconds)
        return True

    def clear(self) -> None:
        """Forget finished records. For tests; an active handoff is left alone."""

        with self._lock:
            self._records = {k: v for k, v in self._records.items() if v.active}
            self._runs.clear()


# One registry per process: the API is the only long-lived caller.
HANDOFFS = HandoffManager()
