# NASADEM COG ImageMosaic on local S3

This configuration test uses the two previously generated 10° NASADEM COGs,
a private MinIO bucket, stock Kartoza GeoServer **2.28.0**, and a PostGIS
granule index. It checks configuration and a few sequential request timings.
It is not a load, capacity, or storage benchmark.

The [test report](validation-report.md) records the measured result and limits.

## Configuration

The pinned GeoServer image is:

```text
docker.io/kartoza/geoserver@sha256:59cd5984bd4e6073dc07432ddb06f3e91e324fc32d55b304eaa5877cb7622667
```

Activate its bundled extension using:

```text
COMMUNITY_EXTENSIONS=cog-s3-plugin
FORCE_DOWNLOAD_COMMUNITY_EXTENSIONS=false
```

This uses the plugin archive already present in `/community_plugins/`.
The test uses the stock renderer without the repository's affine patch.

For objects indexed using `s3://bucket/key`, configure the GeoServer process:

```text
IIO_S3_AWS_ENDPOINT=http://s3:9000
IIO_S3_AWS_REGION=us-east-1
IIO_S3_AWS_FORCE_PATH_STYLE=true
IIO_S3_AWS_USER=<access-key>
IIO_S3_AWS_PASSWORD=<secret-key>
```

Here `s3` is the MinIO service's name on the test container network. Replace it
with an endpoint resolvable and reachable **inside the GeoServer container**.
The URI scheme determines the `S3` alias in these variable names, even though
the endpoint uses HTTP. Region must agree with the object service configuration.
Path-style addressing places the bucket in the request path.

The plugin extends the existing GeoTIFF and ImageMosaic stores; it does not
create a new store type called "S3 ImageMosaic". Kartoza's startup message about
skipping `s3.properties` is for a separate configuration path; the COG reader
uses the `IIO_S3_*` settings above.

The [indexer.properties](indexer.properties) file starts an empty COG mosaic
named `nasadem`, with geometry and object-location fields. Its key settings are:

```properties
CanBeEmpty=true
Cog=true
CogRangeReader=it.geosolutions.imageioimpl.plugins.cog.S3RangeReader
AbsolutePath=true
```

The test supplies this `datastore.properties` alongside it:

```properties
SPI=org.geotools.data.postgis.PostgisNGDataStoreFactory
host=db
port=5432
database=mosaic
schema=public
user=mosaic
passwd=<database-password>
Loose bbox=true
max connections=4
min connections=1
```

PostGIS holds the footprint and object URI for each granule; MinIO holds the
TIFF bytes. GeoServer receives neither a source-data mount nor a local TIFF copy.
The test generates disposable credentials and retains them only in private,
ignored evidence directories. It uses a private test bucket with its own MinIO
administrator; provision a suitable read-only identity for a real deployment.

## Publication sequence

The runnable [run_test.py](run_test.py) performs these operations with generated
credentials:

1. Upload the existing COGs unchanged to `nasadem-test/cogs/`. Download each
   object once to verify its SHA-256, then check that anonymous range reads fail.
2. Create workspace `nasadem_s3`.
3. ZIP `indexer.properties` and `datastore.properties` at the archive root.
   PUT that ZIP to
   `/rest/workspaces/nasadem_s3/coveragestores/nasadem/file.imagemosaic?configure=none`.
4. POST each complete `s3://nasadem-test/cogs/<filename>.tif` URI as `text/plain`
   to the store's `/remote.imagemosaic` endpoint.
5. GET `/coverages.xml?list=all`, then POST the `nasadem` coverage to
   `/coverages`. Configure EPSG:4326, explicitly declare `GEOTIFF` in
   `supportedFormats`, and set the included elevation style.
6. Confirm two index entries, compare native-resolution WCS windows against the
   local COGs, request WMS images, and restart GeoServer to check persistence.

The metadata/configuration directories and PostGIS database need their normal
persistent storage in a lasting deployment. The disposable test keeps them in
the containers' writable layers and tests a restart of those same containers.

The coverage creation body is:

```json
{
  "coverage": {
    "name": "nasadem",
    "nativeName": "nasadem",
    "enabled": true,
    "srs": "EPSG:4326",
    "nativeFormat": "GeoTIFF",
    "supportedFormats": {"string": ["GEOTIFF"]}
  }
}
```

Declaring the supported format matters for WCS 1.0: this REST publication path
otherwise leaves the format list empty, so `GetCoverage` returns an OGC
exception even with HTTP status 200. The test checks the response body and
decodes the TIFF, rather than treating an HTTP success alone as a passing read.

## Run the test

Run from the repository root on Linux with Podman and Python 3.12. The script
creates four uniquely named containers on a private internal network: GeoServer,
MinIO, PostGIS, and a receiver for sanitized MinIO audit events. Only GeoServer
and the S3 API are published, bound to loopback ports 18096 and 19096.

Pull the pinned images declared at the top of `run_test.py` before running if
they are not cached. No Internet access is needed by the running test services.

```bash
python3 -m venv nasadem-cog/.validation/s3-mosaic/venv
nasadem-cog/.validation/s3-mosaic/venv/bin/python -m pip install \
  -r nasadem-cog/s3-imagemosaic/requirements-test.txt
nasadem-cog/.validation/s3-mosaic/venv/bin/python \
  nasadem-cog/s3-imagemosaic/run_test.py \
  --input-dir nasadem-cog/.validation/real-10deg
```

The expected filenames are:

```text
NASADEM_N30E020_N39E029.tif
NASADEM_N30E030_N39E039.tif
```

Use `--geoserver-port`, `--s3-port`, and `--output-dir` to change the local test
ports and evidence destination. Keep evidence inside an ignored directory:
environment files and the test catalog contain disposable credentials.
The script stops its containers after either success or failure and retains
their writable layers for diagnosis. It does not stop or edit existing services.

`result.json` records versions, image digests, source hashes, REST statuses,
pixel checks, range-read totals, timings, and final container states.
`source-oracle.json` contains small GDAL reference windows. WCS TIFFs, WMS PNGs,
sanitized audit records and server logs remain alongside it. Test data and
service logs are not committed.

The GDAL reference process mounts only the existing source directory read-only
and has no network. MinIO audit records retain response status, object key,
range, byte count and user agent; authentication headers are not retained.

## References

- [GeoServer 2.28 COG configuration](https://docs-archive.geoserver.org/2.28.x/en/user/community/cog/cog.html)
- [Official COG ImageMosaic bootstrap sequence](https://github.com/geoserver/geoserver/blob/2.28.x/doc/en/user/source/community/cog/mosaic.rst)
- [Kartoza extension activation](https://github.com/kartoza/docker-geoserver/blob/develop/Advanced-Configuration.md#activate-community-extensions-during-contain-startup)
