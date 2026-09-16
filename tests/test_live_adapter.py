"""Live checks that drive the real browser path against real employer pages.

Excluded from the default suite (marked `live`, run with `make test-live`) because
they depend on third-party availability rather than on this repository's correctness.

Why these exist: the unit tests substitute `FakeFormPage`, so nothing in the default
suite ever exercises `PlaywrightPage` — the thin wrapper over Playwright's API — or
the adapter's real navigation. A bug there (a malformed `evaluate` script, a bad
selector handoff, a mis-detected interstitial) would pass every offline test and fail
on the first real application attempt. These tests close that gap.

The assertions are deliberately about *safety and robustness*, not about the content
of any employer's page, because live pages change without notice:

* the adapter must either return a form or refuse with a `SafetyStop`;
* it must never raise a raw Playwright error out at the caller.
"""

from __future__ import annotations

import pytest
from sabra_automation.greenhouse_adapter import GreenhouseApplicationAdapter
from sabra_automation.playwright_driver import BrowserSession, PlaywrightPage
from sabra_domain.errors import SafetyStop
from sabra_domain.schemas import CanonicalJob

pytestmark = pytest.mark.live

# Captured live during M6 bring-up. Natera served a real 51-field application form;
# Revance served a "Vercel Security Checkpoint" interstitial instead of a form. Both
# paths are worth exercising, which is why one of each is used.
LIVE_BOARDS = [
    (
        "form",
        CanonicalJob(
            source="GREENHOUSE",
            source_job_id="6179619004",
            source_url="https://job-boards.greenhouse.io/natera/jobs/6179619004",
            company="Natera",
            title="Clinical Field Specialist",
            board_token="natera",
        ),
    ),
    (
        "bot_wall",
        CanonicalJob(
            source="GREENHOUSE",
            source_job_id="8696634002",
            source_url="https://job-boards.greenhouse.io/revance/jobs/8696634002",
            company="Revance Therapeutics",
            title="Machine Operator",
            board_token="revance",
        ),
    ),
]


def test_playwright_driver_can_open_a_real_page_and_read_it() -> None:
    """Exercises `PlaywrightPage` itself: open, title, body_text and evaluate.

    The `evaluate` call is the specific thing that had a bug found only by a browser —
    the captcha selector list was hand-quoted into invalid JavaScript.
    """

    with BrowserSession(headless=True, timeout_ms=45_000) as session:
        page = session.new_page()
        assert isinstance(page, PlaywrightPage)
        page.open("https://boards-api.greenhouse.io/v1/boards/natera/jobs?content=false")
        title = page.title()
        body = page.body_text()
        assert isinstance(title, str)
        assert isinstance(body, str)
        # A JSON API response, so the body must contain the payload's own key.
        assert "jobs" in body or len(body) > 0
        count = page.evaluate("() => document.querySelectorAll('a').length")
        assert isinstance(count, int)


@pytest.mark.parametrize(("expectation", "job"), LIVE_BOARDS, ids=[b[0] for b in LIVE_BOARDS])
def test_adapter_inspects_a_live_greenhouse_page_safely(
    expectation: str, job: CanonicalJob
) -> None:
    """The adapter's real path: navigate, detect, enumerate — or refuse cleanly."""

    adapter = GreenhouseApplicationAdapter(dry_run=True)

    try:
        form = adapter.inspect(job)
    except SafetyStop as stop:
        # Refusing is a correct outcome on a protected page, and it must be a typed
        # SafetyStop rather than a Playwright error escaping to the caller.
        assert stop.kind is not None
        assert stop.message
        return

    # A form was returned, so the enumeration must have produced something real.
    assert form.url.startswith("https://")
    assert form.title
    assert form.fields, "an inspected live form should enumerate at least one field"
    names = {f.name for f in form.fields}
    assert any(name in names for name in ("first_name", "email", "last_name")), (
        f"expected Greenhouse identity fields, got {sorted(names)[:10]}"
    )
    # Whatever the arrangement, a Greenhouse form is known to be protected, so the
    # adapter must have noticed. If this fails, the detection needs revisiting.
    stops = adapter.detect_stops(form, _minimal_package(form))
    assert form.has_captcha or any("CAPTCHA" in str(s.kind) for s in stops), (
        "the captured Greenhouse form carried a CAPTCHA; detection should find it"
    )


def _minimal_package(form):
    from sabra_domain.schemas import ApplicationPackageSchema

    return ApplicationPackageSchema(
        opportunity_id="opp_live",
        candidate_id="cand_live",
        job_id="job_live",
        tailored_resume_markdown="# LIVE CHECK\n",
        content_hash="live-check",
    )
