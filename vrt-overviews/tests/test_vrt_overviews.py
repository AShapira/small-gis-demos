"""Generated-data contracts; no production rasters, network, or services."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
from osgeo import gdal, gdal_array, osr

SCRIPT = Path(__file__).resolve().parents[1] / 'build_vrt_overviews.py'
SPEC = importlib.util.spec_from_file_location('vrt_overviews', SCRIPT)
vrt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vrt)
gdal.UseExceptions()


class VRTTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('VRT_TEST_ROOT'):
            self.root = Path(os.environ['VRT_TEST_ROOT']) / self._testMethodName
            self.root.mkdir()
        else:
            temporary = tempfile.TemporaryDirectory(prefix='vrt-overviews-test-')
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)
        self.inputs = self.root / 'input tiles'
        self.inputs.mkdir()
        self.output = self.root / 'output' / 'mosaic.vrt'

    def fixture(self, name, values, x=0, y=0, nodata=None, mask=None, tuple_nodata=None,
                transform=None, epsg=32636, palette=False):
        values = np.asarray(values)
        if values.ndim == 2:
            values = values[None]
        path = self.inputs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with gdal.GetDriverByName('GTiff').Create(str(path), values.shape[2], values.shape[1],
                values.shape[0], gdal_array.NumericTypeCodeToGDALTypeCode(values.dtype),
                options=['TILED=YES', 'COMPRESS=DEFLATE']) as ds:
            ds.SetGeoTransform(transform or (100 + x * 2, 2, 0, 500 - y * 2, 0, -2))
            if epsg:
                srs = osr.SpatialReference()
                srs.ImportFromEPSG(epsg)
                ds.SetProjection(srs.ExportToWkt())
            if palette:
                table = gdal.ColorTable()
                table.SetColorEntry(0, (0, 0, 0, 255))
                ds.GetRasterBand(1).SetColorTable(table)
            ds.WriteArray(values)
            for number in range(values.shape[0]):
                if nodata is not None:
                    ds.GetRasterBand(number + 1).SetNoDataValue(nodata)
            if tuple_nodata:
                ds.SetMetadataItem('NODATA_VALUES', tuple_nodata)
            if mask is not None:
                with vrt.config({'GDAL_TIFF_INTERNAL_MASK': 'YES'}):
                    ds.CreateMaskBand(gdal.GMF_PER_DATASET)
                ds.GetRasterBand(1).GetMaskBand().WriteArray(np.asarray(mask, dtype='uint8') * 255)
        return path

    def args(self, *options, folders=None):
        return vrt.parser().parse_args([*(str(p) for p in (folders or [self.inputs])),
                                       '--output', str(self.output), *options])

    def build(self, *options, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return vrt.run(self.args(*options, **kwargs))

    def snapshot(self):
        return {str(p.relative_to(self.inputs)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in self.inputs.rglob('*') if p.is_file()}

    def assert_clean_failure(self):
        if self.output.parent.exists():
            self.assertEqual(list(self.output.parent.iterdir()), [])

    def assert_image(self, expected, valid):
        if expected.ndim == 2:
            expected = expected[None]
        if valid.ndim == 2:
            valid = np.broadcast_to(valid, expected.shape)
        with gdal.Open(str(self.output)) as ds:
            actual = ds.ReadAsArray().reshape(expected.shape)
            np.testing.assert_array_equal(actual[valid], expected[valid])
            for index in range(expected.shape[0]):
                band = ds.GetRasterBand(index + 1)
                np.testing.assert_array_equal(band.GetMaskBand().ReadAsArray() != 0, valid[index])
                self.assertIsNone(band.GetNoDataValue())

    def test_grayscale_average_keeps_valid_black_and_sources_unchanged(self):
        values = np.tile(np.array([[0, 0], [0, 200]], dtype='uint8'), (256, 256))
        self.fixture('image.tif', values)
        before = self.snapshot()
        result = self.build('--levels', '2', '4', '8')
        self.assert_image(values, np.ones(values.shape, bool))
        with gdal.Open(str(self.output)) as ds:
            for index in range(3):
                overview = ds.GetRasterBand(1).GetOverview(index)
                self.assertLessEqual(np.max(np.abs(overview.ReadAsArray().astype(int) - 50)), 2)
                self.assertTrue(np.all(overview.GetMaskBand().ReadAsArray() == 255))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(result['verification']['overview_levels_verified'], 3)
        self.assertFalse(result['absolute_source_references'])

    def test_rgb_masks_gaps_overlap_and_mask_pyramid(self):
        low = np.full((3, 512, 512), 90, dtype='uint8')
        low[:, 32:64, 32:64] = 0  # Valid black.
        low_valid = np.ones((512, 512), bool)
        low_valid[128:192, 128:192] = False
        self.fixture('a.tif', low, mask=low_valid)
        high = np.full((3, 512, 512), 180, dtype='uint8')
        high_valid = np.ones((512, 512), bool)
        high_valid[64:128, :64] = False
        self.fixture('b.tif', high, x=384, mask=high_valid)
        self.fixture('c.tif', np.full((3, 512, 512), 120, dtype='uint8'), x=1024)
        before = self.snapshot()
        result = self.build('--levels', '2', '4', '8')
        expected = np.zeros((3, 512, 1536), dtype='uint8')
        valid = np.zeros((512, 1536), bool)
        expected[:, :, :512] = low
        valid[:, :512] = low_valid
        region = expected[:, :, 384:896]
        region[:, high_valid] = high[:, high_valid]
        valid[:, 384:896] |= high_valid
        expected[:, :, 1024:] = 120
        valid[:, 1024:] = True
        self.assert_image(expected, valid)
        with gdal.Open(str(self.output)) as ds:
            for band_number in (1, 2, 3):
                for index, factor in enumerate((2, 4, 8)):
                    actual = ds.GetRasterBand(band_number).GetOverview(index).GetMaskBand().ReadAsArray()
                    # Boundaries align with all levels, so nearest sample phase is immaterial.
                    np.testing.assert_array_equal(actual != 0, valid[::factor, ::factor])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(result['grid']['bands'], 3)

    def test_rgb_tuple_nodata_does_not_discard_individual_zero_channels(self):
        values = np.zeros((3, 512, 512), dtype='uint8')
        values[1] = 80
        values[2] = 140
        values[:, 128:256, 128:256] = 0
        self.fixture('tuple.tif', values, tuple_nodata='0 0 0')
        valid = np.any(values != 0, axis=0)
        self.build('--levels', '2', '4')
        self.assert_image(values, valid)
        with gdal.Open(str(self.output)) as ds:
            for number in (1, 2, 3):
                np.testing.assert_array_equal(ds.GetRasterBand(number).GetOverview(0).GetMaskBand().ReadAsArray() != 0,
                                              valid[::2, ::2])
            pixel = ds.ReadAsArray(0, 0, 1, 1, buf_xsize=1, buf_ysize=1).reshape(3)
            np.testing.assert_array_equal(pixel, [0, 80, 140])

    def test_independent_band_nodata_masks_and_overlap(self):
        base = np.full((3, 512, 512), 50, dtype='uint8')
        base[0, 32:64, 32:64] = 0
        self.fixture('a.tif', base, nodata=0)
        overlay = np.full_like(base, 170)
        overlay[0, :256] = 0
        overlay[1, 256:] = 0
        overlay[2] = 0
        self.fixture('b.tif', overlay, nodata=0)
        expected = np.where(overlay != 0, overlay, base)
        valid = (base != 0) | (overlay != 0)
        self.build('--levels', '2', '4')
        self.assert_image(expected, valid)
        with gdal.Open(str(self.output)) as ds:
            for number in (1, 2, 3):
                overview = ds.GetRasterBand(number).GetOverview(0)
                np.testing.assert_array_equal(overview.GetMaskBand().ReadAsArray() != 0,
                                              valid[number - 1, ::2, ::2])
                # Well inside constant valid regions, JPEG must preserve the band's own value.
                for row in (100, 200):
                    sample = int(overview.ReadAsArray(200, row, 1, 1)[0, 0])
                    self.assertLessEqual(abs(sample - int(expected[number - 1, row * 2, 400])), 3)

    def test_recursive_dedup_unicode_and_folder_priority(self):
        path = self.fixture('nested/אריח A.TIFF', np.full((512, 512), 70, dtype='uint8'))
        second = self.root / 'earlier alphabetically'
        second.mkdir()
        shutil.copyfile(path, second / 'a.geotiff')
        with gdal.Open(str(second / 'a.geotiff'), gdal.GA_Update) as ds:
            ds.GetRasterBand(1).Fill(160)
        report = self.build('--levels', '2', folders=[self.inputs, path.parent, second])
        self.assertEqual(report['source_count'], 2)
        self.assert_image(np.full((512, 512), 160, dtype='uint8'), np.ones((512, 512), bool))
        manifest = Path(str(self.output) + '.sources.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(manifest, [str(path.resolve()), str((second / 'a.geotiff').resolve())])

    def test_odd_dimensions_and_explicit_starting_level(self):
        self.fixture('odd.tif', np.full((259, 513), 113, dtype='uint8'))
        report = self.build('--levels', '4', '16', '32')
        self.assertEqual(report['levels'], [4, 16, 32])
        with gdal.Open(str(self.output)) as ds:
            self.assertEqual([(ds.GetRasterBand(1).GetOverview(i).XSize,
                               ds.GetRasterBand(1).GetOverview(i).YSize) for i in range(3)],
                             [(129, 65), (33, 17), (17, 9)])

    def test_small_mosaic_creates_vrt_without_useless_pyramids(self):
        self.fixture('small.tif', np.zeros((17, 31), dtype='uint8'))
        report = self.build()
        self.assertEqual(report['levels'], [])
        self.assertFalse(Path(str(self.output) + '.ovr').exists())
        self.assert_image(np.zeros((17, 31), dtype='uint8'), np.ones((17, 31), bool))

    def test_dry_run_has_no_filesystem_changes(self):
        self.fixture('a.tif', np.full((512, 512), 10, dtype='uint8'))
        before = self.snapshot()
        report = self.build('--dry-run')
        self.assertEqual(report['levels'], [2])
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.output.parent.exists())

    def test_existing_artifact_or_lock_is_never_overwritten(self):
        self.fixture('a.tif', np.ones((32, 32), dtype='uint8'))
        self.output.parent.mkdir()
        for suffix in (*vrt.ARTIFACT_SUFFIXES, '.lock', '.aux.xml', '.ovr.msk.aux.xml'):
            with self.subTest(suffix=suffix):
                existing = Path(str(self.output) + suffix)
                existing.write_bytes(b'keep me')
                with self.assertRaises(vrt.BuildError):
                    self.build()
                self.assertEqual(existing.read_bytes(), b'keep me')
                existing.unlink()

    def test_incompatible_and_unreadable_inputs_fail_before_output(self):
        self.fixture('a.tif', np.ones((32, 32), dtype='uint8'))
        cases = [dict(values=np.ones((32, 32), dtype='uint16')),
                 dict(values=np.ones((3, 32, 32), dtype='uint8')),
                 dict(values=np.ones((4, 32, 32), dtype='uint8')),
                 dict(values=np.ones((32, 32), dtype='uint8'), epsg=4326),
                 dict(values=np.ones((32, 32), dtype='uint8'), epsg=None),
                 dict(values=np.ones((32, 32), dtype='uint8'), transform=(101, 2, 0, 500, 0, -2)),
                 dict(values=np.ones((32, 32), dtype='uint8'), transform=(100, 3, 0, 500, 0, -3)),
                 dict(values=np.ones((32, 32), dtype='uint8'), transform=(100, 2, 0.1, 500, 0, -2)),
                 dict(values=np.ones((32, 32), dtype='uint8'), palette=True)]
        for options in cases:
            with self.subTest(options=str(options.keys())):
                file = self.fixture('bad.tif', **options)
                with self.assertRaisesRegex(vrt.BuildError, 'bad.tif'):
                    self.build()
                file.unlink()
                self.assertFalse(self.output.parent.exists())
        (self.inputs / 'corrupt.tif').write_bytes(b'not a TIFF')
        with self.assertRaisesRegex(vrt.BuildError, 'corrupt.tif'):
            self.build()

    def test_invalid_levels_and_empty_input(self):
        with self.assertRaises(vrt.BuildError):
            self.build()
        self.fixture('a.tif', np.ones((17, 31), dtype='uint8'))
        for levels in (['3'], ['4', '2'], ['2', '2'], ['32', '64']):
            with self.subTest(levels=levels), self.assertRaises(vrt.BuildError):
                self.build('--levels', *levels)
        self.assertFalse(self.output.parent.exists())

    def test_cancellation_cleans_owned_files_and_releases_lock(self):
        self.fixture('a.tif', np.ones((1024, 1024), dtype='uint8'))

        class CancelAtOverview(vrt.Progress):
            def callback(self, fraction, message='', unused=None):
                if self.phase == 'Building JPEG overviews':
                    self.cancelled = True
                    return 0
                return 1

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(vrt.Cancelled):
            vrt.run(self.args(), CancelAtOverview())
        self.assert_clean_failure()
        self.build()

    def test_failed_validation_never_publishes_output(self):
        self.fixture('a.tif', np.ones((512, 512), dtype='uint8'))
        before = self.snapshot()
        with patch.object(vrt, 'verify', side_effect=vrt.BuildError('injected decode failure')):
            with self.assertRaises(vrt.BuildError):
                self.build()
        self.assert_clean_failure()
        self.assertEqual(before, self.snapshot())

    def test_cancellation_during_masks_releases_windows_file_handles(self):
        self.fixture('a.tif', np.ones((3, 1024, 1024), dtype='uint8'))
        for phase in ('Building lossless overview masks', 'Building lower mask levels'):
            class CancelDuringMasks(vrt.Progress):
                def callback(self, fraction, message='', unused=None):
                    if self.phase == phase:
                        self.cancelled = True
                        return 0
                    return 1

            with self.subTest(phase=phase), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(vrt.Cancelled):
                vrt.run(self.args('--levels', '2', '4', '8'), CancelDuringMasks())
            self.assert_clean_failure()

    def test_failed_publication_rolls_back_only_owned_outputs(self):
        self.fixture('a.tif', np.ones((512, 512), dtype='uint8'))
        real_replace = os.replace

        def fail_on_manifest(src, dst):
            if str(dst).endswith('.sources.txt'):
                raise OSError('injected move failure')
            real_replace(src, dst)

        with patch.object(vrt.os, 'replace', side_effect=fail_on_manifest):
            with self.assertRaisesRegex(OSError, 'injected move failure'):
                self.build()
        self.assert_clean_failure()

    def test_source_change_during_build_prevents_publication(self):
        self.fixture('a.tif', np.ones((512, 512), dtype='uint8'))
        real_verify = vrt.verify

        def change_after_verify(*args):
            result = real_verify(*args)
            source = self.inputs / 'a.tif'
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            return result

        with patch.object(vrt, 'verify', side_effect=change_after_verify):
            with self.assertRaisesRegex(vrt.BuildError, 'Source changed'):
                self.build()
        self.assert_clean_failure()

    def test_relative_sources_survive_relocation(self):
        self.fixture('nested/a.tif', np.full((512, 512), 75, dtype='uint8'))
        self.build()
        nodes = ET.parse(self.output).findall('.//SourceFilename')
        self.assertTrue(all(node.get('relativeToVRT') == '1' for node in nodes))
        moved = self.root / 'relocated'
        moved.mkdir()
        shutil.move(str(self.inputs), moved / self.inputs.name)
        shutil.move(str(self.output.parent), moved / self.output.parent.name)
        self.output = moved / 'output' / 'mosaic.vrt'
        self.assert_image(np.full((512, 512), 75, dtype='uint8'), np.ones((512, 512), bool))

    def test_two_thousand_sources_with_bounded_dataset_pool(self):
        for index in range(2000):
            self.fixture(f'batch{index // 100:02d}/tile{index:04d}.tif',
                         np.full((8, 8), index % 200 + 20, dtype='uint8'),
                         x=(index % 50) * 8, y=(index // 50) * 8)
        report = self.build('--threads', '2', '--cache-mb', '32')
        self.assertEqual(report['source_count'], 2000)
        self.assertEqual((report['grid']['width'], report['grid']['height']), (400, 320))
        with gdal.Open(str(self.output)) as ds:
            for index in (0, 127, 1024, 1999):
                value = ds.ReadAsArray((index % 50) * 8, (index // 50) * 8, 1, 1)[0, 0]
                self.assertEqual(value, index % 200 + 20)

    def test_native_cli_success_dry_run_and_error_exit_codes(self):
        self.fixture('a.tif', np.full((512, 512), 80, dtype='uint8'))
        command = [sys.executable, '-B', str(SCRIPT), str(self.inputs), '--output', str(self.output)]
        for suffix, expected in ((['--dry-run'], 0), ([], 0), ([], 1)):
            result = subprocess.run(command + suffix, capture_output=True, text=True, encoding='utf-8', timeout=120)
            (self.root / f'cli-{len(list(self.root.glob("cli-*.txt")))}.txt').write_text(
                result.stdout + result.stderr, encoding='utf-8')
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        report = json.loads(Path(str(self.output) + '.report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['verification']['structure'], 'passed')

    @unittest.skipUnless(os.name == 'nt', 'Native Windows control-break cancellation')
    def test_native_cli_ctrl_break_cancellation(self):
        self.fixture('a.tif', np.full((3, 4096, 4096), 100, dtype='uint8'))
        command = [sys.executable, '-B', '-u', str(SCRIPT), str(self.inputs), '--output', str(self.output)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        lines = []
        try:
            for line in process.stdout:
                lines.append(line)
                if 'Building JPEG overviews ...' in line:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    break
            remaining, _ = process.communicate(timeout=120)
            lines.append(remaining)
            self.assertEqual(process.returncode, 130, ''.join(lines))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            (self.root / 'cancellation.txt').write_text(''.join(lines), encoding='utf-8')
        self.assert_clean_failure()


if __name__ == '__main__':
    unittest.main()
