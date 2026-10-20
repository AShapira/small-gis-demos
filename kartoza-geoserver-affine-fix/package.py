#!/usr/bin/env python3
"""Package public deployment files; never include private lab artifacts."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / 'kartoza-geoserver-2.28.5-affine-fix.zip'


def public_files():
    for path in sorted(ROOT.rglob('*')):
        relative = path.relative_to(ROOT)
        if any(part in ('.artifacts', '__pycache__', '.venv') for part in relative.parts):
            continue
        if not path.is_file() or path.name == 'SHA256SUMS' or path.name.endswith(('.zip', '.zip.sha256', '.pyc')):
            continue
        if path.is_symlink():
            raise RuntimeError('Do not package symlinks: ' + str(relative))
        yield path


def main():
    files = list(public_files())
    checksums = ROOT / 'SHA256SUMS'
    checksums.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT).as_posix()}\n' for p in files))
    files.append(checksums)
    with zipfile.ZipFile(ARCHIVE, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, arcname='kartoza-geoserver-affine-fix/' + path.relative_to(ROOT).as_posix())
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    ARCHIVE.with_suffix('.zip.sha256').write_text(digest + '  ' + ARCHIVE.name + '\n')
    with zipfile.ZipFile(ARCHIVE) as archive:
        assert archive.testzip() is None
        assert not any('/.artifacts/' in n or 'admin-password' in n for n in archive.namelist())
    print(f'{ARCHIVE.name}: {len(files)} files, {ARCHIVE.stat().st_size:,} bytes\nSHA-256 {digest}')


if __name__ == '__main__':
    main()
