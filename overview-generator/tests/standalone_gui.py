"""Fallback: real QgsApplication + QGIS console widget, without qgis-bin desktop."""
import os, runpy, faulthandler
from pathlib import Path
root = Path(__file__).resolve().parents[1]
(root / '.validation').mkdir(exist_ok=True)
trace = (root / '.validation' / 'standalone-stack.txt').open('w')
faulthandler.enable(trace)
faulthandler.dump_traceback_later(20, repeat=False, file=trace)
from qgis.core import QgsApplication
print('Imported QgsApplication', flush=True)
app = QgsApplication([], True, str(root / '.validation' / 'standalone-profile'))
print('Created QgsApplication', flush=True)
app.setApplicationName('Overview validation isolated PyQGIS')
app.initQgis()
print('Initialized QGIS', flush=True)
os.environ['OVERVIEW_VALIDATION_ROOT'] = str(root)
os.environ['OVERVIEW_GUI_MODE'] = 'standalone QgsApplication with real QGIS console widget'
scope = runpy.run_path(str(root / 'tests' / 'gui_validation.py'))
print('Starting event loop', flush=True)
faulthandler.cancel_dump_traceback_later()
app.exec()
trace.close()
