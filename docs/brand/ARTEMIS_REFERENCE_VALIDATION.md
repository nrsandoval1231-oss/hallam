# ARTEMIS selected-reference release validation

Date: 2026-10-28. Baseline: `4a9944e` (merged production-experience PR #15).

The owner selected `docs/brand/reference/approved-interface.jpg` as the active interface target. This release translates that reference into the existing FastAPI application. The Sabra engine packages, database identifiers, application routes and approval/submission logic are unchanged.

## Result and visual evidence

[design-qa.md](../../design-qa.md) records **final result: passed**. The root coordinator opened the source and rendered implementation together in normalized full-view and focused comparisons. Reviewable captures and comparison images are in [evidence/reference-fidelity](evidence/reference-fidelity/).

The implementation now follows the reference's compact ivory masthead, open two-stroke A, architectural hero, four-count strip, three briefing panels, mobile stacked rows, tabbed opportunity detail, four-step Application Studio and Career Plan subtabs. Original generated scenery replaces the flattened reference photograph; real candidate records replace illustrative companies, counts and activity. Studio displays the actual full document instead of an invented customization teaser. These differences are explicit in the QA report; pixel-identical photographic content is not claimed.

The original generated hero is retained as `brand/images/artemis-architecture.png` and served through its approximately 196 KB WebP derivative. Local MIT Feather icons and licensed fonts avoid remote asset requests. All sixteen logo/social SVGs use the open mark, including translated stacked geometry; twenty-two PNG exports were verified, and asset regeneration twice produced identical hashes.

## Actual-data contracts

- **Opportunities analyzed:** assessed opportunities in the active search.
- **High-confidence matches:** active shortlisted roles whose stored engine confidence is at least 0.75; this is an assessment estimate, not an outcome probability.
- **Applications ready:** the existing count of complete, truth-clean candidate packages awaiting approval.
- **Meaningful updates:** recorded employer responses not yet marked as seen, clarified in the metric's tooltip.
- Numbered brief actions and top opportunities come from actual candidate state and shortlist records. Recent activity uses candidate-owned append-only events, with distinct owner-decline, employer-rejection, draft-preparation and execution-start labels.
- Read-aloud is available only when the browser exposes speech synthesis. No video or invented duration is shown. Search controls, account utilities and Ask remain available across responsive layouts.

## Final verification

`uv run pytest tests/ -ra`: **1446 passed, 2 skipped, 3 deselected** in 109.19 seconds.

The platform skips are:

- `tests/test_p2a_final_fixes.py::test_the_windows_calls_declare_their_signatures` — Windows API signatures; this runner is Linux.
- `tests/test_p2a_review_fixes.py::test_the_runner_stops_before_running_anything_when_the_env_script_is_missing` — PowerShell is not installed.

The three deselected tests are explicitly marked live and were not run. One third-party Starlette/anyio deprecation warning remains.

Passed: `uv run ruff check packages apps tests`, `uv lock --check`, `node --check scripts/validate.mjs`, `node scripts/validate.mjs`, `git diff --check`, and `uv build --wheel`. An extracted non-editable wheel imported the reference modules and served the bundled hero, icon license and fonts.

Actual-API Chromium acceptance passed at 1280, 1440, 820 and 375 pixels. It checks font loading, no horizontal page overflow, navigation, Profile/Settings/Ask access, Career Plan and opportunity tabs, all numbered Studio steps, visible unresolved review content, disabled incomplete approval, local assets and console errors. Every request is fulfilled through the real local FastAPI API over isolated service-built synthetic records. No employer request or external submission occurs.

HTML-safety tests continue injecting malicious document text, brief labels/details, opportunity content, activity and run warnings. The formatter escapes before adding limited emphasis/headings/lists. Regression cases cover payload-aware event labels and repeated-render keyboard navigation.

## Independent review and remaining boundaries

Astra high independently approved the stabilized diff against `4a9944e`, conditional on final regression and CI: **no unresolved material findings**. Twenty-five focused data/decision/HTML/API-browser tests passed independently, along with navigation/overflow checks at 375, 760, 761, 820, 960 and 1280 pixels. Review findings about event semantics, tablet navigation, accumulated keyboard listeners and stacked exports were repaired and rechecked.

Four Luna low builders handled separated Home, API evidence, decision pages and asset work. The architect integrated the shell/Career Plan and verification; the root coordinator generated the hero and performed normalized visual QA. The direct role harness was used. The previously attempted Codex CLI remains unavailable because authentication cannot refresh; Orloom and the OpenAI Agents SDK are unavailable. No result from those unavailable runtimes is claimed.

The local preview runs on 127.0.0.1:8787 using a dedicated fresh `/workspace/artemis-preview` database, explicit dry-run mode and no configured model. It shows honest setup/empty states. The root coordinator independently opened this running server in Chromium at 1280×853 and 375×812, confirming HTTP 200, the hero heading, loaded fonts, no horizontal overflow and no JavaScript errors. No private résumé, new real candidate pilot or external job application was created. Historical private pilot evidence remains separate. Deployment and PostgreSQL runtime verification remain outside this UI release.

Candidate truth, sensitive-answer verification, exact content-hash approval, CAPTCHA human handoff, dry-run defaults and employer confirmation safeguards remain authoritative.
