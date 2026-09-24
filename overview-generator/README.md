# overview-generator

A single-file Python utility for rebuilding **external GeoTIFF overviews** from
the Python console in **QGIS 3.28 on Windows**.

The input is one flat directory. For each eligible `image.tif`, the output is
`image.tif.ovr` alongside it. The `.ovr` is a TIFF overview pyramid. Thousands
of inputs are supported without loading whole rasters into Python memory or
submitting thousands of tasks to the executor at once.

Only `overview-generator.py` is needed at runtime. There are no pip dependencies:
the script uses the standard library, QGIS's Qt bindings, and QGIS's GDAL Python
bindings. A separate command-line mode is available in a configured GDAL Python
environment.

## 1. Quick start in QGIS

1. Save `overview-generator.py`, for example to `C:\GIS\overview-generator.py`.
2. Open QGIS 3.28 and open **Plugins → Python Console**.
3. Inspect the planned work with a dry run:

```python
import runpy
og = runpy.run_path(r"C:\GIS\overview-generator.py")
job = og["start"](r"D:\GeoTIFFs", storage="ssd", dry_run=True)
```

`start()` returns immediately. QGIS remains usable while the job runs. Read the
console's final summary and the per-file results before starting the real run.

```python
job.done          # True only after processing and final logging have ended
job.status()      # A dictionary containing counts, progress, ETA and log paths
```

4. Remove the input TIFF layers from the QGIS project before the real run.
   Closing a layer's properties dialog or hiding the layer does not release it.
   The script rejects a real run when it detects directly loaded input TIFFs.
5. Start the real run after the dry run finishes:

```python
job = og["start"](r"D:\GeoTIFFs", storage="ssd")
```

The defaults use bilinear resampling and rebuild existing overviews. To choose
another method:

```python
job = og["start"](r"D:\GeoTIFFs", resampling="average", storage="ssd")
```

Use a raw string for Windows paths, as above, or use forward slashes:
`"D:/GeoTIFFs"`. Do not end a raw string with a single backslash. UNC paths are
accepted, for example `r"\\server\share\rasters"`; use `storage="network"` as a
starting point.

Use `runpy.run_path()` as shown rather than pasting the entire file into the
console. Loading it this way defines the API without invoking its command-line
parser. Keep the `job` variable if you want to inspect or cancel the run. The
script also holds an internal reference until completion.

## 2. What is deleted or changed

**A real run deliberately deletes old overviews before building replacements.**
This is a rebuild utility, not a missing-overviews-only utility. There is no
transactional backup of old pyramids.

| Input situation | Default behavior |
| --- | --- |
| Ordinary TIFF without overviews | Create external `.ovr`. |
| TIFF with external `.ovr` | Delete the old pyramid and associated overview sidecars, then rebuild. |
| Ordinary TIFF with embedded overviews | Remove the embedded overviews, close the TIFF, reopen read-only, then build `.ovr`. This changes the source TIFF's structure. |
| Embedded overviews with `remove_internal=False` | Skip the file; retain its existing overviews. |
| Recognized Cloud Optimized GeoTIFF (COG) | Skip and report it, preserving its specialized layout. |
| Raster already at or below `min_size`, with automatic levels | Skip and retain any existing overviews. Use explicit `levels` to force smaller overview sizes. |
| Palette-indexed raster with a method other than `nearest` | Fail that file before deleting anything. Retry with `nearest`, or expand a separate copy to RGB. |
| Invalid TIFF, unsupported TIFF, or corrupt input | Record a per-file failure and continue. |
| Symlink, multiply hard-linked TIFF, or subdirectory | Ignore during discovery and note relevant exclusions in the text log. |

For an eligible `image.tif`, cleanup targets only these derived paths:

```text
image.tif.ovr
image.tif.ovr.aux.xml
image.tif.ovr.msk
image.tif.ovr.msk.aux.xml
image.tif.msk.ovr
image.tif.msk.ovr.aux.xml
```

The base raster, `image.tif.aux.xml`, and the base mask `image.tif.msk` are not
deleted. Removing embedded overviews modifies TIFF metadata/directory structures;
it does not intentionally resample or rewrite the base-resolution pixels. GDAL
does not reclaim the abandoned internal-overview space, so the source TIFF
usually does **not** shrink. Converting it to a new TIFF would be a separate job.

