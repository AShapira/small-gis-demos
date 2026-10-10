# GeoTIFF merge for QGIS 4.2

Merge a flat directory of `.tif` / `.tiff` files into larger, spatially disjoint
GeoTIFFs. With no size target, all inputs merge into **one BigTIFF**. An optional
size is a **target with a 10% allowance**: a 25 GB target accepts a
finished TIFF up to 27.5 GB, including metadata, its internal mask and any COG overviews. Inputs
are read-only. Every output is exhaustively verified before completion.

## QGIS Python console

QGIS 4.2 includes the required GDAL and NumPy. No Rasterio, pip installation,
or additional plugin is needed. Load the script with `runpy.run_path`:

```python
import runpy
merger = runpy.run_path(r"C:\tools\merge_geotiffs.py")

# Default: one file, available CPUs, and a RAM budget derived from available memory.
job = merger["start"](r"D:\rasters", r"D:\merged", base_name="sentinel")

# Optional size target: split into numbered files, each at most 27.5 GB.
job = merger["start"](r"D:\rasters", r"D:\merged-sized",
                      target_size="25GB", base_name="sentinel")

# Alternatively, set either or both resource limits; use a new output directory.
job = merger["start"](r"D:\rasters", r"D:\merged-limited",
                      target_size="25GB", cpus=8, ram="8GiB")

job.status()   # Progress, PID, log path and current report
job.cancel()   # Optional cooperative cancellation
# When job.done is True:
# report = job.result()
```

`start()` returns immediately. An isolated child process uses QGIS's bundled
Python; progress reaches the console through a Qt timer. QGIS remains responsive,
and its own GDAL cache, exception mode and CPU affinity are not changed.
Use the shown `runpy` loading method rather than pasting the script's CLI entry
point into the console editor. The script file must remain accessible to the child.

## QGIS / OSGeo4W shell

In a shell with QGIS's Python environment activated:

```bash
# Inspect overlaps; still reads and hashes the inputs.
python geotiff-merge/merge_geotiffs.py /data/input /data/analysis \
  --analyze-only

# Merge everything into one sentinel-00001.tif using available resources.
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-single \
  --base-name sentinel

# Merge with an optional size target.
python geotiff-merge/merge_geotiffs.py /data/input /data/merged \
  --target-size 25GB

# Optional limits, independently configurable.
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-limited \
  --target-size 25GB --cpus 8 --ram 8GiB
```

`--max-size` remains an alias for `--target-size`, with the same 10% allowance.
Standalone Python also works with matching GDAL 3.8+ bindings and NumPy.
Do not pip-install a different GDAL into an existing QGIS installation.

The output directory must **not exist** and must be separate from, and not
nested within, the input directory. Use a fresh directory when rerunning.
`25GB` means 25,000,000,000 bytes; `25GiB` means 26,843,545,600 bytes.
The scan is nonrecursive and accepts case-insensitive TIFF extensions.

Outputs are `mosaic-00001.tif`, `mosaic-00002.tif`, etc., plus `report.json`.
Set `--base-name sentinel` / `base_name="sentinel"` for `sentinel-00001.tif`, etc.
The numeric suffix is retained even when there is only one file.
Treat a run as successful only when the process exits zero and the report says
`"status": "complete"`. An analysis run has status `analyzed` and no TIFFs.

## Optional Cloud Optimized GeoTIFF (COG)

COG output is **off by default** (`cog=False`). Enable it to create tiled
BigTIFFs with a layout suitable for HTTP range reads, internal overviews, and
full GDAL COG structural validation:

```python
job = merger["start"](r"D:\rasters", r"D:\merged-cog",
                      cog=True, base_name="sentinel", compression="zstd",
                      compression_level=3, predictor=2,
                      target_size="25GB", cpus=8, ram="8GiB")
```

```bash
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-cog \
  --cog --compression zstd --compression-level 3 --predictor 2
```

Omitting the target still produces one file. All ordinary overlap, compression,
NoData and naming options apply. COG supports the exposed DEFLATE, LZW, ZSTD,
LZMA and NONE codecs when available in the installed GDAL. **PackBits is not
supported by the COG driver**; that combination fails before merging, including
in a dry run. The default remains DEFLATE level 6 with predictor 1.

