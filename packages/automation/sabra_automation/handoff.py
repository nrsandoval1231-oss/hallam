"""Headed-browser handoff for a form this product must not submit itself.

A CAPTCHA, Turnstile, hCaptcha, reCAPTCHA, or any challenge the detector could
not rule out is solved by the person at the machine. This module opens the
already-filled form in a visible browser and does not click Submit.

The browser stays open until one of these happens, and none of them is guessed:

* the person reports an outcome (the dashboard buttons, or the CLI prompt);
* the page itself reaches a confirmed state (`confirmation.detect_confirmation`);
* the person closes the browser or page, which is "needs a report", not an outcome;
* the hard timeout passes.

A handoff that times out or closes without an observed outcome stays APPLYING until
a person reports what happened. This avoids retrying an application that may already
have reached the employer.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .confirmation import (
    PageSnapshot,
    detect_confirmation,
    form_present,
    normalize_url,
)

# What a person can report.
HANDOFF_OUTCOMES = frozenset({"submitted", "abandoned", "failed"})
# What a handoff can end with: a reported outcome, or one of these two non-outcomes.
OUTCOME_TIMEOUT = "timeout"
OUTCOME_CLOSED = "closed"  # browser/page closed with no report: needs a report
# The application left APPLYING while the window was open (someone else resolved it).
OUTCOME_SUPERSEDED = "superseded"
SUPERSEDED_CHECK_SECONDS = 3.0

DEFAULT_TIMEOUT_SECONDS = 2700
POLL_SECONDS = 2.0
MAX_INVALID_ANSWERS = 10


class HandoffLaunchError(Exception):
    """The headed browser never opened, so the person never had the form."""


class HandoffControl:
    """The channel between a running handoff and whoever reports its outcome."""

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self._deadline = time.monotonic() + self.timeout_seconds
        self._event = threading.Event()
        self._outcome: str | None = None
        # Optional probe: True once the application is no longer waiting on this window.
        self.superseded: Callable[[], bool] | None = None
        self._next_superseded_check = 0.0

    def is_superseded(self, every: float = SUPERSEDED_CHECK_SECONDS) -> bool:
        """Poll `superseded` at most once per `every` seconds."""

        if self.superseded is None:
            return False
        now = time.monotonic()
        if now < self._next_superseded_check:
            return False
        self._next_superseded_check = now + every
        try:
            return bool(self.superseded())
        except Exception:  # noqa: BLE001 - an unreadable database does not close the window
            return False

    def report(self, outcome: str) -> None:
        if outcome not in HANDOFF_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(HANDOFF_OUTCOMES)}, got {outcome!r}")
        self._outcome = outcome
        self._event.set()

    @property
    def reported(self) -> str | None:
        return self._outcome

    def wait(self, seconds: float) -> str | None:
        """Sleep up to `seconds`, waking early if an outcome is reported."""

        self._event.wait(max(0.0, min(seconds, self.remaining())))
        return self._outcome

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def timed_out(self) -> bool:
        return self.remaining() <= 0.0


@dataclass(slots=True)
class HandoffResult:
    outcome: str  # submitted | abandoned | failed | timeout | closed | superseded
    source: str
    confirmation_text: str | None = None
    url: str | None = None
    error: str | None = None
    opened_headed: bool = False

    @property
    def submitted(self) -> bool:
        return self.outcome == "submitted"


EventSink = Callable[[str, dict[str, Any]], None]


def _is_closed(page: Any) -> bool:
    probe = getattr(page, "is_closed", None)
    if probe is None:
        return False
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - a page that cannot answer is gone
        return True


class PlaywrightHandoff:
    """Open a headed Chromium, fill, and wait. Never clicks the submit control."""

    def __init__(
        self,
        *,
        prompt: Callable[[], str] | None = None,
        poll_seconds: float = POLL_SECONDS,
        max_invalid_answers: int = MAX_INVALID_ANSWERS,
    ) -> None:
        self.prompt = prompt
        self.poll_seconds = poll_seconds
        self.max_invalid_answers = max_invalid_answers

    def run(
        self,
        *,
        adapter: Any,
        package: Any,
        form: Any,
        url: str,
        settings: Any = None,
        control: HandoffControl | None = None,
        on_event: EventSink | None = None,
        documents: dict[str, str] | None = None,
    ) -> HandoffResult:
        from .playwright_driver import BrowserSession

        timeout_ms = 30_000
        user_agent = None
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
        if settings is not None:
            timeout_ms = int(getattr(settings, "playwright_timeout_ms", 30_000))
            user_agent = getattr(settings, "discovery_user_agent", None)
            timeout_seconds = float(
                getattr(settings, "handoff_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            )
        control = control or HandoffControl(timeout_seconds)
        emit: EventSink = on_event or (lambda _state, _payload: None)

        session = BrowserSession(headless=False, timeout_ms=timeout_ms, user_agent=user_agent)
        try:
            session.__enter__()
            page = session.new_page()
            page.open(url)
        except Exception as exc:
            session.close()
            raise HandoffLaunchError(f"the headed browser could not open {url}: {exc}") from exc

        try:
            # Fill only here, in the headed session the person is looking at. A fill
            # failure is logged and the page is left open: the person can finish by hand.
            if form is not None and package is not None and hasattr(adapter, "apply_to_page"):
                try:
                    adapter.apply_to_page(page, package, form)
                except Exception as exc:  # noqa: BLE001 - never close the person's form
                    emit("fill_failed", {"url": url, "error": str(exc)[:500]})
            ats = str(getattr(adapter, "kind", ""))
            emit("opened", {"url": url})
            if documents:
                # Manual handoff: nothing is typed. These are the files to attach.
                emit("documents_ready", {"url": url, "documents": dict(documents)})
            return self._await(page, ats, url, control, emit)
        finally:
            session.close()

    # -- waiting ---------------------------------------------------------

    def _confirmed(
        self, page: Any, ats: str, snapshot: PageSnapshot | None, url: str
    ) -> HandoffResult | None:
        if snapshot is None:
            return None  # nothing to compare against yet: never confirm
        try:
            found = detect_confirmation(page, ats, snapshot)
        except Exception:  # noqa: BLE001 - an unreadable page is not a confirmation
            return None
        if not found.confirmed:
            return None
        return HandoffResult(
            outcome="submitted",
            source="confirmation_page",
            confirmation_text=found.text or found.reason,
            url=found.url or url,
            opened_headed=True,
        )

    def _current_url(self, page: Any, url: str) -> str:
        try:
            return page.current_url() or url
        except Exception:  # noqa: BLE001
            return url

    def _await(
        self,
        page: Any,
        ats: str,
        url: str,
        control: HandoffControl,
        emit: EventSink,
    ) -> HandoffResult:
        """Wait for a report, a confirmation, a closed window or the timeout.

        The "form was open" snapshot is taken lazily: only once the form is on screen
        and its URL has held across two polls. A bot-wall interstitial, or a redirect
        on the way to the form, is therefore never the baseline, and until a baseline
        exists nothing can be confirmed.
        """

        snapshot: PageSnapshot | None = None
        last_key: str | None = None
        stable = 0
        prompt_state = self._start_prompt(control) if self.prompt is not None else None
        while True:
            reported = control.reported
            if reported is not None:
                if prompt_state is not None:
                    # CLI: a confirmation page that the lazy baseline can vouch for wins
                    # over the typed answer, as it always has.
                    confirmed = self._confirmed(page, ats, snapshot, url)
                    if confirmed is not None:
                        return confirmed
                return HandoffResult(
                    outcome=reported,
                    source="user_confirmation" if prompt_state is not None else "user_report",
                    url=self._current_url(page, url),
                    opened_headed=True,
                    error=None if reported == "submitted" else f"person reported: {reported}",
                )
            if prompt_state is not None and prompt_state["exhausted"]:
                emit("no_valid_answer", {"url": url})
                return HandoffResult(
                    outcome=OUTCOME_CLOSED,
                    source="no_valid_answer",
                    url=self._current_url(page, url),
                    opened_headed=True,
                    error="no valid outcome was entered; report one with `sabra handoff-outcome`",
                )
            if control.timed_out():
                return HandoffResult(
                    outcome=OUTCOME_TIMEOUT,
                    source="timeout",
                    url=self._current_url(page, url),
                    opened_headed=True,
                    error=(
                        f"no outcome was reported within {int(control.timeout_seconds)} seconds"
                    ),
                )
            if _is_closed(page):
                emit("browser_closed", {"url": url})
                return HandoffResult(
                    outcome=OUTCOME_CLOSED,
                    source="browser_closed",
                    url=url,
                    opened_headed=True,
                    error="the browser was closed before an outcome was reported",
                )
            if snapshot is None:
                try:
                    present = form_present(page, ats)
                    key = normalize_url(page.current_url())
                except Exception:  # noqa: BLE001
                    present, key = False, ""
                if present and key == last_key:
                    stable += 1
                else:
                    stable = 1 if present else 0
                last_key = key if present else None
                if stable >= 2:
                    snapshot = PageSnapshot(url_key=key, form_present=True)
            else:
                confirmed = self._confirmed(page, ats, snapshot, url)
                if confirmed is not None:
                    return confirmed
            if control.is_superseded():
                emit("superseded", {"url": url})
                return HandoffResult(
                    outcome=OUTCOME_SUPERSEDED,
                    source="superseded",
                    url=self._current_url(page, url),
                    opened_headed=True,
                    error="the application is no longer waiting on this window",
                )
            control.wait(self.poll_seconds)

    def _start_prompt(self, control: HandoffControl) -> dict[str, bool]:
        """Ask for the outcome on a side thread so the wait can still time out.

        The browser stays on the calling thread (Playwright is thread-affine) and keeps
        being polled. A valid answer is delivered through the control, like a dashboard
        report. A typo is asked again; it is never recorded as an outcome. After
        `max_invalid_answers` the wait ends as "needs a report", not as an abandonment.
        """

        state = {"exhausted": False}

        def ask() -> None:
            for _ in range(self.max_invalid_answers):
                try:
                    raw = (self.prompt() or "").strip().lower() if self.prompt else ""
                except Exception:  # noqa: BLE001 - a closed terminal is not an answer
                    break
                if raw in HANDOFF_OUTCOMES:
                    control.report(raw)
                    return
            state["exhausted"] = True

        threading.Thread(target=ask, name="sabra-handoff-prompt", daemon=True).start()
        return state
