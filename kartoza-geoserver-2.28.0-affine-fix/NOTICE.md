# Third-party notices and modifications

This is an independently prepared experimental patch, not an official release
or endorsement by Kartoza, GeoServer, GeoTools or Eclipse ImageN.

- `gt-render-34.0.jar` contains GeoTools 34.0, under the GNU Lesser General Public
  License 2.1. See `LICENSES/geotools-LICENSE.md`.
- `affine-0.9.0.jar` contains Eclipse ImageN affine 0.9.0, under Apache License
  2.0. See `LICENSES/imagen-LICENSE.md`.

The patch changes `GridCoverageReaderHelper.class` in the first JAR and
`AffineOpImage.class` in the second. These changes were packaged for GeoServer
2.28.0 on 2026-10-20. They bypass premature cropping of same-CRS nearest-neighbour
affine rasters and correct scanline clipping endpoints, respectively. All other
entries, including existing copyright and attribution metadata, are preserved.

The matching `kartoza-geoserver-2.28.0-affine-fix-sources.zip` contains full
upstream source archives for these two modules, original and modified class
sources, patches and rebuild instructions. Provide it alongside the runtime
ZIP when distributing the patch. Existing source copyright headers remain.
See `source/README.md` in that archive for provenance and the list of changes.
