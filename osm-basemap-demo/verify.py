"""Real-data and live-service acceptance checks; writes durable evidence."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from demo import Engine, WORK, env_values, save_json
from catalog import EXTENT, Rest, STYLE_GROUPS, BASEMAP_STYLES


def mercator(lon, lat):
    return lon * EXTENT / 180, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * EXTENT / math.pi


def view_bbox(lon, lat, zoom, width=768, height=512):
    x, y = mercator(lon, lat)
    resolution = EXTENT * 2 / 256 / 2 ** zoom
    return [x-width/2*resolution, y-height/2*resolution, x+width/2*resolution, y+height/2*resolution]


def png_info(raw):
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise AssertionError('Expected PNG, received: ' + raw[:500].decode(errors='replace'))
    width, height = struct.unpack('>II', raw[16:24])
    if not width or not height:
        raise AssertionError('Empty PNG')
    return {'width': width, 'height': height, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def fetch_png(base, endpoint, params, path):
    url = base + endpoint + '?' + urllib.parse.urlencode(params)
    started = time.monotonic()
    with urllib.request.urlopen(url, timeout=180) as response:
        raw = response.read()
        content_type = response.headers.get('Content-Type', '')
    result = png_info(raw)
    if 'image/png' not in content_type:
        raise AssertionError(content_type)
    result['seconds'] = round(time.monotonic() - started, 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return result


def validate(cfg, target, container_name=None, cold_cache=False):
    evidence = target / 'validation'
    evidence.mkdir(exist_ok=True)
    report = {'complete': False, 'checks': {}, 'renders': {}, 'wmts_cache_initially_empty': cold_cache, 'notes': ['Visual quality requires inspection of the saved PNGs; valid PNG responses alone are not visual approval.']}
    if cold_cache:
        assert not next((target / 'cache').rglob('*.png'), None), 'A cold-cache measurement requires an empty tile cache'
    report_path = evidence / 'report.json'
    save_json(report_path, report)
    try:
        for name in ('regional.mbtiles', 'world.gpkg'):
            with sqlite3.connect(f'file:{target / "data_dir/data" / name}?mode=ro', uri=True) as db:
                integrity = db.execute('PRAGMA integrity_check').fetchall()
                assert integrity == [('ok',)], (name, integrity)
                report['checks'][name + '_integrity'] = 'ok'
                if name.endswith('mbtiles'):
                    metadata = dict(db.execute('SELECT name,value FROM metadata'))
                    report['checks']['tiles_by_zoom'] = dict(db.execute('SELECT zoom_level,COUNT(*) FROM tiles GROUP BY zoom_level'))
                    assert int(metadata['maxzoom']) == cfg['source_maxzoom']
                    report['checks']['vector_layers'] = [l['id'] for l in json.loads(metadata['json'])['vector_layers']]
                    assert set(('building','transportation','place','water')) <= set(report['checks']['vector_layers'])
                    points = [('Prague',14.421,50.087)] if target.name == 'smoke' else [('Prague',14.421,50.087),('Bratislava',17.108,48.146),('border',17.65,48.91)]
                    for name, lon, lat in points:
                        z = cfg['source_maxzoom']; n = 2 ** z
                        x = int((lon+180)/360*n)
                        y = int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
                        assert db.execute('SELECT 1 FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?', (z,x,n-1-y)).fetchone(), name
                else:
                    for table, in db.execute('SELECT table_name FROM gpkg_contents'):
                        count = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                        assert count > 0, table
                        report['checks'][table + '_features'] = count
        if (target / 'merged-info.json').exists():
            merged_path = target / 'merged-info.json'
        else:
            lock = json.loads((WORK / 'lock.json').read_text())
            merged_path = WORK / 'scratch' / lock['config_hash'][:12] / 'merged-info.json'
        merged = json.loads(merged_path.read_text())['data']
        assert merged['objects_ordered'] and not merged['multiple_versions']
        report['checks']['merged_osm'] = merged
        base = f'http://127.0.0.1:{cfg["port"]}/geoserver'
        client = Rest(base, env_values(target))
        report['checks']['version'] = json.loads(client.request('/rest/about/version.json'))
        font_data = client.request('/rest/fonts.json')
        (evidence / 'fonts.json').write_bytes(font_data)
        fonts = json.loads(font_data)['fonts']
        all_fonts = json.dumps(fonts)
        for family in ('Noto Sans', 'Metropolis', 'Nunito'):
            assert family in all_fonts, f'Missing font family {family}'
        for style in STYLE_GROUPS:
            sld = ET.fromstring(client.request(f'/rest/styles/{style}.sld'))
            ns = {'s': 'http://www.opengis.net/sld'}
            world50 = next(fts for fts in sld.findall('.//s:FeatureTypeStyle', ns) if fts.findtext('s:Name', namespaces=ns) == 'world-land-50')
            edge = float(world50.findtext('s:Rule/s:MinScaleDenominator', namespaces=ns))
            zoom6_scale = EXTENT * 2 / 256 / 2**6 / 0.00028
            assert zoom6_scale < edge < zoom6_scale * 1.000001, 'Overview must end exactly at display zoom six'
            if style.endswith('-en'):
                properties = {node.text for label in sld.findall('.//s:TextSymbolizer/s:Label', ns) for node in label.iter('{http://www.opengis.net/ogc}PropertyName')}
                assert properties <= {'name:en', 'NAME_EN', 'ref', 'housenumber'}, (style, properties)
                assert 'NAME_EN' in properties
                if style != 'world-en': assert 'name:en' in properties
                if style in ('osm-bright-en', 'positron-en', 'dark-matter-en'):
                    icon_rules = [r for r in sld.findall('.//s:Rule', ns)
                                  if (r.findtext('s:Name', namespaces=ns) or '').endswith('-unlabelled-icon')]
                    assert icon_rules, f'{style}: missing icons for untranslated features'
                    for rule in icon_rules:
                        assert rule.find('s:PointSymbolizer', ns) is None
                        symbol = rule.find('s:TextSymbolizer', ns)
                        assert symbol.find('s:Graphic', ns) is not None
                        assert symbol.findtext('s:Label', namespaces=ns) == ' '
                        assert symbol.findtext('s:Font/s:CssParameter[@name="font-size"]', namespaces=ns) == '0'
                        assert symbol.findtext('s:VendorOption[@name="conflictResolution"]', namespaces=ns) == 'true'
                        assert int(symbol.findtext('s:VendorOption[@name="spaceAround"]', namespaces=ns)) >= 40
                        assert 'name:en' in {p.text for p in rule.iter('{http://www.opengis.net/ogc}PropertyName')}
        report['checks']['english_name_properties'] = 'name:en and NAME_EN; no local-name fallback'
        report['checks']['style_zoom_boundary'] = '256-pixel grid with normalized inclusive thresholds'
        for endpoint in ('/ows?service=WMS&request=GetCapabilities', '/gwc/service/wmts?service=WMTS&request=GetCapabilities'):
            response = client.request(endpoint)
            ET.fromstring(response)
            for style in STYLE_GROUPS:
                assert ('omt:' + style).encode() in response, (endpoint, style)
            (evidence / ('wmts-capabilities.xml' if 'WMTS' in endpoint else 'wms-capabilities.xml')).write_bytes(response)
        views = {'world': [0,20,2], 'countries':[17.2,49,6], 'prague':[14.421,50.087,15], 'rural':[14.4,49.95,12], 'transition5':[15,49,5], 'transition6':[15,49,6], 'street18':[14.421,50.087,18]}
        views.update({f'zoom{z}': [14.421,50.087,z] for z in (14,16,17)})
        if target.name != 'smoke':
            views.update({'bratislava':[17.108,48.146,15], 'border':[17.65,48.91,12]})
        for style in BASEMAP_STYLES:
            for view, (lon,lat,z) in views.items():
                params = {'SERVICE':'WMS','VERSION':'1.3.0','REQUEST':'GetMap','LAYERS':'omt:'+style,'STYLES':'','CRS':'EPSG:3857','BBOX':','.join(map(str,view_bbox(lon,lat,z))),'WIDTH':768,'HEIGHT':512,'FORMAT':'image/png'}
                report['renders'][style+'-'+view] = fetch_png(base, '/wms', params, evidence / f'{style}-{view}.png')
            lon,lat,z = 14.421,50.087,15
            n = 2 ** z
            x = int((lon+180)/360*n); y = int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
            params = {'SERVICE':'WMTS','VERSION':'1.0.0','REQUEST':'GetTile','LAYER':'omt:'+style,'STYLE':'','TILEMATRIXSET':'EPSG:3857','TILEMATRIX':f'EPSG:3857:{z}','TILECOL':x,'TILEROW':y,'FORMAT':'image/png'}
            for label in (('cold','warm') if cold_cache else ('first','repeat')):
                report['renders'][style+'-wmts-'+label] = fetch_png(base, '/gwc/service/wmts', params, evidence / f'{style}-wmts-{label}.png')
            n = 2 ** 18
            params.update({'TILEMATRIX': 'EPSG:3857:18', 'TILECOL': int((lon+180)/360*n), 'TILEROW': int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)})
            report['renders'][style+'-wmts-18'] = fetch_png(base, '/gwc/service/wmts', params, evidence / f'{style}-wmts-18.png')
            params = {'SERVICE':'WMS','VERSION':'1.3.0','REQUEST':'GetMap','LAYERS':'omt:'+style,'STYLES':'','CRS':'EPSG:4326','BBOX':'50.075,14.40,50.10,14.45','WIDTH':768,'HEIGHT':512,'FORMAT':'image/png'}
            report['renders'][style+'-4326'] = fetch_png(base, '/wms', params, evidence / f'{style}-4326.png')
            save_json(report_path, report)
        engine = Engine(cfg)
        name = container_name or ('osm-basemap-smoke' if target.name == 'smoke' else 'osm-basemap-demo')
        inspection = json.loads(engine.run(['inspect', name], capture=True))[0]
        networks = inspection['NetworkSettings']['Networks']
        for network in networks:
            info = json.loads(engine.run(['network','inspect',network],capture=True))[0]
            assert info.get('internal', info.get('Internal')), 'Network has outbound routing'
        result = engine.run(['exec',name,'bash','-c','if curl -fsS --max-time 5 https://example.com >/dev/null 2>&1; then exit 42; else echo outbound-blocked; fi'],capture=True)
        assert 'outbound-blocked' in result
        report['checks']['outbound_blocked'] = True
        quota = ET.fromstring(client.request('/gwc/rest/diskquota.xml'))
        assert quota.findtext('enabled') == 'true'
        assert int(quota.findtext('globalQuota/bytes')) == 20 * 1024**3
        report['checks']['cache_quota_bytes'] = int(quota.findtext('globalQuota/bytes'))
        for style in ('world', 'world-en'):
            params = {'SERVICE':'WMS','VERSION':'1.3.0','REQUEST':'GetMap','LAYERS':'omt:'+style,'STYLES':'','CRS':'EPSG:3857','BBOX':','.join(map(str,view_bbox(0,20,2))),'WIDTH':768,'HEIGHT':512,'FORMAT':'image/png'}
            report['renders']['standalone-'+style] = fetch_png(base, '/wms', params, evidence / f'standalone-{style}.png')
        report['checks']['container_memory'] = engine.run(['stats','--no-stream','--format','{{.MemUsage}}',name],capture=True).strip()
        (evidence/'geoserver.log').write_text(engine.run(['logs',name],capture=True))
        report['complete'] = True
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        save_json(report_path, report)
    print(f'Validation passed: {report_path}')