`--cog-overviews` / `cog_overviews` selects lower-resolution display levels:

| Value | Purpose |
| --- | --- |
| `nearest` (default) | Select existing samples; useful for classes or when averaged display values are unwanted. |
| `average` | Average valid samples for smoother continuous imagery. These derived display levels contain new values. |
| `mode` | Use the most frequent valid class for categorical data. |
| `none` | Omit overviews. The file retains COG tile ordering but large files get a validator warning and lose efficient zoomed-out reads. |

Overviews are generated at powers of two until the largest dimension fits a
256-pixel tile. Small outputs may need none. They use the same lossless codec
and predictor as the full-resolution image. Existing source overviews are not
copied. Overview resampling **never resamples the full-resolution mosaic**:
all required valid base samples remain bit-identical to the sources, with the
same grid, band count and validity. Generated overview values are derived
products, outside the source-sample equality guarantee. No physical alpha band
is added; missing data keeps its existing scalar NoData/internal mask behavior,
including the `--output-nodata` option.

Each candidate is written as a staging TIFF, converted with GDAL's COG driver,
then reopened read-only. The finished COG is checked against the original inputs
and with `osgeo_utils.samples.validate_cloud_optimized_geotiff` using
`full_check=True`, including tile ordering and block structure. A layout marker
alone is insufficient. Failures prevent publication; per-file validation results,
overview counts and warnings are recorded in `report.json`. The COG validator is
included in the tested QGIS 4.2 environment; a standalone GDAL installation must
also supply `osgeo_utils`. Ordinary TIFF output does not require that module.

The size target applies **after conversion**, including all overview and mask
bytes. An oversized COG is split and rebuilt. Planning allows roughly one third
extra for overviews, but compression and padding make the actual overhead vary.
Allow additional temporary disk space: the staging TIFF, final COG and GDAL's
overview temporary data can coexist for each active file worker. Conversion,
overview generation and full layout checking also add time. CPU/cache limits
carry through conversion, and memory planning includes an extra per-worker
reserve; RAM remains a planning budget rather than an OS-enforced ceiling.
Cancellation is cooperative during conversion; full layout checking finishes
its current file before observing cancellation. Failed runs retain unpublished
files under `.incomplete` for inspection.

