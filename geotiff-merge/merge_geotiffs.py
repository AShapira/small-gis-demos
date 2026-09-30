#!/usr/bin/env python3
"""QGIS 4.2 / GDAL merge: one mosaic or optional target size, full verification.

QGIS console: import runpy; merger = runpy.run_path('/path/merge_geotiffs.py')
job = merger['start']('/input', '/new-output', target_size='25GB')
"""

import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import subprocess
import time
import threading
import uuid
import zlib

import numpy as np
from osgeo import gdal, gdal_array, osr


@dataclass(frozen=True)
class Grid:
    """GDAL geotransform; no additional Python packages needed in QGIS."""
    c: float
    a: float
    b: float
    f: float
    d: float
    e: float

    def __iter__(self):
        return iter((self.c, self.a, self.b, self.f, self.d, self.e))

    def __mul__(self, point):
        x, y = point
        return self.c + self.a * x + self.b * y, self.f + self.d * x + self.e * y

    def shifted(self, x, y):
        c, f = self * (x, y)
        return Grid(c, self.a, self.b, f, self.d, self.e)


@contextmanager
def opened(path):
    ds = gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_READONLY,
                     allowed_drivers=['GTiff'], open_options=['NUM_THREADS=1'])
    if ds is None:
        raise MergeError(f"Cannot open GeoTIFF: {path}: {gdal.GetLastErrorMsg()}")
    try:
        yield ds
    finally:
        ds.Close()


def read_values(ds, rect, parent):
    data = ds.ReadAsArray(*rect.window(parent))
    if data is None:
        raise MergeError("GDAL failed to read raster values")
    return data[None] if data.ndim == 2 else data


def read_masks(ds, rect, parent):
    return np.stack([ds.GetRasterBand(i).GetMaskBand().ReadAsArray(*rect.window(parent)) != 0
                     for i in range(1, ds.RasterCount + 1)])


def same_crs(a, b):
    left, right = osr.SpatialReference(), osr.SpatialReference()
    left.ImportFromWkt(a)
    right.ImportFromWkt(b)
    return bool(left.IsSame(right))


class MergeError(Exception):
    pass


class Cancelled(MergeError):
    pass


_cancel_file = None


def check_cancel():
    if _cancel_file is not None and _cancel_file.exists():
        raise Cancelled('Cancelled by user; originals unchanged; unpublished outputs remain incomplete')


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self):
        return self.w * self.h

    def intersection(self, other):
        x, y = max(self.x, other.x), max(self.y, other.y)
        right = min(self.x + self.w, other.x + other.w)
        bottom = min(self.y + self.h, other.y + other.h)
        return Rect(x, y, right - x, bottom - y) if right > x and bottom > y else None

    def window(self, parent):
        return self.x - parent.x, self.y - parent.y, self.w, self.h


@dataclass
class Source:
    path: Path
    rect: Rect
    files: tuple


def size_bytes(value):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)?\s*", value, re.I)
    if not match:
        raise argparse.ArgumentTypeError("Use bytes or a size such as 25GB or 25GiB")
    unit = (match[2] or "B").upper()
    powers = {"B": 1, **{p + "B": 1000 ** i for i, p in enumerate("KMGT", 1)},
              **{p + "IB": 1024 ** i for i, p in enumerate("KMGT", 1)}}
    try:
        result = int(Decimal(match[1]) * powers[unit])
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError("Invalid size") from None
    if result < 1:
        raise argparse.ArgumentTypeError("Size must be positive")
    return result


def output_base_name(value):
    # A filename component, portable across Windows and POSIX; never a path.
    if (not value or value in ('.', '..') or value[-1:] in (' ', '.') or
            re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or
            re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value, re.I)):
        raise argparse.ArgumentTypeError('Base name must be a nonempty portable filename stem, not a path')
    return value


def duration(seconds):
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


class Progress:
    """Thread-safe, bounded-frequency console timing; no GDAL calls in the timer."""
    def __init__(self):
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.phase, self.base, self.weight = 'Starting', 0, 0
        self.completed, self.total = 0, 1
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def phase_start(self, name, base, weight, total=1, finished=False):
        with self.lock:
            self.phase, self.base, self.weight = name, base, weight
            self.completed, self.total = 0, max(1, total)
        self.emit(finished=finished)

    def advance(self, phase, amount, extra_total=0):
        with self.lock:
            if self.phase == phase:
                self.completed += amount
                self.total += extra_total

    def emit(self, finished=False):
        with self.lock:
            phase = self.phase
            fraction = self.base + self.weight * min(1, self.completed / self.total)
        elapsed = time.monotonic() - self.started
        if finished:
            timing = 'remaining 00:00:00'
        elif fraction > 0:
            expected = elapsed / fraction
            timing = f'rough expected total {duration(expected)}; remaining ~{duration(expected - elapsed)}'
        else:
            timing = 'rough expected total: estimating; remaining: estimating'
        log(f'[{phase}] elapsed {duration(elapsed)}; {timing}')

    def _heartbeat(self):
        while not self.stop_event.wait(5):
            self.emit()

    def close(self):
        self.stop_event.set()
        self.thread.join()


