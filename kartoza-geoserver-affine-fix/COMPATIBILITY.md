# Kartoza GeoServer 2.28 compatibility

Checked on **2026-10-20**, using pinned **Linux/amd64** stock images and the
retained rotated-GeoTIFF study. No custom image was built.

**The source correction works on all tested 2.28.0–2.28.5 releases. The existing
2.28.5 installation bundle is not a universal 2.28 bundle.** Older releases need
version-matched JARs and a matching strict installer manifest.

The ready-to-install [2.28.0 bundle](../kartoza-geoserver-2.28.0-affine-fix/README.md)
contains its matching JAR pair, startup scripts, installation/rollback guide and
separate runtime and source ZIP packaging.

## Compatibility matrix

| Kartoza image tag tested | GeoTools | ImageN affine | Existing 2.28.5 bundle | Matching patch + gutter 1 WMS/GWC matrix |
|---|---|---|---|---|
| 2.28.0 | 34.0 | 0.9.0 | No; matching build required | Passed |
| 2.28.1 | 34.1 | 0.9.1 | No; matching build required | Passed |
| 2.28.2 | 34.2 | 0.9.1 | No; matching build required | Passed |
| 2.28.3 | 34.3 | 0.9.1 | No; matching build required | Passed |
| 2.28.4 | 34.4 | 0.9.1 | No; matching build required | Passed |
| 2.28.5--v2026.08.27 | 34.5 | 0.9.1 | Yes, unchanged | Passed |
| 2.28.5--v2026.10.01 / 2.28.5 | 34.5 | 0.9.1 | Yes, original target | Passed in the original bundle validation |

The six rows above the original target were tested in this compatibility run.
The October 2.28.5 result comes from [the original full validation](RESULTS.md);
its current tag digest still matches the pinned, tested image.

What must be delivered for each release:

- **2.28.0:** a patched `gt-render-34.0.jar` and `affine-0.9.0.jar`.
- **2.28.1–2.28.4:** a patched `gt-render-34.1.jar`, `34.2`, `34.3` or `34.4`,
  respectively. Their original and patched `affine-0.9.1.jar` hashes are identical
  to the existing 2.28.5 bundle, so that ImageN payload can be reused.
- **2.28.5 August and October builds:** the complete existing JAR pair and startup
  scripts work unchanged. The August candidate's two JARs, scripts and manifest
  are byte-identical to the shipped bundle.

The supplied Compose override still pins the **October** image. To intentionally
use the tested August build, change only its image reference to:

```text
docker.io/kartoza/geoserver@sha256:42a3de3483406ccc8b7a06b7cc7350a0ac6d4bfcf1818ab6652ffe7b6e492ff6
```

Preserve the deployment's original data volumes and credentials. The wrapper's
behavior was exercised with Podman container creation/startup; the Docker Compose
CLI itself was not used for these checks.

## Evidence from actual rendering

For **each of the six newly tested images**:

- The stock minimal tile reproduced **29,022 missing pixels** through direct WMS
  and GWC with gutter 0.
- With the matching patch and **gutter 0**, that same tile had **zero missing
  pixels** on both routes. The negative-rotation boundary request also completed
  on both routes. This checks the patch's effect independently of added gutter.
- With the matching patch and **gutter 1**, the full matrix completed **1,180
  requests per path**, including **658 fully interior tiles**, with **zero errors,
  zero missing interior pixels and zero missing north-up control pixels**.
- Each path retained **16 isolated footprint-edge omissions**, at most one pixel
  in each affected tile, all within 2.056 source-grid pixels of the outer
  footprint. The fixture corners outside the affine footprint are
  excluded by the independent geometry oracle; no border tolerance is subtracted.
- The checkerboard retained nearest-neighbour transitions: **zero intermediate
  gray pixels**, **99.9954% exact source-value agreement**, and mean absolute error
  **0.007324** gray levels on both routes.
- Every full-matrix GWC request was a cache **MISS**. A separate repeated request
  confirmed **MISS → HIT**. Direct WMS did not use GWC integration.

That is **14,160 full-matrix rendered requests** across the six images, in addition
to the stock reproduction, gutter-0 samples, regression and cache checks.

