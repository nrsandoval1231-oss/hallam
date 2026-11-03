# ARTEMIS reference implementation QA — 2026-11-03

final result: passed

This is a pass for the implemented compositions and interactions with the asset limitations below explicitly retained. It is not a claim of pixel-identical reproduction of an unavailable original photograph or a completed real-user pilot.

## Visual truth, normalization and evidence

Reference B: `docs/brand/reference/approved-interface.jpg` (1280×853). Reference A: user-supplied brand board in the conversation; no separate file is present for automated cropping. Branding follows A, application layout follows B. Implementation captures are in `docs/brand/evidence/fidelity-2026-11/after/`; baseline and first-pass captures are in `before/` and `pass-1/`.

Chromium, deviceScaleFactor 1, real FastAPI HTML/API and local fonts, synthetic candidate/application fixtures. Required exact viewport captures: 1920×1080, 1440×900, 1280×800, 768×1024, 390×844, 375×812. Additional 1280×853, 820×900 and 430×932 captures. `*-viewport.png` dimensions equal CSS viewport dimensions. `NAME-WIDTH.png` images capture the full page and may exceed viewport height.

Comparison inputs place both source and implementation in the same image and were opened and inspected:

- `home-hero-comparison.png`: source and actual crop (136,0)–(1144,374), each 1008×374.
- `home-workspace-comparison.png`: source and actual crop (11,383)–(1095,598), each 1084×215. The source's device overlay obscures the workspace at the right; the comparison excludes that region.
- `home-mobile-comparison.png`: source application crop (1107,408)–(1266,772), 159×364, normalized to 375×858, next to the actual 375×858 page crop. No phone bezel, status bar or notch is recreated as application UI. Approximate source crop boundaries and softened raster type limit precision.
- `detail-comparison.png`, `review-comparison.png`, `career-plan-comparison.png`: each source workflow thumbnail alongside a normalized 1008×585 implementation content crop. Original CSS viewports are unknown, so these establish composition/evidence reachability, not exact glyph sizing at an invented viewport.
- `before-after-home-1280-comparison.png`, `before-after-home-375-comparison.png`: baseline and final rendered view. Their left label explicitly identifies baseline, not Reference B.
- Adjacent `*-overlay.png` files blend registered source/actual crops at 50%; `*-source-crop.png` files isolate the reference regions. `scripts/compare_artemis.py` reproduces them.

## Findings, corrections and final comparison

1. **P1 shortlist composition:** baseline exposed large summary cards and multiple operator inputs. Rebuilt it with quiet heading, live search, filter chips, expandable advanced filters, ranked company/role/pay rows and saves. Existing prepare/pass controls and evidence remain reachable. Verified desktop/mobile screenshots and positive/negative search/filter tests.
2. **P2 desktop panels:** first pass placed panels about 13px too low and used overly narrow workspace proportions. Reduced metric/panel spacing and expanded the desktop workspace relative to the hero canvas. Registered final workspace comparison shows aligned panel starts and an editorial three-column structure; content density differs because the fixture has fewer records.
3. **P2 mobile typography and rhythm:** baseline used Georgia. First pass restored Cormorant but its narrower proportions made headline/image/CTA placements too high. Increased the Cormorant headline to 50px at phone sizes, restored wordmark tracking, used 94px metric rows and a 66px CTA. Final normalized phone comparison shows two-line wrapping, inset photograph, stacked metrics and bottom CTA aligned with the source composition. Cormorant's glyph shapes remain faithful to the approved pairing rather than substituting Georgia to imitate a flattened image.
4. **P2 missing shortlist identity styling:** first-pass initials lost their shared Home styles on the list. Added reusable evergreen company tiles and removed an orphan rank separator for unranked roles. Final list captures show the repair.
5. **P2 tablet metric collision:** rendered 768px screenshot exposed an icon track narrower than its container. Widened the tablet icon track; recaptured and inspected final tablet view. No overlap or horizontal overflow remains in the tested widths.
6. **P2 failure-state continuity:** failed Home fetches previously left loading lists indefinitely. Added explicit unavailable messages and stable metric geometry. The failure browser test verifies all four metrics and absence of loading content.
7. **Functional regressions during reconstruction:** retained the existing overview link; kept dynamic save icons as fixed CSS asset URLs to preserve HTML safety contracts; used scoped DOM queries compatible with offline harnesses; updated the existing industry-filter browser journey to open More filters while retaining all matching assertions. Focused and complete regression receipts below.

No further actionable P0/P1/P2 layout or interaction issue was found in the compared states. The unavailable original photo and differing data states are material, disclosed limits rather than claims of exact matching.

## Required fidelity surfaces

