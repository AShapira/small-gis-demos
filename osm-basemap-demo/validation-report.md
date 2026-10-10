# OSM basemap demo: measured validation

Validated locally on 2026-10-10, Linux AMD64 under WSL2, using rootless Podman
5.8.2 and podman-compose 1.6.0. The generated runtime uses the unmodified
`docker.io/kartoza/geoserver:3.0.1` image. No GCP VM was accessed.

## Generated data

| Item | Result |
| --- | --- |
| Geofabrik snapshot | 2026-10-08, Czechia and Slovakia |
| Regional MBTiles | 964,108,288 bytes; 77,218 tiles; zooms 0–14 |
| Worldwide GeoPackage | 43,474,944 bytes; 21 indexed layers |
| Merged nodes / ways / relations | 133,670,859 / 14,082,234 / 330,643 |
| Merge checks | Sorted objects; no multiple versions |
| Planetiler v0.10.2 elapsed | 130.53 seconds, 8 build threads |
| Natural Earth conversion elapsed | 5.99 seconds |
| Largest sampled Planetiler heap | 7.3 GB, from its periodic log; not peak RSS |
| GeoServer memory after English/local acceptance requests | 2.99 GB; single sampled value |
| Configured heap budgets | Preparation 16 GiB; GeoServer 4 GiB |
| Runtime export, including image | Approximately 3.2 GB; exact bytes/hash in export sidecar |

These timings exclude downloads, Osmium merging and manual review. The host has
28 logical CPUs and 47 GiB RAM; Planetiler was limited to 8 CPUs/24 GiB. Serving
was not benchmarked on a dedicated 4-vCPU GCP machine. The proposed 4-vCPU/16-GiB
serving size remains an initial deployment budget.

## Service and offline checks

- Eight Python regression tests pass, including strict English labels and the
  renderer's missing-name icon case. Shell/JavaScript syntax checks pass.
- SQLite integrity checks pass for both databases. All source zoom levels and
  required vector layers are present. High-zoom tiles cover Prague, Bratislava
  and the shared border.
- All twelve groups appear in WMS and WMTS capabilities. GeoServer reports 3.0.1,
  GeoTools 35.1 and GeoWebCache 2.0.1.
- **162 PNG rendering checks pass**, covering all five styles in both languages, world/country/
  city/street views, shared border, WMS EPSG:4326 reprojection, every display
  level 14–18, and WMTS at levels 15 and 18.
- Noto Sans, Metropolis and Nunito are registered. Browser checks save fourteen
  screenshots with zero JavaScript errors and zero external resource requests.
- The stock image was saved, loaded from its archive and started through Compose
  with the exported catalog in a separate directory. Checksums were verified.
  The internal container network and a failed outbound HTTPS attempt were checked.
- Restarting the service preserves the catalog, normalized styles and WMTS grid.
  A 20-GiB persistent GeoWebCache quota is enabled; metatiling is 4×4 with 64-pixel
  gutter. The quota is a periodic cleanup target, not a hard filesystem limit.
- `refresh-styles` publishes the six English groups without rewriting either
  database. An unchanged refresh preserves all cached PNGs. Removing one
  successful-publication stamp exercises a retry: only `osm-bright-en` is
  republished and its 112 cached tiles are removed; every other cache stays intact.
  GeoWebCache's mass-truncation endpoint requires `Content-Type: text/xml`.

The original local-style cold-cache measurements below used an empty PNG cache after restart.
WMS requests had already warmed the JVM and data access; these are cold **tile
cache** timings, not the first request to a completely cold OS/JVM.

| Style | Uncached WMTS z15 | Immediate cached repeat |
| --- | ---: | ---: |
| OSM Bright | 984.7 ms | 6.1 ms |
| Positron | 281.0 ms | 3.7 ms |
| Dark Matter | 277.8 ms | 3.5 ms |
| Toner | 338.9 ms | 3.5 ms |
| MapTiler Basic | 324.7 ms | 3.3 ms |

## Visual review and limits

