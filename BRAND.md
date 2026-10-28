# ARTEMIS — Canonical Brand and Product Identity

**Status: LOCKED.** ARTEMIS is the sole customer-facing product identity. Do not rename, rebrand, or reintroduce any legacy brand without an explicit, substantive business/legal reason and owner approval.

## Positioning
**ARTEMIS** — The intelligent career hunter. A selective, calm, deeply capable personal career strategist.
- Primary message: **Find what others miss. Pursue what matters.**
- Hero: **Your next move is taking shape.**
- Supporting: **A more intentional career.**
- Voice: confident, insightful, calm, selective, specific, never theatrical or generic AI hype.

## Aesthetic
**Quiet Strategist / architectural editorial minimalism.** The owner’s latest selected interface reference, [approved-interface.jpg](docs/brand/reference/approved-interface.jpg), is the active visual source. It supersedes the earlier non-photographic production interpretation. MARQO (https://www.behance.net/gallery/234103127/MARQO) is an inspiration for restraint, not a source of designs to copy. All logos, UI components and assets must be original.

- No left navigation rail; minimal horizontal navigation.
- Spacious ivory workspaces with judicious evergreen panels; clear hierarchy, quiet shadows and precise alignment.
- Typography-led editorial hero and strong functional data interfaces.
- No gratuitous futuristic effects, generic goddess imagery, stock bow-and-arrow logos, fake intelligence, or overloaded widgets.
- The owner explicitly approved the architectural landscape hero in the selected interface reference. Production uses the original generated `brand/images/artemis-architecture.png`, served as an optimized WebP; it is atmospheric artwork, never evidence of product activity. No stock image or sample company logo is copied from the reference.

## Color tokens
| Token | Hex | Purpose |
| --- | --- | --- |
| Evergreen | `#142D29` | Primary brand surfaces and text on light |
| Forest | `#29483E` | Main action / interactive emphasis |
| Sage | `#A6B8A7` | Soft secondary accent |
| Stone | `#E8E5DC` | Warm secondary surface |
| Sand | `#D6C6A8` | Limited brand warmth |
| Porcelain | `#F7F6F1` | Main workspace background |
| Graphite | `#26312E` | Primary body typography |

Use semantic aliases as necessary. All foreground/background pairings must meet accessibility contrast; do not use low-contrast sage text for essential information.

## Logo
Sharp minimal **open architectural A** made from two elegant tapered strokes and deliberate negative space, with no crossbar, matching the selected interface reference. Uppercase **A R T E M I S** wordmark with restrained tracking. The refined production master is `brand/logo/artemis-mark.svg`, validated at 16 px. No literal mythology symbols. Horizontal and stacked lockups, mono/reversed marks, favicon, app icons and vector/PNG social artwork are supplied in `brand/`; see [the asset kit](docs/brand/ARTEMIS_ASSET_KIT.md).

## Typography
Cormorant Garamond is the editorial display serif; Manrope is the readable sans for application data, controls, inputs, lists and supporting labels. Small uppercase eyebrows use increased tracking. Both variable fonts are bundled locally with their SIL Open Font Licenses in `brand/fonts/`; no remote font request is required.

## Product Experience
The signature Home experience is **Morning Brief**: a concise explanation of what Artemis found, what matters and the **one recommended next action**. Never invent activity or precision. Surfaces:
1. **Home / Morning Brief:** current pursuits, opportunity counts, outstanding approvals, upcoming action.
2. **Opportunities:** clean curated ranked opportunities, fit assessment, pay, employer, location, provenance.
3. **Research:** verified employer facts separated from inference.
4. **Career Plan:** career objectives, constraints and progression.
5. **Applications:** truthful tailored package, unresolved questions, approval and honest status history.
6. **Ask Artemis:** answers backed by actual engine state; do not pretend to be open-ended AI when not implemented.

**Discover → Understand → Prepare → Pursue** is the conceptual journey. The application approval boundary is *never* sacrificed for visual continuity.

## Engineering boundaries
- Keep the internal `sabra` packages, commands, event log, database identifiers and migrations intact unless there is a functional necessity.
- Preserve candidate truth verification, sensitive answer verification, hash-bound human application approval, CAPTCHA stop-and-handoff, dry-run defaults, and existing routes.
- API/UI implementation: `apps/api/sabra_api/pages.py`, `main.py`, `review_pages.py`. Use actual engine outputs, never mock production counts.
- Existing branded DOM identifiers may remain temporarily for backward compatibility; they are not customer-facing names. New IDs should use brand-neutral names.
- Historic descriptions of previous names may remain only in changelog/history, not as active design requirements. Legacy assets should be retired from runtime usage.
- Treat the original October 28 reference in `docs/brand/ARTEMIS_APPROVED_DIRECTION.md` as design provenance, with **this file authoritative** for current work.
- Verify complete test suite, rendered desktop/mobile pages, and brand references before merging.

## Brand governance
ARTEMIS is not a temporary aesthetic experiment. Refine visual execution without restarting naming. If an alternative is suggested, first test whether it represents a genuine business/legal/usability issue. Trademark/domain clearance is outstanding; no claim of exclusivity is made.