Serving a COG remotely also requires a server that supports HTTP range requests.
Avoid editing a finished COG in place because updates can break its ordering;
regenerate it instead. See the [GDAL COG driver documentation](https://gdal.org/en/stable/drivers/raster/cog.html).

## All input parameters

Both positional paths are required. All other user parameters are optional.
The API column refers to keyword arguments of `merger["start"](...)`.

| Shell parameter | QGIS `start()` parameter | Default | Meaning |
| --- | --- | --- | --- |
| `input` | `input_dir` (positional) | Required | Existing flat input directory. Reads `.tif` / `.tiff`, case-insensitively, without recursion. |
| `output` | `output_dir` (positional) | Required | New output directory, separate from and not nested in the input directory (or vice versa). Must not already exist. |
| `--target-size SIZE`, alias `--max-size SIZE` | `target_size` | Omitted / `None` | Omit to merge all inputs into one file with no byte-size cap. Otherwise use a positive size such as `25GB` or `25GiB`; each finished TIFF may be up to 10% larger. Does not set an exact file count. |
| `--base-name NAME` | `base_name` | `"mosaic"` | Filename stem, without the `.tif` extension. Outputs use `NAME-00001.tif`, `NAME-00002.tif`, etc. Spaces and Unicode are allowed; paths, Windows-reserved names/characters, empty names, and trailing dots/spaces are rejected. Quote names containing spaces in the shell. Does not rename `report.json`. |
| `--cpus N` | `cpus` | Available logical CPUs | Positive integer ceiling on worker/codec concurrency and child CPU affinity; never exceeds available CPUs. |
| `--ram SIZE` | `ram` | 80% of available RAM | Working-memory planning budget, e.g. `8GiB`, capped by available-memory allowance. At least 256 MiB is required. Not an OS-enforced memory ceiling. |
| `--overlap error\|first\|last` | `overlap` | `"error"` | Stop on conflicting valid samples, or explicitly select first/last valid source in lexical filename order. Identical overlaps and nodata fallback are accepted in every mode. First/last can discard observations. |
| `--analyze-only`, alias `--dry-run` | `analyze_only` | `False` | Hash sources, inspect compatibility/overlaps, and write a report without TIFFs. Default overlap conflict policy still applies. |
| `--suggest-nodata` | `suggest_nodata` | `False` | Exhaustively identify unused scalar NoData values across all valid source samples. Combine with `--dry-run` to inspect without merging. Adds console suggestions and a `nodata_analysis` report section. |
| `--output-nodata VALUE` | `output_nodata` | Omitted / `None` | Recheck that the value is representable and unused by valid samples, fill missing pixels with it, and write scalar NoData without a stored mask. Use a suggested string unchanged, including `nan` for eligible floating-point inputs. |
| `--compression CODEC` | `compression` | `"deflate"` | `deflate`, `lzw`, `zstd`, `lzma`, `packbits`, or `none` (case-insensitive). Only lossless data encoding; installed GDAL support is checked. See the codec comparison below. |
| `--compression-level N` | `compression_level` | Codec default | DEFLATE: 1–9, default 6; ZSTD: 1–22, default 9; LZMA: 0–9, default 6. Rejected for codecs without a level. |
| `--predictor 1\|2\|3` | `predictor` | `1` | Reversible prediction: 1 none, 2 horizontal, 3 floating-point only. Values 2/3 require LZW, DEFLATE or ZSTD. |
| `--cog` | `cog` | `False` | Create and structurally validate COG output. Default output remains ordinary tiled BigTIFF. PackBits is incompatible. |
| `--cog-overviews nearest\|average\|mode\|none` | `cog_overviews` | `"nearest"` | Resampling for new internal COG overviews, or no overviews. Ignored when COG is false; never affects full-resolution samples. |
| `--block-size N` | `block_size` | Automatic, starts at 2048 | Positive processing-window edge in pixels, reduced if needed for RAM. Independent of the fixed 256-pixel TIFF storage tiles. |
| `--cache-mib N` | `cache_mib` | Derived from RAM | Positive integer GDAL cache ceiling in MiB. Cache is also limited to one quarter of the RAM budget and 4 GiB. |
| `-h`, `--help` | — | — | Print shell usage and exit without processing. |
| — | `python_executable` | QGIS's own Python | Optional Python executable path if automatic discovery fails. Must provide matching GDAL and NumPy; normally leave unset. |

Size values accept bytes without a suffix, or `B`, `KB`, `MB`, `GB`, `TB`,
`KiB`, `MiB`, `GiB`, `TiB` (case-insensitive; decimal quantities accepted).
Use strings for sizes in `start()`, for example `ram="2GiB"`. CPU, cache and
window limits are integers; `analyze_only`, `suggest_nodata`, and `cog` are Booleans.

The hidden shell parameter `--cancel-file PATH` is an internal child-process
control: the worker cancels cooperatively if that file exists. `start()` allocates
it automatically; use `job.cancel()` rather than supplying it yourself. It has
no public `start()` keyword. `job.status()`, `job.done`, and `job.result()` inspect
the running/completed job; these are methods/properties, not input parameters.

## Console elapsed time and rough duration

The shell and QGIS console print timing at phase changes and every five seconds:

```text
[Merging and verifying] elapsed 00:03:10; rough expected total 00:07:20; remaining ~00:04:10
```

Times use `hours:minutes:seconds`. Elapsed time is measured from worker startup.
The expected total and remaining durations are **rough estimates**, using fixed
phase weights and completed hash bytes / overlap pixels / written and verified
pixels. They update as work proceeds, including extra work after size retries.
They can move backward or forward with compression, sparse extents, cache effects,
output hashing, and disk speed. Initially the estimate says `estimating`; it is
not a completion guarantee or a measured benchmark. Verification and rehashing
are included in the estimated run. Completion prints the actual elapsed time
and zero remaining time. The final report retains `elapsed_seconds`.

`start()` relays these lines on QGIS's GUI thread via its Qt timer. Outside a
running Qt application, inspect the job log named by `job.status()["log"]`.

## Choosing lossless compression

Use `--compression CODEC` in the shell, or `compression="CODEC"` in `start()`.
Names are case-insensitive. **All available choices in this script** are
`deflate`, `lzw`, `zstd`, `lzma`, `packbits`, and `none`. No choice enables lossy
encoding. Valid sample bits, masks/NoData semantics and supported metadata still
undergo the same exhaustive verification.

```bash
# ZSTD level 3 with integer horizontal prediction: the benchmark's faster ZSTD profile.
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-zstd \
  --compression zstd --compression-level 3 --predictor 2

# The benchmark's smallest tested lossless profile.
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-zstd9 \
  --compression zstd --compression-level 9 --predictor 2

# LZW with horizontal prediction; no compression-level parameter for LZW.
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-lzw \
  --compression lzw --predictor 2
```

QGIS console equivalent:

```python
job = merger["start"](r"D:\rasters", r"D:\merged-zstd",
                      compression="zstd", compression_level=3, predictor=2)
```

| Codec | Level setting | Predictor support | Default settings when selected |
| --- | --- | --- | --- |
| `deflate` | `--compression-level 1..9` | `1`, `2`, `3` | Level 6, predictor 1. This remains the overall default. |
| `lzw` | No level; specifying one is an error | `1`, `2`, `3` | Predictor 1 |
| `zstd` | `--compression-level 1..22` | `1`, `2`, `3` | Level 9, predictor 1 |
| `lzma` | `--compression-level 0..9` | Only `1` | Preset 6 |
| `packbits` | No level | Only `1` | No prediction |
| `none` | No level | Only `1` | Uncompressed data tiles |

`--predictor 1` applies no prediction and preserves the previous default.
`2` applies reversible horizontal differencing; the historical integer RGB
benchmark used this for LZW, DEFLATE and ZSTD. `3` applies reversible
floating-point prediction and requires Float32/Float64 inputs. These predictors
do not resample or change decoded values. Level ranges are deliberately explicit;
DEFLATE uses the portable 1–9 range even if a GDAL build supports higher levels.
LZMA and high ZSTD levels receive larger per-worker/codec memory reserves in the
RAM planner, which can reduce concurrency or require a larger RAM budget.

The installed GDAL must support the selected codec. A small in-memory TIFF probe
checks actual compression, predictor and sample-bit preservation before input
hashing/merging; unavailable codecs or unsupported combinations fail clearly.
There is no silent substitution. Resolved settings are recorded under
`compression_settings` in `report.json`. Actual output compression and predictor
are checked again after writing. Sample-based size estimates use the selected
codec, level and predictor; the final 110% size check still decides whether to split.
Internal validity masks, when used, remain GDAL's compressed 1-bit masks; the
codec selection controls the raster data tiles, not that internal mask encoding.

### What the existing compression benchmark measured

The [completed study](../docs/benchmark-results.md) used one 32,768 × 52,224,
three-band Byte Sentinel-derived RGB mosaic. The numbers below were copied from
its retained conversion manifests and checked against its final report. A
[sanitized numerical extract](compression-benchmark.json) records exact bytes,
times, creation options and source-manifest hashes. GB here means decimal GB.

| Historical configuration | TIFF size including overviews | Saved vs uncompressed | Conversion wall time | Conversion CPU time |
| --- | ---: | ---: | ---: | ---: |
| `none` | 6.856 GB | 0.0% | Reused reference | Reused reference |
| `lzw`, predictor 2 | 4.530 GB | 33.9% | 19.30 s | 63.28 s |
| `deflate`, level 6, predictor 2 | 3.870 GB | 43.6% | 34.52 s | 102.24 s |
| `packbits` | 6.660 GB | 2.9% | 19.01 s | 20.80 s |
| `zstd`, level 3, predictor 2 | 3.861 GB | 43.7% | 17.66 s | 39.17 s |
| `zstd`, level 9, predictor 2 | 3.778 GB | 44.9% | 30.47 s | 110.03 s |
| `lzma` | Not measured | Not measured | Not measured | Not measured |

All measured lossless variants matched the reference's decoded base and overview
pixels. This was a four-thread conversion with 512-pixel tiles and overviews.
The merger uses 256-pixel tiles, creates overviews only with opt-in COG output, and performs additional
verification and input hashing. **These sizes and times are not predictions for
a merge, and they do not establish a winner for single-band elevation, reflectance,
categorical or floating-point rasters.** In particular, the default merger's
predictor 1 differs from the benchmark's predictor 2.

| Choice | Benefits supported by these results | Costs and limits |
| --- | --- | --- |
| `none` | Straightforward uncompressed baseline; no data-codec work. | Largest measured storage footprint. The zero conversion time in the raw report means reference reuse, not an instantaneous write. |
| `lzw` | Converted faster than DEFLATE-6 while saving about one third of storage. | Larger than DEFLATE and either tested ZSTD profile; ZSTD-3 also converted faster in this run. |
| `deflate` | Saved 43.6%, close to ZSTD-3; keep the existing default when you want unchanged codec settings. | Took roughly twice ZSTD-3's conversion wall time in this dataset; level/predictor and data content affect the tradeoff. |
| `zstd` | Level 3 combined 43.7% savings with the shortest measured compressed-lossless conversion. Level 9 gave the smallest lossless result, 44.9% savings. | Level 9 took more wall/CPU time than level 3. Confirm reader support in downstream software; codec availability alone does not prove compatibility in every application. |
| `packbits` | Lowest conversion CPU time among the measured compressed-lossless profiles. | Saved only 2.9% on this photographic data, retaining almost the entire uncompressed storage cost. |
| `lzma` | An additional general-purpose lossless codec, validated by the merger's exact-bit tests on this QGIS installation. | No historical storage, conversion or serving result supports a speed/size recommendation. Its larger planning reserve can reduce parallelism; measure your own data before selecting it for throughput. |

The GeoServer study also measured rendering: for example, mean run-level p95
direct-WMS complete-view latency was 0.1984 s for uncompressed, 0.2007 s for
PackBits, 0.2193 s for LZW, 0.2225 s for DEFLATE, and 0.2068/0.2075 s for
ZSTD-3/9. These were two runs on shared hardware, not isolated decoder timings
or proof of statistical superiority. File size alone did not determine serving
performance. Nominal TIFFs also reported COG layout, so the study did not isolate
a unique COG layout advantage.

JPEG-80 achieved 0.412 GB in that study but changed pixels; it is not offered.
GDAL has other specialised options (CCITT for packed 1-bit data, and LERC,
WebP and JPEG-XL modes). They are not exposed by this script: their specialised
lossless modes have not been validated for its full supported data-type and
bit-pattern contract. COG is a layout/driver choice, not another compression name;
PNG/GeoPackage outputs are outside this TIFF writer.

Codec and predictor behavior follows the
[GDAL GeoTIFF creation options](https://gdal.org/en/stable/drivers/raster/gtiff.html#creation-options).

## Dry run: replace a stored mask with scalar NoData

To check whether the mask QGIS exposes as an alpha band can be replaced:

```bash
python geotiff-merge/merge_geotiffs.py /data/input /data/nodata-analysis \
  --dry-run --suggest-nodata
```

`--dry-run` is an alias for `--analyze-only`. It creates a new report directory,
reads the inputs and verifies their hashes, but writes no TIFFs. Add
`--suggest-nodata` to check **all valid samples**, including all bands and all
sources in overlapping areas. The console lists safe choices and a suggested
`--output-nodata=VALUE` argument. `report.json` includes `nodata_analysis` with:

- `default_output_uses_internal_mask`: whether the normal merge would store a
  mask because the sources have no scalar NoData value.
- `suggestions`: unused values as strings, suitable for passing back unchanged.
  This is a list of useful choices, not every unused value in the data type.
- `replacement_possible`: whether at least one common unused value was found.
- `dtype`, valid/invalid source band-sample counts, and, when a value was supplied,
  `requested` and `requested_is_safe`. Counts include repeated overlap samples;
  they do not count gaps outside source footprints.

For example, **only if `65535` was suggested for your inputs**, run:

```bash
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-nodata \
  --output-nodata=65535
```

In the QGIS console:

```python
analysis = merger["start"](r"D:\rasters", r"D:\nodata-analysis",
                            analyze_only=True, suggest_nodata=True)
# Once analysis.done is True:
choices = analysis.result()["nodata_analysis"]["suggestions"]
# If choices is nonempty, use a fresh output directory:
job = merger["start"](r"D:\rasters", r"D:\merged-nodata",
                      output_nodata=choices[0])
```

The merge **rescans for collisions**, so a suggestion from an older dry run
cannot silently invalidate changed inputs. A collision stops the run before any
output TIFF is written, even if `--overlap first/last` would discard that source's
value. An out-of-range value, fractional integer NoData, or a floating-point value
requiring narrowing/rounding is rejected. Use the exact suggested string;
`--output-nodata=-9999` with `=` also handles negative/scientific notation safely.

Only invalid pixels and uncovered output areas are filled with the chosen value.
Valid sample bits and the data type remain unchanged. The output uses scalar
NoData metadata and **no stored internal mask or alpha band**. GDAL can still
provide an implicit validity mask derived from NoData; that is not an extra stored
band. Verification checks the new NoData metadata, absence of a stored mask,
every required valid sample, and identical validity locations. Existing scalar
or RGB tuple NoData can be replaced explicitly in the same way; source masks and
NoData always determine which original pixels are valid.

The search first tests common values, then searches unused representable values
with bounded-memory occupancy maps and additional full passes as needed. It can
find holes inside the observed range, not just values outside its minimum and
maximum. Duplicate-heavy data covering much of a large type's domain can make
this exhaustive fallback expensive. `nan` is suggested for floating-point data
only when no valid NaN exists; zero collides with either sign of valid zero.
One scalar value must be unused across **every band** in a multiband output.
No pixel sampling or data-type conversion is used.

If every possible value is already valid somewhere, no scalar NoData value can
replace the mask without losing information. Keep the mask wherever missing
pixels need representation. An entirely valid output needs neither a mask nor
NoData, but automatic removal of redundant masks is not implemented.
Omitting these options retains the existing merge behavior. `--suggest-nodata`
can also accompany a real merge; it reports choices but does not apply one
unless `--output-nodata` is supplied.

## What “data unchanged” means

The default policy preserves every **valid, decoded source band sample** at
its original pixel location. It uses the selected lossless codec (DEFLATE by
default), no reprojection,
resampling, blending, arithmetic, or dtype conversion. `--compression none`
disables compression. Output files are BigTIFFs with 256 × 256 pixel tiles.

Verification is mandatory and exhaustive, with no numerical tolerance:

1. SHA-256 hashes record every input and dependency reported by GDAL, including
   external masks or auxiliary metadata. They are checked again after writing.
2. The script closes and reopens each output, then reads the original sources
   against it in bounded windows. With the default overlap policy, it compares
   **every valid sample of every source**, including duplicate overlap samples,
   byte for byte. Floating-point signed zeros and valid NaN payloads matter.
3. It checks the complete validity mask against the union of valid source
   samples. Uncovered areas remain invalid, including gaps with valid zero-valued
   samples nearby. Source nodata never overwrites valid data from another source.
4. It checks CRS, transform, dimensions, dtype, band count, nodata, scales,
   offsets, units, descriptions, color interpretation, and Area/Point semantics.
   With `--output-nodata`, it verifies the explicitly requested output NoData
   convention instead of requiring the original NoData metadata.
5. It verifies that output rectangles do not overlap and cover every source
   footprint, checks actual TIFF byte lengths, and records output SHA-256 hashes.

Invalid samples are absence of data: their raw payload bytes are not preserved.
They are filled with the common nodata value, or zero plus an internal mask when
there is no nodata value. A valid observation in another source may fill a nodata
hole. This is a mosaic, not a byte-identical archive of the input TIFF containers.

Arbitrary application tags, statistics, overviews, original compression/layout,
and other ancillary metadata are not copied. COG output can generate new overviews. The source files retain their originals.
Do not modify inputs or their sidecars during a run. Hashing detects persistent
changes; this is not an atomic snapshot of a directory being actively written.

## Overlaps: which choice preserves the information?

The report distinguishes intersecting footprints, overlapping **valid** samples,
and conflicting values. Counts are band samples (not necessarily spatial pixels).
Pairwise counts can count the same location more than once with three or more
overlapping files. Examples identify the source pair, band, pixel, map coordinate,
and values.

| Situation | Default behavior |
| --- | --- |
| Footprints overlap, but only one source is valid | Use the valid sample. |
| Both sources are valid and have identical sample bits | Keep one copy; both sources pass verification. |
| Both sources are valid and disagree | Stop before creating any output TIFFs; write details and suggestions to `report.json`. |

If values disagree, a single mosaic cell cannot retain both observations.
**Keep separate layers/originals if both observations must be preserved.** If one
source is authoritative, select an explicit priority after reviewing the report:

```bash
# First valid source wins; precedence is case-sensitive lexical filename order.
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-first \
  --target-size 25GB --overlap first

# Last valid source wins in that same order.
python geotiff-merge/merge_geotiffs.py /data/input /data/merged-last \
  --target-size 25GB --overlap last
```

The exact order appears as `input_order` in the report. Priority applies per
band, with nodata fallback. These options **discard conflicting observations**;
the report sets `all_source_valid_values_preserved` to false and counts discarded
source samples. Verification then proves that every selected value is unchanged
and the output implements the chosen priority, not that all original values
survived. Averaging and blending are deliberately unavailable because they create
new pixel values. Analysis alone never asserts output preservation.

## File count and size planning

Without `target_size` / `--target-size`, one BigTIFF covers the bounding rectangle
of all input footprints. Gaps remain invalid; even distant inputs belong to this
one rectangle. There is no size calibration, byte cap or splitting in this mode.
It still processes bounded windows and verifies every required sample. Very
large gaps can make the single output expensive to write and verify. In the
report, `target_file_bytes`, `max_file_bytes`, and `size_allowance_percent` are
`null`. Analysis-only runs still produce no TIFFs.

With a size target supplied, the following planning rules apply.

**100 GB of inputs does not guarantee exactly four 25 GB outputs.** The result's
size changes with lossless compression, gaps, duplicated overlap, source
overviews, masks, block padding, and TIFF overhead. Each final file must be at most 110% of the target; the number
of files is an outcome. This script does not promise the minimum possible count.

The planner estimates compression from eight sampled 256-pixel windows per
source using the selected GDAL codec, level and predictor, partitions spatial
rectangles on pixel boundaries,
and trims footprint-free outer space. Sampling is for size estimation only;
verification always checks every required sample.
It can split a single input that is larger than the limit. After writing a
candidate (and converting it when COG is enabled), it measures its closed file size. Candidates up to target +10% are
accepted. Larger candidates are discarded and split until every accepted file fits. If even one tiled pixel cannot
fit with its TIFF overhead, the run fails explicitly. Empty areas within output
rectangles are invalid; the planner skips completely uncovered rectangles.
Entirely nodata source rasters retain their footprints.

The cap applies to each **final TIFF**, not the report, combined output directory,
RAM, or temporary candidates. Estimates can be wrong; a temporary candidate can
exceed the cap. Allow disk space for the full result plus concurrent candidates. Heterogeneous
compression and spatial gaps can still produce extra output files.

## CPU and RAM controls

- Without `cpus` / `--cpus`, the script uses the available logical CPU count.
  Independent hashes, overlap pairs, size samples, output mosaics and verification
  run concurrently. Each worker owns its GDAL datasets; handles are never shared.
  Remaining CPU slots form GDAL's shared compression pool, including when there
  is only one output. Codec threads are shared across files, not multiplied by
  the output-worker count.
- `cpus=8` / `--cpus 8` bounds worker/codec concurrency. The worker process also
  receives CPU affinity for at most that many allowed logical CPUs on Windows
  and Linux. This limits cores available to the merge, not a percentage of host
  CPU time. The Qt parent and unrelated programs are unaffected.
- Without `ram` / `--ram`, the working-memory budget is 80% of memory available
  at planning time. The planner uses the smaller of an explicit budget and that
  available-memory allowance. It assigns a shared GDAL cache (up to 4 GiB),
  estimates source decoding buffers and temporary arrays, and reduces worker
  count/window size when needed. Four source handles are cached per output worker.
- `ram="8GiB"` / `--ram 8GiB` is a **working-memory budget, not an OS-enforced
  process memory ceiling**. Python, GDAL codecs, allocator behavior, and memory
  pressure from other applications can affect actual RSS. It excludes the QGIS
  parent process. At least 256 MiB is required; very large source strips can need
  a larger budget. Limits and the resolved allocation are recorded in the report.
- Optional advanced controls (also available as `start()` keywords): `--block-size` caps the processing window edge
  (default auto, up to 2048 pixels), and `--cache-mib` caps the GDAL cache inside
  the overall RAM budget. More threads do not guarantee speedup when storage is
  the bottleneck or there are few independent output regions.

## Compatibility and operational limits

- Equal CRS is necessary but insufficient: inputs must be north-up, unrotated,
  have equal pixel sizes and aligned pixel origins, compatible band interpretation,
  identical band count/dtype, and a common nodata convention. Grid comparisons
  allow only `1e-7` pixel of representation error (including accumulated pixel-size
  drift across a raster). No resampling is attempted for incompatible grids.
- Supported types are real integer types up to 32 bits and Float32/Float64.
  Complex types, 64-bit integers, palettes, alpha bands, GCP/RPC georeferencing,
  differing per-band nodata and combinations of tuple and scalar nodata are rejected. Preprocessing those
  inputs requires a separate, explicit preservation decision.
- RGB tuple nodata (`NODATA_VALUES`, such as `0 0 0`) is supported when scalar
  band nodata is absent. A zero in only one color channel remains valid. The
  tuple metadata and a shared internal validity mask are preserved.
- With scalar nodata, differing per-band validity is supported. Masked-out data is
  normalized to nodata. Masks that mark nodata-valued pixels valid are rejected
  because that distinction cannot be encoded by the output nodata convention.
  Without nodata, all bands must share a validity mask, stored inside the TIFF.
  An explicit safe `--output-nodata` instead encodes per-band validity as scalar
  NoData and removes the need for that stored mask.
- Overlap checking examines intersecting source pairs and all their overlap
  samples; heavily overlapping collections can be expensive. Full verification
  and before/after hashing intentionally add substantial I/O. There is no
  statistical sampling or option to skip verification.
- Work happens in `.incomplete` inside a newly created output directory. Only
  this run's oversized temporary candidates are deleted automatically. Failure
  or Ctrl+C leaves a failure report and may leave incomplete files for inspection.
  No resume or overwrite mode is provided. A crash during final publication can
  leave some TIFFs at the top level: without a complete report, the run is unfinished.
- QGIS console cancellation is cooperative at bounded processing windows and
  hash chunks; an in-progress GDAL call finishes before cancellation is observed.
  The sibling job log and cancellation file are retained as evidence. No inputs
  are removed. Do not call the internal `run()` function concurrently inside
  QGIS; use the isolated `start()` API.

## Validation

```bash
python -m unittest discover -s geotiff-merge/tests -v
```

The generated-data suite checks spatial reconstruction, holes, nodata fallback,
valid zeroes, per-band masks, identical/conflicting overlaps, both priority
policies, float bits, internal/external masks, band metadata, actual-size splitting,
oversized-candidate retries, incompatible grids, small caps, distant footprints,
input changes, and deliberately corrupted pixels/masks. It also exercises the CLI.
See the [validation report](validation-report.md) for tested versions and limits.

The Windows `tests/validate_sentinel.py` harness uses a retained Sentinel-derived RGB
reference, creates aligned lossless input windows plus an identical overlap,
launches the async API with a Qt heartbeat, records process CPU/RAM telemetry,
and independently compares all outputs against the unsplit reference. It does
not download data. See the validation report for measured results and the
distinction between bundled-Python, Qt event-loop and full QGIS desktop coverage.

Design references: [GDAL GeoTIFF masks, compression and BigTIFF](https://gdal.org/en/stable/drivers/raster/gtiff.html)
and [GDAL raster thread safety](https://gdal.org/en/stable/development/rfc/rfc101_raster_dataset_threadsafety.html).
