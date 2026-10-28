# ARTEMIS selected-reference design QA

final result: passed

## Visual truth and rendered evidence

- Source: `docs/brand/reference/approved-interface.jpg`, the owner's latest attached 1280×853 reference. This supersedes the earlier non-photographic execution.
- Implementation: real FastAPI HTML/API, service-built synthetic candidate and application evidence, local licensed fonts, Chromium at device scale factor 1. Desktop 1280×853; mobile 375×812 with full-page capture. Additional automated coverage includes 1440 px and 820 px.
- Rendered captures: `docs/brand/evidence/reference-fidelity/home-1280.png`, `home-375.png`, `review-1280.png`, `review-375.png`, `detail-1280.png`, and `career-plan-1280.png`.
- Full/focused comparison evidence: `docs/brand/evidence/reference-fidelity/home-desktop-comparison.png`, `home-mobile-comparison.png`, and `workflow-comparison.png`. The source and implementation are together in each comparison image and were opened and inspected by the root coordinator.

## Normalization and state

The desktop hero compares the same 1008×374 content crop at x=136 in the 1280 px canvas. The source's device bezel and status bar are presentation artwork, not application UI. Its phone content crop (1107,408)–(1266,772), 159×364 pixels, is normalized to 375×858 for the mobile comparison. The full rendered mobile page continues below that crop. Approximate source crop boundaries limit subpixel precision.

The lower source views are collage thumbnails with unknown underlying CSS viewports. `workflow-comparison.png` compares their composition after downsampling rendered content to the corresponding thumbnail widths; it does not claim pixel-identical typography at an unknown source viewport. Application content and state differ intentionally: real synthetic evidence has one incomplete package and no assessed matches, rather than the mockup's example employers, ready applications and invented counts.

## Comparison history and repairs

1. First desktop/mobile browser pass found P2 headline weight, CTA-arrow contrast, panel vertical placement, full-bleed mobile image, boxed metrics and excess pre-image controls. Repaired the display weight, white arrow, panel spacing, inset image, unboxed rows and mobile presentation. Desktop metrics now begin around y=302 versus source y=300; the panels begin around y=382 versus source y=383.
2. Studio comparison found P1 excessive header/readiness height and dark button-like local navigation. Replaced it with a compact title/readiness summary, four numbered steps and five quiet section rows. Complete unresolved-question details and the hash-bound disabled approval remain available in Review. Post-fix captures show the repaired header and section list.
3. Normalized phone comparison exposed a P2 scale mismatch missed in the initial full-board view. Increased the masthead to 75 px, open A to 46 px, wordmark to 16 px, headline to 44 px, metric rows to 94 px, icons to 34 px, counts to 26 px and labels to 14 px. Post-fix image begins around y=251 versus source y=260, and the brief CTA around y=772 versus source y=780. These small residual offsets are within the flattened phone crop's normalization uncertainty.
4. Independent review found a hidden tablet navigation interval, incorrect event labels and a stale stacked-logo export. These were repaired with tablet browser, payload-aware activity and asset checks before release verification.

## Required fidelity surfaces

- **Typography:** compact desktop editorial hierarchy; stronger Cormorant display treatment, legible Manrope controls, Georgia mobile hero matching the source's broad serif proportions. Local font loading verified. Stable two-line hero wrapping and explicit responsive mobile optical scale were inspected.
- **Spacing/layout:** centered 1008 px ivory canvas, evergreen surround, 49 px desktop masthead, compact hero and four-card strip, three aligned briefing panels, two-column opportunity/plan composition and local studio section navigation. Mobile image/metrics/CTA composition inspected after the scale repair. No permanent application sidebar.
- **Colors/tokens:** canonical porcelain, evergreen, forest and muted sage; quiet borders and restrained depth. Dark CTA has a white arrow and readable foreground. Keyboard focus remains available.
- **Imagery/assets:** original generated architectural hero with the same house/tree/mountain subject and palette, optimized WebP delivery; original open two-stroke A consistently propagated to exports. Licensed Feather icons replace decorative approximations. The original photograph in the flattened source was not available as an independent clean asset, so scenery details are intentionally original rather than represented as pixel-identical.
- **Copy/content:** greeting, counts, opportunity facts, confidence, readiness and recent activity come from actual data. No sample 127/7/3/2 counts, growth percentage, fictional employer response or market insight was copied into production. “Meaningful updates” explicitly describes recorded unread employer responses. A functional read-aloud control replaces the mockup's unimplemented video and invented two-minute duration.

## Interaction and safety checks

Browser acceptance covers navigation, mobile menu, Ask Artemis, Career Plan tabs, opportunity tabs, numbered studio steps and section navigation, loading/empty presentation, local assets/fonts, horizontal overflow and JavaScript errors. Incomplete approval stays disabled; full truth levels, unresolved questions, warnings and the package hash remain reachable. Actual-API browser evidence makes no employer requests or external submissions. Full automated results and independent review are recorded in the release validation receipt.

## Findings and remaining polish

No actionable P0/P1/P2 visual finding remains in the compared states. P3: the flattened source has soft rasterized lettering, subtly textured material and photographic details that cannot be reproduced as identical browser pixels. The implementation uses sharp live text and original imagery. The studio shows the real full document by default instead of the mockup's abbreviated customization teaser; this preserves useful review behavior and does not invent customization claims.

## Implementation checklist

- [x] Compare the selected source and rendered implementation together.
- [x] Repair major desktop, mobile and studio differences and inspect post-fix captures.
- [x] Preserve real data, readable controls and approval safeguards.
- [x] Keep source, captures and comparison evidence reviewable in the repository.
- [ ] Merge only after final-commit regression/static/CI gates and independent review pass.
