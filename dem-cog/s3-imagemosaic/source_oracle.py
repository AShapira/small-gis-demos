"""Read small, native-resolution reference windows from the existing local COGs."""
import base64
import hashlib
import json
from pathlib import Path
from osgeo import gdal

gdal.UseExceptions()
sources = sorted(Path("/input").glob("NASADEM_N30E0[23]0_N39E0[23]9.tif"))
assert len(sources) == 2, sources
mosaic = gdal.BuildVRT("/vsimem/reference.vrt", [str(p) for p in sources])
gt = mosaic.GetGeoTransform()
size = 128
result = {"gdal": gdal.VersionInfo("--version"), "cases": []}
for name, lon, lat in [
    ("west", 23.7, 38.0), ("east", 35.2, 32.8),
    ("seam", 30.0, 30.5), ("ocean", 25.0, 33.0),
]:
    x = round((lon - gt[0]) / gt[1] - 0.5) - size // 2
    y = round((lat - gt[3]) / gt[5] - 0.5) - size // 2
    raw = mosaic.GetRasterBand(1).ReadRaster(x, y, size, size, buf_type=gdal.GDT_Int16)
    xmin, ymax = gt[0] + x * gt[1], gt[3] + y * gt[5]
    result["cases"].append({
        "name": name, "width": size, "height": size,
        "bbox": [xmin, ymax + size * gt[5], xmin + size * gt[1], ymax],
        "nodata": mosaic.GetRasterBand(1).GetNoDataValue(),
        "int16_base64": base64.b64encode(raw).decode(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    })
print(json.dumps(result))
