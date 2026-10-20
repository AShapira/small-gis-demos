#!/usr/bin/env python3
"""Download the official NASADEM VRT and two adjacent real-data test blocks.

No GDAL required. Keeps the full VRT, source tiles, a filtered VRT preserving
source order, and a SHA-256 manifest under an explicitly chosen cache directory.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://opentopography.s3.sdsc.edu/raster/NASADEM/"


def fetch(url, path):
    temporary = path.with_suffix(path.suffix + ".download")
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            digest = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(url, timeout=90) as response, temporary.open("wb") as out:
                expected = response.headers.get("Content-Length")
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
            if expected is not None and size != int(expected):
                raise OSError("Incomplete HTTP response")
            # Retain timestamps when an already cached input is byte-identical;
            # touching it would invalidate the merger's resume identity.
            same = False
            if path.is_file() and path.stat().st_size == size:
                with path.open("rb") as cached:
                    same = hashlib.file_digest(cached, "sha256").hexdigest() == digest.hexdigest()
            if same:
                temporary.unlink()
            else:
                temporary.replace(path)
            return {"url": url, "bytes": size, "sha256": digest.hexdigest()}
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Fixed, documented test selection: [20,40] E x [30,40] N.
    # Download whole source tiles with origins 20..39 E and 30..39 N.
    official = root / "NASADEM_be.vrt"
    vrt_record = fetch(BASE + official.name, official)
    tree = ET.parse(official)
    band = tree.getroot().find("VRTRasterBand")
    selected = []
    for node in list(band):
        if node.tag not in ("SimpleSource", "ComplexSource"):
            continue
        relative = node.findtext("SourceFilename")
        match = re.fullmatch(r"NASADEM_be/NASADEM_HGT_n(3[0-9])e(0[23][0-9])\.tif", relative or "")
        if match:
            selected.append(relative)
        else:
            band.remove(node)
    selected = list(dict.fromkeys(selected))
    if not selected:
        raise RuntimeError("Official VRT no longer matches the expected source layout")
    manifest_path = root / "download-manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    old_files = previous.get("files", {})
    if shutil.disk_usage(root).free < 12 * 1024 ** 3:
        raise RuntimeError("Need at least 12 GiB free for this test download and outputs")

    def download(relative):
        path = root / relative
        old = old_files.get(relative)
        if old and path.is_file() and path.stat().st_size == old["bytes"]:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest == old["sha256"]:
                return relative, old
        return relative, fetch(BASE + relative, path)

    manifest = {"bounds": [20, 30, 40, 40], "official_vrt": vrt_record,
                "files": {}, "complete": False}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, (relative, record) in enumerate(pool.map(download, selected), 1):
            manifest["files"][relative] = record
            temp = manifest_path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(manifest, indent=2) + "\n")
            temp.replace(manifest_path)
            print(f"{index}/{len(selected)} {relative} {record['bytes']:,} bytes", flush=True)
    subset_path = root / "NASADEM_e20-e40_n30-n40.vrt"
    subset_bytes = ET.tostring(tree.getroot(), encoding="UTF-8", xml_declaration=True)
    if not subset_path.is_file() or subset_path.read_bytes() != subset_bytes:
        subset_path.write_bytes(subset_bytes)
    manifest["complete"] = True
    manifest["total_bytes"] = sum(x["bytes"] for x in manifest["files"].values())
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"tiles": len(selected), "bytes": manifest["total_bytes"],
                      "directory": str(root)}), flush=True)


if __name__ == "__main__":
    main()
