# NASADEM / SRTM COG merger

Create Cloud Optimized GeoTIFF blocks from an existing local Int16 NASADEM or
Float32 SRTM VRT and its 1° height tiles using any positive whole-degree divisor of 360. The default
is 10°; 360° creates one whole-world COG. You can also consolidate previously
merged TIFFs into one COG covering their combined extent. Run the single file
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

## Consolidate previous merge results

Use a directory of previously generated TIFFs as the input. The original 1°
tiles, original VRT, and previous `state.json` are not required:

```cmd
python create_nasadem_cogs.py ^
  --merge-results-dir "D:\Maps\NASADEM\COG_10deg" ^
  --output-dir "D:\Maps\NASADEM\COG_merged" ^
  --verify
```

This writes **`NASADEM_merged.tif`**, with fresh internal average overviews.
Use `--output-prefix SRTM_GL1_Ellip` to name it `SRTM_GL1_Ellip_merged.tif`.
Add `--dry-run` first to inspect the selected files, exact extent/dimensions,
overview dimensions, and compressed output/working-space estimates without
creating files. Dry runs read metadata only; they report that pixel overlap
validation occurs during execution.

Only immediate `.tif`/`.tiff` files are selected, case-insensitively; subdirectories
and VRTs are ignored. `--filename-filter` can restrict selection. Every selected
file must be readable and compatible; unrelated TIFFs are not silently skipped.
Inputs may be rectangular and need not themselves be COGs. They must have one
Int16 or Float32 height band, matching finite NoData, EPSG:4326, aligned north-up
square pixels on the existing integer-samples-per-degree grid, and consistent
dataset/band metadata (excluding statistics). Explicit masks are rejected;
use the merger's NoData-based outputs. No horizontal or vertical transformation
is performed.

The output uses the smallest rectangle containing the selected rasters, including
their endpoint samples and existing NoData margins. It does not expand to the
whole world or crop to valid terrain. Shared samples occupy one pixel position;
gaps remain NoData. Overlapping valid values must agree **exactly**, including
Float32 values. A valid value may fill another input's NoData. Conflicts fail
before conversion and report the two source paths and sample location, even
without `--verify`. `--verify` additionally compares every output pixel and
validity flag against these input TIFFs, not against the unavailable originals.

Consolidation always uses BigTIFF, DEFLATE level 6, the type-specific predictor,
512-pixel tiles, and newly generated internal average overviews. Source overviews
are ignored. No overview levels are necessary when the image already fits
512 × 512. `--build-overviews` is accepted but redundant in this mode.
`--block-size` and `--min-tiles-per-block` apply only to `--vrt-file`.
`--workers` still controls concurrent output files; consolidation has only one
output and uses one GDAL compression thread.

Use a separate new/empty output directory, or resume the same consolidation
command in its existing output directory. The selected TIFFs and dependencies
are recorded in its own state; changing inputs or output settings requires a
new destination. Existing inputs are never modified. Disk policy, staging,
cancellation, validation, and resume behavior are otherwise shared with block
generation.

## VRT for each output stage

Every successfully completed stage producing **more than one COG** also writes
**`<output-prefix>_mosaic.vrt`**. This includes ordinary degree-block generation.
The VRT references only that stage's eligible, validated output TIFFs, using
relative filenames. Move the VRT and its TIFFs together to open the mosaic in
QGIS or another GDAL reader. The original input collection is not needed to
read it. To consolidate these outputs, use `--merge-results-dir`; the original
`--vrt-file` mode remains restricted to VRTs referencing 1° input tiles.

The VRT is published atomically after all eligible outputs pass validation.
Failed or cancelled stages publish no new mosaic VRT. Resume regenerates a
missing VRT after checking the COGs, without rewriting them; this also applies
to older compatible runs. VRT ownership is recorded separately from the existing
resume signature, and unrelated or modified VRTs at the target name are refused.
Stages producing a single COG, including consolidation, have **no accompanying
VRT**. Temporary conversion VRTs remain private staging files.

## Float32 SRTM with WGS84 ellipsoidal heights

