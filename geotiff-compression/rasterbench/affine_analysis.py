"""Offline evidence analysis: no GeoServer requests or configuration changes."""
from collections import defaultdict
import json
from pathlib import Path

import numpy as np
from PIL import Image

from affine_probe import expected, pixel_coordinates, source_array, tile, verify, write_json


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def analyze(root):
    manifest = {i['name']: i for i in verify(root)}
    result = {}
    labels = ['baseline', 'baseline-bilinear', 'gutter128-full', 'gutter256-full',
              'expanded128-full', 'gutter129-full', 'transparent128-full', 'patched2/full',
              'patched3/full', 'patched4/full', 'patched5/full', 'patched5/gutter1-full', 'mosaic-full']
    for label in labels:
        data = rows(root / (label + '.jsonl'))
        if not data:
            continue
        grouped = {}
        for route in sorted({r['route'] for r in data}):
            subset = [r for r in data if r['route'] == route]
            errors = [r for r in rows(root / (label + '-errors.jsonl')) if r['route'] == route]
            if label == 'baseline-bilinear':
                errors = [r for r in rows(root / 'baseline-errors.jsonl')
                          if r['route'] == route and r['interpolation'] == 'bilinear']
            else:
                errors = [r for r in errors if r['interpolation'] == 'nearest neighbor']
            interior = [r for r in subset if r['valid_pixels'] == 256 * 256]
            holes_depths = []
            for r in subset:
                if not r['missing_pixels']:
                    continue
                item = manifest[r['name'].removeprefix('mosaic_')]
                folder = root / Path(label).parent
                image = np.array(Image.open(folder / r['image']).convert('RGBA'))
                pad = r.get('pad', 0)
                if pad:
                    image = image[pad:-pad, pad:-pad]
                box = tile(*r['tile'])
                _, valid = expected(source_array(item['path']), item['gt'], box)
                holes = valid & ((image[:, :, 3] == 0) | (image[:, :, :3] >= 254).all(2)
                                 | (image[:, :, :3] <= 1).all(2))
                yy, xx = np.where(holes)
                cx = box[0] + (xx + .5) * (box[2] - box[0]) / 256
                cy = box[3] - (yy + .5) * (box[3] - box[1]) / 256
                ci, ri = pixel_coordinates(item['gt'], cx, cy)
                depth = np.minimum.reduce([ci, ri, item['size'] - ci, item['size'] - ri])
                holes_depths.extend(depth.tolist())
            grouped[route] = {'images': len(subset), 'errors': len(errors),
                'valid_pixels': sum(r['valid_pixels'] for r in subset),
                'missing_pixels': sum(r['missing_pixels'] for r in subset),
                'interior_images': len(interior), 'interior_missing': sum(r['missing_pixels'] for r in interior),
                'max_missing_one_tile': max(r['missing_pixels'] for r in subset),
                'max_hole_depth_source_pixels': max(holes_depths, default=0),
                'holes_beyond_one_source_pixel': sum(d > 1 for d in holes_depths)}
        result[label] = grouped
    write_json(root / 'analysis/matrix.json', result)

    performance = []
    for path in sorted(root.rglob('performance-*-http.jsonl')):
        data = rows(path)
        groups = defaultdict(list)
        for row in data:
            groups[(row['profile'], row['concurrency'])].append(row)
        for (profile, concurrency), values in sorted(groups.items()):
            latencies = [q['seconds'] * 1000 for r in values for q in r['raw'] if 'error' not in q]
            performance.append({'label': values[0]['label'], 'profile': profile, 'concurrency': concurrency,
                'repetitions': len(values), 'requests': sum(r['requests'] for r in values),
                'errors': sum(r['errors'] for r in values), 'median_ms': float(np.median(latencies)),
                'p95_ms': float(np.percentile(latencies, 95)),
                'throughput': sum(r['requests'] for r in values) / sum(r['elapsed'] for r in values),
                'cache_results': {k: sum(r['cache_results'][k] for r in values) for k in ('HIT', 'MISS')}})
    write_json(root / 'analysis/performance.json', performance)

    # A single request with sharp checkerboard edges, and the largest baseline gap.
    candidates = [r for r in rows(root / 'pilot.jsonl') if r['name'] == 'rot30_base' and r['route'] == 'wms']
    minimal = max(candidates, key=lambda r: r['missing_pixels'])
    checkerboards = []
    for row in candidates:
        item = manifest[row['name']]
        ref, mask = expected(source_array(item['path']), item['gt'], tile(*row['tile']))
        if mask.all() and set(np.unique(ref)) == {48, 208}:
            checkerboards.append(row)
    chosen = max(checkerboards, key=lambda r: r['missing_pixels'])
    identity = (chosen['name'], chosen['tile'], 'wms')
    item = manifest[chosen['name']]
    reference, valid = expected(source_array(item['path']), item['gt'], tile(*chosen['tile']))
    images = [('Inverse-affine reference', reference)]
    quality = []
    for label, title in [('pilot', 'Stock nearest'), ('pilot-bilinear', 'Bilinear'),
                         ('expanded_128', 'Nearest + gutter 128'), ('gutter129-full', 'Nearest + gutter 129'),
                         ('patched5/gutter1-full', 'Fix + gutter 1')]:
        matching = [r for r in rows(root / (label + '.jsonl'))
                    if (r['name'], r['tile'], r['route']) == identity]
        if not matching:
            continue
        r = matching[0]
        a = np.array(Image.open(root / Path(label).parent / r['image']).convert('L'))
        pad = r.get('pad', 0)
        if pad:
            a = a[pad:-pad, pad:-pad]
        images.append((title, a))
        # This request lies wholly in the synthetic 48/208 checkerboard region.
        if not set(np.unique(reference[valid])).issubset({48, 208}):
            raise AssertionError('Chosen sharpness request is not the two-level checkerboard')
        nonblank = valid & (a > 1) & (a < 254)
        quality.append({'label': label, 'tile': chosen['tile'], 'missing_pixels': r['missing_pixels'],
            'intermediate_pixels': int((nonblank & ~np.isin(a, [48, 208])).sum()),
            'exact_fraction_nonblank': r['exact_fraction_nonblank'], 'mae_valid_nonblank': r['mae_valid_nonblank']})
    write_json(root / 'analysis/sharpness.json', quality)
    write_json(root / 'analysis/minimal-failure.json', {'fixture': item, 'request': minimal})
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, len(images), figsize=(3.2 * len(images), 3.7), constrained_layout=True)
    for ax, (title, a) in zip(axes, images):
        ax.imshow(a, cmap='gray', vmin=0, vmax=255, interpolation='nearest')
        ax.set_title(title, fontsize=11)
        ax.axis('off')
    fig.suptitle('Same rotated GeoTIFF and map extent; each view is 256 × 256 pixels', fontsize=13)
    fig.savefig(root / 'analysis/comparison.png', dpi=160)
    plt.close(fig)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    import sys
    analyze(Path(sys.argv[1]))
