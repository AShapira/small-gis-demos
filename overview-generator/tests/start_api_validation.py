"""Real PyQGIS and Qt event-loop API tests, independent of desktop availability."""
import io, json, os, runpy, sys, threading, time, traceback
from pathlib import Path
from osgeo import gdal
from qgis.PyQt.QtCore import QCoreApplication, QTimer
from qgis.core import QgsProject

root = Path(__file__).resolve().parents[1]
(root / '.validation').mkdir(exist_ok=True)
out = root / '.validation' / ('start-api-' + time.strftime('%Y%m%d-%H%M%S'))
out.mkdir(exist_ok=False)
data = out / 'generated'; data.mkdir()
ds = gdal.GetDriverByName('GTiff').Create(str(data / 'constant.tif'),1024,768,1,gdal.GDT_Byte)
ds.GetRasterBand(1).Fill(42); ds = None
og = runpy.run_path(str(root / 'overview-generator.py'))
app = QCoreApplication([])
result = dict(mode='QCoreApplication with actual PyQGIS, GDAL, and QTimer; no GUI widget', ticks=0, tests=[])
main_id = threading.get_ident()
printed_threads = set()
capture = io.StringIO()
original = sys.stdout
class Capture:
    def write(self, text):
        printed_threads.add(threading.get_ident()); capture.write(text)
    def flush(self): pass
sys.stdout = Capture()
job = None
stage = 'build'
start = time.monotonic()
def finish(error=None):
    timer.stop()
    if error: result['error'] = error
    result['passed'] = error is None
    result['printed_on_main_thread_only'] = printed_threads == {main_id}
    (out/'results.json').write_text(json.dumps(result,indent=2))
    (out/'console-output.txt').write_text(capture.getvalue())
    sys.stdout = original
    app.quit()
def tick():
    global stage, job
    try:
        result['ticks'] += 1
        if time.monotonic()-start > 30: raise TimeoutError('API job timed out')
        if not job.done or job._timer is not None: return
        if stage == 'build':
            assert job.status()['counts'] == {'ok':1}, job.status()
            assert job not in sys._overview_generator_live_jobs
            assert 'finished |' in capture.getvalue() and 'Summary:' in capture.getvalue()
            assert printed_threads == {main_id}
            ds = gdal.Open(str(data/'constant.tif'))
            b = ds.GetRasterBand(1)
            assert b.GetOverviewCount() == 2
            for i in range(2): assert (b.GetOverview(i).ReadAsArray() == 42).all()
            b = ds = None
            result['tests'].append(dict(test='start build, dimensions/pixels, queued print, timer cleanup',outcome='PASS',status=job.status()))
            stage = 'cancel'
            job = og['start'](str(data),workers=2,threads_per_file=1)
            job.cancel()
        else:
            assert job.status()['phase'] == 'cancelled'
            result['tests'].append(dict(test='start cancellation and cleanup',outcome='PASS',status=job.status()))
            finish()
    except Exception: finish(traceback.format_exc())
errors=[]
def wrong_thread():
    try: og['start'](str(data))
    except RuntimeError as exc: errors.append(str(exc))
t=threading.Thread(target=wrong_thread);t.start();t.join()
assert len(errors)==1 and 'GUI thread' in errors[0]
result['tests'].append(dict(test='wrong-thread start rejection',outcome='PASS',error=errors[0]))
t0=time.monotonic()
job=og['start'](str(data),workers=2,threads_per_file=1,progress_interval=.5)
result['start_return_seconds']=time.monotonic()-t0
timer=QTimer(app);timer.setInterval(25);timer.timeout.connect(tick);timer.start()
app.exec()
