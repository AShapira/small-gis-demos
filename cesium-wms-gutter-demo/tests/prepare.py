"""Prepare references from existing immutable TIFFs; never modify or generate TIFFs."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(REPO))
from rasterbench.affine_probe import cases, expected, tile, tile_at, world

study = REPO / '.runs/affine-probe/evidence'
fixtures = json.loads((study / 'fixtures.json').read_text())
references = ROOT / '.artifacts/references'
references.mkdir(parents=True, exist_ok=True)
matrix = []
checksums = {}
for fixture in fixtures:
    path = study / 'fixtures' / (fixture['name'] + '.tif')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == fixture['sha256'], f'Changed fixture: {path}'
    checksums[fixture['name']] = digest
    with Image.open(path) as image:
        source = np.array(image)
    for z, x, y in cases(fixture):
        reference, valid = expected(source, fixture['gt'], tile(z, x, y))
        rgba = np.stack([reference, reference, reference, valid.astype(np.uint8) * 255], axis=-1)
        name = f"{fixture['name']}_{z}_{x}_{y}"
        Image.fromarray(rgba).save(references / (name + '.png'))
        matrix.append({'name': fixture['name'], 'tile': [z, x, y], 'id': name,
                       'validPixels': int(valid.sum()), 'fullyInside': bool(valid.all())})
fixture = next(f for f in fixtures if f['name'] == 'rot30_base')
benchmark = []
for z in (20, 22, 24):
    _, cx, cy = tile_at(z, *world(fixture['gt'], 4096 * .5, 4096 * .6))
    benchmark.extend([z, cx + (i % 8 - 3) * 4, cy + (i // 8 - 1) * 4] for i in range(32))
(ROOT / 'fixtures.json').write_text(json.dumps([{k: v for k, v in f.items() if k != 'path'} for f in fixtures], indent=2) + '\n')
(ROOT / 'cases.json').write_text(json.dumps({'matrix': matrix, 'benchmark': benchmark}, separators=(',', ':')) + '\n')
(ROOT / 'evidence/fixture-checksums.json').write_text(json.dumps(checksums, indent=2) + '\n')
print(f'Prepared {len(matrix)} independent reference tiles and {len(benchmark)} benchmark requests; verified {len(fixtures)} TIFF checksums.')
