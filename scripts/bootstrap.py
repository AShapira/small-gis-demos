"""Build on Windows Podman from a small staged context; never use the RHEL engine."""
from pathlib import Path
import hashlib
import base64
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PODMAN = os.environ.get('RASTERBENCH_PODMAN') or shutil.which('podman.exe') or (
    shutil.which('podman') if os.name == 'nt' else None)
CONNECTION = os.environ.get('RASTERBENCH_CONNECTION', 'podman-machine-default')


def pm(*args):
    if not PODMAN:
        raise RuntimeError('Set RASTERBENCH_PODMAN to the Windows Podman executable')
    return subprocess.check_output([PODMAN,'--connection',CONNECTION,*args], text=True)


def main():
    lockpath = ROOT/'infra/images.lock.json'
    lock = json.loads(lockpath.read_text())
    rev = lock['downloader_commit']
    dep = ROOT/'.build/copernicus'
    dep.mkdir(parents=True, exist_ok=True)
    checks = {}
    for name in ['copernicus_downloader.py','copernicus_visible_vrt.py','LICENSE']:
        url = f'https://raw.githubusercontent.com/AShapira/copernicus-downloader/{rev}/{name}'
        data = urllib.request.urlopen(url,timeout=30).read()
        if hashlib.sha256(data).hexdigest()!=lock['downloader_files'][name]['sha256']:
            raise ValueError('Downloader checksum mismatch: '+name)
        (dep/name).write_bytes(data)
        checks[name] = {'url':url,'sha256':hashlib.sha256(data).hexdigest()}
    lockpath = ROOT/'infra/images.lock.json'
    lock = json.loads(lockpath.read_text()) if lockpath.exists() else {}
    base_tag = 'ghcr.io/osgeo/gdal:ubuntu-small-3.11.4'
    base = lock.get('gdal_image',base_tag)
    print(pm('pull',base), flush=True)
    info = json.loads(pm('image','inspect',base))[0]
    base = next(x for x in info['RepoDigests'] if 'osgeo/gdal@' in x)
    lock.update(gdal_image=base, downloader_commit=rev, downloader_files=checks,
                geoserver_image='docker.osgeo.org/geoserver@sha256:7cb827ba3f6d9fc04a6647fc0cfa6c254fc642407f0d99281fdf026d2540b558')
    lockpath.write_text(json.dumps(lock,indent=2)+'\n')
    staging = Path(os.environ.get('RASTERBENCH_BUILD_DIR',
        str(ROOT/'.build'/'build-context')))
    staging.mkdir(parents=True,exist_ok=True)
    for name in ('rasterbench','infra','tests','configs','scripts'):
        if (ROOT/name).exists():
            shutil.copytree(ROOT/name,staging/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(dep,staging/'.build/copernicus',dirs_exist_ok=True)
    ctx = str(staging)
    if os.name != 'nt' and PODMAN.endswith('.exe'):
        ctx = subprocess.check_output(['wslpath','-w',ctx],text=True).strip()
    # Tar stdin avoids Windows/WSL path and .containerignore resolution differences.
    args=[PODMAN,'--connection',CONNECTION,'build','--network=host','--build-arg',f'BASE_IMAGE={base}',
          '-f','infra/Containerfile','-t','localhost/rasterbench-worker:0.1.0','-']
    with tempfile.TemporaryFile() as context:
        with tarfile.open(fileobj=context,mode='w') as tar:
            for name in ('rasterbench','infra','tests','configs','scripts','.build'):
                tar.add(staging/name,arcname=name)
        context.seek(0)
        subprocess.run(args,stdin=context,check=True)
    built=json.loads(pm('image','inspect','localhost/rasterbench-worker:0.1.0'))[0]
    lock['worker_image_id']=built['Id']
    lockpath.write_text(json.dumps(lock,indent=2)+'\n')


if __name__ == '__main__':
    main()
