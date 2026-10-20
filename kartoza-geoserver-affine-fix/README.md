# Kartoza GeoServer 2.28.5 affine-raster patch bundle

Deploy the experimental rotated-GeoTIFF correction using **two replacement JARs
and startup scripts**, with the original Kartoza image. No image build, image
commit, TIFF conversion, overview rebuilding or server-side compiler is needed
to install this bundle. The source affine transform and nearest-neighbour
sampling are preserved.

This bundle is specific to **GeoServer 2.28.5 / GeoTools 34.5 / ImageN 0.9.1**.
It is not a generic patch for every 2.28 release. The startup script rejects
different original library hashes, even when the filenames match.

Read [RESULTS.md](RESULTS.md) for the measured rendering outcome and remaining
edge limitations. This is an experimental backport, not an upstream release.

See [COMPATIBILITY.md](COMPATIBILITY.md) for checks against other Kartoza 2.28
releases. The August 2.28.5 build accepts this bundle unchanged; 2.28.0–2.28.4
require version-matched builds even though the same source correction passes
the rotated-GeoTIFF tests.

## Exact supported image and files

Tested Linux amd64 image (Kartoza tag `2.28.5--v2026.10.01`):

```text
docker.io/kartoza/geoserver@sha256:a500f30ec973014be203851290e89996cfccfe4335b1b4345047f64eda05521d
```

| File | Purpose |
|---|---|
| `jars/gt-render-34.5.jar` | Bypasses the premature source-grid ROI crop for same-CRS rotated/sheared rasters with nearest interpolation |
| `jars/affine-0.9.1.jar` | Corrects forward/reverse scanline clipping and empty intersections |
| `start-with-affine-fix.sh` | Copies the hook into the container layer, then invokes Kartoza's original entrypoint |
| `apply-affine-fix.sh` | Verifies both libraries, backs them up outside the classpath, replaces them and checks the result |
| `jars.sha256.tsv` | Original and patched SHA-256 values, in that order |
| `compose.override.yaml` | Adds the read-only bundle mount and wrapper to an existing Compose service |
| `build.py`, `build-lock.json`, `source/` | Repeatable compilation, locked inputs, full upstream source archives, modified sources, diffs and licenses |
| `tests/`, `evidence/` | Startup checks, library regression, rendering runner and retained results |

Only one `.class` entry changes in each JAR; every other entry retains its
original bytes. Java class files target Java 17. Original library filenames and
upstream version metadata remain unchanged, so GeoServer's About page alone
cannot confirm installation: use the checksums and startup log.

## Install without a custom image

1. Copy this entire folder to a local directory on the Docker host. On an
   air-gapped host, transfer the pinned stock image separately with your normal
   `docker save` / `docker load` process. The installation scripts need no network.
2. From the bundle folder, verify the transfer:

   ```bash
   sha256sum -c SHA256SUMS
   ```

3. Inspect the existing container's libraries before changing deployment:

   ```bash
   docker exec geoserver sha256sum \
     /usr/local/tomcat/webapps/geoserver/WEB-INF/lib/gt-render-34.5.jar \
     /usr/local/tomcat/webapps/geoserver/WEB-INF/lib/affine-0.9.1.jar
   ```

   Compare against the original column of `jars.sha256.tsv`. A different version
   or checksum needs its own build and validation; do not rename an incompatible
   JAR or edit the manifest to bypass the check.

4. Add the supplied override to your existing Compose deployment. The example
   assumes its service is named `geoserver`; adjust the override's service key
   if needed. Keep the existing catalog, cache, credentials, ports and other
   volumes in the original Compose file.

   ```bash
   export AFFINE_FIX_BUNDLE=/absolute/path/to/kartoza-geoserver-affine-fix
   docker compose -f /path/to/compose.yaml \
     -f "$AFFINE_FIX_BUNDLE/compose.override.yaml" config
   docker compose -f /path/to/compose.yaml \
     -f "$AFFINE_FIX_BUNDLE/compose.override.yaml" up -d --no-build --force-recreate geoserver
   ```

   Container recreation interrupts this service. Preserve the existing data
   volumes; do not use `down -v`. Check that the rendered Compose configuration
   retains them before applying it. A deployment with a custom entrypoint needs
   to incorporate the wrapper rather than silently replacing that custom logic.

