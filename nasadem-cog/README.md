# NASADEM COG merger

Create 5°, 10°, or 20° Cloud Optimized GeoTIFF blocks from an existing local
NASADEM VRT and its 1° height tiles. Run the single file
`create_nasadem_cogs.py` in the **QGIS/OSGeo4W command shell on Windows**.
It uses QGIS's GDAL, NumPy, and GDAL COG validator; no plugin or pip installation
is required. Python 3.9+ and GDAL 3.6+ are required. This is a command-line tool,
not a QGIS Python Console job.

## Quick start

Keep the downloaded directory structure intact: the public
[NASADEM VRT](https://opentopography.s3.sdsc.edu/raster/NASADEM/NASADEM_be.vrt)
references files inside `NASADEM_be/`. Other local VRT filenames are accepted.
From the QGIS/OSGeo4W shell, check the inputs and output plan first:

```cmd
python create_nasadem_cogs.py ^
  --vrt-file "D:\Maps\NASADEM\NASADEM_be.vrt" ^
  --output-dir "D:\Maps\NASADEM\COG_10deg" ^
  --block-size 10 ^
  --dry-run
```

Then generate COGs with exhaustive verification and internal overviews:

```cmd
python create_nasadem_cogs.py ^
  --vrt-file "D:\Maps\NASADEM\NASADEM_be.vrt" ^
  --output-dir "D:\Maps\NASADEM\COG_10deg" ^
  --block-size 10 ^
  --workers 8 ^
  --verify ^
  --build-overviews
```

Use `python-qgis` in place of `python` if that is the Python launcher provided by
your installation. Confirm `python -c "from osgeo import gdal; import numpy"`
works in that shell. Do not install a second GDAL into QGIS with pip.
Development validation uses Windows QGIS 4.2 / Python 3.12 / GDAL 3.13.
The CI tests also exercise the system GDAL supplied by Ubuntu.

## Parameters

| Option | Default | Meaning |
| --- | --- | --- |
| `--vrt-file` | Required | Local VRT with local TIFF references. Relative paths resolve against the VRT directory. Absolute and UNC paths are supported. |
| `--output-dir` | Required | New/empty directory, or the directory of a compatible resumable run. May be a subdirectory of the source collection. |
| `--block-size {5,10,20}` | `10` | Nominal geographic block size in degrees. |
| `--workers` | `8` | Maximum concurrent blocks. Each GDAL conversion uses one compression thread. |
| `--verify` | Off | Check every full-resolution value and NoData flag against original TIFFs, plus full COG structure. Applies to existing outputs on resume too. |
| `--build-overviews` | Off | Build fresh internal average overviews during COG conversion. |
| `--min-tiles-per-block` | `1` | Minimum distinct available nominal 1° source tiles belonging to a block. Boundary-only neighbors do not inflate the count. |
| `--filename-filter` | None | Case-insensitive substring of the basename, including extension. For example `"n32e"` selects northern 32° tiles in eastern longitudes. |
| `--dry-run` | Off | Print source counts, missing files, exact blocks/dimensions, tile counts, and storage reservations; create no outputs or logs. |

Filtering applies **before** block assignment and seam composition. Only selected
sources can supply pixels, including boundary samples. A filter with no usable
matches is an error. There is no fixed expected global file count: the actual
VRT, available files, filter, and minimum count determine it.

## Grid, seams, and missing coverage

The tool accepts ordinary single-band Int16 NASADEM height VRTs in WGS 84
(EPSG:4326), with matching north-up square pixels, explicit matching NoData,
and 1° TIFFs containing both endpoint samples. At native one-arcsecond spacing,
each source has **3,601 × 3,601** samples and each 10° output has
**36,001 × 36,001** samples. Resolution is read from the input, not resampled.
Rotated, warped, derived, scaled, remote, and incompatible inputs are rejected.

Blocks use degree multiples, with mathematical floor for negative coordinates.
Neighboring outputs share one sample row/column. This preserves the elevation
sample coordinates; the pixel footprint extends half a pixel past the nominal
degree boundary. Both outputs use the same selected contributing sources at
their shared edge, in original VRT order. For `ComplexSource` entries, later
valid values override earlier values; declared source NoData or `UseMaskBand`
is honored. As in GDAL, explicit source `NODATA` takes precedence if both
are present. `SimpleSource` entries retain their overwrite semantics.
Base pixels are never averaged or reprojected.

`NASADEM_N00E000_N09E009.tif` names the nominal source tile origins 0..9:
its final endpoint samples are at 10° north/east. Similarly,
`NASADEM_S10W020_S01W011.tif` spans nominal coordinates -20..-10 longitude
and -10..0 latitude, including the boundary samples.

Missing referenced TIFFs are listed in `state.json`; uncovered areas remain
NoData. They do not produce an error exit when other selected inputs are usable.
Blocks below the minimum are reported without producing TIFFs. A present but
corrupt/incompatible TIFF is an error, not an absent tile. Geographic gaps,
including ocean areas without source coverage, also remain NoData.

## COGs, verification, and resources

Outputs use DEFLATE level 6, integer predictor 2 (`PREDICTOR=STANDARD`),
512 × 512 tiles, and `BIGTIFF=IF_SAFER`. BigTIFF selection is GDAL's heuristic.
Overviews are explicitly disabled unless requested. When enabled, average
overviews are created at powers of two until the largest dimension is at most
512. GDAL's COG generator may round odd overview dimensions down rather than
up; the recorded dimensions reflect the installed driver. Existing source
overviews are not copied. Never add overviews to or edit a
completed COG in place; regenerate it in another output directory.

Every candidate is closed and reopened to check dimensions, georeferencing,
CRS, data type, NoData, selected metadata, compression, tile size, overview
dimensions, and basic COG structure before publication. `--verify` additionally
uses the GDAL validator's `full_check=True` and independently composes every
base-resolution window from original TIFF values/georeferencing and source
precedence. Values and validity must match exactly, including gaps and seams.
This is not a comparison against the temporary block VRT. Overview pixels are
derived average values, outside the base-sample equality guarantee; their
generation is covered by tests.

Workers own their dataset handles. Verification uses 512-pixel windows and
bounded source-reader caches; whole blocks are not loaded into Python memory.
The process uses a shared 512 MiB GDAL cache, a 64-entry VRT dataset pool, and
one GDAL compression thread per worker. These are planning limits, not a hard
operating-system RAM ceiling.

Storage reservation is three times the uncompressed base plus requested
overview pixels (two bytes per Int16 sample), with 1 GiB free headroom.
Scheduling reduces concurrency when free space is insufficient for another
reservation. It fails cleanly if even one pending block cannot fit. Other
processes can still consume disk space during a run.

## Resume, cancellation, and reports

The output directory contains:

- COG TIFFs, published only after the required checks succeed.
- `state.json`: atomic checkpoint, input/configuration identity, missing files,
  per-block status, contributing sources, and verification results.
- `summary.csv`: one row per planned block, including counts, status, time,
  size, pixels checked, and errors.
- `run.log` and `errors.log`: appended across attempts.
- `.nasadem.lock`: OS-released process lock; the small file may remain after exit.
- `.incomplete/`: private staging directories; interrupted/failed candidates
  remain for diagnosis and are rebuilt on retry.

Repeat the same command to resume. VRT content and path, selected source
paths/sizes/modification times (including GDAL-reported dependencies), and
output-affecting options must match. Changing workers or adding `--verify` is
allowed. Changed input files, newly available formerly missing files, changed
block size/filter/minimum/overview options require a new output directory.
Source size/time signatures detect ordinary changes; they are not content hashes
or protection against edits that deliberately preserve metadata.

Existing recorded outputs are reopened and checked before skipping. With
`--verify`, all pixels are checked again. A final TIFF left by a crash before
its checkpoint is adopted only after exhaustive validation, even without the
flag. Unrelated TIFFs and incompatible outputs are refused; no overwrite option
is provided. A damaged final output is reported as failed and retained for
inspection; use a new output directory for rebuilding it.

Ctrl+C requests cooperative shutdown: no new blocks are scheduled, GDAL
callbacks and verification windows observe cancellation, and completed blocks
are checkpointed. Some GDAL operations, including full structural checking,
finish their current operation before cancellation is observed. Failed blocks
do not prevent other blocks from finishing.

| Exit code | Meaning |
| --- | --- |
| `0` | Eligible blocks complete, possibly with reported missing coverage; or successful dry run. |
| `1` | Processing, verification, or storage failure. |
| `2` | Invalid arguments, input, or incompatible resume state. |
| `130` | Cancelled. |

Progress and an approximate pixel-weighted ETA are printed every five seconds.
Source files and their sidecars are opened read-only; no source statistics or
overviews are built. Keep the source collection unchanged during a run.

## Tests and a real 10° subset

In a configured GDAL/QGIS shell:

```cmd
python -m unittest discover -s tests -v
```

The test-data helper downloads the official full VRT and 1° tiles whose
southwest origins are 20..39° E and 30..39° N. This covers two neighboring
10° blocks in the eastern Mediterranean, including Israel, with coastal gaps.
It preserves VRT ordering and mappings in a separate subset VRT and records
download sizes and SHA-256 hashes. Four HTTP downloads run concurrently over
verified TLS. Re-running reuses files only after checking their hashes.

```cmd
python tests\fetch_public_subset.py .validation\public-subset
python create_nasadem_cogs.py ^
  --vrt-file ".validation\public-subset\NASADEM_e20-e40_n30-n40.vrt" ^
  --output-dir ".validation\real-10deg" ^
  --workers 2 --block-size 10 --verify --build-overviews
```

The helper requires Python 3.11+ and at least 12 GiB free before downloading.
The merger also checks its own conservative storage reservations. Test data
and outputs under `.validation/` are ignored by Git. The helper is optional:
the merger itself never downloads data.

See the [validation report](validation-report.md) for the native Windows tests,
the downloaded 160-tile subset, two completed 10° COGs, and exhaustive results.

References: [GDAL COG driver](https://gdal.org/en/stable/drivers/raster/cog.html),
[GDAL VRT format](https://gdal.org/en/stable/drivers/raster/vrt.html),
[OpenTopography bulk access](https://www.opentopography.org/blog/programmatic-access-cloud-optimized-geotiff-cog-global-datasets).
