# Validation of the 2.28.0 payload

The packaged JARs are byte-identical to the version-specific candidate validated
in the Kartoza 2.28 compatibility study. No 2.28.5 library is substituted.

| Check | Result |
|---|---|
| GeoServer / GeoTools / ImageN | 2.28.0 / 34.0 / 0.9.0, Linux amd64 |
| Source data | 14 immutable 4096 × 4096 single-band 8-bit TIFFs, EPSG:3857 |
| Affines | North-up, rotations 15°, 30°, 45°, 75°, −30°, plus shear |
| Overviews | Separate no-overview and nearest-neighbour-overview fixtures |
| Requests | EPSG:3857, zooms 14–24, interiors and boundaries |
| Stock minimal triangle | 29,022 missing pixels in both direct WMS and GWC |
| Patched minimal triangle, gutter 0 | 0 missing pixels in both paths |
| Patched full matrix, gutter 1 | 1,180 successful requests per path, 0 errors |
| Interior tiles in that matrix | 658 per path; 0 missing pixels |
| North-up control | 0 missing pixels |
| Remaining outer-footprint loss | 16 pixels per path, one per affected tile |
| Maximum depth of those losses | Less than 2.056 source pixels from the footprint edge |
| Checkerboard sharpness | No interpolated grey values; exact fraction 0.9999542 |
| Source TIFF checksums | Unchanged, including mounted files |
| GWC verification | All matrix requests MISS; separate MISS → HIT check passed |
| Restart | Same patched hashes and selected gutter-0 requests passed |

Expected coverage and values were calculated independently using the inverse
affine transform. Legitimate empty corners outside the footprint were excluded.
Direct WMS used a 258 × 258 render cropped to the central 256 × 256; GWC used
gutter 1 and 1 × 1 metatiling. Interpolation remained nearest neighbour.

The selected gutter-0 negative-rotation boundary request retained one missing
outer-footprint pixel in each path. The full 2.28.0 matrix was **not** run with
gutter 0; do not generalize its minimal-case success into a universal guarantee.

The Java scanline regression fails on the original ImageN library with
`Expected [0,2) but got RangeInt[0, 3]`. It passes with the patched class, including
forward/reverse traversal and empty intersections. Only the two documented
class entries differ from the original JARs.

The source ZIP includes the curated version, request and matrix evidence under
`evidence/`. Full raw TIFFs and image responses remain in the study checkout.
The repeatable live renderer is
`kartoza-geoserver-affine-fix/compatibility/render_versions.py` in the
`small-gis-demos` repository; it requires the retained fixtures and an isolated
test catalog.

Only a per-user 30 requests/second rate rule was removed from the isolated test
catalog to avoid HTTP 429 errors. Concurrency rules remained. This adjustment is
not included in the installer.

No new 2.28.0 throughput benchmark is claimed here. Validation does not cover
ARM64, other CRS pairs, 16-bit/NoData rasters or arbitrary plugins. Equivalent
Podman startup was tested; the Compose override expresses those settings but
was not itself launched through the Compose CLI.
