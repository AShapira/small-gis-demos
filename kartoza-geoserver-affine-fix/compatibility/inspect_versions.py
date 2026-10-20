#!/usr/bin/env python3
"""Inspect stock Kartoza images and compile isolated per-version patch candidates.

Requires the selected pinned images already present. Does not build images or
change existing containers. Downloaded libraries and candidates stay in .artifacts.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.artifacts/compatibility'
LIB = '/usr/local/tomcat/webapps/geoserver/WEB-INF/lib'
CLASSES = {
    'gt-render': 'org/geotools/renderer/lite/gridcoverage2d/GridCoverageReaderHelper.class',
    'affine': 'org/eclipse/imagen/media/affine/AffineOpImage.class',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, path):
    if not path.exists():
        with urllib.request.urlopen(url, timeout=90) as response:
            path.write_bytes(response.read())
    return path.read_bytes()


def tar_files(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def inspect(tag, engine):
    row = next(r for r in json.loads((WORK / 'dockerhub-tags.json').read_text()) if r['name'] == tag)
    image = 'docker.io/kartoza/geoserver@' + row['digest']
    label = tag.replace('--', '-')
    out = WORK / label
    out.mkdir(exist_ok=True)
    candidate = out / 'candidate'
    (candidate / 'jars').mkdir(parents=True, exist_ok=True)
    container = 'affine-compat-inspect-' + label + '-' + uuid.uuid4().hex[:6]

    def call(*args, data=None, check=True):
        result = subprocess.run(engine + list(args), input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check and result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace') + result.stdout.decode(errors='replace'))
        return result

    # Refuse to reuse an existing container so the baseline is always stock.
    call('run', '--pull=never', '-d', '--name', container, '--label', 'affine.compatibility=2026-10-20',
         '--cpus', '4', '--memory', '4g', '--network', 'none', '--entrypoint', 'sleep', image, 'infinity')
    result = {'tag': tag, 'image': image, 'architecture': 'linux/amd64', 'container': container}
    try:
        info = json.loads(call('inspect', container).stdout)[0]
        result['image_id'] = info['Image']
        names = call('exec', container, 'find', LIB, '-maxdepth', '1', '-name', '*.jar', '-printf', '%f\n').stdout.decode().splitlines()
        selected = {}
        originals = {}
        for prefix in CLASSES:
            matches = [n for n in names if re.fullmatch(prefix + r'-[0-9.]+\.jar', n)]
            assert len(matches) == 1, (prefix, matches)
            name = matches[0]
            selected[prefix] = name
            originals[name] = call('exec', container, 'cat', LIB + '/' + name).stdout
            (out / name).write_bytes(originals[name])
        result['original_jars'] = {n: sha(b) for n, b in originals.items()}
        result['geotools'] = selected['gt-render'][10:-4]
        result['imagen'] = selected['affine'][7:-4]
        result['java'] = call('exec', container, 'javac', '-version').stdout.decode().strip()
        result['geoserver_jars'] = [n for n in names if n.startswith(('gs-main-', 'gwc-core-'))]
        result['entrypoint_scripts'] = {}
        for path in ['/scripts/entrypoint.sh', '/scripts/functions.sh', '/scripts/startup.sh']:
            response = call('exec', container, 'cat', path, check=False)
            if not response.returncode:
                (out / Path(path).name).write_bytes(response.stdout)
                result['entrypoint_scripts'][path] = sha(response.stdout)
        source_files = {}
        result['sources'] = {}
        for prefix, entry in CLASSES.items():
            name = selected[prefix]
            version = name[len(prefix) + 1:-4]
            group = 'org/geotools' if prefix == 'gt-render' else 'org/eclipse/imagen'
            url = f'https://repo.osgeo.org/repository/release/{group}/{prefix}/{version}/{prefix}-{version}-sources.jar'
            archive = fetch(url, out / (name[:-4] + '-sources.jar'))
            with zipfile.ZipFile(io.BytesIO(archive)) as jar:
                original = jar.read(entry[:-6] + '.java')
            base = Path(entry).stem + '.java'
            (out / base).write_bytes(original)
            (out / (base + '.original')).write_bytes(original)
            patch_name = 'geotools-34.5.patch' if prefix == 'gt-render' else 'imagen-0.9.1.patch'
            applied = subprocess.run(['git', 'apply', '--no-index', '-p0', '--verbose', '-'], cwd=out,
                                     input=(ROOT / 'source' / patch_name).read_bytes(), capture_output=True)
            (out / (prefix + '-patch.log')).write_bytes(applied.stdout + applied.stderr)
            assert applied.returncode == 0, applied.stdout.decode() + applied.stderr.decode()
            source_files[base] = (out / base).read_bytes()
            result['sources'][prefix] = {'url': url, 'archive_sha256': sha(archive),
                'original_class_source_sha256': sha(original), 'patched_source_sha256': sha(source_files[base]),
                'patch_method': 'git apply --no-index -p0; exact context', 'patch_output': (applied.stdout + applied.stderr).decode().strip()}
        source_files['AffineScanlineRegression.java'] = (ROOT / 'tests/AffineScanlineRegression.java').read_bytes()
        call('exec', container, 'mkdir', '-p', '/tmp/affine-build/classes', '/tmp/affine-build/tests', '/opt/original-bundle/jars')
        call('exec', '-i', container, 'tar', '-xf', '-', '-C', '/tmp/affine-build', data=tar_files(source_files))
        call('exec', container, 'javac', '--release', '17', '-proc:none', '-cp', LIB + '/*', '-d', '/tmp/affine-build/classes',
             '/tmp/affine-build/GridCoverageReaderHelper.java', '/tmp/affine-build/AffineOpImage.java')
        call('exec', container, 'javac', '--release', '17', '-proc:none', '-cp', LIB + '/*', '-d', '/tmp/affine-build/tests',
             '/tmp/affine-build/AffineScanlineRegression.java')
        test = 'org.eclipse.imagen.media.affine.AffineScanlineRegression'
        baseline = call('exec', container, 'java', '-cp', '/tmp/affine-build/tests:' + LIB + '/*', test, check=False)
        corrected = call('exec', container, 'java', '-cp', '/tmp/affine-build/tests:/tmp/affine-build/classes:' + LIB + '/*', test)
        (out / 'regression-original.txt').write_bytes(baseline.stdout + baseline.stderr)
        (out / 'regression-patched.txt').write_bytes(corrected.stdout + corrected.stderr)
        assert baseline.returncode != 0 and b'Expected [0,2) but got RangeInt[0, 3]' in baseline.stderr
        result['regression'] = {'original_exit': baseline.returncode, 'patched_exit': corrected.returncode}
        manifest = []
        result['patched_jars'] = {}
        for prefix, entry in CLASSES.items():
            name = selected[prefix]
            replacement = call('exec', container, 'cat', '/tmp/affine-build/classes/' + entry).stdout
            buf = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(originals[name])) as source, zipfile.ZipFile(buf, 'w') as target:
                for item in source.infolist():
                    target.writestr(item, replacement if item.filename == entry else source.read(item.filename))
            patched = buf.getvalue()
            with zipfile.ZipFile(io.BytesIO(originals[name])) as a, zipfile.ZipFile(io.BytesIO(patched)) as b:
                assert a.namelist() == b.namelist()
                assert [n for n in a.namelist() if a.read(n) != b.read(n)] == [entry]
            (candidate / 'jars' / name).write_bytes(patched)
            manifest.append(f'{name}\t{sha(originals[name])}\t{sha(patched)}\n')
            result['patched_jars'][name] = {'sha256': sha(patched), 'only_changed_entry': entry}
        (candidate / 'jars.sha256.tsv').write_text(''.join(manifest))
        hook = (ROOT / 'apply-affine-fix.sh').read_text()
        for old, new in [('34.5', result['geotools']), ('0.9.1', result['imagen'])]:
            hook = hook.replace(old.replace('.', r'\.'), new.replace('.', r'\.')).replace(old, new)
        (candidate / 'apply-affine-fix.sh').write_text(hook)
        (candidate / 'start-with-affine-fix.sh').write_bytes((ROOT / 'start-with-affine-fix.sh').read_bytes())
        original_bundle = {n: (ROOT / n).read_bytes() for n in ['apply-affine-fix.sh', 'jars.sha256.tsv', 'jars/gt-render-34.5.jar', 'jars/affine-0.9.1.jar']}
        call('exec', '-i', container, 'tar', '-xf', '-', '-C', '/opt/original-bundle', data=tar_files(original_bundle))
        installed = call('exec', '-e', 'AFFINE_FIX_DIR=/opt/original-bundle', container, 'bash', '-c', '. /opt/original-bundle/apply-affine-fix.sh || true; echo JVM_WOULD_START', check=False)
        result['existing_bundle_test'] = {'exit': installed.returncode, 'log': (installed.stdout + installed.stderr).decode()}
        # Older patch releases must be rejected before either library is changed.
        if result['geotools'] != '34.5':
            assert installed.returncode != 0 and b'JVM_WOULD_START' not in installed.stdout
            for name, data in originals.items():
                assert call('exec', container, 'cat', LIB + '/' + name).stdout == data
            result['existing_bundle_test']['both_originals_preserved'] = True
        (out / 'inspection.json').write_text(json.dumps(result, indent=2) + '\n')
        print(tag, result['geotools'], result['imagen'], 'compiled; original regression fails, patched passes; existing bundle exit', installed.returncode, flush=True)
    finally:
        call('stop', '--time', '2', container)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tags', nargs='+')
    parser.add_argument('--engine', default='podman')
    parser.add_argument('--connection')
    args = parser.parse_args()
    engine = [args.engine] + (['--connection', args.connection] if args.connection else [])
    for tag in args.tags:
        inspect(tag, engine)


if __name__ == '__main__':
    main()
