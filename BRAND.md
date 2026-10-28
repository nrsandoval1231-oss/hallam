# VELORA Brand System — Cloudglass Architectural

This is the canonical brand reference for **VELORA**, the interface of the Sabra job
engine. It supersedes any earlier branding notes. The engine, CLI, environment
variables, and internal module names remain `sabra` — VELORA is the product identity.

- **Product name:** VELORA
- **Positioning:** Your intelligent career operating system.
- **Primary headline:** A clearer path forward.
- **Brand statement:** Find the right opportunities. Present your best self. Move with confidence.
- **Attributes:** intelligent, effortless, refined, calm, confident, precise, forward-looking.

Design principle: **maximum intelligence, minimum visual complexity.** Sophistication
through restraint.

## Color

Frozen palette. Use color sparingly.

| Token | Hex | Purpose |
| --- | --- | --- |
| Cloud Ivory | `#F8F5F2` | Primary background |
| Frosted Mist | `#E5EAEB` | Ambient surfaces |
| Silver | `#C6CED1` | Borders, secondary surfaces |
| Sky Glass | `#A7C3D5` | Signature brand accent |
| Slate Glass | `#98A6AB` | Muted interface elements |
| Storm Blue | `#718C9A` | Strong accent, secondary controls |
| Deep Slate Ink | `#263A48` | Primary text |

Derived interaction colors (implemented as CSS custom properties in
`apps/api/sabra_api/pages.py` under SHELL_CSS): a deep storm ink for primary buttons
and hover states, and softened tints of Sky Glass for selection and fill states.

Never: dark navy backgrounds, purple, neon gradients, saturated blues, gold/copper,
heavy color-coding, generic SaaS gradient aesthetics.

## Typography

- **Display:** Sora — headlines, the wordmark, metric numerals.
- **Interface:** Inter — body copy, controls, navigation.

Loaded from Google Fonts in the shared page shell. Eyebrow labels use Inter in
small caps with wide letter spacing (`YOUR CAREER, IN FOCUS`).

## Logo — interlocking glass V

Two elegant translucent ribbons converging into a V: forward movement, opportunity,
intelligent guidance. The geometry is frozen — do not substitute a generic V, arrow,
or stock icon.

Master artwork is vector (`brand/logo/*.svg`). PNG exports are rendered from the
vectors at 16, 24, 32, 64, and 256 px.

| Asset | File(s) |
| --- | --- |
| Primary horizontal logo | `brand/logo/velora-horizontal.svg` (+ 256 px PNG) |
| Vertical arrangement | `brand/logo/velora-vertical.svg` |
| Icon-only mark | `brand/logo/velora-mark.svg` (+ 32/64/256 px PNGs) |
| Wordmark only | `brand/logo/velora-wordmark.svg` |
| Monochrome mark | `brand/logo/velora-mark-mono.svg` (+ PNG) |
| Dark-background mark | `brand/logo/velora-mark-dark-bg.svg` (+ PNG) |
| App icon | `brand/logo/velora-app-icon.svg` + `velora-app-icon-256.png` |
| Favicon | `brand/logo/favicon.svg`, `favicon-16/24/32.png` (served at `/favicon.svg`) |
| Social sharing card | `brand/social/velora-social-card.svg` |

Small sizes (16–32 px) use the mark alone; ribbons are drawn with enough contrast
and stroke weight to stay legible. Clear space: at least the height of the mark's
inner gap on all sides. Do not recolor the ribbons outside the palette, add effects
(glows, bevels, drop shadows baked into the art), or place the light version on busy
imagery — backgrounds are always the procedural Cloudglass field.

## Glass material — Cloudglass Architectural

The application is composed of elegant, floating glass boards on a softly
illuminated, entirely procedural background. Implemented as utility classes in
SHELL_CSS:

- **Glass Primary** (`.glass`): frosted translucent surface, subtle backdrop blur,
  thin light border, soft architectural shadow.
- **Glass Secondary** (`.glass-soft`): more transparent, lower elevation, reduced
  emphasis.
- **Glass Elevated** (`.glass-elevated`): increased depth and a subtle directional
  illumination; reserved for the most important content (e.g. Featured Opportunity).
- **Glass Interactive** (`.glass-int`): restrained hover elevation, smooth
  transitions, visible keyboard focus ring.

Avoid excessive blur, glowing outlines, thick borders, and permanently animated
backgrounds. Use (nearly) opaque content layers wherever readability requires it.

### Background

Procedural only: soft atmospheric gradients and muted blue/ivory color fields with
gentle ambient illumination (CSS radial/linear gradients in SHELL_CSS). Absolutely
no photography, landscapes, mountains, cloud photos, cityscapes, people, buildings,
heavy illustration, or decorative elements competing with content. The background
supports the interface; it never dominates it.

## Layout

Minimal horizontal top navigation — no permanent sidebar.

- Left: VELORA logo
- Center: Home, Opportunities, Applications, Research, Career Plan
- Right: Ask Velora (search/AI entry), notifications, profile (account menu holds
  Profile, Pipeline, Searches, Answers, Settings)
- Mobile: the center nav collapses into a menu; single-column content, comfortable
  touch targets, no horizontal overflow.

The Home dashboard hierarchy is frozen: eyebrow + `A clearer path forward.` + brand
statement; four metric boards (Matches, Applications, Interviews, Offers) with real
data and honest empty states; a glass-elevated **Featured Opportunity** as the focal
point; then Recent opportunities, Your progress, and Upcoming items. No extra
dashboard sections without demonstrated need.

## Motion

Subtle board hover elevation, smooth page transitions, restrained focus effects,
lightweight loading states, clean modal transitions, clear selection states. No
excessive animation, floating particles, pulsating borders, dramatic parallax, or
constant background motion. Honor `prefers-reduced-motion`.

## Accessibility

- Text is Deep Slate Ink on light glass; interactive elements meet contrast guidance
  against their surface.
- All controls keyboard-reachable with a visible focus state.
- Reduced-motion preference disables non-essential transitions.
- Semantic landmarks: header nav, main content, labelled sections.

## Engineering mapping

| Concept | Where it lives |
| --- | --- |
| Design tokens (color, type, radius, shadow, motion) | CSS custom properties in `SHELL_CSS`, `apps/api/sabra_api/pages.py` |
| Glass material classes | same file (`.glass`, `.glass-soft`, `.glass-elevated`, `.glass-int`) |
| Page shell / navigation / Ask Velora | `page_shell()` and `CHROME_JS` in the same module |
| Logo & favicon serving | `/favicon.svg` route in `apps/api/sabra_api/main.py` |
| Brand assets | `brand/logo/`, `brand/social/` |
