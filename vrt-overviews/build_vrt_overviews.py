#!/usr/bin/env python3
"""Build one VRT and external JPEG overviews from aligned Byte GeoTIFFs.

Run with QGIS/OSGeo4W Python. Only the standard library and osgeo are required.
Source rasters are opened read-only; no source pyramids are created or changed.
"""

import argparse
from contextlib import contextmanager
import copy
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
import uuid
import xml.etree.ElementTree as ET

try:
    from osgeo import gdal, osr
except ImportError:
    gdal = osr = None


EXTENSIONS = {'.tif', '.tiff', '.geotiff'}
GRID_TOLERANCE = 1e-6  # In pixels, including accumulated extent differences.
ARTIFACT_SUFFIXES = ('', '.ovr', '.ovr.msk', '.sources.txt', '.report.json')


class BuildError(Exception):
    pass


class Cancelled(BuildError):
    pass


class Progress:
    def __init__(self):
        self.cancelled = False
        self.started = time.monotonic()
        self.last = 0.0
        self.phase = ''

    def check(self):
        if self.cancelled:
            raise Cancelled('Cancelled; no finished output was published.')

    def begin(self, phase):
        self.check()
        self.phase = phase
        self.last = 0.0
        print(f'{phase} ...', flush=True)

    def callback(self, fraction, message='', unused=None):
        if self.cancelled:
            return 0
        now = time.monotonic()
        if fraction >= 1 or now - self.last >= 2:
            print(f'  {self.phase}: {fraction:.0%} ({now - self.started:.1f}s elapsed)', flush=True)
            self.last = now
        return 1


@contextmanager
def config(options):
    previous = {key: gdal.GetConfigOption(key) for key in options}
    try:
        for key, value in options.items():
            gdal.SetConfigOption(key, str(value) if value is not None else None)
        yield
    finally:
        for key, value in previous.items():
            gdal.SetConfigOption(key, value)


@contextmanager
def opened(path, access=None):
    ds = gdal.Open(str(path), gdal.GA_ReadOnly if access is None else access)
    if ds is None:
        raise BuildError(f'Cannot open {path}')
    try:
        yield ds
    finally:
        ds.Close()


def artifact_paths(output):
    return [Path(str(output) + suffix) for suffix in ARTIFACT_SUFFIXES]


def ensure_absent(output, allow_lock=False):
    # Include alternate/auxiliary pyramids: otherwise GDAL might select stale data.
    for suffix in (*ARTIFACT_SUFFIXES, '.aux.xml', '.msk', '.msk.ovr', '.ovr.aux.xml',
                   '.ovr.msk.ovr', '.ovr.msk.aux.xml', '.aux', '.rrd', '.lock'):
        path = Path(str(output) + suffix)
        if suffix == '.lock' and allow_lock:
            continue
        if os.path.lexists(path):
            raise BuildError(f'Output already exists: {path}. Choose a new output name.')


def discover(folders, progress):
    files, seen = [], set()
    for folder in folders:
        root = Path(folder).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise BuildError(f'Input must be a directory: {root}')
        found = []

        def scan_error(error):
            raise BuildError(f'Cannot scan input folder: {error}')

        for directory, dirs, names in os.walk(root, followlinks=False, onerror=scan_error):
            progress.check()
            # Avoid directory loops, including Windows junctions where detectable.
            dirs[:] = [name for name in dirs if not Path(directory, name).is_symlink()
                       and not (hasattr(os.path, 'isjunction')
                                and os.path.isjunction(Path(directory, name)))]
            for name in names:
                path = Path(directory, name)
                if path.suffix.lower() in EXTENSIONS:
                    found.append(path.resolve(strict=True))
        for path in sorted(found, key=lambda p: (os.path.normcase(str(p)), str(p))):
            progress.check()
            key = os.path.normcase(str(path))
            if key not in seen:
                if '\n' in str(path) or '\r' in str(path):
                    raise BuildError(f'Newlines in source filenames are unsupported: {path!s}')
                seen.add(key)
                files.append(path)
    if not files:
        raise BuildError('No .tif, .tiff, or .geotiff files found in the supplied folders.')
    return files


