# GeoTIFF merge for QGIS 4.2

Merge a flat directory of `.tif` / `.tiff` files into larger, spatially disjoint
GeoTIFFs. With no size target, all inputs merge into **one BigTIFF**. An optional
size is a **target with a 10% allowance**: a 25 GB target accepts a
finished TIFF up to 27.5 GB, including metadata and its internal mask. Inputs
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
| `--compression deflate\|none` | `compression` | `"deflate"` | Lossless DEFLATE compression or uncompressed BigTIFF. |
| `--block-size N` | `block_size` | Automatic, starts at 2048 | Positive processing-window edge in pixels, reduced if needed for RAM. Independent of the fixed 256-pixel TIFF storage tiles. |
| `--cache-mib N` | `cache_mib` | Derived from RAM | Positive integer GDAL cache ceiling in MiB. Cache is also limited to one quarter of the RAM budget and 4 GiB. |
| `-h`, `--help` | — | — | Print shell usage and exit without processing. |
| — | `python_executable` | QGIS's own Python | Optional Python executable path if automatic discovery fails. Must provide matching GDAL and NumPy; normally leave unset. |

Size values accept bytes without a suffix, or `B`, `KB`, `MB`, `GB`, `TB`,
`KiB`, `MiB`, `GiB`, `TiB` (case-insensitive; decimal quantities accepted).
Use strings for sizes in `start()`, for example `ram="2GiB"`. CPU, cache and
window limits are integers; `analyze_only` and `suggest_nodata` are Booleans.

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
its original pixel location. It uses lossless DEFLATE, no reprojection,
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
and other ancillary metadata are not copied. The source files retain them.
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
source using lossless DEFLATE, partitions spatial rectangles on pixel boundaries,
and trims footprint-free outer space. Sampling is for size estimation only;
verification always checks every required sample.
It can split a single input that is larger than the limit. After writing a
candidate, it measures its closed file size. Candidates up to target +10% are
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
