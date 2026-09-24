# GeoServer raster-compression benchmark

Benchmark large RGB rasters through GeoServer 3.0.1 on **Windows Podman
Desktop**. Compare source storage, conversion cost, image quality, WMS rendering
and human-paced WMTS browsing with verified tile-cache outcomes.

**The bounded 10-user study completed on 2026-09-16.** See the
[completed-study summary](benchmark-results.md) for scope and limitations.
Synthetic fixtures never qualify deployment resources. The optional full
100-user plan was not executed.

## What is compared

Uncompressed, LZW, DEFLATE, PackBits, JPEG quality 70/80, ZSTD level 3/9,
matched COG layouts, and PNG/JPEG GeoPackage. WebP-in-TIFF and WebP-in-GeoPackage
are compatibility probes; WebP WMS delivery is a separate extension profile.
The 5 GB input target counts **valid 8-bit RGB sample bytes**, excluding
overviews. It uses real 10 m Sentinel-2 imagery across Israel and neighboring
areas, not repeated pixels or artificially enlarged source data.

See [methodology](methodology.md), [operations](operations.md), and
[third-party dependencies](../THIRD_PARTY_NOTICES.md).

## Requirements

- Windows Podman Desktop with a running WSL machine and its **rootless**
  connection. The default config uses `podman-machine-default`, not `-root`.
- Python 3.11+ for the lightweight controller; geospatial libraries run in
  containers. Install controller dependencies with `python -m pip install -e .`.
- A CDSE account and existing `cdse_username` / `cdse_password` files in a
  protected directory. The launcher imports benchmark-specific Podman secrets.
- Approximately 150 GB free on the physical Windows drive, subject to the
  source-selection estimate; leave another 30 GB free. The guest filesystem's
  virtual free space is not proof of physical host space.
- Start with 8 vCPU/16 GiB for GeoServer and separate capacity for processing
  and load generation. Final sizing requires measured acceptance.

## Windows PowerShell

From this repository, using the Python executable selected for the controller:

```powershell
python -m pip install -e .
.\scripts\benchmark.ps1 -Action build
.\scripts\benchmark.ps1 -Action build-webp
.\scripts\benchmark.ps1 -Action estimate
.\scripts\benchmark.ps1 -Action doctor -CredentialDirectory C:\secure\cdse
.\scripts\benchmark.ps1 -Action smoke
.\scripts\benchmark.ps1 -Action run
```

Use `-PythonExe C:\path\to\python.exe` and `-PodmanExe C:\path\to\podman.exe`
if they are not discoverable. Use a native Windows checkout/staging directory
for Windows operation. Building uses a tar archive over stdin to avoid
Windows/WSL build-context path translation problems.

The full controller runs in the foreground and may require multiple days.
Keep its terminal, Windows and the Podman WSL machine running. It does not
install a scheduler or modify Windows sleep settings. Ctrl+C stops the
controller; see the operations guide before resuming an interrupted cell.

## RHEL/WSL source development with Windows execution

The controller can invoke the Windows executable from WSL. No RHEL container
engine is used for the benchmark:

```bash
export RASTERBENCH_PODMAN="/mnt/c/Users/<user>/AppData/Local/Programs/Podman/podman.exe"
export RASTERBENCH_CDSE_SECRET_DIR=/path/to/protected/cdse-secrets
python3 scripts/bootstrap.py
python3 scripts/build_webp.py
python3 -m rasterbench.cli doctor
python3 -m rasterbench.cli smoke
python3 -m rasterbench.cli run
```

## Commands

All CLI commands accept `--config configs/full.yaml`.

| Command | Purpose |
|---|---|
| `estimate` | Calculate matrix duration and initial disk budget without starting containers |
| `doctor` | Record engine, image, worker dependencies and available storage |
| `select` | Freeze catalogue product selection without downloading imagery |
| `fetch` | Download frozen products, resume partial transfers and verify archive CRC/SHA-256 |
| `prepare` | Build and validate the canonical mosaic and overview pyramid |
| `convert` | Generate candidates; `--variant jpeg80` selects one |
| `validate` | Stream all pixels and overviews; measure lossy errors and write visual crops |
| `serve` | Start the isolated GeoServer instance |
| `smoke` | Convert synthetic fixtures and verify actual GeoServer reader/renderer support |
| `benchmark` | Execute screening, capacity, delivery and first-access stages |
| `report` | Rebuild/export reports exclusively from saved results |
| `run` / `resume` | Execute remaining pipeline stages with preserved configuration |
| `status` | Read the controller checkpoint |

Edit `study_id` when changing any study configuration. Credentials are not
configuration fields. Downloads, imagery and raw results stay in named volumes;
reports are exported to the configured Windows directory. Nothing publishes to
an external service or repository.

## Development checks

The unit suite runs in the worker environment and does not download imagery:

```powershell
podman --connection podman-machine-default exec rasterbench-israel-rgb-5gb-worker python -m unittest discover -s /app/tests -v
```

Use `smoke` for actual GeoServer compatibility. A working GDAL encoder alone is
not evidence that GeoServer can serve the corresponding raster.

## Optional overnight plan: 10 users

The supplied example plan reuses the full study's verified source data and files, with
its own resolved workload configuration, plan-tagged job IDs and checkpoint:

```bash
python3 -m rasterbench.overnight --plan configs/overnight-10.yaml
```

The historical morning target is a planning field, not a stop condition. This
command continues with repeated endurance cycles until stopped. Review
[operations](operations.md#bounded-endurance-completion) before running it.
The completed study used a bounded finish after two endurance cycles. All
resource conclusions apply to 10 users only. Inspect local process/checkpoint
state before starting any controller; never run duplicate controllers.
