# Changelog

## Unreleased

- Estimate NASADEM storage from compressed source sizes, existing overviews,
  compressed NoData coverage, and temporary COG work instead of three raw copies.
  Warn and continue by default; make stopping optional with `--disk-space-policy error`.

- Consolidate the GeoTIFF compression benchmark under `geotiff-compression/`,
  including its package, configs, scripts, tests, documentation and evidence.
  Update CI, installation instructions and cross-demo references.

- Add opt-in COG merge output (`--cog` / `cog=True`, default false), with
  nearest, average, mode or no overviews. Preserve and exhaustively verify
  full-resolution sample bits, masks and core metadata against the original inputs.
- Validate finished COG tile structure, enforce size targets after conversion
  including overviews, and retain CPU/RAM controls and cooperative cancellation.
  Document options, temporary storage costs and COG codec compatibility.

## 0.19.0 — 2026-11-11

- Remove the Cesium WMS gutter comparison, both Kartoza affine-fix bundles,
  and supporting affine research code, patches, tests, evidence and CI jobs.
  Development now lives in [AShapira/geoserver-affine-fix](https://github.com/AShapira/geoserver-affine-fix);
  published tags and historical release contents remain unchanged.
- Update the demo index, licensing notices and relocation documentation;
  retain local ignored evidence, caches and generated archives.

## 0.18.0 — 2026-11-11

- Rename the tool to DEM COG Builder, with directory `dem-cog/` and standalone
  entry point `create_dem_cogs.py`. Update documentation, test imports, launchers
  and CI while preserving existing output names, metadata and resume formats.

- Extend DEM COG Builder with recursive `--dted-dir` input and explicit
  `--dted-level {1,2}` selection. Normalize latitude-zone longitude spacing by
  nearest-neighbor duplication while preserving original elevation posts;
  reject conflicting originals and prefer finer grids for duplicated samples.
  Reuse COG blocks, exact statistics, overviews, verification, resume and stage
  VRTs, with conservative storage estimates including per-worker staging.
- Pass 99 native Windows QGIS/GDAL tests, including 18 DTED contracts for
  latitude-zone grids, original-sample seams, NoData, verification and resume.

## 0.17.0 — 2026-11-04

- Default NASADEM/SRTM COG overviews to nearest-neighbor in VRT and consolidation
  modes. Keep `--overview-resampling average` available for averaged terrain
  overviews; full-resolution values, NoData, types, and statistics are unchanged.
- Distinguish overview methods in resume state. Explicit average mode can resume
  older runs; the new default refuses to mix nearest and legacy average outputs.
- Explain the Float32 storage tradeoff: a 260-tile SRTM sample used 4.363 GB with
  average overviews versus 2.975 GB with nearest, with identical base pixels.
- Accept the provider SRTM VRT's `OverviewList` declaration without reusing its
  preview overviews. Name unsupported dataset XML elements in preflight errors
  while retaining mask/transformation checks.

## 0.16.0 — 2026-11-02

- Embed exact full-resolution minimum, maximum, mean, population standard
  deviation, valid/NoData counts, and valid percentage in newly generated
  NASADEM/SRTM COGs. Preserve completed legacy outputs on resume and verify
  new statistics without modifying finished COGs or requiring sidecars.
- Pass 76 native QGIS tests and verify statistics against an independent
  GDAL calculation on a real 51,854,401-pixel Float32 COG.

## 0.15.0 — 2026-11-02

- Consolidate previous NASADEM/SRTM TIFF results with `--merge-results-dir`
  into one BigTIFF COG with fresh internal average overviews, exact overlap
  checks, native input bounds, and resumable verification.
- Publish portable mosaic VRTs for completed multi-COG stages; omit them for
  single-COG outputs and regenerate missing managed VRTs on resume.
- Pass 66 native QGIS tests and verify all 51,854,401 pixels in a retained
  Float32 COG, including unchanged inputs and successful resume.

## 0.14.0 — 2026-10-27

- Support Float32 SRTM VRTs in the NASADEM COG merger, preserving fractional
  elevations and NoData with floating-point compression and exact pixel checks.
- Add `--output-prefix` while retaining existing Int16/default-prefix resume
  compatibility. Document WGS84 ellipsoidal SRTM inputs and vertical-datum handling.
- Pass 50 native QGIS tests and verify all 51,854,401 pixels in a real two-tile COG.

## 0.12.0 — 2026-10-21

- Accept every positive whole-degree divisor of 360 for NASADEM COG blocks.
  Use symmetric polar clipping and create one whole-world COG with size 360.
- Preserve existing 5°/10°/20° boundaries, filenames, and resume compatibility.
  Document the grid layout, valid sizes, and native global raster dimensions.
- Pass 38 native QGIS tests, dry-run the existing 160-tile subset at seven
  larger sizes, and verify unchanged resume of 12 outputs from the prior version.

## 0.11.0 — 2026-10-20

- Add a standalone QGIS/OSGeo4W-shell NASADEM merger producing 5°, 10°, or
  20° COG blocks from a local VRT and height tiles. Preserve shared endpoint
  samples, source ordering, masks and NoData without resampling base elevations.
- Include bounded parallelism, storage reservations, resumable checkpoints,
  cooperative cancellation, optional internal overviews and exhaustive
  independent pixel verification.
- Add 33 regression tests and a public-data download helper. Validate two
  adjacent 10° blocks from 160 real tiles, comparing all 2,592,144,002 base
  pixels and shared edges, then reverify and resume without rewriting outputs.

## 0.9.0 — 2026-10-20

- Add a dedicated Kartoza GeoServer 2.28.5 affine-raster patch bundle with
  replacement GeoTools 34.5 / ImageN 0.9.1 JARs, startup installation and rollback,
  exact image/library checksums, reproducible compilation, sources and licenses.
  Deployment uses the stock image without building or committing a custom image.
- Retain direct WMS and GWC evidence across 14 unchanged rotated/sheared TIFFs:
  the patch removes large triangles and boundary exceptions; a one-pixel gutter
  eliminates missing interior pixels in the tested matrix, with 16 isolated
  footprint-edge omissions per path. Document gutter-0 limits and client cropping.
- Validate startup, restart, recreation and rollback; add CI checks for bundle
  integrity and seven startup-script regression cases.

## 0.7.0 — 2026-10-10

- Add a standalone offline OSM basemap pipeline with configurable country
  extracts, regional vector MBTiles, a worldwide Natural Earth GeoPackage,
  and stock Kartoza GeoServer 3.0.1 WMS/WMTS serving.
- Provide five recognizable styles in local and English variants, an offline
  viewer, and style refresh without regenerating data. English labels use
  explicit translations; untranslated POI icons use collision handling,
  wider spacing and lower priority to limit clutter.
- Include image/data export, Docker/Podman Compose and private GCP deployment
  guidance, eight regression tests, browser and collision acceptance checks,
  and measured Czechia/Slovakia validation including isolated offline startup.

## 0.6.0 — 2026-10-10

- Add a dedicated Cesium direct-WMS demo with independent gutter selectors
  for 0, 20, 64, 128 and 256 pixels, a split map, exact tile previews and a
  repeatable browser benchmark for all five sizes.
- Preserve nearest-neighbour sampling with expanded WMS requests and a 1:1
  centre crop. Retain measured performance, visual comparisons and the limits
  of the gutter-128 workaround, including footprint-edge request failures.
- Include the deterministic rotated-GeoTIFF study, independent affine oracle,
  WMS/GWC test runner and isolated experimental GeoTools/ImageN patches.
  Add affine-oracle and Cesium adapter checks to CI.

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
