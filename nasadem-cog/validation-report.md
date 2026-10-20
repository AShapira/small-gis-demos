# NASADEM merger validation — 2026-10-20

Validated with native **Windows QGIS 4.2**, Python **3.12.13**, GDAL **3.13.1**,
and NumPy **2.4.6**. The command-line tool read local test inputs and wrote
outputs through a Windows UNC path to the WSL workspace. This exercised the
actual Windows GDAL runtime, filesystem access, and directory lock.

## Synthetic tests

All **33 tests passed**. They cover:

- 5°, 10°, and 20° grids, all coordinate quadrants, native 3,601-sample source
  dimensions, shared boundaries, source order, and overlapping valid/NoData data.
- Zero and negative elevations, explicit masks, GDAL's source-NoData precedence,
  partial source windows, relative/absolute paths, spaces, and Unicode.
- Missing and corrupt inputs, filtering, minimum tile counts, invalid grid/CRS/
  NoData metadata, accumulated pixel-spacing drift, and rejection of VRT scaling.
- Exhaustive pixel verification, deliberate pixel corruption, average overview
  values, and rejection of stale/incorrect source overviews as a new overview
  reference.
- Resume, stronger verification on resume, recovery after publication but before
  checkpointing, input/option mismatch, unrelated outputs, locking, and repeated
  downloads retaining identical input timestamps.
- Disk exhaustion, reduced concurrency under a limited disk reservation,
  source changes during conversion, and cancellation during conversion or
  verification.
- A **real Windows Ctrl+C** sent only to an isolated test console, followed by a
  successful CLI resume.

The test suite is added to the existing GDAL GitHub Actions job. Remote CI was
not run during this local task.

## Downloaded real-data subset

Downloaded the official **6,472,343-byte VRT**, containing **14,520 source
references**, and selected **160 whole 1° tiles** with southwest origins at
20..39° E and 30..39° N. The tile download totals **1,398,108,887 bytes**.
The subset spans two adjacent 10° blocks across the eastern Mediterranean,
including Israel, and includes ocean/coastal gaps.

Local artifacts:

- Full official VRT: `.validation/public-subset/NASADEM_be.vrt`.
- Downloaded TIFFs: `.validation/public-subset/NASADEM_be/`.
- Filtered VRT preserving original source order and mappings:
  `.validation/public-subset/NASADEM_e20-e40_n30-n40.vrt`.
- Source URLs, sizes, and SHA-256 hashes:
  `.validation/public-subset/download-manifest.json`.

All **160 downloaded TIFFs retained their original SHA-256 hashes** after
processing. No download was required during merger execution.

## Full-size COG results

The native CLI ran with 10° blocks, two workers, exhaustive verification, and
internal average overviews.

| Output | Nominal tiles | Dimensions | File bytes | Verified base pixels |
| --- | ---: | ---: | ---: | ---: |
| `NASADEM_N30E020_N39E029.tif` | 73 | 36,001 × 36,001 | 353,359,322 | 1,296,072,001 |
| `NASADEM_N30E030_N39E039.tif` | 87 | 36,001 × 36,001 | 634,711,808 | 1,296,072,001 |

Both outputs passed independent comparison of **all 2,592,144,002 base pixels
and validity flags** against the original TIFFs. Full COG structural checks
reported **zero errors and zero warnings**. Both have seven internal overview
levels: 18,000, 9,000, 4,500, 2,250, 1,125, 562, and 281 pixels per side.
The installed COG driver rounds these odd-sized reductions down.

An additional independent boundary check compared all **36,001 shared samples**
at 30° E. Values and validity flags matched exactly: 21,602 valid samples and
14,399 NoData samples. The computed coordinate difference was approximately
2.49 × 10⁻¹⁴ degrees, attributable to floating-point arithmetic.

Creation and exhaustive verification completed in **274.5 seconds** on this
machine and storage path. A second real-data run exhaustively reverified both
files and **skipped them without rewriting** in **28.125 seconds**; output sizes
and modification times were unchanged. These are observations from one local
run, not a performance guarantee.

During generation, a second native Windows process was correctly refused
access to the locked output directory.

## Evidence and limits

The resulting COGs are in `.validation/real-10deg/`. Local evidence includes:

- `.validation/native-tests.log`
- `.validation/real-dry-run.json`
- `.validation/real-final-preflight.json`
- `.validation/real-run.log` and `.validation/real-first-state.json`
- `.validation/real-resume.log`
- `.validation/concurrent-run-rejected.log`
- `.validation/source-integrity.json` and `.validation/shared-edge.json`
- `.validation/validation-summary.json`, including output SHA-256 hashes

Generated data and evidence are ignored by Git. Syntax parsing and
`git diff --check` passed. This validates the downloaded two-block subset and
generated fixtures, not a complete global NASADEM conversion or the user's
separate `H:` dataset. The QGIS GUI was not required for this shell utility.
