# License scope

The original code, scripts, tests, configuration and documentation written for
`small-gis-demos` are licensed under the [MIT License](LICENSE), except where a
file or component carries a different license. MIT permits commercial and
noncommercial use, modification and redistribution, subject to retaining its
copyright and permission notice.

**Third-party software and adaptations of it retain their own licenses.**
The root MIT license does not relicense upstream source, patches derived from
that source, compiled JARs, vendored libraries, downloaded datasets, map styles
or fonts.

| Material | Applicable license or notice |
|---|---|
| Original installer, build and test scripts in the two `kartoza-geoserver-*affine-fix` folders | MIT; a copy is included in each installation bundle |
| GeoTools source, corresponding diffs, source archives and patched `gt-render` JARs in those folders | LGPL-2.1, including the modifications; preserve the supplied upstream license and provide matching sources when distributing binaries |
| ImageN affine source, corresponding diffs, source archives and patched `affine` JARs in those folders | Apache-2.0, including the modifications; preserve the supplied license, copyright and modification notices |
| GeoTools and ImageN diffs in `geotiff-compression/patches/affine-geotiff/` | LGPL-2.1 and Apache-2.0 respectively |
| Copernicus downloader fetched by the benchmark | GPL-2.0-or-later; its separate upstream license is retained |
| CesiumJS, Playwright, OpenLayers and other installed or vendored dependencies | Their own package licenses and notices; see [third-party software](THIRD_PARTY_NOTICES.md) |
| Basemap data, styles, fonts and other downloaded assets | Their individual terms and attribution requirements; see [basemap notices](osm-basemap-demo/THIRD_PARTY_NOTICES.md) |
| Sentinel-2 imagery and derived imagery | Applicable Copernicus data terms and attribution requirements; see [benchmark notices](geotiff-compression/THIRD_PARTY_NOTICES.md) |

The GeoTools and ImageN source headers and license texts remain in their
respective patch bundles. For the exact versions and modifications, see the
[2.28.0 notices](kartoza-geoserver-2.28.0-affine-fix/NOTICE.md) and
[2.28.5 source provenance](kartoza-geoserver-affine-fix/source/README.md).
Distribute the matching source archive alongside the runtime archive.

MIT on the original files does not remove obligations that apply when
distributing software combined with GPL/LGPL components. Retain all applicable
licenses and notices in standalone packages; a GitHub repository license label
does not describe every component's license.

Primary references: [MIT](https://opensource.org/license/mit),
[GeoTools licensing](https://docs.geotools.org/latest/userguide/welcome/license.html),
[LGPL-2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html),
[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0).
