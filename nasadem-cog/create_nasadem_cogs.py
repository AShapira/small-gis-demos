#!/usr/bin/env python3
"""Merge a local NASADEM VRT into degree-sized COGs in the QGIS/OSGeo4W shell."""
import argparse
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET

try:
    import numpy as np
    from osgeo import gdal, osr
    from osgeo_utils.samples.validate_cloud_optimized_geotiff import validate as validate_cog
except ImportError as exc:
    raise SystemExit("Use the QGIS/OSGeo4W shell's Python, with GDAL, NumPy and "
                     "osgeo_utils installed. Do not pip-install GDAL into QGIS.") from exc

SCHEMA = 1
EDGE = 512
HEADROOM = 1024 ** 3
TOLERANCE = 1e-5  # Pixels, including longitudes far from the VRT origin.


class InputError(Exception):
    pass


class RunError(Exception):
    pass


class Cancelled(Exception):
    pass


def check_cancel(event):
    if event.is_set():
        raise Cancelled("Cancelled")


def integer(value, description):
    if not math.isfinite(value) or abs(value - round(value)) > TOLERANCE:
        raise InputError(f"{description}: expected an aligned integer, got {value}")
    return round(value)


def close(ds):
    if ds is not None and hasattr(ds, "Close"):
        ds.Close()


@contextmanager
def opened(path):
    ds = gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_READONLY,
                     allowed_drivers=["GTiff"], open_options=["NUM_THREADS=1"])
    if ds is None:
        raise RunError(f"Cannot open TIFF: {path}")
    try:
        yield ds
    finally:
        close(ds)


def stamp(path):
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def unchanged(records):
    for record in records:
        try:
            same = stamp(Path(record["path"])) == record
        except OSError:
            same = False
        if not same:
            raise RunError(f"Input changed during processing: {record['path']}")


def wgs84(wkt):
    actual, expected = osr.SpatialReference(), osr.SpatialReference()
    actual.SetFromUserInput(wkt)
    expected.ImportFromEPSG(4326)
    for crs in (actual, expected):
        crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return bool(actual.IsSame(expected))


def rect(node, name):
    item = node.find(name)
    if item is None:
        raise InputError(f"VRT source is missing {name}")
    result = tuple(integer(float(item.attrib[key]), name) for key in
                   ("xOff", "yOff", "xSize", "ySize"))
    if min(result[:2]) < 0 or min(result[2:]) < 1:
        raise InputError(f"Invalid {name}: {result}")
    return result


