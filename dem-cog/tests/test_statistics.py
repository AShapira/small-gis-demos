"""Full-resolution statistics, embedded metadata, and legacy resume contracts."""
import math
import threading
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal

import test_consolidation as fixtures
from test_dem_cogs import merger


class StatisticsTests(unittest.TestCase):
    setUp = fixtures.ConsolidationTests.setUp
    raster = fixtures.ConsolidationTests.raster
    args = fixtures.ConsolidationTests.args
    run_merge = fixtures.ConsolidationTests.run_merge
    make_stage = fixtures.ConsolidationTests.make_stage
    state = fixtures.ConsolidationTests.state

    def assert_statistics(self, path, expected, nodata):
        valid = expected[expected != nodata].astype(np.float64)
        with merger.opened(path) as ds:
            band = ds.GetRasterBand(1)
            metadata = band.GetMetadata()
            self.assertEqual(metadata["NASADEM_STATISTICS_METHOD"], merger.STATISTICS_POLICY)
            self.assertNotIn("STATISTICS_APPROXIMATE", metadata)
            self.assertEqual(int(metadata["NASADEM_VALID_PIXEL_COUNT"]), valid.size)
            self.assertEqual(int(metadata["NASADEM_NODATA_PIXEL_COUNT"]), expected.size - valid.size)
            self.assertAlmostEqual(float(metadata["STATISTICS_VALID_PERCENT"]), 100 * valid.size / expected.size)
            self.assertFalse(any(p.lower().endswith(".aux.xml") for p in ds.GetFileList()))
            if valid.size:
                self.assertEqual(float(metadata["STATISTICS_MINIMUM"]), float(valid.min()))
                self.assertEqual(float(metadata["STATISTICS_MAXIMUM"]), float(valid.max()))
                mean = math.fsum(map(float, valid)) / valid.size
                variance = math.fsum((float(v) - mean) ** 2 for v in valid) / valid.size
                self.assertTrue(math.isclose(float(metadata["STATISTICS_MEAN"]), mean,
                                             rel_tol=1e-12, abs_tol=1e-12))
                self.assertTrue(math.isclose(float(metadata["STATISTICS_STDDEV"]), math.sqrt(variance),
                                             rel_tol=1e-12, abs_tol=1e-12))
            else:
                for key in merger.STATISTICS_FIELDS.values():
                    self.assertNotIn(key, metadata)
            np.testing.assert_array_equal(band.ReadAsArray(), expected)
        self.assertFalse(path.with_name(path.name + ".aux.xml").exists())

    def test_exact_float32_extremes_and_stale_statistics_overviews_ignored(self):
        values = np.full((257, 1025), 1.125, dtype="float32")
        values[192, 998] = -432.375
        values[201, 997] = 8192.125
        values[20:40, 30:50] = -9999.25
        source = self.raster(values=values, data_type="Float32", nodata=-9999.25, resolution=1/128)
        ds = gdal.Open(str(source), gdal.GA_Update)
        b = ds.GetRasterBand(1)
        b.SetStatistics(-1, 1, 0, .5)
        b.SetMetadataItem("STATISTICS_APPROXIMATE", "YES")
        b.SetMetadataItem("NASADEM_VALID_PIXEL_COUNT", "1")
        b.SetMetadataItem("NASADEM_NODATA_PIXEL_COUNT", "0")
        b.SetMetadataItem("NASADEM_STATISTICS_METHOD", "old")
        b.SetMetadataItem("HEIGHT_SOURCE", "test provenance")
        ds.BuildOverviews("NEAREST", [2, 4])
        for i in range(b.GetOverviewCount()):
            b.GetOverview(i).Fill(500)
        merger.close(ds)
        b = ds = None
        self.assertEqual(self.run_merge("--verify"), 0, self.messages)
        output = self.output / "NASADEM_merged.tif"
        self.assert_statistics(output, values, -9999.25)
        with merger.opened(output) as ds:
            self.assertEqual(ds.GetRasterBand(1).GetMetadataItem("HEIGHT_SOURCE"), "test provenance")

    def test_nodata_constant_and_single_valid_pixel(self):
        for index, values in enumerate((np.full((3, 5), -32768), np.full((3, 5), -17),
                                        np.array([[-32768, -32768, 123]]))):
            with self.subTest(index=index):
                self.raster(values=values)
                output = self.root / f"out{index}"
                self.assertEqual(self.run_merge("--verify", output=output), 0, self.messages)
                self.assert_statistics(output / "NASADEM_merged.tif", values, -32768)

    def test_composed_int16_statistics_count_seams_once_and_include_gaps(self):
        self.raster(values=[[1, 2, 3, 4, 5], [6, 7, 8, 9, -32768], [11, 12, 13, 14, 15]])
        self.raster("b.tif", [[5, 20, 21], [10, 22, 23], [15, 24, 25]], west=2)
        self.raster("c.tif", [[30, 31]], west=0, north=-1)
        self.assertEqual(self.run_merge(), 0, self.messages)
        expected = np.full((5, 7), -32768, dtype="int16")
        expected[:3, :5] = [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13, 14, 15]]
        expected[:3, 5:] = [[20, 21], [22, 23], [24, 25]]
        expected[4, :2] = [30, 31]
        self.assert_statistics(self.output / "NASADEM_merged.tif", expected, -32768)

    def test_block_statistics_and_reconsolidation_of_different_statistics(self):
        self.raster(values=np.full((3, 3), 10))
        self.raster("b.tif", np.full((3, 3), 20), west=1)
        self.assertEqual(self.make_stage("--verify"), 0)
        blocks = self.root / "blocks"
        for path in blocks.glob("*.tif"):
            with merger.opened(path) as ds:
                expected = ds.ReadAsArray()
            self.assert_statistics(path, expected, -32768)
        vrt = gdal.Open(str(blocks / "NASADEM_mosaic.vrt"))
        self.assertFalse(any(merger.statistics_key(k) for k in vrt.GetRasterBand(1).GetMetadata()))
        expected = vrt.ReadAsArray()
        merger.close(vrt)
        vrt = None
        self.assertEqual(self.run_merge("--verify", inputs=blocks), 0, self.messages)
        self.assert_statistics(self.output / "NASADEM_merged.tif", expected, -32768)

    def test_nonfinite_samples_fail_with_location(self):
        for value in (np.nan, np.inf, -np.inf):
            with self.subTest(value=value):
                self.raster(values=[[1, value]], data_type="Float32")
                self.assertEqual(self.run_merge(), 1)
                self.assertIn("Non-finite valid elevation at output pixel 1, 0", self.messages)
                self.assertFalse(list(self.output.glob("*.tif")))
                # Each attempt has new inputs and therefore needs a new destination.
                self.output = self.root / (self.output.name + "_next")

    def test_cancellation_during_statistics_then_resume(self):
        self.raster()
        cancel = threading.Event()
        original = merger.Statistics.add

        def interrupted(stats, *args, **kwargs):
            original(stats, *args, **kwargs)
            cancel.set()

        with patch.object(merger.Statistics, "add", interrupted):
            self.assertEqual(self.run_merge(cancel=cancel), 130)
        self.assertFalse(list(self.output.glob("*.tif")))
        self.assertEqual(self.run_merge("--verify"), 0, self.messages)
        self.assertEqual(self.state()["blocks"]["NASADEM_merged.tif"]["statistics_policy"],
                         merger.STATISTICS_POLICY)

    def test_legacy_completed_output_is_unchanged_but_missing_output_gets_statistics(self):
        self.raster()
        # Produce a genuine legacy-format output through the old creation behavior.
        translate = merger.gdal.Translate
        verify = merger.verify_output

        def legacy_translate(*args, **kwargs):
            kwargs["creationOptions"] = ["STATISTICS=NO" if o == "STATISTICS=AUTO" else o
                                         for o in kwargs["creationOptions"]]
            return translate(*args, **kwargs)

        def legacy_verify(*args, **kwargs):
            kwargs.pop("require_statistics", None)
            kwargs.pop("expected_statistics", None)
            return verify(*args, **kwargs)

        with patch.object(merger, "compute_vrt_statistics", return_value=None), (
                patch.object(merger.gdal, "Translate", side_effect=legacy_translate)), (
                patch.object(merger, "verify_output", side_effect=legacy_verify)):
            self.assertEqual(self.run_merge(), 0)
        state = self.state()
        state["blocks"]["NASADEM_merged.tif"].pop("statistics_policy")
        merger.atomic_json(self.output / "state.json", state)
        final = self.output / "NASADEM_merged.tif"
        before = final.read_bytes(), final.stat().st_mtime_ns
        self.assertEqual(self.run_merge("--verify"), 0)
        self.assertEqual((final.read_bytes(), final.stat().st_mtime_ns), before)
        final.unlink()
        self.assertEqual(self.run_merge("--verify"), 0)
        self.assert_statistics(final, np.full((3, 5), 10), -32768)

    def test_new_output_resume_requires_statistics_and_exhaustive_check_recomputes(self):
        self.raster()
        self.assertEqual(self.run_merge(), 0)
        final = self.output / "NASADEM_merged.tif"
        before = final.read_bytes(), final.stat().st_mtime_ns
        self.assertEqual(self.run_merge("--verify"), 0)
        self.assertEqual((final.read_bytes(), final.stat().st_mtime_ns), before)
        read = merger.read_statistics

        def wrong(*args, **kwargs):
            result = read(*args, **kwargs)
            result["mean"] += 1
            return result

        with patch.object(merger, "read_statistics", side_effect=wrong):
            self.assertEqual(self.run_merge("--verify"), 1)
        self.assertIn("statistics mismatch for mean", self.messages)
        self.assertEqual((final.read_bytes(), final.stat().st_mtime_ns), before)

    def test_dry_run_performs_no_statistics_scan(self):
        self.raster()
        with patch.object(merger, "compute_vrt_statistics", side_effect=AssertionError("pixel read")):
            self.assertEqual(self.run_merge("--dry-run"), 0)
        self.assertFalse(self.output.exists())

    def test_resume_rejects_missing_statistics_on_new_output(self):
        source = self.raster()
        self.assertEqual(self.run_merge(), 0)
        replacement = self.root / "without-statistics.tif"
        ds = gdal.Translate(str(replacement), str(source), format="COG", creationOptions=[
            "BIGTIFF=YES", "COMPRESS=DEFLATE", "PREDICTOR=STANDARD", "BLOCKSIZE=512",
            "STATISTICS=NO", "OVERVIEWS=NONE"])
        merger.close(ds)
        ds = None
        replacement.replace(self.output / "NASADEM_merged.tif")
        self.assertEqual(self.run_merge("--verify"), 1)
        self.assertIn("Invalid exact statistics metadata", self.messages)


if __name__ == "__main__":
    unittest.main()
