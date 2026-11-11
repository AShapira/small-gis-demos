# GeoTIFF compression benchmark

Compare GeoTIFF compression and COG layouts, conversion cost and quality, and
GeoServer WMS/WMTS delivery using Windows Podman Desktop.

The historical 10-user study is complete. Read the
[results and limitations](docs/benchmark-results.md); the larger example matrix
was not executed.

## Setup and commands

From the repository root:

```bash
cd geotiff-compression
python -m pip install -e .
python -m rasterbench.cli --help
```

The Python distribution remains `small-gis-demos`, with the `rasterbench` command.
All benchmark commands and relative configuration paths use this folder as their
working directory. See the [benchmark guide](docs/geoserver-benchmark.md) for
Windows PowerShell and WSL setup, and [operations](docs/operations.md) for
credentials, retained resources, pause/resume and recovery.

## Layout

| Folder | Contents |
| --- | --- |
| `rasterbench/` | Controller, workers and reporting |
| `configs/` | Full and overnight example study configurations |
| `infra/` | Containerfiles and pinned dependency manifests |
| `scripts/` | PowerShell launcher, build and study helpers |
| `tests/` | Unit tests and opt-in integration checks |
| `docs/` | Methodology, operations and results |
| `evidence/` | Curated compatibility results |

Local `.runs/` checkpoints and `.build/` caches live inside this folder and are
ignored by Git. When updating an older checkout, move its benchmark `.runs/`,
`.build/copernicus/` and `.build/webp/` here before resuming a retained study.
Keep the existing checkpoint and stop markers. Other demos' caches stay with
those demos. Source imagery and server data remain in the configured volumes.

## Tests

From this folder, using Python 3.11+:

```bash
python -m pip install -r requirements-test.txt
python scripts/fetch_test_dependency.py
PYTHONPATH=.:.build/copernicus python -m unittest discover -s tests -v
```

For PowerShell, set `$env:PYTHONPATH = ".;.build/copernicus"` before running
`python -m unittest discover -s tests -v`. The fetch helper downloads only the
checksum-pinned test dependency when it is missing. Unit tests do not start
services or download imagery; real-container integration is a separate opt-in
step described in the operations guide.

See [third-party notices](THIRD_PARTY_NOTICES.md) and the
[project demo index](../README.md).
