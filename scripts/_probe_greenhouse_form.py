"""Probe a live Greenhouse application form to discover its real field structure.

Not part of the product. Run once to write the adapter against actual selectors
rather than guessed ones.
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

TARGETS = [
    ("revance", "https://job-boards.greenhouse.io/revance/jobs/8696634002"),
    ("natera", "https://job-boards.greenhouse.io/natera/jobs/6179619004"),
]

DUMP_JS = """
() => {
  const out = {title: document.title, url: location.href, fields: [], buttons: [],
               fileInputs: [], iframes: document.querySelectorAll('iframe').length,
               captcha: !!document.querySelector('iframe[src*="recaptcha"], .g-recaptcha, [data-sitekey]'),
               text: (document.body.innerText || '').slice(0, 400)};
  document.querySelectorAll('input, textarea, select').forEach((el) => {
    const labelEl = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
    const wrapLabel = el.closest('label');
    out.fields.push({
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      name: el.getAttribute('name'),
      type: el.getAttribute('type'),
      required: el.required || el.getAttribute('aria-required') === 'true',
      ariaLabel: el.getAttribute('aria-label'),
      placeholder: el.getAttribute('placeholder'),
      autocomplete: el.getAttribute('autocomplete'),
      label: (labelEl ? labelEl.innerText : (wrapLabel ? wrapLabel.innerText : null)),
      value: el.type === 'file' ? null : (el.value || '').slice(0, 60),
      options: el.tagName.toLowerCase() === 'select'
        ? Array.from(el.options).map(o => o.value).slice(0, 12) : null,
    });
  });
  document.querySelectorAll('button, input[type=submit]').forEach((b) => {
    out.buttons.push({text: (b.innerText || b.value || '').trim().slice(0,60),
                      id: b.id || null, type: b.getAttribute('type'), name: b.getAttribute('name')});
  });
  return out;
}
"""


def main() -> None:
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, url in TARGETS:
            page = browser.new_page(user_agent="SabraJobEngine/0.1 (research)")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
                data = page.evaluate(DUMP_JS)
                # Reveal the application form if it sits behind a button.
                for label in ("Apply", "Apply for this job", "Apply Now"):
                    try:
                        button = page.get_by_role("button", name=label, exact=False)
                        if button.count() > 0:
                            button.first.click(timeout=5000)
                            page.wait_for_timeout(3000)
                            data["after_apply_click"] = page.evaluate(DUMP_JS)
                            break
                    except Exception as exc:  # noqa: BLE001
                        data.setdefault("click_errors", []).append(f"{label}: {exc}")
                results[name] = data
                print(f"\n=== {name} :: {url} ===")
                print("title:", data["title"], "| iframes:", data["iframes"], "| captcha:", data["captcha"])
                print("fields:", len(data["fields"]), "| buttons:", data["buttons"])
                for field in data["fields"][:40]:
                    print(
                        f"  {field['tag']:9s} id={str(field['id'])[:24]:24s} "
                        f"name={str(field['name'])[:24]:24s} type={str(field['type'])[:10]:10s} "
                        f"req={str(field['required'])[:5]:5s} label={str(field['label'])[:44]}"
                    )
                print("buttons:", data["buttons"][:10])
                if "after_apply_click" in data:
                    inner = data["after_apply_click"]
                    print(f"AFTER APPLY CLICK -> fields: {len(inner['fields'])}")
                    for field in inner["fields"][:40]:
                        print(
                            f"  {field['tag']:9s} id={str(field['id'])[:24]:24s} "
                            f"name={str(field['name'])[:22]:22s} type={str(field['type'])[:10]:10s} "
                            f"req={str(field['required'])[:5]:5s} label={str(field['label'])[:44]}"
                        )
                    print("inner buttons:", inner["buttons"][:10])
            except Exception as exc:  # noqa: BLE001
                print(f"{name} FAILED: {type(exc).__name__}: {exc}")
                results[name] = {"error": str(exc)}
            finally:
                page.close()
        browser.close()
    with open("_greenhouse_form_probe.json", "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=1)
    print("\nwrote _greenhouse_form_probe.json")


if __name__ == "__main__":
    sys.exit(main())
