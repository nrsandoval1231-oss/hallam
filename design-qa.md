# ARTEMIS visual QA — latest owner-supplied Home target

final result: passed

**Owner-approved rendered design; validation required before merge.** The owner approved the current preview (“Looks amazing!”) and explicitly requested validation and merge. This acceptance covers the implemented composition and disclosed reconstructed imagery. The original photo/source raster remain unavailable; no exact pixel/photograph equivalence is claimed. Deployment is not authorized.

## Current source of truth

The owner's latest **single-screen 1280×853 Home screenshot** supersedes the earlier composite for Home. [Target measurements](docs/brand/reference/LATEST_HOME_TARGET.md) document that screenshot. `docs/brand/reference/approved-interface.jpg` is the older composite, not the latest source. Its comparisons are retained only in `docs/brand/evidence/fidelity-2026-11/historical-reference-b/` and do not certify the new Home.

Current Chromium captures: `docs/brand/evidence/fidelity-2026-11/after/`, deviceScaleFactor 1. Exact viewport files include `WIDTHxHEIGHT-viewport`; full-page files include width only. Synthetic candidate/application fixtures consume the real FastAPI HTML/API and local assets; they are not production data or copied mockup metrics.

## Correction history

1. The owner rejected desktop sizing and clarified “The page is too narrow / margins too wide.” The previous sizing acceptance was withdrawn. Removed the 78.75vw green collage staging, aligned the header/hero/metrics/workspace, and widened the application to the viewport with an 1800px maximum.
2. The owner then supplied a new full-screen target. Rebuilt Home around that exact composition instead of continuing the older collage interpretation: compact header with notification bell, tracked greeting, sunset hero, photograph continuing behind translucent metrics, three warm briefing panels and four photographic destination tiles.
3. Matched 1280×853 landmarks: header y0/h50, hero y50/h305, metrics y355/h71, panels y446/h262, destinations y725/h120. The new regression initially failed because tile content produced128.6px height. Reduced vertical padding, rerendered, and passed the120px target without clipping.
4. Refined the headline tracking, wordmark/navigation spacing, lining numerals and panel content offsets. Desktop scale responds to both width and height so destination tiles remain visible at1280×800,1440×900 and1920×1080. Mobile/tablet remain separate layouts.
5. A fresh reviewer inspected the new wrapper, destination navigation, CSS cascade and rendered1280×853 screenshot. No Important/Critical correctness, overlap or responsive finding was reported. Exact source-image matching was not assessed by that reviewer.

## Required surfaces

- **Typography:** local Cormorant Garamond headlines, section titles and latest-reference navigation treatment; Manrope data/body/greeting. Wordmark spacing, headline wrapping, tracked uppercase greeting and lining numerals refined against the conversation image. Actual name and browser time determine the greeting.
- **Layout:** full-width warm application canvas; metrics sit within the photographic cover; three columns use approximately 431/438/349px tracks; four 120px destination tiles sit below. A landmark regression verifies measured source positions at1280×853. Responsive tests verify wide aligned bounds and removal of the old1500px jump.
- **Colors/surfaces:** approved evergreen/sage/stone/sand/graphite tokens, warm ivory paper, translucent metric surfaces, fine borders, warm restrained depth. No permanent left application sidebar or green presentation rails.
- **Assets:** `artemis-home-architecture.webp` and four `artemis-destination-*.webp` files are generated reconstructions guided by the supplied screenshot. Their combined optimized payload is approximately350KB. They are not the original photograph or original tile assets. Open-A SVG and licensed Feather icons retained; added standard layers icon.
- **Content:** real API counts, employers, compensation, confidence, activity and preferences remain authoritative. No127/7/3/2 metrics, percentage growth, sample employer logos or fabricated analysis were inserted. The fixture has fewer rows and unassessed matches. The existing summary is read aloud; no video or two-minute duration is invented.

## Evidence and normalization

