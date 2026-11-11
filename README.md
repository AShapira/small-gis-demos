| Demo | Purpose | Documentation |
| --- | --- | --- |
| Blazor + OpenLayers | Blazor WebAssembly map with a GeoServer WMTS basemap, client-side graphics and offline build tooling. | [README](blazor-openlayers/README.md) |
| Offline OSM basemaps | Build country and world basemaps and serve them through GeoServer WMS/WMTS. | [README](osm-basemap-demo/README.md) |
| GeoTIFF merge | Merge aligned rasters with optional pixel-size resampling and pixel/mask verification. | [README](geotiff-merge/README.md) |
| DEM COG Builder | Build COG blocks from NASADEM, SRTM or recursive DTED inputs, normalize DTED latitude-zone grids, and consolidate previous TIFF results; includes portable stage VRTs, exact statistics, resume and exhaustive verification. | [README](dem-cog/README.md) |
| GeoTIFF overview generator | Generate external overviews with bounded parallelism, dry runs and cancellation. | [README](overview-generator/README.md) |
| VRT with JPEG overviews | Combine RGB or grayscale GeoTIFFs into a VRT with JPEG overviews and transparency masks. | [README](vrt-overviews/README.md) |
| GeoServer raster benchmark | Compare raster compression, conversion quality, WMS/WMTS delivery and resource usage. | [README](geotiff-compression/README.md) |

GeoServer affine-raster fixes, the Cesium WMS gutter comparison and Kartoza patch
bundles have moved to [AShapira/geoserver-affine-fix](https://github.com/AShapira/geoserver-affine-fix).
See [relocation details and historical versions](docs/geoserver-affine-fix-relocation.md).
