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
and tile cache. Transfer `exports/osm-basemap-<snapshot>.tar` through your approved
path into the isolated network. No GCP credentials are embedded in the package.

```bash
tar -xf osm-basemap-261008.tar
cd osm-basemap
bash load-and-run.sh
```

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
