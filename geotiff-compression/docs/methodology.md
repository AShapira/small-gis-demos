# Measurement methodology

## Reference and conversion

The configured projected rectangle has 32,768 × 52,224 pixels at 10 m. Product
footprints qualify before cloud ranking: a very low-cloud sliver is not a
substitute for a complete tile. Complementary acquisitions fill swath-edge gaps.
The final raster must independently meet the valid-byte and nodata contracts.
High-cloud products are mosaicked first, lower-cloud products last. Each
mixed-CRS input is warped independently before mosaicking.

The canonical image contains 3 Byte bands and a common average-resampled
pyramid. GeoTIFF variants use 512-pixel blocks, pixel interleaving and explicit
BigTIFF. LZW/DEFLATE/ZSTD use horizontal prediction. JPEG uses YCbCr and GDAL's
TIFF subsampling; its color transform is part of the quality comparison.
GeoPackage overviews are replaced with canonical overview pixels after
allocation, so their final imagery does not inherit a lossy base decode.

All decoded base pixels and overview pixels must match for lossless candidates.
Lossy MAE, RMSE and PSNR use valid canonical samples. SSIM uses a deterministic
spatial sample; its sample count and windows are retained. Eightfold difference
images exaggerate error and are labelled accordingly. Lossless display RGB
does not imply scientific reflectance preservation.

## Browser and cache model

Each user requests a 1024×768 map, at most six tiles concurrently, and views it
for a seeded random 3–8 seconds before navigating. Adjacent views retain tiles
from the immediately preceding viewport. Random pan, zoom, revisit and relocation
actions cover five landscape examples. Full-view latency includes the entire
tile burst. Request latency, achieved views/second, HTTP activity, stalled users
and response bytes accompany the view percentiles.

The model is intentionally a closed-loop human workload. When servers slow
down, users issue fewer new views. A low achieved rate is not evidence that the
server sustained the original offered demand. Local Podman networking does not
represent WAN latency or bandwidth.

WMTS uses GeoServer's stock Web Mercator grid, whose historic identifier is
`EPSG:900913` (the EPSG:3857 projection). Fixed 4×4 metatiles create correlated
requests: a MISS can populate tiles requested moments later. Every response's
cache header is saved. The miss traversal allocates fresh metatiles at zooms
13–15, including modest display oversampling at zoom 15; source pixels remain
10 m. An exhausted finite miss trace invalidates the run instead of silently
recycling hits.

Preseed preparation retries failed requests up to three times, retaining each
attempt in `preseed.jsonl`. Persistent failures stop the cell before measurement.
These retries do not apply to measured requests; their errors remain errors.
Preparation time includes retries, and summaries record failed seed attempts.

Hits preseed the exact trace. Mixed tests choose entire metatiles weighted by
the nominal requested trace to target 80% preseeded traffic. In-memory browser
reuse and metatile side effects can change the actual ratio, which is reported.
WMS warm-up bypasses GWC. Each measured cell gets its own cache namespace and a
fresh GeoServer process, followed by the configured warm-up and seeding.

First-access observations omit the workload warm-up. Metadata publication and
server readiness still happen first; filesystem cache state is uncontrolled.
These are not cold-device, cold-OS, or freshly-booted-JVM measurements.

## Fairness, resource accounting and decisions

The source comparison holds output PNG and style fixed. COG/GeoPackage are
identified as layout comparisons. Separate output-encoding profiles must hold
source and request sequence fixed; extension changes must not be mixed into
the vanilla-server storage ranking without a paired comparison.

Repeated runs are grouped by profile, codec, scenario and user count. Bootstrap
intervals resample run-level results; they do not treat correlated tile requests
as independent observations. Two or three repetitions provide limited statistical
power. Record invalid runs and diagnostics instead of deleting unfavorable data.

Cgroup CPU usage deltas are normalized to the assigned CPU quota. Record
throttling, RSS, JVM heap/GC, cgroup current memory, file/anonymous memory,
disk accounting and GWC bytes separately. These memory measures overlap; do not
sum them. Qualification conservatively includes page cache in peak cgroup
memory headroom. VM availability and co-resident cat-watch stats document
background contention.

The minimum resource recommendation requires every configured 100-user capacity
scenario and repetition, p95 full-view latency below 2 seconds for hits and
5 seconds otherwise, fewer than 1% failed views, and 25% CPU/memory headroom.
Synthetic inputs, missing metrics, generator saturation and failed cache
contracts cannot qualify a profile. A failed or incomplete matrix yields no
qualified recommendation.
