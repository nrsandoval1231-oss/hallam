"""Build reviewable reference comparisons from the dated Chromium captures.

Run: uv run python scripts/compare_artemis.py
Reference B is a flattened composite. Crops exclude device chrome and unrelated
screens; workflow thumbnails have unknown original CSS viewports. Overlays are
qualitative registration aids, never a claim of a pixel-identical photograph.
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/brand/evidence/fidelity-2026-11'
REFERENCE = Image.open(ROOT / 'docs/brand/reference/approved-interface.jpg').convert('RGB')
RESAMPLE = Image.Resampling.LANCZOS


def comparison(name, source, implementation, source_label='REFERENCE B'):
    implementation = implementation.resize(source.size, RESAMPLE)
    canvas = Image.new('RGB', (source.width * 2, source.height + 30), '#E8E5DC')
    canvas.paste(source, (0, 30))
    canvas.paste(implementation, (source.width, 30))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 9), source_label, fill='#142D29')
    draw.text((source.width + 12, 9), 'IMPLEMENTATION — synthetic API fixture', fill='#142D29')
    canvas.save(EVIDENCE / f'{name}-comparison.png')
    if source_label == 'REFERENCE B':
        source.save(EVIDENCE / f'{name}-source-crop.png')
    Image.blend(source, implementation, .5).save(EVIDENCE / f'{name}-overlay.png')


def main():
    desktop = Image.open(EVIDENCE / 'after/home-1280x853-viewport.png').convert('RGB')
    comparison('home-hero', REFERENCE.crop((136, 0, 1144, 374)), desktop.crop((136, 0, 1144, 374)))
    comparison('home-workspace', REFERENCE.crop((11, 383, 1095, 598)), desktop.crop((11, 383, 1095, 598)))
    # Source mobile app content excludes the time/status bar and phone surround.
    mobile = REFERENCE.crop((1107, 408, 1266, 772)).resize((375, 858), RESAMPLE)
    actual_mobile = Image.open(EVIDENCE / 'after/home-375.png').convert('RGB').crop((0, 0, 375, 858))
    comparison('home-mobile', mobile, actual_mobile)
    for label, box in [('detail', (11, 613, 412, 845)), ('review', (423, 613, 758, 845)),
                       ('career-plan', (770, 613, 1094, 845))]:
        image = Image.open(EVIDENCE / f'after/{label}-1280.png').convert('RGB')
        # Remove green staging margins; compare thumbnail composition, not CSS pixel sizes.
        comparison(label, REFERENCE.crop(box), image.crop((136, 0, 1144, 585)))
    for width in (1280, 375):
        before = Image.open(EVIDENCE / f'before/home-{width}.png').convert('RGB')
        after = Image.open(EVIDENCE / f'after/home-{width}.png').convert('RGB')
        height = min(before.height, after.height, 900)
        comparison(f'before-after-home-{width}', before.crop((0, 0, width, height)), after.crop((0, 0, width, height)), source_label='BEFORE — baseline main branch')
    print('Comparisons and 50% overlays written to', EVIDENCE.relative_to(ROOT))


if __name__ == '__main__':
    main()
