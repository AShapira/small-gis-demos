#!/usr/bin/env python3
"""Create reproducible public comparisons and assertions from saved raw responses."""
from functools import lru_cache
import gzip
import importlib.util
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT.parent / 'geotiff-compression'
RAW = ROOT / '.artifacts/rendering'
OUT = ROOT / 'evidence'
spec = importlib.util.spec_from_file_location('probe', STUDY / 'rasterbench/affine_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
fixtures = {f['name']: f for f in json.loads((STUDY / '.runs/affine-probe/evidence/fixtures.json').read_text())}


@lru_cache(maxsize=2)
def source(name):
    with Image.open(STUDY / '.runs/affine-probe/evidence/fixtures' / (name + '.tif')) as im:
        return np.array(im)


def rows(label):
    return [json.loads(line) for line in (RAW / (label + '.jsonl')).read_text().splitlines()]


def read_image(row):
    im = Image.open(RAW / row['image']).convert('RGBA')
    pad = row['pad']
    return im.crop((pad, pad, pad + 256, pad + 256)) if pad else im


def main():
    labels = ['stock-g0-unthrottled', 'patched-g0', 'patched-g1']
    data = {label: rows(label) for label in labels}
    edge = {}
    for route in ('wms', 'gwc'):
        selected = [r for r in data['patched-g1'] if r['route'] == route]
        assert len(selected) == 1180
        assert not any(r['missing_pixels'] for r in selected if r['valid_pixels'] == 65536)
        depths, counts = [], []
        for row in selected:
            if not row['missing_pixels']:
                continue
            fixture = fixtures[row['name']]
            box = probe.tile(*row['tile'])
            expected, valid = probe.expected(source(row['name']), fixture['gt'], box)
            measured, holes = probe.metrics(read_image(row), expected, valid)
            assert measured['missing_pixels'] == row['missing_pixels']
            counts.append(int(holes.sum()))
            y, x = np.where(holes)
            wx = box[0] + (x + .5) * (box[2] - box[0]) / 256
            wy = box[3] - (y + .5) * (box[3] - box[1]) / 256
            col, line = probe.pixel_coordinates(fixture['gt'], wx, wy)
            depths.extend(np.minimum.reduce([col, line, fixture['size'] - col, fixture['size'] - line]).tolist())
        edge[route] = {'affected_tiles': len(counts), 'missing_pixels': sum(counts),
                       'max_missing_per_tile': max(counts, default=0),
                       'max_depth_source_pixels': max(depths, default=0)}
        assert sum(counts) == 16 and max(counts) == 1
    (OUT / 'edge-loss.json').write_text(json.dumps(edge, indent=2) + '\n')

    checker = (24, 10021003, 6797199)
    triangles = (24, 10021196, 6798186)
    sharpness = []
    for route in ('wms', 'gwc'):
        row = next(r for r in data['patched-g1'] if r['name'] == 'rot30_base' and tuple(r['tile']) == checker and r['route'] == route)
        ref, valid = probe.expected(source('rot30_base'), fixtures['rot30_base']['gt'], probe.tile(*checker))
        image = read_image(row)
        gray = np.array(image.convert('L'))
        intermediate = int((valid & ~np.isin(gray, np.unique(ref[valid]))).sum())
        result, _ = probe.metrics(image, ref, valid)
        assert intermediate == 0 and result['missing_pixels'] == 0
        assert result['exact_fraction_nonblank'] > .9999
        sharpness.append({'route': route, 'tile': checker, 'intermediate_pixels': intermediate, **result})
    (OUT / 'sharpness.json').write_text(json.dumps(sharpness, indent=2) + '\n')

    font = ImageFont.load_default(size=17)
    canvas = Image.new('RGB', (1120, 650), '#e8edf1')
    draw = ImageDraw.Draw(canvas)
    columns = ['Independent nearest reference', 'Stock / gutter 0', 'Patched / gutter 0', 'Patched / gutter 1']
    for j, text in enumerate(columns):
        draw.text((12 + j * 278, 10), text, font=ImageFont.load_default(size=15), fill='#162536')
    samples = []
    for i, (tile, title) in enumerate([(triangles, 'Interior triangle request'), (checker, 'Sharp checkerboard request')]):
        y = 60 + i * 310
        draw.text((12, y - 25), title, font=font, fill='#162536')
        ref, _ = probe.expected(source('rot30_base'), fixtures['rot30_base']['gt'], probe.tile(*tile))
        canvas.paste(Image.fromarray(ref).convert('RGB'), (12, y))
        for j, label in enumerate(labels, 1):
            row = next(r for r in data[label] if r['name'] == 'rot30_base' and tuple(r['tile']) == tile and r['route'] == 'wms')
            canvas.paste(read_image(row).convert('RGB'), (12 + j * 278, y))
            samples.append({'label': label, **row})
    canvas.save(OUT / 'comparison.png')
    (OUT / 'sample-requests.json').write_text(json.dumps(samples, indent=2) + '\n')
    for label in labels:
        for suffix in ('', '-errors'):
            path = RAW / (label + suffix + '.jsonl')
            if path.exists():
                (OUT / (label + suffix + '.jsonl.gz')).write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    shutil.copyfile(RAW / 'regression-result.json', OUT / 'rendering-regression.json')
    print('PASS: full-matrix interior coverage, edge depth, sharpness and saved visual comparison')


if __name__ == '__main__':
    main()
