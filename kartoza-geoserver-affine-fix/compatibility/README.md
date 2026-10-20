# Kartoza 2.28 compatibility checks

These scripts inspect pinned stock images, build isolated candidates from each
version's own libraries, and test the retained immutable rotated-GeoTIFF fixtures.
They do not change the supported version checks in the released 2.28.5 installer.

The compatibility report is [../COMPATIBILITY.md](../COMPATIBILITY.md). Public
summaries and numerical evidence are under `evidence/`; downloaded stock
libraries, candidate JARs, source files, raw responses and private server logs
remain under `../.artifacts/compatibility/`.

## Repeating the lab checks

Run from the repository root. Requirements are Python 3.11+, Git, a Docker or
Podman engine and the Python packages in `../tests/requirements.txt`. The default
engine is Podman; pass `--engine docker` or an executable path as needed.

1. Save the Docker Hub response from
   `https://hub.docker.com/v2/repositories/kartoza/geoserver/tags?page_size=100&name=2.28`
   as a JSON array of its `results` under
   `kartoza-geoserver-affine-fix/.artifacts/compatibility/dockerhub-tags.json`,
   following pagination if necessary. The checked inventory and digests are in
   [evidence/published-tags.json](evidence/published-tags.json).
2. Pull the specific image digest from that inventory. The scripts use
   `--pull=never`, so they cannot silently select a different image during testing.
3. Inspect and compile against the selected release:

   ```bash
   python kartoza-geoserver-affine-fix/compatibility/inspect_versions.py 2.28.0 \
     --engine podman --connection podman-machine-default
   ```

   The original source is downloaded from OSGeo Maven. The original two patch
   files are applied with exact context using `git apply`; older sources retain
   their existing padding implementation. The image's own Java 17 compiler
   compiles both replacements. Only one class entry changes in each JAR. The
   original Java regression must fail and the corrected version must pass.
   The existing 2.28.5 installer is also tested against the original libraries;
   rejection on older patch releases must leave both originals unchanged.
4. Run the stock reproduction and patched matrix:

   ```bash
   python kartoza-geoserver-affine-fix/compatibility/render_versions.py 2.28.0 \
     --phase stock --engine podman --connection podman-machine-default
   python kartoza-geoserver-affine-fix/compatibility/render_versions.py 2.28.0 \
     --phase patched --full --engine podman --connection podman-machine-default
   ```

   These commands require the original study's local TIFFs and the existing
   `rasterbench-israel-rgb-5gb-data` volume. That data volume is mounted read-only.
   Each treatment uses a new container, new catalog and the original stock image.
   The candidate bundle is copied into the new container before normal startup.
   No custom image is built. Port 18094 is bound to loopback; `--port` changes it.
   The scripts stop their new containers afterward, retaining evidence and catalog
   state. They never restart existing benchmark containers.

   Credentials are random lab credentials. The test runner uses the password
   environment variable because the oldest image mishandles its `_FILE` variant
   when no direct password is provided. The test runner removes only the isolated
   catalog's per-user WMS rate rule; it keeps concurrency rules. Neither choice is
   a change to the production patch or its installer.

   Stock runs reproduce the minimal failing request through both routes. Patched
   runs use a one-pixel gutter, the five-case rendering regression and, with
   `--full`, 1,180 requests per serving path. GWC caches are cleared before the
   matrix. Direct WMS expands/crops the request; GWC uses its layer gutter.
5. After completing all six versions in the report, run:

   ```bash
   python kartoza-geoserver-affine-fix/compatibility/analyze_versions.py
   ```

   Analysis asserts request counts, zero interior/north-up gaps, successful cache
   misses/hits and absence of intermediate checkerboard gray values. It saves
   compressed numerical evidence and a visual comparison without private logs.

Candidate builds are test outputs, not interchangeable production bundles. Each
older GeoServer release requires its matching GeoTools JAR and strict installer
manifest; 2.28.0 also requires ImageN 0.9.0. Do not bypass the released installer's
version checks or rename its GeoTools 34.5 JAR.
