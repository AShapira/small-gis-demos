# Rotated-GeoTIFF patch for Kartoza GeoServer 2.28.0

This bundle installs two patched libraries into the **stock Kartoza GeoServer
2.28.0 image at startup**. No custom image or server-side compiler is required.
It preserves the TIFFs, their affine transforms and nearest-neighbour sampling.
It is an experimental backport, not an upstream GeoServer release.

Use `kartoza-geoserver-2.28.0-affine-fix-runtime.zip` for installation. It contains
the compiled JARs, startup scripts, Compose override, instructions, checksums and
license notices. Matching sources and optional rebuild tools are in
`kartoza-geoserver-2.28.0-affine-fix-sources.zip`; they are not needed on the server.

## Supported version

Tested platform: **Linux amd64**, Java 17, GeoServer **2.28.0**, GeoTools **34.0**,
ImageN **0.9.0**, GeoWebCache **1.28.0**.

Pinned stock image:

```text
docker.io/kartoza/geoserver@sha256:59cd5984bd4e6073dc07432ddb06f3e91e324fc32d55b304eaa5877cb7622667
```

This is the tested image behind the Kartoza `2.28.0` tag. Tags can move; the
installer checks the **exact original JAR hashes**, not just version names.
The 2.28.5 bundle must not be installed on this version.

| Replacement file | Change |
|---|---|
| `jars/gt-render-34.0.jar` | Avoids the premature crop for same-CRS rotated/sheared rasters using nearest-neighbour interpolation |
| `jars/affine-0.9.0.jar` | Corrects affine scanline clipping endpoints and empty intersections |

Only one class changes in each JAR; every other archive entry retains its
original bytes. The original filenames and version metadata are retained.
`jars.sha256.tsv` lists the filename, original SHA-256 and patched SHA-256.

## Install

The example assumes an existing Compose service named `geoserver`. Adjust that
service name and the paths to match your deployment. Keep your existing catalog,
credentials, ports, cache and data volumes.

1. Transfer and extract the runtime ZIP onto the container host. For an
   air-gapped host, transfer the pinned stock image separately using your normal
   image export/import process. The patch installation needs no network.

   ```bash
   sha256sum -c kartoza-geoserver-2.28.0-affine-fix-runtime.zip.sha256
   unzip kartoza-geoserver-2.28.0-affine-fix-runtime.zip
   cd kartoza-geoserver-2.28.0-affine-fix
   sha256sum -c SHA256SUMS
   ```

2. Check the existing server's libraries before changing the deployment. Replace
   `geoserver` below with its container name:

   ```bash
   docker exec geoserver sha256sum \
     /usr/local/tomcat/webapps/geoserver/WEB-INF/lib/gt-render-34.0.jar \
     /usr/local/tomcat/webapps/geoserver/WEB-INF/lib/affine-0.9.0.jar
   ```

   Expected original hashes:

   ```text
   67c0a9071926e52484f668c9ee5f75428601ae6441d0bf26f84881050e1e3d37  gt-render-34.0.jar
   3e30c3e6a3ee171526428949885178a1fc99aee04c103fa812613c0becb128a0  affine-0.9.0.jar
   ```

   A different hash needs its own build and validation. Do not rename another
   version's JARs or edit the manifest to bypass the checks.

3. Add the included override to your existing Compose configuration:

   ```bash
   export AFFINE_FIX_BUNDLE=/absolute/path/to/kartoza-geoserver-2.28.0-affine-fix
   docker compose -f /path/to/compose.yaml \
     -f "$AFFINE_FIX_BUNDLE/compose.override.yaml" config
   docker compose -f /path/to/compose.yaml \
     -f "$AFFINE_FIX_BUNDLE/compose.override.yaml" \
     up -d --no-build --force-recreate geoserver
   ```

   Review the rendered configuration before running `up`: it must retain the
   existing data volumes. Recreation interrupts the service. Do not use
   `down -v`. A deployment with an existing custom entrypoint must incorporate
   this wrapper into that logic.

   The override mounts the bundle read-only at `/opt/affine-fix`, sets
   `entrypoint: ["/bin/bash", "/opt/affine-fix/start-with-affine-fix.sh"]`
   and clears the inherited command. For a Podman deployment, apply these same
   mount and entrypoint settings through your deployment manager.

