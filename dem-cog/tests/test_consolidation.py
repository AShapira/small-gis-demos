"""Folder consolidation and portable output-stage VRT contracts."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
from osgeo import gdal, osr

from test_dem_cogs import merger, overview_reference


class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dem-cog-results-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = self.root / "previous results"
        self.inputs.mkdir()
        self.output = self.root / "consolidated"
        merger.configure()
        crs = osr.SpatialReference()
        crs.ImportFromEPSG(4326)
        self.wkt = crs.ExportToWkt()

    def raster(self, name="a.tif", values=None, west=0, north=1, resolution=.5,
               data_type="Int16", nodata=-32768):
        dtype = "float32" if data_type == "Float32" else "int16"
        values = np.asarray(values if values is not None else np.full((3, 5), 10), dtype=dtype)
        path = self.inputs / name
        ds = gdal.GetDriverByName("GTiff").Create(str(path), values.shape[1], values.shape[0], 1,
                                                 gdal.GetDataTypeByName(data_type))
        ds.SetProjection(self.wkt)
        ds.SetGeoTransform((west - resolution / 2, resolution, 0,
                            north + resolution / 2, 0, -resolution))
        ds.SetMetadataItem("VERTICAL_DATUM", "WGS84 ellipsoid")
        b = ds.GetRasterBand(1)
        b.SetNoDataValue(nodata)
        b.SetUnitType("m")
        b.WriteArray(values)
        merger.close(ds)
        return path

    def args(self, *extra, inputs=None, output=None):
        return merger.parser().parse_args([
            "--merge-results-dir", str(inputs or self.inputs),
            "--output-dir", str(output or self.output), *extra])

    def run_merge(self, *extra, inputs=None, output=None, cancel=None):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            result = merger.run(self.args(*extra, inputs=inputs, output=output), cancel=cancel)
        self.messages = stream.getvalue()
        return result

    def make_stage(self, *extra, cancel=None):
        vrt = self.root / "original.vrt"
        if not vrt.exists():
            ds = gdal.BuildVRT(str(vrt), [str(p) for p in sorted(self.inputs.glob("*.tif"))])
            ds.SetMetadataItem("VERTICAL_DATUM", "WGS84 ellipsoid")
            merger.close(ds)
            ds = None  # Flush the VRT on GDAL versions without Dataset.Close().
        args = merger.parser().parse_args([
            "--vrt-file", str(vrt), "--output-dir", str(self.root / "blocks"),
            "--block-size", "1", "--workers", "1", *extra])
        with contextlib.redirect_stdout(io.StringIO()):
            return merger.run(args, cancel=cancel)

    def state(self, directory=None):
        return json.loads(((directory or self.output) / "state.json").read_text())

    def test_stages_portable_vrt_and_consolidation_without_originals(self):
        expected = np.arange(25, dtype="float32").reshape(5, 5) * .125 - 1.75
        for x, y in ((0, 0), (2, 0), (0, 2), (2, 2)):
            self.raster(f"tile{x}{y}.tif", expected[y:y+3, x:x+3], west=x/2,
                        north=2-y/2, data_type="Float32")
        self.assertEqual(self.make_stage("--verify"), 0)
        blocks = self.root / "blocks"
        mosaic = blocks / "NASADEM_mosaic.vrt"
        nodes = ET.parse(mosaic).findall(".//SourceFilename")
        self.assertEqual(len(nodes), 4)
        self.assertTrue(all(n.get("relativeToVRT") == "1" and Path(n.text).name == n.text for n in nodes))
        moved = self.root / "moved blocks"
        shutil.copytree(blocks, moved)
        shutil.rmtree(blocks)
        shutil.rmtree(self.inputs)
        (self.root / "original.vrt").unlink()
        (moved / "state.json").unlink()
        ds = gdal.Open(str(moved / mosaic.name))
        np.testing.assert_array_equal(ds.ReadAsArray(), expected)
        merger.close(ds)
        before = {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                  for p in moved.glob("*.tif")}
        self.assertEqual(self.run_merge("--verify", inputs=moved), 0, self.messages)
        with merger.opened(self.output / "NASADEM_merged.tif") as ds:
            np.testing.assert_array_equal(ds.ReadAsArray(), expected)
            self.assertEqual(ds.GetMetadataItem("VERTICAL_DATUM"), "WGS84 ellipsoid")
            self.assertEqual(ds.GetRasterBand(1).GetUnitType(), "m")
            self.assertEqual(ds.GetMetadataItem("PREDICTOR", "IMAGE_STRUCTURE"), "3")
        self.assertEqual(before, {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                                  for p in moved.glob("*.tif")})
        self.assertFalse(list(self.output.glob("*.vrt")))
        self.assertFalse(list(self.output.glob("*.ovr")))

    def test_rectangles_shared_edge_valid_over_nodata_and_gaps(self):
        self.raster(values=[[1, 2, 3, 4, 5], [6, 7, 8, 9, -32768], [11, 12, 13, 14, 15]])
        self.raster("b.TIFF", [[5, 20, 21], [10, 22, 23], [15, 24, 25]], west=2)
        self.raster("c.tif", [[30, 31]], west=0, north=-1)
        self.assertEqual(self.run_merge("--verify"), 0, self.messages)
        expected = np.full((5, 7), -32768, dtype="int16")
        expected[:3, :5] = [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13, 14, 15]]
        expected[:3, 5:] = [[20, 21], [22, 23], [24, 25]]
        expected[4, :2] = [30, 31]
        with merger.opened(self.output / "NASADEM_merged.tif") as ds:
            np.testing.assert_array_equal(ds.ReadAsArray(), expected)
            np.testing.assert_array_equal(ds.GetRasterBand(1).GetMaskBand().ReadAsArray() != 0,
                                          expected != -32768)
            self.assertEqual(ds.GetGeoTransform(), (-.25, .5, 0, 1.25, 0, -.5))

    def test_conflicts_rejected_even_without_verify_and_dry_run_is_metadata_only(self):
        self.raster(values=np.full((3, 5), 1.25), data_type="Float32")
        different = np.nextafter(np.float32(1.25), np.float32(2))
        self.raster("conflict.tif", np.full((3, 3), different), west=2, data_type="Float32")
        with patch.object(merger, "verify_overlaps", side_effect=AssertionError("Pixel read")):
            self.assertEqual(self.run_merge("--dry-run"), 0)
        report = json.loads(self.messages)
        self.assertEqual(report["overlap_validation"], "during_execution")
        self.assertEqual(report["blocks"][0]["dimensions"], [7, 3])
        self.assertIsNone(report["mosaic_vrt"])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.run_merge(), 1)
        self.assertIn("Conflicting valid elevations at 2, 1", self.messages)
        self.assertIn("conflict.tif", self.messages)
        self.assertIn("a.tif", self.messages)
        self.assertFalse(list(self.output.glob("*.tif")))

    def test_overviews_fresh_internal_nearest_or_average_bigtiff_and_fractional_nodata(self):
        values = np.full((257, 1025), 4.375, dtype="float32")
        values[:, 512:] = 10.125
        values[0:8, 0:8] = -9999.25
        path = self.raster(values=values, data_type="Float32", nodata=-9999.25, resolution=1/128)
        ds = gdal.Open(str(path), gdal.GA_Update)
        ds.BuildOverviews("NEAREST", [2, 4])
        for i in range(ds.GetRasterBand(1).GetOverviewCount()):
            ds.GetRasterBand(1).GetOverview(i).Fill(999)
        merger.close(ds)
        for method in ("nearest", "average"):
            with self.subTest(method=method):
                target = self.root / method
                options = () if method == "nearest" else ("--overview-resampling", "average")
                self.assertEqual(self.run_merge("--verify", "--output-prefix", "SRTM",
                                                *options, output=target), 0, self.messages)
                self.assertEqual(self.state(target)["overview_resampling"], method)
                final = target / "SRTM_merged.tif"
                with final.open("rb") as f:
                    self.assertIn(f.read(4), (b"II+\x00", b"MM\x00+"))
                with merger.opened(final) as ds:
                    b = ds.GetRasterBand(1)
                    np.testing.assert_array_equal(b.ReadAsArray(), values)
                    self.assertEqual(b.GetOverviewCount(), 2)
                    overview = b.GetOverview(0)
                    np.testing.assert_array_equal(overview.ReadAsArray(), overview_reference(ds, overview, method))
                    self.assertTrue(all(not p.lower().endswith(".ovr") for p in ds.GetFileList()))
                self.assertFalse(list(target.glob("*.ovr")))
                self.assertFalse(list(target.glob("*.msk")))

    def test_legacy_average_consolidation_requires_explicit_resampling(self):
        self.raster()
        self.assertEqual(self.run_merge("--overview-resampling", "average"), 0)
        state = self.state()
        self.assertNotIn("overview_resampling", state["identity"]["config"])
        state.pop("overview_resampling")
        merger.atomic_json(self.output / "state.json", state)
        final = self.output / "NASADEM_merged.tif"
        before = (final.read_bytes(), final.stat().st_mtime_ns)
        with self.assertRaisesRegex(merger.InputError, "different inputs/options"):
            self.run_merge()
        self.assertEqual(self.run_merge("--overview-resampling", "average", "--verify"), 0)
        self.assertEqual((final.read_bytes(), final.stat().st_mtime_ns), before)

    def test_filter_single_input_and_nonrecursive_discovery(self):
        self.raster("selected.TIF")
        (self.inputs / "bad.tif").write_bytes(b"invalid")
        (self.inputs / "nested").mkdir()
        (self.inputs / "nested" / "selected.tif").write_bytes(b"invalid")
        self.assertEqual(self.run_merge("--filename-filter", "SELECTED", "--verify"), 0, self.messages)
        self.assertFalse(list(self.output.glob("*.vrt")))
        self.assertEqual(self.state()["blocks"]["NASADEM_merged.tif"]["verification"]["pixels_checked"], 15)
        with self.assertRaisesRegex(merger.InputError, "No selected"):
            self.run_merge("--filename-filter", "absent", "--dry-run")
        with self.assertRaises(merger.InputError):
            self.run_merge("--dry-run")

    def test_invalid_options_and_same_directory_fail_without_writes(self):
        self.raster()
        for option, value in (("--block-size", "10"), ("--min-tiles-per-block", "1")):
            with self.subTest(option=option), self.assertRaisesRegex(merger.InputError, "only to --vrt-file"):
                self.run_merge(option, value)
        with self.assertRaisesRegex(merger.InputError, "separate output"):
            self.run_merge(output=self.inputs)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args("--vrt-file", "unused.vrt")
        self.assertFalse(self.output.exists())

    def test_incompatible_results_fail_preflight(self):
        self.raster()
        path = self.raster("b.tif", west=2)
        modifications = {
            "rotation": lambda ds: ds.SetGeoTransform((1.75, .5, .1, 1.25, 0, -.5)),
            "alignment": lambda ds: ds.SetGeoTransform((1.8, .5, 0, 1.25, 0, -.5)),
            "resolution": lambda ds: ds.SetGeoTransform((1.75, .5, 0, 1.25, 0, -.25)),
            "nodata": lambda ds: ds.GetRasterBand(1).SetNoDataValue(-9999),
            "missing nodata": lambda ds: ds.GetRasterBand(1).DeleteNoDataValue(),
            "datum": lambda ds: ds.SetMetadataItem("VERTICAL_DATUM", "EGM96"),
            "unit": lambda ds: ds.GetRasterBand(1).SetUnitType("ft"),
            "mask": lambda ds: ds.GetRasterBand(1).CreateMaskBand(gdal.GMF_PER_DATASET),
            "crs": lambda ds: ds.SetProjection("EPSG:3857"),
        }
        for name, change in modifications.items():
            with self.subTest(name=name):
                path = self.raster("b.tif", west=2)
                ds = gdal.Open(str(path), gdal.GA_Update)
                change(ds)
                merger.close(ds)
                with self.assertRaises(merger.InputError):
                    self.run_merge("--dry-run")
                for sidecar in self.inputs.glob("b.tif.*"):
                    sidecar.unlink()
        self.raster("b.tif", west=2, data_type="Float32")
        with self.assertRaisesRegex(merger.InputError, "Incompatible"):
            self.run_merge()
        self.assertFalse(self.output.exists())

    def test_resume_adoption_changed_inventory_and_no_rewrite(self):
        self.raster()
        self.assertEqual(self.run_merge(), 0, self.messages)
        final = self.output / "NASADEM_merged.tif"
        before = final.stat().st_mtime_ns
        self.assertEqual(self.run_merge("--verify", "--build-overviews"), 0, self.messages)
        self.assertEqual(final.stat().st_mtime_ns, before)
        state = self.state()
        state["blocks"][final.name]["status"] = "running"
        merger.atomic_json(self.output / "state.json", state)
        self.assertEqual(self.run_merge(), 0)
        record = self.state()["blocks"][final.name]
        self.assertEqual(record["action"], "adopted")
        self.assertEqual(record["verification"]["pixels_checked"], 15)
        self.raster("added.tif", west=3)
        with self.assertRaisesRegex(merger.InputError, "different inputs/options"):
            self.run_merge()

    def test_new_input_during_conversion_prevents_publication(self):
        self.raster()
        translate = merger.gdal.Translate

        def changed(*args, **kwargs):
            result = translate(*args, **kwargs)
            self.raster("added.tif", west=3)
            return result

        with patch.object(merger.gdal, "Translate", side_effect=changed):
            self.assertEqual(self.run_merge(), 1)
        self.assertIn("Selected results changed", self.messages)
        self.assertFalse(list(self.output.glob("*.tif")))

    def test_cancel_then_resume_and_disk_policy(self):
        self.raster()
        with patch.object(merger.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(merger.RunError, "Insufficient disk"):
                self.run_merge("--disk-space-policy", "error")
        cancel = threading.Event()

        def interrupted(*args, **kwargs):
            cancel.set()
            raise RuntimeError("interrupted")

        with patch.object(merger.gdal, "Translate", side_effect=interrupted):
            self.assertEqual(self.run_merge(cancel=cancel), 130)
        self.assertFalse(list(self.output.glob("*.tif")))
        with patch.object(merger.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            self.assertEqual(self.run_merge("--verify"), 0, self.messages)
        self.assertIn("WARNING", self.messages)

    def test_stage_vrt_regeneration_legacy_state_and_collision(self):
        self.raster(values=np.full((3, 3), 10))
        self.raster("b.tif", np.full((3, 3), 10), west=1)
        self.assertEqual(self.make_stage(), 0)
        blocks = self.root / "blocks"
        vrt = blocks / "NASADEM_mosaic.vrt"
        before = {p.name: p.stat().st_mtime_ns for p in blocks.glob("*.tif")}
        original = self.state(blocks)
        signature = original["signature"]
        vrt.unlink()
        original.pop("mosaic_vrt")
        merger.atomic_json(blocks / "state.json", original)
        self.assertEqual(self.make_stage("--verify"), 0)
        self.assertTrue(vrt.exists())
        self.assertEqual(self.state(blocks)["signature"], signature)
        self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in blocks.glob("*.tif")})
        vrt.unlink()
        self.assertEqual(self.make_stage(), 0)
        vrt.write_text("user owned")
        with self.assertRaisesRegex(merger.InputError, "Unrelated or modified stage VRT"):
            self.make_stage()
        self.assertEqual(vrt.read_text(), "user owned")

    def test_failed_and_cancelled_stages_do_not_publish_vrt(self):
        self.raster(values=np.full((3, 3), 10))
        self.raster("b.tif", np.full((3, 3), 10), west=1)
        process = merger.process_block

        def fail_second(inv, block, *args, **kwargs):
            if "E001" in block.name:
                raise merger.RunError("test failure")
            return process(inv, block, *args, **kwargs)

        with patch.object(merger, "process_block", side_effect=fail_second):
            self.assertEqual(self.make_stage(), 1)
        blocks = self.root / "blocks"
        self.assertEqual(len(list(blocks.glob("*.tif"))), 1)
        self.assertFalse(list(blocks.glob("*.vrt")))
        cancel = threading.Event()

        def interrupted(*args, **kwargs):
            cancel.set()
            raise merger.Cancelled("test cancellation")

        with patch.object(merger, "publish_stage_vrt", side_effect=interrupted):
            self.assertEqual(self.make_stage(cancel=cancel), 130)
        self.assertFalse(list(blocks.glob("*.vrt")))
        self.assertEqual(self.make_stage(), 0)
        self.assertTrue((blocks / "NASADEM_mosaic.vrt").exists())

    def test_single_block_stage_and_dry_run_have_no_vrt(self):
        self.raster(values=np.full((3, 3), 10))
        self.assertEqual(self.make_stage("--dry-run"), 0)
        self.assertFalse((self.root / "blocks").exists())
        self.assertEqual(self.make_stage(), 0)
        self.assertFalse(list((self.root / "blocks").glob("*.vrt")))

    def test_stage_vrt_excludes_below_minimum_blocks(self):
        for west in range(5):
            self.raster(f"tile{west}.tif", np.full((3, 3), 10), west=west)
        self.assertEqual(self.make_stage("--block-size", "2", "--min-tiles-per-block", "2"), 0)
        blocks = self.root / "blocks"
        state = self.state(blocks)
        expected = {name for name, record in state["blocks"].items() if record["status"] == "complete"}
        self.assertEqual(len(expected), 2)
        nodes = ET.parse(blocks / "NASADEM_mosaic.vrt").findall(".//SourceFilename")
        self.assertEqual({n.text for n in nodes}, expected)
        ds = gdal.Open(str(blocks / "NASADEM_mosaic.vrt"))
        self.assertEqual((ds.RasterXSize, ds.RasterYSize), (9, 5))
        merger.close(ds)

    def test_stage_vrt_publication_crash_resumes_without_rewriting_cogs(self):
        self.raster(values=np.full((3, 3), 10))
        self.raster("b.tif", np.full((3, 3), 10), west=1)
        replace = merger.os.replace

        def crash_after_publication(source, destination):
            replace(source, destination)
            if Path(destination).name == "NASADEM_mosaic.vrt":
                raise OSError("simulated crash after VRT publication")

        with patch.object(merger.os, "replace", side_effect=crash_after_publication):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                self.make_stage()
        blocks = self.root / "blocks"
        before = {p.name: p.stat().st_mtime_ns for p in blocks.glob("*.tif")}
        self.assertEqual(len(before), 2)
        self.assertEqual(self.make_stage(), 0)
        self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in blocks.glob("*.tif")})
        self.assertEqual(self.state(blocks)["status"], "complete")

    def test_rectangular_storage_estimate_and_duplicate_coverage(self):
        self.raster(values=np.full((3, 9), 10))
        self.raster("b.tif", values=np.full((3, 5), 10))
        inv = merger.read_results_inventory(self.inputs)
        block, = merger.plan_consolidation(inv, "NASADEM")
        estimate = merger.storage_estimate(block, True)
        self.assertEqual(estimate["estimated_source_base_bytes"], sum(s.base_bytes for s in inv.sources))
        self.assertEqual(merger.covered_pixels([s.dst for s in inv.sources]), 27)


if __name__ == "__main__":
    unittest.main()