def inspect_sources(files, progress):
    sources, reference, reference_crs = [], None, None
    progress.begin(f'Inspecting {len(files)} source files')
    for index, path in enumerate(files):
        progress.check()
        try:
            with opened(path) as ds:
                if ds.GetDriver().ShortName != 'GTiff' or ds.GetSubDatasets():
                    raise BuildError('Expected a single-image GeoTIFF.')
                if ds.RasterCount not in (1, 3):
                    raise BuildError('Expected one grayscale band or three RGB bands; alpha is unsupported.')
                interpretations = []
                for number in range(1, ds.RasterCount + 1):
                    band = ds.GetRasterBand(number)
                    if band.DataType != gdal.GDT_Byte or band.GetColorTable() is not None:
                        raise BuildError('Only non-palette 8-bit (Byte) imagery is supported.')
                    expected = ([gdal.GCI_GrayIndex] if ds.RasterCount == 1 else
                                [gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand])
                    if band.GetColorInterpretation() not in (expected[number - 1], gdal.GCI_Undefined):
                        raise BuildError('Unsupported band color interpretation; expected grayscale or RGB order.')
                    interpretations.append(band.GetColorInterpretation())
                transform = ds.GetGeoTransform(can_return_null=True)
                projection = ds.GetProjection()
                if not projection or not transform or not all(math.isfinite(x) for x in transform):
                    raise BuildError('Missing or invalid CRS/geotransform.')
                if transform[2] != 0 or transform[4] != 0 or transform[1] <= 0 or transform[5] >= 0:
                    raise BuildError('Expected a north-up grid without rotation.')
                crs = osr.SpatialReference()
                crs.ImportFromWkt(projection)
                if reference is None:
                    reference = dict(transform=transform, projection=projection, bands=ds.RasterCount,
                                     color_interpretations=interpretations)
                    reference_crs = crs
                ref = reference['transform']
                if (not crs.IsSame(reference_crs) or ds.RasterCount != reference['bands'] or
                        interpretations != reference['color_interpretations']):
                    raise BuildError('CRS or band layout differs from the first source.')
                # Bound accumulated pixel-size error across each tile, not just error per pixel.
                if (abs(transform[1] / ref[1] - 1) * ds.RasterXSize > GRID_TOLERANCE or
                        abs(transform[5] / ref[5] - 1) * ds.RasterYSize > GRID_TOLERANCE):
                    raise BuildError('Pixel size differs from the first source.')
                x = (transform[0] - ref[0]) / ref[1]
                y = (transform[3] - ref[3]) / ref[5]
                if abs(x - round(x)) > GRID_TOLERANCE or abs(y - round(y)) > GRID_TOLERANCE:
                    raise BuildError('Pixel grid is not aligned with the first source.')
                dependencies = []
                for name in ds.GetFileList() or [str(path)]:
                    item = Path(name)
                    stat = item.stat()
                    dependencies.append(dict(path=str(item), size=stat.st_size, mtime_ns=stat.st_mtime_ns))
                sources.append(dict(path=str(path), width=ds.RasterXSize, height=ds.RasterYSize,
                                    x=round(x), y=round(y), dependencies=dependencies))
                band = None
            ds = None
        except (RuntimeError, OSError, BuildError) as error:
            raise BuildError(f'{path}: {error}') from error
        if not progress.callback((index + 1) / len(files)):
            progress.check()
    left, top = min(s['x'] for s in sources), min(s['y'] for s in sources)
    width = max(s['x'] + s['width'] for s in sources) - left
    height = max(s['y'] + s['height'] for s in sources) - top
    if max(width, height) > 2_147_483_647:
        raise BuildError('The mosaic exceeds GDAL raster dimension limits.')
    ref = reference['transform']
    reference.update(width=width, height=height,
                     transform=(ref[0] + left * ref[1], ref[1], 0,
                                ref[3] + top * ref[5], 0, ref[5]))
    for source in sources:
        source['x'] -= left
        source['y'] -= top
    return sources, reference


def overview_levels(width, height, explicit, min_size):
    if explicit:
        if any(n < 2 or n > 2_147_483_647 or n & (n - 1) for n in explicit):
            raise BuildError('--levels must be powers of two between 2 and 1073741824.')
        if explicit != sorted(set(explicit)):
            raise BuildError('--levels must be strictly increasing without duplicates.')
        levels = explicit
    else:
        levels, factor = [], 1
        while math.ceil(max(width, height) / factor) > min_size:
            factor *= 2
            levels.append(factor)
    sizes = [(math.ceil(width / factor), math.ceil(height / factor)) for factor in levels]
    if len(set(sizes)) != len(sizes):
        raise BuildError('Requested overview levels produce duplicate raster dimensions.')
    return levels


