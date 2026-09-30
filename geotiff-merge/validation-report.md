# GeoTIFF merge validation — 2026-09-30

The current GDAL implementation passed **35 generated-data tests** and two
medium-size runs on real Sentinel-derived imagery using **QGIS 4.2's bundled
Python 3.12.13 and GDAL 3.13.1 on Windows**. No data was downloaded for these tests.

Tested script SHA-256: `d72af68e93c920d757ff83d0f81f6ce7f854a4e7bfdc8564db331cb010713ab2`.

## Real Sentinel data

The retained Sentinel-2 RGB benchmark reference was accessed through a read-only
volume mount and copied into an isolated test workspace. The copy matched the
recorded source SHA-256:
`bcd899aef8ba89374f88ced1c559a16789e07fa334f2e95f68ca28b6f6e18419`.

- Reference: 32,768 × 52,224 pixels, three Byte RGB bands, 10 m EPSG:32636.
- Original reference container: 6,856,300,738 bytes, including its overviews.
- Test input: 16 lossless, aligned windows covering that reference, plus a
  512 × 512 duplicate window crossing four tile boundaries. These are derived
  windows of real imagery, not synthetic image values or 17 original scene files.
- Flat input directory: **17 GeoTIFFs, 5,134,938,324 bytes** (5.135 GB).
- Tuple nodata `NODATA_VALUES=0 0 0` was preserved correctly, including valid
  pixels with individual zero-valued color channels. A regression test covers it.
- Target: **1 GiB** (1,073,741,824 bytes); accepted maximum: **1,181,116,006 bytes**.

## Measured runs

| Setting | File workers | Shared codec threads | RAM budget | Merge + internal verification | Peak worker working set |
| --- | ---: | ---: | ---: | ---: | ---: |
| Automatic: 32 logical CPUs available | 3 | 29 | 26.54 GiB | 332.11 s | 4.65 GiB |
| Explicit `--cpus 8 --ram 2GiB` | 2 | 6 | 2.00 GiB | 103.08 s | 0.79 GiB |

Both runs produced these four outputs, totaling **3,198,307,021 bytes**:

| File | Bytes |
| --- | ---: |
| `mosaic-00001.tif` | 694,466,244 |
| `mosaic-00002.tif` | 562,196,426 |
| `mosaic-00003.tif` | 969,151,112 |
| `mosaic-00004.tif` | 972,493,239 |

The initial planner proposed three regions. One closed candidate measured
1,256,661,742 bytes, above the permitted 10% allowance, so the script split that
region and verified its replacements. Every accepted TIFF met the limit.
A separate generated test confirms that a file 5% above target is accepted.

These are observed runs, not a controlled scaling benchmark. The limited run
followed the automatic run, with warmer filesystem caches and potentially
different background load. The times exclude preparation and the additional
independent reference comparison. Working set was sampled through Windows'
process counters and excludes the parent process and OS filesystem cache.
`--ram` is a planning budget, not an OS-enforced memory ceiling.

## Preservation checks

Both configurations passed all of the following:

- Every required valid source sample matched its output bit for bit, including
  identical overlaps: **5,134,614,378 source band samples** checked per run.
- A separate verifier compared each final output directly against the **unsplit
  reference**, rather than merely comparing the merge against its own input tiles:
  **1,711,276,032 pixels**, **5,133,827,946 valid band samples**, and **150 invalid
  band samples** (50 nodata pixels) matched.
- Output validity masks, spatial coverage, CRS, grid, types, and supported band
  metadata passed. No output rectangles overlapped or left source coverage missing.
- All input/dependency SHA-256 hashes were unchanged before/after merging.
  The unsplit reference hash was also unchanged after the independent comparison.
- Four pairwise footprint overlaps were found, with zero conflicting valid samples.

## QGIS execution coverage

The public `start()` API launched the same bundled Python in a child process.
It returned in 0.016 s / 0.016 s. The real Qt event loop
continued for 1,330 / 416 heartbeat ticks during the two runs.
Parent GDAL cache and exception settings were unchanged; progress timers completed.

**Full QGIS desktop/console-widget validation is not claimed.** Windows
Application Control blocked importing `qgis._core`. QGIS's GDAL/NumPy and
`qgis.PyQt.QtCore` were available, so bundled-Python execution, the asynchronous
API, progress/cancellation, and the Qt event loop were tested. No security policy
or installed QGIS files were changed.

## Generated regression suite and evidence

Command in the QGIS Python environment:
`python -m unittest discover -s geotiff-merge/tests -v` — **35/35 passed**.

Coverage includes gaps, negative offsets, valid zeros, scalar and tuple nodata,
float bit patterns, identical/conflicting/three-way overlaps, explicit source
priority, masks, band metadata, size estimation and actual-size retries, parallel
outputs, CPU/RAM allocation, async completion/cancellation, and rejection of
incompatible inputs. Deliberate corruption of pixels, masks, georeferencing,
coverage and source files was detected. Compilation and `git diff --check` passed.

The scripts under `tests/` reproduce native launch, fixture preparation,
telemetry, and the independent comparison. Raw reports, logs, inputs and output
TIFFs are retained in the local validation workspace and are excluded from Git.
The earlier rejected tuple-nodata run is retained there as failure evidence.
CI configuration uses system GDAL/NumPy. The measurements above are local;
remote CI results are recorded separately in GitHub Actions for each commit/tag.
No 100 GB run, power-loss recovery, or disk-full recovery is claimed.