_progress = None


def advance_work(phase, amount, extra_total=0):
    if _progress is not None:
        _progress.advance(phase, amount, extra_total)


def sha256(path, track_progress=False):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            check_cancel()
            digest.update(data)
            if track_progress:
                advance_work('Hashing inputs', len(data))
                advance_work('Rehashing inputs', len(data))
    return digest.hexdigest()


def log(message):
    print(message, file=sys.stderr, flush=True)


def blocks(rect, edge):
    for y in range(rect.y, rect.y + rect.h, edge):
        for x in range(rect.x, rect.x + rect.w, edge):
            check_cancel()
            yield Rect(x, y, min(edge, rect.x + rect.w - x), min(edge, rect.y + rect.h - y))


def bounds(rects):
    x, y = min(r.x for r in rects), min(r.y for r in rects)
    return Rect(x, y, max(r.x + r.w for r in rects) - x,
                max(r.y + r.h for r in rects) - y)


def split(rect, fraction=0.5):
    """Split on integer pixel boundaries without overlapping or missing cells."""
    if rect.area == 1:
        raise MergeError("The size cap cannot hold even one pixel plus TIFF overhead")
    if rect.w >= rect.h and rect.w > 1:
        n = max(1, min(rect.w - 1, int(rect.w * fraction)))
        return Rect(rect.x, rect.y, n, rect.h), Rect(rect.x + n, rect.y, rect.w - n, rect.h)
    n = max(1, min(rect.h - 1, int(rect.h * fraction)))
    return Rect(rect.x, rect.y, rect.w, n), Rect(rect.x, rect.y + n, rect.w, rect.h - n)


def same_number(a, b):
    return a == b or (a is not None and b is not None and math.isnan(a) and math.isnan(b))


def signature(ds):
    bands = [ds.GetRasterBand(i) for i in range(1, ds.RasterCount + 1)]
    return {"count": ds.RasterCount,
            "dtype": np.dtype(gdal_array.GDALTypeCodeToNumericTypeCode(bands[0].DataType)).name,
            "nodata": bands[0].GetNoDataValue(),
            "nodata_tuple": ' '.join((ds.GetMetadataItem('NODATA_VALUES') or '').split()),
            "scales": tuple(b.GetScale() if b.GetScale() is not None else 1.0 for b in bands),
            "offsets": tuple(b.GetOffset() if b.GetOffset() is not None else 0.0 for b in bands),
            "units": tuple(b.GetUnitType() for b in bands),
            "descriptions": tuple(b.GetDescription() for b in bands),
            "colorinterp": tuple(b.GetColorInterpretation() for b in bands),
            "area_or_point": ds.GetMetadataItem("AREA_OR_POINT") or "Area"}


def compatible(a, b):
    return all(same_number(a[k], b[k]) if k == "nodata" else a[k] == b[k] for k in a)


def inventory(directory):
    paths = sorted((p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in (".tif", ".tiff")),
                   key=lambda p: p.name)
    if not paths:
        raise MergeError("No .tif/.tiff files in the input directory (scan is not recursive)")
    sources, template = [], None
    for path in paths:
        with opened(path) as ds:
            t = Grid(*ds.GetGeoTransform())
            crs = ds.GetProjection()
            if not crs or ds.GetGCPCount() or ds.GetMetadata('RPC'):
                raise MergeError(f"{path.name}: requires a GeoTIFF with a CRS and affine georeferencing")
            if t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0 or not all(math.isfinite(v) for v in t):
                raise MergeError(f"{path.name}: only north-up, unrotated grids are supported")
            sig = signature(ds)
            bands = [ds.GetRasterBand(i) for i in range(1, ds.RasterCount + 1)]
            if len({b.DataType for b in bands}) != 1 or np.dtype(sig['dtype']).kind not in "uif":
                raise MergeError(f"{path.name}: requires uniform real numeric band types")
            if np.dtype(sig['dtype']).itemsize == 8 and np.dtype(sig['dtype']).kind in "ui":
                raise MergeError(f"{path.name}: 64-bit integer nodata metadata is not supported")
            if gdal.GCI_AlphaBand in sig['colorinterp'] or gdal.GCI_PaletteIndex in sig['colorinterp']:
                raise MergeError(f"{path.name}: alpha/palette rasters need explicit preprocessing")
            if any(not same_number(b.GetNoDataValue(), sig['nodata']) for b in bands):
                raise MergeError(f"{path.name}: different per-band nodata is unsupported")
            if sig['nodata_tuple']:
                if sig['nodata'] is not None or len(sig['nodata_tuple'].split()) != ds.RasterCount:
                    raise MergeError(f"{path.name}: tuple nodata requires one value per band and no scalar band nodata")
                # Validate parsing before any output is created. GDAL supplies the
                # tuple-derived common mask; a zero in only one RGB band stays valid.
                [float(v) for v in sig['nodata_tuple'].split()]
            if template is None:
                template = dict(sig, crs=crs, transform=t)
            ref = template["transform"]
            if not same_crs(crs, template["crs"]) or not compatible(sig, {k: template[k] for k in sig}):
                raise MergeError(f"{path.name}: CRS, band types, nodata, or band interpretation differs")
            # Check accumulated resolution drift, not only relative pixel-size error.
            if abs((t.a - ref.a) * ds.RasterXSize / ref.a) > 1e-7 or abs((t.e - ref.e) * ds.RasterYSize / ref.e) > 1e-7:
                raise MergeError(f"{path.name}: pixel sizes differ; resampling would change data")
            x, y = (t.c - ref.c) / ref.a, (t.f - ref.f) / ref.e
            if abs(x - round(x)) > 1e-7 or abs(y - round(y)) > 1e-7:
                raise MergeError(f"{path.name}: grid is not aligned; resampling would change data")
            files = tuple(sorted({Path(f).resolve() for f in ds.GetFileList()}))
            if any(not f.is_file() for f in files):
                raise MergeError(f"{path.name}: all raster dependencies must be local files")
            sources.append(Source(path.resolve(), Rect(round(x), round(y), ds.RasterXSize, ds.RasterYSize), files))
    return sources, template