- Latest preview: `after/home-1280x853-viewport.png`.
- Required desktop/tablet/phone captures:1920×1080,1440×900,1280×800,768×1024,390×844,375×812. Additional1280×853,820×900 and430×932 captures.
- `desktop-layout-bounds.json` records actual browser geometry at1280×853,1280×800,1440×900 and1920×1080.
- `desktop-width-fix-comparison.png`: previous rejected narrow1440px render alongside the current render, same viewport and synthetic fixture type.
- `before-after-home-1280-comparison.png` and `before-after-home-375-comparison.png`: original repository baseline alongside current implementation.
- Adjacent overlays are50% blends of those baseline/current images, not overlays of the new source.
- `scripts/compare_artemis.py` preserves aspect ratios and pads differing heights. `--reference /path/to/latest-home.png` generates a true latest-source comparison/overlay if that actual file becomes available. No generated image is passed off as the original source.

The newest source cannot be placed in a local side-by-side or pixel overlay because its file is not exposed to this workspace. The conversation screenshot and actual rendered image were visually inspected, and source landmark measurements were tested. This limitation remains documented; the owner subsequently approved the rendered implementation and authorized merging after validation.

## Functional, accessibility and performance checks

Navigation and destination links are functional. New tests cover all four destination routes, desktop bounds, source landmarks, standard-height fit, continuous resizing, filters, unknown pay/confidence, recorded remote status, scoped local saves, blocked storage, long content, empty/error states, skip link, keyboard menu and reduced motion. Existing authentication, truth, hash-bound approval and application safeguards remain unchanged.

Local WOFF2 fonts total263,388 bytes; original TTFs/licenses remain. No new runtime dependency. Source/wheel build and local asset loading are checked. No employer requests, external applications, merge or deployment occur during visual QA.

Full regression/static/package receipts are recorded below when the final runs finish. Prior intermediary full runs:1500 passed, then1504 passed after desktop-width regressions, then1505 passed after latest-target/destination regression. These are historical receipts, not substituted for the final run.

## Documented limits accepted for this merge

- Standalone original hero/tile assets are unavailable; reconstruction changes photographic geometry, lighting and foliage.
- Latest source raster is conversation-only; an honest source-versus-current pixel comparison cannot be generated from the file system.
- Example-content differences are intentional and required to preserve actual user data; no private user production pilot is claimed.

Implementation is in [PR #18](https://github.com/nrsandoval1231-oss/hallam/pull/18). The owner authorized merging after fresh validation; deployment remains separate.

## Final verification receipts

- `uv run pytest tests/`: **1508 passed, 2 skipped, 3 live-deselected**, 184.64 seconds, no failures.
- Final responsive screenshots: **10 passed**.
- Final fidelity/shared-chrome tests after height-aware scaling: **24 passed**; all four destination routes and standard desktop height-fit cases pass.
- `uv run ruff check packages apps tests scripts/compare_artemis.py`, `uv lock --check`, `node --check scripts/validate.mjs`, `node scripts/validate.mjs`, `uv build`, and `git diff --check`: passed.
- Current rendered source landmarks and all requested screenshot sizes were inspected. The 1280×853 landmark test verifies exact target positions within four pixels. Current `desktop-layout-bounds.json` records measured geometry.
- Passing functional/layout gates do not establish pixel identity; the owner has accepted the disclosed asset/source limits above.

## Owner acceptance and merge authorization

The owner approved the current preview and requested “validate and merge.” The previous draft-only restriction is superseded by that explicit authorization. Reconstructed imagery, source-overlay limits, browser-local saves and unexercised live-provider/private-data pilot remain documented. Merge is authorized after fresh full-suite/static/package checks, CI and final code review; deployment and employer submissions are not authorized.

## Fresh pre-merge verification

Owner-authorized validation: `uv run pytest tests/` — **1508 passed, 2 skipped, 3 live-deselected**, 188.91 seconds, no failures. Frozen sync/lock check, Ruff, Node validation, source/wheel build and diff checks passed. Fresh independent review found no remaining Critical/Important/Minor issue; a suspected regex issue was withdrawn after checking actual evaluated JavaScript codepoints. Runtime source is unchanged from reviewed `c1847b4`; this acceptance update changes documentation only. PR #18 is ready for merge once checks on its final head complete.