def source_reference(path, directory):
    try:
        return os.path.relpath(path, directory).replace(os.sep, '/'), '1'
    except ValueError:  # Windows paths on different drives/shares.
        return str(path).replace(os.sep, '/'), '0'


def build_vrt(path, sources, grid, progress):
    progress.begin('Building VRT')
    ds = gdal.BuildVRT(str(path), [s['path'] for s in sources], strict=True, callback=progress.callback)
    if ds is None:
        raise BuildError('GDAL did not create the VRT.')
    ds.Close()
    ds = None
    tree = ET.parse(path)
    root = tree.getroot()
    root.set('rasterXSize', str(grid['width']))
    root.set('rasterYSize', str(grid['height']))
    root.find('GeoTransform').text = ', '.join(format(v, '.17g') for v in grid['transform'])
    for child in list(root):
        if child.tag in ('MaskBand', 'Metadata'):
            root.remove(child)
    absolute = set()
    for number, band in enumerate(root.findall('VRTRasterBand'), 1):
        progress.check()
        # Explicit masks are authoritative. No scalar sentinel is assigned to the mosaic:
        # valid black pixels and JPEG-rounded values must never become transparent.
        for child in list(band):
            if child.tag not in ('Description', 'ColorInterp'):
                band.remove(child)
        color = band.find('ColorInterp')
        if color is None:
            color = ET.SubElement(band, 'ColorInterp')
        color.text = 'Gray' if grid['bands'] == 1 else ('Red', 'Green', 'Blue')[number - 1]
        mask = ET.SubElement(ET.SubElement(band, 'MaskBand'), 'VRTRasterBand', dataType='Byte')
        for source in sources:
            progress.check()
            filename, relative = source_reference(source['path'], path.parent)
            if relative == '0':
                absolute.add(source['path'])
            for parent, is_mask in ((band, False), (mask, True)):
                node = ET.SubElement(parent, 'ComplexSource')
                ET.SubElement(node, 'SourceFilename', relativeToVRT=relative).text = filename
                ET.SubElement(node, 'SourceBand').text = f'mask,{number}' if is_mask else str(number)
                ET.SubElement(node, 'SrcRect', xOff='0', yOff='0',
                              xSize=str(source['width']), ySize=str(source['height']))
                ET.SubElement(node, 'DstRect', xOff=str(source['x']), yOff=str(source['y']),
                              xSize=str(source['width']), ySize=str(source['height']))
                if is_mask:
                    ET.SubElement(node, 'NODATA').text = '0'
                    ET.SubElement(node, 'LUT').text = '0:0,1:255,255:255'
                else:
                    ET.SubElement(node, 'UseMaskBand').text = 'true'
    tree.write(path, encoding='utf-8', xml_declaration=True)
    return sorted(absolute)


def make_mask_vrt(vrt, destination):
    original = ET.parse(vrt).getroot()
    result = ET.Element('VRTDataset', original.attrib)
    for name in ('SRS', 'GeoTransform'):
        result.append(copy.deepcopy(original.find(name)))
    bands = original.findall('VRTRasterBand')
    for number, band in enumerate(bands, 1):
        mask = copy.deepcopy(band.find('MaskBand/VRTRasterBand'))
        mask.set('band', str(number))
        # A three-band TIFF uses RGB photometric tags even though these are mask
        # samples. This avoids inconsistent ExtraSamples tags in its lower IFDs.
        ET.SubElement(mask, 'ColorInterp').text = 'Gray' if len(bands) == 1 else ('Red', 'Green', 'Blue')[number - 1]
        result.append(mask)
    ET.ElementTree(result).write(destination, encoding='utf-8', xml_declaration=True)


