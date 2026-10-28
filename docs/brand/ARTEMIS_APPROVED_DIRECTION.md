# ARTEMIS — Approved Brand Direction (2026-10-28)

> STATUS: Owner-approved visual direction, now implemented as the canonical customer-facing identity. `BRAND.md` is authoritative for the current brand and product experience; this document records the approved design provenance. Preserve existing Sabra engine behavior.

## Identity
- **Name:** ARTEMIS (uppercase wordmark).
- **Positioning:** Intelligent career hunter / private career strategist; selective autonomous career intelligence.
- **Brand voice:** Quietly confident, precise, editorial, calm, purposeful; never generic recruiting-tech or gaudy sci-fi.
- **Signature lines:** “Find what others miss. Pursue what matters.” / “Your next move is taking shape.” / “A more intentional career.”
- **Creative reference:** MARQO Behance project, https://www.behance.net/gallery/234103127/MARQO — inspiration for restrained architectural graphic design, typography, materials and pacing. Develop **original** Artemis artwork rather than duplicating proprietary design assets.

## Current interface fidelity direction

The owner subsequently selected [approved-interface.jpg](reference/approved-interface.jpg) as the exact interface target. Its compact ivory masthead, open two-stroke A, photographic architectural hero, four-count strip, three briefing panels and mobile stacked metric rows supersede the earlier production interpretation. The original generated hero is `brand/images/artemis-architecture.png`; its WebP derivative serves the app. Example names, counts, percentages and sample employer logos in the source remain illustrative and are replaced by actual engine evidence or honest empty states.

## Approved visual direction
Architectural/editorial minimalism. Warm ivory and stone surfaces, deep evergreen anchors, generous whitespace, crisp asymmetric grids, refined serif display headlines, letterspaced uppercase secondary text, immaculate alignment, quiet shadows. The selected latest concept board is the visual source of truth for the direction; image generated in the October 28, 2026 ARTEMIS brand conversation. **Artwork is a concept mockup, not production-ready UI or factual live data.**

### Colors from latest concept board (provisional until sampled/finalized)
- Evergreen: `#142D29` — deep anchor, major CTAs and branded panels.
- Sage: `#A6B8A7` — subtle supporting accent.
- Stone: `#E8E5DC` — warm architectural surface.
- Sand: `#D6C6A8` — human warmth in brand collateral, not the default CTA.
- Graphite: `#26312E` — text and structure.
- Porcelain: `#F7F6F1` — primary app background.

Use restraint and accessible contrast. Concept art may differ slightly from token values; validate exact colors against final approved source before build.

## Logo — approved architectural A direction
- Distinctive, thin, architectural **A** mark formed by two elegant sharply cut strokes with an intentional open/negative-space relationship. Avoid stock arrows, literal bows or goddess illustrations.
- Uppercase `A R T E M I S` wordmark with generous tracking, highly refined typography.
- The original vector master is `brand/logo/artemis-mark.svg`; the favicon and application header use the same A artwork. The refined master, horizontal and stacked lockups, monochrome/reversed exports, app icons and tested small-size marks are now supplied; see `ARTEMIS_ASSET_KIT.md`.
- Initial logo explorations included Ascendant, Orbit, Meridian; **the latest minimalist architectural A board is the preferred direction**. Exact vector not frozen.

## Typography
- Elegant editorial serif / sophisticated high-contrast display treatment for hero headlines.
- Clean modern sans serif for dense functional UI, forms and labels (Inter is acceptable).
- Wide-tracked uppercase navigation eyebrow labels. Brand wordmark uses bespoke-refined letter-spacing.
- Production typography is locally bundled Cormorant Garamond and Manrope, with verified SIL Open Font Licenses. Earlier type suggestions are historical.

## Product UI / flows
- Desktop and mobile concept layouts, with **top horizontal navigation**, no permanent left sidebar.
- Primary pages: Home, Opportunities, Research, Career Plan, Applications; an accessible Ask Artemis.
- **Home signature = Morning Brief**: “Your next move is taking shape.” Prioritize real next actions and concise narrative of newly found opportunities, prepared applications, and companies worth watching.
- Opportunity browsing: ranked and curated results with transparent match rationale, compensation, location, source and next action.
- Opportunity detail: explicit separation of verified facts, scoring/inference, fit gaps, and actionable next steps.
- Application review: truthful résumé and cover letter, verified answers, unresolved questions, explicit **human approval** prior to external submission.
- Mobile Morning Brief is a first-class experience, not shrunk desktop UI.
- Four-stage loop: Discover → Understand → Prepare → Pursue.
- Quiet motion only; no dashboards stuffed with charts, glowing effects, heavy glass, or fake activity.
- Architecture and content must honor Sabra engine truth boundaries, source confidence, hash-bound approvals, CAPTCHA stop-and-handoff, and dry-run defaults.
- Mockup company names, postings, applicant names, percentages, and activity are **illustrative, not live user data**.

## Implementation boundary / migration
- The shared application shell and product surfaces use ARTEMIS. Internal `sabra` source modules and database identifiers remain stable for compatibility.
- Legacy VELORA artwork is retired from the tracked brand kit. The ARTEMIS vector mark and favicon are active; the complete production asset kit is documented in `ARTEMIS_ASSET_KIT.md`. Desktop/mobile Chromium smoke acceptance is recorded in `ARTEMIS_PR14_VALIDATION.md`.
- Repository implementation does not establish merge or production deployment status.
