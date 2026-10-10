#!/usr/bin/env python3
"""Reproducible OSM preparation. Python 3.11+, standard library only."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
BUNDLE = ROOT / "bundle"
STYLES = {"osm-bright": "osm-bright-gl", "positron": "positron-gl", "dark-matter": "dark-matter", "toner": "toner-gl", "maptiler-basic": "maptiler-basic-gl"}


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_config(path):
    cfg = json.loads(Path(path).read_text())
    if not cfg["countries"] or len(set(cfg["countries"])) != len(cfg["countries"]):
        raise ValueError("countries must contain distinct Geofabrik identifiers")
    for country in cfg["countries"]:
        if not re.fullmatch(r"[a-z0-9-]+(?:/[a-z0-9-]+)+", country):
            raise ValueError(f"Invalid Geofabrik identifier: {country}")
    if not 0 <= cfg["source_maxzoom"] <= 14 <= cfg["display_maxzoom"] <= 22:
        raise ValueError("Expected source_maxzoom <= 14 and display_maxzoom in 14..22")
    if not re.fullmatch(r"\d+[gGmM]", cfg["java_heap"]):
        raise ValueError("java_heap must be a Java memory size, for example 16g")
    return cfg


def preflight(cfg):
    memory = {}
    if Path('/proc/meminfo').exists():
        memory = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()}
    report = {'checked_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'architecture': platform.machine(), 'cpus': os.cpu_count(),
              'memory_total_bytes': memory.get('MemTotal'),
              'memory_available_bytes': memory.get('MemAvailable'),
              'disk_free_bytes': shutil.disk_usage(ROOT).free,
              'engine': Engine(cfg).exe, 'warnings': []}
    if report['architecture'] not in ('x86_64', 'AMD64'):
        report['warnings'].append('The preparation/runtime images target Linux AMD64; native execution is recommended.')
    if report['cpus'] < cfg['threads']:
        report['warnings'].append('Configured build threads exceed available CPU count.')
    if memory.get('MemAvailable', 32 * 1024**3) < 32 * 1024**3:
        report['warnings'].append('Available RAM is below the initial 32 GiB preparation budget.')
    if report['disk_free_bytes'] < 60 * 1024**3:
        report['warnings'].append('Free disk is below the initial 60 GiB preparation budget.')
    save_json(WORK / 'preflight.json', report)
    print(json.dumps(report, indent=2))


def execute(args, *, log=None, capture=False, timeout=None):
    # Print executable/operation only: command arguments can contain credentials.
    print(f"Running {Path(str(args[0])).name}: {log.name if log else str(args[1]) if len(args)>1 else ''}", flush=True)
    start = time.monotonic()
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as out:
            p = subprocess.run([str(a) for a in args], stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
        if p.returncode:
            raise RuntimeError(f"Command failed ({p.returncode}); see {log}")
    else:
        p = subprocess.run([str(a) for a in args], check=True, text=True, capture_output=capture, timeout=timeout)
    return p.stdout if capture else time.monotonic() - start


class Engine:
    def __init__(self, cfg):
        choice = cfg.get("engine", "auto")
        self.exe = (shutil.which("podman") or shutil.which("docker")) if choice == "auto" else shutil.which(choice)
        if not self.exe:
            raise RuntimeError("Docker or Podman is required")
        self.podman = "podman" in Path(self.exe).name

    def run(self, args, **kwargs):
        return execute([self.exe, *args], **kwargs)

    def image(self, role):
        return json.loads((WORK / "lock.json").read_text())["images"][role]["id"]

    def container(self, role, command, *, mounts=(), network="none", options=(), log=None):
        args = ["run", "--rm", "--pull=never", "--network", network, *options]
        for source, destination, mode in mounts:
            args += ["-v", f"{Path(source).resolve()}:{destination}:{mode}"]
        args += ["--entrypoint", command[0], self.image(role), *command[1:]]
        return self.run(args, log=log)


def download(url, destination, old=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and old and old.get("url") == url and sha256(destination) == old["sha256"]:
        return old
    part = destination.with_suffix(destination.suffix + ".part")
    print(f"Downloading {destination.name}", flush=True)
    execute(["curl", "--fail", "--location", "--retry", "4", "--connect-timeout", "30", "--continue-at", "-", "--output", part, url], log=WORK / "logs" / (destination.name + ".download.log"))
    if destination.suffix in ('.zip', '.jar') and not zipfile.is_zipfile(part):
        part.unlink()
        raise RuntimeError(f'{destination.name}: server returned something other than a ZIP/JAR archive')
    digest = sha256(part)
    if old and old.get('url') == url and digest != old['sha256']:
        raise RuntimeError(f'{destination.name}: upstream content changed from the locked checksum; preserve the lock and start a new input set')
    part.replace(destination)
    return {"url": url, "sha256": digest, "bytes": destination.stat().st_size}


def snapshot(cfg):
    if cfg["snapshot"] != "latest-common":
        if not re.fullmatch(r"\d{6}", cfg["snapshot"]):
            raise ValueError("snapshot must be latest-common or YYMMDD")
        return cfg["snapshot"]
    dates = []
    for region in cfg["countries"]:
        with urllib.request.urlopen(f"https://download.geofabrik.de/{region}.html", timeout=60) as r:
            page = r.read().decode()
        dates.append(set(re.findall(re.escape(region.split("/")[-1]) + r"-(\d{6})\.osm\.pbf", page)))
    common = set.intersection(*dates)
    if not common:
        raise RuntimeError("No shared dated Geofabrik snapshot found; set snapshot explicitly")
    return max(common)


def prefetch(cfg):
    WORK.mkdir(exist_ok=True)
    lockpath = WORK / "lock.json"
    previous = json.loads(lockpath.read_text()) if lockpath.exists() else {}
    config_hash = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    date = previous["snapshot"] if previous.get("config_hash") == config_hash else snapshot(cfg)
    lock = {"config": cfg, "complete": False, "config_hash": config_hash, "snapshot": date, "created_utc": previous.get('created_utc', dt.datetime.now(dt.timezone.utc).isoformat()), "files": previous.get("files", {}), "images": previous.get("images", {})}
    engine = Engine(cfg)
    for role, tag in cfg["images"].items():
        if role not in lock["images"] or lock['images'][role]['tag'] != tag:
            engine.run(["pull", "--platform=linux/amd64", tag], log=WORK / "logs" / f"pull-{role}.log")
            info = json.loads(engine.run(["image", "inspect", tag], capture=True))[0]
            lock["images"][role] = {"tag": tag, "id": info["Id"], "repo_digests": info.get("RepoDigests", [])}
            save_json(lockpath, lock)
    plugin_output = engine.run(['run','--rm','--pull=never','--network','none','--entrypoint','/bin/bash',lock['images']['geoserver']['id'],'-ec','sha256sum /stable_plugins/mbstyle-plugin.zip /community_plugins/mbtiles-store-plugin.zip'],capture=True)
    lock['bundled_plugins'] = {line.split()[1]: line.split()[0] for line in plugin_output.splitlines()}
    save_json(lockpath, lock)
    assets = {
        "planetiler.jar": f"https://github.com/onthegomap/planetiler/releases/download/v{cfg['planetiler_version']}/planetiler.jar",
        "styles.zip": f"https://codeload.github.com/geosolutions-it/openmaptiles/zip/{cfg['styles_commit']}",
        "fonts.zip": "https://codeload.github.com/openmaptiles/fonts/zip/d48c5fce2fc58b55c98d353558d807cac45e7262",
        "NotoSans-BoldItalic.ttf": "https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-BoldItalic.ttf",
        "lake_centerline.shp.zip": "https://dev.maptiler.download/geodata/omt/lake_centerline.shp.zip",
        "water-polygons-split-3857.zip": "https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip",
        "natural_earth_vector.sqlite.zip": "https://dev.maptiler.download/geodata/omt/natural_earth_vector.sqlite.zip",
        "ol.js": "https://cdn.jsdelivr.net/npm/ol@10.6.1/dist/ol.js",
        "ol.css": "https://cdn.jsdelivr.net/npm/ol@10.6.1/ol.css",
        "ol-LICENSE.md": "https://cdn.jsdelivr.net/npm/ol@10.6.1/LICENSE.md",
    }
    for style, repository in {'osm-bright':'osm-bright-gl-style','positron':'positron-gl-style','dark-matter':'dark-matter-gl-style','toner':'maptiler-toner-gl-style','maptiler-basic':'maptiler-basic-gl-style'}.items():
        assets[f'license-{style}.md'] = f'https://raw.githubusercontent.com/openmaptiles/{repository}/master/LICENSE.md'
    for region in cfg["countries"]:
        assets[region.replace("/", "_") + ".osm.pbf"] = f"https://download.geofabrik.de/{region}-{date}.osm.pbf"
    for scale in (110, 50, 10):
        for layer in ("land", "ocean", "lakes", "admin_0_countries", "admin_0_boundary_lines_land", "populated_places"):
            theme = "cultural" if layer in ("admin_0_countries", "admin_0_boundary_lines_land", "populated_places") else "physical"
            filename = f"ne_{scale}m_{layer}.zip"
            assets[filename] = f"https://naturalearth.s3.amazonaws.com/{scale}m_{theme}/{filename}"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(download, url, WORK / "inputs" / filename, lock["files"].get(filename)): filename for filename, url in assets.items()}
        for task in concurrent.futures.as_completed(futures):
            filename = futures[task]
            lock["files"][filename] = task.result()
            save_json(lockpath, lock)
    # Download the distribution's Osmium packages into a mounted cache. No host installation.
    debs = WORK / "inputs" / "debs"
    debs.mkdir(exist_ok=True)
    if not list(debs.glob("*.deb")):
        engine.container("gdal", ["sh", "-ec", "apt-get update && apt-get -y --download-only -o Dir::Cache::archives=/debs install osmium-tool"], mounts=[(debs, "/debs", "rw")], network="bridge", log=WORK / "logs" / "osmium-prefetch.log")
    lock["debs"] = {p.name: sha256(p) for p in sorted(debs.glob("*.deb"))}
    lock['complete'] = True
    save_json(lockpath, lock)
    print(f"Prefetch complete: snapshot {date}")


def verify_inputs(cfg):
    lock = json.loads((WORK / "lock.json").read_text())
    if not lock.get('complete'):
        raise RuntimeError('Prefetch did not complete; run prefetch before building')
    if lock["config_hash"] != hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest():
        raise RuntimeError("Configuration changed: run prefetch before building")
    for filename, entry in lock["files"].items():
        if sha256(WORK / "inputs" / filename) != entry["sha256"]:
            raise RuntimeError(f"Checksum mismatch: {filename}")
    for filename, checksum in lock["debs"].items():
        if sha256(WORK / "inputs" / "debs" / filename) != checksum:
            raise RuntimeError(f"Checksum mismatch: {filename}")
    return lock


def build(cfg, smoke=False):
    lock = verify_inputs(cfg)
    engine = Engine(cfg)
    target = ROOT / ("work/smoke" if smoke else "bundle")
    data = target / "data_dir" / "data"
    data.mkdir(parents=True, exist_ok=True)
    data_inputs = {name: entry for name,entry in lock['files'].items() if name.endswith('.osm.pbf') or name.startswith('ne_') or name in ('planetiler.jar','water-polygons-split-3857.zip','lake_centerline.shp.zip','natural_earth_vector.sqlite.zip')}
    build_key = hashlib.sha256(json.dumps({'files': data_inputs, 'maxzoom': cfg['source_maxzoom'], 'smoke': smoke, 'bbox': cfg['smoke_bbox'] if smoke else None}, sort_keys=True).encode()).hexdigest()
    stamp = target / 'build-inputs.json'
    if stamp.exists() and json.loads(stamp.read_text())['key'] != build_key:
        raise RuntimeError(f'Inputs changed; move {target} aside before rebuilding to keep old and new maps separate')
    save_json(stamp, {'key': build_key})
    scratch = WORK / "scratch" / lock['config_hash'][:12]
    scratch.mkdir(parents=True, exist_ok=True)
    mounts = [(WORK / "inputs", "/inputs", "ro"), (scratch, "/scratch", "rw"), (data, "/output", "rw"), (ROOT, "/project", "ro")]
    country_files = ["/inputs/" + r.replace("/", "_") + ".osm.pbf" for r in cfg["countries"]]
    merged = scratch / "merged.osm.pbf"
    if not merged.exists():
        command = "dpkg -i /inputs/debs/*.deb >/dev/null && osmium merge " + " ".join(country_files) + " -o /scratch/merged.partial.osm.pbf --overwrite && osmium fileinfo -e -j /scratch/merged.partial.osm.pbf > /scratch/merged-info.json && mv /scratch/merged.partial.osm.pbf /scratch/merged.osm.pbf"
        engine.container("gdal", ["sh", "-ec", command], mounts=mounts, log=WORK / "logs" / "merge.log")
    source = "/scratch/merged.osm.pbf"
    if smoke:
        bbox = ",".join(str(v) for v in cfg["smoke_bbox"])
        engine.container("gdal", ["sh", "-ec", f"dpkg -i /inputs/debs/*.deb >/dev/null && osmium extract --strategy=complete_ways --bbox={bbox} /scratch/merged.osm.pbf -o /scratch/smoke.osm.pbf --overwrite"], mounts=mounts, log=WORK / "logs" / "smoke-extract.log")
        source = "/scratch/smoke.osm.pbf"
    output = data / "regional.mbtiles"
    production_path = target / 'production.json'
    timings = json.loads(production_path.read_text()).get('timings', {}) if production_path.exists() else {}
    if not output.exists():
        args = ["java", f"-Xmx{cfg['java_heap']}", "-jar", "/inputs/planetiler.jar", f"--osm_path={source}", "--output=/output/regional.partial.mbtiles", "--mbtiles=/output/regional.partial.mbtiles", "--tmpdir=/scratch/planetiler", "--water_polygons_path=/inputs/water-polygons-split-3857.zip", "--lake_centerlines_path=/inputs/lake_centerline.shp.zip", "--natural_earth_path=/inputs/natural_earth_vector.sqlite.zip", "--download=false", "--fetch_wikidata=false", "--use_wikidata=false", "--languages=cs,sk,en,de", f"--threads={cfg['threads']}", f"--maxzoom={cfg['source_maxzoom']}", "--nodemap_storage=mmap", "--force=true"]
        if smoke:
            args.append('--bounds=' + ','.join(str(v) for v in cfg['smoke_bbox']))
        else:
            args.append('--bounds=' + ','.join(str(v) for v in json.loads((scratch / 'merged-info.json').read_text())['data']['bbox']))
        timings["planetiler_seconds"] = engine.container("java", args, mounts=mounts, options=["--memory", cfg["build_memory"], "--cpus", str(cfg["threads"])], log=WORK / "logs" / ("planetiler-smoke.log" if smoke else "planetiler.log"))
        (data / 'regional.partial.mbtiles').rename(output)
    if not (data / "world.gpkg").exists():
        timings["world_seconds"] = engine.container("gdal", ["python3", "/project/world.py"], mounts=mounts, log=WORK / "logs" / "world.log")
    from catalog import normalize_metadata
    save_json(target / 'metadata-normalization.json', normalize_metadata(output, WORK / 'inputs/styles.zip'))
    prepare_bundle(cfg, target, lock)
    shutil.copyfile(scratch / 'merged-info.json', target / 'merged-info.json')
    save_json(target / "production.json", {"snapshot": lock["snapshot"], "countries": cfg["countries"], "smoke": smoke, "timings": timings, "data_bytes": sum(p.stat().st_size for p in data.iterdir())})
    print(f"Prepared {target}")


def prepare_bundle(cfg, target, lock):
    from catalog import make_styles
    for name in ("fonts", "cache", "licenses", "data_dir/styles", "data_dir/www/basemap"):
        (target / name).mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(WORK / "inputs/fonts.zip") as z:
        for name in z.namelist():
            if name.lower().endswith((".ttf", ".otf")) and not name.startswith("__MACOSX"):
                (target / "fonts" / Path(name).name).write_bytes(z.read(name))
            elif any(token in Path(name).name.lower() for token in ("license", "ofl", "copyright")):
                (target / "licenses" / (name.replace("/", "_") or "fonts.txt")).write_bytes(z.read(name))
    shutil.copyfile(WORK / 'inputs/NotoSans-BoldItalic.ttf', target / 'fonts/NotoSans-BoldItalic.ttf')
    make_styles(WORK / "inputs/styles.zip", target / "data_dir/styles")
    prepare_viewer(cfg, target, lock)
    shutil.copyfile(WORK / "inputs/ol-LICENSE.md", target / "licenses/OpenLayers.md")
    for source in (WORK / 'inputs').glob('license-*.md'):
        shutil.copyfile(source, target / 'licenses' / source.name)
    shutil.copyfile(ROOT / "compose.yaml", target / "compose.yaml")
    shutil.copyfile(ROOT / 'compose.podman.yaml', target / 'compose.podman.yaml')
    shutil.copyfile(ROOT / "init-permissions.sh", target / "init-permissions.sh")
    shutil.copyfile(ROOT / 'load-and-run.sh', target / 'load-and-run.sh')
    for name in ("README.md", "THIRD_PARTY_NOTICES.md", "validation-report.md"):
        if (ROOT / name).exists():
            shutil.copyfile(ROOT / name, target / name)
    shutil.copyfile(WORK / "lock.json", target / "source-manifest.json")
    save_json(target / "config.json", cfg)
    env = target / ".env"
    if not env.exists():
        # Stable image ID works after docker load even without a registry digest lookup.
        env.write_text(f"GEOSERVER_IMAGE={lock['images']['geoserver']['tag']}\nGEOSERVER_ADMIN_USER=admin\nGEOSERVER_ADMIN_PASSWORD={secrets.token_hex(24)}\nGEOSERVER_PORT={cfg['port']}\nGEOSERVER_HEAP={cfg['geoserver_heap']}\nBIND_ADDRESS=127.0.0.1\n")
        env.chmod(0o600)


def prepare_viewer(cfg, target, lock):
    viewer = target / 'data_dir/www/basemap'
    viewer.mkdir(parents=True, exist_ok=True)
    for name in ('index.html', 'app.js', 'app.css'):
        shutil.copyfile(ROOT / 'viewer' / name, viewer / name)
    for name in ('ol.js', 'ol.css'):
        shutil.copyfile(WORK / 'inputs' / name, viewer / name)
    with sqlite3.connect(f'file:{target / "data_dir/data/regional.mbtiles"}?mode=ro', uri=True) as db:
        metadata = dict(db.execute('SELECT name,value FROM metadata'))
    from catalog import STYLE_GROUPS
    revisions = {name: sha256(target / 'data_dir/styles' / (name + '.json'))[:16] for name in STYLE_GROUPS}
    save_json(viewer / 'config.json', {'default_style': cfg['default_style'], 'default_language': 'en', 'style_revisions': revisions, 'maxzoom': cfg['display_maxzoom'], 'countries': cfg['countries'], 'snapshot': lock['snapshot'], 'bounds': [float(v) for v in metadata['bounds'].split(',')]})


def refresh_styles(cfg, target):
    """Refresh a running prepared catalog without opening either dataset for writing."""
    from catalog import Rest, make_styles, provision, ENGLISH_GROUPS
    prepared = json.loads((target / 'config.json').read_text())
    for key in ('countries', 'snapshot', 'source_maxzoom', 'display_maxzoom'):
        if cfg[key] != prepared[key]:
            raise RuntimeError(f'{key} changed; refresh-styles only updates styles/viewer, not data or grids')
    lock = json.loads((target / 'source-manifest.json').read_text())
    archive = WORK / 'inputs/styles.zip'
    if sha256(archive) != lock['files']['styles.zip']['sha256']:
        raise RuntimeError('The style archive does not match the prepared bundle source manifest')
    env = env_values(target)
    base = f'http://127.0.0.1:{env.get("GEOSERVER_PORT", cfg["port"])}/geoserver'
    Rest(base, env).request('/rest/about/version.json')
    data = target / 'data_dir/data'
    before = {name: sha256(data / name) for name in ('regional.mbtiles', 'world.gpkg')}
    pending_path = target / 'pending-style-refresh.json'
    pending = set(json.loads(pending_path.read_text())) if pending_path.exists() else set()
    pending.update(make_styles(archive, target / 'data_dir/styles'))
    pending.update(ENGLISH_GROUPS)
    save_json(pending_path, sorted(pending))
    changed = provision(base, env, target, cfg, names=sorted(pending), refresh=True)
    # Expose the selector only after its groups are successfully registered.
    prepare_viewer(cfg, target, lock)
    after = {name: sha256(data / name) for name in before}
    if before != after:
        raise RuntimeError('Dataset hashes changed during style refresh; inspect before exporting')
    for name in ('README.md', 'validation-report.md'):
        if (ROOT / name).exists(): shutil.copyfile(ROOT / name, target / name)
    save_json(target / 'validation/style-refresh.json', {'complete': True, 'data_unchanged': True, 'data_sha256': after, 'published_groups': changed, 'checked_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    pending_path.unlink()
    print(f'Styles refreshed: {len(changed)} groups; dataset hashes unchanged. Viewer: {base}/www/basemap/index.html')


def env_values(target):
    return dict(line.split("=", 1) for line in (target / ".env").read_text().splitlines() if line and not line.startswith("#"))


def start(cfg, target, offline=True):
    engine = Engine(cfg)
    env = env_values(target)
    name = "osm-basemap-smoke" if target != BUNDLE else "osm-basemap-demo"
    exists = engine.run(["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"], capture=True).strip()
    if exists:
        raise RuntimeError(f"{name} already exists; use stop before a fresh start")
    network = name + "-isolated"
    existing_networks = engine.run(["network", "ls", "--format", "{{.Name}}"], capture=True).splitlines()
    if network not in existing_networks:
        engine.run(["network", "create", "--internal", network])
    args = ["run", "-d", "--name", name, "--pull=never", "--network", network, "-p", f"127.0.0.1:{cfg['port']}:8080"]
    # Rootless Podman needs the image's user mapped to this checkout's owner.
    if engine.podman:
        args += ["--user", "0:0", "--userns=keep-id:uid=2000,gid=2000"]
    for key, value in {"GEOSERVER_ADMIN_USER": env["GEOSERVER_ADMIN_USER"], "GEOSERVER_ADMIN_PASSWORD": env["GEOSERVER_ADMIN_PASSWORD"], "STABLE_EXTENSIONS": "mbstyle-plugin", "COMMUNITY_EXTENSIONS": "mbtiles-store-plugin", "FORCE_DOWNLOAD_STABLE_EXTENSIONS": "false", "FORCE_DOWNLOAD_COMMUNITY_EXTENSIONS": "false", "SAMPLE_DATA": "false", "RECREATE_DATADIR": "false", "GEOSERVER_DATA_DIR": "/opt/geoserver/data_dir", "GEOWEBCACHE_CACHE_DIR": "/opt/geoserver/gwc", "INITIAL_MEMORY": "1G", "MAXIMUM_MEMORY": cfg["geoserver_heap"]}.items():
        args += ["-e", f"{key}={value}"]
    args += ['-e', 'JAVA_TOOL_OPTIONS=-DMBSTYLE_ROOT_TILE_PIXELS=256', '-e', 'ACTIVE_EXTENSIONS=mbstyle-plugin', '-e', 'GEOSERVER_DISABLE_STATIC_WEB_FILES=false', '-e', 'GEOSERVER_STATIC_WEB_FILES_SCRIPT=SELF', '-v', f'{target / "init-permissions.sh"}:/docker-entrypoint-geoserver.d/99-demo-permissions.sh:ro']
    for host, guest, mode in (("data_dir", "/opt/geoserver/data_dir", "rw"), ("fonts", "/opt/fonts", "ro"), ("cache", "/opt/geoserver/gwc", "rw")):
        args += ["-v", f"{target / host}:{guest}:{mode}"]
    args += [engine.image("geoserver")]
    engine.run(args)
    base = f"http://127.0.0.1:{cfg['port']}/geoserver"
    from catalog import wait_ready, provision
    wait_ready(base, env)
    provision(base, env, target, cfg)
    print(f"Viewer: {base}/www/basemap/index.html")


def stop(cfg, target):
    name = "osm-basemap-smoke" if target != BUNDLE else "osm-basemap-demo"
    Engine(cfg).run(["rm", "-f", name])


def export(cfg):
    engine = Engine(cfg)
    exports = ROOT / "exports"
    exports.mkdir(exist_ok=True)
    # Export from a stopped server so SQLite/catalog/cache files form a consistent snapshot.
    active = engine.run(["ps", "--filter", "name=^osm-basemap-demo$", "--format", "{{.Names}}"], capture=True).strip()
    if active:
        raise RuntimeError("Stop the demo before exporting a consistent data directory")
    actual = json.loads(engine.run(['image','inspect',cfg['images']['geoserver']],capture=True))[0]['Id']
    expected = json.loads((WORK / 'lock.json').read_text())['images']['geoserver']['id']
    if actual.removeprefix('sha256:') != expected.removeprefix('sha256:'):
        raise RuntimeError('The image tag moved since prefetch; restore the locked image before exporting')
    image_path = BUNDLE / "images/geoserver.tar"
    image_path.parent.mkdir(exist_ok=True)
    if not image_path.exists():
        engine.run(["save", "--format", "docker-archive", "-o", str(image_path), cfg["images"]["geoserver"]] if engine.podman else ["save", "-o", str(image_path), cfg["images"]["geoserver"]])
    with tarfile.open(image_path) as image_archive:
        manifest = json.load(image_archive.extractfile('manifest.json'))
        archived_id = hashlib.sha256(image_archive.extractfile(manifest[0]['Config']).read()).hexdigest()
    if archived_id != expected.removeprefix('sha256:'):
        raise RuntimeError('The existing image archive differs from the locked image; move it aside and export again')
    files = [p for p in BUNDLE.rglob("*") if p.is_file() and p.name != "SHA256SUMS"]
    (BUNDLE / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.relative_to(BUNDLE).as_posix()}\n" for p in sorted(files)))
    name = f"osm-basemap-{json.loads((WORK / 'lock.json').read_text())['snapshot']}.tar"
    archive_path = exports / name
    archive_path.touch(mode=0o600, exist_ok=True)
    archive_path.chmod(0o600)  # Includes private deployment credentials/security state.
    with tarfile.open(archive_path, "w") as archive:
        archive.add(BUNDLE, arcname="osm-basemap")
    save_json(exports / (name + ".json"), {"sha256": sha256(exports / name), "bytes": (exports / name).stat().st_size})
    print(exports / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "prefetch", "build", "start", "stop", "refresh-styles", "validate", "export"])
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    cfg = read_config(args.config)
    target = WORK / "smoke" if args.smoke else BUNDLE
    if args.command == "preflight": preflight(cfg)
    elif args.command == "prefetch": prefetch(cfg)
    elif args.command == "build": build(cfg, args.smoke)
    elif args.command == "start": start(cfg, target)
    elif args.command == "stop": stop(cfg, target)
    elif args.command == "refresh-styles": refresh_styles(cfg, target)
    elif args.command == "export": export(cfg)
    elif args.command == "validate":
        from verify import validate
        validate(cfg, target)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
