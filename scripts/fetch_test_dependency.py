"""Fetch the pinned downloader for offline unit tests; no accounts or imagery."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    lock = json.loads((ROOT / 'infra/images.lock.json').read_text())
    out = ROOT / '.build' / 'copernicus'
    out.mkdir(parents=True, exist_ok=True)
    for name in ('copernicus_downloader.py', 'LICENSE'):
        expected = lock['downloader_files'][name]['sha256']
        target = out / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            continue
        url = 'https://raw.githubusercontent.com/AShapira/copernicus-downloader/{}/{}'.format(
            lock['downloader_commit'], name)
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Downloader checksum mismatch: ' + name)
        target.write_bytes(data)


if __name__ == '__main__':
    main()
