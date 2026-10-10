# GeoTIFF merge validation — 2026-09-30

## Opt-in COG update

COG output defaults to false. The updated script passed **75 generated-data
tests** in QGIS 4.2's Windows Python 3.12.13 / GDAL 3.13.1. Tests exercise
ordinary TIFF output without the optional validator, all five offered COG codecs,
nearest/average/mode/no overviews, full-resolution integer and floating-point
sample bits, masks, scalar and RGB tuple NoData, band metadata, asynchronous
API forwarding, final-size splitting after conversion, callback cancellation,
unsupported PackBits, layout-validation failure and deliberate data corruption.

Tested script SHA-256: `40be3f885cbcd9f336fb4019cd77c076116430697143b33d2a3e1cc5d6c07259`.

A medium run reused **17 Sentinel-derived rasters totaling 5,134,938,324
bytes**. It used COG output with nearest-neighbour overviews, ZSTD level 3,
predictor 2, scalar NoData 0, a 1 GiB target, 8 CPUs and a 2 GiB RAM budget.
The four resulting COGs were:

| File | Bytes including overviews | Internal overview levels |
| --- | ---: | ---: |
| `mosaic-00001.tif` | 1,099,731,663 | 7 |
| `mosaic-00002.tif` | 1,022,979,001 | 7 |
| `mosaic-00003.tif` | 870,549,166 | 7 |
| `mosaic-00004.tif` | 928,431,042 | 7 |

Total: **3,921,690,872 bytes**. All outputs passed GDAL's
COG validator with `full_check=True` and no warnings. Each was below the
110% cap of 1,181,116,006 bytes. Each retained three data bands and
scalar NoData without an added alpha band or stored mask.

The merge and its internal verification took **92.329 seconds**.
All 5,134,614,378 required valid source band samples passed;
source hashes were unchanged. Independent verification against the unsplit
reference then passed for **1,711,276,032 pixels**,
**5,133,827,946 valid band samples**, and **150 invalid band samples**.
The independent check also reran full COG layout validation, and the reference
hash remained unchanged. These timings exclude the independent checks and are
validation observations, not a controlled performance comparison.

The Qt event loop recorded 371 heartbeat ticks and 25 timing updates;
parent GDAL settings were unchanged. `qgis.core` imported successfully in this
run. This validates bundled Python, the asynchronous API and a real Qt event
loop; the full QGIS desktop console window was not exercised. Earlier import
restrictions below describe the earlier runs, not this run.

Raw evidence is retained locally as `validation-cog-zstd3.json`, the output
`report.json`, and `qgis42-cog-tests.log`, outside Git. The retained harness now
accepts `--cog` and `--cog-overviews`. Python 3.8 syntax, parameter documentation
coverage, and `git diff --check` also passed.

## Explicit lossless compression update

The script passed **64 generated-data tests** in QGIS 4.2's Windows Python
3.12.13 / GDAL 3.13.1. The installed GTiff driver advertised all six offered
codecs: DEFLATE, LZW, ZSTD, LZMA, PackBits and NONE. Each passed actual-codec,
size-cap, sample and mask checks. Tests also cover compression levels, reversible
predictors, exact floating-point bits (signed zero, infinities and NaN payloads),
codec memory accounting, invalid combinations, a simulated unavailable codec,
deliberately incorrect output compression, and asynchronous QGIS API forwarding.

Tested script SHA-256: `353ac07bfdb399c823d4ea20ccc2727b70bf9fc813180eb6ecd7a2832f89e3d5`.

A medium real-data run reused the **17 Sentinel-derived inputs totaling
5,134,938,324 bytes** with ZSTD level 3, predictor 2, scalar NoData 0, a 1 GiB
target, 8 CPUs and a 2 GiB RAM budget. It produced three verified TIFFs:

| File | Bytes |
| --- | ---: |
| `mosaic-00001.tif` | 1,082,662,775 |
| `mosaic-00002.tif` | 895,006,330 |
| `mosaic-00003.tif` | 874,356,041 |

Total: **2,852,025,146 bytes**. Every file was below the 110% cap of 1,181,116,006
bytes; the first was slightly above the nominal target and correctly accepted.
Resolved creation options were `COMPRESS=ZSTD`, `ZSTD_LEVEL=3`, `PREDICTOR=2`;
the closed outputs' compression/predictor and scalar-NoData mask flags passed.
Merge plus internal verification took **70.218 seconds**. All required source
samples passed and input hashes were unchanged.

An additional independent comparison against the unsplit reference passed for
**1,711,276,032 pixels**, **5,133,827,946 valid band samples**, and **150 invalid
band samples**. The reference hash was unchanged. The Qt loop recorded 284
heartbeat ticks and 21 timing updates; parent GDAL settings were unchanged.
Full desktop validation remains blocked by Application Control as below.
This is an observed validation run, not a controlled codec-speed comparison;
timing excludes the independent reference check. The README's codec tradeoffs
instead use the retained historical compression study and its documented scope.

