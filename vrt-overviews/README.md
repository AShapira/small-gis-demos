# VRT with JPEG overviews

Combine thousands of aligned GeoTIFF tiles from one or more folder trees into
one virtual mosaic, with a JPEG-compressed overview pyramid and lossless
transparency masks. Run the standalone script from the **OSGeo4W/QGIS command
shell**. Only Python's standard library and QGIS's GDAL bindings are needed.

The original GeoTIFFs remain the full-resolution data. The script opens sources
read-only and does not create per-source overviews, rewrite imagery, reproject,
or convert pixel types. JPEG compression affects the new lower-resolution
overviews only.

## Quick start on Windows

Copy `build_vrt_overviews.py` to a convenient directory, open the OSGeo4W/QGIS
command shell, and confirm its Python can import GDAL:

```bat
python -c "from osgeo import gdal; print(gdal.VersionInfo('--version'))"
```

Use **GDAL 3.8 or newer** and Python 3.8 or newer. The minimum GDAL version also
provides explicit dataset closing, needed to reliably release Windows file
handles after failures. This utility is tested with QGIS 4.2 / GDAL 3.13.1;
the minimum version is not a claim of a separate compatibility test.

Inspect the planned mosaic without writing any files:

```bat
python "C:\GIS\build_vrt_overviews.py" "D:\imagery\north" "E:\imagery\south" --output "D:\publish\mosaic.vrt" --dry-run
```

Create it:

```bat
python "C:\GIS\build_vrt_overviews.py" "D:\imagery\north" "E:\imagery\south" --output "D:\publish\mosaic.vrt"
```

Paths containing spaces must be quoted. UNC paths are supported. This command
runs in the shell; it is not a snippet for the QGIS Python Console. The script
also runs in a suitably configured GDAL Python environment on other platforms.

## Inputs and ordering

- Each mosaic must contain **one-band Byte grayscale** or **three-band Byte RGB**
  imagery. Undefined band interpretations are accepted in that order, provided
  every source has the same layout. Palette images, alpha bands, elevation,
  higher-bit-depth images, and mixed layouts are rejected.
- Every source must have an equivalent CRS, the same pixel size, and a matching
  north-up grid without rotation. Alignment tolerance is one millionth of a
  pixel; pixel-size error accumulated over a tile must also meet that tolerance.
  Tile dimensions may differ.
- All supplied folders are scanned recursively for `.tif`, `.tiff`, and
  `.geotiff`, case-insensitively. Directory symlinks/junctions are not traversed.
  Canonical source paths are deduplicated; distinct hard links are not identified
  as duplicates. A folder with no imagery contributes nothing; an entirely empty
  input selection fails.
- Folders retain command-line order. Files inside each folder are sorted by
  canonical path. **Later valid samples win in overlaps**; invalid samples do not
  hide valid data below them. The exact source order is saved in the manifest.
- Unreadable files or incompatible metadata fail the complete job with the
  offending filename. There is no silent skipping of incompatible tiles.

This workflow is intended for source imagery without overviews. Sources should
remain unchanged and accessible throughout the build and subsequent use.

## Output files and transparency

For `mosaic.vrt`, a normal build produces:

| File | Contents |
| --- | --- |
| `mosaic.vrt` | XML mosaic references, source ordering, and virtual validity masks. |
| `mosaic.vrt.ovr` | BigTIFF pyramid with 256 × 256 JPEG tiles. |
| `mosaic.vrt.ovr.msk` | DEFLATE-compressed BigTIFF masks with matching overview levels. |
| `mosaic.vrt.sources.txt` | UTF-8 source paths in priority order. |
| `mosaic.vrt.report.json` | Inputs, source/dependency sizes and timestamps, grid, settings, output paths, validation, and elapsed time. |

The `.ovr` and `.ovr.msk` files are omitted if the entire mosaic is already at or
below `--min-size` and no explicit levels were requested.

The VRT uses each source band's GDAL validity mask, including scalar NoData,
RGB `NODATA_VALUES` tuples, or existing mask files. Uncovered gaps are invalid.
Mask samples are normalized to 0/255 and retained independently of JPEG colors.
The output deliberately has **no scalar NoData sentinel**: valid black pixels
remain valid, and JPEG rounding cannot change transparency. Per-band validity is
preserved, including sources whose bands have different masks.

Overview imagery uses the selected resampling method with source validity.
Overview masks use nearest-neighbor reduction, so edges have binary transparency,
not fractional alpha. Lower mask levels are built from the first mask overview.
This choice can remove tiny isolated features at coarse scales. JPEG is lossy,
including at quality 100, and color artifacts can remain near sharp boundaries.

### Moving or serving the result

