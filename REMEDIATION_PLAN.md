# ARTEMIS — Remediation Plan & Record

**Branch:** `feat/v1-production-readiness` · **Base:** `main` @ `101ce37` · **Date:** 2026-11-02

Phases A–F of the master prompt, executed in order. "Planned" items that were
found unnecessary on inspection are marked as such with evidence.

## Phase A — Baseline (done)

- Full-tree inspection: 62k LOC Python across 8 packages + FastAPI app; 84 test
  files; brand kit; ops docs; CI.
- Baseline suite (Windows, Python 3.13, real Node for the render harness):
  **3 failed / 1,463 passed** (see AUDIT_REPORT P1-3, P1-4 for the three).
- Ruff: clean. `uv lock --check`: clean. `validate.mjs`: clean.

## Phase B — Critical repairs (done)

1. **API authentication (P1-1).** New `sabra_api/auth.py` middleware. Behavior:
   - `SABRA_API_TOKEN` unset → unchanged local single-user behavior (bind
     stays 127.0.0.1 by default).
   - Set → every route except `/health`, `/favicon.svg`, `/brand/*` requires
     the token as `Authorization: Bearer`, or a one-time `?token=` GET that is
     exchanged for an HttpOnly `SameSite=strict` cookie and immediately
     redirected away; mutating routes are covered; comparison is constant-time.
   - 9 security regression tests (`tests/test_api_auth.py`).
2. **Time-bombed outcome tests (P1-3).** Root cause fixed properly — the tests,
   not the guard: occurred-at is now relative to the submission the test itself
   just made. The engine's "no outcome before submission" validation is intact
   and still covered by `test_outcome_errors_are_json_the_page_can_show`.
3. **Windows UTF-8 harness (P1-4).** `encoding="utf-8"` at both Node harness
   sites. This was masking ~80 page tests on Windows dev machines.

## Phase C — Product completion (verified, no changes needed)

Every V1 screen in the master prompt exists with contract tests (Home/Morning
Brief, Opportunities, Detail, Research, Career Plan, Applications/Studio,
Pipeline, Candidate, Searches/Settings, Ask Artemis). Browser-verified at 1280px
and 375px by Playwright suites. The acceptance list (no dead controls, no
fabricated stats, escaping, responsive, a11y basics) is enforced by
`test_artemis_acceptance`, `test_artemis_browser`, `test_p2b_pages`,
`test_p3_sources_page`, `test_html_safety`. **Planned UI rework: not needed.**

## Phase D — Reliability (done)

1. **Versioned migrations (P1-2).** `sabra_domain/migrations.py`:
   `schema_migrations` table, numbered ordered migrations (baseline = 1),
   per-migration transactions, fail-stop with `MigrationError`, name-mismatch
   protection against downgrades, idempotent re-runs.
2. **Backup before migration (P2-1/P2-2).** `sabra db migrate` snapshots the
   SQLite file (`VACUUM INTO`, open-verified) before applying anything.
   `sabra db backup` / `sabra db restore` give the operator the same verified
   path on demand. 10 tests in `tests/test_migrations.py`; CLI smoke-tested
   end-to-end (migrate → status → backup → restore).
3. **Background-job recovery, stalled-operation detection:** verified existing —
   run records with server-stopped reconciliation (`record_server_stopped`,
   `RunService.record_server_stopped`), fail-closed forced handoff reports,
   handoff timeout → BLOCKED. Covered by `test_recovery`,
   `test_p2a_runs`, `test_submission_boundary`. **No new infrastructure added**
   per the prompt's "prefer existing mechanisms".

## Phase E — Deployment preparation (done)

`DEPLOYMENT_PLAN.md`: recommended architecture (single VPS, Caddy → uvicorn →
SQLite file with litestream-style off-site backup, Playwright co-located with
the app for V1), env inventory, migration/cutover/rollback procedures, staging
smoke tests, cost estimate (~$10–15/mo). The hosted release blocker (auth) is
now implemented; the remaining external dependency is an actual host + DNS,
which only the owner can provision.

## Phase F — Independent verification (done)

- `ruff check packages apps tests` → **All checks passed!**
- `pytest tests/` → **1466 passed, 3 deselected** (deselected = `live` network
  tests, excluded by design), 0 failed, 1 third-party DeprecationWarning.
- `node --check scripts/validate.mjs` and `node scripts/validate.mjs` →
  `hallam layout ok`.
- CLI smoke: `sabra db migrate/status/backup/restore` on a scratch database.

## Out of scope / deferred (with reason)

- Email ingestion for outcome detection and autonomous model-training pipelines:
  explicitly deferred by the master prompt §8.
- React rebuild of `apps/web`: prohibited by non-negotiable rule 4.
- In-app rate limiting: deferred to reverse proxy (single-user deployment);
  token brute force is bounded by operator-chosen secret entropy + proxy logs.
- PostgreSQL cutover: schema is PG-ready by design (SQLAlchemy core), but no
  production need is demonstrated for V1; documented in DEPLOYMENT_PLAN.md.
