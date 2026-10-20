#!/usr/bin/env python3
"""Package only explicitly selected files; never include private lab artifacts."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
NAME = ROOT.name
RUNTIME = (
    'README.md', 'VALIDATION.md', 'NOTICE.md', 'LICENSE', 'compose.override.yaml',
    'apply-affine-fix.sh', 'start-with-affine-fix.sh', 'jars.sha256.tsv',
    'jars/gt-render-34.0.jar', 'jars/affine-0.9.0.jar',
    'LICENSES/geotools-LICENSE.md', 'LICENSES/imagen-LICENSE.md',
)
SOURCES = (
    'build.py', 'build-lock.json', 'package.py',
    'source/README.md',
    'source/project-MIT.txt',
    'source/GridCoverageReaderHelper.java',
    'source/GridCoverageReaderHelper.java.original',
    'source/AffineOpImage.java', 'source/AffineOpImage.java.original',
    'source/geotools-34.0.patch', 'source/imagen-0.9.0.patch',
    'source/gt-render-34.0-sources.jar', 'source/affine-0.9.0-sources.jar',
    'tests/test_startup.py', 'tests/AffineScanlineRegression.java',
    'evidence/2.28.0-inspection.json', 'evidence/2.28.0-stock.json',
    'evidence/2.28.0-patched.json', 'evidence/2.28.0-patched-g1.jsonl.gz',
    'evidence/2.28.0-regression.jsonl.gz',
    'evidence/regression-original.txt', 'evidence/regression-patched.txt',
    'evidence/build.json', 'evidence/package-install.json',
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def package(suffix, files, manifest):
    checksums = ''.join(f'{sha((ROOT / n).read_bytes())}  {n}\n' for n in sorted(files))
    (ROOT / manifest).write_text(checksums)
    destination = ROOT / f'{NAME}-{suffix}.zip'
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for name in sorted((*files, manifest)):
            entry = zipfile.ZipInfo(f'{NAME}/{name}', date_time=(2026, 10, 20, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (ROOT / name).read_bytes())
    checksum = sha(destination.read_bytes())
    destination.with_suffix('.zip.sha256').write_text(f'{checksum}  {destination.name}\n')
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP integrity failure')
        for name in files:
            if archive.read(f'{NAME}/{name}') != (ROOT / name).read_bytes():
                raise RuntimeError('ZIP member mismatch: ' + name)
    print(f'{destination.name}: {destination.stat().st_size:,} bytes; SHA-256 {checksum}')


def main():
    lock = json.loads((ROOT / 'build-lock.json').read_text())
    for name, checksum in lock['source_sha256'].items():
        if sha((ROOT / 'source' / name).read_bytes()) != checksum:
            raise RuntimeError('Source checksum mismatch: ' + name)
    expected = []
    for name, original in lock['original_jars'].items():
        patched = lock['patched_jars'][name]
        if sha((ROOT / 'jars' / name).read_bytes()) != patched:
            raise RuntimeError('Payload differs from validated JAR: ' + name)
        expected.append(f'{name}\t{original}\t{patched}\n')
    if (ROOT / 'jars.sha256.tsv').read_text() != ''.join(expected):
        raise RuntimeError('Installer manifest differs from the build lock')
    if set(RUNTIME) & set(SOURCES):
        raise RuntimeError('Archive contents overlap')
    package('runtime', RUNTIME, 'SHA256SUMS')
    package('sources', SOURCES, 'SOURCE_SHA256SUMS')


if __name__ == '__main__':
    main()
