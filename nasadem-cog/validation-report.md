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

## Float32 SRTM extension — 2026-10-27

Native Windows QGIS 4.2 validation used Python 3.12.13, GDAL 3.13.1 and NumPy
2.4.6. All **50 tests passed** (43 existing tests and 7 added tests). The new
cases exercise fractional/negative Float32 values, one-ULP corruption detection,
finite fractional NoData, masks, shared edges, mixed-type rejection, overviews,
prefix validation, resume, and the unchanged default Int16 configuration identity.

A real-data test used OpenTopography SRTM GL1 Ellipsoidal tiles
`N33E036_wgs84.tif` and `N33E037_wgs84.tif`. A 2° output covering 36–38° E,
32–34° N (including uncovered NoData) was generated with Float32, DEFLATE,
predictor 3, 512-pixel blocks, and four internal average overviews.

| Output | Dimensions | Bytes | Exact base-pixel checks |
| --- | --- | --- | --- |
| `SRTM_GL1_Ellip_N32E036_N33E037.tif` | 7,201 × 7,201 | 38,168,710 | 51,854,401 |

Every output base value and validity flag matched the independent composition
of original tiles. The full GDAL COG validator reported no errors or warnings.
The output retained the input VRT's explicit `VERTICAL_DATUM=WGS84 ellipsoid`
metadata. Height values were not transformed.

Evidence is under ignored `.validation/srtm-float32/`: `tests.log`,
`real-two-tiles.vrt`, `real-merge.log`, and `real-cog/state.json`.
This section records native GDAL validation, not a QGIS desktop rendering test.
Release CI results are tracked separately in GitHub Actions.

The requested Israel/Lebanon/Syria/Jordan collection was also checked locally:
49 tile positions selected by intersection with Natural Earth 1:10m country
polygons, with zero missing references in a native merger dry run. All
635,392,849 effective source pixels decoded successfully, downloaded SHA-256
hashes matched readback, and interior windows from all 49 sources matched
the regional VRT. The provider's multipart placeholder ETags were not treated
as content checksums.

One provider tile, `N32E038_wgs84.tif`, contains only zeros and lacks NoData.
The original remains unchanged in the local data folder. The curated VRT uses
a separately documented replacement derived from standard SRTM GL1 and the
OSGeo EGM96 15-minute grid. All 12,967,201 replacement values matched an
independent `h = H + N` calculation after Float32 rounding; four neighboring
published samples agreed within 0.000032 m. This data repair is separate from
the merger: its source-type and explicit-NoData requirements remain enforced.
The dataset's manifest and `provenance/repair/` retain the evidence; downloaded
rasters are retained outside the repository and are not included in the release.

## Previous-result consolidation and stage VRTs — 2026-11-02

The native QGIS suite passed **66/66 tests** with Windows QGIS 4.2,
Python 3.12.13, and GDAL 3.13.1: the existing 50 tests plus 16 consolidation
and stage-VRT regressions. New coverage includes rectangular rasters, shared
samples and corners, gaps, exact Float32 conflict detection, fractional NoData,
fresh internal average overviews, BigTIFF, filtering, and input incompatibilities.
It also covers cancellation/resume, input additions during conversion, disk
policy, VRT ownership collisions, and recovery after VRT publication but before
the final checkpoint.

A generated four-block Float32 stage was moved with its relative-path VRT.
The original tiles, original VRT, original output directory, and copied state
were removed from the isolated fixture. The moved mosaic remained readable;
consolidation preserved every expected sample and left input TIFF hashes and
modification times unchanged. Additional tests confirm that stage VRTs exclude
below-minimum blocks, are regenerated for compatible legacy states without
rewriting COGs, and are omitted for single-output and incomplete stages.

Real-data acceptance used the retained, previously merged
`SRTM_GL1_Ellip_N32E036_N33E037.tif` directly through `--merge-results-dir`.
No original source tiles or original VRT were used by consolidation.

| Output | Dimensions | Bytes | Exact base-pixel checks | Internal overviews |
| --- | --- | --- | --- | --- |
| `SRTM_GL1_Ellip_merged.tif` | 7,201 × 7,201 | 38,170,597 | 51,854,401 | 3,600; 1,800; 900; 450 pixels per side |

The output retained Float32 elevations and `VERTICAL_DATUM=WGS84 ellipsoid`.
Full COG structural validation reported zero errors and zero warnings. A second
run exhaustively verified the output again and skipped rewriting it: its
SHA-256, size, and modification time were unchanged. All files in the input
directory also retained their hashes, sizes, and modification times. The final
directory contains no accompanying VRT or external overview file.

