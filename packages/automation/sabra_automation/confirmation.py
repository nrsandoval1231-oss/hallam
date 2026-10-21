"""One definition of "the employer confirmed the application".

Both adapters and the headed handoff decide whether a submission happened here, and
nowhere else, so the rule cannot drift between them.

A submission is confirmed only when BOTH hold:

(a) the page has moved on from the form: its URL (without query or fragment) differs
    from the snapshot taken while the form was open, OR the application form and its
    submit control are no longer on the page; and
(b) an ATS-specific confirmation marker is present.

Body keywords alone never confirm anything. A job description on the same page
routinely says "Thank you for your interest", and a form that failed validation is
still a form. If a click happened and this helper does not confirm, the outcome is
UNCONFIRMED: never FAILED (the employer may have received it) and never SUBMITTED
(nothing proves it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class AtsRules:
    form_selectors: tuple[str, ...]
    confirmation_selectors: tuple[str, ...]
    path_suffixes: tuple[str, ...] = ()
    path_contains: tuple[str, ...] = ()
    # Only honoured after the URL has changed from the form's URL.
    text_markers_after_navigation: tuple[str, ...] = ()


ATS_RULES: dict[str, AtsRules] = {
    "greenhouse": AtsRules(
        form_selectors=(
            'button[type="submit"]',
            'input[type="submit"]',
            "#submit_app",
            "#application-form",
            "#application_form",
        ),
        confirmation_selectors=(
            "#application_confirmation",
            ".application--confirmation",
            '[data-testid*="confirmation"]',
        ),
        path_contains=("/confirmation",),
        text_markers_after_navigation=(
            "application has been submitted",
            "thank you for applying",
        ),
    ),
    "lever": AtsRules(
        form_selectors=(
            'button[type="submit"]',
            'input[type="submit"]',
            "#application-form",
            ".application-form",
        ),
        confirmation_selectors=(),
        path_suffixes=("/thanks",),
    ),
}


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    """What the page looked like while the form was open."""

    url_key: str
    form_present: bool


@dataclass(slots=True)
class Confirmation:
    confirmed: bool
    reason: str
    url: str | None = None
    marker: str | None = None
    text: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "reason": self.reason,
            "url": self.url,
            "marker": self.marker,
        }


def normalize_url(url: str | None) -> str:
    """Scheme, host and path only; no query, no fragment, no trailing slash."""

    parts = urlsplit(url or "")
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


def _count(page: Any, selector: str) -> int:
    value = page.evaluate("() => document.querySelectorAll(" + repr(selector) + ").length")
    return int(value or 0)


def form_present(page: Any, ats: str) -> bool:
    """Whether the application form or its submit control is on the page.

    A probe that cannot run counts as "present", so an unreadable page is never
    mistaken for a page that moved on.
    """

    rules = ATS_RULES.get(ats)
    if rules is None:
        return True
    for selector in rules.form_selectors:
        try:
            if _count(page, selector):
                return True
        except Exception:  # noqa: BLE001 - an unreadable page is not a confirmation
            return True
    return False


def take_snapshot(page: Any, ats: str) -> PageSnapshot:
    return PageSnapshot(
        url_key=normalize_url(_safe_url(page)),
        form_present=form_present(page, ats),
    )


def _safe_url(page: Any) -> str:
    try:
        return page.current_url() or ""
    except Exception:  # noqa: BLE001
        return ""


def detect_confirmation(page: Any, ats: str, before: PageSnapshot) -> Confirmation:
    """Apply the two-part rule against the snapshot taken while the form was open."""

    rules = ATS_RULES.get(ats)
    url = _safe_url(page)
    if rules is None:
        return Confirmation(False, f"no confirmation rules exist for {ats!r}", url=url)

    navigated = normalize_url(url) != before.url_key
    still_on_form = form_present(page, ats)
    if not navigated and still_on_form:
        return Confirmation(
            False,
            "the page is still the application form (same URL, form present)",
            url=url,
        )

    path = urlsplit(url).path.rstrip("/").lower()
    for suffix in rules.path_suffixes:
        if path.endswith(suffix):
            return Confirmation(True, "confirmation URL", url=url, marker=f"path:{suffix}")
    for fragment in rules.path_contains:
        if fragment in path:
            return Confirmation(True, "confirmation URL", url=url, marker=f"path:{fragment}")
    for selector in rules.confirmation_selectors:
        try:
            if _count(page, selector):
                return Confirmation(
                    True, "confirmation element", url=url, marker=f"selector:{selector}"
                )
        except Exception:  # noqa: BLE001 - keep looking; absence is not a confirmation
            continue
    if navigated and rules.text_markers_after_navigation:
        try:
            body = page.body_text() or ""
        except Exception:  # noqa: BLE001
            body = ""
        lowered = body.lower()
        for marker in rules.text_markers_after_navigation:
            if marker in lowered:
                return Confirmation(
                    True,
                    "confirmation text after leaving the form",
                    url=url,
                    marker=f"text:{marker}",
                    text=body[:2000],
                )
    return Confirmation(
        False,
        "the page moved on from the form but shows no confirmation marker",
        url=url,
    )