5. Inspect the service log for two `AFFINE_FIX apply` lines and verify the
   installed JAR hashes against the patched column of `jars.sha256.tsv` using
   the same `sha256sum` command. Check GeoServer readiness and your test requests.
6. Invalidate the affected GWC layer/grid/zoom caches, since already cached
   images are not regenerated merely by replacing libraries. Direct WMS and
   GWC must be checked separately.

The wrapper uses the stock image's normal root startup and subsequent switch to
the GeoServer service user. It does not enable `RUN_AS_ROOT=true`. The payload
mount remains read-only. `AFFINE_FIX_DIR` defaults to `/opt/affine-fix`;
`GEOSERVER_CONTEXT_ROOT` is honored when locating `WEB-INF/lib`. Advanced
overrides `AFFINE_FIX_LIB_DIR` and `AFFINE_FIX_BACKUP_DIR` exist for tests or a
verified alternate layout, not for bypassing version checks.

### Why there are two shell scripts

Kartoza runs custom hooks after extension installation but subsequently changes
ownership in `/docker-entrypoint-geoserver.d`. A direct read-only hook-directory
mount failed in the actual container test. The wrapper avoids that conflict by
copying the small hook into the writable container filesystem, then calling
`/scripts/entrypoint.sh`. The hook runs at the normal extension-safe point.

Kartoza sources hooks with `|| true`, so `set -e` alone cannot guarantee a failed
patch stops startup. This hook uses explicit fatal exits and validates both
inputs before changing either library. Do not run it manually against a live
JVM; library replacement belongs before Tomcat starts.

## Gutters and remaining edges

The patch removes the large interior triangles. The earlier study also found
small tile-edge rows with gutter 0. A one-pixel gutter is the stronger tested
option; consult this bundle's results for the exact counts on Kartoza 2.28.5.

- **GWC:** set `gutter` to `1` on each affected cached layer, then truncate its
  affected cached tiles. The validation uses 1 × 1 metatiling.
- **Direct WMS:** render 258 × 258 with the BBOX expanded by one output pixel on
  every side, then crop the central 256 × 256. The Cesium adapter in this repo
  supports `gutter: 1`; OpenLayers TileWMS supports `gutter: 1` directly.
  A WMS query parameter named `gutter` does not implement this operation.
- Keep nearest-neighbour interpolation when sharp source values are required.
  The patch does not require bilinear interpolation or changes to the TIFFs.

If small seams are acceptable and clients cannot expand requests, evaluate the
reported gutter-0 result for your data. Do not interpret it as zero missing
pixels everywhere.

For an existing cached layer, preserve its full configuration and change only
the gutter. `curl --user "$GS_USER"` prompts for the password:

```bash
GS='http://localhost:8080/geoserver'
GS_USER='admin'
LAYER='your_workspace:your_layer'
curl --fail --user "$GS_USER" "$GS/gwc/rest/layers/$LAYER.xml" -o layer-original.xml
python3 - <<'PY'
import xml.etree.ElementTree as ET
tree = ET.parse('layer-original.xml')
root = tree.getroot()
gutter = root.find('gutter')
if gutter is None:
    gutter = ET.SubElement(root, 'gutter')
gutter.text = '1'
tree.write('layer-gutter1.xml', encoding='UTF-8', xml_declaration=True)
PY
curl --fail --user "$GS_USER" -X PUT -H 'Content-Type: application/xml' \
  --data-binary @layer-gutter1.xml "$GS/gwc/rest/layers/$LAYER.xml"
```

Then use GeoServer's tile-layer cache controls to truncate the affected grid and
zoom range. Verify the first request is `geowebcache-cache-result: MISS` and the
second is `HIT`. Keep `layer-original.xml` if you need to restore its old gutter.

## Rollback

Remove the override and recreate the service from the pinned **stock** image
using its original Compose configuration and the same persistent data volumes:

```bash
docker compose -f /path/to/compose.yaml up -d --no-build --force-recreate geoserver
```