def intersection(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right = min(a[0] + a[2], b[0] + b[2])
    bottom = min(a[1] + a[3], b[1] + b[3])
    return None if right <= left or bottom <= top else (left, top, right - left, bottom - top)


@dataclass
class Source:
    path: Path
    src: tuple
    dst: tuple
    gx: int
    gy: int
    west: int
    south: int
    size: int
    kind: str
    skip_nodata: bool
    use_mask: bool
    files: list

    @property
    def footprint(self):
        # Independent of the VRT DstRect, used by the reference verifier.
        return self.gx + self.src[0], self.gy + self.src[1], self.src[2], self.src[3]


@dataclass
class Block:
    name: str
    bounds: tuple
    window: tuple
    sources: list
    tile_count: int
    eligible: bool


@dataclass
class Inventory:
    vrt: Path
    transform: tuple
    projection: str
    nodata: int
    intervals: int
    metadata: dict
    band_metadata: dict
    band_properties: dict
    sources: list
    missing: list
    identity: dict


def configure():
    gdal.UseExceptions()
    osr.UseExceptions()
    if gdal.GetDriverByName("COG") is None:
        raise InputError("This GDAL installation has no COG driver")
    if int(gdal.VersionInfo("VERSION_NUM")) < 3060000:
        raise InputError("GDAL 3.6 or newer is required for explicit COG overview counts")
    gdal.SetCacheMax(512 * 1024 ** 2)
    gdal.SetConfigOption("GDAL_NUM_THREADS", "1")
    gdal.SetConfigOption("GDAL_MAX_DATASET_POOL_SIZE", "64")


def read_inventory(vrt_file, filename_filter=None, cancel=None, progress=None):
    cancel = cancel or threading.Event()
    path = Path(vrt_file).resolve(strict=True)
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise InputError("VRT DTDs and entity declarations are not supported")
    root = ET.fromstring(raw)
    if root.tag != "VRTDataset" or root.get("subClass"):
        raise InputError("Expected an ordinary, unwarped VRTDataset")
    if any(c.tag not in {"SRS", "GeoTransform", "Metadata", "VRTRasterBand"} for c in root):
        raise InputError("Unsupported VRT dataset element (for example a dataset mask or GCPs)")
    bands = root.findall("VRTRasterBand")
    if len(bands) != 1 or bands[0].get("dataType") != "Int16" or bands[0].get("subClass"):
        raise InputError("Expected one ordinary Int16 NASADEM height band")
    band = bands[0]
    allowed = {"NoDataValue", "ColorInterp", "Metadata", "Description", "UnitType",
               "Scale", "Offset", "SimpleSource", "ComplexSource"}
    if any(child.tag not in allowed for child in band):
        raise InputError("Unsupported VRT band transformation or mask")
    projection = root.findtext("SRS", "")
    if not wgs84(projection):
        raise InputError("Expected WGS 84 / EPSG:4326 horizontal coordinates")
    gt = tuple(float(x) for x in root.findtext("GeoTransform", "").split(","))
    if len(gt) != 6 or not all(math.isfinite(x) for x in gt):
        raise InputError("Invalid VRT geotransform")
    if gt[2] != 0 or gt[4] != 0 or gt[1] <= 0 or gt[5] >= 0:
        raise InputError("Expected a north-up, unrotated VRT")
    intervals = integer(1 / gt[1], "Samples per degree")
    if intervals < 1 or integer(-1 / gt[5], "Latitude samples per degree") != intervals:
        raise InputError("Expected square pixels with an integer number of intervals per degree")
    integer((gt[0] + gt[1] / 2) * intervals, "VRT longitude sample origin")
    integer((gt[3] + gt[5] / 2) * intervals, "VRT latitude sample origin")
    nd = float(band.findtext("NoDataValue", "nan"))
    if not math.isfinite(nd) or nd != int(nd) or not -32768 <= nd <= 32767:
        raise InputError("An explicit Int16 NoData value is required")
    nodata = int(nd)
    vrt_width, vrt_height = int(root.get("rasterXSize")), int(root.get("rasterYSize"))
    sources, missing, records = [], [], []
    selected = 0
    template = None
    next_notice = time.monotonic() + 5
    for node in band:
        if node.tag not in ("SimpleSource", "ComplexSource"):
            continue
        check_cancel(cancel)
        if progress and time.monotonic() >= next_notice:
            progress(f"Inspecting VRT: {selected} selected references, "
                     f"{len(sources)} usable, {len(missing)} missing")
            next_notice = time.monotonic() + 5
        filename = node.find("SourceFilename")
        if filename is None or not filename.text:
            raise InputError("A VRT source has no filename")
        text = filename.text
        if filename_filter and filename_filter.casefold() not in text.replace("\\", "/").split("/")[-1].casefold():
            continue
        selected += 1
        if text.startswith("/vsi") or "://" in text:
            raise InputError("Sources must be local TIFF paths, not remote/VSI references")
        source_path = Path(text)
        if filename.get("relativeToVRT", "0") == "1":
            source_path = path.parent / source_path
        elif not source_path.is_absolute():
            raise InputError(f"Nonabsolute source without relativeToVRT=1: {text}")
        source_path = source_path.resolve()
        allowed_source = {"SourceFilename", "SourceBand", "SourceProperties", "SrcRect",
                          "DstRect", "NODATA", "UseMaskBand"}
        if any(c.tag not in allowed_source for c in node) or node.get("resampling"):
            raise InputError(f"Unsupported source transformation: {text}")
        if node.findtext("SourceBand") != "1":
            raise InputError(f"Expected SourceBand 1: {text}")
        src, dst = rect(node, "SrcRect"), rect(node, "DstRect")
        if src[2:] != dst[2:]:
            raise InputError(f"Resampled VRT sources are not supported: {text}")
        if dst[0] + dst[2] > vrt_width or dst[1] + dst[3] > vrt_height:
            raise InputError(f"Source destination lies outside the VRT: {text}")
        skip_nodata = node.find("NODATA") is not None
        if skip_nodata and (node.tag != "ComplexSource" or float(node.findtext("NODATA")) != nodata):
            raise InputError(f"Unsupported source NoData mapping: {text}")
        mask_text = node.findtext("UseMaskBand", "false").lower()
        if mask_text not in ("true", "false", "1", "0"):
            raise InputError(f"Invalid UseMaskBand: {text}")
        use_mask = mask_text in ("true", "1")
        if use_mask and node.tag != "ComplexSource":
            raise InputError(f"UseMaskBand requires ComplexSource: {text}")
        if not source_path.exists():
            missing.append(str(source_path))
            records.append({"path": str(source_path), "missing": True})
            continue
        try:
            with opened(source_path) as ds:
                size = intervals + 1
                b = ds.GetRasterBand(1)
                if (ds.RasterCount != 1 or b.DataType != gdal.GDT_Int16
                        or ds.RasterXSize != size or ds.RasterYSize != size
                        or b.GetNoDataValue() != nodata or not wgs84(ds.GetProjection())):
                    raise InputError(f"Expected a compatible 1° node-grid Int16 tile: {source_path}")
                sg = ds.GetGeoTransform()
                if (sg[2] != 0 or sg[4] != 0
                        or abs(sg[1] / gt[1] - 1) * intervals > TOLERANCE
                        or abs(sg[5] / gt[5] - 1) * intervals > TOLERANCE):
                    raise InputError(f"Incompatible source pixel grid: {source_path}")
                gx = integer((sg[0] - gt[0]) / gt[1], "Source column")
                gy = integer((sg[3] - gt[3]) / gt[5], "Source row")
                if (gx + src[0], gy + src[1]) != dst[:2] or max(src[0] + src[2], src[1] + src[3]) > size:
                    raise InputError(f"VRT mapping differs from TIFF georeferencing: {source_path}")
                west = integer(sg[0] + sg[1] / 2, "Tile west sample")
                north = integer(sg[3] + sg[5] / 2, "Tile north sample")
                south = north - 1
                if not (-180 <= west < 180 and -90 <= south < 90):
                    raise InputError(f"Tile outside geographic coordinate bounds: {source_path}")
                properties = {"scale": b.GetScale(), "offset": b.GetOffset(),
                              "unit": b.GetUnitType(), "description": b.GetDescription(),
                              "area_or_point": ds.GetMetadataItem("AREA_OR_POINT") or "Area"}
                if template is not None and properties != template:
                    raise InputError(f"Inconsistent band metadata: {source_path}")
                template = properties
                files = [stamp(Path(p).resolve()) for p in ds.GetFileList()]
                records.extend(files)
                sources.append(Source(source_path, src, dst, gx, gy, west, south, size,
                                      node.tag, skip_nodata, use_mask, files))
        except (RuntimeError, OSError, RunError) as exc:
            raise InputError(f"Unreadable source {source_path}: {exc}") from exc
    if not sources:
        raise InputError(f"No usable selected sources ({selected} references, {len(missing)} missing)")
    metadata = {item.get("key"): item.text or "" for parent in root.findall("Metadata")
                if not parent.get("domain") for item in parent.findall("MDI")}
    metadata.setdefault("AREA_OR_POINT", template["area_or_point"])
    band_metadata = {item.get("key"): item.text or "" for parent in band.findall("Metadata")
                     if not parent.get("domain") for item in parent.findall("MDI")
                     if not (item.get("key") or "").startswith("STATISTICS_")}
    props = {"Scale": template["scale"], "Offset": template["offset"],
             "UnitType": template["unit"], "Description": template["description"],
             "ColorInterp": band.findtext("ColorInterp", "Gray")}
    if props["ColorInterp"] not in ("Gray", "Undefined"):
        raise InputError("Only a grayscale/undefined height band is supported")
    for key in ("Scale", "Offset", "UnitType", "Description"):
        if band.find(key) is not None:
            props[key] = band.findtext(key)
    identity = {"vrt": str(path), "vrt_sha256": hashlib.sha256(raw).hexdigest(),
                "vrt_stat": stamp(path), "filter": filename_filter,
                "sources": records}
    return Inventory(path, gt, projection, nodata, intervals, metadata, band_metadata,
                     props, sources, list(dict.fromkeys(missing)), identity)


def coordinate(lat, lon):
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def validate_block_size(value):
    if type(value) is not int or value <= 0 or 360 % value:
        raise InputError("Block size must be a positive integer divisor of 360 degrees")
    return value


def block_size_argument(value):
    try:
        size = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Block size must be a positive integer divisor of 360 degrees") from exc
    try:
        return validate_block_size(size)
    except InputError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def plan_blocks(inv, block_size, minimum):
    validate_block_size(block_size)
    groups = {}
    for source in inv.sources:
        # A symmetric 360-degree grid on both axes; latitude is clipped below.
        west = -180 + ((source.west + 180) // block_size) * block_size
        south = -180 + ((source.south + 180) // block_size) * block_size
        groups.setdefault((west, south), set()).add(str(source.path))
    result = []
    gt = inv.transform
    for (west, south), paths in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0])):
        east = west + block_size
        north = min(90, south + block_size)
        south = max(-90, south)
        col = integer((west - (gt[0] + gt[1] / 2)) / gt[1], "Block column")
        row = integer((north - (gt[3] + gt[5] / 2)) / gt[5], "Block row")
        width = (east - west) * inv.intervals + 1
        height = (north - south) * inv.intervals + 1
        window = col, row, width, height
        contributors = [source for source in inv.sources if intersection(window, source.dst)]
        name = f"NASADEM_{coordinate(south, west)}_{coordinate(north - 1, east - 1)}.tif"
        result.append(Block(name, (west, south, east, north), window, contributors,
                            len(paths), len(paths) >= minimum))
    return result