External `.aux` / RRD pyramids and custom PAM overview references are not deleted.
If GDAL still exposes overviews after supported cleanup, the file fails with an
explanation. Such an input may already have had its `.ovr` or internal pyramids
removed. Resolve the alternate pyramid reference manually before retrying.

COG detection uses GDAL's `LAYOUT=COG` metadata. It is protection for recognized
COGs, not a complete structural validator. If you want to convert a COG, first
produce a separate ordinary GTiff copy and run this utility on that copy.

Use copies for an initial trial, especially when embedded overviews are present.
Close other applications using the inputs, avoid editing the directory during a
run, and avoid adding these rasters back to QGIS until the run finishes. The
loaded-layer check cannot identify every indirect reference through a VRT,
another project, another QGIS instance, or another application.

## 3. Parameters

Pass these as keyword arguments to `start()` or `run()`.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `directory` | Required | Flat local/UNC directory. No recursive search. |
| `resampling` | `"bilinear"` | `bilinear`, `nearest`, `average`, `cubic`, `cubicspline`, `lanczos`, `mode`, `gauss`, or `rms`. |
| `storage` | `"auto"` | `auto`, `hdd`, `network`, `ssd`, or `nvme`; selects a starting worker limit. It does not detect drive hardware. |
| `workers` | `None` | Maximum concurrent files. A positive integer overrides the storage preset. |
| `threads_per_file` | `None` | GDAL threads allowed per active file. Automatically selected when omitted; use `1` for parallelism primarily across files. |
| `levels` | `None` | Explicit integer reduction factors, e.g. `[2, 4, 8, 16, 32]`. Otherwise choose powers of two automatically. |
| `min_size` | `256` | Automatic pyramids stop when the largest overview dimension is at most this size. Ignored when explicit levels are supplied. |
| `compression` | `"DEFLATE"` | `DEFLATE`, `LZW`, or `NONE`. |
| `compression_level` | `1` | DEFLATE level, 1–9. Level 1 favors speed. Ignored by the other compression methods. |
| `bigtiff` | `"YES"` | `YES`, `IF_SAFER`, `IF_NEEDED`, or `NO`. `YES` avoids the classic TIFF 4 GiB limit. |
| `remove_internal` | `True` | Remove embedded overviews from ordinary TIFFs. Set `False` to skip those files. |
| `dry_run` | `False` | Inspect metadata and write a plan; do not delete or build overviews. |
| `progress_interval` | `5.0` | Seconds between progress reports, minimum 0.5. |
| `log_dir` | `None` | Defaults to an `overview-generator-logs` subdirectory of the input directory. |
| `min_free_gb` | `1.0` | Free-space headroom, in binary GiB, in addition to estimated in-flight output reservations. |

TIFF extension matching is case-insensitive: `.tif`, `.tiff`, and `.geotiff`.
Files must open through GDAL's GTiff driver. There is no requirement for a CRS or
geotransform merely to create a pyramid. Multi-page TIFF subdatasets are not
expanded into separate jobs: the default dataset is processed.

### Automatic levels

For a 10,000 × 6,000 raster and `min_size=256`, factors are
`2, 4, 8, 16, 32, 64`. The last overview is 157 × 94 pixels. Dimensions use
ceiling division. The larger dimension controls stopping, so narrow rasters can
end with a one-pixel-wide overview.

Explicit factors are sorted and deduplicated. Factors that produce identical
pixel dimensions are also deduplicated. They must be integers from 2 through
2,147,483,647. All image bands are processed.

### Resampling choice

Bilinear is the requested default. For categorical data, `nearest` preserves
sampled class values; `mode` is another option when dominant-class aggregation
is wanted. The utility deliberately requires `nearest` for palette-indexed
inputs. Floating-point imagery or elevation can use bilinear or average,
depending on the desired display behavior.

NoData values, masks, alpha, and interpolation are handled by the installed GDAL
version. The script does not replace NoData, alter the base raster's data type,
or invent missing mask metadata. Verify representative masked/alpha rasters in
your own QGIS build: mask-overview behavior varies with GDAL version and storage
layout. Image-band dimensions are verified; mask pyramids and every output pixel
are not exhaustively checked.

## 4. Parallelism, CPU, memory, and I/O

