#!/usr/bin/env python3
"""Outline ARTEMIS wordmarks with bundled Manrope and regenerate PNG exports.

Requires fonttools, uharfbuzz, and cairosvg. Run from any directory with:
  python brand/scripts/build_assets.py
"""
from pathlib import Path
import io
import xml.etree.ElementTree as ET
import cairosvg
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

ROOT = Path(__file__).resolve().parents[2]
FONT_PATH = ROOT / 'brand/fonts/Manrope-Variable.ttf'
def load_weight(weight):
    font = TTFont(FONT_PATH)
    if 'fvar' in font:
        axes = {axis.axisTag: axis.defaultValue for axis in font['fvar'].axes}
        axes['wght'] = weight
        font = instantiateVariableFont(font, axes, inplace=False)
    font.flavor = None
    stream = io.BytesIO()
    font.save(stream)
    face = hb.Face(stream.getvalue())
    hbfont = hb.Font(face)
    upm = font['head'].unitsPerEm
    hbfont.scale = (upm, upm)
    hb.ot_font_set_funcs(hbfont)
    return font, hbfont, upm, font.getGlyphSet()

FONTS = {weight: load_weight(weight) for weight in (400, 600)}
NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)

def local(tag):
    return tag.rsplit('}', 1)[-1]

def outline(text):
    if not text.text or not text.text.strip():
        return None
    family = text.get('font-family', '').split(',')[0].strip().strip('"\'')
    if family.lower() != 'manrope':
        return None
    value = text.text
    weight = int(float(text.get('font-weight', '400')))
    font, hbfont, upm, glyph_set = FONTS.get(weight, FONTS[400])
    size = float(text.get('font-size', '16').replace('px', ''))
    tracking = float(text.get('letter-spacing', '0').replace('px', ''))
    x = float(text.get('x', '0'))
    baseline = float(text.get('y', '0'))
    anchor = text.get('text-anchor', 'start')
    buf = hb.Buffer()
    buf.add_str(value)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf)
    infos, positions = buf.glyph_infos, buf.glyph_positions
    scale = size / upm
    total = sum(pos.x_advance for pos in positions) * scale + tracking * max(0, len(positions)-1)
    if anchor == 'middle': x -= total / 2
    elif anchor == 'end': x -= total
    group = ET.Element(f'{{{NS}}}g', {'fill': text.get('fill', '#142D29'), 'aria-label': value})
    cursor = x
    for idx, (info, pos) in enumerate(zip(infos, positions)):
        glyph_name = font.getGlyphName(info.codepoint)
        pen = SVGPathPen(glyph_set)
        transform = (scale, 0, 0, -scale,
                     cursor + pos.x_offset * scale,
                     baseline - pos.y_offset * scale)
        glyph_set[glyph_name].draw(TransformPen(pen, transform))
        d = pen.getCommands()
        if d:
            ET.SubElement(group, f'{{{NS}}}path', {'d': d})
        cursor += pos.x_advance * scale
        if idx < len(positions)-1:
            cursor += tracking
    return group

outlined = 0
svg_paths = sorted((ROOT / 'brand/logo').rglob('*.svg')) + sorted((ROOT / 'brand/social').rglob('*.svg'))
for svg_path in svg_paths:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    changed = False
    for parent in root.iter():
        for child in list(parent):
            if local(child.tag) == 'text':
                replacement = outline(child)
                if replacement is None:
                    raise RuntimeError(f'Unsupported SVG text: {svg_path}')
                parent.insert(list(parent).index(child), replacement)
                parent.remove(child)
                changed = True
                outlined += 1
    if changed:
        tree.write(svg_path, encoding='unicode', xml_declaration=False)
        with svg_path.open('a', encoding='utf-8') as f:
            f.write('\n')

exports = ROOT / 'brand/logo/exports'
social = ROOT / 'brand/social'
for svg_path in svg_paths:
    tree = ET.parse(svg_path)
    viewbox = tree.getroot().get('viewBox')
    _, _, width, height = map(float, viewbox.split())
    if svg_path.parent == social:
        png_path = svg_path.with_suffix('.png')
        out_w, out_h = int(width), int(height)
    elif svg_path.name == 'favicon.svg':
        png_path, out_w, out_h = exports / 'favicon-32.png', 32, 32
    elif svg_path.stem.startswith('artemis-horizontal-') or svg_path.stem.startswith('artemis-stacked-'):
        png_path, out_w, out_h = exports / f'{svg_path.stem}.png', int(width), int(height)
    elif svg_path.stem.startswith('app-icon'):
        png_path, out_w, out_h = exports / f'{svg_path.stem}-512.png', 512, 512
    else:
        png_path, out_w, out_h = exports / f'{svg_path.stem}.png', int(width), int(height)
    cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=out_w, output_height=out_h)

for mode in ('light', 'dark'):
    source = ROOT / f'brand/logo/artemis-mark-{mode}.svg'
    for size in (16, 32, 48):
        cairosvg.svg2png(url=str(source), write_to=str(exports / f'favicon-{size}-{mode}.png'),
                         output_width=size, output_height=size)
print(f'Outlined {outlined} text nodes using bundled Manrope weights 400 and 600; regenerated PNG exports from {len(svg_paths)} SVGs.')