This discards the patched container layer and starts with the original libraries.
Verify original hashes, restore any GWC gutter settings you changed, and
invalidate affected caches again.

For a retained container filesystem, the hook also supports
`AFFINE_FIX_MODE=restore` **during startup**. Backups are stored under
`/usr/local/tomcat/affine-fix-backup/<original-sha256>/`, outside `WEB-INF/lib`.
It verifies both backup hashes before restoration. Backups are container-local;
they are not a replacement for preserving your GeoServer data volumes.

## Rebuild and repeat checks

The delivered JARs are ready to use. Rebuilding is optional and requires Python
3.11+ and a container engine with the pinned image available:

```bash
python3 build.py --engine docker
# Or:
python3 build.py --engine podman --connection podman-machine-default
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

`build.py` starts a unique stock compiler container with networking disabled,
compiles only the two changed classes using the image's Java 17 compiler, checks
the original and patched regression, verifies that no other JAR entries changed,
and removes that compiler container. It never calls an image build or commit.
Source hashes and original library hashes must match `build-lock.json`.

The live rendering tests require the full `small-gis-demos` checkout, the retained
14 TIFFs under `geotiff-compression/.runs/affine-probe/evidence/`, and an **isolated
test server** exposing those files at their manifest paths under
`/data/affine-probe/fixtures/`. The independent oracle is reused from that study;
the test runner reads TIFF arrays with Pillow and never writes them.

From this bundle directory:

```bash
python3 -m venv .artifacts/venv
.artifacts/venv/bin/pip install -r tests/requirements.txt
# Put the isolated server's admin password in a private file; never commit it.
.artifacts/venv/bin/python tests/validate.py ready --password-file /private/password
.artifacts/venv/bin/python tests/validate.py publish --password-file /private/password
.artifacts/venv/bin/python tests/validate.py configure --gutter 0 --password-file /private/password
.artifacts/venv/bin/python tests/validate.py scan --label my-stock-g0 --password-file /private/password
# Deploy the bundle, wait for readiness, and repeat configure + scan for patched g0.
.artifacts/venv/bin/python tests/validate.py configure --gutter 1 --password-file /private/password
.artifacts/venv/bin/python tests/validate.py scan --label my-patched-g1 --gutter 1 --password-file /private/password
.artifacts/venv/bin/python tests/validate.py regression --gutter 1 --password-file /private/password
.artifacts/venv/bin/python tests/validate.py cache-check --label my-patched-g1 --password-file /private/password
```

The default endpoint is `http://127.0.0.1:18093/geoserver`; override it with
`--url`. `publish` creates the study workspace/grid/style/layers and resets their
cache configuration. `configure` changes and truncates only study-layer caches.
Do not point these commands at an unrelated catalog. `scan` resumes an existing
label, including recorded failures: use a fresh label for each new measurement.

Kartoza's test catalog initially limited each user's WMS requests to 30/second.
That produced HTTP 429 responses. For the retained comparison, only this rate
rule was removed from the isolated catalog's `controlflow.properties`; concurrency
limits were retained for stock and patched runs. This is a test preparation
choice and is **not part of the patch or production override**.

Raw responses, failure bodies, masks and per-request URLs remain in
`.artifacts/rendering/` locally. Curated evidence and summaries are in `evidence/`.
The deployment bundle excludes credentials, private test settings and those bulk
raw artifacts.

After a live scan, `python tests/analyze.py` rebuilds the saved comparison and
checks the recorded gutter-1 coverage, footprint losses and checkerboard values.
It uses the three retained labels shown in `RESULTS.md`. Run `python package.py`
to regenerate `SHA256SUMS` and the transfer ZIP from the public bundle files.

## Source and licenses

Original installer, build and test scripts and original documentation use the
[MIT License](LICENSE). Upstream source, modifications to it, source archives and
compiled JARs retain the component licenses described below.

See [source/README.md](source/README.md). The modified GeoTools source retains
LGPL-2.1 licensing; the ImageN affine source retains Apache-2.0 licensing. Full
upstream source archives and license texts accompany the diffs. Neither the
GeoServer project nor Kartoza has approved this experimental patch.
