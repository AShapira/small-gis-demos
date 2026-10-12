# east-europe-bundle: measured validation

Prepared and validated locally on 2026-10-11, Linux AMD64 / WSL2, using stock
Kartoza GeoServer 3.0.1 and rootless Podman. No GCP VM was accessed.

## Coverage and sizes

The 21-country configuration is `east-europe-bundle.json`: Poland, Czechia,
Slovakia, Hungary, Romania, Bulgaria, Moldova, Ukraine, Belarus, Estonia, Latvia,
Lithuania, Slovenia, Croatia, Bosnia and Herzegovina, Serbia, Montenegro, Kosovo,
North Macedonia, Albania and Greece. No Russian extract was downloaded. Worldwide
Natural Earth overview coverage remains. Geofabrik border buffers and complete
objects can extend slightly outside the selected countries.

| Item | Measured result |
| --- | --- |
| Common source snapshot | 2026-10-08 |
| Source PBF total | 7,456,988,312 bytes |
| Regional MBTiles | 7,919,923,200 bytes (7.92 GB / 7.38 GiB) |
| Tile count | 2,569,660; zooms 0–14 |
| Worldwide GeoPackage | 43,474,944 bytes; 21 indexed layers |
| Merged nodes / ways / relations | 875,635,376 / 104,442,800 / 1,827,399 |
| Merge integrity | Sorted objects; no multiple versions |
| Planetiler elapsed | 950.76 seconds (15.85 minutes) |
| World conversion elapsed | 6.68 seconds |
| Preparation limits | 8 CPUs, 12-GiB Java heap, 20-GiB container memory |
| Largest sampled build container memory | 20.96 GB; periodic sampling, not guaranteed peak |
| GeoServer memory after acceptance renders | 4.412GB / 50.51GB |
| Persistent tile-cache quota | 20 GiB shared by all twelve groups |

Build timings exclude downloads, merging, publication and validation. Samples
come from this shared workstation, not a dedicated 4-vCPU GCP server. Planetiler
reported invalid source geometries that remained empty after repair (including
41 feature polygons); complete counters are preserved in `build-evidence/planetiler.log`.
This cartographic output generalizes OSM data and is not a lossless OSM export.

## Checks completed before export

- SQLite integrity checks pass for both datasets. Required vector layers and all
  zoom levels are present. Decoded maximum-zoom tiles contain roads and buildings
  at all 21 capitals; the Czechia/Slovakia border also has road data.
- All twelve local/English groups appear in WMS and WMTS capabilities.
- **372 PNG renders pass**, including all five styles in both languages at each
  capital, world/country/city/street views, the shared border, the zoom 5–6
  transition, display zooms 14–18, WMS EPSG:4326 and EPSG:3857, and PNG WMTS.
- English compiled styles use only `name:en`/`NAME_EN` for name labels. Unnamed
  POI fallback rules retain collision handling and spacing. Fonts are registered.
- Browser acceptance passes with **14 screenshots**, both language variants,
  WMS/WMTS switching, no page errors and no external resource requests.
- Representative contact sheets were visually inspected for all five English
  styles at world, country, city (Kyiv/Athens), street, border and transition
  scales, and both languages at world/city/street scales. Coverage is sampled;
  the millions of output tiles were not individually inspected.
- Runtime uses an internal network; an outbound HTTPS request was blocked.

## Sampled English WMTS request timings

| Style | First request, seconds | Repeat request, seconds |
| --- | --- | --- |
| osm-bright English | 0.7412 | 0.0042 |
| positron English | 0.2405 | 0.0038 |
| dark-matter English | 0.2313 | 0.0029 |
| toner English | 0.2909 | 0.0026 |
| maptiler-basic English | 0.2791 | 0.0030 |

These are individual local requests, not a throughput benchmark. The separate
fresh-export test uses an initially empty PNG cache and records cold/warm timings.

## Export and deployment

The prepared runtime is `packages/east-europe-bundle/`; the transferable archive
is `exports/east-europe-bundle.tar`. Its adjacent `.tar.json` records exact size
and SHA-256. `east-europe-bundle.validation.json` records the later fresh-directory
image-load, offline cold-start and service/browser checks against that exact hash.
A successful pre-export check alone is not proof of that later test.

Start the extracted package with `bash load-and-run.sh`; its port is 8602.
See `PACKAGE.md` and `README.md` inside the archive for private-network GCP setup,
credentials, persistent storage, cache invalidation and restart instructions.

Dataset SHA-256:

- `regional.mbtiles`: `d0113656d1d7f11010df4e73e71dae12d264d844e002e0e8aaa31559733c0b69`
- `world.gpkg`: `37d55030ad3424c68a2c4c6695acdd7bfeca6a79414f1be2545feda15127ded6`

## Exact export verification completed

`east-europe-bundle.tar` is **10,289,469,440 bytes** (10.29 GB / 9.58 GiB).
SHA-256: `aae794e094f98287459fad5d664233c478bd93bac60e1f2cfe1946e9bb78eaa1`.

The exact archive was extracted into a fresh directory. All 1,747 file
checksums passed; its included image was loaded and the restored catalog started
without reprovisioning. Startup took **17.85 seconds** on the shared
workstation. The internal network blocked outbound HTTPS. All **372 renders**,
five additional English WMTS zoom-6 checks and **14 browser screenshots** passed.
Both dataset checksums remained unchanged. The temporary Compose instance was
removed after validation, preserving its evidence directory.

The original Czechia/Slovakia dataset checksums and container ID also remained
unchanged; that original service is still running. The named package's production
instance is stopped for a consistent export.