Evidence is retained under ignored `.validation/nasadem-consolidation/`:
`full-tests.log`, `real-dry-run.json`, `real-validation.log`,
`real-validation.json`, and `real-cog/state.json`. This records native GDAL
validation, not a QGIS desktop rendering test. The existing Linux GDAL CI job
discovers the new tests; remote CI was not run as part of this change.

## Exact embedded statistics — 2026-11-02

The native QGIS suite passed **76/76 tests** on Python 3.12.13 / GDAL 3.13.1,
including ten new statistics regressions. Coverage includes full-resolution
Float32 outliers, deliberately stale input statistics and overviews, fractional
NoData, Int16 seams/gaps, constant and all-NoData rasters, and a single valid
sample. Tests also cover source band-metadata preservation, consolidation of
COGs with differing statistics, cancellation during the statistics scan,
legacy resume, missing statistics on new outputs, and exhaustive statistics
verification. Statistics tests use independently calculated means and population
variances with `math.fsum`; extrema and pixel counts must match exactly.

Real acceptance regenerated a new COG from the retained Float32 result and
independently checked every one of its **51,854,401** base pixels. Embedded
statistics were:

| Statistic | Value |
| --- | ---: |
| Minimum | 575.8612670898438 |
| Maximum | 2533.906005859375 |
| Mean | 855.0728031064002 |
| Population standard deviation | 311.4008575525728 |
| Valid pixels | 25,930,801 |
| NoData pixels | 25,923,600 |
| Valid percentage | 50.00694348007221 |

An additional GDAL `ComputeStatistics(False)` calculation on an in-memory copy
matched both extrema exactly. Mean and standard deviation agreed within the
documented `1e-12` relative/absolute tolerance; small numerical differences
reflect floating-point accumulation order. The finished TIFF is 38,171,165
bytes, retains four internal overview levels and WGS84 ellipsoidal provenance,
and passes full COG validation with zero errors or warnings. No statistics
sidecar is required. A second exhaustive run left its SHA-256, size, and
modification time unchanged; all input-directory files also remained unchanged.

Evidence is under ignored `.validation/nasadem-statistics/`: `full-tests.log`,
`real-dry-run.json`, `real-validation.log`, `real-validation.json`, and
`real-cog/state.json`. This is native GDAL validation; remote CI and QGIS desktop
rendering were not exercised for this change.

## Provider SRTM VRT overview declaration — 2026-11-02

