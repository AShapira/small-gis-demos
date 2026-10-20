#!/usr/bin/env bash
# Copy the hook into the container layer, then use Kartoza's normal startup.
# Kartoza chowns its hooks directory; mounting that directory/file read-only
# can make startup fail. The payload itself stays on a read-only mount.
set -euo pipefail
bundle="${AFFINE_FIX_DIR:-/opt/affine-fix}"
test -f "$bundle/apply-affine-fix.sh"
mkdir -p /docker-entrypoint-geoserver.d
cp -- "$bundle/apply-affine-fix.sh" /docker-entrypoint-geoserver.d/90-affine-fix.sh
chmod 0644 /docker-entrypoint-geoserver.d/90-affine-fix.sh
exec /bin/bash /scripts/entrypoint.sh "$@"