def block_transform(inv, block):
    gt = inv.transform
    return (gt[0] + block.window[0] * gt[1], gt[1], 0,
            gt[3] + block.window[1] * gt[5], 0, gt[5])


def add_metadata(parent, values):
    if values:
        node = ET.SubElement(parent, "Metadata")
        for key, value in values.items():
            ET.SubElement(node, "MDI", key=key).text = str(value)


def write_block_vrt(path, inv, block):
    root = ET.Element("VRTDataset", rasterXSize=str(block.window[2]),
                      rasterYSize=str(block.window[3]))
    ET.SubElement(root, "SRS").text = inv.projection
    ET.SubElement(root, "GeoTransform").text = ",".join(map(repr, block_transform(inv, block)))
    add_metadata(root, inv.metadata)
    band = ET.SubElement(root, "VRTRasterBand", dataType="Int16", band="1")
    ET.SubElement(band, "NoDataValue").text = str(inv.nodata)
    add_metadata(band, inv.band_metadata)
    for key, value in inv.band_properties.items():
        if value is not None and value != "":
            ET.SubElement(band, key).text = str(value)
    for source in block.sources:
        common = intersection(block.window, source.dst)
        if common is None:
            continue
        node = ET.SubElement(band, source.kind)
        ET.SubElement(node, "SourceFilename", relativeToVRT="0").text = str(source.path)
        ET.SubElement(node, "SourceBand").text = "1"
        sx = source.src[0] + common[0] - source.dst[0]
        sy = source.src[1] + common[1] - source.dst[1]
        for tag, rect_value in (("SrcRect", (sx, sy, *common[2:])),
                                ("DstRect", (common[0] - block.window[0],
                                             common[1] - block.window[1], *common[2:]))):
            ET.SubElement(node, tag, **dict(zip(("xOff", "yOff", "xSize", "ySize"),
                                               map(str, rect_value))))
        if source.skip_nodata:
            ET.SubElement(node, "NODATA").text = str(inv.nodata)
        if source.use_mask:
            ET.SubElement(node, "UseMaskBand").text = "true"
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


