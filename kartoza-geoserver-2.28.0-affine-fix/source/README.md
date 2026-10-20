# Matching sources and optional rebuild

Extract the runtime and sources ZIPs into the same parent directory. Both use
`kartoza-geoserver-2.28.0-affine-fix/` as their root; the source archive adds files
without replacing runtime files.

From that directory:

```bash
sha256sum -c SOURCE_SHA256SUMS
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 build.py --engine docker
# Alternatively: python3 build.py --engine podman --connection podman-machine-default
sha256sum -c SHA256SUMS
```

Python 3.11+ and a container engine with the pinned stock image are required
only for rebuilding. No compiler or Python is needed to install the runtime ZIP.
The build starts an isolated stock container with networking disabled, compiles
two classes for Java 17, runs the scanline regression against original and
patched libraries, and checks that only the expected class changes in each JAR.
It does not build or commit a container image.

The expected original, source and patched hashes are locked in
`build-lock.json`. The build rejects a different result instead of changing
the delivered payload. `python3 package.py` verifies locked payloads and
regenerates both deterministic ZIPs and their checksums.

## Source provenance

| Archive | Upstream source |
|---|---|
| `gt-render-34.0-sources.jar` | https://repo.osgeo.org/repository/release/org/geotools/gt-render/34.0/gt-render-34.0-sources.jar |
| `affine-0.9.0-sources.jar` | https://repo.osgeo.org/repository/release/org/eclipse/imagen/affine/0.9.0/affine-0.9.0-sources.jar |

The source archives are unmodified and include the other module source files.
The modified classes are provided beside their `.original` versions; the
version-specific unified diffs are `geotools-34.0.patch` and `imagen-0.9.0.patch`.
These modified files implement the experimental backport packaged on 2026-10-20:

- `GridCoverageReaderHelper.java`: retain the 34.0 implementation and skip its
  premature crop only for same-CRS nearest-neighbour rotated/sheared rasters.
- `AffineOpImage.java`: correct forward/reverse half-open scanline clipping
  endpoints and handle empty intersections.

Do not replace the entire GeoTools 34.0 class with the later 34.5 source:
read-padding code differs between these versions.

The runtime archive includes license texts and modification notices. Curated
rendering evidence and the Java regression are included here for review.

Original build/test scripts and documentation use the MIT license reproduced in
`project-MIT.txt`. GeoTools sources and modifications retain LGPL-2.1; ImageN
sources and modifications retain Apache-2.0.
