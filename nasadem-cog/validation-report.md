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

## Block-size extension — 2026-10-21

Validated the divisor-based grid with native Windows QGIS 4.2, Python 3.12.13,
GDAL 3.13.1, and NumPy 2.4.6. All **38 tests passed in 14.749 seconds**,
including the existing Windows Ctrl+C/resume test. New coverage checks:

- Every positive integer divisor of 360, symmetric continuous longitude and
  latitude bands, polar clipping, dimensions, and georeferencing.
- Invalid CLI and direct-call sizes rejected before inventory/output creation.
- Unchanged 5°/10°/20° boundaries, including the polar blocks, and resume.
- Creation, exhaustive verification, overviews, and resume of a **721 × 361**
  synthetic whole-world COG: all **260,281 pixels** checked against the source
  TIFFs, including both dateline endpoints, both poles, NoData, and source order.
- Expansion of a regional **5 × 3** VRT into a **721 × 361** whole-world COG,
  again checking all pixels and the NoData padding.

The downloaded **160-tile** subset was reused for native-resolution dry runs
with overviews enabled. Every source belonged to exactly one nominal block;
all planned blocks were eligible, and no output directories were created.

| Block size | Planned outputs for this subset | Dimensions per output |
| --- | ---: | --- |
| 30° | 2 | 108,001 × 108,001 |
| 45° | 1 | 162,001 × 162,001 |
| 60° | 1 | 216,001 × 216,001 |
| 90° | 1 | 324,001 × 324,001 |
| 120° | 1 | 432,001 × 432,001 |
| 180° | 1 | 648,001 × 324,001 |
| 360° | 1 | 1,296,001 × 648,001 |

The 360° output is `NASADEM_S90W180_N89E179.tif`, with sample bounds
`(-180, -90, 180, 90)`. Its existing conservative storage calculation reserves
**6,718,487,155,740 bytes** with overviews, plus the separate 1 GiB headroom.
These large native-resolution outputs were planned only; whole-world generation
was exercised using the synthetic fixtures above.

For an independent compatibility check, the committed merger at
`6cc5842209b660995af51bd5a83132acd89e2721` created four synthetic COGs for each
legacy size (5°, 10°, and 20°), including polar and equator-adjacent inputs.
The updated merger exhaustively verified and skipped all **12 outputs** on
resume. Their SHA-256 hashes, modification times, and checkpoint signatures
were unchanged.

Evidence is retained under ignored `.validation/block-sizes/`:
`native-tests-final.log`, `real-*-dry-run.json`, `legacy-*-create.log`,
`legacy-*-resume.log`, `real-plans-and-legacy-resume.log`, and
`validation-summary.json`. Python 3.9 syntax parsing and `git diff --check`
also passed. Remote CI has not been run for this extension.

## Compressed storage estimates and optional stopping — 2026-10-21

All **43 tests passed in 15.033 seconds** under native Windows QGIS 4.2,
Python 3.12.13, and GDAL 3.13.1. Added checks cover:

- Warning and successful generation with a mocked zero-free-space result,
  including multiple blocks and later strict-policy resume without rewriting.
- Opt-in stopping with `--disk-space-policy error`, and warning/error handling
  when the filesystem free-space query is unavailable.
- Simulated actual write failures still returning a failure and checkpointing
  the affected block; no physical disk was filled for this test.
- Duplicate source references, partial source windows, source overview size
  adjustment, compressed global NoData padding, and unchanged source files.

The new estimate was compared with the existing two 10° COGs, which contain
internal overviews. These files and their 160 source TIFFs were reused without
regeneration. Sizes below use decimal GB and exclude the separate 1 GiB disk
headroom:

| Output | Actual COG | Estimated COG | Estimated temporary space | New working reservation | Previous reservation |
| --- | ---: | ---: | ---: | ---: | ---: |
| `NASADEM_N30E020_N39E029.tif` | 0.353 GB | 0.498 GB | 0.166 GB | 0.731 GB | 10.369 GB |
| `NASADEM_N30E030_N39E039.tif` | 0.635 GB | 0.911 GB | 0.304 GB | 1.337 GB | 10.369 GB |
| Total for both concurrent blocks | **0.988 GB** | **1.410 GB** | **0.470 GB** | **2.068 GB** | **20.737 GB** |

The output estimates are approximately 41–44% above the measured files in this
subset. The source-size proxy does not predict the exact effect of the output
predictor, compression ratio, or overview content. The working reservation adds
temporary overview space and a 10% margin; it no longer multiplies the full raw
geographic rectangle by three. Temporary overview storage follows the
[GDAL COG CreateCopy model](https://gdal.org/en/stable/drivers/raster/cog.html).

Metadata-only plans also covered 90° and 360° outputs using these same 160 tiles.
Their uncovered geographic area was estimated as compressed NoData. This is
not an estimate measured against the full global NASADEM collection, and no
new native-resolution global COG was generated.

Both `warn` and `error` accepted the existing real-data checkpoint's signature
and block inventory. This compatibility check only read the checkpoint; its
bytes remained unchanged. The policy and estimate fields are operational
metadata and do not change the output identity.

Evidence is retained under ignored `.validation/storage-estimates/`:
`native-tests.log`, `real-estimates.log`, and `validation-summary.json`.
Python 3.9 syntax parsing and `git diff --check` passed. Remote CI has not been
run for this change.
