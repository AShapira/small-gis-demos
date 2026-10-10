"""Generated-data contract tests; no user rasters or external services."""

import contextlib
import argparse
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, gdal_array, osr

SCRIPT = Path(__file__).resolve().parents[1] / "merge_geotiffs.py"
SPEC = importlib.util.spec_from_file_location("merge_geotiffs", SCRIPT)
merge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merge
SPEC.loader.exec_module(merge)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "inputs"
        self.source.mkdir()
        self.output = self.root / "outputs"
        self.transform = (100, 2, 0, 200, 0, -2)
        gdal.UseExceptions()
        gdal.SetConfigOption("GDAL_TIFF_INTERNAL_MASK", "YES")

    def make(self, name, values, x=0, y=0, nodata=None, mask=None, **extra):
        data = np.asarray(values)
        if data.ndim == 2:
            data = data[None]
        path = self.source / name
        transform = extra.get('transform', (100 + x * 2, 2, 0, 200 - y * 2, 0, -2))
        crs = osr.SpatialReference()
        crs.SetFromUserInput(extra.get('crs', 'EPSG:32636'))
        with gdal.GetDriverByName('GTiff').Create(str(path), data.shape[2], data.shape[1], data.shape[0],
                gdal_array.NumericTypeCodeToGDALTypeCode(data.dtype)) as ds:
            ds.SetGeoTransform(transform)
            ds.SetProjection(crs.ExportToWkt())
            ds.WriteArray(data)
            for i in range(data.shape[0]):
                if nodata is not None:
                    ds.GetRasterBand(i + 1).SetNoDataValue(float(nodata))
            if mask is not None:
                ds.CreateMaskBand(gdal.GMF_PER_DATASET)
                ds.GetRasterBand(1).GetMaskBand().WriteArray(np.asarray(mask, dtype='uint8') * 255)
        return path

    def args(self, *options):
        return merge.parser().parse_args([str(self.source), str(self.output), "--max-size", "1MiB", *options])

    def run_merge(self, *options):
        with contextlib.redirect_stderr(io.StringIO()):
            return merge.run(self.args(*options))

    def assert_mosaic(self, expected, valid, origin=(0, 0)):
        """Reconstruct spatial output independently from TIFF georeferencing."""
        if expected.ndim == 2:
            expected, valid = expected[None], valid[None]
        actual = np.zeros_like(expected)
        actual_valid = np.zeros(expected.shape, dtype=bool)
        occupied = np.zeros(expected.shape[1:], dtype=bool)
        for path in self.output.glob("*.tif"):
            with gdal.Open(str(path)) as ds:
                transform = ds.GetGeoTransform()
                col, row = (transform[0] - 100) / 2, (transform[3] - 200) / -2
                x, y = round(col) - origin[0], round(row) - origin[1]
                sl = (slice(None), slice(y, y + ds.RasterYSize), slice(x, x + ds.RasterXSize))
                self.assertFalse(occupied[sl[1:]].any())
                occupied[sl[1:]] = True
                actual[sl] = ds.ReadAsArray()
                actual_valid[sl] = np.stack([ds.GetRasterBand(i).GetMaskBand().ReadAsArray() != 0
                                             for i in range(1, ds.RasterCount + 1)])
        np.testing.assert_array_equal(actual_valid, valid)
        # Byte comparison also catches float signed-zero / NaN payload changes.
        self.assertEqual(actual[valid].tobytes(), expected[valid].tobytes())

    def test_cog_is_opt_in(self):
        self.assertFalse(self.args().cog)
        self.make('a.tif', np.ones((600, 600), dtype='uint8'))
        with patch.object(merge, 'cog_validator', side_effect=AssertionError('COG dependency used')):
            report = self.run_merge('--cpus', '1')
        self.assertFalse(report['cog'])
        self.assertNotIn('cog_validation', report['outputs'][0])
        with gdal.Open(str(self.output / report['outputs'][0]['name'])) as ds:
            self.assertEqual(ds.GetRasterBand(1).GetOverviewCount(), 0)

    def test_cog_codecs_preserve_samples_masks_and_metadata(self):
        values = np.random.default_rng(42).integers(0, 65535, (600, 600), dtype='uint16')
        valid = np.ones(values.shape, dtype=bool)
        valid[20:50, 20:50] = False
        path = self.make('a.tif', values, mask=valid)
        with gdal.Open(str(path), gdal.GA_Update) as ds:
            band = ds.GetRasterBand(1)
            band.SetScale(.25)
            band.SetOffset(10)
            band.SetUnitType('m')
            band.SetDescription('height')
        for codec in ('none', 'lzw', 'deflate', 'zstd', 'lzma'):
            with self.subTest(codec=codec):
                self.output = self.root / codec
                options = ['--cog', '--compression', codec, '--cpus', '2', '--ram', '1GiB']
                if codec == 'lzma':
                    options += ['--compression-level', '0']
                report = self.run_merge(*options)
                self.assertTrue(report['cog'])
                self.assert_mosaic(values, valid)
                for item in report['outputs']:
                    check = item['cog_validation']
                    self.assertEqual(check['layout'], 'COG')
                    self.assertTrue(check['full_check'])
                    self.assertGreater(check['overview_count'], 0)
                    self.assertFalse(check['warnings'])
                    with gdal.Open(str(self.output / item['name'])) as ds:
                        self.assertEqual(ds.RasterCount, 1)  # No physical alpha band.
                        band = ds.GetRasterBand(1)
                        self.assertEqual(band.GetMaskFlags(), gdal.GMF_PER_DATASET)
                        self.assertEqual((band.GetScale(), band.GetOffset(), band.GetUnitType(), band.GetDescription()),
                                         (.25, 10, 'm', 'height'))

    def test_cog_overview_resampling_and_none(self):
        values = np.tile(np.array([[1, 1], [1, 9]], dtype='uint8'), (256, 256))
        self.make('a.tif', values, nodata=0)
        for method, expected in [('nearest', 1), ('average', 3), ('mode', 1), ('none', None)]:
            with self.subTest(method=method):
                self.output = self.root / method
                report = self.run_merge('--cog', '--cog-overviews', method, '--cpus', '1')
                self.assert_mosaic(values, np.ones(values.shape, dtype=bool))
                with gdal.Open(str(self.output / report['outputs'][0]['name'])) as ds:
                    band = ds.GetRasterBand(1)
                    self.assertEqual(band.GetMaskFlags(), gdal.GMF_NODATA)
                    if expected is None:
                        self.assertEqual(band.GetOverviewCount(), 0)
                    else:
                        np.testing.assert_array_equal(band.GetOverview(0).ReadAsArray(),
                                                      np.full((256, 256), expected, dtype='uint8'))

    def test_cog_float_payload_bits_and_predictors(self):
        values = np.array([[0x80000000, 0, 0x7FC01234, 0x7F800000, 0xFF800000, 1]], dtype='uint32').view('float32')
        self.make('a.tif', values)
        for predictor in (1, 2, 3):
            self.output = self.root / f'float-{predictor}'
            self.run_merge('--cog', '--predictor', str(predictor), '--cpus', '1')
            self.assert_mosaic(values, np.ones(values.shape, dtype=bool))

    def test_cog_rgb_tuple_nodata(self):
        values = np.array([[[0, 0, 8]], [[0, 2, 9]], [[0, 3, 7]]], dtype='uint8')
        path = self.make('a.tif', values)
        with gdal.Open(str(path), gdal.GA_Update) as ds:
            ds.SetMetadataItem('NODATA_VALUES', '0 0 0')
        report = self.run_merge('--cog', '--cpus', '1')
        valid = np.broadcast_to(np.array([[False, True, True]]), values.shape)
        self.assert_mosaic(values, valid)
        with gdal.Open(str(self.output / report['outputs'][0]['name'])) as ds:
            self.assertEqual(ds.RasterCount, 3)
            self.assertEqual(ds.GetMetadataItem('NODATA_VALUES'), '0 0 0')

    def test_cog_async_api_nodata_and_single_file(self):
        values = np.ones((600, 600), dtype='uint16')
        valid = np.ones(values.shape, dtype=bool)
        valid[10:20, 10:20] = False
        self.make('a.tif', values, mask=valid)
        job = merge.start(self.source, self.output, cog=True, cog_overviews='none',
                          output_nodata='65535', compression='zstd', compression_level=3,
                          predictor=2, cpus=2, ram='1GiB')
        job.process.wait(timeout=30)
        report = job.result()
        self.assertTrue(report['cog'])
        self.assertEqual(report['cog_settings']['overviews'], 'none')
        self.assertEqual(len(report['outputs']), 1)
        self.assertIsNone(report['target_file_bytes'])
        self.assertEqual(report['outputs'][0]['cog_validation']['overview_count'], 0)
        self.assertTrue(report['outputs'][0]['cog_validation']['warnings'])
        self.assert_mosaic(values, valid)
        with gdal.Open(str(self.output / report['outputs'][0]['name'])) as ds:
            self.assertEqual(ds.GetRasterBand(1).GetMaskFlags(), gdal.GMF_NODATA)

    def test_cog_cap_includes_overviews_and_retries(self):
        values = np.random.default_rng(1).integers(1, 255, (512, 512), dtype='uint8')
        self.make('a.tif', values, nodata=0)
        converted_sizes = []
        original = merge.convert_cog
        def record(source, destination, *args):
            original(source, destination, *args)
            converted_sizes.append(destination.stat().st_size)
        with patch.object(merge, 'plan', return_value=iter([merge.Rect(0, 0, 512, 512)])), \
                patch.object(merge, 'convert_cog', side_effect=record):
            report = self.run_merge('--cog', '--compression', 'none', '--target-size', '260KiB', '--cpus', '1')
        self.assertGreater(converted_sizes[0], report['max_file_bytes'])
        self.assertGreater(len(report['outputs']), 1)
        self.assertTrue(all(o['bytes'] <= report['max_file_bytes'] for o in report['outputs']))
        self.assert_mosaic(values, np.ones(values.shape, dtype=bool))
        self.assertFalse((self.output / '.incomplete').exists())

    def test_cog_dry_run_and_unsupported_packbits(self):
        self.make('a.tif', np.ones((2, 2), dtype='uint8'))
        report = self.run_merge('--cog', '--dry-run')
        self.assertEqual(report['status'], 'analyzed')
        self.assertEqual(report['cog_settings']['overviews'], 'nearest')
        self.assertFalse(list(self.output.rglob('*.tif')))
        self.output = self.root / 'unsupported'
        with self.assertRaisesRegex(merge.MergeError, 'COG driver does not support packbits'):
            self.run_merge('--cog', '--compression', 'packbits')
        self.assertFalse(list(self.output.rglob('*.tif')))

    def test_cog_validation_errors_prevent_publication(self):
        self.make('a.tif', np.ones((2, 2), dtype='uint8'))
        with patch.object(merge, 'cog_validator') as factory:
            factory.return_value.return_value = ([], ['invalid tile ordering'], {})
            with self.assertRaisesRegex(merge.MergeError, 'COG layout validation failed'):
                self.run_merge('--cog', '--cpus', '1')
            self.assertTrue(factory.return_value.call_args.kwargs['full_check'])
        self.assertFalse(list(self.output.glob('*.tif')))

    def test_cog_final_samples_checked_against_originals(self):
        self.make('a.tif', np.ones((2, 2), dtype='uint8'))
        original = merge.convert_cog
        def corrupt(source, *args):
            with gdal.Open(str(source), gdal.GA_Update) as ds:
                ds.GetRasterBand(1).WriteArray(np.array([[2]], dtype='uint8'))
            return original(source, *args)
        with patch.object(merge, 'convert_cog', side_effect=corrupt):
            with self.assertRaises(merge.MergeError):
                self.run_merge('--cog', '--cpus', '1')
        self.assertFalse(list(self.output.glob('*.tif')))

    def test_cog_cancellation_callback(self):
        path = self.make('a.tif', np.ones((2, 2), dtype='uint8'))
        cancel_file = self.root / 'cancel'
        def cancelled_copy(destination, src, **kwargs):
            cancel_file.write_text('cancel')
            self.assertEqual(kwargs['callback'](.5, '', None), 0)
            raise RuntimeError('User terminated')
        with patch.object(merge, '_cancel_file', cancel_file), patch.object(merge.gdal, 'GetDriverByName') as driver:
            driver.return_value.CreateCopy.side_effect = cancelled_copy
            with self.assertRaises(merge.Cancelled):
                merge.convert_cog(path, self.root / 'cancelled.tif', {'creation_options': []}, 1, 4)

    def test_all_lossless_codecs_preserve_integer_samples_masks_and_limits(self):
        values = np.random.default_rng(3).integers(0, 65535, (512, 512), dtype='uint16')
        valid = np.ones(values.shape, dtype=bool)
        valid[10:20, 10:20] = False
        self.make('a.tif', values, mask=valid)
        for codec in merge.LOSSLESS_CODECS:
            with self.subTest(codec=codec):
                self.output = self.root / codec
                report = self.run_merge('--compression', codec, '--target-size', '200KB', '--cpus', '2', '--ram', '1GiB')
                self.assert_mosaic(values, valid)
                self.assertTrue(all(o['bytes'] <= 220000 for o in report['outputs']))
                for item in report['outputs']:
                    with gdal.Open(str(self.output / item['name'])) as ds:
                        self.assertEqual(ds.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE') or 'NONE', codec.upper())
                self.assertEqual(report['compression_settings']['codec'], codec)

    def test_codecs_and_predictors_preserve_float_payload_bits(self):
        bits = np.array([[0x80000000, 0, 0x7FC01234, 0x7F800000, 0xFF800000, 1, 0x3F800001]], dtype='uint32')
        values = bits.view('float32')
        self.make('a.tif', values)
        for codec in merge.LOSSLESS_CODECS:
            for predictor in ((1, 2, 3) if codec in ('deflate', 'lzw', 'zstd') else (1,)):
                with self.subTest(codec=codec, predictor=predictor):
                    self.output = self.root / f'{codec}-p{predictor}'
                    self.run_merge('--compression', codec, '--predictor', str(predictor), '--cpus', '1', '--ram', '1GiB')
                    self.assert_mosaic(values, np.ones(values.shape, dtype=bool))

    def test_codec_levels_and_integer_predictor(self):
        self.make('a.tif', np.arange(100, dtype='int16').reshape(10, 10))
        for codec, level, predictor in [('deflate', 9, 2), ('zstd', 3, 2), ('lzma', 0, 1), ('lzw', None, 2)]:
            with self.subTest(codec=codec):
                self.output = self.root / codec
                options = ['--compression', codec, '--predictor', str(predictor), '--cpus', '1', '--ram', '1GiB']
                if level is not None:
                    options += ['--compression-level', str(level)]
                report = self.run_merge(*options)
                self.assertEqual(report['compression_settings']['level'], level)
                self.assertEqual(report['compression_settings']['predictor'], predictor)
                self.assert_mosaic(np.arange(100, dtype='int16').reshape(10, 10), np.ones((10, 10), dtype=bool))

    def test_invalid_compression_settings_fail_before_outputs(self):
        self.make('a.tif', np.ones((1, 1), dtype='uint8'))
        cases = [('deflate', '0', '1'), ('zstd', '23', '1'), ('lzma', '10', '1'),
                 ('none', '3', '1'), ('packbits', None, '2'), ('lzw', None, '3')]
        for i, (codec, level, predictor) in enumerate(cases):
            self.output = self.root / f'invalid-{i}'
            options = ['--compression', codec, '--predictor', predictor]
            if level is not None:
                options += ['--compression-level', level]
            with self.subTest(codec=codec, level=level, predictor=predictor):
                with self.assertRaises(merge.MergeError):
                    self.run_merge(*options)
                self.assertFalse(list(self.output.rglob('*.tif')))

    def test_missing_codec_fails_without_fallback(self):
        self.make('a.tif', np.ones((1, 1), dtype='uint8'))
        class MissingCodec:
            def GetMetadataItem(self, key):
                return '<CreationOptionList><Option name="COMPRESS"><Value>NONE</Value></Option></CreationOptionList>'
        with patch.object(merge.gdal, 'GetDriverByName', return_value=MissingCodec()):
            with self.assertRaisesRegex(merge.MergeError, 'does not support zstd'):
                self.run_merge('--compression', 'zstd')
        self.assertFalse(list(self.output.rglob('*.tif')))

    def test_verifier_rejects_wrong_actual_compression(self):
        self.make('a.tif', np.ones((2, 2), dtype='uint8'))
        original = merge.create_candidate
        def wrong_codec(path, region, sources, template, *args):
            changed = dict(template, compression_settings=dict(template['compression_settings'],
                           creation_options=['COMPRESS=NONE']))
            return original(path, region, sources, changed, *args)
        with patch.object(merge, 'create_candidate', side_effect=wrong_codec):
            with self.assertRaisesRegex(merge.MergeError, 'GDAL wrote NONE'):
                self.run_merge('--compression', 'deflate')
        self.assertFalse(list(self.output.glob('*.tif')))

    def test_codec_memory_reserves_are_included_in_budget(self):
        self.make('a.tif', np.ones((1, 1), dtype='uint8'))
        sources, template = merge.inventory(self.source)
        for codec, level in [('lzma', 9), ('zstd', 22)]:
            with self.subTest(codec=codec):
                args = self.args('--compression', codec, '--compression-level', str(level), '--ram', '2GiB', '--cpus', '8')
                with patch.object(merge, 'available_resources', return_value=(32, 16 * 1024**3)):
                    plan = merge.resource_plan(args, template, sources, 10)
                self.assertGreater(plan['estimated_codec_thread_bytes'], 4 * 1024**2)
                self.assertLessEqual(plan['workers'] * plan['estimated_worker_bytes'] +
                    plan['gdal_cache_bytes'] + plan['estimated_codec_pool_bytes'] + 128 * 1024**2,
                    plan['ram_budget_bytes'])

    def test_async_compression_options(self):
        values = np.array([[0, 1, 77]], dtype='uint16')
        self.make('a.tif', values, mask=[[1, 1, 0]])
        job = merge.start(self.source, self.output, compression='ZSTD', compression_level=3,
                          predictor=2, output_nodata='65535', cpus=1, ram='1GiB')
        job.process.wait(timeout=30)
        report = job.result()
        self.assertEqual(report['compression_settings']['codec'], 'zstd')
        self.assertEqual(report['compression_settings']['level'], 3)
        self.assert_mosaic(values, np.array([[1, 1, 0]], dtype=bool))

    def test_dry_run_suggests_nodata_without_writing_tiffs(self):
        self.make('a.tif', np.array([[0, 1, 99]], dtype='uint16'), mask=[[1, 1, 0]])
        proc = subprocess.run([sys.executable, str(SCRIPT), str(self.source), str(self.output),
                               '--dry-run', '--suggest-nodata'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads((self.output / 'report.json').read_text())
        analysis = report['nodata_analysis']
        self.assertEqual(report['status'], 'analyzed')
        self.assertTrue(analysis['default_output_uses_internal_mask'])
        self.assertTrue(analysis['replacement_possible'])
        self.assertNotIn('0', analysis['suggestions'])
        self.assertIn('65535', analysis['suggestions'])
        self.assertEqual(analysis['invalid_source_band_samples'], 1)
        self.assertFalse(list(self.output.rglob('*.tif')))
        self.assertIn('--output-nodata=', proc.stderr)

    def test_full_byte_domain_has_no_unused_nodata(self):
        self.make('full.tif', np.arange(256, dtype='uint8').reshape(16, 16))
        report = self.run_merge('--dry-run', '--suggest-nodata')
        self.assertFalse(report['nodata_analysis']['replacement_possible'])
        self.assertEqual(report['nodata_analysis']['suggestions'], [])

    def test_nodata_search_finds_internal_integer_hole(self):
        for dtype, low, high, absent in [('uint8', 0, 255, 123), ('uint16', 0, 65535, 12345)]:
            with self.subTest(dtype=dtype):
                self.make('full.tif', np.delete(np.arange(low, high + 1, dtype=dtype), absent)[None])
                self.output = self.root / dtype
                report = self.run_merge('--dry-run', '--suggest-nodata')
                self.assertEqual(report['nodata_analysis']['suggestions'], [str(absent)])

    def test_nodata_search_int32_and_float_radix_fallback(self):
        for dtype, values in [('int32', [0, -2147483648, 2147483647, -9999, -1]),
                              ('float32', [np.nan, np.inf, -np.inf, 0, -9999, -1,
                                           np.finfo('float32').min, np.finfo('float32').max]),
                              ('float64', [np.nan, np.inf, -np.inf, 0, -9999, -1,
                                           np.finfo('float64').min, np.finfo('float64').max])]:
            with self.subTest(dtype=dtype):
                self.make('a.tif', np.array([values], dtype=dtype))
                self.output = self.root / (dtype + '-analysis')
                report = self.run_merge('--dry-run', '--suggest-nodata')
                suggested = report['nodata_analysis']['suggestions'][0]
                self.output = self.root / (dtype + '-merged')
                self.run_merge('--output-nodata=' + suggested)
                self.assert_mosaic(np.array([values], dtype=dtype), np.ones((1, len(values)), dtype=bool))

    def test_output_nodata_replaces_mask_and_fills_gaps(self):
        a = self.make('a.tif', np.array([[0, 77, 2]], dtype='int16'), mask=[[1, 0, 1]])
        b = self.make('b.tif', np.array([[3, 4]], dtype='int16'), x=4)
        before = [merge.sha256(p) for p in (a, b)]
        report = self.run_merge('--output-nodata=-9999')
        expected = np.array([[0, -9999, 2, -9999, 3, 4]], dtype='int16')
        self.assert_mosaic(expected, expected != -9999)
        with gdal.Open(str(self.output / report['outputs'][0]['name'])) as ds:
            self.assertEqual(ds.RasterCount, 1)
            self.assertEqual(ds.GetRasterBand(1).GetNoDataValue(), -9999)
            self.assertEqual(ds.GetRasterBand(1).GetMaskFlags(), gdal.GMF_NODATA)
            np.testing.assert_array_equal(ds.ReadAsArray(), expected)
        self.assertEqual(before, [merge.sha256(p) for p in (a, b)])
        self.assertFalse(list(self.output.glob('*.msk')))
        self.assertTrue(report['all_source_valid_values_preserved'])

    def test_output_nodata_collision_rejected_even_with_priority(self):
        self.make('a.tif', np.array([[0]], dtype='uint8'))
        self.make('b.tif', np.array([[1]], dtype='uint8'))
        with self.assertRaisesRegex(merge.MergeError, 'collides'):
            self.run_merge('--output-nodata=0', '--overlap', 'last')
        self.assertFalse(list(self.output.rglob('*.tif')))
        report = json.loads((self.output / 'report.json').read_text())
        self.assertFalse(report['nodata_analysis']['requested_is_safe'])

    def test_output_nodata_rechecks_changed_inputs_after_suggestion(self):
        self.make('a.tif', np.array([[0, 1]], dtype='uint8'))
        report = self.run_merge('--dry-run', '--suggest-nodata')
        suggested = report['nodata_analysis']['suggestions'][0]
        self.make('a.tif', np.array([[0, int(suggested)]], dtype='uint8'))
        self.output = self.root / 'after-analysis'
        with self.assertRaisesRegex(merge.MergeError, 'collides'):
            self.run_merge('--output-nodata=' + suggested)

    def test_output_nodata_rejects_unrepresentable_values(self):
        for dtype, value in [('uint8', '-1'), ('uint8', '256'), ('int16', '1.5'),
                             ('int16', 'nan'), ('float32', '0.1'), ('float32', '1e100')]:
            with self.subTest(dtype=dtype, value=value):
                with self.assertRaisesRegex(merge.MergeError, 'representable'):
                    merge.parse_output_nodata(value, dtype)

    def test_float_nan_nodata_preserves_signed_zeros_and_masks(self):
        values = np.array([[0x80000000, 0, 0x3F800001, 0x7FC01234]], dtype='uint32').view('float32')
        self.make('a.tif', values, mask=[[1, 1, 1, 0]])
        report = self.run_merge('--output-nodata=nan')
        self.assert_mosaic(values, np.array([[1, 1, 1, 0]], dtype=bool))
        with gdal.Open(str(self.output / report['outputs'][0]['name'])) as ds:
            self.assertTrue(np.isnan(ds.GetRasterBand(1).GetNoDataValue()))
            self.assertEqual(ds.GetRasterBand(1).GetMaskFlags(), gdal.GMF_NODATA)

    def test_valid_nan_and_negative_zero_collisions(self):
        self.make('a.tif', np.array([[np.nan, -0.0]], dtype='float32'))
        for value in ('nan', '0', '-0'):
            self.output = self.root / ('collision-' + value)
            with self.assertRaisesRegex(merge.MergeError, 'collides'):
                self.run_merge('--output-nodata=' + value)

    def test_output_nodata_changes_existing_scalar_convention(self):
        self.make('a.tif', np.array([[-99, 0, 2]], dtype='int16'), nodata=-99)
        self.run_merge('--output-nodata=-9999')
        self.assert_mosaic(np.array([[-9999, 0, 2]], dtype='int16'), np.array([[0, 1, 1]], dtype=bool))

    def test_output_nodata_preserves_valid_old_nodata_under_explicit_mask(self):
        self.make('a.tif', np.array([[0, 9]], dtype='uint8'), nodata=0, mask=[[1, 0]])
        self.run_merge('--output-nodata=255')
        self.assert_mosaic(np.array([[0, 255]], dtype='uint8'), np.array([[1, 0]], dtype=bool))

    def test_output_nodata_verifier_rejects_stored_mask(self):
        self.make('a.tif', np.array([[1, 2]], dtype='uint8'))
        original = merge.create_candidate
        def add_mask(path, *args, **kwargs):
            original(path, *args, **kwargs)
            with gdal.Open(str(path), gdal.GA_Update) as ds:
                ds.CreateMaskBand(gdal.GMF_PER_DATASET)
                ds.GetRasterBand(1).GetMaskBand().WriteArray(np.full((1, 2), 255, dtype='uint8'))
        with patch.object(merge, 'create_candidate', side_effect=add_mask):
            with self.assertRaisesRegex(merge.MergeError, 'without a stored mask'):
                self.run_merge('--output-nodata=255')
        self.assertFalse(list(self.output.glob('*.tif')))

    def test_output_nodata_converts_rgb_tuple_without_losing_valid_zeros(self):
        values = np.array([[[0, 0, 5]], [[0, 9, 6]], [[0, 0, 7]]], dtype='uint8')
        path = self.make('rgb.tif', values)
        with gdal.Open(str(path), gdal.GA_Update) as ds:
            ds.SetMetadataItem('NODATA_VALUES', '0 0 0')
        self.run_merge('--output-nodata=255')
        self.assert_mosaic(values, np.broadcast_to(np.array([[False, True, True]]), values.shape))
        with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
            self.assertIsNone(ds.GetMetadataItem('NODATA_VALUES'))
            self.assertEqual(ds.GetRasterBand(1).GetMaskFlags(), gdal.GMF_NODATA)

    def test_async_nodata_suggestion_and_application(self):
        self.make('a.tif', np.array([[0, 7]], dtype='uint8'), mask=[[1, 0]])
        job = merge.start(self.source, self.output, analyze_only=True, suggest_nodata=True, cpus=1, ram='1GiB')
        job.process.wait(timeout=30)
        suggestion = job.result()['nodata_analysis']['suggestions'][0]
        self.output = self.root / 'async-nodata'
        job = merge.start(self.source, self.output, output_nodata=suggestion, cpus=1, ram='1GiB')
        job.process.wait(timeout=30)
        self.assertEqual(job.result()['status'], 'complete')
        self.assert_mosaic(np.array([[0, int(suggestion)]], dtype='uint8'), np.array([[1, 0]], dtype=bool))

    def test_omitted_target_creates_one_verified_file(self):
        self.make('a.tif', np.array([[0, 1]], dtype='uint8'))
        self.make('b.tif', np.array([[2, 3]], dtype='uint8'), x=4)
        args = merge.parser().parse_args([str(self.source), str(self.output)])
        with patch.object(merge, 'estimate_density', side_effect=AssertionError('No size planning needed')):
            with contextlib.redirect_stderr(io.StringIO()):
                report = merge.run(args)
        self.assertEqual([o['name'] for o in report['outputs']], ['mosaic-00001.tif'])
        self.assertIsNone(report['target_file_bytes'])
        self.assertIsNone(report['max_file_bytes'])
        self.assertTrue(report['all_source_valid_values_preserved'])
        self.assert_mosaic(np.array([[0, 1, 0, 0, 2, 3]], dtype='uint8'),
                           np.array([[1, 1, 0, 0, 1, 1]], dtype=bool))

    def test_custom_base_name_for_split_outputs(self):
        values = np.random.default_rng(3).integers(0, 65535, (512, 512), dtype='uint16')
        self.make('a.tif', values)
        report = self.run_merge('--target-size', '200KB', '--base-name', 'Sentinel RGB')
        self.assertGreater(len(report['outputs']), 1)
        self.assertEqual([o['name'] for o in report['outputs']],
                         [f'Sentinel RGB-{i:05d}.tif' for i in range(1, len(report['outputs']) + 1)])
        self.assert_mosaic(values, np.ones(values.shape, dtype=bool))

    def test_cli_single_file_custom_name_and_timing(self):
        self.make('a.tif', np.array([[4, 5]], dtype='uint8'))
        proc = subprocess.run([sys.executable, str(SCRIPT), str(self.source), str(self.output),
                               '--base-name', 'region'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([p.name for p in self.output.glob('*.tif')], ['region-00001.tif'])
        self.assertIn('elapsed ', proc.stderr)
        self.assertIn('rough expected total', proc.stderr)
        self.assertIn('remaining ', proc.stderr)
        self.assertIn('[complete]', proc.stderr)

    def test_base_name_rejects_paths_and_windows_reserved_names(self):
        for name in ('', '..', '../escape', 'a/b', 'a\\b', 'C:escape', 'NUL', 'con.txt', 'a?', 'a.', 'a '):
            with self.subTest(name=name):
                with self.assertRaises(argparse.ArgumentTypeError):
                    merge.output_base_name(name)
        self.assertFalse(self.output.exists())

    def test_timing_estimate_and_retry_work(self):
        with patch.object(merge.time, 'monotonic', return_value=100):
            progress = merge.Progress()
        output = io.StringIO()
        with contextlib.redirect_stderr(output), patch.object(merge.time, 'monotonic', return_value=160):
            progress.phase_start('Merging and verifying', .4, .5, 100)
            progress.advance('Merging and verifying', 50)
            progress.emit()
            # Extra work from a size retry must increase the remaining estimate.
            progress.advance('Merging and verifying', 0, extra_total=100)
            progress.emit()
        lines = output.getvalue().splitlines()
        self.assertIn('elapsed 00:01:00', lines[1])
        self.assertIn('rough expected total 00:01:32', lines[1])
        self.assertIn('rough expected total 00:01:54', lines[2])

    def test_progress_heartbeat_during_long_write_and_cleanup(self):
        self.make('a.tif', np.ones((2, 2), dtype='uint8'))
        original = merge.create_candidate
        def delayed(*args, **kwargs):
            time.sleep(5.2)
            return original(*args, **kwargs)
        output = io.StringIO()
        with patch.object(merge, 'create_candidate', side_effect=delayed), contextlib.redirect_stderr(output):
            merge.run(self.args())
        self.assertGreaterEqual(output.getvalue().count('[Merging and verifying]'), 2)
        self.assertIsNone(merge._progress)

    def test_adjacent_tiles_gap_and_nodata(self):
        a = np.array([[1, -99], [3, 4]], dtype="int16")
        b = np.array([[5, 6], [-99, 8]], dtype="int16")
        paths = [self.make("a.tif", a, nodata=-99), self.make("b.tiff", b, x=3, nodata=-99)]
        before = [p.read_bytes() for p in paths]
        report = self.run_merge()
        expected = np.concatenate([a, np.full((2, 1), -99, dtype="int16"), b], axis=1)
        self.assert_mosaic(expected, expected != -99)
        self.assertEqual(report["status"], "complete")
        self.assertTrue(report["inputs_unchanged"])
        self.assertTrue(report["all_source_valid_values_preserved"])
        self.assertEqual(before, [p.read_bytes() for p in paths])
        self.assertEqual(len(report["outputs"]), 1)

    def test_no_nodata_preserves_valid_zero_and_masks_gaps(self):
        self.make("a.tif", np.array([[0, 1]], dtype="uint8"))
        self.make("b.tif", np.array([[2, 0]], dtype="uint8"), x=4)
        self.run_merge()
        self.assert_mosaic(np.array([[0, 1, 0, 0, 2, 0]], dtype="uint8"),
                           np.array([[1, 1, 0, 0, 1, 1]], dtype=bool))
        self.assertFalse(list(self.output.glob("*.msk")))

    def test_identical_overlap_and_nodata_fallback(self):
        a = np.array([[1, -99, 3]], dtype="int16")
        b = np.array([[1, 2, -99]], dtype="int16")
        self.make("a.tif", a, nodata=-99)
        self.make("b.tif", b, nodata=-99)
        report = self.run_merge()
        self.assertEqual(report["overlaps"][0]["conflicting_band_samples"], 0)
        self.assertEqual(report["overlaps"][0]["valid_band_samples"], 1)
        self.assert_mosaic(np.array([[1, 2, 3]], dtype="int16"), np.ones((1, 3), dtype=bool))

    def test_conflict_stops_before_mosaics(self):
        self.make("a.tif", np.array([[1, 2]], dtype="uint8"))
        self.make("b.tif", np.array([[9, 2]], dtype="uint8"))
        with self.assertRaisesRegex(merge.MergeError, "Conflicting valid overlap"):
            self.run_merge()
        report = json.loads((self.output / "report.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["overlaps"][0]["conflicting_band_samples"], 1)
        self.assertEqual(report["overlaps"][0]["example"]["x"], 101)
        self.assertTrue(report["overlap_advice"])
        self.assertFalse(list(self.output.rglob("*.tif")))

    def test_explicit_first_and_last_report_discarded_values(self):
        self.make("a.tif", np.array([[1, -99, 3]], dtype="int16"), nodata=-99)
        self.make("b.tif", np.array([[9, 2, -99]], dtype="int16"), nodata=-99)
        for policy, expected in (("first", [1, 2, 3]), ("last", [9, 2, 3])):
            with self.subTest(policy=policy):
                self.output = self.root / policy
                report = self.run_merge("--overlap", policy)
                self.assert_mosaic(np.array([expected], dtype="int16"), np.ones((1, 3), dtype=bool))
                self.assertFalse(report["all_source_valid_values_preserved"])
                self.assertEqual(sum(o["discarded_conflicting_source_band_samples"] for o in report["outputs"]), 1)

    def test_multiband_independent_nodata(self):
        a = np.array([[[1, -99]], [[-99, 4]]], dtype="int16")
        b = np.array([[[-99, 2]], [[3, -99]]], dtype="int16")
        self.make("a.tif", a, nodata=-99)
        self.make("b.tif", b, nodata=-99)
        self.run_merge()
        expected = np.array([[[1, 2]], [[3, 4]]], dtype="int16")
        self.assert_mosaic(expected, np.ones(expected.shape, dtype=bool))

    def test_rgb_tuple_nodata_preserves_individual_zero_channels(self):
        values = np.array([[[0, 0, 5]], [[0, 9, 6]], [[0, 0, 7]]], dtype='uint8')
        path = self.make('rgb.tif', values)
        with gdal.Open(str(path), gdal.GA_Update) as ds:
            ds.SetMetadataItem('NODATA_VALUES', '0 0 0')
        self.run_merge()
        valid = np.broadcast_to(np.array([[False, True, True]]), values.shape)
        self.assert_mosaic(values, valid)
        with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
            self.assertEqual(ds.GetMetadataItem('NODATA_VALUES'), '0 0 0')

    def test_float_bits_including_nan_payload_and_signed_zero(self):
        a = np.array([[0x80000000, 0x7FC01234, 0x3F800001, 0x00000001]], dtype="uint32").view("float32")
        self.make("a.tif", a)
        self.run_merge()
        self.assert_mosaic(a, np.ones(a.shape, dtype=bool))

    def test_float_nan_nodata(self):
        a = np.array([[np.nan, 1.125]], dtype="float32")
        self.make("a.tif", a, nodata=np.nan)
        self.make("b.tif", np.array([[2.5, np.nan]], dtype="float32"), nodata=np.nan)
        self.run_merge()
        self.assert_mosaic(np.array([[2.5, 1.125]], dtype="float32"), np.ones((1, 2), dtype=bool))

    def test_internal_mask_with_nonzero_hidden_payload(self):
        a = np.array([[0, 9, 2]], dtype="uint8")
        self.make("a.tif", a, mask=[[1, 0, 1]])
        self.run_merge()
        self.assert_mosaic(a, np.array([[1, 0, 1]], dtype=bool))

    def test_external_mask_hashed_and_converted_to_internal(self):
        p = self.make("a.tif", np.array([[0, 7]], dtype="uint8"))
        with gdal.config_options({"GDAL_TIFF_INTERNAL_MASK": "NO"}):
            with gdal.Open(str(p), gdal.GA_Update) as ds:
                ds.CreateMaskBand(gdal.GMF_PER_DATASET)
                ds.GetRasterBand(1).GetMaskBand().WriteArray(np.array([[255, 0]], dtype="uint8"))
        report = self.run_merge()
        self.assertEqual(len(report["inputs"][0]["dependencies"]), 2)
        self.assert_mosaic(np.array([[0, 7]], dtype="uint8"), np.array([[1, 0]], dtype=bool))

    def test_band_metadata(self):
        p = self.make("a.tif", np.array([[10, 20]], dtype="uint16"))
        with gdal.Open(str(p), gdal.GA_Update) as ds:
            band = ds.GetRasterBand(1)
            band.SetScale(0.1); band.SetOffset(1.0); band.SetUnitType("m"); band.SetDescription("elevation")
            ds.SetMetadataItem("AREA_OR_POINT", "Point")
            band = None
        self.run_merge()
        with gdal.Open(str(next(self.output.glob("*.tif")))) as ds:
            band = ds.GetRasterBand(1)
            self.assertEqual((band.GetScale(), band.GetOffset(), band.GetUnitType(), band.GetDescription()), (0.1, 1.0, "m", "elevation"))
            self.assertEqual(ds.GetMetadataItem("AREA_OR_POINT"), "Point")
            band = None

    def test_negative_offsets_and_all_nodata_source(self):
        self.make("a.tif", np.array([[1, 2]], dtype="int16"), nodata=-99)
        self.make("b.tif", np.full((1, 2), -99, dtype="int16"), x=-2, y=-1, nodata=-99)
        self.run_merge()
        expected = np.array([[-99, -99, -99, -99], [-99, -99, 1, 2]], dtype="int16")
        self.assert_mosaic(expected, expected != -99, origin=(-2, -1))

    def test_three_way_overlap_counts_discarded_source_samples(self):
        for name, value in (("a", 1), ("b", 2), ("c", 3)):
            self.make(f"{name}.tif", np.array([[value]], dtype="uint8"))
        report = self.run_merge("--overlap", "last")
        self.assertEqual(sum(o["discarded_conflicting_source_band_samples"] for o in report["outputs"]), 2)
        self.assertEqual(sum(o["conflicting_band_samples"] for o in report["overlaps"]), 3)
        self.assert_mosaic(np.array([[3]], dtype="uint8"), np.ones((1, 1), dtype=bool))

    def test_no_compression(self):
        values = np.arange(256 * 256, dtype="uint16").reshape(256, 256)
        self.make("a.tif", values)
        report = self.run_merge("--compression", "none", "--max-size", "200KB")
        self.assertTrue(all(o["bytes"] <= 220000 for o in report["outputs"]))
        self.assert_mosaic(values, np.ones(values.shape, dtype=bool))

    def test_real_size_cap_splits_single_large_source(self):
        a = np.random.default_rng(12).integers(0, 65535, (768, 768), dtype="uint16")
        self.make("large.tif", a)
        report = self.run_merge("--max-size", "350KB")
        self.assertGreater(len(report["outputs"]), 1)
        self.assertTrue(all(o["bytes"] <= 385000 for o in report["outputs"]))
        self.assert_mosaic(a, np.ones(a.shape, dtype=bool))
        for o in report["outputs"]:
            p = self.output / o["name"]
            self.assertEqual(p.stat().st_size, o["bytes"])
            self.assertEqual(merge.sha256(p), o["sha256"])

    def test_measured_size_retry_after_bad_estimate(self):
        a = np.random.default_rng(3).integers(0, 65535, (512, 512), dtype="uint16")
        self.make("large.tif", a)
        # Force an optimistic initial estimate; the measured-size check must recover.
        with patch.object(merge, "plan", return_value=iter([merge.Rect(0, 0, 512, 512)])):
            report = self.run_merge("--max-size", "200KB")
        self.assertGreater(len(report["outputs"]), 1)
        self.assertTrue(all(o["bytes"] <= 220000 for o in report["outputs"]))
        self.assert_mosaic(a, np.ones(a.shape, dtype=bool))

    def test_verifier_detects_corrupted_sample(self):
        self.make("a.tif", np.array([[1, 2]], dtype="uint16"))
        original = merge.create_candidate

        def corrupt(path, *args, **kwargs):
            original(path, *args, **kwargs)
            with gdal.Open(str(path), gdal.GA_Update) as ds:
                ds.GetRasterBand(1).WriteArray(np.array([[7, 2]], dtype="uint16"))

        with patch.object(merge, "create_candidate", side_effect=corrupt):
            with self.assertRaisesRegex(merge.MergeError, "source pixels differ"):
                self.run_merge()
        self.assertFalse(list(self.output.glob("*.tif")))
        self.assertEqual(json.loads((self.output / "report.json").read_text())["status"], "failed")

    def test_verifier_detects_corrupted_empty_area_mask(self):
        self.make("a.tif", np.array([[1]], dtype="uint8"))
        self.make("b.tif", np.array([[2]], dtype="uint8"), x=2)
        original = merge.create_candidate

        def corrupt(path, *args, **kwargs):
            original(path, *args, **kwargs)
            with gdal.Open(str(path), gdal.GA_Update) as ds:
                ds.GetRasterBand(1).GetMaskBand().WriteArray(np.full((1, 3), 255, dtype="uint8"))

        with patch.object(merge, "create_candidate", side_effect=corrupt):
            with self.assertRaisesRegex(merge.MergeError, "validity mask or empty area"):
                self.run_merge()

    def test_source_modification_is_detected(self):
        p = self.make("a.tif", np.array([[1, 2]], dtype="uint8"))
        original = merge.ensure_coverage

        def modify(*args):
            original(*args)
            with gdal.Open(str(p), gdal.GA_Update) as ds:
                ds.SetMetadataItem("changed_during_test", "yes")

        with patch.object(merge, "ensure_coverage", side_effect=modify):
            with self.assertRaisesRegex(merge.MergeError, "Input changed"):
                self.run_merge()
        self.assertFalse(list(self.output.glob("*.tif")))

    def test_verifier_rejects_missing_output_coverage(self):
        self.make("a.tif", np.array([[1, 2]], dtype="uint8"))
        with patch.object(merge, "plan", return_value=iter([merge.Rect(0, 0, 1, 1)])):
            with self.assertRaisesRegex(merge.MergeError, "missing source coverage"):
                self.run_merge()
        self.assertFalse(list(self.output.glob("*.tif")))

    def test_verifier_rejects_changed_georeferencing(self):
        self.make("a.tif", np.array([[1, 2]], dtype="uint8"))
        original = merge.create_candidate

        def corrupt(path, *args, **kwargs):
            original(path, *args, **kwargs)
            with gdal.Open(str(path), gdal.GA_Update) as ds:
                ds.SetGeoTransform((102, 2, 0, 200, 0, -2))

        with patch.object(merge, "create_candidate", side_effect=corrupt):
            with self.assertRaisesRegex(merge.MergeError, "georeferencing or band metadata"):
                self.run_merge()

    def test_valid_nodata_value_under_explicit_mask_rejected(self):
        self.make("a.tif", np.array([[0, 1]], dtype="uint8"), nodata=0, mask=[[1, 1]])
        with self.assertRaisesRegex(merge.MergeError, "marks nodata-valued pixels valid"):
            self.run_merge()

    def test_dtype_mismatch_and_alpha_rejected(self):
        self.make("a.tif", np.ones((2, 2), dtype="uint8"))
        self.make("b.tif", np.ones((2, 2), dtype="uint16"))
        with self.assertRaisesRegex(merge.MergeError, "band types"):
            self.run_merge()
        self.output = self.root / "alpha-rejected"
        self.make("b.tif", np.ones((4, 2, 2), dtype="uint8"))
        with self.assertRaisesRegex(merge.MergeError, "alpha/palette"):
            self.run_merge()

    def test_rejects_misalignment_resolution_crs_and_types(self):
        self.make("a.tif", np.ones((2, 2), dtype="uint8"))
        for i, extra in enumerate((dict(transform=(101, 2, 0, 200, 0, -2)),
                                   dict(transform=(100, 4, 0, 200, 0, -4)),
                                   dict(crs="EPSG:4326"), dict(nodata=255))):
            with self.subTest(extra=extra):
                self.make("b.tif", np.ones((2, 2), dtype="uint8"), **extra)
                self.output = self.root / f"bad-{i}"
                with self.assertRaises(merge.MergeError):
                    self.run_merge()

    def test_existing_or_nested_output_rejected(self):
        self.make("a.tif", np.ones((1, 1), dtype="uint8"))
        self.output.mkdir()
        marker = self.output / "keep"
        marker.write_text("original")
        with self.assertRaises(FileExistsError):
            self.run_merge()
        self.assertEqual(marker.read_text(), "original")
        self.output = self.source / "nested"
        with self.assertRaisesRegex(merge.MergeError, "non-nested"):
            self.run_merge()

    def test_tiny_cap_fails_without_publishing(self):
        self.make("a.tif", np.ones((1, 1), dtype="uint8"))
        with self.assertRaisesRegex(merge.MergeError, "even one pixel"):
            self.run_merge("--max-size", "10B")
        self.assertFalse(list(self.output.glob("*.tif")))

    def test_far_apart_tiles_do_not_enumerate_empty_space(self):
        self.make("a.tif", np.ones((1, 1), dtype="uint8"))
        self.make("b.tif", np.ones((1, 1), dtype="uint8"), x=10**9, y=10**9)
        report = self.run_merge("--max-size", "10KB")
        self.assertEqual(len(report["outputs"]), 2)
        self.assertEqual(sum(o["grid_window"]["w"] * o["grid_window"]["h"] for o in report["outputs"]), 2)

    def test_analyze_only_and_cli(self):
        self.make("a.TIF", np.ones((1, 1), dtype="uint8"))
        proc = subprocess.run([sys.executable, str(SCRIPT), str(self.source), str(self.output),
                               "--max-size", "25GB", "--analyze-only"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads((self.output / "report.json").read_text())
        self.assertEqual(report["status"], "analyzed")
        self.assertEqual(report["max_file_bytes"], 27500000000)
        self.assertFalse(list(self.output.glob("*.tif")))

    def test_sizes_decimal_and_binary(self):
        self.assertEqual(merge.size_bytes("25GB"), 25000000000)
        self.assertEqual(merge.size_bytes("25GiB"), 25 * 1024**3)
        self.assertEqual(merge.size_bytes("1.5 MiB"), 1572864)

    def test_accepts_file_five_percent_above_target(self):
        self.make('a.tif', np.array([[1, 2]], dtype='uint8'))
        original = merge.create_candidate
        def padded(path, *args, **kwargs):
            original(path, *args, **kwargs)
            with path.open('ab') as stream:
                stream.write(b'\0' * (105000 - path.stat().st_size))
        with patch.object(merge, 'create_candidate', side_effect=padded):
            report = self.run_merge('--target-size', '100KB')
        self.assertEqual(len(report['outputs']), 1)
        self.assertEqual(report['outputs'][0]['bytes'], 105000)
        self.assertEqual(report['max_file_bytes'], 110000)

    def test_planner_uses_allowance_to_avoid_unnecessary_split(self):
        rect = merge.Rect(0, 0, 100, 105)
        source = merge.Source(Path('example.tif'), rect, ())
        self.assertEqual(list(merge.plan(rect, [source], 10000)), [rect])

    def test_optional_cpu_and_ram_budget(self):
        self.make('a.tif', np.ones((256, 256), dtype='uint8'))
        sources, template = merge.inventory(self.source)
        args = self.args('--cpus', '3', '--ram', '512MiB')
        with patch.object(merge, 'available_resources', return_value=(16, 16 * 1024**3)):
            r = merge.resource_plan(args, template, sources, 10)
        self.assertEqual(r['cpus'], 3)
        self.assertEqual(r['ram_budget_bytes'], 512 * 1024**2)
        slots = r['workers'] + r['codec_threads_per_worker'] if r['codec_threads_per_worker'] > 1 else r['workers']
        self.assertLessEqual(slots, 3)
        self.assertLessEqual(r['workers'] * r['estimated_worker_bytes'] + r['gdal_cache_bytes'] +
                            r['estimated_codec_pool_bytes'] + 128 * 1024**2,
                             r['ram_budget_bytes'])

    def test_parallel_outputs_and_full_verification(self):
        values = np.random.default_rng(12).integers(0, 65535, (512, 512), dtype='uint16')
        self.make('a.tif', values)
        report = self.run_merge('--target-size', '200KB', '--cpus', '2', '--ram', '2GiB')
        self.assertEqual(report['resources']['workers'], 2)
        self.assert_mosaic(values, np.ones(values.shape, dtype=bool))

    def test_async_api_and_cancellation(self):
        self.make('a.tif', np.ones((512, 512), dtype='uint8'))
        job = merge.start(self.source, self.output, base_name='async region', cpus=2, ram='1GiB',
                          block_size=256, cache_mib=64)
        deadline = time.monotonic() + 30
        while not job.done and time.monotonic() < deadline:
            time.sleep(.025)
        self.assertTrue(job.done, job.status())
        self.assertEqual(job.result()['status'], 'complete')
        self.assertIsNone(job.result()['target_file_bytes'])
        self.assertEqual(job.result()['outputs'][0]['name'], 'async region-00001.tif')
        self.assertIn('rough expected total', job.log_path.read_text(encoding='utf-8'))
        self.output = self.root / 'cancelled-output'
        job = merge.start(self.source, self.output, target_size='1MiB', cpus=1, ram='1GiB')
        job.cancel()
        deadline = time.monotonic() + 30
        while not job.done and time.monotonic() < deadline:
            time.sleep(.025)
        self.assertTrue(job.done)
        self.assertEqual(job.status()['state'], 'cancelled', job.status())
        self.assertFalse(list(self.output.glob('*.tif')))


if __name__ == "__main__":
    unittest.main()
