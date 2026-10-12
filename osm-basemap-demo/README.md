# Offline OSM basemap demo

Prepared vector basemaps served by the **stock Kartoza GeoServer 3.0.1 image**.
The regional source is MVT inside MBTiles; GeoServer applies the cartography and
returns raster WMS/WMTS images. Natural Earth supplies worldwide context.

The five designs are OSM Bright, Positron, Dark Matter, Toner and MapTiler Basic,
based on [GeoSolutions' OpenMapTiles data directory](https://github.com/geosolutions-it/openmaptiles).
The default extracts cover Czechia and Slovakia. No PostGIS, public tile server,
online font service, API key, custom GeoServer image or plugin compilation is used.

Each design has **Local** and **English** label variants; the viewer defaults to
English. English variants use explicit OSM `name:en` values only. Missing English
names leave the text blank while features and applicable icons remain visible.
Road references and house numbers remain in both variants. The legacy `name_en`
attribute is deliberately avoided because it contains local-name fallbacks.
Natural Earth overview labels use `NAME_EN`. Names may retain accents where the
supplied English proper name does; no translation or transliteration is performed.

## Produce the package

Requirements on the preparation machine: Linux AMD64, Python 3.11+, curl and
Docker or rootless Podman. Python orchestration uses only the standard library.
GDAL, Osmium and Java run inside stock containers. Osmium's Debian packages are
downloaded during prefetch and installed inside disposable GDAL containers during
offline builds; no packages are installed on the host.

Start with approximately 8 CPU cores, 32 GiB available RAM and 60 GiB free disk.
The configured Java heap is 16 GiB with a 24 GiB container memory limit. These are
planning budgets, not measured minimums; inspect the generated production report.
See [the measured demo validation](validation-report.md) for the completed Czechia/
Slovakia run. Production commands below run from this source project; the exported
bundle contains the prepared runtime and is started with `load-and-run.sh`.

From this directory:

```bash
python3 demo.py preflight
python3 demo.py prefetch
python3 demo.py build --smoke
python3 demo.py start --smoke
python3 demo.py validate --smoke
python3 demo.py stop --smoke
python3 demo.py build
python3 demo.py start
python3 demo.py validate
# Inspect bundle/validation/*.png and the browser viewer before export.
python3 demo.py stop
python3 demo.py export
```

`prefetch` is the only network-dependent production stage. It obtains a common
dated Geofabrik snapshot, Planetiler v0.10.2, auxiliary water/lake/Natural Earth
datasets, pinned source styles and fonts, OpenLayers and the three stock images.
Its `work/lock.json` records source URLs, timestamps, SHA-256 checksums and image
IDs/registry digests. Repeating prefetch resumes downloads and retains the resolved
snapshot. Downloads that should be ZIP/JAR files are checked as archives.

Build containers use `--network=none`. Osmium streams a sorted merge and removes
duplicate shared objects. The merged file has no header bounds, so Planetiler
receives explicit bounds from the merged data report. The smoke build clips Prague
and supplies its own bounds. Planetiler builds one regional MVT archive through
zoom 14; display zooms 15–18 reuse this highest source level. Tile geometry is
generalized and quantized: this is a cartographic product, not a lossless OSM export.

GeoServer validates style fields against MBTiles metadata. Planetiler lists fields
observed in the extract, so the build declares optional fields used by the styles
even when an extract has no values for them. This changes metadata only; it does
not invent features or values. `metadata-normalization.json` records additions.

Natural Earth datasets at 1:110 million, 1:50 million and 1:10 million become an
indexed Web Mercator GeoPackage. Country labels use supplied label coordinates,
and borders use dedicated line data. At zooms 0–5, the overview supplies all content;
from zoom 6, regional OSM detail overlays the global land/ocean background. Outside
the extracts, the map remains an overview, even when zoomed in.

`MBSTYLE_ROOT_TILE_PIXELS=256` aligns GeoServer's style zooms with the viewer and
WMTS matrix levels. GeoTools otherwise assumes 512 pixels, shifting the transition
by one level; see [GeoTools zoom tuning](https://docs.geotools.org/latest/userguide/extension/mbstyle/overview.html#zoom-level-tuning).
During preparation, the installed MBStyle extension compiles each JSON style to
SLD. The pipeline adds a tiny tolerance (one part in 100 million) to the compiled
scale thresholds so floating-point rounding cannot delay the transition at an
exact WMTS matrix level. Both editable MBStyle JSON and the resulting SLD are
bundled; GeoServer publishes the SLD. No plugin is compiled or modified.

English SLDs also include icon-only rules for missing `name:en` values. GeoServer
otherwise suppresses a combined text/icon symbol when its text is empty. These
rules reuse the original sprite, position, feature filter and scale range; they
do not substitute local text. Both labelled and unlabelled POI icons participate
in GeoServer's label collision handling. English POIs reserve 16 pixels of spacing;
icons without English names reserve 40 pixels and yield to translated labels.
An icon without a translation is still eligible, but is omitted when it conflicts
with another symbol or label. The icon-only rule uses a preserved blank label and
zero-size font, so it draws no text and never falls back to a local name.

Catalog registration is a one-time local production step using GeoServer's REST
interface. The exported `data_dir` already contains the resulting configuration;
deployment does not depend on a publishing API workflow.

## Eastern Europe package

The separate `east-europe-bundle.json` profile covers Poland, Czechia, Slovakia,
Hungary, Romania, Bulgaria, Moldova, Ukraine, Belarus, Estonia, Latvia, Lithuania,
Slovenia, Croatia, Bosnia and Herzegovina, Serbia, Montenegro, Kosovo,
North Macedonia, Albania and Greece. Russia is excluded from the regional sources;
worldwide Natural Earth context is retained. Extracts use the common 2026-10-08
snapshot. Geofabrik border buffers can contain a small amount of neighboring data.

Run the production commands with `--config east-europe-bundle.json`, for example:

```bash
python3 demo.py preflight --config east-europe-bundle.json
python3 demo.py prefetch --config east-europe-bundle.json
python3 demo.py build --config east-europe-bundle.json
python3 demo.py start --config east-europe-bundle.json
python3 demo.py validate --config east-europe-bundle.json
python3 demo.py stop --config east-europe-bundle.json
python3 demo.py export --config east-europe-bundle.json
```

Its work files live in `work/east-europe-bundle/`, prepared runtime in
`packages/east-europe-bundle/`, and export in `exports/east-europe-bundle.tar`.
The archive extracts to `east-europe-bundle/`; run `bash load-and-run.sh` there.
The independent server uses port **8602** and a separate Compose project,
credentials and 20-GiB cache quota. The Czechia/Slovakia demo is unchanged.
This profile limits preparation to eight CPUs, 12-GiB Java heap and a 20-GiB
container budget. Reserve at least 100 GiB of preparation disk space; actual
requirements depend on scratch data and retained exports. Acceptance tests add
high-zoom tile checks and ten style/language renders for each of the 21 capitals.
These are coverage samples, not exhaustive pixel-level country verification.

To verify the exact export through a fresh rootless Podman Compose startup:

```bash
python3 tests/offline_bundle.py exports/east-europe-bundle.tar \
  --destination .artifacts/east-europe-offline-check --port 8603
```

This optional acceptance command requires the Playwright setup described below,
loads the included image, checks all manifest hashes, and starts the restored
catalog without provisioning it again. The destination must not already exist.
The generated `exports/east-europe-bundle.validation.json` is tied to the exact
archive checksum. Runtime outbound access is blocked by an internal network.

## Run on an air-gapped Windows workstation with Podman Desktop

Use Windows PowerShell **5.1** or PowerShell **7**, with Podman Desktop installed
and a **running Linux AMD64 Podman machine** prepared before entering the isolated
network. The launcher uses `podman.exe` directly; Python, Compose, downloads, and
image builds are not required. It does not install, start, resize, or reconfigure
the Podman machine, change its default connection, or change Windows firewall rules.

Transfer the private `east-europe-bundle.tar`, its adjacent `.tar.json` checksum
sidecar, and `load-and-run.ps1`. Verify the archive before extraction:

```powershell
$archive = 'D:\Maps\east-europe-bundle.tar'
$expected = Get-Content -LiteralPath ($archive + '.json') -Raw | ConvertFrom-Json
if ((Get-Item -LiteralPath $archive).Length -ne $expected.bytes -or
    (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected.sha256) {
    throw 'Archive size or SHA-256 mismatch'
}
# Use a fresh destination; do not extract over a previously running deployment.
New-Item -ItemType Directory -Path 'D:\Maps\Fresh deployment' -ErrorAction Stop
tar.exe -xf $archive -C 'D:\Maps\Fresh deployment'
if ($LASTEXITCODE -ne 0) { throw 'Archive extraction failed' }
```

The launcher accepts an **extracted directory**, not a tar file. Future exports
include it inside the bundle. For the existing validated archive, keep the new
script alongside the archive and supply the extracted path; repacking is unnecessary:

```powershell
& 'D:\Maps\load-and-run.ps1' -BundlePath 'D:\Maps\Fresh deployment\east-europe-bundle'
# When the script is included in the extracted bundle:
# .\load-and-run.ps1
```

Use your organization's approved script execution policy. No permanent policy
change or Administrator PowerShell session is required by the launcher.

| Parameter | Default / purpose |
| --- | --- |
| `-BundlePath` | Directory containing the script; must be an extracted, unmodified bundle |
| `-Connection` | Current Podman connection; explicitly select one from `podman system connection list` if needed |
| `-Name` | `east-europe-bundle`; prefix for the container, internal network, and volumes |
| `-Port` | Bundle `.env` value, **8602** for East Europe |
| `-BindAddress` | `127.0.0.1`; optional IPv4 address, or `0.0.0.0` for all interfaces |
| `-TimeoutSeconds` | **600** seconds for WMS startup readiness; individual HTTP requests have bounded timeouts |

The first run verifies every file listed in `SHA256SUMS`, imports the locked
GeoServer image if absent, then copies the catalog/datasets, fonts, and cache into
three named Podman volumes: `<name>-catalog`, `<name>-fonts`, and `<name>-cache`.
The source directory remains unchanged. Copied files are checked again inside
the Podman machine, and Linux ownership is set there to UID/GID 2000. Fonts are
read-only in the serving container. Credentials pass through process environment
variables, not command-line values or temporary credential files. Container
inspection and logs can still expose sensitive information; keep them private.

Budget disk space separately on Windows and inside the Podman machine: retain
space for the 9.58-GiB archive plus its extracted files on Windows, and for a second
copy of the runtime directories, the loaded image, and future tile cache growth
inside Podman. The prepared cache quota is 20 GiB. Start with 4 vCPU and 16 GiB RAM;
the bundle configures a 4-GiB GeoServer heap. These are deployment budgets, not
measured Windows minimums. Copying and hashing can take several minutes and occur
before the readiness timeout starts. Failed copies retain their volumes for
inspection; the script never prunes Podman or silently replaces existing data.

Repeated runs verify the original extraction again, reuse a matching deployment,
and start a stopped container. Bundle or launch-setting changes require a different
`-Name` (and an unused port if both deployments will run). Conflicting resources,
missing initialization markers, and partial volume sets fail with an explanation.
Keep the original extraction immutable: edit the running catalog through GeoServer,
not the source bundle. The runtime's mutable catalog/cache are not compared against
their original hashes on restart.

After successful Windows-side WMS/WMTS capabilities and PNG checks:

- Viewer: `http://127.0.0.1:8602/geoserver/www/basemap/index.html`
- WMS: `http://127.0.0.1:8602/geoserver/wms`
- WMTS: `http://127.0.0.1:8602/geoserver/gwc/service/wmts`

The script prints commands for the selected connection and deployment name:

```powershell
podman stop east-europe-bundle
podman start east-europe-bundle
podman logs --tail 100 east-europe-bundle
```

Stopping or removing the serving container retains the named volumes. The restart
policy applies while the Podman machine is running; this script does not arrange
Windows login/boot startup. An internal container network and disabled image/plugin
downloads preserve offline operation. LAN access additionally requires appropriate
Podman/Windows forwarding and firewall configuration; changing `-BindAddress`
alone does not establish LAN reachability.

For failures, check `podman info`, the selected connection, Podman-machine free
space, resource labels, and private container logs. Port conflicts or Windows
forwarding failures must be resolved before HTTP readiness can pass. Interrupted
initialization is retained rather than automatically reset: inspect its resources
and use a new `-Name` for a fresh deployment. Never remove unrelated volumes or
use a global prune command to recover this demo.

The dependency-free launcher tests can be run in Windows PowerShell:

```powershell
powershell.exe -NoProfile -File .\tests\test_windows_launcher.ps1
```

These use a simulated Podman interface. They do **not** establish native Podman
mount/copy behavior, port forwarding, or live rendering correctness. Native
acceptance requires a separately authorized deployment, first-start and restart
checks, WMS/WMTS rendering, and confirmation that source dataset hashes are unchanged.
The earlier Linux export-validation report is not Windows deployment evidence.

### Replace the launcher deployment with Windows Compose

If GeoServer is healthy inside its container but the launcher reports
`Timed out waiting for Windows-side WMS readiness`, its internal bridge may be
blocking published ports. Use the standalone [compose.windows.yaml](compose.windows.yaml)
to replace that container on a regular bridge. Do not combine it with either
`compose.yaml` or `compose.podman.yaml`, and do not switch Podman connections or
rootful/rootless mode during this migration: the existing image and volumes must
remain visible to the selected connection.

This configuration reuses the launcher's three named volumes as **external**
volumes; it refuses to create empty replacements. It restores the permission hook
inside the new container without Windows bind mounts, retains the catalog and
credentials, disables image pulls/plugin downloads, and publishes on
`127.0.0.1:8602` by default. The new regular bridge allows outbound routing through
the Podman machine; the workstation/network air gap provides external isolation.
It is a migration for successfully initialized volumes, not a first-install loader.

Copy `compose.windows.yaml` into the extracted bundle beside its original `.env`.
Leave the existing files and `SHA256SUMS` unchanged. In Windows PowerShell, run
these preparation checks **before stopping the old container**:

```powershell
Set-Location 'D:\Maps\east-europe-bundle'  # Your extracted bundle directory
function Invoke-CheckedPodman {
    & podman @args
    if ($LASTEXITCODE -ne 0) { throw 'Podman command failed; stop here.' }
}
Invoke-CheckedPodman compose version
$env:BASEMAP_NAME = 'east-europe-bundle'  # Match the original -Name, if customized
foreach ($suffix in @('catalog', 'fonts', 'cache')) {
    Invoke-CheckedPodman volume exists "$env:BASEMAP_NAME-$suffix"
}
Invoke-CheckedPodman exec $env:BASEMAP_NAME test -s /opt/geoserver/data_dir/.offline-launcher-ready
# Pin the replacement to the exact image already running; do not pull a tag.
$env:GEOSERVER_IMAGE = (Invoke-CheckedPodman inspect --format '{{.Image}}' $env:BASEMAP_NAME).Trim()
Invoke-CheckedPodman image exists $env:GEOSERVER_IMAGE
# Rendered configuration includes the password, so suppress successful output.
Invoke-CheckedPodman compose --env-file .env -f compose.windows.yaml config > $null
```

`podman compose` requires an already installed Compose provider. If the version or
configuration check fails, retain the old deployment and resolve the missing
offline prerequisite before proceeding. No tool installation is attempted here.

Once these checks pass, stop and remove **only the old serving container**. The
launcher did not create a Compose project, so `compose down` cannot remove it:

```powershell
Invoke-CheckedPodman stop $env:BASEMAP_NAME
Invoke-CheckedPodman rm $env:BASEMAP_NAME
Invoke-CheckedPodman compose --env-file .env -f compose.windows.yaml up -d
```

No `-v`, `volume rm`, or prune command is used. The old internal network can remain
unused. After GeoServer finishes starting, test from **Windows**, not `podman exec`:

```powershell
curl.exe --noproxy "*" --max-time 15 -sS -D - -o NUL `
  'http://127.0.0.1:8602/geoserver/ows?service=WMS&request=GetCapabilities'
```

Expect HTTP 200, then open
`http://127.0.0.1:8602/geoserver/www/basemap/index.html`.
Container health alone does not establish Windows/browser reachability. This
Compose file is configuration-validated; live acceptance on the restricted
workstation still requires that Windows-side request to succeed.

Use Compose for subsequent lifecycle operations, from the same bundle directory:

```powershell
podman compose --env-file .env -f compose.windows.yaml down
podman compose --env-file .env -f compose.windows.yaml up -d
```

External volumes survive `down`. Do not rerun the original PowerShell launcher
against this Compose-managed deployment. If `-Name` was customized, set
`BASEMAP_NAME` to that name in each new PowerShell session. The original bundle's
image tag remains available locally after the launcher import; the first migration
above additionally pins the exact running image through the process environment.
Keep Compose output and logs private because providers may display credentials.

## Configuration and updates

Edit **config.json** before prefetch. Important settings:

| Setting | Default / meaning |
| --- | --- |
| `countries` | Geofabrik identifiers: `europe/czech-republic`, `europe/slovakia` |
| `snapshot` | `latest-common`, resolved and locked once; or explicit `YYMMDD` |
| `source_maxzoom` | 14 |
| `display_maxzoom` | 18 |
| `threads`, `java_heap`, `build_memory` | 8, 16g, 24g |
| `engine` | `auto`, preferring Podman when available; or `docker` / `podman` |
| `port`, `geoserver_heap` | 8600, 4G |
| `smoke_bbox` | Prague bbox; change when testing different countries |

To update countries or the snapshot, stop the existing demo, move `bundle/` and
`work/smoke/` aside, change the configuration and repeat the workflow. Input checks
prevent quietly mixing an old MBTiles file with a new manifest. An existing
prefetch lock does not advance `latest-common`; choose an explicit new date for
an intentional update. A fresh bundle also starts with a fresh tile cache.

Generated inputs, bundle, caches, image archives and credentials are ignored by Git.
The source repository contains the reproducible production code and documentation.

To refresh styles and the viewer on an already prepared, running demo:

```bash
python3 demo.py refresh-styles
python3 demo.py validate
```

This verifies the pinned style archive, generates both language variants, publishes
changed styles and clears only their server-side tile caches using GeoWebCache's
[layer truncation API](https://geowebcache.osgeo.org/docs/current/rest/masstruncate.html).
Successful style hashes make repeated refreshes a no-op for publication/cache
invalidation. It checks that both dataset files have identical SHA-256 hashes
before and after. No data download, Osmium merge, Planetiler run, metadata update
or dataset rewrite occurs. Keep data/grid settings unchanged for this command.
Reload the viewer after a refresh. Its map URLs include each style's revision so
the browser fetches updated tiles. Stop, export and start again to refresh the
portable package.

## Run the exported package on GCP

Use a Linux AMD64 Compute Engine VM with Docker Engine/Compose already installed.
Start with 4 vCPU, 16 GiB RAM and enough persistent disk for the exported bundle
and tile cache. Transfer `exports/osm-basemap-<snapshot>.tar` or the named
`exports/east-europe-bundle.tar` through your approved path into the isolated network. No GCP credentials are embedded in the package.

```bash
tar -xf osm-basemap-261008.tar
cd osm-basemap
bash load-and-run.sh
```

For the Eastern Europe profile, extract `east-europe-bundle.tar`, change into
`east-europe-bundle/`, and run the same loader. Its port is **8602**; substitute
that port in the example service URLs below.

The loader checks file checksums, loads the archived image, verifies its image ID,
sets ownership only within this bundle, and starts Compose with pulls disabled.
Kartoza expands its bundled `mbstyle-plugin` and `mbtiles-store-plugin` archives
on startup. Both force-download flags are false. Only MBStyle is activated from
the stable extension set, avoiding unrelated default plugins.

Fonts are mounted read-only at `/opt/fonts`; styles/sprites live in the data
directory. A small mounted initialization hook repairs ownership of bootstrap
catalog files created by Kartoza before it drops to its service user. Map datasets
are excluded from that hook. Runtime state and cache persist across restarts.

The Compose network is internal. The default published address is **127.0.0.1:8600**.
Use an SSH/IAP tunnel for the first demo. For access through a private VM address,
set `BIND_ADDRESS` in `.env` and allow only the intended private clients in your
existing GCP firewall. If a reverse proxy changes the external address/path,
configure GeoServer's proxy base URL accordingly.

`.env` contains a generated administrator password and must be kept private.
Anonymous map requests are enabled; administration remains authenticated. The
export contains GeoServer security state and should be treated as a private
deployment artifact. Do not publish it or the raw logs to a public repository.

```bash
docker compose ps
docker compose logs --tail=100 geoserver
docker compose restart geoserver
docker compose down      # Retains the bind-mounted data and cache
```

After changing styles/data, seed a new bundle/cache or truncate the affected
GeoWebCache layers before demonstrating them. Avoid reusing cached tiles from a
different dataset/style build. Preserve `cache/geowebcache.xml` and
`cache/geowebcache-diskquota.xml`: they contain the custom WMTS grid and quota
configuration. Deleting the whole cache directory also deletes those settings.
For a cold-cache test, stop the service and remove only its rendered tile PNGs.
The provided startup health check verifies WMS
capabilities; the production acceptance script additionally verifies rendering.

## Viewer and services

- Viewer: `http://localhost:8600/geoserver/www/basemap/index.html`
- WMS: `http://localhost:8600/geoserver/wms`
- WMTS: `http://localhost:8600/geoserver/gwc/service/wmts`
- Groups: `omt:osm-bright`, `omt:positron`, `omt:dark-matter`, `omt:toner`,
  `omt:maptiler-basic`, and `omt:world`.
- English groups use the same names with `-en`: for example `omt:osm-bright-en`
  and `omt:world-en`. Select these names in WMS `LAYERS` or WMTS `LAYER`.
- WMS supports EPSG:3857 and EPSG:4326. For WMS 1.3.0 EPSG:4326, bbox axes are
  latitude, longitude. WMTS uses the explicit EPSG:3857 matrix set and PNG output.

The viewer supports switching between WMS and WMTS, Local and English labels,
and includes world/country/
Prague/Bratislava views. Its scripts, CSS and all map resources are local.

## Validation and limits

```bash
python3 -m unittest discover -s tests -v
```

These focused tests check geographic math, style composition and invalid-response
detection. They do not establish rendering correctness. `demo.py validate` checks
the actual SQLite data, merged OSM ordering, fonts, capabilities, all five styles
in both languages, WMS reprojection, high-zoom requests and repeated WMTS responses. True cold/warm
measurements require an initially empty tile cache, as in the exported-bundle test.
It verifies an
internal container network and failed outbound access and saves images, timings,
version information and logs under `bundle/validation/`.

For optional browser screenshots, prefetch Playwright on the preparation machine:

```bash
npm install --prefix .artifacts/browser playwright@1.56.1
PLAYWRIGHT_BROWSERS_PATH="$PWD/.artifacts/browsers" .artifacts/browser/node_modules/.bin/playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.artifacts/browsers" node tests/browser.cjs
```

Browser automation is a preparation-side check and is not required by the runtime.
It rejects external resource requests and saves screenshots of all five styles
in both languages, including the English default and language switching.

The default Czechia/Slovakia demo also has an optional live POI collision check:

```bash
python3 tests/poi_collisions.py
PLAYWRIGHT_BROWSERS_PATH="$PWD/.artifacts/browsers" node tests/poi_collisions.cjs
```

This tests nearby untranslated ATMs with collision handling on/off, then compares
English/local POI placements in six views. Diagnostic renders substitute 11-pixel
magenta markers for sprites to make counting reliable; they measure diagnostic
placements, not exact original sprite counts. Evidence is saved under
`bundle/validation/poi-collisions/`. The tests do not modify the datasets or catalog.

Inspect the PNGs for label/symbol quality and coastline/border artifacts. The
report explicitly separates successful HTTP/data checks from visual review.
Local Podman validation is not evidence of execution on a GCP VM. GeoServer is
also a different renderer from MapLibre, so recognizable styling is the target,
not pixel-identical output to a browser vector renderer.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for sources and attribution.