class Readers:
    """Each verification worker owns at most 16 open source datasets."""
    def __init__(self):
        self.datasets = OrderedDict()

    def get(self, path):
        key = str(path)
        if key not in self.datasets:
            if len(self.datasets) >= 16:
                _, old = self.datasets.popitem(last=False)
                close(old)
            ds = gdal.OpenEx(key, gdal.OF_RASTER | gdal.OF_READONLY,
                             allowed_drivers=["GTiff"], open_options=["NUM_THREADS=1"])
            if ds is None:
                raise RunError(f"Cannot read source {path}")
            self.datasets[key] = ds
        self.datasets.move_to_end(key)
        return self.datasets[key]

    def close(self):
        for ds in self.datasets.values():
            close(ds)
        self.datasets.clear()


def verify_pixels(ds, inv, block, cancel, progress):
    readers = Readers()
    checked = 0
    width, height = block.window[2:]
    try:
        for y in range(0, height, EDGE):
            for x in range(0, width, EDGE):
                check_cancel(cancel)
                w, h = min(EDGE, width - x), min(EDGE, height - y)
                region = block.window[0] + x, block.window[1] + y, w, h
                expected = np.full((h, w), inv.nodata, dtype=np.int16)
                for source in block.sources:
                    common = intersection(region, source.footprint)
                    if common is None:
                        continue
                    src_ds = readers.get(source.path)
                    sx, sy = common[0] - source.gx, common[1] - source.gy
                    values = src_ds.GetRasterBand(1).ReadAsArray(sx, sy, *common[2:])
                    if values is None:
                        raise RunError(f"Failed to read source values: {source.path}")
                    valid = np.ones(values.shape, dtype=bool)
                    if source.skip_nodata:
                        valid &= values != inv.nodata
                    # GDAL ComplexSource gives an explicit NODATA priority
                    # over UseMaskBand when both are present.
                    elif source.use_mask:
                        mask = src_ds.GetRasterBand(1).GetMaskBand().ReadAsArray(sx, sy, *common[2:])
                        if mask is None:
                            raise RunError(f"Failed to read source mask: {source.path}")
                        valid &= mask != 0
                    ox, oy = common[0] - region[0], common[1] - region[1]
                    target = expected[oy:oy + common[3], ox:ox + common[2]]
                    target[valid] = values[valid]
                actual = ds.GetRasterBand(1).ReadAsArray(x, y, w, h)
                mask = ds.GetRasterBand(1).GetMaskBand().ReadAsArray(x, y, w, h)
                if (actual is None or mask is None or not np.array_equal(actual, expected)
                        or not np.array_equal(mask != 0, expected != inv.nodata)):
                    raise RunError(f"Pixel/NoData verification failed in {block.name} at window {x},{y},{w},{h}")
                checked += w * h
                progress(checked / (width * height))
    finally:
        readers.close()
    return checked


