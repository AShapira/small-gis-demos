# Operations and recovery

## Isolation

The launcher selects an explicit Windows rootless Podman connection. It does
not change the default engine, resize WSL, clear host caches, stop other
services, or use the Podman engine socket inside a container. Images and raw
data use the machine's Linux filesystem. Only a loopback HTTP port is exposed.
All owned containers/volumes have the study label. Existing cat-watch remains
on the rootful connection and is inspected only through read-only statistics.

## Credentials and dependencies

Set `RASTERBENCH_CDSE_SECRET_DIR` to a directory holding protected
`cdse_username` and `cdse_password` files. Benchmark-specific secrets are
imported through stdin. A randomly generated GeoServer admin password is also
stored as a Podman secret. Do not place passwords in YAML, command arguments,
commits, reports or logs.

`infra/images.lock.json` pins base/server images and downloader revision and
hashes. The worker records installed Python and OS packages. Rebuild after
source changes; retain the worker image ID and code manifest with every study.
Full reproducibility uses retained artifacts and image IDs rather than a later
resolution of moving image tags.

## Long runs and checkpoints

The host controller writes `.runs/<study_id>/state.json` and holds an advisory
lock. Each cell has a deterministic ID. Artifacts receive completion metadata
only after the operation and its validation succeed. Download `.part` files
can resume; completed downloads are rehashed before reuse. Rerunning conversion
checks completed artifact hashes. A changed configuration requires a new ID.

After interruption, inspect the benchmark loader before `resume`: a Podman
exec/container can outlive an interrupted controller. Stop only owned work
that is still running, preserve volumes, and then retry the cell. Do not run
two controllers against the same study. Do not delete `.runs` merely to bypass
configuration mismatch checks.

Reports are under `/data/<study_id>/report` in the named volume and exported
to the configured Windows path. Raw request/view logs are gzip JSONL under
`runs/<cell_id>`; traces, summaries, telemetry and GC logs accompany them.
The complete evidence remains in the data volume. Keep this volume for auditing
and report regeneration.

## Pausing and cleanup

### Bounded endurance completion

The example overnight plan repeats endurance cycles indefinitely. To finish
after zero-based cycle 1 (two cycles), run the owned finish guard separately:

```bash
python3 -m scripts.finish_endurance --cycle 1
```

It waits for every required completed cell, then writes `STOP_AFTER_CELL` so
the controller stops at a cell boundary. Verify that no duplicate guard or
controller is running first. Keep the marker after completion. Rebuild the
final report from retained exports without starting new measurements:

```bash
python3 -m scripts.final_report --root /path/to/export/israel-rgb-5gb
```

This strict final-report tool targets the historical 154-cell study contract;
use the general `report` command for a different measurement matrix.

### Preserving owned resources

Wait for a conversion or download checkpoint, stop the controller, then stop
the benchmark GeoServer and idle worker. Keep the two volumes, secrets, images
and network to resume. Check container ownership labels before changing any
container with a similar name. Cleanup must name individual resources; never
use `podman system prune`, volume pruning, `wsl --shutdown`, or global cache
flushes as part of this benchmark.

## Known integration checks

- GeoServer external GeoTIFF publication uses the absolute container path as
  the request body; coverage-store URLs use proper file URIs.
- GeoPackage REST store payloads explicitly include their workspace.
- Configure GeoWebCache using XML to preserve array/filter type information.
- GDAL in the pinned Ubuntu image requires the NumPy 1.x ABI.
- Virtual guest disk free space may exceed physical Windows drive free space.
- A successful HTTP 200 response can still contain an OGC exception document;
  validate image content, dimensions and decoding.

## Verified integration findings (2026-09-15)

The pinned 3.0.1 server passed three-scale WMS fixture checks for uncompressed,
LZW, DEFLATE, PackBits, JPEG-70/80, ZSTD-3/9 and matched COG files. Every decoded
base and overview sample matched for the supported lossless fixtures.
GeoPackage PNG/JPEG renders some views but fails another scale with a raster
origin (`minX or minY not equal to zero`) exception. This is a failed release
configuration probe, not a claim that GeoPackage is generally unsupported.

`python scripts/build_webp.py` (PowerShell launcher action `build-webp`) builds a
separate image using `gs-webp-3.0.1.jar` and `webp-imageio-0.2.2.jar`. Both are
hash-checked against `infra/extensions.lock.json`; no nightly artifact is used.
The WebP profile supports WMS WebP output, while its GWC WMTS endpoint rejects
`image/webp`. PNG/JPEG GWC requests pass. WebP TIFF still reports unsupported
compression tag 50001. WebP GeoPackage reaches an image decoder but fails the
same raster-origin check. Source rankings use vanilla GeoServer; delivery
comparisons use the separately recorded WebP image for all compared encoders.

`infra/requirements.lock.txt` pins every locally installed Python dependency.
NumPy 1.26.4, SciPy 1.15.3, contourpy 1.3.3 and tifffile 2025.5.10 form the
verified GDAL-compatible combination. `pip check` must pass after rebuilding.
Run `python -m tests.integration_host` from this checkout for a short synthetic
real-container resource/resume check; it uses the existing fixture and restarts
only the benchmark GeoServer. It is not a production performance run.

## Continuing while downloads are active

`python -m scripts.continue_study --config configs/full.yaml` waits for the
existing stage lock and any surviving container-side stage. It then replaces
only the idle benchmark worker with the rebuilt image and executes `resume`.
This avoids stopping an in-progress download during a dependency update. A
controller failure leaves source data and completed artifacts in the volumes.
The wrapper does not change Windows power policy or create a scheduled task.

The additional 10-user browsing cells cover the configured user level between
screening and capacity. They use the shortlisted sources and screening resource
profile. Delivery cells include GWC scenarios only after the pinned endpoint
has returned a verified cache-hit image for that output format.

## Portable image retention

Run `python -m scripts.export_images` to save the pinned server, worker and
WebP profile into a Windows `images-<worker-id>.tar` with a checksum manifest.
Restore with `podman --connection podman-machine-default load -i <archive>`.
The controller applies the repository's Python source snapshot to the worker
and `/data/runtime` and records its hashes with every measurement cell. Keep
that exact repository revision together with the image archive and manifests.
Image IDs alone are insufficient if the locally built image is later deleted.
