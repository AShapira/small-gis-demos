# Third-party software

The [MIT License](LICENSE) covers original project work. The components below
retain their own licenses; see [license scope](LICENSING.md).

- OpenLayers 10.11.0: BSD-2-Clause. Its browser build and CSS are vendored in
  `blazor-openlayers/wwwroot/vendor/openlayers/` with the upstream license and
  checksums. The .NET SDK/runtime archive and NuGet packages used by this demo
  retain their own bundled licenses; package versions are pinned in its lock file.
- Copernicus downloader: Alex Shapira, GPL-2.0-or-later. Source is fetched at
  commit `ae22a599ef56d2d17344882c9b77658242f32643`; its license is retained
  in `/opt/copernicus/LICENSE` in the worker. The original repository is not edited.
- GeoServer / GeoWebCache / GeoTools: OSGeo projects. Their official image
  retains bundled software and licenses. See https://geoserver.org/license/.
- GDAL / PROJ and codecs: provided by the pinned OSGeo GDAL image; consult
  the image's package inventory and bundled license notices.
- Playwright 1.58.2: Apache-2.0. The Blazor/OpenLayers browser tests use this
  version; installed packages retain their licenses and notices.
- Python packages are listed in `geotiff-compression/infra/requirements.txt` and the worker's
  installed-package inventory. They include aiohttp, requests, Shapely, tqdm,
  PyYAML, NumPy, Pillow, scikit-image, Matplotlib, psutil and Python-Markdown.
- Sentinel-2 source imagery: Copernicus Sentinel data obtained through CDSE.
  Preserve product identifiers/acquisition dates and identify rendered output
  as containing modified Copernicus Sentinel data for the acquisition year.

The benchmark does not install ECW, MrSID or Kakadu server libraries.