4. Check the service log for both `AFFINE_FIX apply` messages. Repeat the
   `sha256sum` command above and compare with these **patched** hashes:

   ```text
   b9914e88094778f34ffbe6d9a15329b1ae47d1be366f3b06370aa0fad98999b7  gt-render-34.0.jar
   0d76d4d2de5bf9f7e4626ce9f46f5c7940f5f90a50d7d33aad85312b737a6782  affine-0.9.0.jar
   ```

   Check readiness and representative WMS/GWC requests. GeoServer's About page
   still reports the upstream versions and cannot confirm patch installation.

5. Truncate the affected GWC layer/grid/zoom caches. Previously cached defective
   tiles remain until invalidated. Verify a new tile reports
   `geowebcache-cache-result: MISS`, followed by `HIT` on repetition.

The wrapper copies the hook into the writable container layer and then invokes
Kartoza's normal `/scripts/entrypoint.sh`. Do not mount the hook directory
read-only: Kartoza changes its ownership during startup. Normal root startup and
the subsequent switch to the service user are retained; `RUN_AS_ROOT=true` is
not required.

The hook validates both libraries and payloads before replacement, backs up
originals outside the classpath, rejects duplicate library versions and exits
fatally on incompatibility. Repeated startup is supported. Install before Tomcat
starts; do not replace JARs in a running JVM.

## Rendering settings and remaining edges

Keep **nearest-neighbour** interpolation. The patch removes the large interior
triangles in the reproduced case with gutter 0. The broader validated setup
uses a **one-pixel gutter**:

- **GWC:** set the cached layer's gutter to `1`, then truncate its affected
  tiles. Tests used 1 × 1 metatiling.
- **Direct tiled WMS:** expand each BBOX by one output pixel per side, request
  258 × 258, then crop the centre to 256 × 256. OpenLayers TileWMS and this
  repository's Cesium adapter support `gutter: 1`. A WMS query parameter named
  `gutter` alone does not implement request expansion and cropping.

The full 2.28.0 test covered 1,180 requests **per serving path**, with zero
interior holes and zero request errors. Each path retained 16 isolated missing
pixels at the outer TIFF footprint. These are within 2.056 source pixels of its
edge. TIFF checksums were unchanged and checkerboard values remained sharp.
See [VALIDATION.md](VALIDATION.md) for scope and limitations.

If small edges are acceptable and your WMS client cannot expand requests,
evaluate gutter 0 on your data. Its minimal failing request and selected
regressions passed, but the full 2.28.0 matrix was run with gutter 1.

To change an existing GWC layer without replacing its other configuration:

```bash
GS='http://localhost:8080/geoserver'
GS_USER='admin'
LAYER='your_workspace:your_layer'
# --user with only a username prompts for the password.
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

Then truncate the affected caches using GeoServer's tile-layer cache controls.
Retain `layer-original.xml` to restore the previous setting if needed.

## Rollback

Remove the patch override and recreate the service from the **same pinned stock
2.28.0 image**, retaining the existing data volumes:

```bash
docker compose -f /path/to/compose.yaml \
  up -d --no-build --force-recreate geoserver
```

First ensure the original Compose file uses the stock image and does not retain
the patch entrypoint. This resets the container layer to the original JARs.
Verify original hashes, restore any gutter settings you changed, and invalidate
affected GWC caches again.

This assumes `WEB-INF/lib` is in the container layer. If your deployment mounts
that directory persistently, recreation alone will not undo its changes. The
hook also supports `AFFINE_FIX_MODE=restore` **at startup of the retained
container**, using its checked original backups at
`/usr/local/tomcat/affine-fix-backup/<original-sha256>/`. Preserve these backups
until rollback is complete; they are container-local by default.

## Sources and distribution

Read [NOTICE.md](NOTICE.md) and the license texts in `LICENSES/`. When sharing the
runtime ZIP, provide the matching sources ZIP alongside it. The source ZIP
contains the original module source archives, modified classes, diffs, pinned
build inputs and optional rebuild/regression tools. Its `source/README.md`
explains rebuilding. Installation does not require those files.
