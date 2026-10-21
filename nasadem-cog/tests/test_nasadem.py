"""Generated-data contracts, run with QGIS Python or system GDAL + NumPy."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
from osgeo import gdal, osr

SCRIPT = Path(__file__).resolve().parents[1] / "create_nasadem_cogs.py"
SPEC = importlib.util.spec_from_file_location("nasadem", SCRIPT)
merger = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merger
SPEC.loader.exec_module(merger)


class MergerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nasadem-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs with spaces"
        self.inputs.mkdir()
        self.vrt = self.inputs / "mosaic.vrt"
        self.output = self.root / "outputs"
        self.tiles = []
        self.n = 2
        merger.configure()
        crs = osr.SpatialReference()
        crs.ImportFromEPSG(4326)
        self.wkt = crs.ExportToWkt()
        merger.configure()

    def tile(self, west=0, south=0, values=None, name=None, missing=False, mask=None,
             simple=False, nodata=-32768, step_error=0):
        name = name or f"height_{len(self.tiles)}.tif"
        path = self.inputs / name
        size = self.n + 1
        if values is None:
            values = np.full((size, size), len(self.tiles) + 1, dtype="int16")
        values = np.asarray(values, dtype="int16")
        if not missing:
            ds = gdal.GetDriverByName("GTiff").Create(str(path), size, size, 1, gdal.GDT_Int16)
            ds.SetProjection(self.wkt)
            ds.SetGeoTransform((west - .5 / self.n, (1 + step_error) / self.n, 0,
                                south + 1 + .5 / self.n, 0, -1 / self.n))
            ds.SetMetadataItem("AREA_OR_POINT", "Point")
            band = ds.GetRasterBand(1)
            band.SetNoDataValue(nodata)
            band.SetUnitType("m")
            band.WriteArray(values)
            if mask is not None:
                band.CreateMaskBand(0)
                band.GetMaskBand().WriteArray(np.asarray(mask, dtype="uint8") * 255)
            band = None
            merger.close(ds)
            ds = None
        self.tiles.append((path, west, south, simple, mask is not None))
        return path

    def write_vrt(self):
        n = self.n
        root = ET.Element("VRTDataset", rasterXSize=str(360 * n + 1), rasterYSize=str(180 * n + 1))
        ET.SubElement(root, "SRS").text = self.wkt
        ET.SubElement(root, "GeoTransform").text = f"{-180 - .5/n},{1/n},0,{90+.5/n},0,{-1/n}"
        band = ET.SubElement(root, "VRTRasterBand", dataType="Int16", band="1")
        ET.SubElement(band, "NoDataValue").text = "-32768"
        for path, west, south, simple, mask in self.tiles:
            node = ET.SubElement(band, "SimpleSource" if simple else "ComplexSource")
            ET.SubElement(node, "SourceFilename", relativeToVRT="1").text = path.name
            ET.SubElement(node, "SourceBand").text = "1"
            ET.SubElement(node, "SrcRect", xOff="0", yOff="0", xSize=str(n+1), ySize=str(n+1))
            ET.SubElement(node, "DstRect", xOff=str((west+180)*n), yOff=str((89-south)*n),
                          xSize=str(n+1), ySize=str(n+1))
            if not simple and not mask:
                ET.SubElement(node, "NODATA").text = "-32768"
            if mask:
                ET.SubElement(node, "UseMaskBand").text = "true"
        ET.ElementTree(root).write(self.vrt, encoding="UTF-8", xml_declaration=True)
        return root

    def args(self, *extra):
        return merger.parser().parse_args(["--vrt-file", str(self.vrt), "--output-dir",
                                           str(self.output), "--workers", "2", *extra])

    def run_merge(self, *extra, cancel=None):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = merger.run(self.args(*extra), cancel=cancel)
        if result == 1:
            print(output.getvalue(), file=sys.stderr)
        return result

    def state(self):
        return json.loads((self.output / "state.json").read_text())

    def sample(self, path, lon, lat):
        with merger.opened(path) as ds:
            gt = ds.GetGeoTransform()
            x = round((lon - gt[0]) / gt[1] - .5)
            y = round((lat - gt[3]) / gt[5] - .5)
            return ds.GetRasterBand(1).ReadAsArray(x, y, 1, 1)[0, 0]

    def test_defaults_and_dry_run_do_not_write(self):
        self.tile(name="גובה tile.tif")
        self.write_vrt()
        args = self.args()
        self.assertEqual(merger.parser().parse_args(["--vrt-file", "a", "--output-dir", "b"]).workers, 8)
        self.assertEqual(args.block_size, 10)
        self.assertFalse(args.verify)
        self.assertFalse(args.build_overviews)
        self.assertEqual(args.disk_space_policy, "warn")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(merger.run(self.args("--dry-run")), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["blocks"][0]["dimensions"], [21, 21])
        self.assertEqual(report["disk_space_policy"], "warn")
        estimate = report["blocks"][0]
        self.assertEqual(estimate["storage_estimate_method"], "source-compression-v1")
        self.assertGreater(estimate["estimated_output_bytes"], 0)
        self.assertGreater(estimate["storage_reservation_bytes"],
                           estimate["estimated_output_bytes"] + estimate["estimated_temporary_bytes"])
        self.assertFalse(self.output.exists())

    def test_legacy_block_sizes_quadrants_and_zero(self):
        for west, south in ((0, 0), (-1, -1), (-20, -10), (39, 39)):
            self.tile(west, south)
        self.write_vrt()
        inv = merger.read_inventory(self.vrt)
        for size in (5, 10, 20):
            with self.subTest(size=size):
                blocks = merger.plan_blocks(inv, size, 1)
                for block in blocks:
                    self.assertEqual(block.window[2:], (size * self.n + 1,) * 2)
                    self.assertEqual(block.bounds[0] % size, 0)
                    self.assertEqual(block.bounds[1] % size, 0)
        self.assertIn("NASADEM_S10W020_S01W011.tif",
                      [b.name for b in merger.plan_blocks(inv, 10, 1)])

    def test_all_block_sizes_cover_symmetric_world_axes(self):
        # Cross-shaped strips exercise every nominal longitude/latitude without
        # creating a global source collection or allocating full-sized rasters.
        origins = {(west, 0) for west in range(-180, 180)}
        origins.update((0, south) for south in range(-90, 90))
        n = self.n
        inv = SimpleNamespace(
            transform=(-180 - .5/n, 1/n, 0, 90 + .5/n, 0, -1/n), intervals=n,
            sources=[SimpleNamespace(path=Path(f"tile_{west}_{south}.tif"), west=west,
                                     south=south, dst=((west+180)*n, (89-south)*n, n+1, n+1))
                     for west, south in sorted(origins)])
        sizes = (1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 18, 20, 24,
                 30, 36, 40, 45, 60, 72, 90, 120, 180, 360)
        expected_heights = {30: [30]*6, 45: [45]*4, 60: [30, 60, 60, 30],
                            90: [90, 90], 120: [30, 120, 30], 180: [90, 90], 360: [180]}
        for size in sizes:
            with self.subTest(size=size):
                self.assertEqual(self.args("--block-size", str(size)).block_size, size)
                blocks = merger.plan_blocks(inv, size, 1)
                self.assertEqual(len({b.name for b in blocks}), len(blocks))
                self.assertEqual(sum(b.tile_count for b in blocks), len(origins))
                xs = sorted({(b.bounds[0], b.bounds[2]) for b in blocks})
                ys = sorted({(b.bounds[1], b.bounds[3]) for b in blocks})
                self.assertEqual(len(xs), 360 // size)
                self.assertTrue(all(east-west == size for west, east in xs))
                for segments, low, high in ((xs, -180, 180), (ys, -90, 90)):
                    self.assertEqual((segments[0][0], segments[-1][1]), (low, high))
                    self.assertTrue(all(0 < end-start <= size for start, end in segments))
                    self.assertTrue(all(a[1] == b[0] for a, b in zip(segments, segments[1:])))
                    self.assertEqual(segments, sorted((-end, -start) for start, end in segments))
                if size in expected_heights:
                    self.assertEqual([north-south for south, north in ys], expected_heights[size])
                for block in blocks:
                    west, south, east, north = block.bounds
                    self.assertEqual(block.window[2:], ((east-west)*n+1, (north-south)*n+1))
                    transform = merger.block_transform(inv, block)
                    self.assertEqual((transform[0]+.5/n, transform[3]-.5/n), (west, north))

    def test_invalid_block_sizes_fail_before_inventory_or_output(self):
        for value in ("0", "-10", "7", "11", "359", "361", "720", "1.5", "30.0", "abc"):
            with self.subTest(cli=value), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as raised:
                    self.args("--block-size", value)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("positive integer divisor of 360", errors.getvalue())
        for value in (0, -10, 7, 361, 720, 1.5, 30.0, True, None, "30"):
            with self.subTest(direct=value):
                with self.assertRaisesRegex(merger.InputError, "positive integer divisor of 360"):
                    merger.plan_blocks(None, value, 1)
                args = self.args()
                args.block_size = value
                with self.assertRaisesRegex(merger.InputError, "positive integer divisor of 360"):
                    merger.run(args)
        self.assertFalse(self.output.exists())

    def test_legacy_polar_bounds_and_resume(self):
        for west, south in ((-180, -90), (0, 0), (179, 89)):
            self.tile(west, south)
        self.write_vrt()
        inv = merger.read_inventory(self.vrt)
        expected = {
            5: [(-180, -90, -175, -85), (0, 0, 5, 5), (175, 85, 180, 90)],
            10: [(-180, -90, -170, -80), (0, 0, 10, 10), (170, 80, 180, 90)],
            20: [(-180, -90, -160, -80), (0, 0, 20, 20), (160, 80, 180, 90)],
        }
        for size, bounds in expected.items():
            with self.subTest(size=size):
                self.assertEqual([b.bounds for b in merger.plan_blocks(inv, size, 1)], bounds)
                self.output = self.root / f"outputs_{size}"
                self.assertEqual(self.run_merge("--block-size", str(size), "--verify"), 0)
                paths = sorted(self.output.glob("*.tif"))
                before = [(p.stat().st_mtime_ns, p.read_bytes()) for p in paths]
                self.assertEqual(self.run_merge("--block-size", str(size), "--verify"), 0)
                self.assertEqual([(p.stat().st_mtime_ns, p.read_bytes()) for p in paths], before)
                self.assertTrue(all(b["action"] == "skipped" for b in self.state()["blocks"].values()))

    def test_whole_world_cog_values_gaps_overviews_and_resume(self):
        # Samples on both dateline endpoints, both poles, and in every quadrant.
        samples = [(-180, -90, -7), (-180, 89, 0), (179, -90, 42), (179, 89, 999),
                   (-1, -1, -20), (0, 0, 100)]
        for west, south, value in samples:
            self.tile(west, south, np.full((3, 3), value))
        self.write_vrt()
        inv = merger.read_inventory(self.vrt)
        blocks = merger.plan_blocks(inv, 360, 1)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].bounds, (-180, -90, 180, 90))
        self.assertEqual(blocks[0].tile_count, len(samples))
        self.assertFalse(merger.plan_blocks(inv, 360, len(samples)+1)[0].eligible)
        options = ("--block-size", "360", "--verify", "--build-overviews")
        self.assertEqual(self.run_merge(*options), 0)
        paths = list(self.output.glob("*.tif"))
        self.assertEqual([p.name for p in paths], ["NASADEM_S90W180_N89E179.tif"])
        path = paths[0]
        with merger.opened(path) as ds:
            self.assertEqual((ds.RasterXSize, ds.RasterYSize), (721, 361))
            self.assertEqual(ds.GetRasterBand(1).GetOverviewCount(), 1)
            self.assertEqual(ds.GetGeoTransform(), (-180.25, .5, 0, 90.25, 0, -.5))
        for west, south, value in samples:
            self.assertEqual(self.sample(path, west+.5, south+.5), value)
        for lon, lat, value in ((-180, -90, -7), (-180, 90, 0), (180, -90, 42),
                                (180, 90, 999), (0, 0, 100), (45, 45, -32768)):
            self.assertEqual(self.sample(path, lon, lat), value)
        record = next(iter(self.state()["blocks"].values()))
        self.assertEqual(record["verification"]["pixels_checked"], 721*361)
        self.assertTrue(record["verification"]["full_cog_check"])
        before = path.stat().st_mtime_ns, path.read_bytes()
        self.assertEqual(self.run_merge(*options), 0)
        self.assertEqual(next(iter(self.state()["blocks"].values()))["action"], "skipped")
        self.assertEqual((path.stat().st_mtime_ns, path.read_bytes()), before)

    def test_whole_world_cog_expands_regional_vrt(self):
        self.tile(20, 30)
        self.tile(21, 30)
        root = self.write_vrt()
        root.set("rasterXSize", "5")
        root.set("rasterYSize", "3")
        root.find("GeoTransform").text = "19.75,0.5,0,31.25,0,-0.5"
        for index, rect in enumerate(root.findall(".//DstRect")):
            rect.set("xOff", str(index*2))
            rect.set("yOff", "0")
        ET.ElementTree(root).write(self.vrt)
        inv = merger.read_inventory(self.vrt)
        block, = merger.plan_blocks(inv, 360, 1)
        self.assertEqual(block.window, (-400, -118, 721, 361))
        self.assertEqual(self.run_merge("--block-size", "360", "--verify"), 0)
        path, = self.output.glob("*.tif")
        self.assertEqual(self.sample(path, 20.5, 30.5), 1)
        self.assertEqual(self.sample(path, 21, 30.5), 2)
        self.assertEqual(self.sample(path, 21.5, 30.5), 2)
        for lon, lat in ((-180, -90), (-180, 90), (180, -90), (180, 90), (0, 0)):
            self.assertEqual(self.sample(path, lon, lat), -32768)
        self.assertEqual(next(iter(self.state()["blocks"].values()))["verification"]["pixels_checked"],
                         721*361)

    def test_shared_edges_last_valid_source_and_full_pixel_count(self):
        self.tile(9, 0, np.full((3, 3), 12))
        values = np.full((3, 3), -9)
        values[1, 0] = -32768
        self.tile(10, 0, values)
        self.write_vrt()
        self.assertEqual(self.run_merge("--verify"), 0)
        paths = sorted(self.output.glob("*.tif"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertEqual(self.sample(path, 10, 1), -9)
            self.assertEqual(self.sample(path, 10, .5), 12)
        for record in self.state()["blocks"].values():
            self.assertEqual(record["tile_count"], 1)
            self.assertEqual(len(record["sources"]), 2)
            self.assertEqual(record["verification"]["pixels_checked"], 441)

    def test_zero_negative_and_uncovered_pixels(self):
        self.tile(values=[[0, -1, 2], [3, -32768, 5], [6, 7, 8]])
        self.write_vrt()
        self.assertEqual(self.run_merge("--verify"), 0)
        path = next(self.output.glob("*.tif"))
        self.assertEqual(self.sample(path, 0, 1), 0)
        self.assertEqual(self.sample(path, .5, 1), -1)
        self.assertEqual(self.sample(path, 5, 5), -32768)

    def test_simple_source_nodata_overwrites_lower_source(self):
        self.tile(values=np.full((3, 3), 7))
        self.tile(values=np.full((3, 3), -32768), simple=True)
        self.write_vrt()
        self.assertEqual(self.run_merge("--verify"), 0)
        self.assertEqual(self.sample(next(self.output.glob("*.tif")), .5, .5), -32768)

    def test_explicit_source_mask(self):
        self.tile(values=np.full((3, 3), 4))
        mask = np.ones((3, 3))
        mask[1, 1] = 0
        self.tile(values=np.full((3, 3), 9), mask=mask)
        self.write_vrt()
        self.assertEqual(self.run_merge("--verify"), 0)
        self.assertEqual(self.sample(next(self.output.glob("*.tif")), .5, .5), 4)

    def test_explicit_nodata_takes_priority_over_mask(self):
        self.tile(values=np.full((3, 3), 4))
        mask = np.ones((3, 3))
        mask[1, 1] = 0
        self.tile(values=np.full((3, 3), 9), mask=mask)
        root = self.write_vrt()
        ET.SubElement(root.findall(".//ComplexSource")[-1], "NODATA").text = "-32768"
        ET.ElementTree(root).write(self.vrt)
        self.assertEqual(self.run_merge("--verify"), 0)
        path = next(self.output.glob("*.tif"))
        self.assertEqual(self.sample(path, .5, .5), 9)
        with gdal.Open(str(self.vrt)) as original:
            original_value = original.ReadAsArray(361, 179, 1, 1)[0, 0]
        self.assertEqual(self.sample(path, .5, .5), original_value)

    def test_partial_source_window_and_absolute_path(self):
        self.tile(values=np.arange(9).reshape(3, 3))
        root = self.write_vrt()
        node = root.find(".//ComplexSource")
        filename = node.find("SourceFilename")
        filename.text = str(self.tiles[0][0])
        filename.set("relativeToVRT", "0")
        node.find("SrcRect").set("xOff", "1")
        node.find("SrcRect").set("xSize", "2")
        node.find("DstRect").set("xOff", "361")
        node.find("DstRect").set("xSize", "2")
        ET.ElementTree(root).write(self.vrt)
        self.assertEqual(self.run_merge("--verify"), 0)
        path = next(self.output.glob("*.tif"))
        self.assertEqual(self.sample(path, 0, 1), -32768)
        self.assertEqual(self.sample(path, .5, 1), 1)

    def test_missing_source_is_reported_and_filled(self):
        self.tile()
        self.tile(1, 0, missing=True)
        self.write_vrt()
        self.assertEqual(self.run_merge("--verify"), 0)
        state = self.state()
        self.assertEqual(state["status"], "complete_with_missing")
        self.assertEqual(len(state["missing_sources"]), 1)
        self.assertEqual(self.sample(next(self.output.glob("*.tif")), 1.5, .5), -32768)

    def test_filename_filter_minimum_and_no_matches(self):
        self.tile(0, 0, name="KEEP.tif")
        self.tile(1, 0, name="drop.tif")
        self.write_vrt()
        inv = merger.read_inventory(self.vrt, "keep")
        self.assertEqual(len(inv.sources), 1)
        with self.assertRaisesRegex(merger.InputError, "No usable"):
            merger.read_inventory(self.vrt, "nothing")
        self.assertEqual(self.run_merge("--min-tiles-per-block", "3"), 0)
        self.assertFalse(list(self.output.glob("*.tif")))
        self.assertEqual(next(iter(self.state()["blocks"].values()))["status"], "below_minimum")

    def test_corrupt_source_is_not_treated_as_missing(self):
        path = self.tile()
        self.write_vrt()
        path.write_bytes(b"broken")
        with self.assertRaisesRegex(merger.InputError, "Unreadable"):
            self.run_merge()

    def test_invalid_vrt_mapping_rejected(self):
        self.tile()
        root = self.write_vrt()
        root.find(".//DstRect").set("xOff", "1")
        ET.ElementTree(root).write(self.vrt)
        with self.assertRaisesRegex(merger.InputError, "georeferencing"):
            self.run_merge()

    def test_accumulated_pixel_spacing_drift_is_rejected(self):
        self.n = 64
        self.tile(step_error=1e-6)
        self.write_vrt()
        with self.assertRaisesRegex(merger.InputError, "pixel grid"):
            self.run_merge()

    def test_scaled_and_derived_vrt_rejected(self):
        self.tile()
        root = self.write_vrt()
        root.find(".//DstRect").set("xSize", "4")
        ET.ElementTree(root).write(self.vrt)
        with self.assertRaisesRegex(merger.InputError, "Resampled"):
            self.run_merge()
        root = self.write_vrt()
        ET.SubElement(root.find(".//ComplexSource"), "ScaleRatio").text = "2"
        ET.ElementTree(root).write(self.vrt)
        with self.assertRaisesRegex(merger.InputError, "transformation"):
            self.run_merge()

    def test_wrong_crs_nodata_and_rotation_rejected(self):
        path = self.tile()
        self.write_vrt()
        with gdal.Open(str(path), gdal.GA_Update) as ds:
            ds.GetRasterBand(1).SetNoDataValue(-9999)
        with self.assertRaises(merger.InputError):
            self.run_merge()
        self.tiles = []
        path = self.tile(name="rotated.tif")
        self.write_vrt()
        with gdal.Open(str(path), gdal.GA_Update) as ds:
            ds.SetGeoTransform((0, .5, .1, 1, 0, -.5))
        with self.assertRaises(merger.InputError):
            self.run_merge()
        root = self.write_vrt()
        root.find("SRS").text = "EPSG:3857"
        ET.ElementTree(root).write(self.vrt)
        with self.assertRaisesRegex(merger.InputError, "EPSG:4326"):
            self.run_merge()

    def test_resume_skip_and_upgrade_verification(self):
        self.tile()
        self.write_vrt()
        self.assertEqual(self.run_merge(), 0)
        path = next(self.output.glob("*.tif"))
        before = path.stat().st_mtime_ns
        self.assertEqual(self.run_merge("--verify", "--workers", "1"), 0)
        record = next(iter(self.state()["blocks"].values()))
        self.assertEqual(record["action"], "skipped")
        self.assertEqual(record["verification"]["pixels_checked"], 441)
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_repeated_download_of_identical_vrt_preserves_resume(self):
        import fetch_public_subset
        self.tile()
        self.write_vrt()
        self.run_merge()
        payload = self.vrt.read_bytes()
        before = self.vrt.stat().st_mtime_ns

        def response(*args, **kwargs):
            stream = io.BytesIO(payload)
            stream.headers = {"Content-Length": str(len(payload))}
            return stream

        with patch.object(fetch_public_subset.urllib.request, "urlopen", side_effect=response):
            fetch_public_subset.fetch("https://example.invalid/test.vrt", self.vrt)
        self.assertEqual(self.vrt.stat().st_mtime_ns, before)
        self.assertEqual(self.run_merge("--verify"), 0)
        self.assertEqual(next(iter(self.state()["blocks"].values()))["action"], "skipped")

    def test_resume_refuses_changed_inputs_and_options(self):
        path = self.tile()
        self.write_vrt()
        self.run_merge()
        with self.assertRaisesRegex(merger.InputError, "different inputs/options"):
            self.run_merge("--build-overviews")
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
        with self.assertRaisesRegex(merger.InputError, "different inputs/options"):
            self.run_merge()

    def test_crash_before_checkpoint_adopts_only_after_full_verification(self):
        self.tile()
        self.write_vrt()
        self.run_merge()
        state = self.state()
        name = next(iter(state["blocks"]))
        state["blocks"][name]["status"] = "running"
        merger.atomic_json(self.output / "state.json", state)
        self.assertEqual(self.run_merge(), 0)
        record = self.state()["blocks"][name]
        self.assertEqual(record["action"], "adopted")
        self.assertEqual(record["verification"]["mode"], "every_pixel")

    def test_preexisting_unowned_directory_refused(self):
        self.tile()
        self.write_vrt()
        self.output.mkdir()
        other = self.output / "notes.txt"
        other.write_text("keep")
        with self.assertRaisesRegex(merger.InputError, "must be empty"):
            self.run_merge()
        self.assertEqual(other.read_text(), "keep")

    def test_unrelated_tiff_and_lock_are_rejected(self):
        self.tile()
        self.write_vrt()
        self.run_merge()
        with merger.directory_lock(self.output):
            with self.assertRaisesRegex(merger.InputError, "Another merger"):
                self.run_merge()
        (self.output / "unrelated.tif").write_bytes(b"keep")
        with self.assertRaisesRegex(merger.InputError, "Unrelated"):
            self.run_merge()

    def test_strict_disk_space_failure_checkpoints(self):
        self.tile()
        self.write_vrt()
        usage = type("Usage", (), {"free": 0})()
        with patch.object(merger.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(merger.RunError, "Insufficient disk"):
                self.run_merge("--disk-space-policy", "error")
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("Insufficient disk", (self.output / "errors.log").read_text())
        self.assertFalse(list(self.output.glob("*.tif")))

    def test_low_space_warns_continues_and_allows_strict_resume(self):
        self.tile(0, 0)
        self.tile(10, 0)
        self.write_vrt()
        usage = SimpleNamespace(free=0)
        with patch.object(merger.shutil, "disk_usage", return_value=usage):
            self.assertEqual(self.run_merge("--verify"), 0)
            paths = sorted(self.output.glob("*.tif"))
            self.assertEqual(len(paths), 2)
            before = [(p.stat().st_mtime_ns, p.read_bytes()) for p in paths]
            self.assertEqual(self.state()["disk_space_policy"], "warn")
            self.assertEqual(self.state()["status"], "complete")
            self.assertEqual((self.output / "run.log").read_text().count("WARNING:"), 2)
            self.assertEqual((self.output / "errors.log").read_text(), "")
            # Policy is operational, so changing it does not invalidate outputs.
            self.assertEqual(self.run_merge("--verify", "--disk-space-policy", "error"), 0)
            self.assertEqual(self.state()["disk_space_policy"], "error")
            self.assertEqual([(p.stat().st_mtime_ns, p.read_bytes()) for p in paths], before)
            self.assertTrue(all(b["action"] == "skipped" for b in self.state()["blocks"].values()))

    def test_disk_space_query_failure_obeys_policy(self):
        self.tile()
        self.write_vrt()
        with patch.object(merger.shutil, "disk_usage", side_effect=OSError("free-space query unavailable")):
            self.assertEqual(self.run_merge(), 0)
            self.assertIn("WARNING: Cannot check disk free space", (self.output / "run.log").read_text())
            self.output = self.root / "strict-output"
            with self.assertRaisesRegex(merger.RunError, "Cannot check disk free space"):
                self.run_merge("--disk-space-policy", "error")
            self.assertEqual(self.state()["status"], "failed")

    def test_actual_write_failure_still_fails_in_warn_mode(self):
        self.tile()
        self.write_vrt()
        with patch.object(merger.shutil, "disk_usage", return_value=SimpleNamespace(free=0)), (
                patch.object(merger.gdal, "Translate", side_effect=OSError(28, "No space left on device"))):
            self.assertEqual(self.run_merge(), 1)
        self.assertEqual(self.state()["status"], "failed")
        self.assertFalse(list(self.output.glob("*.tif")))
        self.assertIn("No space left on device", (self.output / "errors.log").read_text())

    def test_storage_estimate_deduplicates_and_weights_source_windows(self):
        self.tile()
        root = self.write_vrt()
        inv = merger.read_inventory(self.vrt)
        block, = merger.plan_blocks(inv, 10, 1)
        original = merger.storage_estimate(block, False)
        self.assertEqual(original["estimated_source_base_bytes"], inv.sources[0].base_bytes)
        node = root.find(".//ComplexSource")
        root.find("VRTRasterBand").append(ET.fromstring(ET.tostring(node)))
        ET.ElementTree(root).write(self.vrt)
        inv = merger.read_inventory(self.vrt)
        block, = merger.plan_blocks(inv, 10, 1)
        self.assertEqual(merger.storage_estimate(block, False), original)
        # Keep only the eastern two columns of the same source, in both references.
        for node in root.findall(".//ComplexSource"):
            node.find("SrcRect").set("xOff", "1")
            node.find("SrcRect").set("xSize", "2")
            node.find("DstRect").set("xOff", "361")
            node.find("DstRect").set("xSize", "2")
        ET.ElementTree(root).write(self.vrt)
        inv = merger.read_inventory(self.vrt)
        block, = merger.plan_blocks(inv, 10, 1)
        partial = merger.storage_estimate(block, False)["estimated_source_base_bytes"]
        self.assertAlmostEqual(partial / original["estimated_source_base_bytes"], 2/3, delta=.01)

    def test_source_storage_estimate_accounts_for_existing_overviews(self):
        self.n = 64
        path = self.tile()
        ds = gdal.Open(str(path), gdal.GA_Update)
        ds.BuildOverviews("NEAREST", [2, 4])
        merger.close(ds)
        ds = None
        before = path.stat().st_mtime_ns, path.read_bytes()
        self.write_vrt()
        inv = merger.read_inventory(self.vrt)
        self.assertGreater(inv.sources[0].base_bytes, 0)
        self.assertLess(inv.sources[0].base_bytes, path.stat().st_size)
        block, = merger.plan_blocks(inv, 10, 1)
        base = merger.storage_estimate(block, False)
        pyramid = merger.storage_estimate(block, True)
        self.assertGreater(pyramid["estimated_output_bytes"], base["estimated_output_bytes"])
        self.assertGreater(pyramid["estimated_temporary_bytes"], base["estimated_temporary_bytes"])
        self.assertEqual((path.stat().st_mtime_ns, path.read_bytes()), before)

    def test_source_change_during_conversion_prevents_publication(self):
        path = self.tile()
        self.write_vrt()
        original = merger.gdal.Translate

        def changed_source(*args, **kwargs):
            result = original(*args, **kwargs)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            return result

        with patch.object(merger.gdal, "Translate", side_effect=changed_source):
            self.assertEqual(self.run_merge("--verify"), 1)
        self.assertFalse(list(self.output.glob("*.tif")))
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("Input changed", (self.output / "errors.log").read_text())

    def test_disk_reservation_limits_concurrency(self):
        self.tile(0, 0)
        self.tile(10, 0)
        self.write_vrt()
        inv = merger.read_inventory(self.vrt)
        blocks = merger.plan_blocks(inv, 10, 1)
        usage = type("Usage", (), {"free": merger.HEADROOM + merger.reservation(blocks[0], False)})()
        original = merger.process_block
        counts = {"active": 0, "maximum": 0}
        lock = threading.Lock()

        def tracked(*args, **kwargs):
            with lock:
                counts["active"] += 1
                counts["maximum"] = max(counts["active"], counts["maximum"])
            try:
                time.sleep(.03)
                return original(*args, **kwargs)
            finally:
                with lock:
                    counts["active"] -= 1

        with patch.object(merger.shutil, "disk_usage", return_value=usage), (
                patch.object(merger, "process_block", side_effect=tracked)):
            self.assertEqual(self.run_merge("--verify"), 0)
        self.assertEqual(counts["maximum"], 1)

    def test_cancel_conversion_then_resume(self):
        self.tile()
        self.write_vrt()
        cancel = threading.Event()

        def interrupted(*args, **kwargs):
            cancel.set()
            raise RuntimeError("User interrupted")

        with patch.object(merger.gdal, "Translate", side_effect=interrupted):
            self.assertEqual(self.run_merge(cancel=cancel), 130)
        self.assertEqual(self.state()["status"], "cancelled")
        self.assertFalse(list(self.output.glob("*.tif")))
        self.assertEqual(self.run_merge("--verify"), 0)

    def test_cancel_verification_does_not_publish(self):
        self.tile()
        self.write_vrt()
        cancel = threading.Event()
        original = merger.verify_pixels

        def interrupted(*args, **kwargs):
            cancel.set()
            return original(*args, **kwargs)

        with patch.object(merger, "verify_pixels", side_effect=interrupted):
            self.assertEqual(self.run_merge("--verify", cancel=cancel), 130)
        self.assertFalse(list(self.output.glob("*.tif")))

    def test_corrupted_pixel_detected_by_independent_oracle(self):
        self.tile()
        self.write_vrt()
        self.run_merge()
        path = next(self.output.glob("*.tif"))
        ds = gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_UPDATE,
                         open_options=["IGNORE_COG_LAYOUT_BREAK=YES"])
        ds.GetRasterBand(1).WriteArray(np.array([[111]], dtype="int16"), 0, 0)
        merger.close(ds)
        ds = None
        inv = merger.read_inventory(self.vrt)
        block = merger.plan_blocks(inv, 10, 1)[0]
        with merger.opened(path) as ds:
            with self.assertRaisesRegex(merger.RunError, "Pixel/NoData"):
                merger.verify_pixels(ds, inv, block, threading.Event(), lambda _: None)

    def test_overviews_and_source_preservation(self):
        import hashlib
        self.n = 64
        path = self.tile(values=np.arange(65 * 65).reshape(65, 65))
        self.write_vrt()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(self.run_merge("--verify", "--build-overviews"), 0)
        output = next(self.output.glob("*.tif"))
        with merger.opened(output) as ds:
            band = ds.GetRasterBand(1)
            self.assertEqual(band.GetOverviewCount(), 1)
            overview = band.GetOverview(0)
            self.assertIn(overview.XSize, (320, 321))
            self.assertEqual(overview.XSize, overview.YSize)
            # Independent base-image resampling: explicitly prohibit reuse of
            # the output overview that we are testing.
            reference = gdal.Translate("", ds, format="MEM", width=overview.XSize,
                                       height=overview.YSize, resampleAlg="average", overviewLevel="NONE")
            np.testing.assert_array_equal(band.GetOverview(0).ReadAsArray(),
                                          reference.GetRasterBand(1).ReadAsArray())
            merger.close(reference)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_native_resolution_plan(self):
        # Full-sized source fixture; dry run verifies native 36,001 pixel block sizing.
        self.n = 3600
        self.tile(34, 31)
        self.write_vrt()
        inv = merger.read_inventory(self.vrt)
        block = merger.plan_blocks(inv, 10, 1)[0]
        self.assertEqual(block.window[2:], (36001, 36001))
        self.assertEqual(block.bounds, (30, 30, 40, 40))
        for size, bounds in {
            30: (30, 30, 60, 60), 45: (0, 0, 45, 45), 60: (0, 0, 60, 60),
            90: (0, 0, 90, 90), 120: (-60, -60, 60, 60),
            180: (0, 0, 180, 90), 360: (-180, -90, 180, 90),
        }.items():
            with self.subTest(size=size):
                block, = merger.plan_blocks(inv, size, 1)
                self.assertEqual(block.bounds, bounds)
                west, south, east, north = bounds
                self.assertEqual(block.window[2:], ((east-west)*3600+1, (north-south)*3600+1))
                if size == 360:
                    # One source plus compressed NoData padding, not a raw global raster.
                    estimate = merger.storage_estimate(block, False)
                    self.assertLess(estimate["estimated_output_bytes"], 10 * 1024**3)

    def test_old_source_overviews_are_not_used_for_new_averages(self):
        self.n = 64
        path = self.tile(values=np.full((65, 65), 10))
        ds = gdal.Open(str(path), gdal.GA_Update)
        ds.BuildOverviews("NEAREST", [2])
        overview = ds.GetRasterBand(1).GetOverview(0)
        overview.Fill(999)
        overview = None
        merger.close(ds)
        ds = None
        self.write_vrt()
        self.assertEqual(self.run_merge("--verify", "--build-overviews"), 0)
        with merger.opened(next(self.output.glob("*.tif"))) as result:
            values = result.GetRasterBand(1).GetOverview(0).ReadAsArray()
            self.assertTrue(np.all(values[values != -32768] == 10))

    @unittest.skipUnless(os.name == "nt", "Native Windows console signal test")
    def test_real_windows_ctrl_c_and_cli_resume(self):
        self.n = 3600
        self.tile(34, 31)
        self.write_vrt()
        command = [sys.executable, "-B", str(SCRIPT.parent / "tests" / "with_qgis.py"), "cli",
                   "--vrt-file", str(self.vrt), "--output-dir", str(self.output),
                   "--verify", "--workers", "1"]
        helper = self.root / "send_ctrl_c.py"
        helper.write_text(
            "import ctypes, sys, time\n"
            "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
            "k.FreeConsole()\n"
            "if not k.AttachConsole(int(sys.argv[1])): raise ctypes.WinError(ctypes.get_last_error())\n"
            "if not k.SetConsoleCtrlHandler(None, True): raise ctypes.WinError(ctypes.get_last_error())\n"
            "if not k.GenerateConsoleCtrlEvent(0, 0): raise ctypes.WinError(ctypes.get_last_error())\n"
            "time.sleep(.3)\n"
            "k.FreeConsole()\n"
        )
        with (self.root / "child.log").open("w") as output:
            child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NEW_CONSOLE)
            try:
                deadline = time.monotonic() + 30
                while child.poll() is None and time.monotonic() < deadline:
                    if any(self.output.glob(".incomplete/*/candidate.tif")):
                        break
                    time.sleep(.01)
                self.assertIsNone(child.poll(), "Child must still be generating")
                sent = subprocess.run([sys.executable, "-B", str(helper), str(child.pid)],
                                      capture_output=True, timeout=10)
                self.assertEqual(sent.returncode, 0, sent.stderr)
                self.assertEqual(child.wait(timeout=30), 130)
            finally:
                if child.poll() is None:
                    child.terminate()  # Only the isolated test process created above.
                    child.wait(timeout=10)
        self.assertEqual(self.state()["status"], "cancelled")
        self.assertFalse(list(self.output.glob("*.tif")))
        # Resume without changing data; tests skip/upgrade on small fixtures.
        completed = subprocess.run(command[:-3], capture_output=True, timeout=90)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(self.state()["status"], "complete")

    def test_preflight_cancellation(self):
        self.tile()
        self.write_vrt()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(merger.Cancelled):
            self.run_merge(cancel=cancel)
        self.assertFalse(self.output.exists())

    def test_invalid_worker_count(self):
        with self.assertRaisesRegex(merger.InputError, "positive"):
            merger.run(self.args("--workers", "0"))


if __name__ == "__main__":
    unittest.main()