Downloaded the current public
[`SRTM_GL1_Ellip_srtm.vrt`](https://opentopography.s3.sdsc.edu/raster/SRTM_GL1_Ellip/SRTM_GL1_Ellip_srtm.vrt)
(6,641,364 bytes; SHA-256
`4fd062ea3d89444e0ffc4cfa39f1d7dc38257405dd6c9ccd37a23980248d7120`).
It describes one Float32 band, 1,296,001 × 417,601 samples, NoData -32768,
and 14,280 relative `ComplexSource` tile references. Its final dataset child is
`<OverviewList resampling="nearest">2 4 8</OverviewList>`.

Native QGIS reproduced the original `Unsupported VRT dataset element` error
before any tile inspection. The parser allowlist omitted this valid GDAL
virtual-overview declaration. It now accepts and ignores the declaration when
rebuilding block VRTs, retaining the existing full-resolution composition and
fresh output-overview policy. Unsupported dataset elements remain errors and
are now named in the message.

All **78/78 tests** passed on Windows QGIS 4.2 / Python 3.12.13 / GDAL 3.13.1.
Regression coverage includes Int16/Float32 base values, unchanged input VRTs,
no unsolicited output overviews, rejection of dataset masks/GCPs/unknown XML,
and fresh average overviews despite deliberately incorrect source overviews.

Real acceptance used the unchanged downloaded VRT with a filename filter for
the retained `N32E036_wgs84.tif` and a 1° output block. All **12,967,201** base
pixels passed exhaustive merger verification and an independent NumPy equality
comparison against the source. The provider VRT's 3,599 × 3,599 tile interior
also matched independently; border reads were excluded from that VRT comparison
because its neighboring tiles were not included in this validation subset.
The COG passed full structural validation without warnings and contains fresh
1,800 × 1,800, 900 × 900, and 450 × 450 internal overviews.

The current provider `N32E038_wgs84.tif` was also downloaded to recheck the old
defect recorded above. Its SHA-256 is now
`79dda14c4c32927e526c34822d4fe9b288d4fa8554383014d86221320c5c14d1`;
it contains nonzero elevations, declares NoData -32768, and passes preflight.
The older retained defective copy has a different hash. This check does not
validate the terrain accuracy of the replacement or the entire global dataset.

Evidence is under ignored `.validation/srtm-provider-vrt/`: the downloaded VRT,
`full-tests.log`, `real-merge.log`, `real-validation.json`, and `real-cog/state.json`.
The user's `D:\Maps` collection, a global merge, remote CI, and QGIS desktop
rendering were not exercised.

## Nearest-neighbor overview default — 2026-11-04

The merger now defaults to nearest-neighbor overviews in both VRT-block and
consolidation modes. `--overview-resampling average` retains averaged overviews
and compatibility with pre-0.17 average-overview resume states. Nearest and
average runs cannot share a resume identity; runs without overviews retain their
previous identity. Full-resolution composition, Float32/Int16 types, NoData,
compression settings, and exact statistics are unchanged.

All **81/81 native tests passed** under Windows QGIS 4.2 / Python 3.12.13 /
GDAL 3.13.1. Coverage includes default nearest pixels for Int16 and Float32,
explicit average pixels, ignored stale source overviews, consolidation,
legacy-average resume, method-change rejection without rewriting files, and
resampler selection without enabling overviews. The nearest reference selects
source indices independently with NumPy using GDAL's overview sampling rule;
general-purpose `gdal.Translate` nearest resizing uses different pixel-center
selection and is not the nearest-overview oracle. Average references explicitly
disable source-overview reuse.

Real release acceptance used the downloaded **N31E035_wgs84.tif**, a 3,601 × 3,601
Float32 tile from the current provider VRT. The new default produced:

| Mode | Output bytes | Base pixels verified | Overview levels |
| --- | ---: | ---: | ---: |
| VRT block | 15,886,471 | 12,967,201 | 3 |
| Consolidation of that block | 15,887,336 | 12,967,201 | 3 |

Both outputs passed full COG validation without warnings. Independent NumPy
checks matched all base pixels bit for bit and all three overview arrays
exactly against the provider's nearest-neighbor overviews. Exhaustive resumes
preserved output SHA-256, size, and modification time. The source file also
retained its SHA-256, size, and modification time.

A copy of the earlier real average-overview output and its original state was
used for compatibility testing. The new default refused that state. Explicit
`--overview-resampling average --verify` checked all **12,967,201** base pixels
and resumed the **21,657,653-byte** COG without changing its hash, size, or
modification time. The original investigation artifacts were not modified.

### Storage investigation motivating the default

Before changing the default, **260 updated SRTM tiles** with origins 20..49° E,
30..39° N were downloaded and their local hashes checked. The subset retained
the provider's full-VRT source order, grid, and source mappings. Three 10° COGs
were generated with the then-current average-overview merger; a controlled
nearest-neighbor re-encoding used the same grids, DEFLATE-6, predictor 3,
512-pixel tiles, and seven overview levels.

| Product | Full-resolution compressed bytes | Overview compressed bytes | Total file bytes |
| --- | ---: | ---: | ---: |
| 260 source TIFFs | 1,993,350,615 | 887,893,598 | 2,881,932,433 |
| Three average-overview COGs | 2,047,939,295 | 2,314,733,606 | 4,363,090,679 |
| Three nearest-overview COGs | 2,047,939,295 | 926,808,132 | 2,975,165,205 |

The merger checked **3,888,216,003** base pixels against the source composition.
The nearest control independently matched all those pixels bit for bit against
the verified average outputs. All six COGs passed full structural validation
without warnings. Nearest saved **31.8%** versus average; its total was **3.2%**
larger than the downloaded tiles. The extra four average overview levels beyond
factor 8 accounted for only **40,414,644 bytes**. The dominant increase came
from the poorer compression of averaged Float32 overview values.

Evidence for the release checks is under the repository root's ignored
`.validation/nasadem-nearest-default/`: `full-tests.log`, `real-validation.log`,
`real-validation.json`, and the three real acceptance output directories.
Investigation evidence is in `.validation/srtm-compression-20261104/` and the
separate downloaded dataset's `COMPRESSION-REPORT.txt` and
`compression-investigation.json`. These are sample measurements, not a global
size guarantee or a terrain-accuracy audit. QGIS desktop rendering was not
tested for this change.
