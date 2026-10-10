import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import sqlite3
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import adapt_style, english_style, normalize_metadata, normalize_sld_scales, preserve_unlabelled_icons, STYLE_FILES, EXTENT, ENGLISH_POI_PADDING, UNLABELLED_ICON_PADDING
from demo import read_config
from verify import mercator, view_bbox, png_info


class PipelineTests(unittest.TestCase):
    def test_english_names_do_not_change_symbols_filters_or_non_name_labels(self):
        local = {'name':'Bright', 'layers':[
            {'id':'poi','source-layer':'poi','filter':['==','class','cafe'],
             'layout':{'text-field':'{name:latin}\\n{name:nonlatin}','icon-image':'cafe','text-font':['Noto Sans Regular']},
             'paint':{'text-color':'#444'}},
            {'id':'road','source-layer':'transportation_name','layout':{'text-field':'{name:latin} {name:nonlatin}'}},
            {'id':'ref','source-layer':'transportation_name','layout':{'text-field':'{ref}'}},
            {'id':'house','source-layer':'housenumber','layout':{'text-field':'{housenumber}'}},
            {'id':'world','source-layer':'world_places_50','layout':{'text-field':'{NAME}'}}]}
        before = copy.deepcopy(local)
        english = english_style(local)
        self.assertEqual(local, before)
        self.assertEqual([x['layout']['text-field'] for x in english['layers']],
                         ['{name:en}','{name:en}','{ref}','{housenumber}','{NAME_EN}'])
        for source, result in zip(local['layers'], english['layers']):
            result = copy.deepcopy(result)
            result['layout']['text-field'] = source['layout']['text-field']
            self.assertEqual(source, result)
        # The compiled SLD adds icon-only fallback rules; the main rule stays intact.
        self.assertEqual(english['layers'][0]['filter'], ['==','class','cafe'])

    def test_missing_english_icon_rule_keeps_scales_graphic_and_original_filter(self):
        source = b'''<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc"><NamedLayer><Name>poi</Name><UserStyle><FeatureTypeStyle><Rule><Name>cafe</Name><ogc:Filter><ogc:PropertyIsEqualTo><ogc:PropertyName>class</ogc:PropertyName><ogc:Literal>cafe</ogc:Literal></ogc:PropertyIsEqualTo></ogc:Filter><MinScaleDenominator>1</MinScaleDenominator><MaxScaleDenominator>2000</MaxScaleDenominator><TextSymbolizer><Label><ogc:PropertyName>name:en</ogc:PropertyName></Label><Graphic><Mark><WellKnownName>circle</WellKnownName></Mark><Size>11</Size></Graphic></TextSymbolizer></Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>'''
        s = '{http://www.opengis.net/sld}'
        o = '{http://www.opengis.net/ogc}'
        serialized = preserve_unlabelled_icons(source)
        self.assertIn(b'<![CDATA[ ]]>', serialized)
        self.assertNotIn(b'__GEO_SERVER_ICON_ONLY_LABEL__', serialized)
        result = ET.fromstring(serialized)
        original, fallback = result.findall('.//' + s + 'Rule')
        text = original.find(s + 'TextSymbolizer')
        self.assertEqual(text.findtext(s + 'Label/' + o + 'PropertyName'), 'name:en')
        self.assertEqual(float(text.findtext(s + 'VendorOption[@name="spaceAround"]')), ENGLISH_POI_PADDING)
        self.assertEqual(text.findtext(s + 'VendorOption[@name="conflictResolution"]'), 'true')
        self.assertIsNone(fallback.find(s + 'PointSymbolizer'))
        icon = fallback.find(s + 'TextSymbolizer')
        self.assertEqual(icon.findtext(s + 'Label'), ' ')
        self.assertEqual(icon.findtext(s + 'Font/' + s + 'CssParameter[@name="font-size"]'), '0')
        self.assertEqual(icon.findtext(s + 'VendorOption[@name="conflictResolution"]'), 'true')
        self.assertEqual(int(icon.findtext(s + 'VendorOption[@name="spaceAround"]')), UNLABELLED_ICON_PADDING)
        self.assertEqual(icon.findtext(s + 'VendorOption[@name="group"]'), 'false')
        self.assertEqual([v.text for v in icon.findall(s + 'Priority/' + o + 'Sub/' + o + 'Literal')], ['1000', '1000000'])
        self.assertEqual(fallback.findtext(s + 'MaxScaleDenominator'), '2000')
        self.assertEqual(ET.tostring(icon.find(s + 'Graphic')),
                         ET.tostring(original.find(s + 'TextSymbolizer/' + s + 'Graphic')))
        predicate = fallback.find(o + 'Filter/' + o + 'And')
        self.assertEqual(ET.tostring(predicate[0]), ET.tostring(original.find(o + 'Filter')[0]))
        self.assertEqual(predicate[1].tag, o + 'Or')
        self.assertEqual([n.text for n in predicate[1].iter(o + 'PropertyName')], ['name:en', 'name:en'])
        self.assertEqual(len(predicate[1].findall(o + 'PropertyIsNull')), 1)
        self.assertEqual(preserve_unlabelled_icons(source.replace(b'name:en', b'ref')), source.replace(b'name:en', b'ref'))

    def test_compiled_style_boundary_includes_integer_wmts_zoom(self):
        # GeoTools uses a rounded scale constant; WMTS derives it from the extent.
        source = b'<sld:MaxScaleDenominator>8735660.374232702</sld:MaxScaleDenominator>'
        value = float(normalize_sld_scales(source).split(b'>')[1].split(b'<')[0])
        actual = EXTENT * 2 / 256 / 2**6 / 0.00028
        self.assertGreater(value, actual)
        self.assertLess(value, actual * 1.000001)

    def test_world_background_and_nonoverlapping_labels(self):
        source = {'name':'Bright','glyphs':'https://remote/fonts','sources':{'osm':{'url':'https://remote/tiles'}},'layers':[
            {'id':'bg','type':'background','paint':{'background-color':'#eee'}},
            {'id':'water','type':'fill','source-layer':'water','paint':{'fill-color':'#abc'}},
            {'id':'place','type':'symbol','source-layer':'place','maxzoom':10},
            {'id':'continent','type':'symbol','source-layer':'place','maxzoom':2}]}
        before = copy.deepcopy(source)
        result = adapt_style(source)
        self.assertEqual(source, before)
        self.assertNotIn('glyphs', result)
        self.assertEqual(result['layers'][0]['paint']['background-color'], '#abc')
        self.assertNotIn('continent', [x['id'] for x in result['layers']])
        self.assertEqual(next(x for x in result['layers'] if x['id']=='place')['minzoom'],6)
        world_labels = [x for x in result['layers'] if x['id'].startswith('world-') and x['type']=='symbol']
        self.assertTrue(all(x['maxzoom']<=6 for x in world_labels))

    def test_country_identifiers_cannot_inject_shell_tokens(self):
        cfg = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
        cfg['countries']=['europe/slovakia;rm']
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text(json.dumps(cfg))
            with self.assertRaises(ValueError): read_config(p)

    def test_mercator_and_zoom_scale(self):
        x,y=mercator(0,0)
        self.assertAlmostEqual(x,0);self.assertAlmostEqual(y,0)
        a=view_bbox(14,50,14);b=view_bbox(14,50,15)
        self.assertAlmostEqual(a[2]-a[0],2*(b[2]-b[0]))

    def test_service_exception_is_not_accepted_as_image(self):
        with self.assertRaises(AssertionError): png_info(b'<ServiceException>bad style</ServiceException>')

    def test_optional_schema_fields_do_not_rewrite_tile_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            database=Path(d)/'map.mbtiles'; archive=Path(d)/'styles.zip'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE metadata(name TEXT,value TEXT)')
                db.executemany('INSERT INTO metadata VALUES(?,?)',[('maxzoom','14'),('json',json.dumps({'vector_layers':[{'id':'water','fields':{'class':'String'}}]}))])
                db.execute('CREATE TABLE tiles(tile_data BLOB)');db.execute('INSERT INTO tiles VALUES(?)',(b'unchanged vector tile',))
            with zipfile.ZipFile(archive,'w') as z:
                for filename in STYLE_FILES.values():
                    z.writestr(f'example/workspaces/omt/styles/{filename}.json',json.dumps({'layers':[{'source-layer':'water','filter':['!=','brunnel','tunnel']},{'source-layer':'place','filter':['>=','rank',1],'layout':{'text-field':'{name:latin}'}}]}))
            additions=normalize_metadata(database,archive)
            self.assertEqual(additions['water'],['brunnel'])
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT tile_data FROM tiles').fetchone()[0],b'unchanged vector tile')
                layers=json.loads(db.execute("SELECT value FROM metadata WHERE name='json'").fetchone()[0])['vector_layers']
                place=next(x for x in layers if x['id']=='place')
                self.assertEqual(place['fields'],{'rank':'Number','name:latin':'String','name:en':'String'})


if __name__ == '__main__': unittest.main()