The script uses a `ThreadPoolExecutor`. GDAL's native work can run concurrently
while Python coordinates it. Each worker opens its own dataset; dataset and
band objects are never shared across workers. Qt widgets and QGIS project state
are accessed only on the main thread. A Qt timer delivers console messages
there, avoiding GUI calls from the worker threads.

This design runs inside the existing QGIS process. It avoids the executable and
module-import problems of spawning Python multiprocessing workers from the QGIS
console on Windows. It does **not** isolate a native GDAL crash from QGIS.

For automatic settings, the CPU budget is `max(1, logical_CPU_count - 1)`:

| Storage preset | Automatic maximum concurrent files |
| --- | ---: |
| `hdd` | 1 |
| `network` | 2 |
| `auto` or `ssd` | 4 |
| `nvme` | 8 |

These limits are capped by the CPU budget. Automatic GDAL threads per file are
`min(4, max(1, CPU_budget // workers))`. Explicit values override the defaults.
The product of workers and GDAL threads is a planning guide, not a hard OS
thread limit: libraries can create auxiliary threads, and not every GDAL phase
uses all requested threads.

The scheduling strategy is fixed during a run:

- Scan the directory once, then start the largest source files first.
- Keep at most `workers` futures in flight; submit another when one finishes.
- Disable repeated GDAL directory listings while keeping sidecar lookup enabled.
- Use lossless, tiled overviews, 256 × 256 blocks, and band interleaving.
- Apply GDAL tuning options locally to each worker thread and restore them.
- Leave QGIS's global GDAL cache size and exception policy unchanged.

The script keeps paths and file sizes for the input list, plus a bounded amount
of active-job state. Raster pixels remain under GDAL's control. Results are
streamed to disk instead of accumulating a large result list in memory. GDAL's
block cache is shared with QGIS; per-file working buffers and compression buffers
add memory usage. There is no hard RAM limit or automatic RAM measurement.

### Tune for the actual workstation

There is no universally optimal worker count for an unknown combination of
storage, compression, band count, raster dimensions, RAM, and CPU. The presets
are starting points, not a benchmark claim or a live autotuner.

Try a representative subset with different settings and compare elapsed time:

```python
# HDD: limit concurrent seeks; allow a little compute parallelism.
job = og["start"](r"D:\GeoTIFFs", storage="hdd", threads_per_file=2)

# SSD: a practical starting configuration.
job = og["start"](r"D:\GeoTIFFs", workers=4, threads_per_file=2)

# Many modest files on a fast NVMe drive, with sufficient CPU and RAM.
job = og["start"](r"D:\GeoTIFFs", workers=8, threads_per_file=1)

# Few very large files: try more GDAL threads and fewer active files.
job = og["start"](r"D:\GeoTIFFs", workers=2, threads_per_file=4)
```

Run these examples separately, after the preceding job finishes. On an 8 GiB
machine or with very wide/many-band rasters, begin with 1–2 workers. If disk
latency increases without higher throughput, reduce workers. If CPU is busy
compressing but storage is lightly used, compare level-1 DEFLATE against LZW or
uncompressed overviews. Uncompressed output trades disk space and I/O for less
compression work. `ALL_CPUS` is deliberately not an accepted parameter: use an
explicit integer to make nested concurrency predictable.

Python-level file sizes are used for scheduling, not a complete upfront GDAL
metadata scan. This keeps startup light for thousands of files but means a
highly compressed, computationally expensive raster may look deceptively small.

## 5. Disk space and BigTIFF

Each file is checked for available space **before its existing overviews are
deleted**. The estimate uses all requested overview dimensions, padded to tile
boundaries, sample sizes, mask allowance, 15% overhead, and 16 MiB of fixed
headroom per active file. Concurrent jobs reserve their estimated output space
under a lock. The configured `min_free_gb` is added on top.

This is a conservative estimate, not a storage guarantee. It can reject a run
that would fit after compression. It does not count future savings from deleting
old sidecars; it can also partly double-count reservations once another worker
has written some of its output. Other applications can consume space after the
check. A dry run reports size estimates but does not reserve space or verify
every later write permission.

A long 2× pyramid contains roughly one-third as many pixels as the original,
before tile padding and masks. That ratio is **not** a reliable fraction of the
source file's compressed size. For example, a small compressed source can have
much larger losslessly compressed overviews than expected.

