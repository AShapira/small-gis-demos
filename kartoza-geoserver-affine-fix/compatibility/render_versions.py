#!/usr/bin/env python3
"""Run stock/patched WMS+GWC checks in a new stock-image container per treatment.

Uses the existing fixtures read-only. Each new container has its own catalog,
settings and writable layer; all are stopped afterward. No custom image is built.
"""
import argparse
from collections import Counter
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import time
import uuid
import numpy as np
from PIL import Image
import requests

from inspect_versions import ROOT, WORK, LIB, tar_files

spec = importlib.util.spec_from_file_location('affine_probe', ROOT.parent / 'geotiff-compression/rasterbench/affine_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
FIXTURES = ROOT.parent / 'geotiff-compression/.runs/affine-probe/evidence'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag')
    parser.add_argument('--phase', choices=['stock', 'patched'], required=True)
    parser.add_argument('--engine', default='podman')
    parser.add_argument('--connection')
    parser.add_argument('--port', type=int, default=18094)
    parser.add_argument('--full', action='store_true', help='Full 1180 requests per route; otherwise minimal regression cases')
    args = parser.parse_args()
    engine = [args.engine] + (['--connection', args.connection] if args.connection else [])
    label = args.tag.replace('--', '-')
    version_root = WORK / label
    info = json.loads((version_root / 'inspection.json').read_text())
    output = version_root / ('render-' + args.phase + '-' + uuid.uuid4().hex[:6])
    output.mkdir()
    container = 'affine-compat-' + label + '-' + args.phase + '-' + uuid.uuid4().hex[:6]
    password_file = WORK / 'admin-password'
    if not password_file.exists():
        password_file.write_text(secrets.token_hex(24))
        password_file.chmod(0o600)
    password = password_file.read_text().strip()
    session = requests.Session()
    session.auth = ('admin', password)
    probe.BASE = f'http://127.0.0.1:{args.port}/geoserver'
    entries = json.loads((FIXTURES / 'fixtures.json').read_text())

    def verify(_):
        for item in entries:
            path = FIXTURES / 'fixtures' / (item['name'] + '.tif')
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise RuntimeError('Fixture changed: ' + item['name'])
        return entries

    @lru_cache(maxsize=2)
    def source_array(path):
        with Image.open(FIXTURES / 'fixtures' / Path(path).name) as image:
            return np.array(image)

    probe.verify, probe.source_array = verify, source_array
    verify(output)

    def call(*command, data=None, check=True):
        r = subprocess.run(engine + list(command), input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check and r.returncode:
            raise RuntimeError(r.stderr.decode(errors='replace') + r.stdout.decode(errors='replace'))
        return r

    command = ['create', '--pull=never', '--name', container, '--label', 'affine.compatibility=2026-10-20',
               '--cpus', '4', '--memory', '8g', '-p', f'127.0.0.1:{args.port}:8080',
               '-v', 'rasterbench-israel-rgb-5gb-data:/data:ro',
               '-e', 'GEOSERVER_ADMIN_USER=admin', '-e', 'GEOSERVER_ADMIN_PASSWORD=' + password,
               '-e', 'SHOW_PASSWORD=false', '-e', 'INITIAL_MEMORY=2G', '-e', 'MAXIMUM_MEMORY=4G',
               '-e', 'SAMPLE_DATA=false']
    if args.phase == 'patched':
        command += ['--entrypoint', '/bin/bash']
    command += [info['image']]
    if args.phase == 'patched':
        command += ['/opt/affine-fix/start-with-affine-fix.sh']
    call(*command)
    result = {'tag': args.tag, 'phase': args.phase, 'image': info['image'], 'container': container,
              'full_matrix': args.full, 'source_fixture_sha256': {e['name']: e['sha256'] for e in entries}}
    try:
        files = {'compat-password': password.encode()}
        if args.phase == 'patched':
            files.update({'affine-fix/' + p.relative_to(version_root / 'candidate').as_posix(): p.read_bytes()
                          for p in (version_root / 'candidate').rglob('*') if p.is_file()})
        call('cp', '-', container + ':/opt', data=tar_files(files))
        call('start', container)
        started = time.monotonic()
        while time.monotonic() - started < 240:
            try:
                r = session.get(probe.BASE + '/rest/about/version.json', timeout=5)
                if r.ok:
                    result['version'] = r.json()
                    break
            except requests.RequestException:
                pass
            if call('inspect', '--format', '{{.State.Status}}', container).stdout.strip() == b'exited':
                raise RuntimeError('Test container exited before readiness; inspect private server log')
            time.sleep(2)
        else:
            raise RuntimeError('Readiness timeout')
        print(args.tag, args.phase, 'ready', flush=True)
        mounted_hashes = call('exec', container, 'sha256sum', *[e['path'].removeprefix('file:') for e in entries]).stdout.decode()
        actual_hashes = dict(line.split(None, 1)[::-1] for line in mounted_hashes.splitlines())
        for entry in entries:
            assert actual_hashes[entry['path'].removeprefix('file:')] == entry['sha256']
        result['mounted_fixture_sha256_verified'] = True
        result['active_jars'] = {}
        for name in info['original_jars']:
            data = call('exec', container, 'cat', LIB + '/' + name).stdout
            actual = hashlib.sha256(data).hexdigest()
            wanted = info['original_jars'][name] if args.phase == 'stock' else info['patched_jars'][name]['sha256']
            assert actual == wanted
            result['active_jars'][name] = actual
        # Remove only this isolated catalog's per-user rate rule, retaining concurrency rules.
        control_path = '/opt/geoserver/data_dir/controlflow.properties'
        control = call('exec', container, 'cat', control_path, check=False)
        if control.returncode == 0:
            (output / 'controlflow-original.properties').write_bytes(control.stdout)
            filtered = b'\n'.join(line for line in control.stdout.splitlines() if not line.strip().startswith(b'user.ows.wms.getmap=')) + b'\n'
            call('exec', '-i', container, 'tee', control_path, data=filtered)
            result['isolated_wms_rate_rule_removed'] = filtered != control.stdout
            time.sleep(3)
        probe.snapshot(output, session)
        probe.publish(output, session)
        if args.phase == 'patched':
            result['patched_g0_samples'] = []
            for name, tile in [('rot30_base', (24, 10021196, 6798186)),
                               ('rotm30_base', (20, 626306, 424906))]:
                item = next(e for e in entries if e['name'] == name)
                for route in ['wms', 'gwc']:
                    response = probe.request_image(session, item, tile, route=route)
                    probe.record(output, 'patched-g0-samples', *response)
                    result['patched_g0_samples'].append(response[0])
                    if name == 'rot30_base':
                        assert response[0]['missing_pixels'] == 0
        gutter = 0 if args.phase == 'stock' else 1
        probe.configure(output, session, gutter, 1)
        result['gutter'] = gutter
        if args.phase == 'stock':
            item = next(e for e in entries if e['name'] == 'rot30_base')
            result['minimal'] = []
            for route in ['wms', 'gwc']:
                response = probe.request_image(session, item, (24, 10021196, 6798186), route=route)
                probe.record(output, 'stock-minimal', *response)
                result['minimal'].append(response[0])
                assert response[0]['missing_pixels'] > 1000
        else:
            probe.regression(output, session, 1)
            result['regression'] = json.loads((output / 'regression-result.json').read_text())
            if args.full:
                probe.configure(output, session, gutter, 1)
                probe.scan(output, session, label='patched-g1', routes=[('wms','nearest neighbor'),('gwc','nearest neighbor')], pad=1)
                rows = [json.loads(line) for line in (output / 'patched-g1.jsonl').read_text().splitlines()]
                error_file = output / 'patched-g1-errors.jsonl'
                errors = [json.loads(line) for line in error_file.read_text().splitlines()] if error_file.exists() else []
                result['matrix'] = {}
                for route in ['wms', 'gwc']:
                    subset = [r for r in rows if r['route'] == route]
                    interior = [r for r in subset if r['valid_pixels'] == 65536]
                    result['matrix'][route] = {'successful_requests': len(subset), 'errors':sum(r['route']==route for r in errors),
                        'missing_pixels':sum(r['missing_pixels'] for r in subset),'interior_requests':len(interior),
                        'interior_missing_pixels':sum(r['missing_pixels'] for r in interior),
                        'north_up_missing_pixels':sum(r['missing_pixels'] for r in subset if r['name'].startswith('rot0_')),
                        'cache_headers':dict(Counter(r['cache'] for r in subset))}
                print(json.dumps(result['matrix']),flush=True)
            item = next(e for e in entries if e['name'] == 'rot30_base')
            probe.truncate(session, item['name'])
            cache = [probe.request_image(session, item, (24,10021196,6798186),route='gwc')[0]['cache'] for _ in range(2)]
            assert cache == ['MISS','HIT'],cache
            result['cache_sequence'] = cache
        result['fixtures_unchanged'] = bool(verify(output))
    finally:
        (output / 'server.log').write_bytes(call('logs', container, check=False).stdout)
        call('stop', '--time', '20', container, check=False)
        result['container_state_after'] = call('inspect','--format','{{.State.Status}}',container).stdout.decode().strip()
        (output / 'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print('Saved', output.name, 'container',result['container_state_after'],flush=True)


if __name__ == '__main__':
    main()