Keep the VRT, both pyramid sidecars, original GeoTIFFs, and any original mask or
metadata sidecars accessible. **The VRT is not a self-contained merged raster.**
References are relative to the output folder where possible. Windows paths on
different drives/shares remain absolute and are listed in the report. To move
between machines, preserve the relative directory layout or regenerate the VRT
for the destination. Manifest/report paths record the original build location;
they are informational, not runtime dependencies.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--output PATH` | Required | New `.vrt` path. Existing output artifacts are rejected. |
| `--dry-run` | Off | Metadata inspection and a console summary; no output folder, lock, or raster writes. |
| `--jpeg-quality 1..100` | `85` | JPEG quality for every imagery overview. |
| `--resampling METHOD` | `average` | `average`, `nearest`, `bilinear`, `cubic`, or `lanczos`; imagery only. |
| `--levels N ...` | Automatic | Strictly increasing powers of two, e.g. `2 4 8 16 32`. Non-consecutive levels are allowed. |
| `--min-size N` | `256` | Stop automatic powers-of-two levels when their largest dimension is at most N; ignored with explicit levels. |
| `--threads N` | Up to `4` | GDAL processing threads, capped at logical CPU count by default. |
| `--cache-mb N` | `512` | GDAL block cache in binary MiB; the dataset pool gets a separate equal RAM budget. |

RGB JPEG overviews use YCbCr with pixel interleaving. Grayscale uses MINISBLACK.
BigTIFF is always used for both physical pyramids, avoiding the classic TIFF
4 GiB limit. All pixels at the VRT's native resolution retain their source type
and grid; no physical full-resolution mosaic is written.

Example with explicit quality and resource settings:

```bat
python "C:\GIS\build_vrt_overviews.py" "D:\imagery" --output "D:\publish\mosaic.vrt" --jpeg-quality 90 --threads 2 --cache-mb 256 --levels 2 4 8 16 32 64
```

The source-dataset pool is limited to 128 datasets and per-file VSI caching is
disabled. These and the GDAL cache bounds are **not a total process RAM limit**:
compression, resampling, TIFF indexes, and file metadata need additional memory.
GDAL decides which processing stages can use multiple threads; JPEG compression
itself should not be assumed to scale with `--threads`.

The script prints an uncompressed overview-plus-mask pixel estimate before
building. It is not a compressed disk-space prediction or reservation. A mosaic
spanning large empty gaps can have a very large bounding rectangle; inspect
`--dry-run` first. Sparse TIFF encoding is not used.

## Completion, cancellation, and recovery

Stage progress and elapsed time are printed to the console. Press **Ctrl+C** to
request cancellation. GDAL stops at its next callback; interruption need not be
instant during native I/O. Exit codes are 0 for success, 1 for build failure,
2 for invalid command arguments, and 130 for cancellation.

An exclusive `<output>.lock` prevents cooperating script instances from building
the same output simultaneously. Existing VRTs, pyramids, reports, and recognized
sidecars are never overwritten. Choose a new output name to rebuild.

New data is staged under unique names beside the output so relative references
stay correct. After validation, sidecars and reports are installed first and the
VRT is installed last. Handled failures remove only the current job's files and
release its lock. Multi-file publication is not an atomic filesystem transaction;
do not open the output until the command reports completion.

A forced process kill, power loss, or failed storage device can leave a lock and
partial files. Check the PID in the lock and inspect the files before manually
removing leftovers. The script never guesses that an existing lock is stale.

Runtime verification checks CRS/grid, band counts/types, all overview dimensions,
every TIFF directory's codec/block size, BigTIFF headers, explicit masks, and
decoded samples at each level. Source/dependency sizes and timestamps are checked
again before publication. These are structural and sampled checks, not an
exhaustive pixel audit or a guarantee against edits that preserve timestamps.

## GeoServer

This utility prepares files only; it does not configure or publish to GeoServer.
GeoServer's documented GDAL formats do not list VRT, so verify the target
server's actual reader support before choosing direct VRT publication. Installing
the generic GDAL extension alone is not evidence that VRT is supported. A reader
must also honor the external pyramid and mask sidecars.

- [GDAL external-overview settings](https://gdal.org/en/stable/programs/gdaladdo.html)
- [GDAL virtual rasters and masks](https://gdal.org/en/stable/drivers/raster/vrt.html)
- [GeoServer GDAL formats](https://docs.geoserver.org/main/en/user/data/raster/gdal.html)

## Generated-data tests

The tests require NumPy in addition to GDAL; QGIS normally bundles both. They
generate their own fixtures and never select production imagery or start services.

```bat
python -m unittest discover -s tests -v
```

For a persistent run directory containing all generated files, CLI logs, and
a JSON summary (the destination must not exist):

```bat
python tests\run_validation.py --output-directory "D:\tests\vrt-validation-001"
```

The suite covers source hashes, masks and NoData, full-resolution samples,
overview contents, overlaps and gaps, Unicode paths, relocation, invalid inputs,
dry runs, existing output protection, cancellation, failed publication, and
2,000 generated tiles. Windows also runs native Ctrl+Break cancellation. See
[validation-report.md](validation-report.md) for measured coverage and limitations.