Gutter 1 retains the same meaning as in the original report: direct WMS uses a
258 × 258 request and a central 256 × 256 crop; GWC uses a one-pixel layer gutter.
The narrow tile-edge rows previously observed at gutter 0 have not been ruled
out across every older release's full matrix. The complete matrix here uses
**gutter 1**, and the gutter-0 check is deliberately limited to the selected cases.

[Summary and sharpness metrics](compatibility/evidence/summary.json) ·
[Visual comparison](compatibility/evidence/comparison.png) ·
[Per-image inspection and rendering evidence](compatibility/evidence/)

## Why the original JAR pair cannot be copied into older releases

The current installer requires exactly `gt-render-34.5.jar` and
`affine-0.9.1.jar`, with the recorded original hashes. Against every tested
2.28.0–2.28.4 image it stopped before changing either library. This is expected.

GeoTools 34.0 and 34.1 have the older read-padding implementation. GeoTools
34.2–34.5 share an identical original `GridCoverageReaderHelper` source file.
All five older versions accepted the same small patch hunks with exact context;
34.0/34.1 required line offsets, preserving their pre-refactor implementation.

Replacing the **whole** older GeoTools JAR with 34.5 would also replace 17–21 other
compiled classes. Matching builds instead change exactly one class entry in each
original JAR, preserving every other archive member's bytes. Do not rename the
34.5 JAR or relax the installer checks to make it fit an older release.

ImageN 0.9.0 and 0.9.1 contain an identical `AffineOpImage` source file, so the
same clipping correction applies. Nevertheless, their complete JARs differ;
2.28.0 was compiled and tested with its own 0.9.0 library.

The original Java scanline regression failed in all six images; each matching
patched build passed forward, reverse and empty-intersection cases. Compilation
used each image's own Java 17 compiler, not a different GeoTools classpath.

## Tags and limits

The [Docker Hub tag inventory](https://hub.docker.com/r/kartoza/geoserver/tags)
contained 28 tags beginning with `2.28` at inspection time. This run selected the
current aliases 2.28.0–2.28.4 and the older August 2.28.5 build. It did not run
all historical rebuilds independently.

The following dated aliases have the same **Linux/amd64 image manifest** as the
selected image, so they identify the same tested image content:

| Selected alias | Equivalent dated aliases |
|---|---|
| 2.28.0 | 2.28.0--v2026.01.26; 2.28.0--v2026.01.25 |
| 2.28.1 | 2.28.1--v2026.01.26 |
| 2.28.2 | 2.28.2--v2026.04.18 |
| 2.28.3 | 2.28.3--v2026.04.22 |
| 2.28.4 | 2.28.4--v2026.06.09; 2.28.4--v2026.06.06 |
| 2.28.5 | 2.28.5--v2026.10.01 |

Full index/architecture digests are saved in
[published-tags.json](compatibility/evidence/published-tags.json). Other historical
builds, future retagged images and ARM64 images are not established as compatible
by these results. Match the original JAR hashes, not merely the `2.28.x` label.

Coverage remains the original 14 immutable 4096 × 4096, single-band 8-bit grayscale
EPSG:3857 fixtures: north-up, rotations 15°, 30°, 45°, 75°, −30° and shear, each
with and without nearest overviews; zooms 14–24. This does not validate other
CRSs, 16-bit/multiband imagery, explicit NoData masks or every ImageN operation.
No new performance benchmark was run.

## Reproduction and retained files

See [compatibility/README.md](compatibility/README.md) for the commands and scripts.
Version-specific candidate JARs, strict installer scripts and matching sources
are retained locally under `.artifacts/compatibility/<tag>/candidate/` and its
parent directory. They are compatibility-test outputs; the released 2.28.5 JAR payloads
and installer version guards remain unchanged.

The original fixture volume was mounted read-only and all fixture hashes remain
unchanged. All pre-existing containers retain their original stopped state;
all new test containers are stopped, with their isolated catalogs and evidence
retained. See [final state](compatibility/evidence/final-state.json).

The test runner encountered and handled an older Kartoza password-file startup
behavior before the first successful 2.28.0 rendering run. Successful checks use
random isolated credentials via its supported environment variable. Only the
isolated catalogs' per-user WMS rate rule was removed; concurrency limits were
retained. These lab choices are not modifications to the production patch.
