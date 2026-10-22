"""Explicit Byte fill fallback preserves data values while discarding missingness."""
import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal
import test_merge as fixtures

merge = fixtures.merge


class FallbackFillTests(unittest.TestCase):
    setUp = fixtures.MergeTests.setUp
    make = fixtures.MergeTests.make
    args = fixtures.MergeTests.args
    run_merge = fixtures.MergeTests.run_merge
    assert_mosaic = fixtures.MergeTests.assert_mosaic

    def full_byte_sources(self):
        values = np.tile(np.arange(256, dtype='uint8'), (4, 1))
        valid = np.ones(values.shape, dtype=bool)
        valid[0, 7] = False  # Value 7 remains valid in the other rows.
        self.make('a-full.tif', values, mask=valid)
        self.make('b-zero.tif', np.zeros((4, 1), dtype='uint8'), x=258)
        expected = np.zeros((4, 259), dtype='uint8')
        expected[:, :256] = values
        coverage = np.ones(expected.shape, dtype=bool)
        coverage[:, 256:258] = False
        coverage[0, 7] = False
        return expected, coverage

    def assert_no_validity_storage(self):
        for path in self.output.glob('*.tif'):
            with gdal.Open(str(path)) as ds:
                self.assertEqual(ds.RasterCount, 1)
                self.assertEqual(ds.GetRasterBand(1).DataType, gdal.GDT_Byte)
                self.assertIsNone(ds.GetRasterBand(1).GetNoDataValue())
                self.assertIsNone(ds.GetMetadataItem('NODATA_VALUES'))
                self.assertEqual(ds.GetRasterBand(1).GetMaskFlags(), gdal.GMF_ALL_VALID)
        self.assertFalse(list(self.output.glob('*.msk*')))

    def test_zero_and_nonzero_fill_preserve_all_valid_byte_values(self):
        values, valid = self.full_byte_sources()
        for fill in (0, 255, 37):
            with self.subTest(fill=fill):
                self.output = self.root / f'fill-{fill}'
                report = self.run_merge('--fallback-fill', str(fill))
                expected = np.where(valid, values, fill).astype('uint8')
                self.assert_mosaic(expected, np.ones(expected.shape, dtype=bool))
                self.assert_no_validity_storage()
                self.assertTrue(report['inputs_unchanged'])
                self.assertTrue(report['all_source_valid_values_preserved'])
                self.assertEqual(report['fallback_fill'], {'value': fill, 'selected': True})
                self.assertEqual(report['output_nodata_source'], 'fallback_fill')
                self.assertIsNone(report['output_nodata'])
                self.assertEqual(sum(o['filled_output_band_samples'] for o in report['outputs']), 9)

    def test_cli_dry_run_and_actual_merge(self):
        values, valid = self.full_byte_sources()
        for dry in (True, False):
            self.output = self.root / ('dry' if dry else 'merged')
            command = [sys.executable, str(fixtures.SCRIPT), str(self.source), str(self.output),
                       '--fallback-fill=0', '--cpus=1']
            result = subprocess.run(command + (['--dry-run'] if dry else []), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((self.output / 'report.json').read_text())
            self.assertEqual(report['status'], 'analyzed' if dry else 'complete')
            self.assertTrue(report['fallback_fill']['selected'])
            self.assertFalse(report['nodata_analysis']['replacement_possible'])
            self.assertIn('as ordinary valid data', result.stderr)
            if dry:
                self.assertFalse(list(self.output.rglob('*.tif')))
            else:
                expected = np.where(valid, values, 0).astype('uint8')
                self.assert_mosaic(expected, np.ones(expected.shape, dtype=bool))
                self.assert_no_validity_storage()

    def test_safe_or_inherited_nodata_takes_precedence(self):
        for nd in (None, 255):
            with self.subTest(nodata=nd):
                self.output = self.root / str(nd)
                self.make('a.tif', np.array([[1, 9]], dtype='uint8'), nodata=nd, mask=[[1, 0]])
                report = self.run_merge('--fallback-fill=37')
                self.assertFalse(report['fallback_fill']['selected'])
                self.assertEqual(report['output_nodata_source'], 'automatic' if nd is None else 'inherited')
                with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
                    band = ds.GetRasterBand(1)
                    self.assertEqual(band.GetMaskFlags(), gdal.GMF_NODATA)
                    self.assertEqual(ds.ReadAsArray()[0, 1], 0 if nd is None else 255)

    def test_invalid_values_and_conflicting_options(self):
        for value in ('-1', '256', '1.5', 'nan', 'inf', 'True'):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.args('--fallback-fill=' + value)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args('--fallback-fill=0', '--output-nodata=0')
        self.assertFalse(self.output.exists())

    def test_rejects_non_byte_or_multiband_inputs(self):
        for i, values in enumerate((np.ones((1, 2), dtype='uint16'),
                                    np.ones((1, 2), dtype='float32'),
                                    np.ones((2, 1, 2), dtype='uint8'))):
            self.output = self.root / str(i)
            self.make('a.tif', values)
            with self.assertRaisesRegex(merge.MergeError, 'single-band Byte'):
                self.run_merge('--fallback-fill=0')
            self.assertFalse(list(self.output.rglob('*.tif')))

    def test_cog_and_split_outputs_have_no_nodata_or_mask(self):
        values, valid = self.full_byte_sources()
        expected = np.where(valid, values, 37).astype('uint8')
        for cog in (False, True):
            self.output = self.root / str(cog)
            options = ['--cog'] if cog else ['--target-size=80KiB', '--compression=none']
            report = self.run_merge('--fallback-fill=37', *options)
            self.assert_mosaic(expected, np.ones(expected.shape, dtype=bool))
            self.assert_no_validity_storage()
            self.assertEqual(sum(o['filled_output_band_samples'] for o in report['outputs']), 9)
            if cog:
                self.assertGreater(report['outputs'][0]['cog_validation']['overview_count'], 0)
                with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
                    ov = ds.GetRasterBand(1).GetOverview(0)
                    self.assertIsNone(ov.GetNoDataValue())
                    self.assertEqual(ov.GetMaskFlags(), gdal.GMF_ALL_VALID)
            else:
                self.assertGreater(len(report['outputs']), 1)

    def test_resampling_uses_source_validity_before_filling(self):
        values, valid = self.full_byte_sources()
        for method in ('average', 'nearest'):
            self.output = self.root / method
            report = self.run_merge('--pixel-size=4', '--resampling=' + method, '--fallback-fill=37')
            expected = np.full((2, 130), 37, dtype='uint8')
            missing = 0
            for y in range(2):
                for x in range(130):
                    if method == 'nearest':
                        sy, sx = y * 2 + 1, x * 2 + 1
                        samples = values[sy:sy+1, sx:sx+1][valid[sy:sy+1, sx:sx+1]]
                    else:
                        samples = values[y*2:y*2+2, x*2:x*2+2][valid[y*2:y*2+2, x*2:x*2+2]]
                    if samples.size:
                        expected[y, x] = int(np.floor(samples.mean() + .5))
                    else:
                        missing += 1
            with gdal.Open(str(next(self.output.glob('*.tif')))) as ds:
                np.testing.assert_array_equal(ds.ReadAsArray(), expected)
            self.assert_no_validity_storage()
            self.assertEqual(sum(o['filled_output_band_samples'] for o in report['outputs']), missing)

    def test_verifiers_reject_corrupted_fills_values_and_validity(self):
        self.full_byte_sources()
        original = merge.create_candidate
        for resample in (False, True):
            for corruption in ('fill', 'value', 'mask', 'nodata'):
                with self.subTest(resample=resample, corruption=corruption):
                    self.output = self.root / f'{resample}-{corruption}'
                    def corrupt(path, *args, **kwargs):
                        original(path, *args, **kwargs)
                        with gdal.Open(str(path), gdal.GA_Update) as ds:
                            band = ds.GetRasterBand(1)
                            if corruption == 'fill':
                                band.WriteArray(np.array([[38]], dtype='uint8'), xoff=128 if resample else 256)
                            elif corruption == 'value':
                                band.WriteArray(np.array([[99]], dtype='uint8'))
                            elif corruption == 'mask':
                                ds.CreateMaskBand(gdal.GMF_PER_DATASET)
                                band.GetMaskBand().Fill(255)
                            else:
                                band.SetNoDataValue(0)
                    with patch.object(merge, 'create_candidate', corrupt):
                        with self.assertRaisesRegex(merge.MergeError, 'Verification failed'):
                            self.run_merge('--fallback-fill=37', *(['--pixel-size=4'] if resample else []))
                    self.assertFalse(list(self.output.glob('*.tif')))

    def test_async_api_forwards_zero_fill(self):
        values, valid = self.full_byte_sources()
        job = merge.start(self.source, self.output, fallback_fill=0, cpus=1, ram='1GiB')
        job.process.wait(timeout=30)
        self.assertTrue(job.result()['fallback_fill']['selected'])
        expected = np.where(valid, values, 0).astype('uint8')
        self.assert_mosaic(expected, np.ones(expected.shape, dtype=bool))
        self.assert_no_validity_storage()


if __name__ == '__main__':
    unittest.main()
