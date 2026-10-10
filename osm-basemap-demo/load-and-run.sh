#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
sha256sum --check SHA256SUMS
docker load --input images/geoserver.tar
python3 - <<'PY'
import json, subprocess
from pathlib import Path
locked=json.loads(Path('source-manifest.json').read_text())['images']['geoserver']
actual=json.loads(subprocess.check_output(['docker','image','inspect',locked['tag']]))[0]['Id']
if actual.removeprefix('sha256:') != locked['id'].removeprefix('sha256:'):
    raise SystemExit('Loaded GeoServer image does not match source-manifest.json')
PY
# This affects only directories inside this unpacked deployment bundle.
# Give Kartoza's service user ownership before fonts are mounted read-only.
docker run --rm --pull=never --network none --user 0:0 \
  --entrypoint /bin/bash \
  -v "$PWD/data_dir:/catalog" -v "$PWD/fonts:/fonts" -v "$PWD/cache:/cache" \
  docker.io/kartoza/geoserver:3.0.1 -ec 'chown -R 2000:2000 /catalog /fonts /cache'
docker compose up -d --pull never
docker compose ps
