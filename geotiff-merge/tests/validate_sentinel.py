"""Medium test using an existing Sentinel-derived reference; no downloads.

Run in Windows QGIS 4.2 Python. Keep the reference read-only; derived test inputs,
outputs, telemetry and reports go in a separate test workspace.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import runpy
import sys
import time
import traceback

import numpy as np
from osgeo import gdal
from qgis.PyQt.QtCore import QCoreApplication, QTimer

try:
    from qgis.core import Qgis
    QGIS_VERSION, QGIS_IMPORT_ERROR = Qgis.QGIS_VERSION, None
except ImportError as exc:
    QGIS_VERSION, QGIS_IMPORT_ERROR = None, str(exc)

gdal.UseExceptions()


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def prepare(reference, work):
    manifest_path = work / 'sentinel-inputs.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        assert manifest['reference_sha256'] == digest(reference), 'Reference changed'
        for item in manifest['inputs']:
            assert (work / 'inputs' / item['name']).stat().st_size == item['bytes']
        return manifest
    inputs = work / 'inputs'
    inputs.mkdir(exist_ok=False)
    before = digest(reference)
    manifest = dict(reference_sha256=before, reference_bytes=reference.stat().st_size,
                    provenance='Lossless pixel windows from the retained Sentinel-2 RGB benchmark reference', inputs=[])
    with gdal.Open(str(reference)) as src:
        width, height = src.RasterXSize, src.RasterYSize
        manifest.update(width=width, height=height, bands=src.RasterCount,
                        projection=src.GetProjection(), transform=src.GetGeoTransform(),
                        mask_flags=[src.GetRasterBand(i).GetMaskFlags() for i in range(1, src.RasterCount + 1)])
        windows = []
        for row in range(4):
            for col in range(4):
                x, y = width * col // 4, height * row // 4
                windows.append((f'sentinel-r{row}-c{col}.tif', x, y,
                                width * (col + 1) // 4 - x, height * (row + 1) // 4 - y))
        # Identical real-imagery overlap across a tile boundary.
        windows.append(('sentinel-overlap.tif', width // 4 - 128, height // 4 - 128, 512, 512))
        for name, x, y, w, h in windows:
            path = inputs / name
            print(f'Preparing {name}: {w} x {h}', flush=True)
            ds = gdal.Translate(str(path), src, format='GTiff', srcWin=[x, y, w, h],
                                creationOptions=['TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256',
                                                 'COMPRESS=NONE', 'BIGTIFF=YES'])
            ds.Close()
            manifest['inputs'].append(dict(name=name, bytes=path.stat().st_size,
                                           source_window=[x, y, w, h]))
    assert digest(reference) == before, 'Reference changed during preparation'
    manifest['total_input_bytes'] = sum(x['bytes'] for x in manifest['inputs'])
    save(manifest_path, manifest)
    return manifest


def sample_process(pid):
    """Read-only Windows process telemetry; no extra package in QGIS."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'rss', 'paged_peak', 'paged',
                'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak', 'private')]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times))
        seconds = sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:]) / 1e7
        return dict(monotonic=time.monotonic(), rss_bytes=counters.rss,
                    peak_rss_bytes=counters.peak, private_bytes=counters.private, cpu_seconds=seconds)
    finally:
        kernel.CloseHandle(handle)


