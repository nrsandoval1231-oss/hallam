"""Create honest baseline/current comparisons and optional latest-source overlay.

The latest source image is attached in the conversation, not a local file.
Supply --reference only when that actual source raster becomes available.
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/brand/evidence/fidelity-2026-11'
RESAMPLE = Image.Resampling.LANCZOS


def comparison(name, source, implementation, source_label):
    """Align widths, preserve proportions and pad remaining height with ivory."""
    implementation = implementation.resize(
        (source.width, round(implementation.height * source.width / implementation.width)),
        RESAMPLE,
    )
    height = max(source.height, implementation.height)
    left = Image.new('RGB', (source.width, height), '#F7F6F1')
    right = left.copy()
    left.paste(source, (0, 0))
    right.paste(implementation, (0, 0))
    canvas = Image.new('RGB', (source.width * 2, height + 30), '#E8E5DC')
    canvas.paste(left, (0, 30))
    canvas.paste(right, (source.width, 30))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 9), source_label, fill='#142D29')
    draw.text((source.width + 12, 9), 'CURRENT APP — synthetic API fixture', fill='#142D29')
    canvas.save(EVIDENCE / f'{name}-comparison.png')
    Image.blend(left, right, .5).save(EVIDENCE / f'{name}-overlay.png')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path)
    args = parser.parse_args()
    for width in (1280, 375):
        before = Image.open(EVIDENCE / f'before/home-{width}.png').convert('RGB')
        after = Image.open(EVIDENCE / f'after/home-{width}.png').convert('RGB')
        height = min(before.height, after.height, 900)
        comparison(f'before-after-home-{width}', before.crop((0, 0, width, height)),
                   after.crop((0, 0, width, height)), 'BASELINE — original main branch')
    comparison('desktop-width-fix', Image.open(EVIDENCE / 'desktop-width-before-1440.png').convert('RGB'),
               Image.open(EVIDENCE / 'after/home-1440x900-viewport.png').convert('RGB'),
               'PREVIOUS — rejected narrow desktop layout')
    if args.reference:
        comparison('latest-reference', Image.open(args.reference).convert('RGB'),
                   Image.open(EVIDENCE / 'after/home-1280x853-viewport.png').convert('RGB'),
                   'OWNER-SUPPLIED LATEST REFERENCE')
    else:
        print('Latest source is conversation-only; no latest-source pixel overlay claimed.')
    print('Baseline/current comparisons written to', EVIDENCE.relative_to(ROOT))


if __name__ == '__main__':
    main()