BigTIFF is enabled by default for every output, including small ones. It is
readable by QGIS/GDAL and avoids relying on a compressed-size prediction near
4 GiB. Choose `bigtiff="IF_SAFER"` if you prefer classic TIFF for smaller outputs.
Avoid `"NO"` when any individual pyramid could approach 4 GiB.

## 6. Progress and completion time

The console prints periodic lines resembling:

```text
processing | 327/2500 files | 18.4% by source bytes | active 4 | OK 326 / failed 1 / skipped 0 / planned 0 / cancelled 0 | elapsed 00:12:20 | ETA 00:54:43 | finish ~2026-09-24 16:28:03 (local)
```

The line includes terminal file counts, active workers, source-byte-weighted
progress, elapsed time, estimated time remaining, and an approximate completion
timestamp in the workstation's local time.

An active file contributes its source file size multiplied by GDAL's progress
fraction. The last 1% is reserved for closing and checking output. Finished,
failed, skipped, and planned files count as settled work; therefore reaching
100% means the queue has been handled, not that every file succeeded.

ETA uses successful-source bytes plus active-file weighted progress divided by
processing time. It starts displaying after at least five seconds of useful
progress. It is a rough cumulative estimate and can change sharply when file
types or compression ratios differ, or when a large file fails. Cleanup,
metadata reads, final flushing, network delays, and verification are not
proportional to GDAL's callback fraction. For a dry run, ETA generally stays
`calculating` until completion because no raster-processing throughput exists.

`source_equivalent_mib_per_second` in `job.status()` is an estimated processing
rate expressed in source-file bytes. It is **not measured physical disk I/O**.
Updates continue while a large file is processing, although GDAL may pause its
callback during some phases.

Inspect status without waiting:

```python
s = job.status()
print(s["counts"])
print(s["active_files"])    # File path -> estimated file progress percent
print(s["eta_seconds"])
print(s["fatal_error"])
```

Final phases are `finished`, `finished_with_errors`, `cancelled`, or `failed`.
`finished` can include skipped files. Check counts and reasons if you need an
overview for every input. Symlinks, hard links, and directory entries excluded
during discovery are outside the result totals.

## 7. Logs and reports

Every run has a timestamp and a random suffix to avoid filename collisions.
By default the script creates:

```text
GeoTIFFs/
  image.tif
  image.tif.ovr
  overview-generator-logs/
    <run-id>.log
    <run-id>.results.jsonl
    <run-id>.summary.json
```

- **`.log`**: settings, GDAL version, source directory, progress reports,
  per-file results, warnings, and errors.
- **`.results.jsonl`**: one JSON object per discovered file. It records status,
  levels, dimensions when known, deleted sidecars, embedded-overview removal,
  elapsed time, warnings, errors, and output size. Unstarted files on normal
  cancellation are recorded as cancelled. An early fatal error can leave the
  result log incomplete.
- **`.summary.json`**: final state, aggregate counts, settings, timing, source
  size, aggregate output estimates, and successful main `.ovr` bytes. Output
  byte totals exclude ancillary sidecars.

Results are flushed after each completed file. This reduces loss of diagnostic
information, but it is not a filesystem `fsync` guarantee against power loss.
Only the first ten per-file failures are echoed individually to the console;
all failures are recorded in the logs and counted in progress reports.

Example: list failures and skips after the run:

```python
import json
with open(job.results_path, encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        if r["status"] in ("failed", "skipped"):
            print(r["status"], r["path"], r.get("error") or r.get("reason"))
```

For a dry run, `planned` entries include `existing_sidecars`,
`would_remove_internal`, `levels`, and `estimated_output_bytes`. Inspect the
summary's `estimated_output_bytes` for the aggregate conservative estimate.
Dry runs still create diagnostic files and a temporary directory lock; they
do not alter TIFFs or their overview sidecars.

## 8. Cancellation, reruns, and locks

From the QGIS console:

```python
job.cancel()
```

This is cooperative. New files stop being submitted; active workers return
through GDAL's progress callback and close their dataset handles. Their partial
external output is removed where possible. Files that completed successfully
remain available. Check `job.done` or wait for the final console message before
starting another run or closing QGIS.

