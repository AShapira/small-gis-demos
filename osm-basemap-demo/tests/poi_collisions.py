"""Optional live collision/density acceptance check for the default CZ/SK demo."""
import sys,copy,xml.etree.ElementTree as E,urllib.parse,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from catalog import Rest,preserve_unlabelled_icons
from demo import env_values
from verify import png_info,view_bbox
s='{http://www.opengis.net/sld}';o='{http://www.opengis.net/ogc}'
for prefix,uri in [('sld',s[1:-1]),('ogc',o[1:-1]),('xlink','http://www.w3.org/1999/xlink')]:E.register_namespace(prefix,uri)
client=Rest('http://127.0.0.1:8600/geoserver',env_values(root/'bundle'));out=root/'bundle/validation/poi-collisions';out.mkdir(exist_ok=True)
def request(style,view,lon,lat,z,width=768,height=512):
 params={'SERVICE':'WMS','VERSION':'1.3.0','REQUEST':'GetMap','CRS':'EPSG:3857','BBOX':','.join(map(str,view_bbox(lon,lat,z,width,height))),'WIDTH':width,'HEIGHT':height,'FORMAT':'image/png','SLD_BODY':style}
 raw=client.request('/wms',urllib.parse.urlencode(params),'POST','application/x-www-form-urlencoded');png_info(raw);(out/(view+'.png')).write_bytes(raw)
fixture='''<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc" version="1.0.0"><NamedLayer><Name>omt:poi</Name><UserStyle><FeatureTypeStyle><Rule><Name>missing-English-ATM</Name><ogc:Filter><ogc:And><ogc:PropertyIsEqualTo><ogc:PropertyName>name</ogc:PropertyName><ogc:Literal>Euronet</ogc:Literal></ogc:PropertyIsEqualTo><ogc:PropertyIsNull><ogc:PropertyName>name:en</ogc:PropertyName></ogc:PropertyIsNull></ogc:And></ogc:Filter><TextSymbolizer><Label><ogc:PropertyName>name:en</ogc:PropertyName></Label><Graphic><Mark><WellKnownName>square</WellKnownName><Fill><CssParameter name="fill">#ff00ff</CssParameter></Fill></Mark><Size>11</Size></Graphic></TextSymbolizer></Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>'''
fixture=preserve_unlabelled_icons(fixture.encode()).decode()
request(fixture,'collision-on',14.42052,50.086515,17,128,64)
request(fixture.replace('name="conflictResolution">true','name="conflictResolution">false'),'collision-off',14.42052,50.086515,17,128,64)
print('Rendered missing-English ATM collision control',flush=True)
views=[('prague14',14.421,50.087,14),('prague15',14.421,50.087,15),('prague16',14.421,50.087,16),('prague17',14.421,50.087,17),('prague18',14.421,50.087,18),('bratislava15',17.108,48.146,15)]
for language in ('local','en'):
 path=root/'bundle/data_dir/styles'/('osm-bright'+('-en' if language=='en' else '')+'.sld')
 tree=E.fromstring(path.read_bytes());tree.set('xmlns',s[1:-1])
 for layer in tree.findall(s+'NamedLayer'):
  name=layer.find(s+'Name');name.text='omt:'+name.text.removeprefix('omt:')
  if name.text=='omt:poi':
   for graphic in layer.iter(s+'Graphic'):
    for child in list(graphic):
     if child.tag in (s+'ExternalGraphic',s+'Mark',s+'Size'):graphic.remove(child)
    mark=E.Element(s+'Mark');E.SubElement(mark,s+'WellKnownName').text='square';fill=E.SubElement(mark,s+'Fill');E.SubElement(fill,s+'CssParameter',name='fill').text='#ff00ff';graphic.insert(0,mark);size=E.Element(s+'Size');size.text='11';graphic.insert(1,size)
 for label in tree.iter(s+'Label'):
  if label.text==' ':label.text='__BLANK_LABEL__'
 raw=E.tostring(tree,encoding='unicode').replace('<sld:Label>__BLANK_LABEL__</sld:Label>','<sld:Label><![CDATA[ ]]></sld:Label>')
 for view,lon,lat,z in views:request(raw,language+'-'+view,lon,lat,z)
 print('Rendered diagnostic POI markers:',language,flush=True)
(out/'method.json').write_text(json.dumps({'method':'Diagnostic full-map renders substitute 11-pixel magenta squares for POI sprites, retaining text, placement, filters, scales and collision options. Counts measure diagnostic symbol placements, not exact original sprite counts.','fixture':'Existing Euronet ATMs with no name:en, at 14.42052, 50.086515; only conflictResolution changes between controls.','views':[v[0] for v in views]},indent=2)+'\n')