def overview_dimensions(width, height):
    result = []
    while max(width, height) > 512:
        width, height = math.ceil(width / 2), math.ceil(height / 2)
        result.append((width, height))
    return result


def verify_output(path, inv, block, overviews, exhaustive, cancel, progress=lambda value: None):
    check_cancel(cancel)
    with opened(path) as ds:
        b = ds.GetRasterBand(1)
        width, height = block.window[2:]
        expected_gt = block_transform(inv, block)
        if (ds.RasterCount != 1 or ds.RasterXSize != width or ds.RasterYSize != height
                or b.DataType != gdal.GDT_Int16 or b.GetNoDataValue() != inv.nodata
                or not wgs84(ds.GetProjection())
                or any(abs(a - c) / inv.transform[1] > TOLERANCE
                       for a, c in zip(ds.GetGeoTransform(), expected_gt))):
            raise RunError(f"Output grid/type/NoData mismatch: {path}")
        if b.GetBlockSize() != [512, 512] or ds.GetMetadataItem("LAYOUT", "IMAGE_STRUCTURE") != "COG":
            raise RunError(f"Output is not a 512-pixel tiled COG: {path}")
        if ds.GetMetadataItem("COMPRESSION", "IMAGE_STRUCTURE") != "DEFLATE":
            raise RunError(f"Output compression mismatch: {path}")
        if ds.GetMetadataItem("PREDICTOR", "IMAGE_STRUCTURE") != "2":
            raise RunError(f"Output predictor mismatch: {path}")
        for key, value in inv.metadata.items():
            if ds.GetMetadataItem(key) != value:
                raise RunError(f"Output metadata mismatch ({key}): {path}")
        for key, getter in (("Scale", b.GetScale), ("Offset", b.GetOffset),
                            ("UnitType", b.GetUnitType), ("Description", b.GetDescription)):
            value = inv.band_properties[key]
            actual = getter()
            if key in ("Scale", "Offset"):
                default = 1 if key == "Scale" else 0
                if float(actual if actual is not None else default) != float(value if value is not None else default):
                    raise RunError(f"Output band {key} mismatch: {path}")
            elif (value or "") != actual:
                raise RunError(f"Output band {key} mismatch: {path}")
        dims = overview_dimensions(width, height) if overviews else []
        actual_dims = [(b.GetOverview(i).XSize, b.GetOverview(i).YSize)
                       for i in range(b.GetOverviewCount())]
        # The COG generator rounds odd dimensions down in some GDAL versions;
        # ordinary GTiff BuildOverviews rounds up. Both retain the full extent.
        valid_dims = len(actual_dims) == len(dims) and all(
            all(actual in {max(1, base // (2 ** (i + 1))),
                           max(1, math.ceil(base / (2 ** (i + 1))))}
                for actual, base in zip(size, (width, height)))
            for i, size in enumerate(actual_dims))
        if not valid_dims:
            raise RunError(f"Output overview pyramid mismatch: expected {dims}, got {actual_dims}: {path}")
        warnings, errors, _ = validate_cog(ds, full_check=exhaustive)
        if errors:
            raise RunError("COG structure validation failed: " + "; ".join(errors))
        check_cancel(cancel)
        count = verify_pixels(ds, inv, block, cancel, progress) if exhaustive else 0
        return {"mode": "every_pixel" if exhaustive else "structure", "pixels_checked": count,
                "full_cog_check": exhaustive, "overview_count": len(dims),
                "overview_dimensions": actual_dims, "warnings": warnings}


def reservation(block, overviews):
    width, height = block.window[2:]
    pixels = width * height
    if overviews:
        pixels += sum(w * h for w, h in overview_dimensions(width, height))
    # Conservative allowance for final output, GDAL intermediates and codec overhead.
    return 3 * pixels * 2


@contextmanager
def directory_lock(directory):
    path = directory / ".nasadem.lock"
    if path.is_symlink():
        raise InputError("Output lock must not be a symlink")
    if os.name == "nt":
        # Exclusive file sharing works for local and UNC files, including WSL
        # shares that do not implement msvcrt byte-range locking.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error in (32, 33):
                raise InputError("Another merger is using this output directory")
            raise InputError(f"Cannot lock output directory: {ctypes.WinError(error)}")
        try:
            yield
        finally:
            kernel.CloseHandle(handle)
        return
    with path.open("a+b") as stream:
        import fcntl
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InputError("Another merger is using this output directory") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def save_state(directory, state):
    atomic_json(directory / "state.json", state)
    path = directory / "summary.csv"
    temporary = path.with_suffix(".csv.tmp")
    fields = ["name", "bounds", "tile_count", "contributing_sources", "status",
              "elapsed_seconds", "bytes", "pixels_checked", "error"]
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for name, result in state["blocks"].items():
            row = {key: result.get(key, "") for key in fields}
            row.update(name=name, pixels_checked=result.get("verification", {}).get("pixels_checked", 0),
                       contributing_sources=len(result["sources"]))
            writer.writerow(row)
    os.replace(temporary, path)


class Progress:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}

    def set(self, name, value):
        with self.lock:
            self.values[name] = max(self.values.get(name, 0), value)

    def snapshot(self):
        with self.lock:
            return dict(self.values)


