#!/bin/bash
set -euo pipefail
# Kartoza creates bootstrap files as root before dropping to its service user.
# Restrict repairs to this deployment's writable catalog/cache; leave map data alone.
owner="${GEOSERVER_UID:-2000}:${GEOSERVER_GID:-2000}"
find /opt/geoserver/data_dir -path /opt/geoserver/data_dir/data -prune -o -exec chown "$owner" {} +
chown -R "$owner" /opt/geoserver/gwc
