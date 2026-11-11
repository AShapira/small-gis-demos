"""Real GDAL DTED fixtures plus independent sample/edge contracts."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, osr

from test_dem_cogs import merger, overview_reference


class DtedTests(unittest.TestCase):
    def setUp(self):
        merger.configure()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "sources"
        self.inputs.mkdir()
        self.output = self.root / "output"
        self.cancel = threading.Event()
        self.crs = osr.SpatialReference()
        self.crs.ImportFromEPSG(4326)

    def tile(self, south=50, west=12, level=1, name=None, values=None, vertical=None):
        n = 1200 if level == 1 else 3600
        reference = south if south >= 0 else -(south + 1)
        factor = 6 if reference >= 80 else 4 if reference >= 75 else 3 if reference >= 70 else 2 if reference >= 50 else 1
        width = n // factor + 1
        path = self.inputs / (name or f"nested/e{west}/n{south}.dt{level}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if values is None:
            cols = (west * n + factor * np.arange(width)) % 10000
            rows = ((south + 1) * n - np.arange(n + 1)) % 1000
            values = (rows[:, None] + cols[None, :]).astype(np.int16)
        source = gdal.GetDriverByName("MEM").Create("", width, n + 1, 1, gdal.GDT_Int16)
        source.SetProjection(self.crs.ExportToWkt())
        source.SetGeoTransform((west - factor / (2 * n), factor / n, 0,
                                south + 1 + .5 / n, 0, -1 / n))
        source.SetMetadataItem("AREA_OR_POINT", "Point")
        if vertical:
            source.SetMetadataItem("DTED_VerticalDatum", vertical)
        source.GetRasterBand(1).SetNoDataValue(-32767)
        source.GetRasterBand(1).WriteArray(values)
        with merger.dted_options():
            result = gdal.GetDriverByName("DTED").CreateCopy(str(path), source)
            merger.close(result)
        merger.close(source)
        return path, values

    def args(self, *extra, level=1):
        return merger.parser().parse_args(["--dted-dir", str(self.inputs), "--dted-level", str(level),
            "--output-dir", str(self.output), "--block-size", "1", "--workers", "1", *extra])

    def run_merge(self, *extra, level=1, cancel=None):
        with contextlib.redirect_stdout(io.StringIO()) as log:
            result = merger.run(self.args(*extra, level=level), cancel=cancel)
        self.log = log.getvalue()
        return result

    def inventory(self, level=1):
        return merger.read_dted_inventory(self.inputs, level)

    def signature(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns

    def test_recursive_discovery_level_filter_and_metadata_only_dry_run(self):
        self.tile(name="deep/one/TILE.DT1")
        (self.inputs / "ignored.dt2").write_bytes(b"unselected level need not be readable")
        (self.inputs / "readme.txt").write_text("ignored", encoding="utf-8")
        with patch.object(merger, "write_dted_block", side_effect=AssertionError("dry run wrote pixels")):
            self.assertEqual(self.run_merge("--dry-run", "--filename-filter", "tile"), 0)
        report = json.loads(self.log)
        self.assertEqual(report["sources"], 1)
        self.assertEqual(report["mode"], "dted_blocks")
        self.assertEqual(report["output_prefix"], "DTED1")
        self.assertEqual(report["longitude_expansion_counts"], {"2": 1})
        self.assertEqual(report["blocks"][0]["dimensions"], [1201, 1201])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.run_merge("--dry-run"), 0)
        self.assertEqual(json.loads(self.log)["excluded_other_level_files"], 1)
        self.assertEqual(self.run_merge("--dry-run", "--output-prefix", "NASADEM"), 0)
        self.assertEqual(json.loads(self.log)["output_prefix"], "NASADEM")

    def test_level_arguments_required_and_exclusive(self):
        for args in (["--dted-dir", str(self.inputs)], ["--vrt-file", "x", "--dted-level", "1"]):
            parsed = merger.parser().parse_args([*args, "--output-dir", str(self.output)])
            with self.assertRaisesRegex(merger.InputError, "supplied together"):
                merger.run(parsed)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            merger.parser().parse_args(["--dted-dir", "a", "--vrt-file", "b", "--output-dir", "c"])

    def test_real_dted1_cog_values_statistics_overviews_and_resume(self):
        path, original = self.tile()
        original[10:20, 50:60] = -32767
        self.tile(values=original)
        before = self.signature(path)
        self.assertEqual(self.run_merge("--verify", "--build-overviews"), 0, self.log)
        output, = self.output.glob("*.tif")
        expected = original[:, np.array([min((j + 1) // 2, 600) for j in range(1201)])]
        with merger.opened(output) as ds:
            np.testing.assert_array_equal(ds.ReadAsArray(), expected)
            self.assertEqual(ds.GetGeoTransform(), (12 - .5 / 1200, 1 / 1200, 0, 51 + .5 / 1200, 0, -1 / 1200))
            self.assertEqual(ds.GetMetadataItem("AREA_OR_POINT"), "Point")
            self.assertEqual(ds.GetMetadataItem("VERTICAL_DATUM"), "EGM96")
            band = ds.GetRasterBand(1)
            stats = merger.read_statistics(band, expected.size, required=True)
            valid = expected[expected != -32767].astype(np.float64)
            self.assertEqual(stats["valid_pixels"], valid.size)
            self.assertEqual(stats["nodata_pixels"], expected.size - valid.size)
            self.assertAlmostEqual(stats["mean"], valid.mean(), places=9)
            self.assertAlmostEqual(stats["stddev"], valid.std(), places=9)
            for i in range(band.GetOverviewCount()):
                overview = band.GetOverview(i)
                np.testing.assert_array_equal(overview.ReadAsArray(), overview_reference(ds, overview, "nearest"))
        output_before = self.signature(output)
        self.assertEqual(self.run_merge("--verify", "--build-overviews"), 0, self.log)
        self.assertEqual(self.signature(output), output_before)
        self.assertEqual(self.signature(path), before)
        self.assertFalse(list(self.output.glob("*.vrt")))
        self.assertFalse(list(self.output.glob(".incomplete/*")))
        self.assertFalse(list(self.inputs.rglob("*.aux.xml")))

    def test_real_dted2_native_grid(self):
        _, original = self.tile(south=50, level=2)
        self.assertEqual(self.run_merge("--verify", level=2), 0, self.log)
        output, = self.output.glob("DTED2_*.tif")
        with merger.opened(output) as ds:
            self.assertEqual((ds.RasterXSize, ds.RasterYSize), (3601, 3601))
            np.testing.assert_array_equal(ds.ReadAsArray(0, 0, 7, 1), original[:1, [0, 1, 1, 2, 2, 3, 3]])
            np.testing.assert_array_equal(ds.ReadAsArray(3600, 0, 1, 3601), original[:, -1:])

    def test_all_zone_factors_both_hemispheres_and_window_boundaries(self):
        for south, factor in ((49, 1), (50, 2), (70, 3), (75, 4), (80, 6),
                              (-50, 1), (-51, 2), (-71, 3), (-76, 4), (-81, 6)):
            with self.subTest(south=south):
                path, original = self.tile(south=south, west=-17)
                inv = self.inventory()
                source = next(s for s in inv.sources if s.path == path)
                self.assertEqual(source.dted_factor, factor)
                with merger.dted_options():
                    readers = merger.Readers("DTED")
                    try:
                        for start, count in ((0, 13), (509, 13), (1190, 11)):
                            region = source.gx + start, source.gy + 7, count, 2
                            actual = merger.compose_dted_window(inv, [source], region, readers, self.cancel)
                            # Scalar nearest-coordinate oracle, ties toward the east.
                            positions = range(0, 1201, factor)
                            indices = [min(range(len(positions)), key=lambda k: (abs(positions[k] - j), -k))
                                       for j in range(start, start + count)]
                            np.testing.assert_array_equal(actual, original[7:9, indices])
                    finally:
                        readers.close()

    def test_zone_seam_preserves_native_posts_and_publishes_relative_vrt(self):
        _, fine = self.tile(south=74)  # factor 3
        _, coarse = self.tile(south=75)  # factor 4
        self.assertEqual(self.run_merge("--verify"), 0, self.log)
        outputs = sorted(self.output.glob("*.tif"))
        with merger.opened(outputs[0]) as south, merger.opened(outputs[1]) as north:
            a = south.ReadAsArray(0, 0, 1201, 1)[0]
            b = north.ReadAsArray(0, 1200, 1201, 1)[0]
            np.testing.assert_array_equal(a, b)
            np.testing.assert_array_equal(a[::3], fine[0])
            np.testing.assert_array_equal(a[::4], coarse[-1])
        vrt = self.output / "DTED1_mosaic.vrt"
        self.assertTrue(vrt.exists())
        self.assertNotIn(str(self.inputs), vrt.read_text())
        self.assertIn('relativeToVRT="1"', vrt.read_text())
        before = [self.signature(p) for p in outputs]
        vrt.unlink()
        self.assertEqual(self.run_merge("--verify"), 0, self.log)
        self.assertTrue(vrt.exists())
        self.assertEqual(before, [self.signature(p) for p in outputs])

    def test_original_conflict_fails_without_verify_and_cleans_staging(self):
        self.tile(south=49)
        _, coarse = self.tile(south=50)
        coarse[-1, 10] += 1
        self.tile(south=50, values=coarse)
        self.assertEqual(self.run_merge(), 1)
        self.assertIn("Conflicting original DTED elevations", self.log)
        self.assertIn("12.0166666667, 50", self.log)
        self.assertFalse(list(self.output.glob("*.tif")))
        self.assertFalse(list(self.output.glob("*.vrt")))
        self.assertFalse(list(self.output.glob(".incomplete/*")))

    def test_cancellation_resume_and_input_change_rejection(self):
        path, _ = self.tile()
        original = merger.compose_dted_window
        def cancel_after_window(*args):
            result = original(*args)
            self.cancel.set()
            return result
        with patch.object(merger, "compose_dted_window", side_effect=cancel_after_window):
            self.assertEqual(self.run_merge(cancel=self.cancel), 130)
        self.assertFalse(list(self.output.glob(".incomplete/*")))
        self.assertEqual(self.run_merge("--verify"), 0, self.log)
        stamp = path.stat()
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
        with self.assertRaisesRegex(merger.InputError, "different inputs/options"):
            self.run_merge()

    def test_bad_selected_input_and_level_mismatch_fail(self):
        (self.inputs / "bad.dt1").write_bytes(b"bad")
        with self.assertRaises((merger.InputError, RuntimeError, merger.RunError)):
            self.run_merge("--dry-run")
        (self.inputs / "bad.dt1").unlink()
        self.tile(level=2, name="wrong.dt1")
        with self.assertRaisesRegex(merger.InputError, "DTED1 Int16"):
            self.run_merge("--dry-run")

    def test_storage_reserves_staging_and_concurrent_workers(self):
        self.tile(south=50)
        self.tile(south=51)
        self.assertEqual(self.run_merge("--dry-run", "--workers", "2", "--build-overviews"), 0)
        report = json.loads(self.log)
        expected = merger.HEADROOM
        for block in report["blocks"]:
            self.assertEqual(block["storage_estimate_method"], "dted-raw-tiles-v1")
            self.assertGreaterEqual(block["estimated_staging_bytes"], 1201 * 1201 * 2)
            self.assertGreater(block["estimated_temporary_bytes"], block["estimated_staging_bytes"])
            expected += block["storage_reservation_bytes"]
        self.assertEqual(report["estimated_concurrent_reservation_bytes"], expected)

    def test_duplicate_coverage_fills_nodata_and_counts_nominal_tiles(self):
        _, values = self.tile()
        first, second = values.copy(), values.copy()
        first[:, :200] = -32767
        second[:, 200:] = -32767
        self.tile(values=first)
        self.tile(values=second, name="copies/other.dt1")
        self.assertEqual(self.run_merge("--dry-run", "--min-tiles-per-block", "2"), 0)
        block = json.loads(self.log)["blocks"][0]
        self.assertEqual(block["tile_count"], 1)
        self.assertFalse(block["eligible"])
        self.assertEqual(self.run_merge("--verify"), 0, self.log)
        output, = self.output.glob("*.tif")
        with merger.opened(output) as ds:
            np.testing.assert_array_equal(ds.ReadAsArray()[:, ::2], values)

    def test_all_nodata_and_uncovered_part_of_degree_block(self):
        self.tile(values=np.full((1201, 601), -32767, dtype=np.int16))
        self.assertEqual(self.run_merge("--verify", "--block-size", "2", "--build-overviews"), 0, self.log)
        output, = self.output.glob("*.tif")
        with merger.opened(output) as ds:
            self.assertEqual((ds.RasterXSize, ds.RasterYSize), (2401, 2401))
            self.assertTrue(np.all(ds.ReadAsArray() == -32767))
            stats = merger.read_statistics(ds.GetRasterBand(1), 2401 ** 2, required=True)
            self.assertEqual(stats["valid_pixels"], 0)
            self.assertEqual(stats["nodata_pixels"], 2401 ** 2)
            self.assertIsNone(stats["mean"])

    def test_vertical_datums_must_agree(self):
        self.tile(south=50)
        self.tile(south=51, vertical="E08")
        with self.assertRaisesRegex(merger.InputError, "vertical datum"):
            self.run_merge("--dry-run")
        self.assertFalse(self.output.exists())

    def test_directory_symlinks_are_pruned(self):
        self.tile()
        linked = self.inputs / "linked"
        linked.mkdir()
        (linked / "bad.dt1").write_bytes(b"must not be opened")
        # Exercise traversal pruning on Windows too, without needing permission
        # to create OS symlinks on the user's host.
        original = type(linked).is_symlink
        with patch.object(type(linked), "is_symlink", lambda p: p == linked or original(p)):
            self.assertEqual(self.run_merge("--dry-run"), 0)
        self.assertEqual(json.loads(self.log)["sources"], 1)

    def test_driver_configuration_is_pinned_and_restored(self):
        path, _ = self.tile()
        key = "DTED_APPLY_PIXEL_IS_POINT"
        previous = gdal.GetThreadLocalConfigOption(key)
        try:
            gdal.SetThreadLocalConfigOption(key, "TRUE")
            inv = self.inventory()
            self.assertEqual(inv.sources[0].path, path)
            self.assertEqual(inv.sources[0].west, 12)
            self.assertEqual(gdal.GetThreadLocalConfigOption(key), "TRUE")
            self.assertEqual(self.run_merge("--verify"), 0, self.log)
            self.assertEqual(gdal.GetThreadLocalConfigOption(key), "TRUE")
        finally:
            gdal.SetThreadLocalConfigOption(key, previous)

    def test_concurrent_blocks_east_west_edges_and_southern_zone_verification(self):
        self.tile(south=-81, west=-17)
        self.tile(south=-81, west=-16)
        self.assertEqual(self.run_merge("--verify", "--workers", "2"), 0, self.log)
        outputs = list(self.output.glob("*.tif"))
        self.assertEqual(len(outputs), 2)
        edges = []
        for path in outputs:
            with merger.opened(path) as ds:
                edges.append((ds.GetGeoTransform()[0], ds.ReadAsArray(0, 0, 1, 1201),
                              ds.ReadAsArray(1200, 0, 1, 1201)))
        edges.sort(key=lambda item: item[0])
        np.testing.assert_array_equal(edges[0][2], edges[1][1])

    def test_new_selected_file_during_run_prevents_stage_publication(self):
        self.tile(south=50)
        self.tile(south=51)
        translate = merger.gdal.Translate
        def add_input(*args, **kwargs):
            result = translate(*args, **kwargs)
            if not (self.inputs / "added.dt1").exists():
                self.tile(south=52, name="added.dt1")
            return result
        with patch.object(merger.gdal, "Translate", side_effect=add_input):
            with self.assertRaisesRegex(merger.RunError, "Selected DTED files changed"):
                self.run_merge()
        self.assertFalse(list(self.output.glob("*.vrt")))
        state = json.loads((self.output / "state.json").read_text())
        self.assertEqual(state["status"], "failed")

    def test_verifier_detects_corrupted_compositor_and_no_output_is_published(self):
        self.tile()
        compose = merger.compose_dted_window
        def corrupted(*args):
            result = compose(*args)
            result[0, 0] += 1
            return result
        with patch.object(merger, "compose_dted_window", side_effect=corrupted):
            self.assertEqual(self.run_merge("--verify"), 1)
        self.assertIn("DTED pixel/NoData verification failed", self.log)
        self.assertFalse(list(self.output.glob("*.tif")))
        self.assertFalse(list(self.output.glob(".incomplete/*")))


if __name__ == "__main__":
    unittest.main()
