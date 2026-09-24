"""Pinned Copernicus downloader adapter; frozen per-tile selection and verified reuse."""
from __future__ import annotations

import concurrent.futures
import dataclasses
import datetime as dt
import json
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path

from .common import measured, read_json, sha256, utc, write_json


def verify_archive(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip(): raise ValueError('Archive CRC validation failed')
    return {'bytes':Path(path).stat().st_size,'sha256':sha256(path)}


def serialize(p):
    from shapely.geometry import mapping
    a = {f.name: getattr(p, f.name) for f in dataclasses.fields(p)}
    a['footprint'] = mapping(p.footprint)
    for k in ('acquisition_start', 'acquisition_date', 'publication_date'):
        a[k] = a[k].isoformat() if a[k] else None
    return a


def deserialize(a):
    import copernicus_downloader as d
    from shapely.geometry import shape
    a = dict(a)
    a['footprint'] = shape(a['footprint'])
    a['acquisition_start'] = dt.datetime.fromisoformat(a['acquisition_start'])
    a['acquisition_date'] = dt.date.fromisoformat(a['acquisition_date'])
    a['publication_date'] = dt.datetime.fromisoformat(a['publication_date']) if a['publication_date'] else None
    return d.Product(**a)


def select(products, aoi, maximum):
    """Greedy geographic coverage, then lowest cloud for each MGRS tile.

    Do not rely on one acquisition date covering multiple satellite swaths.
    """
    from shapely.geometry import GeometryCollection
    best = {}
    by_tile = {}
    for p in products:
        if not p.online or not p.footprint.intersects(aoi):
            continue
        by_tile.setdefault(p.tile_id,[]).append(p)
    # Some acquisitions contain only a narrow sliver of an MGRS tile. Qualify
    # footprint coverage before cloud ranking or "lowest cloud" can lose half the AOI.
    for tile, candidates in by_tile.items():
        maximum_area=max(p.footprint.intersection(aoi).area for p in candidates)
        candidates=[p for p in candidates if p.footprint.intersection(aoi).area>=maximum_area*.999]
        p=min(candidates,key=lambda p:(p.cloud_cover,-p.acquisition_start.timestamp(),p.id))
        key = (p.cloud_cover, -p.acquisition_start.timestamp(), p.id)
        old = best.get(p.tile_id)
        if old is None or key < (old.cloud_cover, -old.acquisition_start.timestamp(), old.id):
            best[p.tile_id] = p
    coverage, chosen = GeometryCollection(), []
    remaining = list(best.values())
    while remaining and len(chosen) < maximum:
        remaining.sort(key=lambda p: (-p.footprint.intersection(aoi).difference(coverage).area,
                                      p.cloud_cover, p.id))
        p = remaining.pop(0)
        gain = p.footprint.intersection(aoi).difference(coverage).area
        if gain < aoi.area * .00001:
            break
        chosen.append(p)
        coverage = coverage.union(p.footprint.intersection(aoi))
        if coverage.area / aoi.area >= .9999:
            break
    # A second acquisition of the same tile can fill a swath-edge gap left by
    # even its largest individual footprint. Add only products with new coverage.
    fallback=[p for ps in by_tile.values() for p in ps if p.id not in {x.id for x in chosen}]
    while fallback and len(chosen)<maximum and coverage.area/aoi.area < .9999:
        gains=[(p,p.footprint.intersection(aoi).difference(coverage).area) for p in fallback]
        maximum_gain=max(g for _,g in gains)
        if maximum_gain<aoi.area*.000001: break
        candidates=[p for p,g in gains if g>=maximum_gain*.99]
        p=min(candidates,key=lambda p:(p.cloud_cover,-p.acquisition_start.timestamp(),p.id))
        chosen.append(p); coverage=coverage.union(p.footprint.intersection(aoi))
        fallback=[x for x in fallback if x.id!=p.id]
    if not chosen or coverage.area / aoi.area < .995:
        raise RuntimeError(f'Catalogue covers only {coverage.area / aoi.area:.2%}; expand dates/bounds in a new study')
    # High cloud first; the last source wins in the mosaic.
    return sorted(chosen, key=lambda p: (-p.cloud_cover, p.acquisition_start, p.id))


def target_polygon(c):
    from osgeo import osr
    from shapely.geometry import Polygon
    src, dst = osr.SpatialReference(), osr.SpatialReference()
    src.SetFromUserInput(c['raster']['crs'])
    dst.ImportFromEPSG(4326)
    for s in (src, dst):
        s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tx = osr.CoordinateTransformation(src, dst)
    x0, y0, x1, y1 = c['raster']['bounds']
    corners = [(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)]
    pts = []
    for a,b in zip(corners,corners[1:]):
        for i in range(21):
            p = tx.TransformPoint(a[0]+(b[0]-a[0])*i/20, a[1]+(b[1]-a[1])*i/20)
            pts.append(p[:2])
    return Polygon(pts)


def run(c, root, select_only=False):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import copernicus_downloader as d
    root = Path(root)
    downloads = root/'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    frozen = root/'selection.json'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    if frozen.exists():
        selection = read_json(frozen)
        if selection['downloader_commit'] != c['source']['downloader_commit']:
            raise ValueError('Downloader revision changed; use a new study')
        products = [deserialize(p) for p in selection['products']]
    else:
        session = requests.Session()
        session.mount('https://', HTTPAdapter(max_retries=Retry(total=4, backoff_factor=2,
                                                              status_forcelist=[429,500,502,503,504])))
        aoi = target_polygon(c)
        bounds = list(aoi.bounds)
        products = d.search_products(session, bounds, dt.date.fromisoformat(c['source']['start_date']),
                                     dt.date.fromisoformat(c['source']['end_date']), 1000)
        products = select(d.deduplicate_products(products), aoi, c['source']['max_products'])
        selection = {'schema_version':1, 'created_at':utc(), 'downloader_commit':c['source']['downloader_commit'],
                     'policy':'lowest-cloud-per-tile, greedy unique coverage, lower-cloud mosaic priority',
                     'search_bbox':bounds, 'products':[serialize(p) for p in products]}
        write_json(frozen, selection)
    source_bytes = sum(p.content_length or 1_000_000_000 for p in products)
    # Source ZIPs, canonical+overviews, variants, temporary files, metrics/cache allowance.
    payload = c['raster']['minimum_valid_bytes']
    estimate = source_bytes + payload * 16 + 20_000_000_000
    write_json(root/'storage-estimate.json', {'source_bytes':source_bytes,'estimated_peak_bytes':estimate,
                                           'product_count':len(products), 'initial_estimate':True})
    print(json.dumps({'selection_products':len(products),'source_gb':source_bytes/1e9,
                      'estimated_peak_gb':estimate/1e9}), flush=True)
    if select_only:
        return
    free = shutil.disk_usage(root).free
    remaining = max(0, source_bytes - sum(p.stat().st_size for p in downloads.glob('*.zip')))
    if free < remaining + payload*12 + c['runtime']['disk_reserve_gb']*1e9:
        raise RuntimeError('Insufficient volume space for selected products and benchmark working set')
    auth = d.CdseAuth(d.get_secret('CDSE_USERNAME',None), d.get_secret('CDSE_PASSWORD',None))
    auth.login()
    store = d.StateStore(downloads/'state.db')
    hashes = root/'download-checksums'
    hashes.mkdir(exist_ok=True)
    def download(p):
        destination = downloads/d.product_filename(p)
        marker = hashes/f'{p.id}.json'
        old = read_json(marker)
        if old and destination.exists() and destination.stat().st_size == old['bytes'] and sha256(destination) == old['sha256']:
            print(f'Verified reuse: {p.tile_id}', flush=True)
            return
        if destination.exists():
            # Preserve unverified files for diagnosis; never append them to a download.
            destination.rename(destination.with_suffix('.zip.unverified'))
        store.ensure_download('frozen-regional-mosaic', p, destination)
        for attempt in range(4):
            try:
                d.download_product(auth, store, p, destination)
                verified=verify_archive(destination)
                write_json(marker, {'product_id':p.id,'name':p.name,**verified,'verified_at':utc()})
                print(f'Download verified: {p.tile_id}', flush=True)
                return
            except Exception:
                if destination.exists():
                    destination.rename(destination.with_suffix(f'.zip.failed-{attempt}'))
                if attempt == 3:
                    raise
                time.sleep(min(60, 5 * 2**attempt))
    try:
        with measured(root/'fetch.metrics.json', {'products':len(products)}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=c['source']['concurrency']) as pool:
                for f in concurrent.futures.as_completed([pool.submit(download,p) for p in products]):
                    f.result()
    finally:
        store.close()
