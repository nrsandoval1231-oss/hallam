"""Browser doubles shared by the adapter and execution tests. No browser, no network."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sabra_automation.greenhouse_adapter import ENUMERATE_FIELDS_JS, GreenhouseApplicationAdapter

GH_URL = "https://job-boards.greenhouse.io/revance/jobs/9001"


class FakeFormPage:
    """A `FormPage` driven by the real captured field inventory.

    By default a click on the submit control changes nothing, so the form is still
    present at the same URL: that is NOT a confirmation. Tests opt into a page that
    navigates (`confirmation_url`), loses its form (`form_gone_after_submit`), or runs
    arbitrary effects (`after_submit`).
    """

    def __init__(
        self,
        capture: dict,
        *,
        title: str | None = None,
        body: str | None = None,
        captcha: bool | None = None,
        submit_selector: str | None = "button[type='submit']",
        confirmation_url: str | None = None,
        form_gone_after_submit: bool = False,
        confirmation_body: str | None = None,
        confirmation_selectors: tuple[str, ...] = (),
        after_submit: Callable[[FakeFormPage], None] | None = None,
        click_raises: Exception | None = None,
        fill_raises: Exception | None = None,
        submit_count: int = 1,
    ) -> None:
        self.capture = capture
        self._title = title or capture.get("title") or "Job Application"
        self._body = body if body is not None else "Apply for this job"
        self._captcha = capture.get("captcha", False) if captcha is None else captcha
        self._submit_selector = submit_selector
        self._submit_count = submit_count
        self._url = GH_URL
        self.form_present = True
        self.confirmation_present: tuple[str, ...] = ()
        self._confirmation_url = confirmation_url
        self._form_gone_after_submit = form_gone_after_submit
        self._confirmation_body = confirmation_body
        self._confirmation_selectors = confirmation_selectors
        self._after_submit = after_submit
        self._click_raises = click_raises
        self._fill_raises = fill_raises
        self.filled: dict[str, str] = {}
        self.files: dict[str, str] = {}
        self.clicked: list[str] = []
        self.screenshots: list[str] = []
        self.closed = False

    # -- FormPage ------------------------------------------------------

    def open(self, url: str) -> None:
        self._url = url

    def title(self) -> str:
        return self._title

    def current_url(self) -> str:
        return self._url

    def evaluate(self, script: str) -> Any:
        if script.strip() == ENUMERATE_FIELDS_JS.strip():
            return [
                {
                    "id": field.get("id"),
                    "name": field.get("name"),
                    "ariaLabel": field.get("ariaLabel"),
                    "autocomplete": field.get("autocomplete"),
                    "context": field.get("context"),
                    "index": index,
                    "tag": field.get("tag"),
                    "type": field.get("type"),
                    "required": field.get("required"),
                    "label": field.get("label"),
                    "placeholder": field.get("placeholder"),
                    "value": field.get("value"),
                    "options": field.get("options") or [],
                }
                for index, field in enumerate(self.capture["fields"])
            ]
        if ".some(s => !!document.querySelector(s))" in script:
            return self._captcha
        if "querySelectorAll" in script:
            selector = script.split("querySelectorAll(")[-1].split(")")[0].strip().strip("'\"")
            return self._count(selector)
        return None

    def _count(self, selector: str) -> int:
        # Normalize quote style: the adapter builds selectors in either style and the
        # real DOM does not care, but a naive comparison does.
        normalized = selector.replace("'", '"')
        if selector in self.confirmation_present:
            return 1
        if normalized.replace("'", '"') == (self._submit_selector or "").replace("'", '"'):
            return self._submit_count if self.form_present else 0
        if selector in {"#application-form", "#application_form", ".application-form"}:
            return 0
        if selector.startswith("#"):
            identifier = selector[1:]
            return sum(1 for f in self.capture["fields"] if f.get("id") == identifier)
        if "type=" in normalized and "file" in normalized:
            return sum(1 for f in self.capture["fields"] if f.get("type") == "file")
        if "recaptcha" in selector or "captcha" in selector or "sitekey" in selector:
            return 1 if self._captcha else 0
        if selector.startswith('input[name="'):
            name = selector.split('"')[1]
            return sum(1 for f in self.capture["fields"] if f.get("id") == name)
        return 0

    def body_text(self) -> str:
        return self._body

    def fill(self, selector: str, value: str) -> None:
        if self._fill_raises is not None:
            raise self._fill_raises
        self.filled[selector] = value

    def select_option(self, selector: str, value: str | None = None, *, label: str | None = None) -> None:
        self.filled[selector] = label if label is not None else str(value)

    def check(self, selector: str) -> None:
        self.filled[selector] = "checked"

    def uncheck(self, selector: str) -> None:
        self.filled[selector] = "unchecked"

    def set_input_files(self, selector: str, path: str) -> None:
        self.files[selector] = path

    def click(self, selector: str) -> None:
        self.clicked.append(selector)
        if self._click_raises is not None:
            raise self._click_raises
        if self._confirmation_url:
            self._url = self._confirmation_url
        if self._form_gone_after_submit:
            self.form_present = False
        if self._confirmation_body is not None:
            self._body = self._confirmation_body
        self.confirmation_present = self._confirmation_selectors
        if self._after_submit is not None:
            self._after_submit(self)

    def wait(self, milliseconds: int) -> None:
        pass

    def screenshot(self, path: str) -> str | None:
        self.screenshots.append(path)
        return path

    def close(self) -> None:
        self.closed = True


def make_adapter(
    capture: dict, **page_kwargs: Any
) -> tuple[GreenhouseApplicationAdapter, list[FakeFormPage], list[FakeFormPage]]:
    """Adapter whose page factory yields one scripted fake page per call."""

    pages: list[FakeFormPage] = []

    def factory(_url: str) -> FakeFormPage:
        page = FakeFormPage(capture, **page_kwargs)
        pages.append(page)
        return page

    adapter = GreenhouseApplicationAdapter(dry_run=True, page_factory=factory)
    return adapter, pages, pages  # `pages` is populated lazily, so it is the same list


def answerable_capture(capture: dict, *, answerable_keys: set[str] | None = None) -> dict:
    """The captured form reduced to the fields this test can actually answer.

    Derived from the real inventory rather than invented: the real Natera form asks
    board-specific required questions ("Have you previously worked at Natera?") that
    no memory can answer, so a submission-path test needs a form whose required
    questions are all resolvable. Everything the adapter recognizes as a known
    section (identity, employment, education) is kept.
    """

    from sabra_documents.answers import classify_question

    answerable = answerable_keys or {"work_authorization"}
    keep = []
    for field in capture["fields"]:
        identifier = (field.get("id") or "").lower()
        label = field.get("label") or ""
        if identifier.startswith("question_"):
            if classify_question(label) in answerable:
                keep.append(field)
            continue
        # Keep identity/file/employment/education fields, drop the phone-country
        # widget and any unnamed inputs the enumerator could not identify.
        if identifier and not identifier.startswith("iti-"):
            keep.append(field)
    return {**capture, "fields": keep, "captcha": False}