- **Typography:** bundled Cormorant Garamond headlines, Manrope interface/data; consistent local WOFF2 loads verified in a live Uvicorn/Chromium visit. Original TTFs retained. No external font request. Hero wraps in two lines at tested sizes; long role names wrap without hiding saves or actions. Tiny source raster text is treated as visual hierarchy, not copied as illegible screenshot text.
- **Spacing/layout:** minimal horizontal masthead; narrow photographic hero canvas and wider three-column desktop workspace; quiet compact metric strip; two-column detail/plan; studio section navigation and real document preview. Tablet uses two workspace columns with activity below. Phone uses its own image/stacked metrics/CTA layout. No left application sidebar.
- **Colors:** approved #142D29, #A6B8A7, #E8E5DC, #D6C6A8, #26312E; warm #F7F6F1/#FBFAF6 surfaces, hairline rules and restrained depth. Evergreen CTAs, readable focus outlines and subdued active navigation.
- **Images/assets:** original architectural WebP retained and recropped. It is an existing generated substitute, not the exact source photograph. Original open architectural A remains SVG. Icons use the existing licensed Feather set, with its bookmark path added under the same license. No sample employer logos or reference employer names were added to live data.
- **Copy/content:** actual engine counts, compensation, confidence, recommendations and preferences remain authoritative. The fixture has zero assessed matches and one incomplete application; the reference has populated sample metrics. No fabricated market activity, percentage uplift, confidence, document customization or video duration is inserted. Summary is supported speech synthesis, not an invented video.

## Functional, accessibility and performance verification

- Browser/API journey coverage: navigation, mobile/account menus, Home/Opportunities/detail/studio/Applications/Pipeline/Research/Career Plan, local fonts/assets, detail and plan tabs, studio steps, unresolved answers and disabled approval, no horizontal overflow and no JS errors.
- New regressions: text/chip/salary/industry filtering, unknown pay/confidence, browser-local save persistence across routes/reloads, candidate isolation, blocked storage, long mobile titles, Home API failure, keyboard skip/menu and reduced motion.
- Separate real HTTP visit: Uvicorn on port 8787 with isolated empty data, Chromium 390×844, fonts loaded as exactly two WOFF2 assets, no JavaScript errors and no overflow. Screenshot `empty-live-390.png`.
- No employer requests or submissions in visual QA. Existing auth, approval, candidate-truth and API/adapter regression tests remain in the suite.
- WOFF2 assets total 263,388 bytes; architectural image is the existing ~192KB WebP. Stable geometry and reduced-motion rules retained. No new runtime JavaScript framework or package dependency.
- Full-suite and static/package receipts are added below after completion. There is no configured TypeScript typecheck or npm production build for this Python HTML runtime.

## Remaining discrepancies and limits

- Exact source photograph unavailable: geometry, tree shapes, crop details and lighting remain different. Replace the one original WebP if the independent original becomes available; no search or API logic needs rebuilding.
- Reference A has no separately accessible file for automated overlays; brand palette/fonts/mark were checked against the conversation image and repository assets.
- Workflow-thumbnail viewport uncertainty; actual full document/evidence content is preserved instead of copying the reference's abbreviated text or inventing key-customization claims.
- Saves persist only in this browser, scoped to candidate. No cross-device synchronization is claimed.
- Third-party live-provider checks and private user data were not exercised. Synthetic evidence is clearly identified.

## Acceptance checklist

- [x] Audit repository and preserve backend/approval/auth boundaries.
- [x] Correct global shell, Home, shortlist and shared secondary surfaces.
- [x] Inspect aligned source/actual images; correct and recapture desktop/mobile/tablet findings.
- [x] Capture all requested viewports, plus 430px.
- [x] Verify actual browser interactions, keyboard, error/empty states and font delivery.
- [x] Produce baseline/final, reference comparisons, overlays and documented asset limits.
- [x] Record final complete regression and packaging receipts.
- [ ] Open feature-branch PR.
- [ ] Owner review; merge/deploy only with explicit approval.

## Final verification receipts

- `uv run pytest tests/`: **1500 passed, 2 skipped, 3 live-deselected**, 153.75 seconds, no failures. The sole warning is the dependency's Starlette/AnyIO deprecated alias.
- Final responsive-capture `tests/test_artemis_acceptance.py tests/test_artemis_fidelity.py` run before the additional remote regressions: **14 passed**, including all required screenshot sizes and new interaction regressions.
- `uv run ruff check packages apps tests scripts/compare_artemis.py`: clean.
- `uv lock --check`: clean; no runtime dependency changes.
- `node --check scripts/validate.mjs` and `node scripts/validate.mjs`: passed.
- `uv build`: source distribution and wheel built successfully. Wheel inspection confirms new view modules, WOFF2 assets and bookmark SVG are included.
- `git diff --check`: clean.
- Chromium HTTP visit: isolated running Uvicorn, 390×844, exactly two WOFF2 font requests, no JS errors or overflow.

## Independent review

A fresh reviewer inspected the working tree against `44e4841`, without session history. One Important finding: Remote used location text while the domain stores work arrangement separately. Corrected `/api/opportunities` to expose recorded `remote_status`; the chip now prioritizes explicit REMOTE/ONSITE and uses text fallback only for missing/UNKNOWN status. Added regressions for a remote city role, onsite “Remote Office” and unknown city, plus a real API assertion of ONSITE. The reviewer verified the correction and reported no remaining finding. Dynamic HTML escaping, save initialization and safeguard preservation were reviewed. The new focused browser/API/HTML-safety run passed 20 tests.

Release run after independent-review correction: `uv run pytest tests/` — **1500 passed, 2 skipped, 3 live-deselected**, no failures. Skips concern Windows API signatures and unavailable PowerShell on this Linux worker.
