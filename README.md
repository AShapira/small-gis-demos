# small-gis-demos

Practical GIS demonstrations with reproducible tests and documented limits.

| Demo | Purpose | Start here |
| --- | --- | --- |
| GeoTIFF merge for QGIS 4.2 | Merge aligned rasters into one file or an optional target size (+10% allowance), use available CPU/RAM with optional limits, and exhaustively verify pixels and masks. | [geotiff-merge/](geotiff-merge/README.md) |
| GeoTIFF overview generator | Rebuild external overviews with GDAL, bounded parallelism, dry runs, logs and cooperative cancellation; CLI and QGIS Python-console entry points. | [overview-generator/](overview-generator/README.md) |
| VRT with JPEG overviews | Combine folder trees of aligned RGB/grayscale GeoTIFFs into one VRT with JPEG overviews and lossless transparency masks; OSGeo4W/QGIS-shell CLI. | [vrt-overviews/](vrt-overviews/README.md) |
| GeoServer raster benchmark | Compare raster compression, conversion quality, WMS/WMTS delivery and resource profiles using Windows Podman Desktop. | [Benchmark guide](docs/geoserver-benchmark.md) |
| Cesium WMS gutter comparison | Compare gutters 0, 20, 64, 128 and 256 on rotated GeoTIFFs, with sharp nearest-neighbour rendering and browser performance measurements. | [cesium-wms-gutter-demo/](cesium-wms-gutter-demo/README.md) |

The [rotated-GeoTIFF study](docs/rotated-geotiff-study.md) documents the
reproduction, tested workarounds, remaining footprint-edge failures and an
isolated experimental source correction. Gutter 128 removes the tested interior
triangles; it is a partial workaround, not a universal fix.

## Overview generator

The standalone script and its tests live under `overview-generator/`.
Run in a Python environment with GDAL installed, such as QGIS's bundled Python:

```bash
python overview-generator/overview-generator.py /path/to/test-rasters --dry-run
python overview-generator/overview-generator.py /path/to/test-rasters --workers 2
```

A real run replaces existing overviews and, by default, removes embedded
overviews from ordinary TIFFs. Read its [README](overview-generator/README.md)
before using real data. Native validation used QGIS 4.2 / GDAL 3.13.1 on
Windows; the original QGIS 3.28 compatibility target remains unverified.

## GeoServer benchmark

The existing benchmark package remains at the repository root:
`rasterbench/`, `configs/`, `infra/`, `scripts/` and `tests/`. Run its commands
from this root. Installing `small-gis-demos` supplies the `rasterbench` CLI;
the GeoTIFF merger and overview generator are independent scripts.

The historical 10-user study completed. Its
[summary and limitations](docs/benchmark-results.md) distinguish measured
results from the larger, unexecuted example matrix. Start with the
[benchmark guide](docs/geoserver-benchmark.md) for setup and
[operations guide](docs/operations.md) for lifecycle and recovery.

Credentials, source imagery, generated test TIFFs, raw workstation logs,
local operational checkpoints and email receipts are excluded from Git.
Public validation summaries retain test outcomes with local paths redacted.

## Tests

- GeoTIFF merge: use QGIS 4.2 Python (GDAL/NumPy included), then run
  `python -m unittest discover -s geotiff-merge/tests -v` (generated rasters only).
- [Overview-generator native Windows tests](overview-generator/tests/README.md)
- VRT/JPEG overview tests: use GDAL 3.8+ Python with NumPy, then run
  `python -m unittest discover -s vrt-overviews/tests -v` (generated rasters only).
- Benchmark unit suite: `PYTHONPATH=.:.build/copernicus python -m unittest discover -s tests -v`
  after `python -m pip install -r requirements-test.txt` and fetching the
  checksum-pinned downloader with `python scripts/fetch_test_dependency.py`.
- Container-backed integration and real benchmark runs are separate, opt-in
  operations; unit tests do not start services or download imagery.
- Cesium adapter unit tests: `cd cesium-wms-gutter-demo && npm ci && npm test`
  with Node.js 22+. The demo README covers the separate browser/GeoServer checks.

Release: **0.6.0**. See [CHANGELOG.md](CHANGELOG.md) and
[third-party notices](THIRD_PARTY_NOTICES.md).