class Readers:
    """Bound the number of simultaneously open source datasets."""
    def __init__(self, limit=16):
        self.limit, self.open = limit, OrderedDict()

    def get(self, source):
        ds = self.open.pop(source.path, None)
        if ds is None:
            ds = gdal.OpenEx(str(source.path), gdal.OF_RASTER | gdal.OF_READONLY,
                             allowed_drivers=['GTiff'], open_options=['NUM_THREADS=1'])
            if ds is None:
                raise MergeError(f"Cannot read {source.path}")
        self.open[source.path] = ds
        if len(self.open) > self.limit:
            self.open.popitem(last=False)[1].Close()
        return ds

    def close(self):
        for ds in self.open.values():
            ds.Close()
        self.open.clear()


def read(source, rect, readers, template):
    ds = readers.get(source)
    data = read_values(ds, rect, source.rect)
    valid = read_masks(ds, rect, source.rect)
    nd = template["nodata"]
    if nd is None:
        if not np.all(valid == valid[0]):
            raise MergeError(f"{source.path.name}: different band masks without nodata cannot be stored in one internal mask")
    else:
        is_nodata = np.isnan(data) if math.isnan(nd) else data == nd
        if np.any(valid & is_nodata):
            raise MergeError(f"{source.path.name}: a mask marks nodata-valued pixels valid; cannot preserve this with output nodata")
    return data, valid


def bit_equal(a, b):
    """Exact stored sample equality, including signed zero and NaN payloads."""
    a, b = np.ascontiguousarray(a), np.ascontiguousarray(b)
    return np.all(a.view(np.uint8).reshape(a.shape + (a.dtype.itemsize,)) ==
                  b.view(np.uint8).reshape(b.shape + (b.dtype.itemsize,)), axis=-1)


def overlap_scan(sources, template, readers, edge):
    report = []
    for i, a in enumerate(sources):
        for b in sources[i + 1:]:
            rect = a.rect.intersection(b.rect)
            if rect is None:
                continue
            valid_count = different = 0
            example = None
            for part in blocks(rect, edge):
                av, am = read(a, part, readers, template)
                bv, bm = read(b, part, readers, template)
                both = am & bm
                diff = both & ~bit_equal(av, bv)
                advance_work('Checking overlaps', part.area)
                valid_count += int(both.sum())
                different += int(diff.sum())
                if example is None and diff.any():
                    band, row, col = np.argwhere(diff)[0]
                    gx, gy = part.x + int(col), part.y + int(row)
                    wx, wy = template["transform"] * (gx + 0.5, gy + 0.5)
                    example = {"band": int(band) + 1, "grid_col": gx, "grid_row": gy,
                               "x": wx, "y": wy, "first_value": repr(av[band, row, col].item()),
                               "second_value": repr(bv[band, row, col].item())}
            report.append({"first": a.path.name, "second": b.path.name,
                           "footprint_pixels": rect.area, "valid_band_samples": valid_count,
                           "conflicting_band_samples": different, "example": example})
            log(f"Overlap {a.path.name} / {b.path.name}: {different:,} conflicting band samples")
    return report


