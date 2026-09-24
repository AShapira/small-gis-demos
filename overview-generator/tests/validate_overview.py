"""Real Windows/GDAL CLI integration tests. Each invocation gets a NEW directory.

Usage: python tests/validate_overview.py
Launch through tests/run-windows.ps1 -Mode cli. No user rasters are read or changed.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np
from osgeo import gdal, osr

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'overview-generator.py'
RUNS = ROOT / '.validation'
RUNS.mkdir(exist_ok=True)
OUT = RUNS / ('cli-' + time.strftime('%Y%m%d-%H%M%S'))
OUT.mkdir(exist_ok=False)
(RUNS / 'latest-cli.txt').write_text(str(OUT))
LOGS = OUT / 'cli-logs'
LOGS.mkdir()
RESULTS = []
gdal.UseExceptions()

def save():
    (OUT / 'results.json').write_text(json.dumps(RESULTS, indent=2), encoding='utf-8')

def check(name, fn):
    began = time.monotonic()
    try:
        details = fn()
        r = dict(test=name, outcome='PASS', details=details)
    except Exception:
        r = dict(test=name, outcome='FAIL', traceback=traceback.format_exc())
    r['seconds'] = round(time.monotonic() - began, 3)
    RESULTS.append(r)
    save()
    print(json.dumps(r), flush=True)

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def snapshot(directory):
    return {p.name: (sha(p), p.stat().st_size, p.stat().st_mtime_ns)
            for p in directory.iterdir() if p.is_file()}

def fixture(path, kind='byte', width=1024, height=768):
    bands = 3 if kind == 'multi' else 1
    typ = gdal.GDT_Float32 if kind == 'float' else gdal.GDT_Byte
    ds = gdal.GetDriverByName('GTiff').Create(str(path), width, height, bands, typ,
                                           options=['TILED=YES', 'COMPRESS=DEFLATE'])
    ds.SetGeoTransform((100, 2, 0, 2000, 0, -2))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32636)
    ds.SetProjection(srs.ExportToWkt())
    y, x = np.indices((height, width))
    for n in range(1, bands + 1):
        if kind == 'float':
            a = (x * 0.5 + y * 0.25).astype(np.float32)
            a[32:96, 32:96] = -9999
            ds.GetRasterBand(n).SetNoDataValue(-9999)
        else:
            a = ((x * 3 + y * 5 + n * 41) % 251).astype(np.uint8)
        ds.GetRasterBand(n).WriteArray(a)
    ds = None

def overview(path, method='NEAREST', levels=(2, 4), internal=False):
    ds = gdal.Open(str(path), gdal.GA_Update if internal else gdal.GA_ReadOnly)
    assert ds.BuildOverviews(method, list(levels)) == 0
    ds = None

def base(path):
    ds = gdal.Open(str(path))
    result = (hashlib.sha256(ds.ReadRaster()).hexdigest(), ds.GetGeoTransform(), ds.GetProjection(),
              [(ds.GetRasterBand(n).DataType, ds.GetRasterBand(n).GetNoDataValue())
               for n in range(1, ds.RasterCount + 1)])
    ds = None
    return result

def internal_count(path):
    gdal.SetThreadLocalConfigOption('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
    gdal.SetThreadLocalConfigOption('GDAL_PAM_ENABLED', 'NO')
    try:
        ds = gdal.Open(str(path))
        count = ds.GetRasterBand(1).GetOverviewCount()
        ds = None
        return count
    finally:
        gdal.SetThreadLocalConfigOption('GDAL_DISABLE_READDIR_ON_OPEN', None)
        gdal.SetThreadLocalConfigOption('GDAL_PAM_ENABLED', None)

def cli(directory, label, extra=(), expected=0):
    logdir = LOGS / label
    logdir.mkdir()
    cmd = [sys.executable, str(SCRIPT), str(directory), '--workers', '2', '--threads-per-file', '1',
           '--progress-interval', '0.5', '--log-dir', str(logdir)] + list(extra)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    (logdir / 'stdout.txt').write_bytes(p.stdout)
    (logdir / 'command.json').write_text(json.dumps(dict(argv=cmd, exit_code=p.returncode), indent=2))
    assert p.returncode == expected, (p.returncode, p.stdout.decode(errors='replace'))
    files = list(logdir.glob('*.summary.json'))
    summary = json.loads(files[0].read_text(encoding='utf-8')) if files else None
    rows = [json.loads(line) for f in logdir.glob('*.results.jsonl') for line in f.read_text(encoding='utf-8').splitlines()]
    assert not (directory / '.overview-generator.lock').exists()
    return summary, rows, p.stdout.decode(errors='replace')

def compare(path, ref, factors=(2, 4)):
    ds, oracle = gdal.Open(str(path)), gdal.Open(str(ref))
    pixels = 0
    dims = []
    for n in range(1, ds.RasterCount + 1):
        b, rb = ds.GetRasterBand(n), oracle.GetRasterBand(n)
        assert b.GetOverviewCount() == len(factors)
        for i, f in enumerate(factors):
            o, r = b.GetOverview(i), rb.GetOverview(i)
            dim = ((ds.RasterXSize + f - 1) // f, (ds.RasterYSize + f - 1) // f)
            assert (o.XSize, o.YSize) == dim
            np.testing.assert_array_equal(o.ReadAsArray(), r.ReadAsArray())
            assert o.GetNoDataValue() == r.GetNoDataValue()
            pixels += o.XSize * o.YSize
            dims.append([n, f, *dim])
    o = r = b = rb = ds = oracle = None
    return dict(pixels_compared=pixels, dimensions=dims)

def matrix(method):
    d, refs = OUT / ('matrix-' + method), OUT / ('reference-' + method)
    d.mkdir(); refs.mkdir()
    baseline, hashes = {}, {}
    for kind in ('byte', 'float', 'multi'):
        for state in ('none', 'external', 'embedded'):
            p = d / (kind + '-' + state + '.tif')
            fixture(p, kind)
            ref = refs / p.name
            shutil.copyfile(p, ref)
            overview(ref, method.upper(), internal=True)
            if state != 'none':
                overview(p, internal=(state == 'embedded'))
            baseline[p.name], hashes[p.name] = base(p), sha(p)
    before = snapshot(d)
    s, rows, _ = cli(d, method + '-dry', ['--dry-run', '--resampling', method])
    assert s['counts'] == {'planned': 9}, s
    assert snapshot(d) == before, 'Dry run changed data, sidecar, size, or modification time'
    assert sum(r['would_remove_internal'] for r in rows) == 3
    dry_result = dict(planned=9, files_preserved=len(before), hashes_sizes_mtimes_identical=True)
    RESULTS.append(dict(test=method + ' dry-run preservation', outcome='PASS', details=dry_result)); save()
    s, rows, text = cli(d, method + '-build', ['--resampling', method])
    assert s['counts'] == {'ok': 9}, rows
    assert sum(r['internal_overviews_removed'] for r in rows) == 3
    details = {}
    for p in d.glob('*.tif'):
        assert base(p) == baseline[p.name], 'Base samples or metadata changed: ' + p.name
        assert internal_count(p) == 0, p.name
        assert Path(str(p) + '.ovr').exists()
        if 'embedded' not in p.name:
            assert sha(p) == hashes[p.name]
        details[p.name] = compare(p, refs / p.name)
    return dict(files=9, dry_run=dry_result, outputs=details, base_pixels_metadata_preserved=True)

def rebuild():
    d = OUT / 'matrix-bilinear'
    old = {p.name: sha(p) for p in d.glob('*.ovr')}
    s, rows, _ = cli(d, 'rebuild-average', ['--resampling', 'average'])
    assert s['counts'] == {'ok': 9}, rows
    assert all(len(r['deleted_sidecars']) == 1 for r in rows)
    for p in d.glob('*.tif'):
        compare(p, OUT / 'reference-average' / p.name)
    changed = sum(sha(p) != old[p.name] for p in d.glob('*.ovr'))
    assert changed == 9
    return dict(files=9, all_old_sidecars_replaced=True, different_resampling_changed_all_outputs=True)

def analytic():
    d = OUT / 'analytic'; d.mkdir()
    p = d / 'plane.tif'
    fixture(p, 'float', 1024, 768)
    details = {}
    for method in ('average', 'bilinear'):
        cli(d, 'analytic-' + method, ['--resampling', method])
        ds = gdal.Open(str(p))
        a = ds.GetRasterBand(1).GetOverview(0).ReadAsArray()
        yy, xx = np.indices(a.shape)
        expected = (2 * xx + .5) * .5 + (2 * yy + .5) * .25
        np.testing.assert_allclose(a[100:-2, 100:-2], expected[100:-2, 100:-2], rtol=0, atol=1e-5)
        assert np.all(a[18:46, 18:46] == -9999)
        assert -9999 == ds.GetRasterBand(1).GetOverview(0).GetNoDataValue()
        details[method] = dict(interior_plane_error=float(np.max(np.abs(a[100:-2,100:-2]-expected[100:-2,100:-2]))), nodata_cells=28*28)
        ds = None
    return details

def odd():
    d, refd = OUT / 'odd', OUT / 'odd-ref'; d.mkdir(); refd.mkdir()
    for name, w, h in [('odd.tiff', 1001, 777), ('narrow.GEOTIFF', 1, 1025)]:
        p = d / name; fixture(p, 'float', w, h)
        shutil.copyfile(p, refd / name)
        # External and internal GDAL pyramids can differ on fractional odd-size
        # reductions; compare with the same external-overview storage path.
        overview(refd / name, 'AVERAGE', (2, 4, 8), False)
    s, rows, _ = cli(d, 'odd', ['--levels', '8', '2', '4', '2', '--resampling', 'average'])
    assert s['counts'] == {'ok': 2}, rows
    return {p.name: compare(p, refd / p.name, (2, 4, 8)) for p in d.iterdir() if p.suffix.lower() in ('.tiff', '.geotiff')}

def keep_and_small():
    d = OUT / 'keep'; d.mkdir()
    fixture(d / 'embedded.tif'); overview(d / 'embedded.tif', internal=True)
    fixture(d / 'small.tif', width=128, height=64); overview(d / 'small.tif')
    before = snapshot(d)
    s, rows, _ = cli(d, 'keep', ['--keep-internal'])
    assert s['counts'] == {'skipped': 2}, rows
    assert snapshot(d) == before
    return rows

def corrupt():
    d = OUT / 'corrupt'; d.mkdir()
    (d / 'broken.tif').write_bytes(b'generated invalid TIFF\x00')
    fixture(d / 'healthy.tif')
    s, rows, _ = cli(d, 'corrupt', expected=1)
    assert s['counts'] == {'ok': 1, 'failed': 1}, rows
    assert s['phase'] == 'finished_with_errors'
    assert not (d / 'broken.tif.ovr').exists()
    return rows

def concurrent():
    d = OUT / 'parallel'; d.mkdir()
    for n in range(3):
        fixture(d / ('large-%s.tif' % n), 'multi', 4096, 4096)
    s, rows, text = cli(d, 'parallel')
    assert s['counts'] == {'ok': 3}, rows
    assert 'active 2' in text, 'No observation of simultaneous two-worker activity'
    threads = set()
    for p in (LOGS / 'parallel').glob('*.log'):
        for line in p.read_text().splitlines():
            if 'OK {' in line: threads.add(line.split('INFO ')[1].split(' ')[0])
    return dict(configured_workers=2, observed_active_2=True, files=3, elapsed=s['elapsed_seconds'])

def cancellation():
    d = OUT / 'cancel'; d.mkdir()
    # Several compressed, generated Byte images. No production input.
    rng = np.random.default_rng(712)
    tile = rng.integers(0, 256, (256, 8192), dtype=np.uint8)
    for n in range(4):
        p = d / ('cancel-%s.tif' % n)
        ds = gdal.GetDriverByName('GTiff').Create(str(p), 8192, 8192, 1, gdal.GDT_Byte,
                                               options=['TILED=YES', 'COMPRESS=DEFLATE'])
        for y in range(0, 8192, 256): ds.GetRasterBand(1).WriteArray(tile, 0, y)
        ds = None
    originals = {p.name: sha(p) for p in d.glob('*.tif')}
    logs = LOGS / 'cancel'; logs.mkdir()
    cmd = [sys.executable, str(SCRIPT), str(d), '--workers', '2', '--threads-per-file', '1',
           '--compression-level', '9', '--progress-interval', '0.5', '--log-dir', str(logs)]
    with (logs / 'stdout.txt').open('wb') as output:
        proc = subprocess.Popen(cmd, stdout=output, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NEW_CONSOLE)
        (logs / 'process.json').write_text(json.dumps(dict(pid=proc.pid, argv=cmd), indent=2))
        deadline = time.monotonic() + 45
        observed = []
        while time.monotonic() < deadline and proc.poll() is None:
            observed = [p.name for p in d.glob('*.ovr') if p.stat().st_size > 0]
            if observed: break
            time.sleep(.02)
        assert observed and proc.poll() is None, 'No active partial output before cancellation'
        helper = subprocess.run([sys.executable, str(ROOT / 'tests' / 'send_ctrl_c.py'), str(proc.pid)],
                                capture_output=True, timeout=15)
        (logs / 'signal.txt').write_bytes(helper.stdout + helper.stderr)
        assert helper.returncode == 0
        rc = proc.wait(timeout=90)
    assert rc == 130, (rc, (logs / 'stdout.txt').read_text(errors='replace'))
    summary = json.loads(next(logs.glob('*.summary.json')).read_text())
    rows = [json.loads(x) for x in next(logs.glob('*.results.jsonl')).read_text().splitlines()]
    assert summary['phase'] == 'cancelled' and summary['completed'] == 4, summary
    assert len(rows) == 4 and summary['counts'].get('cancelled', 0) >= 1
    for row in rows:
        if row['status'] == 'cancelled': assert not Path(row['path'] + '.ovr').exists(), row
    assert any(r.get('partial_output_deleted') for r in rows), rows
    assert not (d / '.overview-generator.lock').exists()
    assert {p.name: sha(p) for p in d.glob('*.tif')} == originals
    return dict(exit_code=rc, observed_partial_outputs_before_signal=observed, summary=summary, files=rows)

def invalid_args():
    d = OUT / 'invalid-args'; d.mkdir()
    cli(d, 'invalid-args', ['--workers', '0'], expected=2)
    return dict(exit_code=2)

if __name__ == '__main__':
    (OUT / 'process.json').write_text(json.dumps(dict(pid=os.getpid(), started=time.ctime(), script=str(SCRIPT), script_sha256=sha(SCRIPT))))
    for name, fn in [('bilinear state/type matrix', lambda: matrix('bilinear')),
                     ('average state/type matrix', lambda: matrix('average')),
                     ('rebuild existing external overviews', rebuild), ('analytic pixels and NoData', analytic),
                     ('odd/narrow dimensions and explicit factors', odd), ('keep internal and small preservation', keep_and_small),
                     ('corrupt input isolation', corrupt), ('two parallel workers', concurrent),
                     ('native CLI Ctrl+C cancellation', cancellation), ('invalid CLI arguments', invalid_args)]:
        check(name, fn)
    (OUT / 'complete.json').write_text(json.dumps(dict(passed=sum(r['outcome']=='PASS' for r in RESULTS), failed=sum(r['outcome']=='FAIL' for r in RESULTS)), indent=2))
