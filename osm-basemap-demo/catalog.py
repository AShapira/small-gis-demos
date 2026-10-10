"""Style adaptation and one-time offline GeoServer catalog provisioning."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

STYLE_FILES = {'osm-bright': 'osm-bright-gl', 'positron': 'positron-gl', 'dark-matter': 'dark-matter', 'toner': 'toner-gl', 'maptiler-basic': 'maptiler-basic-gl'}
LOCAL_GROUPS = (*STYLE_FILES, 'world')
ENGLISH_GROUPS = tuple(name + '-en' for name in LOCAL_GROUPS)
STYLE_GROUPS = (*LOCAL_GROUPS, *ENGLISH_GROUPS)
BASEMAP_STYLES = (*STYLE_FILES, *(name + '-en' for name in STYLE_FILES))
NAME_TOKEN = re.compile(r'\{name(?::[^}]+|_[^}]+)?\}')
ENGLISH_POI_PADDING = 16
UNLABELLED_ICON_PADDING = 40
EXTENT = 20037508.342789244


def normalize_metadata(path, archive):
    """Declare optional OMT attributes even when a small extract has no values for them.

    GeoServer validates styles against the MBTiles metadata schema, while Planetiler
    reports fields observed in the generated tiles. Geometry and tile bytes stay intact.
    """
    required = {}
    with zipfile.ZipFile(archive) as z:
        for filename in STYLE_FILES.values():
            entry = next(p for p in z.namelist() if p.endswith(f'/workspaces/omt/styles/{filename}.json'))
            for layer in json.loads(z.read(entry))['layers']:
                source = layer.get('source-layer')
                if not source:
                    continue
                fields = required.setdefault(source, {})
                def field(name, kind='String'):
                    if name not in ('$type', '$id'):
                        fields[name] = kind if kind != 'String' else fields.get(name, kind)
                def walk(value):
                    if isinstance(value, list):
                        if len(value) > 1 and value[0] in ('==','!=','<','>','<=','>=','in','!in','has','!has','get') and isinstance(value[1], str):
                            field(value[1], 'Number' if len(value)>2 and isinstance(value[2], (int,float)) and not isinstance(value[2],bool) else 'String')
                        for v in value: walk(v)
                    elif isinstance(value, dict):
                        if 'property' in value: field(value['property'], 'Number')
                        for v in value.values(): walk(v)
                    elif isinstance(value, str):
                        for name in re.findall(r'\{([^{}]+)\}',value): field(name)
                walk(layer.get('filter'))
                walk(layer.get('layout'))
                walk(layer.get('paint'))
                if NAME_TOKEN.search(str(layer.get('layout', {}).get('text-field', ''))):
                    field('name:en')
    with sqlite3.connect(path) as db:
        metadata = dict(db.execute('SELECT name,value FROM metadata'))
        description = json.loads(metadata['json'])
        layers = {layer['id']: layer for layer in description['vector_layers']}
        additions = {}
        for name, fields in required.items():
            layer = layers.setdefault(name, {'id':name,'fields':{},'minzoom':0,'maxzoom':int(metadata['maxzoom'])})
            additions[name] = sorted(set(fields) - set(layer['fields']))
            for field_name, kind in fields.items(): layer['fields'].setdefault(field_name, kind)
        description['vector_layers'] = list(layers.values())
        db.execute('UPDATE metadata SET value=? WHERE name=?', (json.dumps(description), 'json'))
    return additions


def adapt_style(original, world_only=False):
    style = copy.deepcopy(original)
    old = style['layers']
    land = next(l['paint']['background-color'] for l in old if l['type'] == 'background')
    water = next(l['paint']['fill-color'] for l in old if l.get('source-layer') == 'water' and 'fill-color' in l.get('paint', {}))
    style.pop('glyphs', None)
    style['metadata'] = {'description': 'Offline GeoServer adaptation of GeoSolutions/OpenMapTiles styles'}
    # GeoServer resolves source-layer against its catalog. No remote TileJSON/glyph service is used.
    style['sources'] = {'openmaptiles': {'type': 'vector'}, 'world': {'type': 'vector'}}
    layers = [{'id': 'background', 'type': 'background', 'paint': {'background-color': water}}]
    dark = style.get('name') == 'Dark Matter'
    text = '#b5b5b5' if dark else '#555555'
    border = '#555555' if dark else '#9b9b9b'
    for scale, low, high in [(110, 0, 4), (50, 4, 6), (10, 6, 24)]:
        def add(kind, source, paint, suffix, **kwargs):
            layer = {'id': f'world-{suffix}-{scale}', 'type': kind, 'source': 'world', 'source-layer': f'world_{source}_{scale}', 'minzoom': low, 'maxzoom': high, 'paint': paint, **kwargs}
            layers.append(layer)
        add('fill', 'ocean', {'fill-color': water, 'fill-antialias': False}, 'ocean')
        add('fill', 'land', {'fill-color': land, 'fill-antialias': False}, 'land')
        add('fill', 'lakes', {'fill-color': water}, 'lakes')
        if low < 6:
            add('line', 'boundaries', {'line-color': border, 'line-width': 0.6}, 'borders')
            add('symbol', 'country_labels', {'text-color': text, 'text-halo-color': land, 'text-halo-width': 1}, 'countries', layout={'text-field': '{NAME_EN}', 'text-font': ['Noto Sans Regular'], 'text-size': 11})
            add('symbol', 'places', {'text-color': text, 'text-halo-color': land, 'text-halo-width': 1}, 'places', filter=['<=', 'SCALERANK', 2 if scale == 110 else 4], layout={'text-field': '{NAME}', 'text-font': ['Noto Sans Regular'], 'text-size': 10})
    if not world_only:
        for layer in old:
            if layer['type'] == 'background':
                continue
            layer['minzoom'] = max(6, layer.get('minzoom', 0))
            if layer.get('maxzoom', 24) <= layer['minzoom']:
                continue
            layers.append(layer)
    style['layers'] = layers
    return style


def make_styles(archive, output):
    output.mkdir(parents=True, exist_ok=True)
    changed = set()
    def write(name, style):
        path = output / f'{name}.json'
        content = json.dumps(style, ensure_ascii=False)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
            changed.add(name)
    with zipfile.ZipFile(archive) as z:
        for name, filename in STYLE_FILES.items():
            entry = next(p for p in z.namelist() if p.endswith(f'/workspaces/omt/styles/{filename}.json'))
            original = json.loads(z.read(entry))
            adapted = adapt_style(original)
            write(name, adapted)
            write(name + '-en', english_style(adapted))
            if name == 'osm-bright':
                world = adapt_style(original, world_only=True)
                write('world', world)
                write('world-en', english_style(world))
        for entry in z.namelist():
            marker = '/workspaces/omt/styles/'
            if marker in entry and '/sprite' in entry and not entry.endswith('/'):
                relative = Path(entry.split(marker, 1)[1])
                if '..' in relative.parts:
                    raise ValueError('Unsafe archive path')
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(z.read(entry))
    return changed


def english_style(local):
    """Change name text only. Missing translations never suppress geometry/icons."""
    style = copy.deepcopy(local)
    style['name'] += ' — English'
    style.setdefault('metadata', {})['label-language'] = 'en-only'
    style['metadata']['geoserver-unlabelled-icons'] = {
        'version': 2, 'poi-padding': ENGLISH_POI_PADDING,
        'unlabelled-padding': UNLABELLED_ICON_PADDING}
    for layer in style['layers']:
        layout = layer.get('layout', {})
        text = layout.get('text-field')
        if layer.get('source-layer', '').startswith('world_places_'):
            layout['text-field'] = '{NAME_EN}'
        elif isinstance(text, str) and NAME_TOKEN.search(text):
            layout['text-field'] = '{name:en}'
    return style


class Rest:
    def __init__(self, base, env):
        self.base = base
        self.auth = 'Basic ' + base64.b64encode(f"{env['GEOSERVER_ADMIN_USER']}:{env['GEOSERVER_ADMIN_PASSWORD']}".encode()).decode()

    def request(self, path, data=None, method='GET', content_type='application/xml', allowed=()):
        if isinstance(data, str): data = data.encode()
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={'Authorization': self.auth, 'Content-Type': content_type})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in allowed:
                return b''
            raise RuntimeError(f'{method} {path}: HTTP {exc.code}: {exc.read().decode(errors="replace")[:1200]}') from exc

    def ensure(self, collection, name, document, update=False):
        path = collection + '/' + urllib.parse.quote(name, safe=':') + '.xml'
        if self.request(path, allowed=(404,)):
            if update:
                return self.request(path, document, 'PUT')
            return
        self.request(collection, document, 'POST')


def wait_ready(base, env):
    client = Rest(base, env)
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        try:
            client.request('/rest/about/version.json')
            return
        except (OSError, RuntimeError):
            time.sleep(3)
    raise RuntimeError('GeoServer did not become ready in six minutes; inspect container logs')


def xml(tag, fields):
    root = ET.Element(tag)
    for key, value in fields.items():
        ET.SubElement(root, key).text = str(value)
    return ET.tostring(root)


def normalize_sld_scales(sld):
    # GeoTools' zoom-zero scale constant is slightly below the value derived
    # from the EPSG:3857 extent. A tiny tolerance makes integer tile zooms
    # consistently select the new range, including WMS and WMTS metatiles.
    return re.sub(rb'(<(?:\w+:)?(?:Min|Max)ScaleDenominator>)([^<]+)',
                  lambda match: match[1] + str(float(match[2]) * 1.00000001).encode(), sld)


def preserve_unlabelled_icons(sld):
    """Keep MBStyle label graphics when a feature has no explicit English name.

    GeoTools couples the graphic and text in one TextSymbolizer. A null/empty
    label suppresses both. A whitespace label with zero-size font uses GeoTools'
    native icon-only label path, including collision detection and priorities.
    Untranslated icons get lower priority and generous spacing; visible labels
    still use name:en only. No standalone PointSymbolizer bypasses collisions.
    """
    s = '{http://www.opengis.net/sld}'
    o = '{http://www.opengis.net/ogc}'
    root = ET.fromstring(sld)
    changed = False
    poi_styles = {fts for layer in root.findall(s + 'NamedLayer')
                  if (layer.findtext(s + 'Name') or '').split(':')[-1] == 'poi'
                  for fts in layer.iter(s + 'FeatureTypeStyle')}
    def option(symbol, name, value):
        node = symbol.find(f'{s}VendorOption[@name="{name}"]')
        if node is None:
            node = ET.SubElement(symbol, s + 'VendorOption', name=name)
        node.text = str(value)
    for fts in root.iter(s + 'FeatureTypeStyle'):
        for rule in list(fts.findall(s + 'Rule')):
            symbols = [t for t in rule.findall(s + 'TextSymbolizer')
                       if t.find(s + 'Graphic') is not None
                       and any(n.text == 'name:en' for n in t.findall(s + 'Label//' + o + 'PropertyName'))]
            if not symbols:
                continue
            if fts in poi_styles:
                for symbol in symbols:
                    existing = symbol.findtext(f'{s}VendorOption[@name="spaceAround"]', '0')
                    option(symbol, 'spaceAround', max(ENGLISH_POI_PADDING, float(existing)))
                    option(symbol, 'conflictResolution', 'true')
            fallback = ET.Element(s + 'Rule')
            ET.SubElement(fallback, s + 'Name').text = (rule.findtext(s + 'Name') or 'symbol') + '-unlabelled-icon'
            predicate = ET.SubElement(fallback, o + 'Filter')
            original_filter = rule.find(o + 'Filter')
            if original_filter is not None:
                predicate = ET.SubElement(predicate, o + 'And')
                predicate.extend(copy.deepcopy(list(original_filter)))
            missing = ET.SubElement(predicate, o + 'Or')
            null = ET.SubElement(missing, o + 'PropertyIsNull')
            ET.SubElement(null, o + 'PropertyName').text = 'name:en'
            empty = ET.SubElement(missing, o + 'PropertyIsEqualTo')
            ET.SubElement(empty, o + 'PropertyName').text = 'name:en'
            ET.SubElement(empty, o + 'Literal').text = ''
            for tag in ('MinScaleDenominator', 'MaxScaleDenominator'):
                scale = rule.find(s + tag)
                if scale is not None:
                    fallback.append(copy.deepcopy(scale))
            for symbol in symbols:
                icon = ET.SubElement(fallback, s + 'TextSymbolizer', symbol.attrib)
                geometry = symbol.find(s + 'Geometry')
                if geometry is not None:
                    icon.append(copy.deepcopy(geometry))
                # CDATA is inserted during serialization: plain XML whitespace
                # is trimmed by GeoServer and would silently suppress the icon.
                ET.SubElement(icon, s + 'Label').text = '__GEO_SERVER_ICON_ONLY_LABEL__'
                font = ET.SubElement(icon, s + 'Font')
                ET.SubElement(font, s + 'CssParameter', name='font-family').text = 'Noto Sans'
                ET.SubElement(font, s + 'CssParameter', name='font-size').text = '0'
                placement = ET.SubElement(ET.SubElement(icon, s + 'LabelPlacement'), s + 'PointPlacement')
                anchor = ET.SubElement(placement, s + 'AnchorPoint')
                ET.SubElement(anchor, s + 'AnchorPointX').text = '0.5'
                ET.SubElement(anchor, s + 'AnchorPointY').text = '0.5'
                icon.append(copy.deepcopy(symbol.find(s + 'Graphic')))
                priority = ET.SubElement(icon, s + 'Priority')
                lower = ET.SubElement(priority, o + 'Sub')
                original_priority = symbol.find(s + 'Priority')
                if original_priority is not None and len(original_priority):
                    lower.append(copy.deepcopy(original_priority[0]))
                else:
                    ET.SubElement(lower, o + 'Literal').text = symbol.findtext(s + 'Priority', '1000')
                ET.SubElement(lower, o + 'Literal').text = '1000000'
                for name, value in {'conflictResolution': 'true', 'spaceAround': UNLABELLED_ICON_PADDING,
                                    'group': 'false', 'partials': 'false', 'graphicPlacement': 'INDEPENDENT',
                                    'graphic-resize': 'NONE', 'fallbackOnDefaultMark': 'false'}.items():
                    option(icon, name, value)
            fts.append(fallback)
            changed = True
    if not changed:
        return sld
    # GeoServer's SLD parser also expects the default SLD namespace here.
    for prefix, uri in (('sld', s[1:-1]), ('ogc', o[1:-1]), ('xlink', 'http://www.w3.org/1999/xlink')):
        ET.register_namespace(prefix, uri)
    root.set('xmlns', s[1:-1])
    return ET.tostring(root, encoding='utf-8', xml_declaration=True).replace(
        b'<sld:Label>__GEO_SERVER_ICON_ONLY_LABEL__</sld:Label>',
        b'<sld:Label><![CDATA[ ]]></sld:Label>')


def publish_style(client, name, path):
    collection = '/rest/styles'
    metadata = xml('style', {'name': name, 'format': 'mbstyle', 'filename': name + '.json'})
    client.ensure(collection, name, metadata, update=True)
    client.request(f'{collection}/{name}?raw=true', path.read_bytes(), 'PUT', 'application/vnd.geoserver.mbstyle+json')
    # Compile through the installed MBStyle extension during preparation.
    # Preserve JSON sources; publish the normalized SLD for stable scale edges.
    sld = normalize_sld_scales(client.request(f'{collection}/{name}.sld'))
    if name.endswith('-en'):
        sld = preserve_unlabelled_icons(sld)
    path.with_suffix('.sld').write_bytes(sld)
    client.request(f'{collection}/{name}.xml', xml('style', {'name': name, 'format': 'sld', 'filename': name + '.sld'}), 'PUT')
    client.request(f'{collection}/{name}', sld, 'PUT', 'application/vnd.ogc.sld+xml')


def provision(base, env, target, cfg, names=STYLE_GROUPS, refresh=False):
    client = Rest(base, env)
    client.ensure('/rest/workspaces', 'omt', xml('workspace', {'name': 'omt'}))
    stores = {'regional': {'dbtype': 'mbtiles', 'database': 'file:data/regional.mbtiles', 'namespace': 'https://example.invalid/omt'}, 'world': {'dbtype': 'geopkg', 'database': 'file:data/world.gpkg', 'namespace': 'https://example.invalid/omt'}}
    for name, params in stores.items():
        root = ET.Element('dataStore')
        ET.SubElement(root, 'name').text = name
        ET.SubElement(root, 'enabled').text = 'true'
        entries = ET.SubElement(root, 'connectionParameters')
        for k, v in params.items(): ET.SubElement(entries, 'entry', key=k).text = v
        client.ensure('/rest/workspaces/omt/datastores', name, ET.tostring(root))
    with sqlite3.connect(f'file:{target / "data_dir/data/regional.mbtiles"}?mode=ro', uri=True) as db:
        metadata = dict(db.execute('SELECT name, value FROM metadata'))
    layers = {'regional': [v['id'] for v in json.loads(metadata['json'])['vector_layers']], 'world': [f'world_{theme}_{scale}' for scale in (110, 50, 10) for theme in ('land', 'ocean', 'lakes', 'countries', 'boundaries', 'country_labels', 'places')]}
    for store, layer_names in layers.items():
        for name in layer_names:
            root = ET.Element('featureType')
            for key, value in {'name': name, 'nativeName': name, 'title': name, 'srs': 'EPSG:3857', 'enabled': 'true'}.items(): ET.SubElement(root, key).text = value
            bb = ET.SubElement(root, 'nativeBoundingBox')
            for key, value in {'minx': -EXTENT, 'miny': -EXTENT, 'maxx': EXTENT, 'maxy': EXTENT, 'crs': 'EPSG:3857'}.items(): ET.SubElement(bb, key).text = str(value)
            client.ensure(f'/rest/workspaces/omt/datastores/{store}/featuretypes', name, ET.tostring(root))
    available = set(layers['regional'] + layers['world'])
    published_path = target / 'published-styles.json'
    published = json.loads(published_path.read_text()) if published_path.exists() else {}
    changed = []
    for name in names:
        style_path = target / 'data_dir/styles' / f'{name}.json'
        style = json.loads(style_path.read_text())
        missing = {l['source-layer'] for l in style['layers'] if 'source-layer' in l} - available
        # Tiny smoke extracts legitimately omit optional schema layers. Full builds must retain them.
        if missing:
            if target.name != 'smoke':
                raise RuntimeError(f'{name} references missing source layers: {sorted(missing)}')
            style['layers'] = [l for l in style['layers'] if l.get('source-layer') not in missing]
            style_path.write_text(json.dumps(style, ensure_ascii=False))
        # A successful publication stamp is recorded only after cache invalidation.
        # Failed/partial refreshes are retried even when generated JSON is unchanged.
        source_hash = hashlib.sha256(style_path.read_bytes()).hexdigest()
        group_exists = bool(client.request(f'/rest/workspaces/omt/layergroups/{name}.xml', allowed=(404,)))
        if refresh and group_exists and published.get(name) == source_hash:
            continue
        publish_style(client, name, style_path)
        group = ET.Element('layerGroup')
        ET.SubElement(group, 'name').text = name
        ET.SubElement(group, 'mode').text = 'SINGLE'
        ET.SubElement(group, 'title').text = style['name'] + (' — worldwide overview' if name in ('world', 'world-en') else ' — OSM + world overview')
        ET.SubElement(ET.SubElement(group, 'publishables'), 'published')
        ET.SubElement(ET.SubElement(ET.SubElement(group, 'styles'), 'style'), 'name').text = name
        bounds = ET.SubElement(group, 'bounds')
        for k, v in {'minx': -EXTENT, 'miny': -EXTENT, 'maxx': EXTENT, 'maxy': EXTENT, 'crs': 'EPSG:3857'}.items(): ET.SubElement(bounds, k).text = str(v)
        path = '/rest/workspaces/omt/layergroups'
        # The style-group membership is fixed; replace the style content above.
        # GeoServer 3.0.1 cannot persist a PUT containing a null published slot.
        client.ensure(path, name, ET.tostring(group))
        if group_exists:
            # GWC's mass-truncation endpoint requires text/xml (not application/xml).
            client.request('/gwc/rest/masstruncate', xml('truncateLayer', {'layerName': 'omt:' + name}), 'POST', 'text/xml')
        published[name] = source_hash
        changed.append(name)
        # GWC's default EPSG:900913 grid is Web Mercator; add explicit EPSG:3857 grid below.
    grid = f'''<gridSet><name>EPSG:3857</name><srs><number>3857</number></srs><extent><coords><double>{-EXTENT}</double><double>{-EXTENT}</double><double>{EXTENT}</double><double>{EXTENT}</double></coords></extent><alignTopLeft>true</alignTopLeft><resolutions>{''.join(f'<double>{2*EXTENT/256/2**z}</double>' for z in range(cfg['display_maxzoom']+1))}</resolutions><metersPerUnit>1.0</metersPerUnit><pixelSize>0.00028</pixelSize><scaleNames>{''.join(f'<string>EPSG:3857:{z}</string>' for z in range(cfg['display_maxzoom']+1))}</scaleNames><tileHeight>256</tileHeight><tileWidth>256</tileWidth><yCoordinateFirst>false</yCoordinateFirst></gridSet>'''
    if not refresh:
        client.request('/gwc/rest/gridsets/EPSG:3857.xml', grid, 'PUT')
    for name in changed:
        cache = f'''<GeoServerLayer><name>omt:{name}</name><enabled>true</enabled><inMemoryCached>true</inMemoryCached><mimeFormats><string>image/png</string></mimeFormats><gridSubsets><gridSubset><gridSetName>EPSG:3857</gridSetName><zoomStart>0</zoomStart><zoomStop>{cfg['display_maxzoom']}</zoomStop></gridSubset></gridSubsets><metaWidthHeight><int>4</int><int>4</int></metaWidthHeight><gutter>64</gutter><expireCache>0</expireCache><expireClients>3600</expireClients><autoCacheStyles>false</autoCacheStyles></GeoServerLayer>'''
        client.request(f'/gwc/rest/layers/omt:{name}.xml', cache, 'PUT')
    quota = ET.fromstring(client.request('/gwc/rest/diskquota.xml'))
    quota.find('enabled').text = 'true'
    global_quota = quota.find('globalQuota')
    global_quota.clear()
    ET.SubElement(global_quota, 'value').text = '20'
    ET.SubElement(global_quota, 'units').text = 'GiB'
    quota.find('cacheCleanUpFrequency').text = '60'
    quota.find('cacheCleanUpUnits').text = 'SECONDS'
    client.request('/gwc/rest/diskquota.xml', ET.tostring(quota), 'PUT')
    save = {'workspace': 'omt', 'layers': layers, 'groups': list(STYLE_GROUPS), 'maxzoom': cfg['display_maxzoom']}
    (target / 'catalog.json').write_text(json.dumps(save, indent=2))
    published_path.write_text(json.dumps(published, indent=2))
    return changed
