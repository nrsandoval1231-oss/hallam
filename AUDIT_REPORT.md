# ARTEMIS — V1 Engineering Audit Report

**Date:** 2026-11-02 · **Base:** `main` @ `101ce37` · **Auditor:** autonomous engineering pass (this branch)

## Scope

Full repository audit per the master engineering prompt: engine stages, UI, data layer,
security, autonomy, CI, deployment surfaces, tests, docs, scaffolding. Evidence: code
inspection plus the complete local test suite on Windows (Python 3.13, uv-locked deps,
real Node.js for the page-render harness, Playwright Chromium).

## Verdict

The repository is substantially healthier than its age suggests: the core engine
(discover → normalize → filter → score → research → shortlist → prepare → review →
approve → execute → track) is implemented with its safeguards intact (hash-bound
approvals, CAPTCHA handoff, dry-run default, truth levels, append-only ledger), and
1,463 of 1,466 default tests passed at baseline. The material defects found were
concentrated in **test-suite time bombs**, **missing hosted-deployment security
(auth)**, and **missing database lifecycle tooling (versioned migrations, backup,
restore)** — all repaired on this branch.

## Findings

### P0 — none open

No data-loss, truth-boundary, or submission-safety defect was found. Safeguard
regression tests (`test_m5_m7_approval_execution_ledger`, `test_submission_boundary`,
`test_p2c_truth_gate`, `test_answer_guards`) all pass.

### P1 (fixed on this branch)

| # | Finding | Evidence | Fix |
|---|---------|----------|-----|
| P1-1 | `SABRA_API_TOKEN` existed in Settings but was **never enforced** — the API and all pages (candidate PII, resume upload, application execution) were reachable by anyone who could reach the port. This is the release blocker for any hosted deployment. | `settings.py:68` had no references anywhere else in `packages/`, `apps/`, `tests/` | `sabra_api/auth.py` + `tests/test_api_auth.py` (9 security regression tests) |
| P1-2 | No versioned database migrations. `Database.create_all()` builds current-model schema but can never change an existing database; the pilot database's future evolution was unversioned and unrecoverable. | `packages/domain/sabra_domain/db.py`, empty `migrations/` | `sabra_domain/migrations.py` + `sabra db status/migrate/backup/restore` + `tests/test_migrations.py` (10 tests) |
| P1-3 | Outcome-recording tests were **time bombs**: hardcoded `occurred_at` of 2026-10-01/02 eventually predates the fresh `submission_time`, so both tests started failing after 2026-10-03 with a legitimate engine validation error. Baseline: 2 failures on 2026-11-02. | `tests/test_p2b_pipeline.py:311,365` | Tests now use `datetime.now(UTC)`; the engine guard ("outcome before submission") is unchanged |
| P1-4 | Test harness decoded Node's UTF-8 stdout with the Windows locale codepage, corrupting every non-ASCII assertion (e.g. `résumé` → `rÃ©sumÃ©`). Baseline: 1 failure; latent for all page tests on Windows. | `tests/test_p2a_html_safety.py`, `tests/test_html_safety.py` `subprocess.run(..., text=True)` with no `encoding` | `encoding="utf-8"` added at both sites |

### P2 (fixed or documented)

| # | Finding | Disposition |
|---|---------|-------------|
| P2-1 | `sabra db restore` held the database file (WAL) open in its own process while trying to overwrite it — restore could never succeed. | Fixed: context closed before restore; locked-file error now names the culprit process. Verified by CLI smoke test. |
| P2-2 | No backup/restore path for the pilot database operator. | `sabra db backup` (verified `VACUUM INTO` snapshot) and `sabra db restore`; see `RECOVERY_RUNBOOK.md`. |
| P2-3 | Deployment documentation absent (no architecture, env inventory, cutover/rollback). | `DEPLOYMENT_PLAN.md` added. |

### P3 (accepted, documented)

- **Rate limiting / brute-force throttling**: not implemented in-app. For a
  single-user deployment, terminate TLS and enforce rate limits at the reverse
  proxy (Caddy/nginx) — see DEPLOYMENT_PLAN.md. In-app token throttling is a
  deferred improvement.
- **`apps/web` scaffold** (Vite/React skeleton): kept per the non-negotiable
  rules — the FastAPI server-rendered UI is the product; the scaffold is inert
  (no build step in CI, `.gitkeep`-only `src`).
- **`workers/{applications,discovery,research}`** empty dirs: reserved layout
  for the future Playwright worker split; kept.
- **`scripts/_probe_*.py`, `_capture_fixtures.py`**: one-off live-network probes
  used to capture test fixtures; they are evidence of how fixtures were produced.
  Kept, documented as operator tooling.
- **Starlette TestClient DeprecationWarning** (anyio alias): third-party noise,
  one warning, no action.

### Verified working (evidence: passing suites)

- Discovery providers (Greenhouse, Lever, Ashby, direct-careers) with timeouts,
  bounded retries, dedupe, source health; dead boards refused, one failure does
  not abort a run (`test_m2_discovery`, `test_p3_discovery`, `test_p3_providers`).
- Intelligence: hard filters, compensation, geography, confidence-vs-fit
  separation, ranking (`test_m3_intelligence`, `test_artemis_intelligence`).
- Documents: truth gate, tailoring bounded to verified facts, PDF generation,
  unresolved-question handling (`test_m4_documents`, `test_p2c_generate`,
  `test_p2c_truth_gate`, `test_pdf_*`).
- Approval/execution/ledger: hash-bound batch approval, refresh-invalidates,
  posting-closed refusal, unknown-field blocking, CAPTCHA handoff, forced-report
  fail-closed (`test_m5_m7_*`, `test_submission_boundary`, `test_dialog_confirmations`).
- UI acceptance: every screen's contract tested at markup level + real-browser
  Playwright tests at two viewports (`test_artemis_browser`,
  `test_local_browser_acceptance`, `test_p2b_pages`, `test_p3_sources_page`).
- SSRF: probe URL parsing never fetches a pasted URL; only the three ATS API
  hosts are contacted, fixed templates, no redirects (`sabra_providers/probe.py`,
  `test_p3_sources`, `test_operator_controls`).

## Non-findings (checked, healthy)

- Secrets: no credentials, résumés, or databases in Git (`.gitignore` covers
  `var/`, `config/candidates/`; `config/search_profile.example.yaml` is a template).
- HTML safety: escaping verified against an EVIL-payload suite; no inline event
  handlers; no `javascript:` URLs (`test_html_safety`, `test_p2a_html_safety`).
- Idempotency: duplicate applications/opportunities prevented
  (`test_posting_race`, `test_recovery`, `test_p2a_runs`).
- CI validates the real PR head: `uv lock --check`, ruff, full pytest,
  Playwright install, `validate.mjs` (layout contract).
