#!/usr/bin/env python3
"""Test existing NASADEM COGs through stock Kartoza 2.28.0 and private local S3.

Requires Podman, requests, boto3, numpy and Pillow. All test containers are new,
use their own writable layers, and are stopped on completion. No existing
server, bucket, catalog or volume is changed. Raw evidence is private/ignored.
"""
import argparse
import base64
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import secrets
import statistics
import subprocess
import time
import traceback
import uuid
import zipfile

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
import numpy as np
from PIL import Image
import requests

HERE = Path(__file__).resolve().parent
GEOSERVER_IMAGE = "docker.io/kartoza/geoserver@sha256:59cd5984bd4e6073dc07432ddb06f3e91e324fc32d55b304eaa5877cb7622667"
MINIO_IMAGE = "docker.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
GDAL_IMAGE = "ghcr.io/osgeo/gdal@sha256:1e450ff0e12140dc72c2ad6ff3978c96ea910187ec3ec4281a814f470606412c"
POSTGIS_IMAGE = "docker.io/postgis/postgis@sha256:01a6a70e41e6c4467c8f55f6063555ed72db2d6662cd0d571040d42eadaeb6f6"
LIB = "/usr/local/tomcat/webapps/geoserver/WEB-INF/lib"
BUCKET = "nasadem-test"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def summarize_audit(records, keys):
    java_reads = [row for row in records
                  if (row.get("user_agent") or "").startswith("aws-sdk-java/")
                  and row.get("api", {}).get("name") == "GetObject"]
    data_reads = [row for row in java_reads if row["api"].get("object") in keys]
    assert data_reads, "No GeoServer object reads recorded"
    assert all(row.get("range") and row["api"]["statusCode"] == 206 for row in data_reads), data_reads
    sidecars = [row for row in java_reads if row["api"].get("object") not in keys]
    assert all(row["api"].get("object") in {k + ".ovr" for k in keys}
               and row["api"]["statusCode"] == 404 for row in sidecars), sidecars
    return {
        "successful": len(data_reads),
        "by_object": dict(Counter(row["api"]["object"] for row in data_reads)),
        "bytes": sum(int(row["content_length"]) for row in data_reads),
        "all_data_gets_ranged_206": True,
        "full_object_gets_from_geoserver": 0,
        "optional_external_overview_404s": len(sidecars),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=HERE.parent / ".validation/real-10deg")
    parser.add_argument("--output-dir", type=Path, default=HERE.parent / ".validation/s3-mosaic")
    parser.add_argument("--geoserver-port", type=int, default=18096)
    parser.add_argument("--s3-port", type=int, default=19096)
    args = parser.parse_args()
    files = [args.input_dir.resolve() / name for name in
             ("NASADEM_N30E020_N39E029.tif", "NASADEM_N30E030_N39E039.tif")]
    for path in files:
        if not path.is_file():
            parser.error(f"Existing COG required: {path}")
    run_id = "nasadem-s3-" + uuid.uuid4().hex[:8]
    output = args.output_dir.resolve() / run_id
    output.mkdir(parents=True, mode=0o700)
    print("Evidence:", output, flush=True)
    password, access_key, secret_key = secrets.token_hex(24), "test" + secrets.token_hex(8), secrets.token_hex(24)
    gs_env = {
        "GEOSERVER_ADMIN_USER": "admin", "GEOSERVER_ADMIN_PASSWORD": password,
        "SHOW_PASSWORD": "false", "SAMPLE_DATA": "false",
        "INITIAL_MEMORY": "512M", "MAXIMUM_MEMORY": "2G",
        "ACTIVE_EXTENSIONS": "control-flow-plugin",
        "COMMUNITY_EXTENSIONS": "cog-s3-plugin",
        "FORCE_DOWNLOAD_COMMUNITY_EXTENSIONS": "false",
        "IIO_S3_AWS_ENDPOINT": "http://s3:9000", "IIO_S3_AWS_REGION": "us-east-1",
        "IIO_S3_AWS_USER": access_key, "IIO_S3_AWS_PASSWORD": secret_key,
        "IIO_S3_AWS_FORCE_PATH_STYLE": "true",
    }
    minio_env = {
        "MINIO_ROOT_USER": access_key, "MINIO_ROOT_PASSWORD": secret_key,
        "MINIO_AUDIT_WEBHOOK_ENABLE_TEST": "on",
        "MINIO_AUDIT_WEBHOOK_ENDPOINT_TEST": "http://audit:8080",
    }
    db_env = {"POSTGRES_DB": "mosaic", "POSTGRES_USER": "mosaic", "POSTGRES_PASSWORD": password}
    for name, values in (("geoserver.env", gs_env), ("minio.env", minio_env), ("postgis.env", db_env)):
        path = output / name
        path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        path.chmod(0o600)
    result = {"run_id": run_id, "status": "running", "images": {
        "geoserver": GEOSERVER_IMAGE, "minio": MINIO_IMAGE, "oracle": GDAL_IMAGE,
        "postgis": POSTGIS_IMAGE},
        "source_files": {p.name: {"bytes": p.stat().st_size, "sha256": digest(p)} for p in files},
        "resources": {"geoserver_cpus": 2, "geoserver_memory": "3g", "java_max_heap": "2G"},
        "rest": [], "wcs": [], "wms": []}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    containers = []

    def call(*command, data=None, check=True):
        p = subprocess.run(["podman", *command], input=data, capture_output=True)
        if check and p.returncode:
            raise RuntimeError("Podman " + command[0] + ": " + p.stderr.decode(errors="replace"))
        return p.stdout

    def request(method, path, **kwargs):
        start = time.monotonic()
        response = session.request(method, base + path, timeout=120, **kwargs)
        result["rest"].append({"method": method, "path": path, "status": response.status_code,
                               "seconds": round(time.monotonic() - start, 3)})
        if not response.ok:
            (output / "rest-error.txt").write_text(response.text)
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}; see rest-error.txt")
        return response

    def ready(url, auth=None):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            try:
                response = requests.get(url, auth=auth, timeout=4)
                if response.ok:
                    return response
            except requests.RequestException:
                pass
            time.sleep(2)
        raise RuntimeError("Readiness timeout: " + url)

    base = f"http://127.0.0.1:{args.geoserver_port}/geoserver"
    s3_url = f"http://127.0.0.1:{args.s3_port}"
    session = requests.Session()
    session.auth = ("admin", password)
    network = run_id
    try:
        # Local source oracle: existing files mounted read-only, no network.
        oracle = call("run", "--rm", "--network", "none", "--entrypoint", "python3",
                      "-v", str(args.input_dir.resolve()) + ":/input:ro",
                      "-v", str(HERE / "source_oracle.py") + ":/oracle.py:ro",
                      GDAL_IMAGE, "/oracle.py")
        (output / "source-oracle.json").write_bytes(oracle)
        reference = json.loads(oracle)
        call("network", "create", "--internal", network)
        audit, minio, geoserver, postgis = [run_id + "-" + suffix for suffix in ("audit", "s3", "geoserver", "postgis")]
        call("create", "--name", audit, "--network", network, "--network-alias", "audit",
             "--image-volume=ignore", "--memory", "128m", "--entrypoint", "python3",
             GDAL_IMAGE, "/tmp/audit_sink.py")
        containers.append(audit)
        call("cp", str(HERE / "audit_sink.py"), audit + ":/tmp/audit_sink.py")
        call("start", audit)
        call("run", "-d", "--name", minio, "--network", network, "--network-alias", "s3",
             "--image-volume=ignore", "--cpus", "1", "--memory", "1g",
             "-p", f"127.0.0.1:{args.s3_port}:9000", "--env-file", str(output / "minio.env"),
             MINIO_IMAGE, "server", "/data")
        containers.append(minio)
        ready(s3_url + "/minio/health/live")
        call("run", "-d", "--name", postgis, "--network", network, "--network-alias", "db",
             "--image-volume=ignore", "--cpus", "1", "--memory", "512m",
             "--env-file", str(output / "postgis.env"), POSTGIS_IMAGE)
        containers.append(postgis)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            check = call("exec", postgis, "pg_isready", "-U", "mosaic", "-d", "mosaic", check=False)
            if b"accepting connections" in check:
                break
            time.sleep(2)
        else:
            raise RuntimeError("PostGIS readiness timeout")
        s3 = boto3.client("s3", endpoint_url=s3_url, region_name="us-east-1",
                          aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                          config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
        s3.create_bucket(Bucket=BUCKET)
        result["uploads"] = []
        for path in files:
            started = time.monotonic()
            key = "cogs/" + path.name
            s3.upload_file(str(path), BUCKET, key,
                           ExtraArgs={"Metadata": {"sha256": result["source_files"][path.name]["sha256"]}},
                           Config=TransferConfig(max_concurrency=2))
            # Validate stored bytes, independently of multipart ETags.
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"]
            h = hashlib.sha256()
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                h.update(chunk)
            body.close()
            assert h.hexdigest() == result["source_files"][path.name]["sha256"]
            anonymous = requests.get(s3_url + "/" + BUCKET + "/" + key,
                                     headers={"Range": "bytes=0-15"}, timeout=10)
            assert anonymous.status_code == 403, anonymous.status_code
            result["uploads"].append({"key": key, "sha256_verified": True, "anonymous_status": 403,
                                      "seconds": round(time.monotonic() - started, 3)})
        print("Both original COGs uploaded and hash-verified; anonymous reads denied.", flush=True)
        call("create", "--name", geoserver, "--network", network,
             "--image-volume=ignore", "--cpus", "2", "--memory", "3g",
             "-p", f"127.0.0.1:{args.geoserver_port}:8080",
             "--env-file", str(output / "geoserver.env"), GEOSERVER_IMAGE)
        containers.append(geoserver)
        call("start", geoserver)
        result["version"] = ready(base + "/rest/about/version.json", session.auth).json()
        result["geoserver_mounts"] = json.loads(call("inspect", geoserver))[0]["Mounts"]
        assert not result["geoserver_mounts"], result["geoserver_mounts"]
        print("GeoServer ready; creating remote ImageMosaic.", flush=True)
        result["plugin_zip_sha256"] = call("exec", geoserver, "sha256sum",
                                           "/community_plugins/cog-s3-plugin.zip").decode().split()[0]
        result["cog_libraries"] = call("exec", geoserver, "bash", "-c",
            f"find {LIB} -maxdepth 1 -type f -name '*cog*' -printf '%f\\n'").decode().splitlines()
        request("POST", "/rest/workspaces", json={"workspace": {"name": "nasadem_s3"}})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.write(HERE / "indexer.properties", "indexer.properties")
            archive.writestr("datastore.properties", (
                "SPI=org.geotools.data.postgis.PostgisNGDataStoreFactory\n"
                "host=db\nport=5432\ndatabase=mosaic\nschema=public\n"
                f"user=mosaic\npasswd={password}\n"
                "Loose bbox=true\nmax connections=4\nmin connections=1\n"))
        store = "/rest/workspaces/nasadem_s3/coveragestores/nasadem"
        request("PUT", store + "/file.imagemosaic", params={"configure": "none"},
                headers={"Content-Type": "application/zip"}, data=buffer.getvalue())
        for path in files:
            request("POST", store + "/remote.imagemosaic",
                    headers={"Content-Type": "text/plain"},
                    data="s3://" + BUCKET + "/cogs/" + path.name)
        available = request("GET", store + "/coverages.xml", params={"list": "all"})
        (output / "available-coverages.xml").write_text(available.text)
        request("POST", store + "/coverages", json={"coverage": {
            "name": "nasadem", "nativeName": "nasadem", "enabled": True,
            "srs": "EPSG:4326", "nativeFormat": "GeoTIFF",
            "supportedFormats": {"string": ["GEOTIFF"]}}})
        coverage = request("GET", store + "/coverages/nasadem.json")
        (output / "coverage.json").write_text(coverage.text)
        granules = request("GET", store + "/coverages/nasadem/index/granules.json").json()
        (output / "granules.json").write_text(json.dumps(granules, indent=2))
        assert len(granules["features"]) == 2, granules
        result["granule_locations"] = [f["properties"]["location"] for f in granules["features"]]
        assert set(result["granule_locations"]) == {
            "s3://" + BUCKET + "/cogs/" + p.name for p in files}
        request("POST", "/rest/workspaces/nasadem_s3/styles",
                params={"name": "elevation"}, headers={"Content-Type": "application/vnd.ogc.sld+xml"},
                data=(HERE / "elevation.sld").read_bytes())
        request("PUT", "/rest/layers/nasadem_s3:nasadem",
                json={"layer": {"defaultStyle": {"name": "elevation", "workspace": "nasadem_s3"}}})
        for case in reference["cases"]:
            response = request("GET", "/wcs", params={
                "service": "WCS", "version": "1.0.0", "request": "GetCoverage",
                "coverage": "nasadem_s3:nasadem", "crs": "EPSG:4326",
                "response_crs": "EPSG:4326", "bbox": ",".join(map(str, case["bbox"])),
                "width": case["width"], "height": case["height"], "format": "GeoTIFF",
                "interpolation": "nearest neighbor"})
            (output / (case["name"] + "-wcs.tif")).write_bytes(response.content)
            if not response.content.startswith((b"II", b"MM")):
                raise RuntimeError("WCS did not return TIFF: " + response.text[:500])
            image = Image.open(io.BytesIO(response.content))
            actual = np.array(image)
            expected = np.frombuffer(base64.b64decode(case["int16_base64"]), dtype="<i2").reshape(
                case["height"], case["width"])
            assert actual.shape == expected.shape, (case["name"], actual.shape)
            assert np.array_equal(actual, expected), (case["name"], int(np.count_nonzero(actual != expected)))
            tags = image.tag_v2
            expected_origin = (case["bbox"][0], case["bbox"][3])
            if 34264 in tags:
                matrix = tags[34264]
                origin, scale = (matrix[3], matrix[7]), (matrix[0], -matrix[5])
                assert matrix[1] == matrix[4] == 0
            else:
                origin, scale = tags[33922][3:5], tags[33550][:2]
            assert np.allclose(origin, expected_origin, atol=1e-10, rtol=0)
            assert np.allclose(scale, [1/3600, 1/3600], atol=1e-12, rtol=0)
            keys = tags[34735]
            geokeys = {keys[i]: tuple(keys[i+1:i+4]) for i in range(4, len(keys), 4)}
            assert geokeys[2048] == (0, 1, 4326)
            assert geokeys[1025] == (0, 1, 1)  # PixelIsArea; matrix is corner-based.
            assert tags[258] == (16,) and tags[339] == (2,)
            assert float(tags[42113].rstrip("\0")) == case["nodata"]
            result["wcs"].append({"case": case["name"], "pixels": int(actual.size),
                                   "exact_values": True, "exact_georeferencing": True,
                                   "int16_epsg4326_nodata_verified": True,
                                   "valid": int(np.count_nonzero(expected != case["nodata"]))})
        print("Four native-resolution WCS windows match local values and georeferencing.", flush=True)
        # A few sequential requests only; no cache clearing, concurrency or load test.
        views = [("west", [22, 36, 26, 40]), ("east", [34, 30, 36, 34]),
                 ("seam", [29.5, 30, 30.5, 31])]
        for name, bbox in views:
            measurements = []
            for attempt in range(4):
                start = time.monotonic()
                response = request("GET", "/wms", params={
                    "service": "WMS", "version": "1.1.1", "request": "GetMap",
                    "layers": "nasadem_s3:nasadem", "styles": "", "srs": "EPSG:4326",
                    "bbox": ",".join(map(str, bbox)), "width": 512, "height": 512,
                    "format": "image/png", "transparent": "true", "tiled": "false"})
                elapsed = time.monotonic() - start
                image = Image.open(io.BytesIO(response.content)).convert("RGBA")
                assert image.size == (512, 512)
                assert np.count_nonzero(np.array(image)[:, :, 3]) > 0, name
                measurements.append(round(elapsed, 4))
                if attempt == 0:
                    (output / (name + "-wms.png")).write_bytes(response.content)
            result["wms"].append({"view": name, "bbox": bbox, "size": [512, 512],
                "seconds": measurements, "first_view_seconds": measurements[0],
                "repeat_median_seconds": statistics.median(measurements[1:])})
        call("restart", "--time", "20", geoserver)
        ready(base + "/rest/about/version.json", session.auth)
        restored = request("GET", store + "/coverages/nasadem/index/granules.json").json()
        assert len(restored["features"]) == 2
        response = request("GET", "/wms", params={
            "service": "WMS", "version": "1.1.1", "request": "GetMap",
            "layers": "nasadem_s3:nasadem", "srs": "EPSG:4326", "bbox": "29.5,30,30.5,31",
            "width": 256, "height": 256, "format": "image/png", "transparent": "true"})
        assert Image.open(io.BytesIO(response.content)).size == (256, 256)
        result["restart_preserved_mosaic"] = True
        time.sleep(2)
        audit_records = [json.loads(line) for line in call("logs", audit).decode().splitlines() if line.startswith("{")]
        (output / "s3-audit.jsonl").write_text("".join(json.dumps(row) + "\n" for row in audit_records))
        result["range_reads"] = summarize_audit(audit_records, {"cogs/" + p.name for p in files})
        assert len(result["range_reads"]["by_object"]) == 2, result["range_reads"]
        result["source_files_unchanged"] = all(digest(p) == result["source_files"][p.name]["sha256"] for p in files)
        assert result["source_files_unchanged"]
        result["status"] = "passed"
        print("Private S3 ImageMosaic, range reads, basic WMS timing and restart passed.", flush=True)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        (output / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        for name in reversed(containers):
            logs = call("logs", name, check=False).decode(errors="replace")
            for secret in (password, access_key, secret_key):
                logs = logs.replace(secret, "[REDACTED]")
            (output / (name + ".log")).write_text(logs)
            call("stop", "--time", "20", name, check=False)
        result["containers"] = containers
        result["containers_stopped"] = {
            name: call("inspect", "--format", "{{.State.Status}}", name, check=False).decode().strip()
            for name in containers}
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print("Saved", output / "result.json", flush=True)


if __name__ == "__main__":
    main()
