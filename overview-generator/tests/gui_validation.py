"""Run inside a separate QGIS desktop instance with --code and an isolated profile.

No UI input injection; checks the actual QGIS console widget and Qt event loop.
"""
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time
import traceback
from osgeo import gdal
from qgis.core import Qgis, QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import QCoreApplication, QThread, QTimer
from qgis.utils import iface
from console import console

ROOT = Path(os.environ['OVERVIEW_VALIDATION_ROOT'])
RUNS = ROOT / '.validation'
RUNS.mkdir(exist_ok=True)
OUT = RUNS / ('gui-' + time.strftime('%Y%m%d-%H%M%S'))
OUT.mkdir(exist_ok=False)
(RUNS / 'latest-gui.txt').write_text(str(OUT))
DATA = OUT / 'generated'; DATA.mkdir()
og = runpy.run_path(str(ROOT / 'overview-generator.py'))
results = dict(qgis=Qgis.QGIS_VERSION, mode=os.environ.get('OVERVIEW_GUI_MODE','QGIS desktop'), pid=os.getpid(), tests=[], heartbeat=0, snapshots=[])
job = None
phase = 'start'
began = time.monotonic()
original_stdout = None

def persist():
    (OUT / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')

def record(name, **details):
    results['tests'].append(dict(test=name, outcome='PASS', **details)); persist()

def console_text():
    return console._console.console.shell_output.text()

def finish(error=None):
    timer.stop()
    if error:
        results['tests'].append(dict(test=phase, outcome='FAIL', traceback=error))
        if job and not job.done:
            job.cancel()
    results['elapsed'] = time.monotonic() - began
    persist()
    (OUT / 'console.txt').write_text(console_text(), encoding='utf-8')
    window = iface.mainWindow() if iface else console._console
    window.grab().save(str(OUT / 'qgis-console.png'))
    (OUT / 'complete.json').write_text(json.dumps(dict(failed=bool(error), tests=len(results['tests']))))
    # This instance was launched solely for these generated tests.
    QTimer.singleShot(1000, QCoreApplication.instance().quit)

def tick():
    global job, phase, before_heartbeat, initial_cache, initial_exceptions
    try:
        results['heartbeat'] += 1
        if time.monotonic() - began > 120:
            raise TimeoutError('GUI validation exceeded 120 seconds')
        if phase == 'start':
            console.show_console()
            console._console.resize(1100, 650)
            for n in range(2):
                p = DATA / ('constant-%s.tif' % n)
                ds = gdal.GetDriverByName('GTiff').Create(str(p), 4096, 4096, 1, gdal.GDT_Byte, options=['TILED=YES','COMPRESS=DEFLATE'])
                ds.GetRasterBand(1).Fill(42 + n)
                ds.SetGeoTransform((100, 1, 0, 5000, 0, -1))
                ds = None
            layer = QgsRasterLayer(str(DATA / 'constant-0.tif'), 'Synthetic test', 'gdal')
            assert layer.isValid()
            QgsProject.instance().addMapLayer(layer)
            try:
                og['start'](str(DATA), workers=2)
            except RuntimeError as exc:
                assert 'Remove target TIFF' in str(exc)
                record('loaded-layer real-run refusal', message=str(exc))
            else:
                raise AssertionError('Loaded layer was not rejected')
            initial_cache, initial_exceptions = gdal.GetCacheMax(), gdal.GetUseExceptions()
            before_heartbeat = results['heartbeat']
            t = time.monotonic()
            job = og['start'](str(DATA), dry_run=True, workers=2, threads_per_file=1, progress_interval=.5)
            record('start returns without blocking', return_seconds=time.monotonic()-t)
            phase = 'dry'
        elif phase == 'dry' and job.done and job._timer is None:
            assert job.status()['counts'] == {'planned': 2}, job.status()
            assert results['heartbeat'] > before_heartbeat
            record('loaded-layer dry run and Qt timer cleanup', status=job.status())
            QgsProject.instance().removeAllMapLayers()
            job = og['start'](str(DATA), workers=2, threads_per_file=1, progress_interval=.5)
            phase = 'build'
        elif phase == 'build':
            results['snapshots'].append(job.status())
            if not job.done or job._timer is not None: return
            assert job.status()['counts'] == {'ok': 2}, job.status()
            assert job not in sys._overview_generator_live_jobs
            assert gdal.GetCacheMax() == initial_cache and gdal.GetUseExceptions() == initial_exceptions
            for n in range(2):
                ds = gdal.Open(str(DATA / ('constant-%s.tif' % n)))
                b = ds.GetRasterBand(1)
                assert b.GetOverviewCount() == 4
                for i in range(4):
                    a = b.GetOverview(i).ReadAsArray()
                    assert (a == 42 + n).all()
                b = ds = None
            record('GUI start builds real external overviews', status=job.status(), all_pixels_constant=True)
            text = console_text()
            assert '[overview-generator]' in text and 'finished |' in text and 'Summary:' in text, text
            record('actual Python console updates', characters=len(text), heartbeat=results['heartbeat'])
            record('global GDAL settings and live-job cleanup preserved')
            phase = 'cancel'
            job = og['start'](str(DATA), workers=2, threads_per_file=1, progress_interval=.5)
            job.cancel()
        elif phase == 'cancel' and job.done and job._timer is None:
            assert job.status()['phase'] == 'cancelled', job.status()
            record('GUI job.cancel and timer cleanup', status=job.status())
            finish()
    except Exception:
        finish(traceback.format_exc())

timer = QTimer(QCoreApplication.instance())
timer.setInterval(50)
timer.timeout.connect(tick)
timer.start()
