"""Playwright-backed page driver.

The adapter talks to `FormPage`, not to Playwright, so the automation logic is
testable without a browser and so a different driver could be substituted later.

Bot-protection detection lives here because it is a property of *the page*, not of
any one adapter, and because the detection must fire before any interaction.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from sabra_domain.enums import SafetyStopKind
from sabra_domain.errors import SafetyStop

# Signals that a page is a bot-protection interstitial rather than the form.
BOT_WALL_SIGNALS: tuple[str, ...] = (
    "vercel security checkpoint",
    "just a moment",
    "attention required",
    "checking your browser",
    "enable javascript and cookies to continue",
    "access denied",
    "are you a human",
    "cloudflare",
)

# Selectors whose presence means an interactive challenge exists on the page.
CAPTCHA_SELECTORS: tuple[str, ...] = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[title*="captcha" i]',
    'iframe[src*="turnstile"]',
    ".g-recaptcha",
    ".h-captcha",
    "[data-sitekey]",
)

LOGIN_SIGNALS: tuple[str, ...] = (
    "sign in to continue",
    "log in to continue",
    "create an account to apply",
    "verify your identity",
)


class FormPage(Protocol):
    """The operations an adapter needs from a browser page."""

    def open(self, url: str) -> None: ...
    def title(self) -> str: ...
    def current_url(self) -> str: ...
    def evaluate(self, script: str) -> Any: ...
    def body_text(self) -> str: ...
    def fill(self, selector: str, value: str) -> None: ...
    def select_option(self, selector: str, value: str) -> None: ...
    def check(self, selector: str) -> None: ...
    def set_input_files(self, selector: str, path: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def wait(self, milliseconds: int) -> None: ...
    def screenshot(self, path: str) -> str | None: ...
    def close(self) -> None: ...


def looks_like_bot_wall(title: str, body_text: str) -> bool:
    haystack = f"{title}\n{body_text}".lower()
    return any(signal in haystack for signal in BOT_WALL_SIGNALS)


def looks_like_login_wall(body_text: str) -> bool:
    lowered = (body_text or "").lower()
    return any(signal in lowered for signal in LOGIN_SIGNALS)


class PlaywrightPage:
    """Concrete `FormPage` over a Playwright page."""

    def __init__(self, page: Any, *, timeout_ms: int = 30_000, wait_after_load_ms: int = 2500) -> None:
        self._page = page
        self.timeout_ms = timeout_ms
        self.wait_after_load_ms = wait_after_load_ms

    def open(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        # Single-page application: wait for the form to hydrate rather than guessing
        # at a network-idle event, which never settles on boards that poll.
        self._page.wait_for_timeout(self.wait_after_load_ms)

    def title(self) -> str:
        return self._page.title()

    def current_url(self) -> str:
        return self._page.url

    def evaluate(self, script: str) -> Any:
        return self._page.evaluate(script)

    def body_text(self) -> str:
        try:
            return self._page.evaluate("() => (document.body && document.body.innerText) || ''")
        except Exception:  # noqa: BLE001 - a missing body must not mask the real error
            return ""

    def fill(self, selector: str, value: str) -> None:
        self._page.fill(selector, value, timeout=self.timeout_ms)

    def select_option(self, selector: str, value: str) -> None:
        self._page.select_option(selector, value, timeout=self.timeout_ms)

    def check(self, selector: str) -> None:
        self._page.check(selector, timeout=self.timeout_ms)

    def set_input_files(self, selector: str, path: str) -> None:
        self._page.set_input_files(selector, path, timeout=self.timeout_ms)

    def click(self, selector: str) -> None:
        self._page.click(selector, timeout=self.timeout_ms)

    def wait(self, milliseconds: int) -> None:
        self._page.wait_for_timeout(milliseconds)

    def screenshot(self, path: str) -> str | None:
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=path, full_page=True)
            return path
        except Exception:  # noqa: BLE001 - evidence capture must not fail the run
            return None

    def close(self) -> None:
        with suppress(Exception):
            self._page.close()


class BrowserSession:
    """Owns one Chromium instance for a bounded operation."""

    def __init__(self, *, headless: bool = True, timeout_ms: int = 30_000, user_agent: str | None = None):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent
        self._playwright = None
        self._browser = None

    def __enter__(self) -> BrowserSession:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def new_page(self) -> PlaywrightPage:
        if self._browser is None:
            raise SafetyStop(
                SafetyStopKind.MATERIAL_AMBIGUITY,
                "browser session is not started; use it as a context manager",
            )
        context = self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1440, "height": 900},
        )
        return PlaywrightPage(context.new_page(), timeout_ms=self.timeout_ms)

    def close(self) -> None:
        # Teardown must never mask the real error, so both handles are released
        # best-effort rather than propagating a close-time failure.
        with suppress(Exception):
            if self._browser is not None:
                self._browser.close()
        with suppress(Exception):
            if self._playwright is not None:
                self._playwright.stop()
        self._browser = None
        self._playwright = None
