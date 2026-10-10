#!/usr/bin/env python3
"""Host bridge for the isolated affine study; never invokes benchmark lifecycle code."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import threading
import time

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / '.runs/affine-probe'
PODMAN = os.environ.get('AFFINE_PODMAN') or shutil.which('podman') or shutil.which('podman.exe') or 'podman'
CONNECTION = 'podman-machine-default'
OWNER = 'israel-rgb-5gb'
WORKER = 'rasterbench-israel-rgb-5gb-worker'
SERVER = 'rasterbench-israel-rgb-5gb-gs'
REMOTE = '/data/affine-probe'


def podman(*args, data=None, capture=True):
    result = subprocess.run([PODMAN, '--connection', CONNECTION, *args], input=data,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, check=False)
    if result.returncode:
        raise RuntimeError(f'Podman {args[0]} failed ({result.returncode}): ' +
                           (result.stderr or b'').decode('utf-8', 'replace')[-3000:])
    return result


def inspect(name):
    item = json.loads(podman('inspect', name).stdout)[0]
    if item['Config'].get('Labels', {}).get('rasterbench.study') != OWNER:
        raise RuntimeError(f'Refusing unowned container: {name}')
    return item


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def preflight():
    found = {}
    for name in (WORKER, SERVER):
        item = inspect(name)
        found[name] = {'running': item['State']['Running'], 'image': item['ImageName'],
                       'image_id': item['Image'], 'command': item['Config']['Cmd'],
                       'mounts': item['Mounts'], 'resources': {
                           key: item['HostConfig'].get(key) for key in
                           ('Memory', 'NanoCpus', 'CpuPeriod', 'CpuQuota')},
                       'ports': item['HostConfig'].get('PortBindings')}
    if not (OUT / 'initial-state.json').exists():
        save('initial-state.json', found)
    save('current-state.json', found)
    print(json.dumps(found, indent=2))


def sync():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as archive:
        for src in (REPO / 'rasterbench/affine_probe.py', REPO / 'rasterbench/affine_analysis.py',
                    REPO / 'tests/test_affine_probe.py'):
            archive.add(src, arcname=src.name)
    podman('exec', WORKER, 'mkdir', '-p', '/opt/affine-probe', REMOTE)
    podman('exec', '-i', WORKER, 'tar', '--no-same-owner', '-xf', '-', '-C', '/opt/affine-probe', data=buf.getvalue())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['preflight', 'start', 'sync', 'run', 'benchmark', 'test', 'export', 'clone-start', 'restore'])
    p.add_argument('args', nargs=argparse.REMAINDER)
    a = p.parse_args()
    if a.action == 'preflight':
        preflight()
    elif a.action == 'start':
        preflight()
        save('invocation-state.json', json.loads((OUT / 'current-state.json').read_text()))
        for name in (WORKER, SERVER):
            if not inspect(name)['State']['Running']:
                podman('start', name, capture=False)
    elif a.action == 'sync':
        inspect(WORKER)
        sync()
    elif a.action == 'run':
        inspect(WORKER)
        podman('exec', WORKER, 'python', '/opt/affine-probe/affine_probe.py',
               '--root', REMOTE, *a.args, capture=False)
    elif a.action == 'benchmark':
        label, server = a.args[:2]
        info = json.loads(podman('inspect', server).stdout)[0]
        if server != SERVER and info['Config'].get('Labels', {}).get('affine.probe') != 'true':
            raise RuntimeError('Unowned performance target')
        stop = threading.Event()
        sample_errors = []
        output = OUT / ('resources-' + label + '.jsonl')
        def sample():
            with output.open('a') as f:
                while not stop.is_set():
                    began = time.time()
                    try:
                        raw = podman('exec', server, 'sh', '-c',
                                     'cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.current /proc/1/status').stdout.decode()
                    except Exception as error:
                        sample_errors.append(str(error))
                        return
                    f.write(json.dumps({'started': began, 'finished': time.time(), 'raw': raw}) + '\n')
                    f.flush()
                    stop.wait(.25)
        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        try:
            base = 'http://' + ('geoserver' if server == SERVER else server) + ':8080/geoserver'
            evidence = REMOTE if server == SERVER else REMOTE + '/' + server.removeprefix('affine-probe-')
            podman('exec', WORKER, 'python', '/opt/affine-probe/affine_probe.py', '--root', evidence,
                   '--base', base, 'benchmark', '--label', label, *a.args[2:], capture=False)
        finally:
            stop.set()
            thread.join(timeout=10)
        if sample_errors:
            raise RuntimeError('Resource sampling failed: ' + '; '.join(sample_errors))
    elif a.action == 'test':
        podman('exec', '-w', '/opt/affine-probe', WORKER, 'python', '-m', 'unittest',
               '-v', 'test_affine_probe', capture=False)
    elif a.action == 'export':
        # Stream files instead of relying on Windows/WSL bind-path translation.
        raw = podman('exec', WORKER, 'tar', '-cf', '-', '-C', REMOTE, '.').stdout
        OUT.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            archive.extractall(OUT / 'evidence', filter='data')
        print(OUT / 'evidence')
    elif a.action == 'clone-start':
        kind = a.args[0]
        if kind not in ('2285', 'directoff', 'patched', 'patched2', 'patched3', 'patched4', 'patched5'):
            raise ValueError('Unsupported comparison kind')
        name = 'affine-probe-' + kind
        existing = podman('ps', '-a', '--filter', 'name=^' + name + '$', '--format', '{{.Names}}').stdout.decode().strip()
        if existing:
            item = json.loads(podman('inspect', name).stdout)[0]
            if item['Config'].get('Labels', {}).get('affine.probe') != 'true':
                raise RuntimeError('Refusing unowned comparison container')
            podman('start', name, capture=False)
            return
        image = ('docker.osgeo.org/geoserver:2.28.5' if kind == '2285' else
                 'localhost/affine-probe:35.1-crop-read-clip' if kind == 'patched5' else
                 'localhost/affine-probe:35.1-uncropped' if kind == 'patched4' else
                 'localhost/affine-probe:35.1-crop-read-padding' if kind == 'patched3' else
                 'localhost/affine-probe:35.1-crop-read' if kind == 'patched2' else
                 'localhost/affine-probe:35.1-crop' if kind == 'patched' else inspect(SERVER)['ImageName'])
        if kind == '2285':
            podman('pull', image, capture=False)
        volume = name + '-catalog'
        podman('volume', 'create', '--label', 'affine.probe=true', volume)
        options = '-Xms4g -Xmx4g -XX:+UseG1GC'
        if kind == 'directoff':
            options += ' -Dorg.geoserver.render.raster.direct.disable=true'
        podman('run', '-d', '--name', name, '--label', 'affine.probe=true',
               '--network', 'rasterbench-israel-rgb-5gb-net', '--network-alias', name,
               '--cpus', '4', '--memory', '8g', '-p', '127.0.0.1:' + {'2285': '18086', 'directoff': '18087', 'patched': '18088', 'patched2': '18089', 'patched3': '18090', 'patched4': '18091', 'patched5': '18092'}[kind] + ':8080',
               '-v', 'rasterbench-israel-rgb-5gb-data:/data:ro', '-v', volume + ':/opt/geoserver_data',
               '--secret', 'rasterbench-israel-rgb-5gb-admin,target=admin_password',
               '-e', 'GEOSERVER_ADMIN_USER=admin', '-e', 'GEOSERVER_ADMIN_PASSWORD_FILE=/run/secrets/admin_password',
               '-e', 'SKIP_DEMO_DATA=true', '-e', 'INSTALL_EXTENSIONS=false', '-e', 'EXTRA_JAVA_OPTS=' + options,
               image, capture=False)
        save(name + '-image.json', {'image': image, 'inspect': json.loads(podman('image', 'inspect', image).stdout)[0]['Id']})
    elif a.action == 'restore':
        state_file = OUT / 'invocation-state.json'
        initial = json.loads((state_file if state_file.exists() else OUT / 'initial-state.json').read_text())
        if inspect(WORKER)['State']['Running'] and inspect(SERVER)['State']['Running']:
            podman('exec', WORKER, 'python', '/opt/affine-probe/affine_probe.py',
                   '--root', REMOTE, 'restore', capture=False)
        for name in (SERVER, WORKER):
            if not initial[name]['running'] and inspect(name)['State']['Running']:
                podman('stop', '--time', '30', name, capture=False)
        save('restored-state.json', {name: inspect(name)['State']['Running'] for name in initial})
        for kind in ('2285', 'directoff', 'patched', 'patched2', 'patched3', 'patched4', 'patched5', 'build'):
            name = 'affine-probe-' + kind
            existing = podman('ps', '-a', '--filter', 'name=^' + name + '$', '--format', '{{.Names}}').stdout.decode().strip()
            if existing:
                item = json.loads(podman('inspect', name).stdout)[0]
                if item['Config'].get('Labels', {}).get('affine.probe') == 'true' and item['State']['Running']:
                    podman('stop', '--time', '30', name, capture=False)


if __name__ == '__main__':
    main()
