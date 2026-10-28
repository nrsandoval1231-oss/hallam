# ARTEMIS production asset kit

This is the production asset set for the approved Quiet Strategist direction. The mark is an original architectural A: two tapered, separately cut legs with a compact crosspiece. The deliberate open apex gives the silhouette room at small sizes. `brand/logo/artemis-mark.svg` is the master; the matching favicon is `brand/logo/favicon.svg`.

## Logo files

- `brand/logo/artemis-mark.svg` — master evergreen mark on transparent canvas.
- `brand/logo/artemis-mark-light.svg` and `artemis-mark-dark.svg` — evergreen and porcelain mark variants.
- `brand/logo/artemis-mark-mono-dark.svg` and `artemis-mark-mono-light.svg` — single-color variants for dark and light backgrounds.
- `brand/logo/artemis-horizontal-light.svg` / `artemis-horizontal-dark.svg` — horizontal lockups.
- `brand/logo/artemis-stacked-light.svg` / `artemis-stacked-dark.svg` — centered stacked lockups.
- `brand/logo/app-icon.svg` / `app-icon-light.svg` — rounded app tile variants.
- `brand/logo/exports/` — PNG exports of the marks and lockups; includes transparent 16, 32 and 48 px favicon marks and a 512 px app icon.
- `brand/social/` — square and landscape social graphics in SVG and PNG, in light and dark palettes.

Use the standalone mark at 16 px and above. At 16 px, the crosspiece remains a distinct pixel-width stroke; keep the source geometry unchanged and render directly at the target size. Use the horizontal lockup where the available width is at least 250 px. Keep clear space around all marks equal to the mark's crosspiece height. Do not add effects, outlines, or gradients.

## Color and type

Core colors: Evergreen `#142D29`, Forest `#29483E`, Sage `#A6B8A7`, Stone `#E8E5DC`, Sand `#D6C6A8`, Porcelain `#F7F6F1`, and Graphite `#26312E`.

Display face: **Cormorant Garamond**, variable weight 300–700, supplied as `brand/fonts/CormorantGaramond-Variable.ttf`. Use for editorial display headlines, generally weight 500 or 600. UI and wordmark face: **Manrope**, variable weight 200–800, supplied as `brand/fonts/Manrope-Variable.ttf`. Use for controls, data, labels, and wordmark. Both fonts are under the SIL Open Font License; the complete license text is bundled beside each font as `OFL-CormorantGaramond.txt` and `OFL-Manrope.txt`.

All distributed SVG lockups and social artwork use Manrope glyph outlines, so they render consistently without a locally installed font. The SVG `<title>` labels remain available to assistive technology. `brand/scripts/build_assets.py` outlines Manrope text at its declared weight (400 regular or 600 semibold) and regenerates PNG exports. Run it with Python packages `fonttools`, `uharfbuzz`, and `cairosvg` installed.

```css
@font-face {
  font-family: "Cormorant Garamond";
  src: url("/brand/fonts/CormorantGaramond-Variable.ttf") format("truetype");
  font-style: normal;
  font-weight: 300 700;
  font-display: swap;
}

@font-face {
  font-family: "Manrope";
  src: url("/brand/fonts/Manrope-Variable.ttf") format("truetype");
  font-style: normal;
  font-weight: 200 800;
  font-display: swap;
}
```

## Provenance

Font binaries and license texts are from the Google Fonts repository: [Cormorant Garamond](https://github.com/google/fonts/tree/main/ofl/cormorantgaramond) and [Manrope](https://github.com/google/fonts/tree/main/ofl/manrope). The outlined lockup lettering uses the bundled Manrope variable font instantiated at weight 600, with HarfBuzz shaping to preserve glyph spacing and alignment. SVG assets use only original ARTEMIS geometry and the canonical brand palette. Social artwork is brand collateral and does not depict product activity or factual application data.
