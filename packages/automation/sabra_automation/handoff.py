"""Headed-browser handoff for a form this product must not submit itself.

A CAPTCHA, Turnstile, hCaptcha, reCAPTCHA, or any challenge the detector could
not rule out is solved by the person at the machine. This module opens the
already-filled form in a visible browser and does not click Submit. The outcome
is either the confirmation page or the person's own report.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CONFIRMATION_SIGNALS: tuple[str, ...] = (
    "thank you",
    "application submitted",
    "application has been submitted",
    "we received your application",
    "we have received",
    "your application was submitted",
)

HANDOFF_OUTCOMES = frozenset({"submitted", "abandoned", "failed"})


@dataclass(slots=True)
class HandoffResult:
    outcome: str
    source: str
    confirmation_text: str | None = None
    url: str | None = None
    error: str | None = None
    opened_headed: bool = False

    @property
    def submitted(self) -> bool:
        return self.outcome == "submitted"


def confirmation_in(text: str | None) -> bool:
    lowered = (text or "").lower()
    return any(signal in lowered for signal in CONFIRMATION_SIGNALS)


class PlaywrightHandoff:
    """Open a headed Chromium, fill, and wait. Never clicks the submit control."""

    def __init__(self, *, prompt: Callable[[], str] | None = None, polls: int = 3) -> None:
        self.prompt = prompt
        self.polls = polls

    def run(
        self,
        *,
        adapter: Any,
        package: Any,
        form: Any,
        url: str,
        settings: Any = None,
    ) -> HandoffResult:
        from .playwright_driver import BrowserSession

        timeout_ms = 30_000
        user_agent = None
        if settings is not None:
            timeout_ms = int(getattr(settings, "playwright_timeout_ms", 30_000))
            user_agent = getattr(settings, "discovery_user_agent", None)
        session = BrowserSession(headless=False, timeout_ms=timeout_ms, user_agent=user_agent)
        session.__enter__()
        try:
            page = session.new_page()
            page.open(url)
            if form is not None and package is not None and hasattr(adapter, "apply_to_page"):
                adapter.apply_to_page(page, package, form)
            return self._await(page, url)
        finally:
            session.close()

    def _await(self, page: Any, url: str) -> HandoffResult:
        for _ in range(self.polls):
            body = page.body_text() or ""
            if confirmation_in(body):
                return HandoffResult(
                    outcome="submitted",
                    source="confirmation_page",
                    confirmation_text=body[:2000],
                    url=page.current_url() or url,
                    opened_headed=True,
                )
            page.wait(400)
        choice = "abandoned"
        if self.prompt is not None:
            raw = (self.prompt() or "").strip().lower()
            choice = raw if raw in HANDOFF_OUTCOMES else "abandoned"
        body = page.body_text() or ""
        if confirmation_in(body):
            return HandoffResult(
                outcome="submitted",
                source="confirmation_page",
                confirmation_text=body[:2000],
                url=page.current_url() or url,
                opened_headed=True,
            )
        return HandoffResult(
            outcome=choice,
            source="user_confirmation" if self.prompt is not None else "no_confirmation",
            confirmation_text=body[:2000] if body else None,
            url=page.current_url() or url,
            opened_headed=True,
            error=None if choice == "submitted" else f"human reported the attempt as {choice}",
        )
