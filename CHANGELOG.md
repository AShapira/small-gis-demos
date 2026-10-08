# Changelog

## 0.5.0 — 2026-10-08

- Add a standalone OSGeo4W/QGIS-shell utility for combining recursive folders
  of aligned 8-bit RGB or grayscale GeoTIFFs into one VRT with JPEG overviews.
- Preserve source files and transparency with explicit virtual validity masks
  and a separate lossless overview mask pyramid. Include bounded GDAL resources,
  dry runs, source manifests, validation reports, and cooperative cancellation.
- Validate 20 generated-data tests with Windows QGIS GDAL, including 2,000 tiles,
  NoData and overlap behavior, native CLI cancellation, and failure cleanup.
  Add the suite to Linux CI and document GeoServer reader compatibility limits.

## 0.4.0 — 2026-09-30

- Extend explicit lossless compression selection to DEFLATE, LZW, ZSTD, LZMA,
  PackBits and uncompressed TIFF, with codec levels and reversible predictors.
  Probe runtime support, account for codec memory, estimate sizes using the
  selected codec, and verify the actual output compression.
- Document all codec choices and measured tradeoffs from the retained Sentinel
  compression benchmark, with a sanitized numerical evidence extract.
- Add `--dry-run --suggest-nodata` to find values unused by valid source samples,
  including exact searches for holes inside integer/floating-point value ranges.
- Add `--output-nodata` and matching QGIS API options to encode missing pixels
  with scalar NoData instead of a stored mask. Recheck collisions before writing
  and verify all valid values and validity locations without changing data types.
- Validate 64 generated-data tests and medium Sentinel merges with independent
  full-reference comparisons for scalar NoData and ZSTD compression.

## 0.3.0 — 2026-09-30

- Make the GeoTIFF merge size target optional: omitting it produces one verified
  BigTIFF covering all input footprints. Explicit targets retain the 10% allowance.
- Add configurable output base names, elapsed time and rough total/remaining
  duration in the shell and QGIS console, and a complete parameter reference.
- Expose processing-window and GDAL-cache controls in the QGIS `start()` API.
- Validate 41 generated-data tests and one 5.135 GB Sentinel input run producing
  a single 3.198 GB TIFF, with an independent full-reference comparison.

## 0.2.0 — 2026-09-30

- Add a standalone GeoTIFF merger for QGIS 4.2 with lossless compression,
  target output sizes allowing up to 10% extra, and splitting based on actual
  file sizes. Use available CPU/RAM with optional resource budgets.
- Verify every valid source sample, validity masks, spatial coverage and core
  metadata; hash sources before and after merging. Stop on conflicting overlaps
  by default and report options, with explicit first/last priority available.
- Provide an asynchronous QGIS Python-console API, usage documentation,
  35 generated-data regression tests and a GDAL/NumPy CI job.
- Record two successful Windows QGIS-bundled-Python runs on 5.135 GB of
  Sentinel-derived input tiles, including independent full-reference comparisons.
  Full desktop validation remains blocked by Windows Application Control;
  RAM limits are planning budgets, not OS-enforced ceilings.

## 0.1.0 — 2026-09-24

- Publish the existing GeoServer raster compression and WMS/WMTS benchmark,
  including completed-study limitations, reproducible dependency pins,
  bounded preseed retries and endurance completion/report tools.
- Add the standalone GDAL overview generator under `overview-generator/`,
  with Windows CLI, PyQGIS console and Qt API validation scripts and results.
- Name the repository and Python distribution `small-gis-demos`; retain the
  benchmark's `rasterbench` package and command.
- Replace machine-specific defaults and examples with configurable paths;
  exclude local operational records and raw validation artifacts from Git.

The overview-generator implementation retains its supplied internal version
1.0.0. The repository tag 0.1.0 versions the collection, not that script alone.
