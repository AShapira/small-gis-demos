# Source provenance

Modifications prepared on 2026-10-20 for the exact image in `../build-lock.json`.
Original copyrights and license headers are retained in the Java sources.

| Component | Exact upstream source |
|---|---|
| GeoTools | [34.5 reader helper](https://github.com/geotools/geotools/blob/34.5/modules/library/render/src/main/java/org/geotools/renderer/lite/gridcoverage2d/GridCoverageReaderHelper.java) |
| ImageN | [0.9.1 affine operation](https://github.com/eclipse-imagen/imagen/blob/0.9.1/modules/affine/src/main/java/org/eclipse/imagen/media/affine/AffineOpImage.java) |

The `.original` files are unmodified upstream source. The adjacent `.java` files
contain the changes; `.patch` files show the differences. GeoTools adds a guard
that preserves source pixels for same-CRS rotated/sheared grids when nearest
interpolation is selected. ImageN corrects half-open source bounds in forward
and reverse scanline traversal and handles empty intersections.

Complete upstream source archives:

- [gt-render-34.5-sources.jar](gt-render-34.5-sources.jar), downloaded from
  [OSGeo Maven](https://repo.osgeo.org/repository/release/org/geotools/gt-render/34.5/gt-render-34.5-sources.jar).
- [affine-0.9.1-sources.jar](affine-0.9.1-sources.jar), downloaded from
  [OSGeo Maven](https://repo.osgeo.org/repository/release/org/eclipse/imagen/affine/0.9.1/affine-0.9.1-sources.jar).

The patched files were checked against these archives. The remaining library
source is unchanged. `../build.py` compiles the supplied modified classes against
the exact stock libraries and replaces only their matching class entries.

Licenses: [GeoTools LGPL-2.1](geotools-LICENSE.md),
[ImageN license notices](imagen-LICENSE.md). The ImageN `AffineOpImage.java`
header specifically declares Apache-2.0.
