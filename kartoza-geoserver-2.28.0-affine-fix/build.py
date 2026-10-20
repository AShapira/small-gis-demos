#!/usr/bin/env python3
"""Compile two version-locked classes in a stock container; never build/commit an image."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', default='docker', help='Docker or Podman executable')
    parser.add_argument('--connection', help='Optional Podman connection')
    args = parser.parse_args()
    engine = [args.engine] + (['--connection', args.connection] if args.connection else [])
    lock = json.loads((ROOT / 'build-lock.json').read_text())
    for name, sha in lock['source_sha256'].items():
        if digest((ROOT / 'source' / name).read_bytes()) != sha:
            raise RuntimeError('Source checksum mismatch: ' + name)
    container = 'kartoza-affine-compile-' + uuid.uuid4().hex[:10]

    def call(*command, data=None, check=True):
        r = subprocess.run(engine + list(command), input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check and r.returncode:
            raise RuntimeError(r.stderr.decode(errors='replace') + r.stdout.decode(errors='replace'))
        return r

    # Override image-declared volumes with temporary memory filesystems so the
    # compiler creates no persistent catalog/cache volumes on the engine host.
    config = json.loads(call('image', 'inspect', lock['image']).stdout)[0]
    volumes = config.get('Config', {}).get('Volumes') or {}
    temporary_mounts = [arg for path in sorted(volumes) for arg in ('--tmpfs', path)]
    call('run', '-d', '--name', container, '--label', 'affine.kartoza.build=true',
         '--platform', lock['platform'], '--pull=never', '--network', 'none',
         *temporary_mounts, '--entrypoint', 'sleep', lock['image'], 'infinity')
    try:
        lib = lock['library_directory']
        originals = {}
        for name, sha in lock['original_jars'].items():
            data = call('exec', container, 'cat', lib + '/' + name).stdout
            if digest(data) != sha:
                raise RuntimeError('Image library checksum mismatch: ' + name)
            originals[name] = data
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as archive:
            for name in ('GridCoverageReaderHelper.java', 'AffineOpImage.java'):
                archive.add(ROOT / 'source' / name, arcname=name)
            archive.add(ROOT / 'tests/AffineScanlineRegression.java', arcname='AffineScanlineRegression.java')
        call('exec', container, 'mkdir', '-p', '/tmp/affine-build/classes', '/tmp/affine-build/tests')
        call('exec', '-i', container, 'tar', '-xf', '-', '-C', '/tmp/affine-build', data=buf.getvalue())
        call('exec', container, 'javac', '--release', str(lock['java_release']), '-proc:none',
             '-cp', lib + '/*', '-d', '/tmp/affine-build/classes',
             '/tmp/affine-build/GridCoverageReaderHelper.java', '/tmp/affine-build/AffineOpImage.java')
        call('exec', container, 'javac', '--release', str(lock['java_release']), '-proc:none',
             '-cp', lib + '/*', '-d', '/tmp/affine-build/tests', '/tmp/affine-build/AffineScanlineRegression.java')
        test = 'org.eclipse.imagen.media.affine.AffineScanlineRegression'
        baseline = call('exec', container, 'java', '-cp', '/tmp/affine-build/tests:' + lib + '/*', test, check=False)
        if baseline.returncode == 0 or b'Expected [0,2) but got RangeInt[0, 3]' not in baseline.stderr:
            raise RuntimeError('Original library did not fail the expected regression')
        corrected = call('exec', container, 'java', '-cp',
                         '/tmp/affine-build/tests:/tmp/affine-build/classes:' + lib + '/*', test)
        class_tar = call('exec', container, 'tar', '-cf', '-', '-C', '/tmp/affine-build/classes', '.').stdout
        with tarfile.open(fileobj=io.BytesIO(class_tar)) as archive:
            classes = {m.name.removeprefix('./'): archive.extractfile(m).read()
                       for m in archive.getmembers() if m.isfile()}
        expected = {
            'gt-render-34.0.jar': 'org/geotools/renderer/lite/gridcoverage2d/GridCoverageReaderHelper.class',
            'affine-0.9.0.jar': 'org/eclipse/imagen/media/affine/AffineOpImage.class',
        }
        if set(classes) != set(expected.values()):
            raise RuntimeError('Unexpected compiler output: ' + repr(list(classes)))
        results = {}
        (ROOT / 'jars').mkdir(exist_ok=True)
        for name, entry in expected.items():
            destination = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(originals[name])) as source, zipfile.ZipFile(destination, 'w') as target:
                if entry not in source.namelist():
                    raise RuntimeError('Missing original class: ' + entry)
                for info in source.infolist():
                    target.writestr(info, classes[entry] if info.filename == entry else source.read(info.filename))
            jar = destination.getvalue()
            # Check that all other archive members have exactly their original bytes.
            with zipfile.ZipFile(io.BytesIO(originals[name])) as a, zipfile.ZipFile(io.BytesIO(jar)) as b:
                changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
                if a.namelist() != b.namelist() or changed != [entry]:
                    raise RuntimeError('Unexpected archive changes: ' + repr(changed))
            if digest(jar) != lock['patched_jars'][name]:
                raise RuntimeError('Build differs from the validated payload: ' + name)
            (ROOT / 'jars' / name).write_bytes(jar)
            results[name] = {'original_sha256': digest(originals[name]), 'patched_sha256': digest(jar),
                             'changed_entries': changed, 'bytes': len(jar)}
        (ROOT / 'jars.sha256.tsv').write_text(''.join(
            f"{name}\t{r['original_sha256']}\t{r['patched_sha256']}\n" for name, r in results.items()))
        evidence = ROOT / 'evidence'
        evidence.mkdir(exist_ok=True)
        (evidence / 'build.json').write_text(json.dumps({
            'image': lock['image'], 'custom_image_built': False,
            'java': call('exec', container, 'javac', '-version').stdout.decode().strip(),
            'libraries': results, 'original_regression_exit': baseline.returncode,
            'patched_regression_exit': corrected.returncode,
        }, indent=2) + '\n')
        (evidence / 'regression-original.txt').write_bytes(baseline.stdout + baseline.stderr)
        (evidence / 'regression-patched.txt').write_bytes(corrected.stdout + corrected.stderr)
        print(json.dumps(results, indent=2))
        print(corrected.stdout.decode().strip())
    finally:
        # This unique disposable compiler container has no host mounts or source data.
        call('rm', '-f', container)


if __name__ == '__main__':
    main()
