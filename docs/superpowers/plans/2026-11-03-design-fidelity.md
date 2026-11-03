# ARTEMIS Design Fidelity Implementation Plan

**Goal:** Reproduce the supplied brand board and dashboard in the existing working application.
**Architecture:** Retain FastAPI HTML, vanilla JavaScript, SQLAlchemy and existing APIs. Refine the final shared visual layer and page modules rather than introduce a frontend framework.
**Spec:** User's ARTEMIS master directive; Reference B is `docs/brand/reference/approved-interface.jpg`. Reference A is supplied in conversation only.

## Audit and prioritized discrepancies

- P1: Opportunities is still an operator form with large summary boxes, four exposed filters and verbose rows; the board requires a quiet search, chips, compact company rows and save controls.
- P1: Hero photo differs from flattened source. Existing image is documented original generated artwork; no original independent photograph is supplied. Retain this closest available asset, correct crop/overlay and disclose the limitation.
- P2: Mobile hero/metrics force Georgia despite approved local Cormorant; mobile CTA is excessively rounded and header is oversized.
- P2: Dashboard is squeezed into the hero canvas; Reference B gives its three panels more horizontal room. Company, pay and confidence need distinct hierarchy.
- P2: Multiple legacy style layers compete; final reference rules use repeated literals rather than centralized surface/spacing tokens.
- P2: Dense UI has 7–9px labels and too-small isolated touch targets. Improve legibility without inflating the compact composition.
- P2: Activity uses absolute dates instead of relative timestamps. Failed home requests leave perpetual loading indicators.
- P2: No existing bookmark persistence exists. Add explicitly browser-local, candidate-scoped saves; preserve prepare/pass and all server ranking logic.

## Existing architecture and inventory

Runtime routes: Home, Opportunities, detail, Research, Career Plan, Applications, package studio, Pipeline, Candidate, Searches, Answers, Settings. All flow through `pages.page_shell`; detail/studio are `review_pages`, plan is `reference_plan`, home is `reference_home`. APIs consume EngineContext and active candidate/search; browser fetches real JSON. Authentication is single-operator token/cookie middleware. Do not alter submission, approval, truth or authentication semantics.

Fonts: licensed local Cormorant Garamond and Manrope variable TTF. Logos: original tapered open A and vector exports. Icons: licensed Feather SVG. Photo: optimized 2048px WebP plus PNG master. No component framework, bundler or TypeScript runtime. `apps/web` is a stub, not the application.

Checks: locked uv environment, ruff, full pytest including Playwright and Node DOM harnesses, syntax/layout validation; GitHub Actions mirrors them. No frontend npm build/typecheck is configured. Existing visual evidence is historical and will be replaced with a new dated QA receipt.

## Execution

- [x] Capture current screens using service-built synthetic fixtures and Chromium.
- [x] Refine shared tokens, shell, approved fonts, responsive navigation and quiet controls.
- [x] Correct hero, metrics, three-panel geometry, activity hierarchy and failure states.
- [x] Reconstruct Opportunities search/chips/rows; preserve filters, rank, prepare and pass; add scoped local saves across Home/list/detail.
- [x] Refine detail/studio/plan and remaining pages via shared components; preserve review evidence and disabled incomplete approvals.
- [x] Capture all requested viewports plus 430px; test real API journeys, keyboard, filters/saves, empty/error and responsive states.
- [x] Crop Reference B, generate aligned side-by-sides/overlays, inspect, correct and recapture.
- [x] Run complete regression/static/package checks and document limitations.
- [x] Commit and open [draft PR #18](https://github.com/nrsandoval1231-oss/hallam/pull/18). Nothing merged or deployed.

## Review focus

Unassessed confidence stays unassessed; unknown salary does not match numeric salary filters; long titles never hide controls; failed fetches exit loading; candidate saves never bleed across candidate IDs; incomplete packages remain unapprovable.

Proceed autonomously under the user's explicit execution directive; no design exploration or plan approval gate.

## Desktop-width correction after owner review

- [x] Reproduce the rejected width/margin mismatch and clarify the reported issue.
- [x] Remove collage staging; use one wide application canvas with neutral margins.
- [x] Correct desktop scale together and retain phone/tablet rules.
- [x] Add failing/passing geometry and breakpoint regressions.
- [x] Recapture all target sizes, inspect corrected desktop and regenerate comparisons with aspect-preserving measured crops.
- [x] Independent correction review: no Important/Critical finding.
- [x] Finish full regression: 1508 passed, 2 skipped, 3 live-deselected.
- [x] Push updated draft PR/preview with current screenshots and explicit exact-fidelity blockers.

## Latest single-screen Home target

The new owner-provided 1280×853 screenshot supersedes the collage for Home. Implementation includes matching cover/metric/panel/destination composition, reconstructed photo assets, compact header and time-aware greeting. Target measurements are in `docs/brand/reference/LATEST_HOME_TARGET.md`. All functional/layout tests pass; exact pixel/photographic sign-off remains blocked by original-asset and latest-raster availability. Do not claim exact matching or merge/deploy.
