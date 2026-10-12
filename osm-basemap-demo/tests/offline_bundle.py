"""Load and validate an exact export in a fresh directory on an isolated network."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import sha256, env_values, read_config
from catalog import Rest, wait_ready, ENGLISH_GROUPS, STYLE_FILES
from verify import validate, fetch_png

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('archive', type=Path)
parser.add_argument('--destination', type=Path, required=True)
parser.add_argument('--port', type=int, default=8603)
args = parser.parse_args()
archive = args.archive.resolve()
archive_info = json.loads(archive.with_suffix('.tar.json').read_text())
assert sha256(archive) == archive_info['sha256']
destination = args.destination.resolve()
destination.mkdir()  # Never reuse or delete a previous validation directory.
with tarfile.open(archive) as source:
    roots = {Path(n).parts[0] for n in source.getnames()}
    assert len(roots) == 1, roots
    target = destination / roots.pop()
    source.extractall(destination, filter='data')
checked = 0
for line in (target / 'SHA256SUMS').read_text().splitlines():
    checksum, name = line.split('  ', 1)
    assert sha256(target / name) == checksum, name
    checked += 1
print(f'Verified archive and {checked} file checksums', flush=True)
data_hashes = {name: sha256(target / 'data_dir/data' / name) for name in ('regional.mbtiles', 'world.gpkg')}
# Change only the restored copy's host port. Remove its PNG tiles after checksum
# verification to obtain new cold tile-cache measurements, keeping the GWC config.
env_path = target / '.env'
env_path.write_text('\n'.join('GEOSERVER_PORT=' + str(args.port) if line.startswith('GEOSERVER_PORT=') else line for line in env_path.read_text().splitlines()) + '\n')
env_path.chmod(0o600)
cached = list((target / 'cache').rglob('*.png'))
for path in cached:
    path.unlink()
cfg = read_config(target / 'config.json')
cfg['port'] = args.port
cfg['engine'] = 'podman'
project = 'offline-check-' + target.name
compose = ['podman-compose', '-p', project, '-f', str(target / 'compose.yaml'), '-f', str(target / 'compose.podman.yaml')]
log_path = destination / 'commands.log'
log_path.touch(mode=0o600)
def run(args, **kwargs):
    with log_path.open('a') as log:
        subprocess.run(args, cwd=target, stdout=log, stderr=subprocess.STDOUT, check=True, **kwargs)
run(['podman', 'load', '-i', str(target / 'images/geoserver.tar')])
manifest = json.loads((target / 'source-manifest.json').read_text())
loaded = json.loads(subprocess.check_output(['podman', 'image', 'inspect', manifest['images']['geoserver']['tag']]))[0]['Id']
assert loaded.removeprefix('sha256:') == manifest['images']['geoserver']['id'].removeprefix('sha256:')
report = {'archive': archive_info, 'checksums_verified': checked, 'data_sha256': data_hashes, 'image_id': loaded,
          'catalog_reprovisioned': False, 'restored_pngs_removed_for_cold_cache': len(cached), 'complete': False}
sidecar = archive.with_suffix('.validation.json')
try:
    started = time.monotonic()
    run(compose + ['up', '-d'])
    base = f'http://127.0.0.1:{args.port}/geoserver'
    wait_ready(base, env_values(target))
    report['startup_seconds'] = round(time.monotonic() - started, 2)
    names = subprocess.check_output(['podman', 'ps', '--filter', 'label=io.podman.compose.project=' + project, '--format', '{{.Names}}'], text=True).splitlines()
    assert len(names) == 1, names
    print(f'Fresh exported container ready in {report["startup_seconds"]} seconds', flush=True)
    validate(cfg, target, container_name=names[0], cold_cache=True)
    live = json.loads((target / 'validation/report.json').read_text())
    report['service_checks'] = live['checks']
    report['render_count'] = len(live['renders'])
    report['english_wmts_timings'] = {name: {mode: live['renders'][name + '-en-wmts-' + mode]['seconds'] for mode in ('cold', 'warm')} for name in STYLE_FILES}
    report['wmts_zoom6'] = {}
    z = 6; lon, lat = 14.421, 50.087; n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    for style in STYLE_FILES:
        params = {'SERVICE':'WMTS', 'VERSION':'1.0.0', 'REQUEST':'GetTile', 'LAYER':'omt:' + style + '-en', 'STYLE':'', 'TILEMATRIXSET':'EPSG:3857', 'TILEMATRIX':'EPSG:3857:6', 'TILECOL':x, 'TILEROW':y, 'FORMAT':'image/png'}
        report['wmts_zoom6'][style] = fetch_png(base, '/gwc/service/wmts', params, target / 'validation' / (style + '-en-wmts-zoom6.png'))
    browser_env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(ROOT / '.artifacts/browsers'), BROWSER_BASE_URL=f'http://127.0.0.1:{args.port}', BROWSER_OUTPUT=str(target / 'validation'))
    run(['node', str(ROOT / 'tests/browser.cjs')], env=browser_env)
    report['browser'] = json.loads((target / 'validation/browser-report.json').read_text())
    assert all(sha256(target / 'data_dir/data' / name) == digest for name, digest in data_hashes.items())
    report['complete'] = True
finally:
    sidecar.write_text(json.dumps(report, indent=2) + '\n')
    run(compose + ['down'])
print(json.dumps({'complete': report['complete'], 'startup_seconds': report['startup_seconds'], 'render_count': report['render_count'], 'extra_wmts_zoom6': len(report['wmts_zoom6']), 'browser_screenshots': report['browser']['screenshots'], 'archive': archive_info}, indent=2))