def independent_check(reference, output, report):
    """Compare all output samples directly to the original reference, not the split inputs."""
    compared, valid_samples, nodata_samples = 0, 0, 0
    with gdal.Open(str(reference)) as src:
        gt = src.GetGeoTransform()
        rectangles = []
        for item in report['outputs']:
            with gdal.Open(str(output / item['name'])) as dst:
                ot = dst.GetGeoTransform()
                x, y = round((ot[0] - gt[0]) / gt[1]), round((ot[3] - gt[3]) / gt[5])
                rectangles.append((x, y, dst.RasterXSize, dst.RasterYSize))
                for row in range(0, dst.RasterYSize, 1024):
                    for col in range(0, dst.RasterXSize, 1024):
                        w, h = min(1024, dst.RasterXSize - col), min(1024, dst.RasterYSize - row)
                        a = src.ReadAsArray(x + col, y + row, w, h)
                        b = dst.ReadAsArray(col, row, w, h)
                        for band in range(1, src.RasterCount + 1):
                            am = src.GetRasterBand(band).GetMaskBand().ReadAsArray(x + col, y + row, w, h) != 0
                            bm = dst.GetRasterBand(band).GetMaskBand().ReadAsArray(col, row, w, h) != 0
                            assert np.array_equal(am, bm), 'Independent mask mismatch'
                            assert a[band - 1][am].tobytes() == b[band - 1][am].tobytes(), 'Independent pixel mismatch'
                            valid_samples += int(am.sum())
                            nodata_samples += int((~am).sum())
                        compared += w * h
            print(f'Independently checked {item["name"]}', flush=True)
        assert compared == src.RasterXSize * src.RasterYSize
        for i, (x, y, w, h) in enumerate(rectangles):
            assert 0 <= x and 0 <= y and x + w <= src.RasterXSize and y + h <= src.RasterYSize
            for xx, yy, ww, hh in rectangles[i + 1:]:
                assert x + w <= xx or xx + ww <= x or y + h <= yy or yy + hh <= y
    return dict(compared_pixels=compared, valid_band_samples=valid_samples, invalid_band_samples=nodata_samples)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--cpus', type=int)
    p.add_argument('--ram')
    p.add_argument('--single-file', action='store_true', help='Omit the size target to exercise one-file mode')
    p.add_argument('--base-name', default='mosaic')
    p.add_argument('--label', help='Fresh result label when retaining evidence from an earlier run')
    args = p.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    manifest = prepare(args.reference, work)
    label = args.label or ('limited' if args.cpus or args.ram else 'automatic')
    output = work / f'merged-{label}'
    assert not output.exists(), 'Test already started; inspect its existing evidence before rerunning'
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'merge_geotiffs.py'))
    app = QCoreApplication.instance() or QCoreApplication([])
    result = dict(qgis=QGIS_VERSION, qgis_core_import_error=QGIS_IMPORT_ERROR,
                  gdal=gdal.VersionInfo('RELEASE_NAME'),
                  python=sys.version, mode='QGIS Python with a real Qt event loop', heartbeat=0,
                  input_count=len(manifest['inputs']), input_bytes=manifest['total_input_bytes'], samples=[])
    cache_before, exceptions_before = gdal.GetCacheMax(), gdal.GetUseExceptions()
    began = time.monotonic()
    job = module['start'](work / 'inputs', output, target_size=None if args.single_file else '1GiB',
                          base_name=args.base_name, cpus=args.cpus, ram=args.ram)
    result['start_return_seconds'] = time.monotonic() - began
    timer = QTimer(app)
    timer.setInterval(250)
    errors = []
    def tick():
        result['heartbeat'] += 1
        sample = sample_process(job.process.pid)
        if sample:
            result['samples'].append(sample)
        if time.monotonic() - began > 1800:
            job.cancel()
            errors.append('Medium test exceeded 30 minutes')
        if job.done and job._timer is None:
            timer.stop()
            app.quit()
    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    result['wall_seconds'] = time.monotonic() - began
    result['worker_status'] = job.status()
    result['qgis_cache_unchanged'] = gdal.GetCacheMax() == cache_before
    result['qgis_exception_mode_unchanged'] = gdal.GetUseExceptions() == exceptions_before
    try:
        report = job.result()
        assert not errors, errors
        assert result['qgis_cache_unchanged'] and result['qgis_exception_mode_unchanged']
        assert result['heartbeat'] > 2
        if args.single_file:
            assert len(report['outputs']) == 1 and report['target_file_bytes'] is None
        else:
            assert all(o['bytes'] <= 1024**3 * 11 // 10 for o in report['outputs'])
        assert [o['name'] for o in report['outputs']] == [
            f'{args.base_name}-{i:05d}.tif' for i in range(1, len(report['outputs']) + 1)]
        console_log = job.log_path.read_text(encoding='utf-8')
        result['timing_updates'] = console_log.count('rough expected total')
        assert result['timing_updates'] > 2 and 'elapsed ' in console_log
        result['independent_verification'] = independent_check(args.reference, output, report)
        result['reference_unchanged'] = digest(args.reference) == manifest['reference_sha256']
        assert result['reference_unchanged']
        result['passed'] = True
    except Exception:
        result['passed'] = False
        result['error'] = traceback.format_exc()
    save(work / f'validation-{label}.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in ('samples', 'worker_status')}, indent=2), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