def process_block(inv, block, output, overviews, exhaustive, cancel, progress, adopted=False):
    start = time.monotonic()
    inputs = [record for source in block.sources for record in source.files]
    unchanged(inputs)
    unchanged([inv.identity["vrt_stat"]])
    final = output / block.name
    if final.exists():
        if final.is_symlink():
            raise RunError(f"Output must not be a symlink: {final}")
        verification = verify_output(final, inv, block, overviews, exhaustive or adopted,
                                     cancel, lambda p: progress.set(block.name, p))
        unchanged(inputs)
        unchanged([inv.identity["vrt_stat"]])
        return {"status": "complete", "action": "adopted" if adopted else "skipped",
                "verification": verification, "bytes": final.stat().st_size,
                "elapsed_seconds": time.monotonic() - start}
    staging = output / ".incomplete" / (block.name + "." + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    vrt = staging / "block.vrt"
    candidate = staging / "candidate.tif"
    write_block_vrt(vrt, inv, block)
    options = ["COMPRESS=DEFLATE", "LEVEL=6", "PREDICTOR=STANDARD", "BLOCKSIZE=512",
               "BIGTIFF=IF_SAFER", "NUM_THREADS=1", "STATISTICS=NO",
               "OVERVIEWS=IGNORE_EXISTING" if overviews else "OVERVIEWS=NONE"]
    if overviews:
        options.append("OVERVIEW_RESAMPLING=AVERAGE")
        options.append(f"OVERVIEW_COUNT={len(overview_dimensions(*block.window[2:]))}")

    def callback(fraction, message, data):
        progress.set(block.name, fraction * (0.5 if exhaustive else 0.9))
        return 0 if cancel.is_set() else 1

    ds = None
    try:
        check_cancel(cancel)
        ds = gdal.Translate(str(candidate), str(vrt), format="COG", creationOptions=options,
                            callback=callback)
        if ds is None:
            raise RunError(f"COG generation failed for {block.name}")
        close(ds)
        ds = None
        check_cancel(cancel)
        verification = verify_output(candidate, inv, block, overviews, exhaustive, cancel,
                                     lambda p: progress.set(block.name, 0.5 + p / 2))
        unchanged(inputs)
        unchanged([inv.identity["vrt_stat"]])
        check_cancel(cancel)
        if final.exists():
            raise RunError(f"Output appeared during generation; refusing to overwrite: {final}")
        os.rename(candidate, final)
        shutil.rmtree(staging)
        return {"status": "complete", "action": "created", "verification": verification,
                "bytes": final.stat().st_size, "elapsed_seconds": time.monotonic() - start}
    except Exception:
        check_cancel(cancel)
        raise
    finally:
        close(ds)


def prepare_state(output, inv, blocks, args):
    config = {"block_size": args.block_size, "min_tiles_per_block": args.min_tiles_per_block,
              "build_overviews": args.build_overviews,
              "creation": "DEFLATE-6-STANDARD-512-IF_SAFER-v1"}
    identity = {"input": inv.identity, "config": config}
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    state_path = output / "state.json"
    for name in ("state.json", "state.json.tmp", "summary.csv", "summary.csv.tmp",
                 "run.log", "errors.log", ".incomplete"):
        if (output / name).is_symlink():
            raise InputError(f"Managed output path must not be a symlink: {name}")
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("schema") != SCHEMA or state.get("signature") != signature:
            raise InputError("Existing state belongs to different inputs/options. Use a new output directory.")
        expected = {block.name for block in blocks}
        if set(state.get("blocks", {})) != expected:
            raise InputError("Existing state has an incompatible block inventory")
    else:
        other = [p.name for p in output.iterdir() if p.name != ".nasadem.lock"]
        if other:
            raise InputError("An output directory without state.json must be empty")
        state = {"schema": SCHEMA, "signature": signature, "identity": identity,
                 "missing_sources": inv.missing, "blocks": {}}
        for block in blocks:
            state["blocks"][block.name] = {
                "bounds": block.bounds, "tile_count": block.tile_count,
                "sources": [str(s.path) for s in block.sources],
                "status": "pending" if block.eligible else "below_minimum"}
    known = set(state["blocks"])
    unexpected = [p.name for p in output.iterdir() if p.suffix.lower() in (".tif", ".tiff")
                  and p.name not in known]
    if unexpected:
        raise InputError(f"Unrelated TIFFs in output directory: {unexpected}")
    state.update(status="running", workers=args.workers, verify=args.verify,
                 started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 runtime={"python": sys.version.split()[0], "gdal": gdal.VersionInfo("RELEASE_NAME")})
    return state


@contextmanager
def worker_pool(count, cancel):
    pool = ThreadPoolExecutor(max_workers=count)
    try:
        yield pool
    except BaseException:
        # Request cancellation before waiting for active GDAL workers.
        cancel.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def execute(inv, blocks, output, args, cancel):
    state = prepare_state(output, inv, blocks, args)
    progress = Progress()
    with (output / "run.log").open("a", encoding="utf-8") as log_file, (
            output / "errors.log").open("a", encoding="utf-8") as error_file:
        def log(message, error=False):
            line = time.strftime("%Y-%m-%d %H:%M:%S ") + message
            print(line, flush=True)
            log_file.write(line + "\n")
            log_file.flush()
            if error:
                error_file.write(line + "\n")
                error_file.flush()
        log(f"Selected {len(inv.sources)} source references; {len(inv.missing)} missing; "
            f"{sum(b.eligible for b in blocks)} eligible blocks; workers <= {args.workers}")
        if inv.missing:
            log("Missing files are recorded in state.json; uncovered pixels will be NoData")
        save_state(output, state)
        pending = [b for b in blocks if b.eligible]
        active = {}
        failures = False
        completed = 0
        started = last_progress = time.monotonic()
        try:
            with worker_pool(args.workers, cancel) as pool:
                while pending or active:
                    if cancel.is_set():
                        pending.clear()
                    while pending and len(active) < args.workers:
                        block = pending[0]
                        needed = 0 if (output / block.name).exists() else reservation(block, args.build_overviews)
                        available = shutil.disk_usage(output).free
                        reserved = sum(item[1] for item in active.values())
                        if needed and available < reserved + needed + HEADROOM:
                            if active:
                                break
                            raise RunError(f"Insufficient disk space for {block.name}: "
                                           f"need {needed + HEADROOM:,} bytes, free {available:,}")
                        pending.pop(0)
                        previous = state["blocks"][block.name]
                        adopted = previous["status"] != "complete"
                        previous.update(status="running")
                        previous.pop("error", None)
                        save_state(output, state)
                        log(f"Starting {block.name} ({block.tile_count} nominal tiles)")
                        future = pool.submit(process_block, inv, block, output, args.build_overviews,
                                             args.verify, cancel, progress, adopted)
                        active[future] = (block, needed)
                    if active:
                        done, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                        for future in done:
                            block, _ = active.pop(future)
                            try:
                                result = future.result()
                                state["blocks"][block.name].update(result)
                                completed += 1
                                progress.set(block.name, 1)
                                log(f"{result['action']}: {block.name}; "
                                    f"{result['verification']['pixels_checked']:,} pixels checked")
                            except Cancelled:
                                state["blocks"][block.name].update(status="cancelled")
                                log(f"Cancelled: {block.name}")
                            except Exception as exc:
                                failures = True
                                state["blocks"][block.name].update(status="failed", error=str(exc))
                                log(f"Failed {block.name}: {exc}", error=True)
                            save_state(output, state)
                    now = time.monotonic()
                    if now - last_progress >= 5:
                        values = progress.snapshot()
                        total = sum(b.window[2] * b.window[3] for b in blocks if b.eligible)
                        done_work = sum(b.window[2] * b.window[3] * values.get(b.name, 0)
                                        for b in blocks if b.eligible)
                        fraction = done_work / total if total else 1
                        eta = (now - started) * (1 - fraction) / fraction if fraction > 0 else None
                        log(f"{completed} complete; {len(active)} active; {len(pending)} pending; "
                            f"{fraction:.1%}; ETA {round(eta)}s" if eta is not None else "Estimating ETA")
                        last_progress = now
        except Exception as exc:
            cancel.set()
            state["status"] = "failed"
            log(f"Run error: {exc}", error=True)
            raise
        finally:
            for result in state["blocks"].values():
                if result["status"] == "running":
                    result["status"] = "interrupted"
            if state["status"] != "failed":
                state["status"] = ("cancelled" if cancel.is_set() else "failed" if failures
                                   else "complete_with_missing" if inv.missing else "complete")
            state["elapsed_seconds"] = time.monotonic() - started
            save_state(output, state)
            log(f"Run {state['status']}; elapsed {state['elapsed_seconds']:.1f}s")
    return 130 if cancel.is_set() else 1 if failures else 0


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--vrt-file", required=True, type=Path)
    result.add_argument("--output-dir", required=True, type=Path)
    result.add_argument("--block-size", type=block_size_argument, default=10, metavar="DEGREES",
                        help="Positive integer divisor of 360 (default: 10); "
                             "360 creates one whole-world COG")
    result.add_argument("--workers", type=int, default=8)
    result.add_argument("--verify", action="store_true", help="Verify every base pixel and full COG structure")
    result.add_argument("--build-overviews", action="store_true", help="Create internal average overviews")
    result.add_argument("--min-tiles-per-block", type=int, default=1)
    result.add_argument("--filename-filter", help="Case-insensitive basename substring")
    result.add_argument("--dry-run", action="store_true", help="Inspect and print the plan without writing files")
    return result


def run(args, cancel=None):
    validate_block_size(args.block_size)
    if args.workers < 1 or args.min_tiles_per_block < 1:
        raise InputError("Workers and minimum tiles per block must be positive")
    configure()
    cancel = cancel or threading.Event()
    try:
        inv = read_inventory(args.vrt_file, args.filename_filter, cancel,
                             lambda message: print(message, file=sys.stderr, flush=True))
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, ET.ParseError) as exc:
        raise InputError(f"Cannot inventory VRT and source tiles: {exc}") from exc
    blocks = plan_blocks(inv, args.block_size, args.min_tiles_per_block)
    output = args.output_dir
    if output.is_symlink():
        raise InputError("Output directory must not be a symlink")
    output = output.resolve()
    input_paths = {inv.vrt, *(source.path for source in inv.sources)}
    if output in input_paths or any(output / b.name in input_paths for b in blocks):
        raise InputError("Output paths overlap inputs")
    if args.dry_run:
        print(json.dumps({"sources": len(inv.sources), "missing_sources": inv.missing,
                          "blocks": [{"name": b.name, "bounds": b.bounds,
                                      "dimensions": b.window[2:], "tile_count": b.tile_count,
                                      "contributing_sources": len(b.sources), "eligible": b.eligible,
                                      "storage_reservation_bytes": reservation(b, args.build_overviews)}
                                     for b in blocks]}, indent=2))
        return 0
    output.mkdir(parents=True, exist_ok=True)
    with directory_lock(output):
        return execute(inv, blocks, output, args, cancel)


def main(argv=None):
    args = parser().parse_args(argv)
    cancel = threading.Event()
    previous = signal.signal(signal.SIGINT, lambda signum, frame: cancel.set())
    try:
        return run(args, cancel)
    except Cancelled:
        print("Cancelled", file=sys.stderr)
        return 130
    except (InputError, ET.ParseError, FileNotFoundError, ValueError) as exc:
        print(f"Preflight error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Processing error: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    sys.exit(main())