Cancellation is not instantaneous: a GDAL call, a blocked network operation, or
an output flush may take time to return. Do not block QGIS's main thread with a
polling loop or call the blocking `run()` API in the GUI. Keep QGIS open until
the job finishes. Closing QGIS requests cancellation but is not a substitute
for waiting for orderly completion.

Old overviews that were already deleted are not restored. Embedded overview
removal also cannot be undone by cancellation. If a GDAL failure happens after
output creation starts, the script attempts to delete partial `.ovr` sidecars
and records cleanup failures. A process crash or power interruption can still
leave an incomplete pyramid or an interrupted TIFF metadata update.

The file `.overview-generator.lock` prevents two instances of this script from
working in the same directory simultaneously. It contains the process ID,
hostname, run ID, and start time. A normal finish releases it. It is not an
operating-system lock on every input and does not stop unrelated applications.
Atomic exclusive-file creation on a network share depends on the share's normal
filesystem semantics.

After an abnormal exit, inspect the lock and confirm that the recorded run is
no longer active before deleting it manually. The script never guesses that a
lock is stale. A rerun rebuilds all eligible files again; there is no automatic
resume or missing-only mode.

## 9. Command-line use (optional)

Use a shell in which Python can import the same GDAL bindings, for example an
appropriately configured OSGeo4W/QGIS environment. A random system Python
installation may not have GDAL or the matching native libraries.

```bat
python overview-generator.py "D:\GeoTIFFs" --storage ssd --dry-run
python overview-generator.py "D:\GeoTIFFs" --workers 4 --threads-per-file 2
python overview-generator.py "D:\GeoTIFFs" --resampling average --levels 2 4 8 16 32
python overview-generator.py "D:\GeoTIFFs" --keep-internal --compression LZW
python overview-generator.py --help
```

`--keep-internal` is the CLI equivalent of `remove_internal=False`. The other
keyword names use hyphens instead of underscores on the command line.
Ctrl+C requests cancellation and waits for active writes to close. Exit codes:
`0` for a finished run (possibly with skips), `1` for processing/fatal errors,
`2` for invalid arguments, and `130` for cancellation.

The blocking Python API returns the final status dictionary:

```python
import runpy
og = runpy.run_path("overview-generator.py")
summary = og["run"]("D:/GeoTIFFs", workers=4, threads_per_file=1)
```

## 10. Compatibility and validation

The implementation targets QGIS 3.28 with **Python 3.7+ and GDAL 3.4+**.
QGIS distributions can bundle different GDAL versions; the script checks the
GDAL version at startup. It does not depend on recent GDAL 3.10 thread-safe
dataset APIs, new command-line utilities, or GDAL 3.8's explicit dataset close
API. Dataset references are released in the compatibility style used by older
bindings.

Check the actual workstation's GDAL version in QGIS:

```python
from osgeo import gdal
print(gdal.VersionInfo("RELEASE_NAME"))
```

Workstation validation on 2026-09-24 used Windows 11,
QGIS **4.2.0**, Python **3.12.13**, and GDAL **3.13.1**. QGIS 3.28 was not found
in the checked installation locations or installed-app records, so the original
3.28 / Python 3.7 / GDAL 3.4 runtime target remains unverified.

All 12 command-line integration checks passed using actual GDAL and generated
rasters. Coverage includes Byte, Float32/NoData, three bands, no/external/embedded
overviews, dry-run preservation, bilinear and average, two workers, rebuilding,
corrupt input, native Windows Ctrl+C cancellation, and dimensions/pixel values.
Seven separate GUI checks passed in a standalone `QgsApplication` hosting the
real QGIS Python console widget; three additional Qt event-loop API checks passed.
The full QGIS desktop could not launch because Windows Application Control
blocked its application DLL. No security policy was changed.

No implementation defect was demonstrated, so `overview-generator.py` remains
byte-for-byte identical to the attachment. One test reference was corrected:
on odd-sized Float32 rasters, compare external overview output with direct GDAL
external overviews, because internal and external GDAL paths can differ slightly.
The affected check and the complete CLI suite passed on rerun.

See [validation-report.md](validation-report.md), the [test guide](tests/README.md),
and saved JSON results in `validation/` for exact coverage and limitations. These checks are synthetic validation,
not a production-data, performance, or QGIS 3.28 compatibility certification.

### Small real-GDAL smoke test in QGIS

