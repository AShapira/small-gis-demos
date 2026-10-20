#!/usr/bin/env python3
"""Use the retained immutable fixtures and independent affine oracle against a lab server."""
import argparse
from collections import Counter
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
import requests

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT.parent / 'geotiff-compression'
spec = importlib.util.spec_from_file_location('affine_probe', STUDY / 'rasterbench/affine_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['ready', 'publish', 'scan', 'regression', 'configure', 'cache-check'])
    parser.add_argument('--url', default='http://127.0.0.1:18093/geoserver')
    parser.add_argument('--password-file', type=Path, default=ROOT / '.artifacts/admin-password')
    parser.add_argument('--fixtures', type=Path, default=STUDY / '.runs/affine-probe/evidence')
    parser.add_argument('--label', default='stock-g0')
    parser.add_argument('--gutter', type=int, default=0)
    parser.add_argument('--quick', action='store_true')
    args = parser.parse_args()
    probe.BASE = args.url
    output = ROOT / '.artifacts/rendering'
    output.mkdir(exist_ok=True, parents=True)
    session = requests.Session()
    session.auth = ('admin', args.password_file.read_text().strip())

    if args.action == 'ready':
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            try:
                r = session.get(args.url + '/rest/about/version.json', timeout=5)
                if r.ok:
                    (ROOT / 'evidence' / (args.label + '-version.json')).write_text(json.dumps(r.json(), indent=2) + '\n')
                    print('GeoServer ready:', args.url, flush=True)
                    return
            except requests.RequestException:
                pass
            time.sleep(2)
        raise RuntimeError('GeoServer readiness timed out')

    entries = json.loads((args.fixtures / 'fixtures.json').read_text())

    def verify(_):
        for item in entries:
            path = args.fixtures / 'fixtures' / (item['name'] + '.tif')
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise RuntimeError('Source TIFF checksum changed: ' + item['name'])
        return entries

    @lru_cache(maxsize=2)
    def source_array(server_path):
        with Image.open(args.fixtures / 'fixtures' / Path(server_path).name) as im:
            return np.array(im)

    probe.verify, probe.source_array = verify, source_array
    verify(output)
    if args.action == 'publish':
        probe.snapshot(output, session)
        probe.publish(output, session)
    elif args.action == 'configure':
        probe.configure(output, session, args.gutter, 1)
    elif args.action == 'scan':
        probe.scan(output, session, quick=args.quick, label=args.label,
                   routes=[('wms', 'nearest neighbor'), ('gwc', 'nearest neighbor')], pad=args.gutter)
        rows = [json.loads(line) for line in (output / (args.label + '.jsonl')).read_text().splitlines()]
        errorfile = output / (args.label + '-errors.jsonl')
        errors = [json.loads(line) for line in errorfile.read_text().splitlines()] if errorfile.exists() else []
        summary = {'label': args.label, 'gutter': args.gutter, 'fixture_sha256': {e['name']: e['sha256'] for e in entries}, 'routes': {}}
        for route in ('wms', 'gwc'):
            subset = [r for r in rows if r['route'] == route]
            interior = [r for r in subset if r['valid_pixels'] == 65536]
            summary['routes'][route] = {
                'successful_requests': len(subset), 'errors': len([r for r in errors if r['route'] == route]),
                'missing_pixels': sum(r['missing_pixels'] for r in subset),
                'interior_requests': len(interior), 'interior_missing_pixels': sum(r['missing_pixels'] for r in interior),
                'north_up_missing_pixels': sum(r['missing_pixels'] for r in subset if r['name'].startswith('rot0_')),
                'cache_headers': dict(Counter(r['cache'] for r in subset)),
            }
        (ROOT / 'evidence' / (args.label + '.json')).write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary['routes'], indent=2), flush=True)
    elif args.action == 'regression':
        probe.regression(output, session, args.gutter)
    elif args.action == 'cache-check':
        item = next(e for e in entries if e['name'] == 'rot30_base')
        probe.truncate(session, item['name'])
        tile = (24, 10021196, 6798186)
        first = probe.request_image(session, item, tile, route='gwc')[0]
        second = probe.request_image(session, item, tile, route='gwc')[0]
        assert first['cache'] == 'MISS' and second['cache'] == 'HIT', (first, second)
        (ROOT / 'evidence' / (args.label + '-cache.json')).write_text(json.dumps([first, second], indent=2) + '\n')
        print('Verified GWC MISS followed by HIT', flush=True)
    verify(output)


if __name__ == '__main__':
    main()
