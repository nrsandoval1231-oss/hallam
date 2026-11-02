# ARTEMIS — Validation Report

**Branch:** `feat/v1-production-readiness` · **Validated commit:** see PR head
SHA in the pull request · **Date:** 2026-11-02

## Environment

- Windows 11 (America/Chicago), Python 3.13.9 (uv-managed), uv 0.13.4.
- Dependencies: `uv sync --all-extras --frozen` (lockfile unchanged).
- Node.js v24.21.0 (required by the page-render test harness; CI provides its own).
- Playwright Chromium preinstalled (used by `test_artemis_browser`,
  `test_local_browser_acceptance`).

## Results (final, after all changes)

| Check | Command | Result |
|-------|---------|--------|
| Lint | `uv run ruff check packages apps tests` | **All checks passed!** (0 errors) |
| Unit + integration + API + security + migration + browser suites | `uv run pytest tests/` | **1466 passed, 3 deselected, 0 failed** in 114s |
| Layout contract | `node scripts/validate.mjs` | `hallam layout ok` |
| Lockfile | `uv lock --check` (CI step) | clean (lockfile untouched) |
| CLI smoke | `sabra db migrate/status/backup/restore` on scratch DB | all four commands succeed; restore verified |

Deselected: 3 tests marked `live` (they reach third-party job boards; excluded
by default per `pyproject.toml` and run separately via `make test-live`).
No tests were skipped, deleted, or weakened to achieve these results; the two
modified tests (P1-3) still exercise the same engine validation.

## What each suite validates (evidence map)

- `test_api_auth.py` (new, 9 tests): token enforced on API + pages + mutations;
  public health/brand routes; HttpOnly SameSite cookie exchange; URL token never
  lingers; no-token mode unchanged.
- `test_migrations.py` (new, 10 tests): baseline stamping on fresh and legacy
  databases, idempotency, ordering, failure rollback, downgrade refusal,
  backup verification, restore round-trip, garbage-backup refusal.
- `test_m5_m7_approval_execution_ledger.py`, `test_submission_boundary.py`,
  `test_dialog_confirmations.py`: hash-bound approval, modified-package
  invalidation, closed-posting refusal, unknown-field blocking, CAPTCHA
  handoff, no duplicate submissions, ledger completeness.
- `test_artemis_browser.py`, `test_local_browser_acceptance.py`: real Chromium
  rendering of Home/Review at desktop + mobile widths; console-error free.
- `test_p2b_pages.py`, `test_p3_sources_page.py`, `test_p2a_html_safety.py`:
  screen contracts, escaping, no dead controls, verified/inferred separation.
- `test_recovery.py`, `test_posting_race.py`, `test_p2a_runs.py`: idempotent
  runs, interruption recovery, duplicate prevention.

## Acceptance-gate status (master prompt §14)

| Gate | Status |
|------|--------|
| P0/P1 resolved or blocked with evidence | **Resolved** (P1-1..P1-4, P2-1) |
| Core workflows pass synthetic end-to-end validation | **Yes** (1466 tests, incl. full pipeline) |
| Truth + approval safeguards intact | **Yes** (safeguard suites untouched and green) |
| UI desktop/tablet/mobile | **Yes** (Playwright at 1280/375 + markup contracts) |
| Tests + lint green | **Yes** (exact counts above) |
| Migrations + recovery verified | **Yes** (new suites + CLI smoke) |
| Hosted security implemented or flagged | **Implemented** (token auth); proxy rate limiting documented as external |
| Staging-ready | **Yes, modulo owner-provisioned host** (see DEPLOYMENT_PLAN) |
| Deployment docs executable | **Yes** |
| Independent review, no material defects | This audit found none open; owner review via PR |
| PR opened with exact results | See linked PR |

## Not validated here (external blockers)

- Live network tests (`make test-live`) — require third-party board availability.
- Actual hosted deployment — requires owner-authorized host, DNS, and secrets.
- Owner's private pilot database — untouched by policy; `sabra db migrate` will
  stamp its baseline when the owner chooses to run it (backup is automatic).