Inspected contact sheets for every style at world, country, Prague, Bratislava,
shared-border, rural and street scales, plus the zoom 5–6 transition. OSM Bright
shows POI symbols and Czech/Slovak diacritics; buildings, river bridges, roads,
rail and land-use detail are visible. The five palettes remain recognizable.
Positron and Dark Matter intentionally have subdued contrast. This review is a
sampled cartographic check, not an exhaustive audit of every tile or a claim of
pixel equality with MapLibre.

The English variants were separately inspected at those scales. Actual tile
attributes and map labels confirm **Praha → Prague**, **Plzeň → Pilsen**, and
**Dunaj → Danube**. Sampled Prague POIs such as Euronet and Trdelník have a local
`name` and fallback-filled `name_en`, but no `name:en`. Their local text disappears
in English. Their symbols remain eligible and the surrounding buildings/roads are unchanged.
GeoServer needs supplemental icon-only SLD rules for this: an empty label in a
combined text/icon symbol otherwise suppresses its icon as well. These rules keep
the original graphics, filters and zoom limits. Icon-only rules now use GeoTools'
native label collision handling with a preserved blank label and zero-size font.
Translated POIs reserve 16 pixels of spacing. Untranslated icons reserve 40 pixels
and have lower priority, so they yield to translated labels and other symbols.
There are no standalone point-symbol fallbacks bypassing collision handling.

A live collision control uses two nearby Euronet ATMs with no `name:en`: disabling
collision handling paints two markers; enabling it paints one. Diagnostic full-map
renders replace POI sprites with 11-pixel magenta squares while retaining text,
filters, placement and collision settings. The results below count these diagnostic
placements, not exact production sprites. No diagnostic English markers overlap.

| View | Local placements | English placements |
| --- | ---: | ---: |
| Prague z14 | 107 | 35 |
| Prague z15 | 73 | 21 |
| Prague z16 | 231 | 45 |
| Prague z17 | 138 | 38 |
| Prague z18 | 93 | 42 |
| Bratislava z15 | 61 | 21 |

The regular map screenshots were also inspected for the resulting icon density.
Viewer requests include a style revision so reloading after a refresh fetches the
updated tiles instead of reusing old browser images.

Both dataset SHA-256 values are unchanged throughout the language update:

| Dataset | SHA-256 |
| --- | --- |
| `regional.mbtiles` | `8548afc1bb01d3622390fde88cf79f2f1fb22b4acd6ff8353291d66277aa34b5` |
| `world.gpkg` | `6c4c96ee7cb7ea71d746378d699479f38cd0745f6b3e9480201af93d1f6fc5e0` |

No OSM download, merging, tile generation or database metadata rewrite was performed
for the English update. Overview English names come directly from Natural Earth
`NAME_EN`; regional names come only from explicit `name:en` values.

Two production fixes are included: optional MBTiles schema fields are declared
without changing tile bytes, and MBStyle is compiled to SLD with normalized scale
thresholds so exact WMTS level 6 selects regional data. Editable JSON is retained.
Overview labels/borders stop at that transition; worldwide land/ocean/lakes persist.

Planetiler logs include geometry repairs and incomplete source relations
(185 multipolygon and 1,312 boundary missing-way diagnostics). Generation completed;
these source-data diagnostics are retained in the bundle rather than treated as
proof that every original OSM relation is complete.

Evidence is in `bundle/validation/`: `report.json`, `browser-report.json`,
`english-attributes.json`, `english-cache-refresh.json`, `style-refresh.json`,
`poi-collisions/report.json`, `poi-collisions/method.json`,
`*-inspection.png`, individual PNGs, capabilities, fonts, service log and build log.
The export's exact checksum, size, fresh-container startup time and offline checks
are in `exports/osm-basemap-261008.validation.json` alongside its archive.
`source-manifest.json` records input checksums, versions, timestamps, image identity
and bundled plugin archive checksums. Credentials remain only in ignored private
runtime files. GCP deployment requires the target VM and network details.
