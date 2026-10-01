"""A FormPage backed by a local HTML fixture. No browser and no network."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[dict[str, Any]] = []
        self._label_for: dict[str, str] = {}
        self._current_label: str | None = None
        self._label_buf: list[str] = []
        self.title = ""
        self._in_title = False
        self._title_buf: list[str] = []
        self.body_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
            self._title_buf = []
        if tag == "label":
            self._current_label = attr.get("for") or ""
            self._label_buf = []
        if tag in {"input", "textarea", "select"}:
            kind = attr.get("type") or ("textarea" if tag == "textarea" else "text")
            if tag == "select":
                kind = "select"
            if kind == "hidden":
                return
            self.fields.append(
                {
                    "id": attr.get("id") or None,
                    "tag": tag,
                    "type": kind,
                    "required": "required" in attr or attr.get("aria-required") == "true",
                    "label": None,
                    "placeholder": attr.get("placeholder"),
                    "value": "",
                    "options": [],
                    "_for": attr.get("id") or "",
                }
            )
        if tag == "option" and self.fields and self.fields[-1]["tag"] == "select":
            self.fields[-1]["options"].append(attr.get("value") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            self.title = "".join(self._title_buf).strip()
        if tag == "label" and self._current_label is not None:
            self._label_for[self._current_label] = " ".join("".join(self._label_buf).split())
            self._current_label = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
        if self._current_label is not None:
            self._label_buf.append(data)
        self.body_parts.append(data)

    def finish(self) -> None:
        for field in self.fields:
            field["label"] = self._label_for.get(field.pop("_for", ""), None)


class HtmlFormPage:
    """Enough of a page for the adapters to inspect and fill a fixture form."""

    def __init__(self, html: str, *, captcha_probe_raises: bool = False) -> None:
        parser = _Extractor()
        parser.feed(html)
        parser.finish()
        self.html = html
        self.fields = parser.fields
        self._title = parser.title or "Job Application"
        self._body = " ".join(part.strip() for part in parser.body_parts if part.strip())
        self._url = "http://127.0.0.1:9/fixture"
        self.filled: dict[str, str] = {}
        self.files: dict[str, str] = {}
        self.clicked: list[str] = []
        self.checked: list[str] = []
        self.captcha_probe_raises = captcha_probe_raises
        self.closed = False

    def open(self, url: str) -> None:
        self._url = url

    def title(self) -> str:
        return self._title

    def current_url(self) -> str:
        return self._url

    def evaluate(self, script: str) -> Any:
        if "querySelectorAll('input, textarea, select')" in script or 'querySelectorAll("input, textarea, select")' in script:
            return [dict(field) for field in self.fields]
        if ".some(s => !!document.querySelector(s))" in script:
            if self.captcha_probe_raises:
                raise RuntimeError("challenge probe failed")
            lowered = self.html.lower()
            markers = (
                "recaptcha",
                "hcaptcha",
                "turnstile",
                "data-sitekey",
                "challenges.cloudflare.com",
                "arkoselabs",
                "funcaptcha",
            )
            return any(marker in lowered for marker in markers)
        if ".length" in script and "querySelectorAll(" in script:
            selector = script.split("querySelectorAll(")[-1].split(")")[0].strip().strip("'\"")
            return self._count(selector)
        return None

    def _count(self, selector: str) -> int:
        normalized = selector.replace("'", '"')
        if normalized in {'button[type="submit"]', "button[type='submit']"} or "submit" in selector:
            return 1 if "submit" in self.html.lower() else 0
        if selector.startswith("#"):
            identifier = selector[1:]
            return sum(1 for field in self.fields if field.get("id") == identifier)
        if "file" in selector:
            return sum(1 for field in self.fields if field.get("type") == "file")
        return 0

    def body_text(self) -> str:
        return self._body

    def fill(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    def select_option(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    def check(self, selector: str) -> None:
        self.checked.append(selector)
        self.filled[selector] = "checked"

    def set_input_files(self, selector: str, path: str) -> None:
        self.files[selector] = path

    def click(self, selector: str) -> None:
        self.clicked.append(selector)
        if "submit" in selector:
            self._body = "Thank you. Your application has been submitted."

    def wait(self, milliseconds: int) -> None:
        return None

    def screenshot(self, path: str) -> str | None:
        return path

    def close(self) -> None:
        self.closed = True