def plan(rect, sources, target_pixels):
    """Partition occupied footprints; trim empty outer space before subdividing."""
    maximum_pixels = max(1, target_pixels * 11 // 10)
    pending = [rect]
    while pending:
        region = pending.pop()
        hits = [hit for s in sources if (hit := region.intersection(s.rect)) is not None]
        if not hits:
            continue
        region = bounds(hits)
        if region.area <= maximum_pixels or region.area == 1:
            yield region
        else:
            count = math.ceil(region.area / maximum_pixels)
            first, rest = split(region, 1 / count)
            pending.extend((rest, first))


def slices(part, parent):
    return (slice(None), slice(part.y - parent.y, part.y - parent.y + part.h),
            slice(part.x - parent.x, part.x - parent.x + part.w))


def create_candidate(path, region, sources, template, readers, edge, policy, compression):
    nd = template["nodata"]
    fill = nd if nd is not None else 0
    options = ['TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256', 'BIGTIFF=YES',
               'INTERLEAVE=PIXEL', f'COMPRESS={compression.upper()}',
               f'NUM_THREADS={template.get("codec_threads", 1)}']
    hits = [s for s in sources if s.rect.intersection(region)]
    dst = gdal.GetDriverByName('GTiff').Create(str(path), region.w, region.h, template['count'],
            gdal_array.NumericTypeCodeToGDALTypeCode(np.dtype(template['dtype'])), options)
    if dst is None:
        raise MergeError(f"Cannot create {path}: {gdal.GetLastErrorMsg()}")
    try:
        dst.SetProjection(template['crs'])
        dst.SetGeoTransform(tuple(template['transform'].shifted(region.x, region.y)))
        dst.SetMetadataItem('AREA_OR_POINT', template['area_or_point'])
        if template['nodata_tuple']:
            dst.SetMetadataItem('NODATA_VALUES', template['nodata_tuple'])
        for i in range(template['count']):
            b = dst.GetRasterBand(i + 1)
            if nd is not None:
                b.SetNoDataValue(nd)
            b.SetScale(template['scales'][i])
            b.SetOffset(template['offsets'][i])
            b.SetUnitType(template['units'][i])
            b.SetDescription(template['descriptions'][i])
            b.SetColorInterpretation(template['colorinterp'][i])
        if nd is None:
            dst.CreateMaskBand(gdal.GMF_PER_DATASET)
        for part in blocks(region, edge):
            shape = (template["count"], part.h, part.w)
            data = np.full(shape, fill, dtype=template["dtype"])
            if template['nodata_tuple']:
                data[:] = np.array([float(v) for v in template['nodata_tuple'].split()],
                                   dtype=template['dtype'])[:, None, None]
            valid = np.zeros(shape, dtype=bool)
            for src in hits:
                intersection = part.intersection(src.rect)
                if intersection is None:
                    continue
                values, mask = read(src, intersection, readers, template)
                section = slices(intersection, part)
                take = mask if policy == "last" else mask & ~valid[section]
                np.copyto(data[section], values, where=take)
                valid[section] |= mask
            dst.WriteArray(data, xoff=part.x - region.x, yoff=part.y - region.y)
            if nd is None:
                dst.GetRasterBand(1).GetMaskBand().WriteArray(valid[0].astype('uint8') * 255,
                        xoff=part.x - region.x, yoff=part.y - region.y)
            advance_work('Merging and verifying', part.area)
    finally:
        dst.Close()


def verify_output(path, region, sources, template, readers, edge, policy):
    """Independently read sources against the closed output, without recomposing it.

    Default policy checks EVERY valid source sample. Priority policies walk
    sources in winning order and check the first valid sample at each location.
    All remaining masks are checked against the source-validity union.
    """
    checked = discarded = 0
    with opened(path) as dst:
        expected_transform = template["transform"].shifted(region.x, region.y)
        if (dst.RasterXSize != region.w or dst.RasterYSize != region.h or not same_crs(dst.GetProjection(), template['crs']) or
                Grid(*dst.GetGeoTransform()) != expected_transform or not compatible(signature(dst), {k: template[k] for k in signature(dst)})):
            raise MergeError(f"Verification failed: georeferencing or band metadata in {path.name}")
        ordered = list(reversed(sources)) if policy == "last" else sources
        hits = [s for s in ordered if s.rect.intersection(region)]
        for part in blocks(region, edge):
            actual = read_values(dst, part, region)
            actual_mask = read_masks(dst, part, region)
            covered = np.zeros(actual.shape, dtype=bool)
            for src in hits:
                intersection = part.intersection(src.rect)
                if intersection is None:
                    continue
                original, mask = read(src, intersection, readers, template)
                section = slices(intersection, part)
                equal = bit_equal(original, actual[section])
                required = mask if policy == "error" else mask & ~covered[section]
                if np.any(required & (~actual_mask[section] | ~equal)):
                    raise MergeError(f"Verification failed: source pixels differ from {path.name} ({src.path.name})")
                checked += int(required.sum())
                discarded += int((mask & covered[section] & ~equal).sum())
                covered[section] |= mask
            if not np.array_equal(covered, actual_mask):
                raise MergeError(f"Verification failed: validity mask or empty area in {path.name}")
            advance_work('Merging and verifying', part.area)
    return {"checked_source_band_samples": checked, "discarded_conflicting_source_band_samples": discarded}


def ensure_coverage(sources, outputs):
    regions = [Rect(**o["grid_window"]) for o in outputs]
    for i, region in enumerate(regions):
        if any(region.intersection(other) for other in regions[i + 1:]):
            raise MergeError("Verification failed: overlapping output extents")
    for source in sources:
        covered = sum(hit.area for r in regions if (hit := r.intersection(source.rect)) is not None)
        if covered != source.rect.area:
            raise MergeError(f"Verification failed: missing source coverage for {source.path.name}")


def write_report(directory, report):
    temporary = directory / ".report.tmp"
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(directory / "report.json")


def available_resources():
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
    if os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise MergeError('Cannot determine available memory; supply --ram explicitly')
        available = status.available
    else:
        info = Path('/proc/meminfo')
        if info.exists():
            available = int(re.search(r'MemAvailable:\s+(\d+)', info.read_text())[1]) * 1024
        else:
            available = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    return cpus, available


def resource_plan(args, template, sources, jobs):
    detected_cpus, available = available_resources()
    cpus = min(args.cpus or detected_cpus, detected_cpus)
    budget = min(args.ram or int(available * 0.8), int(available * 0.8))
    if budget < 256 * 1024 ** 2:
        raise MergeError('At least 256 MiB of RAM budget and available memory is required')
    cache = min(budget // 4, 4 * 1024 ** 3)
    if args.cache_mib is not None:
        cache = min(cache, args.cache_mib * 1024 ** 2)
    # Include source decode buffers (four cached source datasets per worker).
    decode_sizes = []
    for src in sources:
        with opened(src.path) as ds:
            decode_sizes.append(sum(math.prod(ds.GetRasterBand(i).GetBlockSize()) *
                (gdal.GetDataTypeSize(ds.GetRasterBand(i).DataType) // 8)
                for i in range(1, ds.RasterCount + 1)))
    decode_reserve = 2 * sum(sorted(decode_sizes, reverse=True)[:4])
    edge = args.block_size or 2048
    count, itemsize = template['count'], np.dtype(template['dtype']).itemsize
    # Conservative allowance for source/output arrays, masks, byte comparisons,
    # TIFF codecs, Python/native runtime, and allocator overhead.
    def worker_bytes(n):
        return 64 * 1024 ** 2 + decode_reserve + n * n * count * (itemsize * 10 + 24)
    usable = budget - cache - 128 * 1024 ** 2
    while worker_bytes(edge) > usable and edge > 64:
        edge //= 2
    if worker_bytes(edge) > usable:
        raise MergeError('RAM budget cannot hold the source blocks and a processing window')
    workers = max(1, min(cpus, max(1, jobs), usable // worker_bytes(edge)))
    # GDAL's GTiff writers use one process-wide codec pool, shared by all
    # output files. Dividing this pool by worker count would leave CPUs unused.
    spare_memory = max(0, usable - workers * worker_bytes(edge))
    codec_threads = max(1, min(cpus - workers, spare_memory // (4 * 1024**2)))
    return dict(detected_cpus=detected_cpus, available_ram_bytes=available,
                requested_cpus=args.cpus, requested_ram_bytes=args.ram,
                cpus=cpus, ram_budget_bytes=budget, workers=workers,
                codec_threads_per_worker=codec_threads, codec_pool_shared=True,
                estimated_codec_pool_bytes=codec_threads * 4 * 1024**2 if codec_threads > 1 else 0,
                block_size=edge,
                gdal_cache_bytes=cache, estimated_worker_bytes=worker_bytes(edge),
                ram_limit_kind='working-memory budget, not an OS-enforced process limit')


@contextmanager
def worker_options():
    options = {'GDAL_TIFF_INTERNAL_MASK': 'YES', 'GDAL_NUM_THREADS': '1'}
    before = {k: gdal.GetThreadLocalConfigOption(k) for k in options}
    with gdal.ExceptionMgr(useExceptions=True), osr.ExceptionMgr(useExceptions=True):
        try:
            for k, v in options.items():
                gdal.SetThreadLocalConfigOption(k, v)
            yield
        finally:
            for k, v in before.items():
                gdal.SetThreadLocalConfigOption(k, v)


def estimate_density(sources, template, compression, workers):
    """Sample real 256-pixel TIFF-sized blocks with the same lossless codec."""
    raw = template['count'] * np.dtype(template['dtype']).itemsize
    if compression == 'none':
        return raw
    def sample(src):
        readers = Readers(limit=1)
        total, pixels = 0, 0
        try:
            with worker_options():
                for fy, fx in ((.1, .1), (.1, .5), (.1, .9), (.5, .3), (.5, .7), (.9, .1), (.9, .5), (.9, .9)):
                    w, h = min(256, src.rect.w), min(256, src.rect.h)
                    rect = Rect(src.rect.x + int((src.rect.w - w) * fx),
                                src.rect.y + int((src.rect.h - h) * fy), w, h)
                    values, valid = read(src, rect, readers, template)
                    values[~valid] = template['nodata'] if template['nodata'] is not None else 0
                    if template['nodata_tuple']:
                        for band, value in enumerate(template['nodata_tuple'].split()):
                            values[band][~valid[band]] = float(value)
                    total += len(zlib.compress(np.moveaxis(values, 0, -1).tobytes(), 6)) + 32
                    pixels += rect.area
        finally:
            readers.close()
        return total / pixels * src.rect.area, src.rect.area
    with ThreadPoolExecutor(max_workers=workers) as executor:
        samples = list(executor.map(sample, sources))
    return max(0.001, sum(x for x, _ in samples) / sum(x for _, x in samples))


def process_region(region, staging, sources, template, resources, args):
    readers = Readers(limit=4)
    outputs = []
    try:
        with worker_options():
            pending = [region]
            local_template = dict(template, codec_threads=resources['codec_threads_per_worker'])
            while pending:
                region = pending.pop()
                path = staging / f'candidate-{uuid.uuid4().hex}.tif'
                log(f'Writing {region.w} x {region.h} pixels at ({region.x}, {region.y})')
                create_candidate(path, region, sources, local_template, readers, resources['block_size'],
                                 args.overlap, args.compression)
                size = path.stat().st_size
                if args.max_size is not None and size > args.max_size * 11 // 10:
                    path.unlink()  # This run's unpublished candidate only.
                    log(f'Candidate {size:,} exceeds target +10%; splitting')
                    advance_work('Merging and verifying', 0, extra_total=region.area)
                    first, rest = split(region, min(0.5, args.max_size / size))
                    pending.extend((rest, first))
                    continue
                log(f'Verifying all samples: {size:,} bytes at ({region.x}, {region.y})')
                verified = verify_output(path, region, sources, template, readers,
                                         resources['block_size'], args.overlap)
                with opened(path) as ds:
                    if {Path(f) for f in ds.GetFileList()} != {path}:
                        raise MergeError('Unexpected output sidecar')
                outputs.append(dict(name=path.name, bytes=size, sha256=sha256(path),
                                    grid_window=vars(region), **verified))
    finally:
        readers.close()
    return outputs


def run(args):
    global _progress
    progress = Progress()
    _progress = progress
    progress.thread.start()
    try:
        result = _run(args, progress)
        progress.phase_start(result['status'], 1, 0, finished=True)
        return result
    finally:
        progress.close()
        _progress = None


def _run(args, progress):
    global _cancel_file
    _cancel_file = getattr(args, 'cancel_file', None)
    output_base_name(args.base_name)
    source_dir, output_dir = args.input.resolve(), args.output.resolve()
    if not source_dir.is_dir():
        raise MergeError("Input must be an existing directory")
    if output_dir == source_dir or output_dir in source_dir.parents or source_dir in output_dir.parents:
        raise MergeError("Input and output directories must be separate, non-nested directories")
    # Exclusive directory creation prevents overwrites and concurrent runs.
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "target_file_bytes": args.max_size,
              "max_file_bytes": args.max_size * 11 // 10 if args.max_size is not None else None,
              "base_name": args.base_name, "size_allowance_percent": 10 if args.max_size is not None else None,
              "overlap_policy": args.overlap, "compression": args.compression,
              "gdal": gdal.VersionInfo("RELEASE_NAME"), "outputs": [], "inputs": []}
    started = progress.started
    old_cache = gdal.GetCacheMax()
    readers = Readers()
    staging = output_dir / ".incomplete"
    staging.mkdir()
    try:
        with worker_options():
            check_cancel()
            progress.phase_start('Inspecting inputs', 0, .05)
            sources, template = inventory(source_dir)
            resources = resource_plan(args, template, sources, len(sources))
            gdal.SetCacheMax(resources["gdal_cache_bytes"])
            report["resources"] = resources
            write_report(output_dir, report)
            report["input_order"] = [s.path.name for s in sources]
            report["crs"] = template["crs"]
            report["reference_transform"] = list(template["transform"])[:6]
            report["sample_comparison"] = "bitwise equality of every required valid band sample; exhaustive validity masks"
            fingerprints = {}
            log(f"Hashing {len(sources)} inputs and their GDAL-reported dependencies")
            files = sorted({p for src in sources for p in src.files})
            progress.phase_start('Hashing inputs', .05, .15, sum(p.stat().st_size for p in files))
            with ThreadPoolExecutor(max_workers=resources['workers']) as executor:
                hashes = executor.map(lambda p: sha256(p, track_progress=True), files)
                fingerprints = {p: dict(bytes=p.stat().st_size, sha256=h) for p, h in zip(files, hashes)}
            for source in sources:
                report["inputs"].append({"name": source.path.name, "grid_window": vars(source.rect),
                                         "dependencies": [{"path": str(p), **fingerprints[p]} for p in source.files]})
            log("Checking overlap pixels")
            pairs = [(a, b) for i, a in enumerate(sources) for b in sources[i + 1:]
                     if a.rect.intersection(b.rect)]
            progress.phase_start('Checking overlaps', .2, .15,
                                 sum(a.rect.intersection(b.rect).area for a, b in pairs))
            def scan_pair(pair):
                local = Readers(limit=2)
                try:
                    with worker_options():
                        return overlap_scan(list(pair), template, local, resources['block_size'])[0]
                finally:
                    local.close()
            with ThreadPoolExecutor(max_workers=resources['workers']) as executor:
                report['overlaps'] = list(executor.map(scan_pair, pairs))
            conflicts = sum(p["conflicting_band_samples"] for p in report["overlaps"])
            if conflicts:
                report["overlap_advice"] = [
                    "Keep the originals as separate layers to retain all conflicting observations.",
                    "If one source is authoritative, rerun with --overlap first or last after reviewing input_order.",
                    "First/last discard competing values; averaging/blending changes data and is not offered."]
                if args.overlap == "error":
                    raise MergeError("Conflicting valid overlap pixels: review report.json; choose source priority or keep separate layers")
            if not args.analyze_only:
                progress.phase_start('Planning outputs', .35, .05)
                extent = bounds([s.rect for s in sources])
                if args.max_size is None:
                    regions = [extent]
                else:
                    log('Calibrating output size from source samples')
                    bpp = estimate_density(sources, template, args.compression, resources['workers'])
                    report['estimated_bytes_per_pixel'] = bpp
                    target = max(1, int(max(1, args.max_size - 4096) / bpp))
                    regions = list(plan(extent, sources, target))
                resources = resource_plan(args, template, sources, len(regions))
                report['resources'] = resources
                log(f"Resources: {resources['workers']} file workers + shared pool of "
                    f"{resources['codec_threads_per_worker']} codec threads; "
                    f"RAM budget {resources['ram_budget_bytes'] / 1024**3:.2f} GiB")
                write_report(output_dir, report)
                progress.phase_start('Merging and verifying', .4, .5, 2 * sum(r.area for r in regions))
                with ThreadPoolExecutor(max_workers=resources['workers']) as executor:
                    futures = [executor.submit(process_region, region, staging, sources,
                                               template, resources, args) for region in regions]
                    for future in futures:
                        report['outputs'].extend(future.result())
                for i, output in enumerate(report['outputs'], 1):
                    name = f'{args.base_name}-{i:05d}.tif'
                    (staging / output['name']).rename(staging / name)
                    output['name'] = name
                ensure_coverage(sources, report["outputs"])
            readers.close()
            log("Rehashing inputs to verify they remained unchanged")
            progress.phase_start('Rehashing inputs', .9, .1, sum(v['bytes'] for v in fingerprints.values()))
            current, _ = inventory(source_dir)
            if [(s.path, s.files) for s in current] != [(s.path, s.files) for s in sources]:
                raise MergeError("Input file list or dependency list changed during the run")
            with ThreadPoolExecutor(max_workers=resources['workers']) as executor:
                for path, digest in zip(files, executor.map(lambda p: sha256(p, track_progress=True), files)):
                    before = fingerprints[path]
                    if path.stat().st_size != before["bytes"] or digest != before["sha256"]:
                        raise MergeError(f"Input changed during the run: {path}")
            report["inputs_unchanged"] = True
            report["all_source_valid_values_preserved"] = None if args.analyze_only else not conflicts
            if args.analyze_only:
                report["status"] = "analyzed"
            else:
                for output in report["outputs"]:
                    (staging / output["name"]).rename(output_dir / output["name"])
                report["status"] = "complete"
            staging.rmdir()
            report["elapsed_seconds"] = time.monotonic() - started
            write_report(output_dir, report)
            log(f"{report['status']}: {len(report['outputs'])} output files; {output_dir / 'report.json'}")
            return report
    except BaseException as exc:
        report["status"] = "cancelled" if isinstance(exc, (Cancelled, KeyboardInterrupt)) else "failed"
        report["error"] = str(exc) or type(exc).__name__
        write_report(output_dir, report)
        raise
    finally:
        readers.close()
        gdal.SetCacheMax(old_cache)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("input", type=Path, help="Flat directory of input .tif/.tiff files")
    result.add_argument("output", type=Path, help="New, separate output directory; must not exist")
    result.add_argument("--target-size", "--max-size", dest="max_size", type=size_bytes,
                        help="Optional target TIFF size (+10%% allowed); omit to produce one TIFF")
    result.add_argument("--base-name", default="mosaic", type=output_base_name,
                        help="Output filename stem; default mosaic produces mosaic-00001.tif, etc.")
    result.add_argument("--cpus", type=int, help="Maximum worker/codec CPU concurrency; default all available CPUs")
    result.add_argument("--ram", type=size_bytes, help="Working-memory budget, e.g. 8GiB; default 80%% of available RAM")
    result.add_argument('--cancel-file', type=Path, help=argparse.SUPPRESS)
    result.add_argument("--overlap", choices=("error", "first", "last"), default="error",
                        help="Conflicting valid samples: stop (default), or use filename order priority")
    result.add_argument("--analyze-only", action="store_true", help="Hash inputs and inspect overlaps without creating mosaics")
    result.add_argument("--compression", choices=("deflate", "none"), default="deflate")
    result.add_argument("--block-size", type=int, help="Processing window edge; default auto, up to 2048 pixels")
    result.add_argument("--cache-mib", type=int, help="Optional GDAL cache ceiling in MiB; otherwise derived from RAM budget")
    return result


class Job:
    """Asynchronous isolated worker; Qt progress is printed only on the GUI thread."""
    def __init__(self, process, output, log_path, cancel_file):
        self.process, self.output = process, output
        self.log_path, self.cancel_file = log_path, cancel_file
        self._position = 0
        self._timer = None

    @property
    def done(self):
        return self.process.poll() is not None

    def status(self):
        report = {}
        path = self.output / 'report.json'
        if path.exists():
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        code = self.process.poll()
        state = 'running' if code is None else report.get('status', 'failed')
        if code not in (None, 0) and state not in ('failed', 'cancelled'):
            state = 'failed'
        return dict(state=state, pid=self.process.pid, exit_code=code,
                    log=str(self.log_path), report=report)

    def cancel(self):
        if not self.done:
            self.cancel_file.write_text('cancel\n', encoding='utf-8')

    def result(self):
        status = self.status()
        if not self.done:
            raise RuntimeError('Job is still running; call status() later')
        if status['exit_code'] != 0:
            raise MergeError(status['report'].get('error', f"Worker failed; see {self.log_path}"))
        return status['report']

    def _tick(self):
        with self.log_path.open('r', encoding='utf-8', errors='replace') as stream:
            stream.seek(self._position)
            text = stream.read()
            self._position = stream.tell()
        if text:
            print(text, end='')
        if self.done and self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
            live = getattr(sys, '_geotiff_merge_jobs', [])
            if self in live:
                live.remove(self)


def start(input_dir, output_dir, *, target_size=None, base_name='mosaic', cpus=None, ram=None,
          overlap='error', compression='deflate', analyze_only=False, block_size=None,
          cache_mib=None, python_executable=None):
    """Run from QGIS 4.2 console without blocking its Qt event loop.

    Uses QGIS's own Python in a separate process, so its GDAL cache and CPU
    settings cannot modify those of the interactive QGIS application.
    """
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f'Output directory must be new: {output}')
    executable = Path(python_executable) if python_executable else (
        Path(sys.prefix) / 'python.exe' if os.name == 'nt' else Path(sys.executable))
    if not executable.is_file():
        raise MergeError('Cannot locate QGIS Python; pass python_executable explicitly')
    output.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    log_path = output.parent / f'.{output.name}-{token}.log'
    cancel_file = output.parent / f'.{output.name}-{token}.cancel'
    command = [str(executable), '-u', str(Path(__file__).resolve()), str(input_dir), str(output),
               '--base-name', base_name, '--overlap', overlap, '--compression', compression,
               '--cancel-file', str(cancel_file)]
    if target_size is not None:
        command += ['--target-size', str(target_size)]
    if block_size is not None:
        command += ['--block-size', str(block_size)]
    if cache_mib is not None:
        command += ['--cache-mib', str(cache_mib)]
    if cpus is not None:
        command += ['--cpus', str(cpus)]
    if ram is not None:
        command += ['--ram', str(ram)]
    if analyze_only:
        command += ['--analyze-only']
    # Validate before launching; do not send invalid parameters to a background job.
    parser().parse_args(command[3:])
    environment = dict(os.environ, PYTHONIOENCODING='utf-8', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    with log_path.open('x', encoding='utf-8') as log_stream:
        process = subprocess.Popen(command, stdout=log_stream, stderr=subprocess.STDOUT,
                                   env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    job = Job(process, output, log_path, cancel_file)
    try:
        from qgis.PyQt.QtCore import QCoreApplication, QTimer
        if QCoreApplication.instance() is not None:
            timer = QTimer()
            timer.setInterval(1000)
            timer.timeout.connect(job._tick)
            job._timer = timer
            if not hasattr(sys, '_geotiff_merge_jobs'):
                sys._geotiff_merge_jobs = []
            sys._geotiff_merge_jobs.append(job)
            timer.start()
    except ImportError:
        pass
    return job


def limit_cpu_affinity(count):
    if count is None:
        return
    if hasattr(os, 'sched_getaffinity'):
        allowed = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, allowed[:count])
    elif os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        process = kernel.GetCurrentProcess()
        available, system = ctypes.c_size_t(), ctypes.c_size_t()
        if not kernel.GetProcessAffinityMask(process, ctypes.byref(available), ctypes.byref(system)):
            raise MergeError('Cannot inspect CPU affinity')
        bits = [1 << i for i in range(ctypes.sizeof(ctypes.c_size_t) * 8) if available.value & (1 << i)]
        if not kernel.SetProcessAffinityMask(process, sum(bits[:count])):
            raise MergeError('Cannot apply CPU affinity limit')


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if any(value is not None and value < 1 for value in (args.block_size, args.cache_mib, args.cpus)):
        p.error("--block-size, --cache-mib and --cpus must be positive")
    try:
        limit_cpu_affinity(args.cpus)
        run(args)
    except Cancelled as exc:
        log(str(exc))
        return 130
    except (MergeError, OSError, RuntimeError) as exc:
        log(f"ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        log("Interrupted; output directory is incomplete (see report.json)")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