def build_overviews(vrt, levels, grid, args, progress, mask_vrt):
    if not levels:
        return
    overview = Path(str(vrt) + '.ovr')
    progress.begin('Building JPEG overviews')
    with config(dict(COMPRESS_OVERVIEW='JPEG', JPEG_QUALITY_OVERVIEW=args.jpeg_quality,
                     PHOTOMETRIC_OVERVIEW='YCBCR' if grid['bands'] == 3 else 'MINISBLACK',
                     INTERLEAVE_OVERVIEW='PIXEL', BIGTIFF_OVERVIEW='YES',
                     GDAL_TIFF_OVR_BLOCKSIZE='256', VRT_VIRTUAL_OVERVIEWS='NO',
                     USE_RRD='NO', TIFF_USE_OVR='YES', SPARSE_OK_OVERVIEW='OFF')):
        with opened(vrt) as ds:
            result = ds.BuildOverviews(args.resampling.upper(), levels, callback=progress.callback)
            if result != 0:
                raise BuildError('GDAL failed to build JPEG overviews.')
        ds = None
    progress.check()
    progress.begin('Building lossless overview masks')
    make_mask_vrt(vrt, mask_vrt)
    width, height = math.ceil(grid['width'] / levels[0]), math.ceil(grid['height'] / levels[0])
    # A conventional external GDAL mask for the .ovr TIFF. Its bands and internal
    # overviews match the JPEG TIFF's bands/IFDs, without a full-resolution mask TIFF.
    mask = gdal.Translate(str(overview) + '.msk', str(mask_vrt), format='GTiff',
                          width=width, height=height, resampleAlg='nearest',
                          creationOptions=['TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256',
                                           'COMPRESS=DEFLATE', 'INTERLEAVE=BAND', 'BIGTIFF=YES',
                                           'PHOTOMETRIC=RGB' if grid['bands'] == 3 else 'PHOTOMETRIC=MINISBLACK'],
                          callback=progress.callback)
    if mask is None:
        raise BuildError('GDAL failed to create overview masks.')
    try:
        for number in range(1, grid['bands'] + 1):
            mask.SetMetadataItem(f'INTERNAL_MASK_FLAGS_{number}', '0')
        mask.FlushCache()
        with config(dict(COMPRESS_OVERVIEW='DEFLATE',
                         PHOTOMETRIC_OVERVIEW='RGB' if grid['bands'] == 3 else 'MINISBLACK',
                         INTERLEAVE_OVERVIEW='BAND', GDAL_TIFF_OVR_BLOCKSIZE='256',
                         TIFF_USE_OVR='NO', USE_RRD='NO')):
            if len(levels) > 1:
                progress.begin('Building lower mask levels')
                if mask.BuildOverviews('NEAREST', [n // levels[0] for n in levels[1:]],
                                       callback=progress.callback) != 0:
                    raise BuildError('GDAL failed to create the lossless mask pyramid.')
    finally:
        mask.Close()
    mask = None


def verify(vrt, grid, levels, progress):
    progress.begin('Verifying output')
    with opened(vrt) as ds:
        if (ds.RasterXSize, ds.RasterYSize, ds.RasterCount) != (grid['width'], grid['height'], grid['bands']):
            raise BuildError('Unexpected VRT dimensions/band count.')
        if ds.GetGeoTransform() != tuple(grid['transform']):
            raise BuildError('Unexpected VRT geotransform.')
        expected_crs, actual_crs = osr.SpatialReference(), osr.SpatialReference()
        expected_crs.ImportFromWkt(grid['projection'])
        actual_crs.ImportFromWkt(ds.GetProjection())
        if not expected_crs.IsSame(actual_crs):
            raise BuildError('Unexpected VRT CRS.')
        for number in range(1, grid['bands'] + 1):
            band = ds.GetRasterBand(number)
            if band.GetOverviewCount() != len(levels):
                raise BuildError('Unexpected overview count.')
            for index, factor in enumerate([1] + levels):
                progress.check()
                current = band if index == 0 else band.GetOverview(index - 1)
                expected = math.ceil(grid['width'] / factor), math.ceil(grid['height'] / factor)
                if (current.XSize, current.YSize) != expected or current.DataType != gdal.GDT_Byte:
                    raise BuildError('Unexpected overview dimensions/type.')
                if current.GetMaskFlags() != 0:
                    raise BuildError(f'Band {number}, factor {factor}: missing explicit per-band mask.')
                for x, y in ((0, 0), ((expected[0] - 1) // 2, (expected[1] - 1) // 2),
                             (expected[0] - 1, expected[1] - 1)):
                    if current.ReadRaster(x, y, 1, 1) is None:
                        raise BuildError('Cannot decode raster sample.')
                    mask_value = current.GetMaskBand().ReadRaster(x, y, 1, 1)
                    if mask_value not in (b'\x00', b'\xff'):
                        raise BuildError('Cannot decode binary validity mask.')
                current = None
            band = None
    ds = None
    for suffix, compression in (('.ovr', 'JPEG'), ('.ovr.msk', 'DEFLATE')):
        if not levels:
            break
        file = Path(str(vrt) + suffix)
        with file.open('rb') as stream:
            if stream.read(4) not in (b'II+\x00', b'MM\x00+'):
                raise BuildError(f'{file.name} is not BigTIFF.')
        # Inspect every TIFF image directory, not only the first overview.
        for index in range(len(levels)):
            progress.check()
            with opened(f'GTIFF_DIR:{index + 1}:{file}') as tif:
                codec = tif.GetMetadata('IMAGE_STRUCTURE').get('COMPRESSION', '')
                if not codec.endswith(compression) or tif.GetRasterBand(1).GetBlockSize() != [256, 256]:
                    raise BuildError(f'Unexpected compression/block size in {file.name}, IFD {index + 1}.')
            tif = None
    return dict(structure='passed', raster_and_mask_samples='passed',
                exhaustive_pixels=False, overview_levels_verified=len(levels))


def assert_sources_unchanged(sources, progress):
    for source in sources:
        progress.check()
        for item in source['dependencies']:
            stat = Path(item['path']).stat()
            if (stat.st_size, stat.st_mtime_ns) != (item['size'], item['mtime_ns']):
                raise BuildError(f'Source changed during the run: {item["path"]}')


def check_runtime():
    if gdal is None:
        raise BuildError('GDAL Python bindings are missing. Run with Python from the OSGeo4W/QGIS shell.')
    if int(gdal.VersionInfo('VERSION_NUM')) < 3080000:
        raise BuildError('GDAL 3.8 or newer is required (including explicit dataset closing on Windows).')
    driver = gdal.GetDriverByName('GTiff')
    if driver is None or 'JPEG' not in (driver.GetMetadataItem('DMD_CREATIONOPTIONLIST') or ''):
        raise BuildError('This GDAL build lacks GeoTIFF JPEG compression support.')


def run(args, progress=None):
    check_runtime()
    progress = progress or Progress()
    output = args.output.expanduser().absolute()
    # Resolve the parent only, so an existing output symlink cannot redirect the job.
    output = output.parent.resolve() / output.name
    if output.suffix.lower() != '.vrt':
        raise BuildError('--output must end in .vrt.')
    ensure_absent(output)
    files = discover(args.inputs, progress)
    sources, grid = inspect_sources(files, progress)
    levels = overview_levels(grid['width'], grid['height'], args.levels, args.min_size)
    report = dict(gdal_version=gdal.VersionInfo('--version'), output=str(output), source_count=len(sources),
                  grid=grid, levels=levels, settings=dict(jpeg_quality=args.jpeg_quality,
                  resampling=args.resampling, mask_resampling='nearest', tile_size=256,
                  bigtiff=True, threads=args.threads, cache_mib=args.cache_mb, dataset_pool_size=128),
                  input_priority='supplied folder order, sorted canonical paths; later valid samples win',
                  validity='explicit per-band masks; no output NoData sentinel', sources=sources)
    overview_pixels = sum(math.ceil(grid['width'] / n) * math.ceil(grid['height'] / n) for n in levels)
    report['uncompressed_overview_and_mask_bytes'] = overview_pixels * grid['bands'] * 2
    print(f'{len(sources)} sources; {grid["width"]} x {grid["height"]}; '
          f'{grid["bands"]} bands; overview factors: {levels or "none (already small)"}', flush=True)
    print(f'Uncompressed overview + mask pixels: {report["uncompressed_overview_and_mask_bytes"] / 2**30:.2f} GiB '
          '(compressed size and peak memory are data-dependent).', flush=True)
    if args.dry_run:
        report['dry_run'] = True
        preview = {key: value for key, value in report.items() if key != 'sources'}
        preview['first_sources'] = [s['path'] for s in sources[:10]]
        print(json.dumps(preview, indent=2), flush=True)
        return report
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = Path(str(output) + '.lock')
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise BuildError(f'Another build owns {lock}.') from None
    # The temporary VRT must be in the final parent to keep relative source paths valid.
    temporary = output.parent / f'.{output.name}.{uuid.uuid4().hex}.partial.vrt'
    mask_vrt = Path(str(temporary) + '.mask.vrt')
    published = []
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(pid=os.getpid(), output=str(output))))
        ensure_absent(output, allow_lock=True)
        old_cache = gdal.GetCacheMax()
        try:
            gdal.SetCacheMax(args.cache_mb * 1024 * 1024)
            with config(dict(GDAL_NUM_THREADS=args.threads, GDAL_MAX_DATASET_POOL_SIZE='128',
                             GDAL_MAX_DATASET_POOL_RAM_USAGE=args.cache_mb * 1024 * 1024,
                             GDAL_DISABLE_READDIR_ON_OPEN='TRUE', VSI_CACHE='FALSE')):
                report['absolute_source_references'] = build_vrt(temporary, sources, grid, progress)
                build_overviews(temporary, levels, grid, args, progress, mask_vrt)
                report['verification'] = verify(temporary, grid, levels, progress)
        finally:
            gdal.SetCacheMax(old_cache)
        assert_sources_unchanged(sources, progress)
        report['elapsed_seconds'] = round(time.monotonic() - progress.started, 3)
        report['artifacts'] = [str(p) for p in artifact_paths(output)
                               if levels or not str(p).endswith(('.ovr', '.ovr.msk'))]
        Path(str(temporary) + '.sources.txt').write_text(''.join(s['path'] + '\n' for s in sources), encoding='utf-8')
        Path(str(temporary) + '.report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        progress.check()
        # Claim each name without overwriting. Move the VRT last so consumers do not
        # discover a finished-looking VRT before its sidecars have been installed.
        for suffix in (*ARTIFACT_SUFFIXES[1:], ''):
            src, dest = Path(str(temporary) + suffix), Path(str(output) + suffix)
            if not src.exists():
                continue
            with dest.open('xb'):
                pass
            published.append(dest)
            os.replace(src, dest)
        print(f'Complete: {output} ({report["elapsed_seconds"]:.1f}s)', flush=True)
        if report['absolute_source_references']:
            print('Some sources require absolute paths across drives/shares; see the report.', flush=True)
        return report
    except BaseException:
        for dest in reversed(published):
            dest.unlink(missing_ok=True)
        if progress.cancelled:
            raise Cancelled('Cancelled; temporary output was removed.') from None
        raise
    finally:
        # Delete only this job's unique temporary files; never glob user output names.
        for suffix in (*ARTIFACT_SUFFIXES, '.mask.vrt', '.aux.xml', '.ovr.aux.xml', '.ovr.msk.aux.xml'):
            Path(str(temporary) + suffix).unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return value


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('inputs', nargs='+', type=Path, help='Input folders (recursive; earlier folders have lower priority)')
    result.add_argument('--output', required=True, type=Path, help='New output .vrt path; existing artifacts are never overwritten')
    result.add_argument('--dry-run', action='store_true', help='Inspect metadata and print the plan without writing files')
    result.add_argument('--jpeg-quality', type=int, choices=range(1, 101), metavar='1..100', default=85)
    result.add_argument('--resampling', choices=('average', 'nearest', 'bilinear', 'cubic', 'lanczos'), default='average')
    result.add_argument('--levels', nargs='+', type=int, help='Increasing powers of two, e.g. 2 4 8 16')
    result.add_argument('--min-size', type=positive_int, default=256, help='Largest dimension of the smallest automatic overview (default 256)')
    result.add_argument('--threads', type=positive_int, default=min(4, os.cpu_count() or 1))
    result.add_argument('--cache-mb', type=positive_int, default=512, help='GDAL block cache in MiB, not a total RAM limit (default 512)')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    progress = Progress()
    saved_signals = {}

    def cancel(signum, frame):
        progress.cancelled = True

    for signum in (signal.SIGINT, getattr(signal, 'SIGBREAK', signal.SIGINT)):
        if signum not in saved_signals:
            saved_signals[signum] = signal.signal(signum, cancel)
    try:
        check_runtime()
        gdal.UseExceptions()
        run(args, progress)
        return 0
    except (Cancelled, KeyboardInterrupt) as error:
        print(f'Cancelled: {error}', file=sys.stderr)
        return 130
    except (BuildError, RuntimeError, OSError, ValueError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    finally:
        for signum, previous in saved_signals.items():
            signal.signal(signum, previous)


if __name__ == '__main__':
    sys.exit(main())