Raw evidence is retained locally as `validation-zstd3.json` and excluded from
Git. The sanitized historical metrics are in `compression-benchmark.json`.
Compilation, parameter-documentation and benchmark-number consistency checks,
and `git diff --check` passed. Remote CI results are recorded separately in
GitHub Actions for each commit/tag.

## NoData suggestions and replacement update

The updated script passed **56 generated-data tests** in QGIS 4.2's Windows
Python 3.12.13 / GDAL 3.13.1. Coverage includes full Byte domains with no unused
value, unused values inside UInt8/UInt16 ranges, Int32/Float32/Float64 fallback
searches, valid NaNs and signed zeros, invalid/out-of-range values, collisions
after a previous dry run, existing scalar/tuple NoData, explicit masks, and the
QGIS asynchronous API. Deliberately adding a stored mask to a scalar-NoData output
was detected by verification. All previous tests also passed.

Tested script SHA-256: `9d25206ae3663920cc2c75c334bdade72cac3195562cdadde900cb8d974cccf7`.

The dry run reused the same **17 Sentinel-derived inputs, 5,134,938,324 bytes**.
`--dry-run --suggest-nodata --cpus 8 --ram 2GiB` checked **5,134,614,378 valid
source band samples** and **150 invalid source band samples**. It found that
`0` is unused by valid samples across all three bands, completed in **16.656 s**,
wrote no TIFFs, and confirmed unchanged input hashes. This suggestion is specific
to this dataset; generated tests separately cover valid zero-valued channels.

A subsequent single-file merge with `--output-nodata=0 --cpus 8 --ram 2GiB`
rescanned for collisions and produced **`sentinel-nodata-00001.tif`**, **3,197,180,843
bytes**, in **114.391 s** including internal verification. Every band used scalar
NoData with `GMF_NODATA`, with no stored mask. All required source samples passed
verification and source hashes remained unchanged. A separate full comparison
against the unsplit reference passed for **1,711,276,032 pixels**, **5,133,827,946
valid band samples**, and **150 invalid band samples**; the reference hash was
unchanged. The comparison is additional work excluded from the merge time.

The real Qt event loop recorded **460 heartbeat ticks** and **29 rough timing
updates**; the parent GDAL cache and exception settings were unchanged. Full
desktop validation remains blocked by Application Control as described below.
Raw dry-run reports and `validation-scalar-nodata.json` are retained locally and
excluded from Git. Compilation and `git diff --check` passed. These results are
from local validation; remote CI has not been run for this update.

## Version 0.3.0: optional size, naming and timing

The updated script passed **41 generated-data tests** in QGIS 4.2's Windows
Python 3.12.13 / GDAL 3.13.1. New coverage includes omitted size targets, custom
names for single and split outputs, filename validation, CLI/API defaults,
timing estimates, retry work, and five-second updates during a long write.

Tested script SHA-256: `ba1db96b8662593c696622f4dd5b32fe1d15ff1e4c249c4504debb1db00b7155`.

A further real Sentinel test reused the same 17 inputs (5,134,938,324 bytes)
described below. With no size target, `base_name="sentinel"`, `cpus=8`, and
`ram="2GiB"`, it produced exactly one **`sentinel-00001.tif`** of **3,198,304,237
bytes**. Merge plus exhaustive internal verification took **112.734 seconds**.
All **5,134,614,378** required source band samples passed; input hashes were
unchanged. A separate full comparison against the unsplit reference passed for
**1,711,276,032 pixels**, **5,133,827,946 valid band samples**, and **150 invalid
band samples**. The reference hash remained unchanged.

The Qt event loop recorded **456 heartbeat ticks** and relayed **28 rough
duration updates**. Parent GDAL cache and exception mode were unchanged.
The full desktop import remains blocked as described below; this validates
bundled-Python execution and the Qt event loop, not the desktop console widget.
The reported merge time excludes the independent reference comparison.
Raw evidence is retained locally as `validation-single-options.json`; it is
excluded from Git. Compilation and `git diff --check` also passed. Remote CI
results are recorded separately in GitHub Actions for each commit/tag.

## Version 0.2.0 validation

The version 0.2.0 GDAL implementation passed **35 generated-data tests** and two
medium-size runs on real Sentinel-derived imagery using **QGIS 4.2's bundled
Python 3.12.13 and GDAL 3.13.1 on Windows**. No data was downloaded for these tests.

Medium-run script SHA-256: `d72af68e93c920d757ff83d0f81f6ce7f854a4e7bfdc8564db331cb010713ab2`.

The release script replaces `GetDataTypeSizeBytes(type)` with the equivalent
`GetDataTypeSize(type) // 8` for compatibility with Ubuntu's older GDAL.
That is the only production-code change after the medium runs. All 35 tests
passed again in QGIS 4.2 Python after this change; the medium runs were not repeated.
Release script SHA-256: `13cea2b4065c521a0ba9381cf339ca478c82c7ec00258f2160707ef64f1021d3`.

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
