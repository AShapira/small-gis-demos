# Native Windows validation

These tests create synthetic TIFFs beneath `overview-generator/.validation/`.
They never select a production raster folder. They intentionally replace or
cancel generated overview files. Allow about 300 MiB for one CLI run.

From a Windows-native clone, choose the installed QGIS directory explicitly:

```powershell
.\overview-generator\tests\run-windows.ps1 -QgisRoot 'C:\Program Files\QGIS 4.2.0' -Mode cli
.\overview-generator\tests\run-windows.ps1 -QgisRoot 'C:\Program Files\QGIS 4.2.0' -Mode api
.\overview-generator\tests\run-windows.ps1 -QgisRoot 'C:\Program Files\QGIS 4.2.0' -Mode console
```

The launcher loads that installation's `bin/qgis-bin.env`, uses its actual
Python executable, checks for a matching active test, and launches a detached
Windows process. It prints the PID and paths to timestamped logs. No installer,
security-policy changes, accounts or network access are needed for the tests.
The launcher was tested with QGIS 4.2; other packaging layouts may need an
adapted environment setup. QGIS 3.28 remains untested.

- `cli`: 12 checks using actual script subprocesses and GDAL. Read the path in
  `.validation/latest-cli.txt`, then `results.json` and `complete.json` there.
  Cancellation sends native Ctrl+C only to a newly created test console.
- `api`: real PyQGIS and Qt event-loop tests, including wrong-thread rejection.
  Results are in `.validation/start-api-<timestamp>/`.
- `console`: a standalone `QgsApplication` hosting the real QGIS console widget,
  not the full QGIS desktop. Read `.validation/latest-gui.txt` and that run's
  `results.json`, `complete.json`, `console.txt` and widget screenshot.

Each run creates a new timestamped directory. Check completion and existing
processes before repeating a run. The launcher returns before tests finish;
process launch alone is not evidence of a pass. A failed check contains its
traceback in `results.json`. Data and logs remain local and are ignored by Git.

The ordinary matrix uses direct-GDAL references and independent plane/NoData
assertions. For fractional odd-size reductions the reference also uses external
overviews: GDAL's internal and external paths can produce slightly different
Float32 pixels. See the [validation report](../validation-report.md).