The following creates a separate temporary folder and a small synthetic TIFF;
it does not use your production data:

```python
import tempfile
from pathlib import Path
from osgeo import gdal

test_dir = Path(tempfile.mkdtemp(prefix="overview-generator-test-"))
test_tif = test_dir / "sample.tif"
ds = gdal.GetDriverByName("GTiff").Create(
    str(test_tif), 1024, 768, 1, gdal.GDT_Byte,
    options=["TILED=YES", "COMPRESS=DEFLATE"])
ds.GetRasterBand(1).Fill(42)
ds.SetGeoTransform((0, 1, 0, 768, 0, -1))
ds = None

test_job = og["start"](str(test_dir), workers=2, threads_per_file=1)
```

After `test_job.done` is `True`:

```python
print(test_job.status()["counts"])   # Expected: {'ok': 1}
assert Path(str(test_tif) + ".ovr").is_file()
ds = gdal.Open(str(test_tif))
b = ds.GetRasterBand(1)
print([(b.GetOverview(i).XSize, b.GetOverview(i).YSize)
       for i in range(b.GetOverviewCount())])
# Expected: [(512, 384), (256, 192)]
print(b.GetOverview(0).ReadRaster(0, 0, 1, 1))   # Expected: b'*' (value 42)
b = None
ds = None
```

Run the job a second time to test replacement. Then try copies of representative
production rasters, including floating-point, NoData, masks, multiband, and
embedded-overview cases. Load the outputs in QGIS after completion and inspect
them at several zoom levels. The runtime validation checks that `.ovr` exists,
is nonempty, and exposes exactly the expected overview dimensions for every
image band. It does not compute source hashes or scan every output pixel.

## 11. Troubleshooting

| Symptom | What to do |
| --- | --- |
| `No module named osgeo` | Run from QGIS's Python console or a correctly initialized GDAL Python environment. |
| `start() must run on the QGIS GUI thread` | Call `start()` directly in the console. In a terminal, use `run()` or the CLI. |
| Loaded input layers reported | Remove those layers from the project, wait for rendering to stop, and retry. Dry-run inspection is allowed while layers are loaded. |
| `PermissionError`, sharing violation, or cannot delete `.ovr` | Close applications holding the input or sidecars; check folder permissions and read-only attributes. |
| Embedded cleanup fails | The TIFF may be read-only, locked, damaged, or structurally unsupported. Review the log; use `remove_internal=False` to skip it. |
| Directory lock exists | Check the recorded process/host. Remove the lock only if that run has stopped. |
| `Insufficient free space` | Free space, choose another working copy location, or inspect the conservative estimates. Lowering headroom is possible but does not remove the output's space requirement. |
| High RAM consumption or slow desktop | Reduce workers and GDAL threads; close unrelated large QGIS layers. |
| HDD at 100% activity but little throughput | Use `storage="hdd"` or `workers=1`. Avoid competing disk workloads. |
| ETA fluctuates | Expected for heterogeneous or highly compressed data. Compare the active-file progress and the log. |
| 100% progress but missing outputs | Check failed/skipped counts and per-file reasons; progress measures settled queue work. |
| Palette-indexed TIFF fails | Use `resampling="nearest"` or convert a copy to RGB before using bilinear. |
| Process stops or native GDAL crashes | Review the last logged file, release a stale lock only after confirming termination, and retry that file separately. Native crashes cannot be caught by Python. |
| No files found | Check the directory path and supported extensions; nested folders are not scanned. |

## 12. Technical references

The implementation uses the established read-only GTiff overview-building path
to create external pyramids and supplies configuration options supported by
older GDAL releases. Current documentation may also describe newer options that
this script intentionally does not use.

- [GDAL gdaladdo: external overviews, resampling, cleaning, and threading](https://gdal.org/en/stable/programs/gdaladdo.html)
- [GDAL GTiff driver: TIFF overviews and configuration](https://gdal.org/en/stable/drivers/raster/gtiff.html)
- [GDAL multithreading: separate dataset handles](https://gdal.org/en/stable/user/multithreading.html)
- [GDAL configuration options: thread-local settings and directory scanning](https://gdal.org/en/stable/user/configoptions.html)
- [GDAL Python configuration API](https://gdal.org/en/stable/api/python/general.html)
