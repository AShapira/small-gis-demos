"""Check the portable launcher shipped by export, without a container engine."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demo


class WindowsExportTests(unittest.TestCase):
    def test_export_refreshes_launcher_before_hashing_and_archiving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, work = root / "bundle", root / "work"
            (bundle / "images").mkdir(parents=True)
            work.mkdir()
            launcher = b"# current Windows launcher\n"
            (root / "load-and-run.ps1").write_bytes(launcher)
            (bundle / "load-and-run.ps1").write_text("# old launcher\n")
            image_config = b'{"architecture":"amd64","os":"linux"}'
            image_id = hashlib.sha256(image_config).hexdigest()
            with tarfile.open(bundle / "images/geoserver.tar", "w") as archive:
                for name, content in {
                    "manifest.json": b'[{"Config":"config.json"}]',
                    "config.json": image_config,
                }.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
            (work / "lock.json").write_text(json.dumps({
                "snapshot": "261008", "images": {"geoserver": {"id": image_id}}
            }))
            cfg = {"images": {"geoserver": "test-image"}}

            class FakeEngine:
                def __init__(self, config):
                    pass

                def run(self, args, capture=False):
                    if args[0] == "ps":
                        return ""
                    if args[:2] == ["image", "inspect"]:
                        return json.dumps([{"Id": "sha256:" + image_id}])
                    raise AssertionError(f"Unexpected engine call: {args}")

            with patch.multiple(demo, ROOT=root, WORK=work, BUNDLE=bundle, Engine=FakeEngine):
                demo.export(cfg)
            exported = root / "exports/osm-basemap-261008.tar"
            with tarfile.open(exported) as archive:
                self.assertEqual(archive.extractfile("osm-basemap/load-and-run.ps1").read(), launcher)
                sums = archive.extractfile("osm-basemap/SHA256SUMS").read().decode()
                self.assertIn(hashlib.sha256(launcher).hexdigest() + "  load-and-run.ps1\n", sums)
            sidecar = json.loads(exported.with_suffix(".tar.json").read_text())
            self.assertEqual(sidecar["bytes"], exported.stat().st_size)
            self.assertEqual(sidecar["sha256"], hashlib.sha256(exported.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
