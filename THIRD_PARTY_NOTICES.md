# Third-party software

- Copernicus downloader: Alex Shapira, GPL-2.0-or-later. Source is fetched at
  commit `ae22a599ef56d2d17344882c9b77658242f32643`; its license is retained
  in `/opt/copernicus/LICENSE` in the worker. The original repository is not edited.
- GeoServer / GeoWebCache / GeoTools: OSGeo projects. Their official image
  retains bundled software and licenses. See https://geoserver.org/license/.
- GDAL / PROJ and codecs: provided by the pinned OSGeo GDAL image; consult
  the image's package inventory and bundled license notices.
- CesiumJS 1.146.0 and Playwright 1.58.2: Apache-2.0. The demo pins them in
  `cesium-wms-gutter-demo/package-lock.json`; their packages retain licenses
  and notices. Cesium attribution remains visible in the map.
- The experimental affine patches target GeoTools 35.1 code licensed under
  LGPL-2.1 and Eclipse ImageN 0.9.2 affine code licensed under Apache-2.0.
  They are source diffs, not bundled binaries; preserve upstream licenses
  when building or distributing them.
- Python packages are listed in `infra/requirements.txt` and the worker's
  installed-package inventory. They include aiohttp, requests, Shapely, tqdm,
  PyYAML, NumPy, Pillow, scikit-image, Matplotlib, psutil and Python-Markdown.
- Sentinel-2 source imagery: Copernicus Sentinel data obtained through CDSE.
  Preserve product identifiers/acquisition dates and identify rendered output
  as containing modified Copernicus Sentinel data for the acquisition year.

The benchmark does not install ECW, MrSID or Kakadu server libraries.
