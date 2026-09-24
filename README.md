# small-gis-demos

Practical GIS demonstrations with reproducible tests and documented limits.

| Demo | Purpose | Start here |
| --- | --- | --- |
| GeoTIFF overview generator | Rebuild external overviews with GDAL, bounded parallelism, dry runs, logs and cooperative cancellation; CLI and QGIS Python-console entry points. | [overview-generator/](overview-generator/README.md) |
| GeoServer raster benchmark | Compare raster compression, conversion quality, WMS/WMTS delivery and resource profiles using Windows Podman Desktop. | [Benchmark guide](docs/geoserver-benchmark.md) |

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
the overview generator is an independent script.

The historical 10-user study completed. Its
[summary and limitations](docs/benchmark-results.md) distinguish measured
results from the larger, unexecuted example matrix. Start with the
[benchmark guide](docs/geoserver-benchmark.md) for setup and
[operations guide](docs/operations.md) for lifecycle and recovery.

Credentials, source imagery, generated test TIFFs, raw workstation logs,
local operational checkpoints and email receipts are excluded from Git.
Public validation summaries retain test outcomes with local paths redacted.

## Tests

- [Overview-generator native Windows tests](overview-generator/tests/README.md)
- Benchmark unit suite: `PYTHONPATH=.:.build/copernicus python -m unittest discover -s tests -v`
  after `python -m pip install -r requirements-test.txt` and fetching the
  checksum-pinned downloader with `python scripts/fetch_test_dependency.py`.
- Container-backed integration and real benchmark runs are separate, opt-in
  operations; unit tests do not start services or download imagery.

Release: **0.1.0**. See [CHANGELOG.md](CHANGELOG.md) and
[third-party notices](THIRD_PARTY_NOTICES.md).
