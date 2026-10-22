"""Resampling contracts, including independent fractional-area expectations."""
import contextlib
import io
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal
import test_merge as fixtures
merge = fixtures.merge


class ResamplingTests(unittest.TestCase):
    setUp = fixtures.MergeTests.setUp
    make = fixtures.MergeTests.make
    args = fixtures.MergeTests.args
    run_merge = fixtures.MergeTests.run_merge

    def raster(self, values, name='a.tif', col=0, **kwargs):
        return self.make(name, values, transform=(100 + col * 4, 4, 0, 200, 0, -4), **kwargs)

    def output_arrays(self):
        outputs = sorted(self.output.glob('*.tif'))
        with gdal.Open(str(outputs[0])) as ds:
            dtype = ds.ReadAsArray().dtype
            count = ds.RasterCount
        specs = []
        for path in outputs:
            with gdal.Open(str(path)) as ds:
                t = ds.GetGeoTransform()
                x, y = round((t[0] - 100) / 10), round((200 - t[3]) / 10)
                self.assertEqual(t[1:3], (10., 0.))
                self.assertEqual(t[4:], (0., -10.))
                specs.append((path, x, y, ds.RasterXSize, ds.RasterYSize))
        width, height = max(x+w for _, x, y, w, h in specs), max(y+h for _, x, y, w, h in specs)
        result = np.zeros((count, height, width), dtype=dtype)
        valid = np.zeros(result.shape, dtype=bool)
        occupied = np.zeros((height, width), dtype=bool)
        for path, x, y, w, h in specs:
            self.assertFalse(occupied[y:y+h, x:x+w].any())
            occupied[y:y+h, x:x+w] = True
            with gdal.Open(str(path)) as ds:
                result[:, y:y+h, x:x+w] = ds.ReadAsArray()
                valid[:, y:y+h, x:x+w] = np.stack([ds.GetRasterBand(i+1).GetMaskBand().ReadAsArray() != 0 for i in range(count)])
        return result, valid

    def test_fractional_average_across_input_boundary(self):
        values = np.arange(25, dtype='float64').reshape(5, 5)
        self.raster(values[:, :3])
        self.raster(values[:, 3:], 'b.tif', col=3)
        report = self.run_merge('--pixel-size', '10')
        actual, valid = self.output_arrays()
        np.testing.assert_allclose(actual[0], [[4.8, 7.2], [16.8, 19.2]], rtol=0, atol=1e-14)
        self.assertTrue(valid.all())
        self.assertFalse(report['all_source_valid_values_preserved'])
        self.assertEqual(report['resampling']['source_pixel_size'], [4, 4])
        self.assertEqual(report['reference_transform'], [100, 4, 0, 200, 0, -4])
        self.assertEqual(report['resampling']['width'], 2)

    def test_integer_rounding_and_partial_edges(self):
        self.raster(np.array([[-1, -2, 8]], dtype='int16'))
        self.run_merge('--pixel-size', '10')
        actual, valid = self.output_arrays()
        # (-1 - 2 + 0.5*8)/2.5=0.4; last cell uses remaining half of 8.
        np.testing.assert_array_equal(actual[0], [[0, 8]])
        self.assertTrue(valid.all())
        self.output = self.root / 'ties'
        self.run_merge('--pixel-size', '8')
        with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
            np.testing.assert_array_equal(ds.ReadAsArray(), [[-2, 8]])

    def test_nearest_partial_edge_and_invalid_center(self):
        mask = np.ones((3, 3), dtype=bool)
        mask[1, 1] = False
        self.raster(np.arange(9, dtype='uint8').reshape(3, 3), mask=mask)
        self.run_merge('--pixel-size', '10', '--resampling', 'nearest')
        _, valid = self.output_arrays()
        self.assertFalse(valid.any())  # First center masked; remaining centers outside footprint.

    def test_nearest_keeps_bits(self):
        values = np.zeros((5, 5), dtype='float32')
        values[1, 1] = -0.
        values.view('uint32')[1, 3] = 0x7fc00023
        values[3, 1] = np.inf
        values[3, 3] = 8
        self.raster(values)
        self.run_merge('--pixel-size', '10', '--resampling', 'nearest')
        actual, valid = self.output_arrays()
        self.assertEqual(actual[0].tobytes(), values[np.ix_([1,3], [1,3])].tobytes())
        self.assertTrue(valid.all())

    def test_average_excludes_masks_and_checks_nonfinite(self):
        values = np.ones((5, 5), dtype='float32') * 3
        values[0, 0] = np.nan
        mask = np.ones(values.shape, dtype=bool)
        mask[0, 0] = False
        self.raster(values, mask=mask)
        self.run_merge('--pixel-size', '10')
        actual, valid = self.output_arrays()
        np.testing.assert_array_equal(actual, 3)
        self.assertTrue(valid.all())
        self.output = self.root / 'nonfinite'
        self.raster(values, name='b.tif')
        with self.assertRaisesRegex(merge.MergeError, 'finite valid'):
            self.run_merge('--pixel-size', '10')
        self.assertFalse(list(self.output.glob('*.tif')))

    def test_derived_nodata_collision_and_suggestions(self):
        self.raster(np.tile([1, 3], (2, 1)).astype('uint8'), nodata=2)
        with self.assertRaisesRegex(merge.MergeError, 'collide'):
            self.run_merge('--pixel-size', '8')
        self.assertFalse(list(self.output.glob('*.tif')))
        self.output = self.root / 'analysis'
        report = self.run_merge('--pixel-size', '8', '--suggest-nodata', '--dry-run')
        self.assertNotIn('2', report['nodata_analysis']['suggestions'])
        self.assertIn('resampled', report['nodata_analysis']['scope'])
        self.assertFalse(list(self.output.glob('*.tif')))
        self.output = self.root / 'explicit'
        self.run_merge('--pixel-size', '8', '--output-nodata', '0')
        with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
            self.assertEqual(ds.ReadAsArray()[0,0], 2)
            self.assertEqual(ds.GetRasterBand(1).GetMaskFlags(), gdal.GMF_NODATA)
        self.output = self.root / 'explicit-bad'
        with self.assertRaisesRegex(merge.MergeError, 'collid'):
            self.run_merge('--pixel-size', '8', '--output-nodata', '2')

    def test_automatic_nodata_excludes_rounded_average_and_overlap_losers(self):
        # Zero is absent from the inputs but introduced by averaging -1 and 1.
        values = np.array([[-1, 1]], dtype='int16')
        self.raster(values)
        # The discarded observation must also exclude the next candidate (-32768).
        self.raster(np.array([[-32768, 1]], dtype='int16'), name='b.tif')
        report = self.run_merge('--pixel-size', '8', '--overlap', 'first')
        self.assertEqual(report['output_nodata'], '32767')
        self.assertIn('resampled', report['nodata_analysis']['scope'])
        with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
            np.testing.assert_array_equal(ds.ReadAsArray(), [[0]])
            self.assertEqual(ds.GetRasterBand(1).GetNoDataValue(), 32767)
            self.assertEqual(ds.GetRasterBand(1).GetMaskFlags(), gdal.GMF_NODATA)

    def test_overlap_is_resolved_before_average(self):
        self.raster(np.ones((5,5), dtype='uint8') * 2)
        self.raster(np.ones((5,5), dtype='uint8') * 8, name='b.tif')
        with self.assertRaisesRegex(merge.MergeError, 'Conflicting'):
            self.run_merge('--pixel-size', '10')
        for policy, value in [('first', 2), ('last', 8)]:
            self.output = self.root / policy
            self.run_merge('--pixel-size', '10', '--overlap', policy)
            actual, valid = self.output_arrays()
            np.testing.assert_array_equal(actual, value)
            self.assertTrue(valid.all())

    def test_windows_splits_and_cog(self):
        values = np.random.default_rng(123).integers(1, 250, (700, 700), dtype='uint8')
        self.raster(values)
        expected = None
        for name, opts in [('small', ['--block-size', '7']), ('large', ['--block-size', '128']),
                           ('split', ['--target-size', '150KiB', '--compression', 'none']),
                           ('cog', ['--cog', '--cog-overviews', 'average'])]:
            self.output = self.root / name
            report = self.run_merge('--pixel-size', '10', '--cpus', '1', *opts)
            actual, valid = self.output_arrays()
            self.assertTrue(valid.all())
            if expected is None:
                expected = actual
            else:
                np.testing.assert_array_equal(actual, expected)
            if name == 'split':
                self.assertGreater(len(report['outputs']), 1)
                self.assertTrue(all(o['bytes'] <= 150*1024*1.1 for o in report['outputs']))
            if name == 'cog':
                self.assertTrue(report['outputs'][0]['cog_validation']['full_check'])

    def test_invalid_size_and_dry_run_grid(self):
        self.raster(np.ones((3,3), dtype='uint8'))
        for value in ('0', '-1', 'nan', 'inf', 'bad'):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.args('--pixel-size', value)
        with self.assertRaisesRegex(merge.MergeError, 'upsampling'):
            self.run_merge('--pixel-size', '3')
        self.output = self.root / 'dry'
        report = self.run_merge('--pixel-size', '10', '--dry-run')
        self.assertEqual((report['resampling']['width'], report['resampling']['height']), (2,2))
        self.assertEqual(report['status'], 'analyzed')
        self.assertFalse(list(self.output.glob('*.tif')))

    def test_verifier_rejects_pixels_mask_and_transform(self):
        self.raster(np.ones((5,5), dtype='uint8') * 4)
        original = merge.create_candidate
        for corruption in ('value', 'mask', 'transform'):
            self.output = self.root / corruption
            def corrupt(path, *args, **kwargs):
                original(path, *args, **kwargs)
                with gdal.Open(str(path), gdal.GA_Update) as ds:
                    if corruption == 'value':
                        ds.GetRasterBand(1).WriteArray(np.array([[99]], dtype='uint8'))
                    elif corruption == 'mask':
                        ds.CreateMaskBand(gdal.GMF_PER_DATASET)
                        ds.GetRasterBand(1).GetMaskBand().WriteArray(np.array([[0]], dtype='uint8'))
                    else:
                        ds.SetGeoTransform((0,10,0,200,0,-10))
            with patch.object(merge, 'create_candidate', corrupt):
                with self.assertRaisesRegex(merge.MergeError, 'Verification failed'):
                    self.run_merge('--pixel-size', '10')
            self.assertFalse(list(self.output.glob('*.tif')))

    def test_multiband_validity_and_gaps(self):
        values = np.full((2, 5, 5), 4, dtype='int16')
        values[0, :3, :3] = -9999
        values[1, 2:, 2:] = -9999
        self.raster(values, nodata=-9999)
        self.raster(np.full((2, 5, 5), 8, dtype='int16'), name='b.tif', col=10, nodata=-9999)
        self.run_merge('--pixel-size', '10')
        actual, valid = self.output_arrays()
        self.assertFalse(valid[:, :, 2:4].any())
        self.assertFalse(valid[0, 0, 0])
        self.assertFalse(valid[1, 1, 1])
        np.testing.assert_array_equal(actual[:, :, 4:], 8)
        self.assertTrue(valid[:, :, 4:].all())

    def test_tuple_nodata_collision_after_rounding(self):
        values = np.array([[[0, 1]], [[1, 0]], [[0, 0]]], dtype='uint8')
        path = self.raster(values)
        with gdal.Open(str(path), gdal.GA_Update) as ds:
            ds.SetMetadataItem('NODATA_VALUES', '1 1 0')
        with self.assertRaisesRegex(merge.MergeError, 'tuple NoData'):
            self.run_merge('--pixel-size', '8')
        self.output = self.root / 'safe-tuple'
        self.run_merge('--pixel-size', '8', '--output-nodata', '255')
        with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
            np.testing.assert_array_equal(ds.ReadAsArray()[:, 0, 0], [1,1,0])

    def test_decimal_ratios_float_and_integer_windows(self):
        for dtype in ('float32', 'float64', 'int16'):
            values = np.random.default_rng(12).normal(0, 100, (77, 79)).astype(dtype)
            self.raster(values)
            expected = None
            for edge in (7, 64):
                self.output = self.root / (dtype + str(edge))
                self.run_merge('--pixel-size', '7.3', '--block-size', str(edge))
                with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
                    actual = ds.ReadAsArray()
                    if expected is None:
                        expected = actual
                    elif dtype == 'int16':
                        np.testing.assert_array_equal(actual, expected)
                    else:
                        np.testing.assert_allclose(actual, expected, rtol=8*np.finfo(dtype).eps,
                                                   atol=8*np.finfo(dtype).eps*np.max(np.abs(values)))

    def test_coarse_single_pixel_and_cancellation(self):
        self.raster(np.arange(25, dtype='uint8').reshape(5,5))
        self.run_merge('--pixel-size', '10000')
        with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
            np.testing.assert_array_equal(ds.ReadAsArray(), [[12]])
        self.output = self.root / 'cancelled'
        def cancel(*args, **kwargs):
            raise merge.Cancelled('test cancellation')
        with patch.object(merge, 'resample_block', cancel):
            with self.assertRaises(merge.Cancelled):
                self.run_merge('--pixel-size', '10')
        self.assertFalse(list(self.output.glob('*.tif')))

    def test_decimal_grid_has_no_extra_edge_pixel(self):
        self.make('a.tif', np.ones((3,3), dtype='uint8'),
                  transform=(100, .1, 0, 200, 0, -.1))
        self.run_merge('--pixel-size', '.3')
        with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
            self.assertEqual((ds.RasterXSize, ds.RasterYSize), (1,1))

    def test_async_api_forwarding(self):
        self.raster(np.arange(25, dtype='uint8').reshape(5,5))
        job = merge.start(self.source, self.output, pixel_size=10, resampling='nearest', cpus=1)
        self.assertEqual(job.process.wait(timeout=60), 0)
        report = job.result()
        self.assertEqual(report['resampling']['method'], 'nearest')
        actual, _ = self.output_arrays()
        np.testing.assert_array_equal(actual[0], [[6,8], [16,18]])


if __name__ == '__main__':
    unittest.main()
