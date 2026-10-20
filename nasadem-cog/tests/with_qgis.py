"""Validation launcher for QGIS's Windows python.exe from an unconfigured shell.

python.exe tests/with_qgis.py test [unittest arguments]
python.exe tests/with_qgis.py cli [merger arguments]
"""
import os
from pathlib import Path
import runpy
import sys
import unittest

QGIS_ROOT = Path(sys.executable).parents[2]
DLL_HANDLES = [os.add_dll_directory(str(QGIS_ROOT / relative))
               for relative in ("bin", "apps/qgis/bin")]
os.environ["PROJ_DATA"] = str(QGIS_ROOT / "share/proj")
os.environ["GDAL_DATA"] = str(QGIS_ROOT / "share/gdal")

HERE = Path(__file__).resolve().parent
mode = sys.argv.pop(1)
if mode == "test":
    sys.path.insert(0, str(HERE))
    if len(sys.argv) > 1:
        unittest.main(module=None)
    else:
        suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        sys.exit(not result.wasSuccessful())
elif mode == "cli":
    sys.argv[0] = str(HERE.parent / "create_nasadem_cogs.py")
    runpy.run_path(sys.argv[0], run_name="__main__")
else:
    raise SystemExit("Expected test or cli")