[OpenTopography SRTM GL1 Global 30m Ellipsoidal](https://portal.opentopography.org/raster?opentopoID=OTSRTM.082016.4326.1)
provides Float32 GeoTIFF tiles and a VRT at
`https://opentopography.s3.sdsc.edu/raster/SRTM_GL1_Ellip/`.
Keep the VRT's referenced directory structure, or use a local subset VRT that
references only the downloaded tiles. The provider's full VRT is named
`SRTM_GL1_Ellip_srtm.vrt` and references `SRTM_GL1_Ellip_srtm/`.

```cmd
python create_nasadem_cogs.py ^
  --vrt-file "D:\Maps\SRTM_GL1_Ellip\SRTM_GL1_Ellip_srtm.vrt" ^
  --output-dir "D:\Maps\SRTM_GL1_Ellip\COG_10deg" ^
  --output-prefix SRTM_GL1_Ellip ^
  --block-size 10 --workers 2 --verify --build-overviews
```

The merger detects the VRT data type and preserves Float32 base values exactly,
including fractional and negative elevations. It does not convert heights or
infer a vertical datum from EPSG:4326. OpenTopography documents this particular
SRTM collection as WGS84 ellipsoidal; ordinary SRTM/NASADEM collections can have
EGM96 heights. Use inputs with a consistent height reference and keep that
provenance with the data. Default-domain metadata on the input VRT, including
an explicit `VERTICAL_DATUM` entry when present, is retained in the outputs.

## Parameters

| Option | Default | Meaning |
| --- | --- | --- |
| `--vrt-file` | One input mode required | Local VRT with local 1° TIFF references. Mutually exclusive with `--merge-results-dir`. Relative paths resolve against the VRT directory. Absolute and UNC paths are supported. |
| `--merge-results-dir` | One input mode required | Directory of previously merged TIFFs; create one COG with internal average overviews covering their combined bounds. |
| `--output-dir` | Required | New/empty directory, or the directory of a compatible resumable run. May be a subdirectory of the source collection. |
| `--block-size DEGREES` | `10` | Positive integer divisor of 360; nominal geographic block size in degrees. Latitude bands are clipped at the poles. Use `360` for one whole-world COG. |
| `--workers` | `8` | Maximum concurrent blocks. Each GDAL conversion uses one compression thread. |
| `--verify` | Off | Check every full-resolution value and NoData flag against the selected input TIFFs, plus full COG structure. Applies to existing outputs on resume too. |
| `--build-overviews` | Off in VRT mode; always on for consolidation | Build fresh internal average overviews during COG conversion. |
| `--disk-space-policy {warn,error}` | `warn` | Warn and continue if estimated space is insufficient or free space cannot be queried. Use `error` to stop before starting a block in these cases. |
| `--min-tiles-per-block` | `1` | Minimum distinct available nominal 1° source tiles belonging to a block. Boundary-only neighbors do not inflate the count. |
| `--filename-filter` | None | Case-insensitive substring of the basename, including extension. For example `"n32e"` selects northern 32° tiles in eastern longitudes. |
| `--output-prefix` | `NASADEM` | Output filename prefix; use `SRTM_GL1_Ellip` for the ellipsoidal SRTM collection. Letters, digits, `_`, and `-` only, starting with a letter or digit. |
| `--dry-run` | Off | Print source counts, missing files, exact blocks/dimensions, tile counts, estimated compressed output and temporary sizes, working-space reservations, and disk policy; create no outputs or logs. |

Valid block sizes are **1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 18, 20, 24,
30, 36, 40, 45, 60, 72, 90, 120, 180, and 360** degrees. Fractions, zero,
negative values, and sizes that do not divide 360 exactly are rejected.

Filtering applies **before** block assignment and seam composition. Only selected
sources can supply pixels, including boundary samples. A filter with no usable
matches is an error. There is no fixed expected global file count: the actual
VRT, available files, filter, and minimum count determine it.

## Grid, seams, and missing coverage

The tool accepts ordinary single-band Int16 or Float32 height VRTs in WGS 84
(EPSG:4326), with matching north-up square pixels, explicit matching NoData,
and 1° TIFFs containing both endpoint samples. At native one-arcsecond spacing,
each source has **3,601 × 3,601** samples and each 10° output has
**36,001 × 36,001** samples. Resolution is read from the input, not resampled.
Rotated, warped, derived, scaled, remote, and incompatible inputs are rejected.
Every selected source must have the VRT's data type; mixed Int16/Float32 inputs
are rejected. NoData must be explicit and finite, representable in the input
type, and match the source tiles. Float32 NoData is normalized to Float32
precision and can be fractional; NaN/infinite NoData is not supported.

Both grid axes start at -180° and advance by the block size. Latitude bands
are then clipped to -90..90°, giving symmetric polar edge blocks. This preserves
the original 5°/10°/20° boundaries, filenames, and compatible resume states.

| Size | Complete world grid (columns × rows) | Latitude band heights, south to north |
| --- | --- | --- |
| 30° | 12 × 6 | Six 30° bands |
| 45° | 8 × 4 | Four 45° bands |
| 60° | 6 × 4 | 30°, 60°, 60°, 30° |
| 90° | 4 × 2 | 90°, 90° |
| 120° | 3 × 3 | 30°, 120°, 30° |
| 180° | 2 × 2 | 90°, 90° |
| 360° | 1 × 1 | 180° |

These are potential world grids: only blocks containing selected source tiles
are planned, and only those meeting `--min-tiles-per-block` are generated.
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
`--output-prefix SRTM_GL1_Ellip` changes only the filename prefix, for example
`SRTM_GL1_Ellip_N30E030_N39E039.tif`.

With `--block-size 360`, the single output is
`NASADEM_S90W180_N89E179.tif`. It spans -180..180° longitude and -90..90°
latitude even when the input VRT or selected sources cover a smaller region.
At native one-arcsecond spacing it has **1,296,001 × 648,001** samples.
Inspect its dimensions and compressed storage estimate before generation:

```cmd
python create_nasadem_cogs.py ^
  --vrt-file "D:\Maps\NASADEM\NASADEM_be.vrt" ^
  --output-dir "D:\Maps\NASADEM\COG_world" ^
  --block-size 360 ^
  --dry-run
```

Missing referenced TIFFs are listed in `state.json`; uncovered areas remain
NoData. They do not produce an error exit when other selected inputs are usable.
Blocks below the minimum are reported without producing TIFFs. A present but
corrupt/incompatible TIFF is an error, not an absent tile. Geographic gaps,
including ocean areas without source coverage, also remain NoData.

## COGs, verification, and resources

Outputs use DEFLATE level 6, integer predictor 2 (`PREDICTOR=STANDARD`) for Int16
or floating-point predictor 3 (`PREDICTOR=FLOATING_POINT`) for Float32,
512 × 512 tiles, and `BIGTIFF=IF_SAFER` for ordinary blocks; consolidation uses
`BIGTIFF=YES`. `IF_SAFER` selection is GDAL's heuristic.
In VRT mode, overviews are explicitly disabled unless requested. When enabled, average
overviews are created at powers of two until the largest dimension is at most
512. GDAL's COG generator may round odd overview dimensions down rather than
up; the recorded dimensions reflect the installed driver. Existing source
overviews are not copied. Never add overviews to or edit a
completed COG in place; regenerate it in another output directory.

Every candidate is closed and reopened to check dimensions, georeferencing,
CRS, data type, NoData, selected metadata, compression, tile size, overview
dimensions, and basic COG structure before publication. `--verify` additionally
uses the GDAL validator's `full_check=True` and independently composes every
base-resolution window from the selected input TIFF values/georeferencing and source
precedence. Values and validity must match exactly, including gaps and seams.
This is not a comparison against the temporary block VRT. Overview pixels are
derived average values, outside the base-sample equality guarantee; their
generation is covered by tests.

Workers own their dataset handles. Verification uses 512-pixel windows and
bounded source-reader caches; whole blocks are not loaded into Python memory.
The process uses a shared 512 MiB GDAL cache, a 64-entry VRT dataset pool, and
one GDAL compression thread per worker. These are planning limits, not a hard
operating-system RAM ceiling.

Storage estimates use the **actual compressed source TIFF sizes**, apportioned
to their contributing windows. Existing source overviews are discounted using
their share of the source pyramid's pixel count; identical source references
are counted once. Uncovered areas use a small compressed NoData-tile allowance
(2 KiB per 512 × 512 tile), plus TIFF metadata/index overhead. Requested output
overviews are estimated from their additional pixel count. This avoids treating
the entire geographic rectangle, including ocean and missing coverage, as raw
uncompressed data. Estimation reads metadata, without scanning raster pixels.

Dry-run JSON and `state.json` distinguish `estimated_output_bytes` from
`estimated_temporary_bytes`. The latter allows for the temporary overview data
used by [GDAL's COG CreateCopy workflow](https://gdal.org/en/stable/drivers/raster/cog.html)
(approximately one third of final output size when overviews are generated),
plus the staging VRT. `storage_reservation_bytes` is their sum with a **10% working
margin**; the disk check adds **1 GiB headroom**. These are estimates, not upper
bounds: source compression, overview compression, terrain, and GDAL versions
can change the actual result. No source files are modified by estimation.

The default **`--disk-space-policy warn`** logs a warning and continues when a
block's estimated working space exceeds free space. Scheduling still reduces
concurrency when estimated reservations do not fit together. If one block alone
exceeds the estimate-based limit, it runs by itself. To stop before that block
starts instead, pass **`--disk-space-policy error`**. An unavailable free-space
query also follows the selected policy. Actual GDAL or filesystem write errors
always fail the affected block and are checkpointed normally.

## Resume, cancellation, and reports

The output directory contains:

- COG TIFFs, published only after the required checks succeed.
- `<output-prefix>_mosaic.vrt` for a successfully completed multi-COG stage.
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
output-affecting options must match. Changing workers, disk-space policy, or adding `--verify` is
allowed. Changed input files, newly available formerly missing files, changed
block size/filter/minimum/overview/prefix options require a new output directory.
Existing Int16 runs with the default `NASADEM` prefix retain their original
resume identity and can be resumed after this Float32 extension.
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
The merger uses the compressed storage estimates and disk-space policy above. Test data
and outputs under `.validation/` are ignored by Git. The helper is optional:
the merger itself never downloads data.

See the [validation report](validation-report.md) for the native Windows tests,
the downloaded 160-tile subset, two completed 10° COGs, divisor-grid dry runs,
whole-world synthetic COGs, and compatibility with previous resume states.

The [local S3 ImageMosaic test](s3-imagemosaic/README.md) reuses those two COGs
with Kartoza GeoServer 2.28.0, MinIO, and a PostGIS index.

References: [GDAL COG driver](https://gdal.org/en/stable/drivers/raster/cog.html),
[GDAL VRT format](https://gdal.org/en/stable/drivers/raster/vrt.html),
[OpenTopography bulk access](https://www.opentopography.org/blog/programmatic-access-cloud-optimized-geotiff-cog-global-datasets).
