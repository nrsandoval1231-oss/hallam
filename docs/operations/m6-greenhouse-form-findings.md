# M6 — Greenhouse application form findings

> **Historical record.** Captured by driving live Greenhouse boards. Treat the URLs,
> field lists, and page contents as observations from that run. The adapter behavior
> described below is still the implementation.

Evidence gathered by driving a **real** Greenhouse board with a headless Chromium
(`scripts/_probe_greenhouse_form.py`). The adapter's selectors come from this
capture, not from guesswork, and the capture is committed so the tests run against
the same shape offline (`tests/fixtures/greenhouse_application_form.json`).

## What was inspected

| Board | URL | Result |
| --- | --- | --- |
| Natera | `job-boards.greenhouse.io/natera/jobs/6179619004` | Application form served — 51 fields |
| Revance | `job-boards.greenhouse.io/revance/jobs/8696634002` | **"Vercel Security Checkpoint"** — a bot-protection interstitial, no form |

## The form's real structure

Identity inputs, by `id`:

```
first_name  last_name  preferred_name  email  country  phone
```

File inputs: `resume`, `cover_letter`.

A repeatable **employment block**: `company-name-0`, `title-0`,
`start-date-month-0`, `start-date-year-0`, `end-date-month-0`, `end-date-year-0`,
`current-role-0_1` (checkbox). `Add another` appends index `-1`, `-2`, …

A repeatable **education block**: `school--0`, `degree--0`, `discipline--0`,
`start-month--0`, `start-year--0`, `end-month--0`, `end-year--0`.

Board-specific questions as `question_<numeric id>`, labelled in the DOM. The
captured board asked, all required:

- Are you 18 or older?
- Have you previously worked at Natera?
- Do you have any family members or relatives employed by Natera?
- Are you legally authorized to work in the United States?
- Do you now or will you in the future require sponsorship?
- Have you signed a non-compete with your current employer?
- Please provide your current permanent residence.

Submit control: accessible name **"Submit application"**, `type="submit"`.
No `name` attributes are set on any input; selection must be by `id`.

## The finding that shapes V1

**The form is CAPTCHA-protected** (three iframes plus a CAPTCHA element on the
captured page), and a second board sits behind a bot-protection interstitial.

Requirement 21 says a CAPTCHA means *stop safely and request human intervention*.
It does not say work around it. The adapter therefore detects both conditions and
stops, and `ExecutionService` records the application as `BLOCKED` with the reason
so the ledger says what happened.

This is the intended V1 behaviour, not an unfinished feature: the employer put a
control there deliberately, and defeating it is not this product's call to make.

## Second live finding: an interstitial can look like a form

A later live run against the same Revance posting served neither the form nor the
bot wall but a **cookie-consent overlay** — a page whose only input was the consent
widget (`ckyCCPAOptOut`). The adapter accepted it as the application form.

The consequence was worse than a wasted click. A consent page has **no required
fields**, so `validate` returned `ok=True`, `detect_stops` found no CAPTCHA, and the
service proceeded to prepare against a page that was not the application — and with
real submission enabled, to click a button on it.

`looks_like_application_form` now requires two independent signals before the adapter
will touch a page: a submit control, and *either* a page title naming it an
application *or* at least two known identity inputs. Anything else is a
`SafetyStop(MATERIAL_AMBIGUITY)` naming the title and the fields it did find.

The same live run also showed that the interstitial is **not stable** — the first
inspection found a bot wall, a later one found a consent overlay. Detection cannot
be a one-off check of a known wall; it has to be a confirmation that the page is what
it claims to be.

## Known limits, stated rather than hidden

- The employment and education blocks are **recognized and mapped but not filled**.
  The adapter reports them as `employment_history`, which is why they do not appear
  as unknown blockers. Populating them from the candidate profile is a natural next
  step and is deliberately out of V1 scope.
- Board-specific required questions are answered only from candidate-verified answer
  memory. Anything else stops the attempt with the question named, because guessing
  at "Have you signed a non-compete?" is exactly the failure requirement 21 exists
  to prevent.
- A field whose label maps to a question key by coincidence is a hazard: the
  employment block's date inputs are labelled "Start date month" / "Start date year",
  which classifies as the *"when can you start?"* question. Structural identity is
  therefore checked before label classification, or every application would be
  blocked by a question the form never asked.
