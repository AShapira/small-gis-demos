# Local S3 ImageMosaic configuration test — 2026-10-20

**Passed:** stock Kartoza GeoServer **2.28.0** served an ImageMosaic made from
the two existing NASADEM COGs in a **private local MinIO bucket**, using the
bundled COG S3 extension and a PostGIS granule index.

GeoServer read the TIFFs through signed S3 byte-range requests. It had no
source-file mounts. Four native-resolution WCS extracts matched the local
COGs exactly, WMS previews rendered, and the mosaic survived a GeoServer
restart. This is a configuration and basic response-time test.

See the [configuration and reproduction guide](README.md), the
[runnable test](run_test.py), and the sanitized
[machine-readable evidence](validation-summary.json).

## Software and inputs

| Component | Tested configuration |
| --- | --- |
| GeoServer | Stock Kartoza 2.28.0, Linux amd64; no affine patch |
| GeoTools / GeoWebCache | 34.0 / 1.28.0 |
| Java | Eclipse Temurin 17.0.16 |
| Object storage | Cached MinIO image, pinned by digest; private bucket |
| Granule index | PostGIS in a separate isolated container |
| Runtime | Linux rootless Podman in WSL |
| GeoServer allocation | 2 CPUs, 3 GiB container memory, 512 MiB initial / 2 GiB maximum JVM heap |
| MinIO / PostGIS allocation | 1 CPU each; 1 GiB / 512 MiB container memory |
| Reference reader | GDAL 3.11.4, separate container, read-only source mount, no network |

All four image digests and test dependency versions are in
`validation-summary.json`. The GeoServer image is pinned to:

```text
docker.io/kartoza/geoserver@sha256:59cd5984bd4e6073dc07432ddb06f3e91e324fc32d55b304eaa5877cb7622667
```

Its bundled `/community_plugins/cog-s3-plugin.zip` SHA-256 is:

```text
31e8980868cc1bc730f1766479c69b0c6a0f83d38f81a7a5b793946b8de49357
```

The plugin installed `gs-cog-core-2.28-SNAPSHOT.jar`,
`gs-cog-s3-2.28-SNAPSHOT.jar`, and
`imageio-ext-cog-rangereader-s3-2.0.1.jar`; the base image supplied COG reader,
streams and commons version 2.0.0. These are the actual bundled filenames,
not a claim that arbitrary later snapshot packages are interchangeable.
No extension download occurred during startup.

| Existing COG | Dimensions | Bytes |
| --- | ---: | ---: |
| `NASADEM_N30E020_N39E029.tif` | 36,001 × 36,001 | 353,359,322 |
| `NASADEM_N30E030_N39E039.tif` | 36,001 × 36,001 | 634,711,808 |

Both files retain their original Int16 elevations, EPSG:4326 grid,
`-32768` NoData, DEFLATE compression and seven internal overviews.
They were reused unchanged; no source download, COG conversion, or overview
rebuild was performed for this test. Each uploaded object was downloaded once
by the test client and SHA-256 compared with its source. Both local files
retained their SHA-256 hashes after testing.

## Configuration results

- Activated `COMMUNITY_EXTENSIONS=cog-s3-plugin`, using the bundled ZIP.
- Connected using `IIO_S3_AWS_ENDPOINT=http://s3:9000`, region `us-east-1`,
  path-style addressing, and generated test credentials.
- Anonymous GET requests to both objects returned **403**.
- Created an empty COG ImageMosaic with the PostGIS index and harvested both
  `s3://nasadem-test/cogs/...tif` URIs. Each harvest returned **202**.
- Read back exactly two indexed granules, with their original S3 URIs and
  geographic footprints.
- Published the EPSG:4326 coverage, explicitly declaring the WCS GeoTIFF
  output format, and applied the included elevation style.
- After a GeoServer process/container restart, both granules remained indexed
  and a new WMS request returned a valid 256 × 256 PNG.

The GeoServer container had **zero mounts**. Its normal local catalog and
mosaic metadata contained the PostGIS connection and remote object references;
the complete source rasters were available only from MinIO.

Two configuration details were resolved before the successful fresh run:

1. An empty mosaic containing only `indexer.properties` with the default file
   index failed to initialize on the first remote harvest. The tested setup
   follows the official example by supplying a PostGIS `datastore.properties`.
   This result does not establish that all file-index configurations fail.
2. The minimal REST coverage body left `supportedFormats` empty. WCS 1.0
   consequently returned an OGC exception saying GeoTIFF was unsupported,
   despite HTTP status 200. Explicitly setting `supportedFormats` to `GEOTIFF`
   fixed the request. The successful test decodes and validates response bodies.

## Elevation and spatial checks

GDAL read four 128 × 128 windows directly from the unchanged local COGs.
The same aligned bounds and dimensions were requested through WCS 1.0,
without downsampling. Every returned sample was compared.

| Window | Approximate centre | Samples compared | Valid samples | NoData samples |
| --- | --- | ---: | ---: | ---: |
| Western COG | 23.7° E, 38° N | 16,384 | 16,384 | 0 |
| Eastern COG | 35.2° E, 32.8° N | 16,384 | 16,384 | 0 |
| Shared 30° E boundary | 30° E, 30.5° N | 16,384 | 16,384 | 0 |
| Ocean/coverage gap | 25° E, 33° N | 16,384 | 4,160 | 12,224 |

All **65,536 samples matched exactly**, including all 12,224 NoData cells.
The responses also passed checks for signed 16-bit samples, EPSG:4326,
NoData metadata, grid origin and one-arcsecond spacing. Spatial comparisons
used tolerances of 1e-10 degrees for origin and 1e-12 degrees for spacing.

This is a targeted serving check. The earlier [COG creation validation](../validation-report.md)
separately compared all 2,592,144,002 base pixels; that exhaustive comparison
was not repeated through GeoServer.

## S3 range-read evidence

The MinIO audit receiver retained non-secret API status, object key, Range and
Content-Range headers, response length and client user agent. Requests made
by GeoServer's Java AWS SDK were distinguished from the test client's upload
and whole-object integrity checks.

| Source object | Successful range GETs |
| --- | ---: |
| Western COG | 166 |
| Eastern COG | 174 |
| **Total** | **340** |

Every GeoServer GET for either source TIFF included a byte range and returned
**206 Partial Content**. Their combined response payload was **10,434,258 bytes**
over the complete publication, WCS, WMS and restart sequence. GeoServer made
**zero whole-object GETs** for these TIFFs.

The reader also probed optional `<filename>.tif.ovr` objects **14 times**.
Those returned **404**, as expected: the overviews are internal to the COGs.
These probes were recorded separately from successful source-object reads;
they did not prevent any final WCS or WMS response.

## Basic response timings

Issued **12 sequential, direct WMS 1.1.1 requests**: three geographic views,
one first request and three repeats each. Each returned a nonempty
512 × 512 RGBA PNG in EPSG:4326. Requests used the WMS endpoint with
`tiled=false`, without GeoWebCache tile serving.

| View / BBOX (west, south, east, north) | First request for view | Median of 3 repeats |
| --- | ---: | ---: |
| Western region: `22,36,26,40` | 0.442 s | 0.157 s |
| Eastern region: `34,30,36,34` | 0.178 s | 0.107 s |
| Shared boundary: `29.5,30,30.5,31` | 0.092 s | 0.126 s |

These measure the local HTTP request through receipt of the complete PNG.
Publication and WCS checks ran first, so the initial view requests are **not
cold-cache measurements**. Reader, JVM and operating-system caches were left
untouched. All three repeat samples per view are retained in the JSON evidence.
The separate 256 × 256 WMS request after restart took **1.772 s**.

No concurrency, throughput, sustained load, remote-network, cache-capacity,
or large-collection benchmark was performed. Background services remained
running. The small timing sample is a basic functional check, not an SLA.

## Evidence and remaining scope

Successful run: `nasadem-s3-1b1ed222`. Raw evidence remains under:

```text
nasadem-cog/.validation/s3-mosaic/nasadem-s3-1b1ed222/
```

It contains `result.json`, `source-oracle.json`, `coverage.json`,
`granules.json`, four WCS TIFFs, three WMS PNGs, `s3-audit.jsonl`, and
sanitized service logs. Disposable credential files remain private and ignored.
The published summary includes source and test-file hashes plus the raw
result-file hash. The audit summarizer was additionally checked against the
retained events, including expected external-overview probes.

All test containers, including diagnostic attempts, are **stopped**. Their
isolated catalogs, objects and database layers are retained for inspection.
Existing services were not modified.

This validates the pinned 2.28.0 image and its bundled plugin against local
MinIO over HTTP. Other Kartoza patch releases, external S3 appliances, TLS,
custom CAs, IAM/STS credential rotation, production read-only policies,
container recreation with persistent volumes, and large mosaics were not
tested. The included procedure documents the working configuration to use as
the starting point for those environments.
